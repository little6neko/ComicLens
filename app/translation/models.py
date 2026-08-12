from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class TextBlock:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float | None = None
    source_path: str | None = None
    translation: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TextBlock:
        bbox = value.get("bbox")
        if not isinstance(bbox, list | tuple) or len(bbox) != 4:
            raise ValueError("text block bbox is invalid")
        return cls(
            text=str(value.get("text") or ""),
            bbox=tuple(int(item) for item in bbox),  # type: ignore[arg-type]
            confidence=(
                float(value["confidence"])
                if isinstance(value.get("confidence"), int | float)
                else None
            ),
            source_path=(
                str(value["source_path"]) if value.get("source_path") is not None else None
            ),
            translation=(
                str(value["translation"]) if value.get("translation") is not None else None
            ),
        )
