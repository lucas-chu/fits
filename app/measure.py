"""Turn SAM boxes into area percents and a colored overlay. Pillow only."""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image, ImageDraw

PROMPTS = ["window", "desk", "bed", "kitchen sink", "closet"]

COLORS = {
    "window": (36, 96, 168),
    "desk": (184, 122, 20),
    "bed": (122, 62, 112),
    "kitchen sink": (18, 112, 106),
    "closet": (168, 72, 36),
}

BOX_RE = re.compile(
    r"<\|box;x1=([0-9.]+);y1=([0-9.]+);x2=([0-9.]+);y2=([0-9.]+)"
    r"(?:;w=([0-9.]+);h=([0-9.]+))?\|>"
)


def load_image(data: bytes, max_edge: int = 1280) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image = image.convert("RGB")
    width, height = image.size
    edge = max(width, height)
    if edge > max_edge:
        scale = max_edge / float(edge)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def parse_sam_boxes(output_text: str) -> list[dict[str, float]]:
    """Parse SAM 3.1 special-token boxes. An empty string means no match."""
    boxes = []
    for match in BOX_RE.finditer(output_text or ""):
        x1, y1, x2, y2, src_w, src_h = match.groups()
        boxes.append(
            {
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "src_w": float(src_w) if src_w else 0.0,
                "src_h": float(src_h) if src_h else 0.0,
            }
        )
    return boxes


def scale_boxes(boxes: list[dict[str, float]], width: int, height: int) -> list[tuple[int, int, int, int]]:
    scaled = []
    for box in boxes:
        src_w = box.get("src_w") or width
        src_h = box.get("src_h") or height
        sx = width / src_w if src_w else 1.0
        sy = height / src_h if src_h else 1.0
        x1 = int(round(box["x1"] * sx))
        y1 = int(round(box["y1"] * sy))
        x2 = int(round(box["x2"] * sx))
        y2 = int(round(box["y2"] * sy))
        x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
        y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
        if x2 > x1 and y2 > y1:
            scaled.append((x1, y1, x2, y2))
    return scaled


def area_percent(image: Image.Image, boxes: list[tuple[int, int, int, int]]) -> float:
    """Union of box areas as a percent of the image, measured with Pillow."""
    width, height = image.size
    total = width * height
    if total <= 0 or not boxes:
        return 0.0
    mask = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for box in boxes:
        draw.rectangle(box, fill=1)
    covered = mask.histogram()[1] if mask.histogram() else 0
    return round(100.0 * covered / total, 1)


def measurement_for(
    image: Image.Image,
    prompt: str,
    boxes: list[dict[str, float]],
    error: str | None = None,
) -> dict[str, Any]:
    width, height = image.size
    scaled = scale_boxes(boxes, width, height)
    percent = area_percent(image, scaled)
    found = bool(scaled) and not error
    finding = None
    if error:
        finding = f"sam-3.1 failed for {prompt}"
    elif not found:
        finding = f"no {prompt}"
    return {
        "prompt": prompt,
        "found": found,
        "area_percent": percent if found else 0.0,
        "box_count": len(scaled),
        "boxes": [{"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3]} for b in scaled],
        "finding": finding,
        "error": error,
    }


def draw_overlay(image: Image.Image, measurements: list[dict[str, Any]]) -> Image.Image:
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    for item in measurements:
        color = COLORS.get(item["prompt"], (20, 20, 20))
        for box in item.get("boxes") or []:
            rect = (box["x1"], box["y1"], box["x2"], box["y2"])
            draw.rectangle(rect, outline=color, width=4)
            label = f"{item['prompt']} {item['area_percent']}%"
            text_w = 7 * len(label) + 10
            text_h = 16
            x = box["x1"] + 4
            y = box["y1"] + 4
            draw.rectangle((x, y, x + text_w, y + text_h), fill=color)
            draw.text((x + 4, y + 2), label, fill=(255, 255, 255))
    return canvas


def jpeg_bytes(image: Image.Image, quality: int = 86) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
