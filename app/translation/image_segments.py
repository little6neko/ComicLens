from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageStat

from app.translation.models import TextBlock


@dataclass(frozen=True, slots=True)
class VerticalSlice:
    index: int
    top: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_dict(self) -> dict[str, int]:
        return {
            "index": self.index,
            "top": self.top,
            "bottom": self.bottom,
            "height": self.height,
        }


def plan_vertical_slices(
    image: Image.Image,
    target_height: int,
    overlap: int = 0,
    min_height: int = 2800,
    aspect_ratio_threshold: float = 2.6,
    search_radius: int = 180,
    band_height: int = 10,
) -> list[VerticalSlice]:
    _width, height = image.size
    if not should_split_long_image(
        image_size=image.size,
        min_height=min_height,
        aspect_ratio_threshold=aspect_ratio_threshold,
    ):
        return [VerticalSlice(index=1, top=0, bottom=height)]

    overlap = max(0, min(overlap, max(0, target_height // 3)))
    grayscale = image.convert("L")
    min_last_slice_height = max(320, target_height // 3)

    slices: list[VerticalSlice] = []
    top = 0
    index = 1

    while top < height:
        tentative_bottom = min(height, top + target_height)
        if tentative_bottom >= height:
            bottom = height
        else:
            bottom = _find_cut_position(
                grayscale=grayscale,
                target_y=tentative_bottom,
                min_y=min(
                    height - min_last_slice_height,
                    top + max(420, target_height // 2),
                ),
                max_y=max(
                    top + max(420, target_height // 2),
                    height - min_last_slice_height,
                ),
                search_radius=search_radius,
                band_height=band_height,
            )
            if height - bottom < min_last_slice_height:
                bottom = height

        slices.append(VerticalSlice(index=index, top=top, bottom=bottom))
        if bottom >= height:
            break

        next_top = max(0, bottom - overlap)
        if next_top <= top:
            next_top = bottom
        top = next_top
        index += 1

    return slices


def should_split_long_image(
    image_size: tuple[int, int],
    min_height: int,
    aspect_ratio_threshold: float,
) -> bool:
    width, height = image_size
    if width <= 0:
        return False
    return height >= min_height and (height / width) >= aspect_ratio_threshold


def crop_vertical_slice(image: Image.Image, image_slice: VerticalSlice) -> Image.Image:
    return image.crop((0, image_slice.top, image.width, image_slice.bottom)).copy()


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def shift_text_blocks(
    text_blocks: list[TextBlock],
    offset_y: int,
    segment_index: int,
) -> None:
    for block in text_blocks:
        x1, y1, x2, y2 = block.bbox
        block.bbox = (x1, y1 + offset_y, x2, y2 + offset_y)
        if block.source_path:
            block.source_path = f"{block.source_path}|segment={segment_index}"


def dedupe_text_blocks(text_blocks: list[TextBlock]) -> list[TextBlock]:
    deduped: list[TextBlock] = []
    for block in sorted(text_blocks, key=lambda item: (item.bbox[1], item.bbox[0], len(item.text))):
        if _looks_duplicate(block, deduped):
            continue
        deduped.append(block)
    return deduped


def _find_cut_position(
    grayscale: Image.Image,
    target_y: int,
    min_y: int,
    max_y: int,
    search_radius: int,
    band_height: int,
) -> int:
    height = grayscale.height
    min_y = max(band_height, min_y)
    max_y = min(height - band_height, max_y)
    if min_y >= max_y:
        return max(band_height, min(height - band_height, target_y))

    search_start = max(min_y, target_y - search_radius)
    search_end = min(max_y, target_y + search_radius)
    if search_start >= search_end:
        return max(min_y, min(max_y, target_y))

    best_y = max(min_y, min(max_y, target_y))
    best_score: float | None = None
    for y in range(search_start, search_end + 1, 8):
        band = grayscale.crop(
            (
                0,
                max(0, y - band_height),
                grayscale.width,
                min(height, y + band_height),
            )
        )
        brightness = ImageStat.Stat(band).mean[0]
        distance_penalty = abs(y - target_y) * 0.08
        score = brightness - distance_penalty
        if best_score is None or score > best_score:
            best_score = score
            best_y = y
    return best_y


def _looks_duplicate(candidate: TextBlock, existing_blocks: list[TextBlock]) -> bool:
    candidate_text = _normalize_text(candidate.text)
    if not candidate_text:
        return True

    for existing in existing_blocks:
        if _normalize_text(existing.text) != candidate_text:
            continue
        if _bbox_iou(candidate.bbox, existing.bbox) >= 0.35:
            return True
        if (
            _vertical_overlap_ratio(candidate.bbox, existing.bbox) >= 0.65
            and abs(_center_y(candidate.bbox) - _center_y(existing.bbox)) <= 40
        ):
            return True
    return False


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _center_y(bbox: tuple[int, int, int, int]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _vertical_overlap_ratio(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    overlap = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    smallest_height = max(1, min(left[3] - left[1], right[3] - right[1]))
    return overlap / smallest_height


def _bbox_iou(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> float:
    inter_left = max(left[0], right[0])
    inter_top = max(left[1], right[1])
    inter_right = min(left[2], right[2])
    inter_bottom = min(left[3], right[3])
    inter_width = max(0, inter_right - inter_left)
    inter_height = max(0, inter_bottom - inter_top)
    intersection = inter_width * inter_height
    if intersection <= 0:
        return 0.0

    left_area = max(1, (left[2] - left[0]) * (left[3] - left[1]))
    right_area = max(1, (right[2] - right[0]) * (right[3] - right[1]))
    return intersection / max(1, left_area + right_area - intersection)
