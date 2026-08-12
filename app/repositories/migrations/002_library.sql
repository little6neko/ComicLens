CREATE TABLE favorites (
    comic_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    cover_source_url TEXT NOT NULL,
    rating REAL,
    is_adult INTEGER NOT NULL DEFAULT 0 CHECK (is_adult IN (0, 1)),
    latest_chapters_json TEXT NOT NULL DEFAULT '[]',
    favorited_at INTEGER NOT NULL
);

CREATE INDEX favorites_recent_idx ON favorites(favorited_at DESC);

CREATE TABLE reading_history (
    comic_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    cover_source_url TEXT NOT NULL,
    rating REAL,
    is_adult INTEGER NOT NULL DEFAULT 0 CHECK (is_adult IN (0, 1)),
    latest_chapters_json TEXT NOT NULL DEFAULT '[]',
    chapter_id TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    total_pages INTEGER NOT NULL CHECK (total_pages >= 1),
    updated_at INTEGER NOT NULL
);

CREATE INDEX reading_history_recent_idx ON reading_history(updated_at DESC);

CREATE TABLE read_chapters (
    comic_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    read_at INTEGER NOT NULL,
    PRIMARY KEY (comic_id, chapter_id)
);

CREATE INDEX read_chapters_comic_idx ON read_chapters(comic_id, read_at DESC);
