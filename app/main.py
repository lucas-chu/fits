"""Fits server: one search, three Meta models, and a keyless replay."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image

from app.listings import ListingError, gather_listings
from app.measure import PROMPTS, draw_overlay, jpeg_bytes, load_image, measurement_for, parse_sam_boxes
from app.meta_api import (
    IMAGE,
    SAM,
    SPARK,
    ModelError,
    apply_ranking,
    edit_furniture,
    key_present,
    rank_listings,
    segment_prompt,
    write_verdict,
)

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
(ROOT / "demo" / "trace").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Fits")
app.mount("/demo", StaticFiles(directory=ROOT / "demo"), name="demo")

IMAGE_HOST_SUFFIXES = (
    "images.craigslist.org",
    "craigslist.org",
    "zillowstatic.com",
    "zillow.com",
)


class SearchIn(BaseModel):
    max_rent: int = Field(default=3200, ge=200, le=20000)
    neighborhood: str = Field(default="Mission", max_length=80)
    must_haves: str = Field(default="a desk, a real window, and a bed", max_length=300)
    listing_url: str = Field(default="", max_length=500)


def _trace() -> dict[str, Any]:
    path = ROOT / "demo" / "trace" / "trace.json"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Replay trace is missing.")
    return json.loads(path.read_text())


def _public_listings(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hidden = {"image_url"}
    return [{key: value for key, value in listing.items() if key not in hidden} for listing in listings]


def _data_url(raw: bytes, mime: str) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _image_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in IMAGE_HOST_SUFFIXES)


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes:
    if not _image_allowed(url):
        raise ModelError("Winner photo host is not Craigslist or Zillow.")
    response = await client.get(
        url,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Fits/1.0 (hackathon demo)"},
    )
    if response.status_code >= 400:
        raise ModelError(f"Could not download the winner photo ({response.status_code}).")
    content_type = response.headers.get("content-type", "")
    if "image" not in content_type and not response.content[:3] == b"\xff\xd8\xff":
        raise ModelError("Winner photo URL did not return an image.")
    if len(response.content) > 8_000_000:
        raise ModelError("Winner photo is too large.")
    return response.content


async def _segment_winner(
    client: httpx.AsyncClient,
    image: Image.Image,
    image_url: str,
    image_bytes: bytes,
) -> list[dict[str, Any]]:
    async def one(prompt: str) -> dict[str, Any]:
        try:
            text = await segment_prompt(client, prompt, image_url)
            boxes = parse_sam_boxes(text)
        except ModelError as exc:
            # Public listing URLs can be refused. Retry once with the bytes we already hold.
            if "image_url" in str(exc).lower() or "400" in str(exc):
                try:
                    data_url = _data_url(image_bytes, "image/jpeg")
                    text = await segment_prompt(client, prompt, data_url)
                    boxes = parse_sam_boxes(text)
                except ModelError as retry_exc:
                    return measurement_for(image, prompt, [], error=str(retry_exc))
            else:
                return measurement_for(image, prompt, [], error=str(exc))
        return measurement_for(image, prompt, boxes)

    return list(await asyncio.gather(*[one(prompt) for prompt in PROMPTS]))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/replay")
def replay() -> dict[str, Any]:
    return _trace()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "key_present": key_present()}


@app.post("/api/search")
async def search(body: SearchIn) -> dict[str, Any]:
    try:
        listings, zillow_status = await gather_listings(
            body.max_rent,
            body.neighborhood.strip() or "San Francisco",
            body.listing_url.strip(),
        )
    except ListingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Listing fetch failed: {exc.__class__.__name__}") from exc

    result: dict[str, Any] = {
        "mode": "live",
        "key_present": key_present(),
        "query": {
            "max_rent": body.max_rent,
            "neighborhood": body.neighborhood,
            "must_haves": body.must_haves,
            "listing_url": body.listing_url,
        },
        "zillow_status": zillow_status,
        "listings": _public_listings(listings),
        "winner_id": None,
        "measurements": [],
        "verdict": None,
        "images": None,
        "models": {"rank": SPARK, "segment": SAM, "verdict": SPARK, "edit": IMAGE},
        "photo_note": "Live listing photo, fetched for this session and not stored in the repo.",
        "notice": "",
    }
    if not listings:
        result["notice"] = "No listings came back."
        return result
    if not key_present():
        result["notice"] = (
            "MODEL_API_KEY is not set, so Muse Spark, SAM 3.1, and Muse Image did not run. "
            "Replay shows the full critic on a drawn fixture room."
        )
        return result

    winner_id = listings[0]["id"]
    try:
        async with httpx.AsyncClient() as client:
            ranked = await rank_listings(
                client,
                listings,
                body.max_rent,
                body.neighborhood,
                body.must_haves,
            )
            listings, winner_id = apply_ranking(listings, ranked)
            result["listings"] = _public_listings(listings)
            result["winner_id"] = winner_id
            winner = next(listing for listing in listings if listing["id"] == winner_id)
            image_url = winner.get("image_url") or ""
            if not image_url:
                result["notice"] = "The winner has no photo, so SAM 3.1 and Muse Image were skipped."
                return result
            raw = await _download_image(client, image_url)
            image = load_image(raw)
            jpeg = jpeg_bytes(image)
            measurements = await _segment_winner(client, image, image_url, jpeg)
            result["measurements"] = measurements
            overlay = draw_overlay(image, measurements)
            try:
                verdict = await write_verdict(
                    client,
                    winner,
                    measurements,
                    body.max_rent,
                    body.neighborhood,
                    body.must_haves,
                )
                result["verdict"] = verdict
            except (ModelError, json.JSONDecodeError) as exc:
                result["notice"] = f"Verdict failed: {exc}"
            edit_error = ""
            after_url = None
            try:
                edited, fmt = await edit_furniture(client, jpeg)
                mime = "image/jpeg" if fmt in {"jpeg", "jpg"} else f"image/{fmt}"
                after_url = _data_url(edited, mime)
            except (ModelError, httpx.HTTPError) as exc:
                edit_error = str(exc)
            result["images"] = {
                "before": _data_url(jpeg, "image/jpeg"),
                "overlay": _data_url(jpeg_bytes(overlay), "image/jpeg"),
                "after": after_url,
                "edit_error": edit_error,
            }
    except ModelError as exc:
        result["notice"] = str(exc)
        result["winner_id"] = winner_id
        result["listings"] = _public_listings(listings)
    return result
