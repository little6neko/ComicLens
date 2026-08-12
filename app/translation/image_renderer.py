from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from app.translation.models import TextBlock

RENDERER_VERSION = "comic-translator-overlay-v1"
DEFAULT_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Light.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)


def sanitize_image(raw_bytes: bytes) -> tuple[Image.Image, bytes]:
    with Image.open(BytesIO(raw_bytes)) as image:
        image = ImageOps.exif_transpose(image)
        if getattr(image, "is_animated", False):
            image.seek(0)
        canvas = image.convert("RGB")

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return canvas, buffer.getvalue()


def render_translated_image(
    image: Image.Image,
    text_blocks: list[TextBlock],
    font_path: str = "",
) -> Image.Image:
    canvas = image.convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    for block in sorted(text_blocks, key=_bbox_area, reverse=True):
        translated = (block.translation or block.text).strip()
        if not translated:
            continue
        bbox = _expand_bbox(block.bbox, canvas.size)
        fill_color = _estimate_fill_color(canvas, bbox)
        draw.rounded_rectangle(
            bbox,
            radius=max(6, min((bbox[2] - bbox[0]), (bbox[3] - bbox[1])) // 7),
            fill=fill_color + (236,),
        )
        _draw_text_in_box(
            draw=draw,
            bbox=bbox,
            text=translated,
            fill_color=fill_color,
            font_path=font_path,
        )

    return canvas.convert("RGB")


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def font_identity(font_path: str = "") -> str:
    if font_path and Path(font_path).is_file():
        path = Path(font_path)
        return f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    for candidate in DEFAULT_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    return "pillow-default"


def _bbox_area(block: TextBlock) -> int:
    x1, y1, x2, y2 = block.bbox
    return (x2 - x1) * (y2 - y1)


def _expand_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    width, height = image_size
    pad_x = max(4, int((x2 - x1) * 0.08))
    pad_y = max(4, int((y2 - y1) * 0.12))
    return (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(width, x2 + pad_x),
        min(height, y2 + pad_y),
    )


def _estimate_fill_color(
    image: Image.Image,
    bbox: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    region = image.crop(bbox).convert("RGB")
    stats = ImageStat.Stat(region)
    mean = [int(value) for value in stats.mean]
    luminance = (0.299 * mean[0]) + (0.587 * mean[1]) + (0.114 * mean[2])
    if luminance >= 128:
        return tuple(min(255, int(channel * 1.08 + 10)) for channel in mean)
    return tuple(max(12, int(channel * 0.72)) for channel in mean)


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    text: str,
    fill_color: tuple[int, int, int],
    font_path: str,
) -> None:
    x1, y1, x2, y2 = bbox
    box_width = max(12, x2 - x1 - 12)
    box_height = max(12, y2 - y1 - 12)

    max_font_size = max(14, min(48, int(box_height * 0.7)))
    min_font_size = 12
    chosen_font = None
    chosen_lines: list[str] = []
    chosen_spacing = 0

    for font_size in range(max_font_size, min_font_size - 1, -1):
        font = _load_font(font_size, font_path)
        lines = _wrap_text(draw, text, font, box_width)
        line_height = _text_height(font)
        spacing = max(1, font_size // 10)
        total_height = (line_height * len(lines)) + (spacing * max(0, len(lines) - 1))
        if total_height <= box_height:
            chosen_font = font
            chosen_lines = lines
            chosen_spacing = spacing
            break

    if chosen_font is None:
        chosen_font = _load_font(min_font_size, font_path)
        chosen_lines = _wrap_text(draw, text, chosen_font, box_width)
        chosen_spacing = 1
        max_lines = max(1, box_height // max(1, _text_height(chosen_font)))
        chosen_lines = _truncate_lines(draw, chosen_lines, chosen_font, box_width, max_lines)

    line_height = _text_height(chosen_font)
    total_height = (line_height * len(chosen_lines)) + (
        chosen_spacing * max(0, len(chosen_lines) - 1)
    )
    start_y = y1 + max(6, (box_height - total_height) // 2 + 6)

    luminance = (0.299 * fill_color[0]) + (0.587 * fill_color[1]) + (0.114 * fill_color[2])
    text_color = (34, 34, 34) if luminance >= 140 else (248, 248, 248)
    stroke_color = (250, 250, 250) if luminance < 140 else (255, 255, 255)
    font_size = getattr(chosen_font, "size", 12)
    stroke_width = 0 if font_size <= 28 else 1

    current_y = start_y
    for line in chosen_lines:
        text_width = draw.textlength(line, font=chosen_font)
        current_x = x1 + max(6, int((box_width - text_width) / 2) + 6)
        if stroke_width > 0:
            draw.text(
                (current_x, current_y),
                line,
                font=chosen_font,
                fill=text_color,
                stroke_width=stroke_width,
                stroke_fill=stroke_color,
            )
        else:
            draw.text(
                (current_x, current_y),
                line,
                font=chosen_font,
                fill=text_color,
            )
        current_y += line_height + chosen_spacing


def _truncate_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    if len(lines) <= max_lines:
        return lines

    truncated = lines[:max_lines]
    last = truncated[-1]
    ellipsis = "..."
    while last and draw.textlength(last + ellipsis, font=font) > max_width:
        last = last[:-1]
    truncated[-1] = (last + ellipsis).strip()
    return truncated


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    lines: list[str] = []
    for paragraph in paragraphs:
        tokens = paragraph.split(" ") if " " in paragraph else list(paragraph)
        current = ""
        joiner = " " if " " in paragraph else ""
        for token in tokens:
            candidate = token if not current else f"{current}{joiner}{token}"
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            current = token
        if current:
            lines.append(current)
    return lines or [text]


def _text_height(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    _left, top, _right, bottom = font.getbbox("汉")
    return max(1, bottom - top)


@lru_cache(maxsize=128)
def _load_font(
    font_size: int,
    font_path: str = "",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path] if font_path else []
    candidates.extend(DEFAULT_FONT_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        if not Path(candidate).exists():
            continue
        try:
            return ImageFont.truetype(candidate, font_size)
        except OSError:
            continue
    return ImageFont.load_default()
