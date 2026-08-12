CREATE TABLE media_sources (
    media_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('cover', 'original')),
    comic_id TEXT NOT NULL,
    chapter_id TEXT,
    page_index INTEGER,
    source_url TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (kind, comic_id, chapter_id, page_index)
);

CREATE INDEX media_sources_comic_idx
    ON media_sources(comic_id, chapter_id, page_index);
