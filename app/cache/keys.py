from __future__ import annotations

import hashlib


def identity_digest(*parts: object) -> str:
    identity = "\0".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def cover_bundle_key(comic_id: str) -> str:
    return f"cover:{identity_digest('cover', comic_id)}"


def cover_path(comic_id: str) -> str:
    return f"covers/{identity_digest('cover', comic_id)}.img"


def chapter_digest(comic_id: str, chapter_id: str) -> str:
    return identity_digest("chapter", comic_id, chapter_id)


def chapter_bundle_key(comic_id: str, chapter_id: str) -> str:
    return f"chapter:{chapter_digest(comic_id, chapter_id)}"


def original_path(comic_id: str, chapter_id: str, page_index: int) -> str:
    return f"chapters/{chapter_digest(comic_id, chapter_id)}/originals/{page_index:05d}.img"


def generation_directory(comic_id: str, chapter_id: str, generation_id: str) -> str:
    return f"chapters/{chapter_digest(comic_id, chapter_id)}/generations/{generation_id}"


def generation_page_path(
    comic_id: str,
    chapter_id: str,
    generation_id: str,
    directory: str,
    page_index: int,
    suffix: str,
) -> str:
    root = generation_directory(comic_id, chapter_id, generation_id)
    return f"{root}/{directory}/{page_index:05d}.{suffix}"
