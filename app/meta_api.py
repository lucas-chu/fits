"""Meta Model API calls: Muse Spark, SAM 3.1, and Muse Image. No other models."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx

API_BASE = "https://api.meta.ai/v1"
SPARK = "muse-spark-1.3"
SAM = "sam-3.1"
IMAGE = "muse-image-1.0"

EDIT_PROMPT = (
    "Add a narrow standing desk in the window light. Change furniture only. "
    "Do not add people, faces, bodies, animals, or text. "
    "Keep the room, walls, windows, and existing furniture otherwise the same."
)


class ModelError(Exception):
    pass


def key_present() -> bool:
    return bool(os.environ.get("MODEL_API_KEY", "").strip())


def _headers() -> dict[str, str]:
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        raise ModelError("MODEL_API_KEY is not set.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "output_text" and isinstance(item.get("text"), str):
            chunks.append(item["text"])
        for content in item.get("content") or []:
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                chunks.append(content.get("text") or "")
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ModelError("Muse Spark did not return JSON.")
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ModelError("Muse Spark returned JSON that was not an object.")
    return parsed


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = await client.post(
        f"{API_BASE}{path}",
        headers=_headers(),
        json=body,
        timeout=timeout,
    )
    if response.status_code == 400 and "reasoning" in body:
        response = await client.post(
            f"{API_BASE}{path}",
            headers=_headers(),
            json={key: value for key, value in body.items() if key != "reasoning"},
            timeout=timeout,
        )
    if response.status_code >= 400:
        detail = response.text[:280].replace("\n", " ")
        raise ModelError(f"{path} failed ({response.status_code}): {detail}")
    return response.json()


def _listing_brief(listing: dict[str, Any]) -> str:
    description = (listing.get("description") or "").strip()
    if len(description) > 900:
        description = description[:900] + "…"
    return (
        f"id: {listing['id']}\n"
        f"price: {listing.get('price_label') or 'unknown'}\n"
        f"location: {listing.get('location') or listing.get('neighborhood') or 'unknown'}\n"
        f"title: {listing.get('title') or ''}\n"
        f"description: {description or '(no description)'}"
    )


async def rank_listings(
    client: httpx.AsyncClient,
    listings: list[dict[str, Any]],
    max_rent: int,
    neighborhood: str,
    must_haves: str,
) -> dict[str, Any]:
    prompt = (
        "Rank these San Francisco rental listings for someone who must work, sleep, and cook there.\n"
        "Use only the text below. Do not browse. You cannot see photos.\n"
        f"Constraints: max rent ${max_rent}; neighborhood preference {neighborhood or 'San Francisco'}; "
        f"must-haves: {must_haves or 'a desk, a real window, and a bed'}.\n"
        "Score each listing from 0 to 100. Lower the score when the rent is over the cap, "
        "the location is outside the neighborhood, or a must-have is absent from the description. "
        "The reason is one sentence that cites the description and the location.\n"
        "Return only JSON with this shape:\n"
        '{"rankings":[{"id":"string","score":0,"reason":"string"}],"winner_id":"string"}\n\n'
        + "\n\n".join(_listing_brief(listing) for listing in listings)
    )
    payload = await _post(
        client,
        "/responses",
        {"model": SPARK, "input": prompt, "reasoning": {"effort": "low"}},
        timeout=60,
    )
    return parse_json_object(extract_output_text(payload))


def apply_ranking(listings: list[dict[str, Any]], ranked: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    by_id = {listing["id"]: listing for listing in listings}
    scores: dict[str, tuple[float, str]] = {}
    for row in ranked.get("rankings") or []:
        if not isinstance(row, dict):
            continue
        listing_id = str(row.get("id", "")).strip()
        if listing_id not in by_id:
            continue
        try:
            score = float(row.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(row.get("reason") or "").strip()
        scores[listing_id] = (score, reason)
    for listing in listings:
        score, reason = scores.get(listing["id"], (None, ""))
        listing["score"] = score
        listing["reason"] = reason
    winner = str(ranked.get("winner_id") or "").strip()
    if winner not in by_id:
        ranked_ids = sorted(scores, key=lambda listing_id: scores[listing_id][0], reverse=True)
        winner = ranked_ids[0] if ranked_ids else listings[0]["id"]
    ordered = sorted(
        listings,
        key=lambda listing: (
            listing["id"] != winner,
            -(listing["score"] if isinstance(listing["score"], (int, float)) else -1),
        ),
    )
    return ordered, winner


async def segment_prompt(
    client: httpx.AsyncClient,
    prompt: str,
    image_url: str,
) -> str:
    body = {
        "model": SAM,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }
        ],
        "metadata": {"mask_encoding": "one_bit"},
    }
    payload = await _post(client, "/responses", body, timeout=90)
    return extract_output_text(payload)


async def write_verdict(
    client: httpx.AsyncClient,
    listing: dict[str, Any],
    measurements: list[dict[str, Any]],
    max_rent: int,
    neighborhood: str,
    must_haves: str,
) -> dict[str, Any]:
    measure_lines = []
    for item in measurements:
        if item.get("error"):
            measure_lines.append(f"{item['prompt']}: request failed")
        elif item.get("found"):
            measure_lines.append(
                f"{item['prompt']}: found, {item['area_percent']}% of the photo, {item['box_count']} box"
            )
        else:
            measure_lines.append(f"{item['prompt']}: {item.get('finding') or 'not found'} (empty mask)")
    prompt = (
        "Write a leasing verdict for one San Francisco rental. Use only this text. Do not browse. "
        "You cannot see the photo; trust the measurements. An empty mask means the object was not found. "
        "Say 'no desk' rather than calling an empty mask an error.\n"
        f"Constraints: max rent ${max_rent}; neighborhood preference {neighborhood}; must-haves: {must_haves}.\n"
        f"Title: {listing.get('title')}\n"
        f"Location: {listing.get('location') or listing.get('neighborhood')}\n"
        f"Rent: {listing.get('price_label')}\n"
        f"Description: {(listing.get('description') or '')[:1200]}\n"
        "Measurements from sam-3.1:\n"
        + "\n".join(measure_lines)
        + "\nChoose fit, maybe, or no. fit means they can work, sleep, and cook here within the rent cap "
        "and neighborhood. maybe means one real gap. no means a must-have is missing, the rent is over the cap, "
        "or the neighborhood is wrong.\n"
        "Return only JSON:\n"
        '{"fit":"fit|maybe|no","bullets":["four strings"],"questions":["three strings"]}\n'
        "Each bullet cites the description, the neighborhood, or a measurement."
    )
    payload = await _post(
        client,
        "/responses",
        {"model": SPARK, "input": prompt, "reasoning": {"effort": "low"}},
        timeout=60,
    )
    verdict = parse_json_object(extract_output_text(payload))
    fit = str(verdict.get("fit") or "maybe").strip().lower()
    if fit not in {"fit", "maybe", "no"}:
        fit = "maybe"
    bullets = [str(item).strip() for item in (verdict.get("bullets") or []) if str(item).strip()]
    questions = [str(item).strip() for item in (verdict.get("questions") or []) if str(item).strip()]
    return {"fit": fit, "bullets": bullets[:4], "questions": questions[:3]}


async def edit_furniture(client: httpx.AsyncClient, image_bytes: bytes, mime: str = "image/jpeg") -> tuple[bytes, str]:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    subtype = mime.split("/")[-1]
    if subtype == "jpg":
        subtype = "jpeg"
    body = {
        "model": IMAGE,
        "prompt": EDIT_PROMPT,
        "images": [{"image_url": f"data:{mime};base64,{encoded}"}],
        "n": 1,
        "output_format": "jpeg",
        "reasoning_strength": "low",
        "tool_enablement": {
            "enable_web_search": False,
            "enable_image_search": False,
            "enable_shell": False,
        },
    }
    payload = await _post(client, "/images/edits", body, timeout=120)
    data = payload.get("data") or []
    if not data:
        raise ModelError("Muse Image returned no image.")
    first = data[0]
    if first.get("b64_json"):
        fmt = payload.get("output_format") or "jpeg"
        return base64.b64decode(first["b64_json"]), str(fmt)
    if first.get("url"):
        downloaded = await client.get(first["url"], timeout=60)
        if downloaded.status_code >= 400:
            raise ModelError(f"Muse Image URL failed ({downloaded.status_code}).")
        return downloaded.content, str(payload.get("output_format") or "jpeg")
    raise ModelError("Muse Image response had no b64_json or url.")
