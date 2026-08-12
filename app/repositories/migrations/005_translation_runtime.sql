ALTER TABLE translation_generations
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'normal';

ALTER TABLE translation_generations
    ADD COLUMN current_page_index INTEGER;

ALTER TABLE translation_pages
    ADD COLUMN width INTEGER;

ALTER TABLE translation_pages
    ADD COLUMN height INTEGER;

ALTER TABLE translation_pages
    ADD COLUMN display_parts_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX translation_generations_active_idx
    ON translation_generations(comic_id, chapter_id, status, created_at);
