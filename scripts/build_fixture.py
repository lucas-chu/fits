"""Draw the Replay room and write demo/trace. No scraped listing photos."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.measure import area_percent, draw_overlay, jpeg_bytes  # noqa: E402

OUT = ROOT / "demo" / "trace"
WIDTH = 960
HEIGHT = 640

WINDOW = (80, 70, 320, 350)
BED = (500, 300, 840, 530)
SINK = (70, 430, 320, 520)
CLOSET = (860, 70, 930, 430)


def _room() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#e7d7b8")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 500, WIDTH, HEIGHT), fill="#8d6b43")
    draw.line((0, 500, WIDTH, 500), fill="#5c4128", width=3)
    # Window: frame, sky, muntins. This is the light a desk would use.
    draw.rectangle(WINDOW, fill="#f4efe6")
    draw.rectangle((98, 88, 302, 332), fill="#8ec4e8")
    draw.rectangle((190, 88, 206, 332), fill="#f4efe6")
    draw.rectangle((98, 198, 302, 214), fill="#f4efe6")
    # Bed.
    draw.rectangle((500, 300, 528, 530), fill="#6b4a36")
    draw.rectangle((528, 360, 880, 530), fill="#f7f3ec")
    draw.rectangle((548, 378, 680, 430), fill="#efe4d4")
    draw.rectangle((700, 400, 860, 520), fill="#7a4e46")
    # Kitchenette sink.
    draw.rectangle(SINK, fill="#cbb892")
    draw.ellipse((120, 450, 250, 505), fill="#d5e2e4", outline="#8aa0a4", width=3)
    draw.rectangle((176, 438, 188, 452), fill="#8d8f92")
    # Closet door.
    draw.rectangle(CLOSET, fill="#6d5344")
    draw.rectangle((872, 88, 918, 250), outline="#e7d7b8", width=2)
    draw.rectangle((872, 258, 918, 412), outline="#e7d7b8", width=2)
    draw.ellipse((908, 240, 920, 252), fill="#d9c27a")
    return image


def _with_desk(image: Image.Image) -> Image.Image:
    edited = image.copy()
    draw = ImageDraw.Draw(edited)
    # Narrow standing desk in the window light. Furniture only.
    draw.rectangle((108, 312, 292, 328), fill="#a56b32")
    draw.rectangle((118, 328, 128, 430), fill="#6b4524")
    draw.rectangle((272, 328, 282, 430), fill="#6b4524")
    return edited


def _box(rect: tuple[int, int, int, int]) -> dict[str, int]:
    return {"x1": rect[0], "y1": rect[1], "x2": rect[2], "y2": rect[3]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = _room()
    specs = [
        ("window", [WINDOW]),
        ("desk", []),
        ("bed", [BED]),
        ("kitchen sink", [SINK]),
        ("closet", [CLOSET]),
    ]
    measurements = []
    for prompt, rects in specs:
        boxes = [_box(rect) for rect in rects]
        percent = area_percent(before, rects) if rects else 0.0
        measurements.append(
            {
                "prompt": prompt,
                "found": bool(rects),
                "area_percent": percent if rects else 0.0,
                "box_count": len(rects),
                "boxes": boxes,
                "finding": None if rects else f"no {prompt}",
                "error": None,
            }
        )
    overlay = draw_overlay(before, measurements)
    after = _with_desk(before)
    (OUT / "before.jpg").write_bytes(jpeg_bytes(before))
    (OUT / "overlay.jpg").write_bytes(jpeg_bytes(overlay))
    (OUT / "after.jpg").write_bytes(jpeg_bytes(after))

    by_prompt = {item["prompt"]: item["area_percent"] for item in measurements}
    window_pct = by_prompt["window"]
    bed_pct = by_prompt["bed"]
    sink_pct = by_prompt["kitchen sink"]
    closet_pct = by_prompt["closet"]

    listings = [
        {
            "id": "fixture-mission",
            "source": "fixture",
            "title": "Top-floor studio, 14th near Mission",
            "price_label": "$2,800",
            "price": 2800,
            "neighborhood": "Mission District",
            "location": "Mission District, San Francisco · 14th St near Mission St · $2,800",
            "description": (
                "Top-floor studio with a street-facing window, a full-size bed, a kitchenette sink, "
                "and a closet. No desk. Hardwood floors. Street parking. A short walk to BART."
            ),
            "url": "",
            "score": 74,
            "reason": "Mission, under the cap, and the description has a window, a bed, and a sink, but it says no desk.",
        },
        {
            "id": "fixture-soma",
            "source": "fixture",
            "title": "Furnished SOMA loft with a built-in desk",
            "price_label": "$3,900",
            "price": 3900,
            "neighborhood": "SOMA",
            "location": "SOMA, San Francisco · $3,900",
            "description": "Loft with a built-in desk, a sleeping alcove, and a galley kitchen. Rent is $3,900.",
            "url": "",
            "score": 41,
            "reason": "The description includes a desk, but SOMA is the wrong neighborhood and $3,900 is over a $3,200 cap.",
        },
        {
            "id": "fixture-richmond",
            "source": "fixture",
            "title": "Quiet Richmond room, garden light",
            "price_label": "$2,450",
            "price": 2450,
            "neighborhood": "Richmond",
            "location": "Richmond, San Francisco · $2,450",
            "description": "Quiet room with a bed and a closet. The only light is a frosted garden window in the hall.",
            "url": "",
            "score": 48,
            "reason": "Under the cap, but Richmond misses the Mission preference and the description has no real work window or desk.",
        },
    ]
    trace = {
        "mode": "replay",
        "key_present": False,
        "query": {
            "max_rent": 3200,
            "neighborhood": "Mission",
            "must_haves": "a desk, a real window, and a bed",
            "listing_url": "",
        },
        "zillow_status": "Replay does not call Zillow. The room below is a drawn fixture, not a listing photo.",
        "listings": listings,
        "winner_id": "fixture-mission",
        "measurements": measurements,
        "verdict": {
            "fit": "maybe",
            "bullets": [
                f"The description says street-facing window in the Mission, and the window box is {window_pct}% of the photo.",
                f"A full-size bed is in the description and the photo ({bed_pct}%), so sleeping is covered.",
                f"The kitchenette sink is real but small ({sink_pct}%), and the closet is {closet_pct}% of the frame.",
                "There is no desk in the description, and the desk mask is empty. $2,800 is under a $3,200 cap.",
            ],
            "questions": [
                "Does the street-facing window open, and is night noise livable?",
                "Can a narrow desk sit in that window light without blocking the closet?",
                "What is the lease length, and are utilities on top of the $2,800?",
            ],
        },
        "images": {
            "before": "/demo/trace/before.jpg",
            "overlay": "/demo/trace/overlay.jpg",
            "after": "/demo/trace/after.jpg",
            "edit_error": "",
        },
        "models": {
            "rank": "muse-spark-1.3",
            "segment": "sam-3.1",
            "verdict": "muse-spark-1.3",
            "edit": "muse-image-1.0",
        },
        "photo_note": "Drawn fixture for Replay. Not a Craigslist or Zillow photo.",
        "notice": "Replay is local. No listing sites and no model calls.",
    }
    (OUT / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
