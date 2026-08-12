CREATE TABLE translation_generations (
    generation_id TEXT PRIMARY KEY,
    comic_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    semantic_settings_json TEXT NOT NULL,
    status TEXT NOT NULL,
    stop_requested INTEGER NOT NULL DEFAULT 0 CHECK (stop_requested IN (0, 1)),
    total_pages INTEGER NOT NULL DEFAULT 0,
    completed_pages INTEGER NOT NULL DEFAULT 0,
    failed_pages INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX translation_generations_chapter_idx
    ON translation_generations(comic_id, chapter_id, created_at DESC);

CREATE TABLE translation_pages (
    generation_id TEXT NOT NULL REFERENCES translation_generations(generation_id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    status TEXT NOT NULL,
    original_path TEXT,
    ocr_path TEXT,
    blocks_path TEXT,
    translations_path TEXT,
    translated_path TEXT,
    translated_version TEXT,
    error_stage TEXT,
    error_code TEXT,
    error_summary TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (generation_id, page_index)
);

CREATE TABLE active_translation_pages (
    comic_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    generation_id TEXT NOT NULL REFERENCES translation_generations(generation_id) ON DELETE CASCADE,
    translated_path TEXT NOT NULL,
    translated_version TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (comic_id, chapter_id, page_index)
);

CREATE TABLE cache_bundles (
    bundle_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('cover', 'chapter')),
    comic_id TEXT NOT NULL,
    chapter_id TEXT,
    byte_size INTEGER NOT NULL DEFAULT 0 CHECK (byte_size >= 0),
    accessed_at INTEGER NOT NULL,
    protected_until INTEGER NOT NULL DEFAULT 0,
    active_task INTEGER NOT NULL DEFAULT 0 CHECK (active_task IN (0, 1)),
    UNIQUE (kind, comic_id, chapter_id)
);

CREATE INDEX cache_bundles_lru_idx ON cache_bundles(accessed_at);

CREATE TABLE cache_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_key TEXT NOT NULL REFERENCES cache_bundles(bundle_key) ON DELETE CASCADE,
    relative_path TEXT NOT NULL UNIQUE,
    entry_kind TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    checksum TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX cache_entries_bundle_idx ON cache_entries(bundle_key);
