ALTER TABLE translation_generations
    ADD COLUMN planning_complete INTEGER NOT NULL DEFAULT 0
    CHECK (planning_complete IN (0, 1));

ALTER TABLE translation_generations
    ADD COLUMN total_segments INTEGER NOT NULL DEFAULT 0;

ALTER TABLE translation_generations
    ADD COLUMN completed_segments INTEGER NOT NULL DEFAULT 0;

ALTER TABLE translation_generations
    ADD COLUMN failed_segments INTEGER NOT NULL DEFAULT 0;

ALTER TABLE translation_generations
    ADD COLUMN current_segment_index INTEGER;

ALTER TABLE translation_pages
    ADD COLUMN source_url TEXT;

ALTER TABLE translation_pages
    ADD COLUMN original_checksum TEXT;

ALTER TABLE translation_pages
    ADD COLUMN prepared INTEGER NOT NULL DEFAULT 0
    CHECK (prepared IN (0, 1));

CREATE TABLE translation_segments (
    generation_id TEXT NOT NULL,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
    global_index INTEGER NOT NULL CHECK (global_index >= 0),
    status TEXT NOT NULL DEFAULT 'pending',
    source_width INTEGER NOT NULL CHECK (source_width > 0),
    source_height INTEGER NOT NULL CHECK (source_height > 0),
    display_top INTEGER NOT NULL CHECK (display_top >= 0),
    display_bottom INTEGER NOT NULL CHECK (display_bottom > display_top),
    ocr_top INTEGER NOT NULL CHECK (ocr_top >= 0),
    ocr_bottom INTEGER NOT NULL CHECK (ocr_bottom > ocr_top),
    ocr_input_path TEXT NOT NULL,
    ocr_path TEXT,
    blocks_path TEXT,
    translations_path TEXT,
    translated_path TEXT,
    translated_version TEXT,
    ocr_job_id TEXT,
    error_stage TEXT,
    error_code TEXT,
    error_summary TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (generation_id, page_index, segment_index),
    UNIQUE (generation_id, global_index),
    FOREIGN KEY (generation_id, page_index)
        REFERENCES translation_pages(generation_id, page_index)
        ON DELETE CASCADE
);

CREATE INDEX translation_segments_pending_idx
    ON translation_segments(generation_id, status, global_index);

CREATE TABLE active_translation_segments (
    comic_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    page_index INTEGER NOT NULL CHECK (page_index >= 0),
    generation_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
    display_top INTEGER NOT NULL CHECK (display_top >= 0),
    display_bottom INTEGER NOT NULL CHECK (display_bottom > display_top),
    source_width INTEGER NOT NULL CHECK (source_width > 0),
    source_height INTEGER NOT NULL CHECK (source_height > 0),
    translated_path TEXT NOT NULL,
    translated_version TEXT NOT NULL,
    published_at INTEGER NOT NULL,
    PRIMARY KEY (
        comic_id, chapter_id, page_index, generation_id, segment_index
    ),
    FOREIGN KEY (generation_id, page_index, segment_index)
        REFERENCES translation_segments(generation_id, page_index, segment_index)
        ON DELETE CASCADE
);

CREATE INDEX active_translation_segments_page_idx
    ON active_translation_segments(comic_id, chapter_id, page_index, published_at);
