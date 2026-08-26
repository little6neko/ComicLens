CREATE TABLE translation_batches (
    batch_id TEXT PRIMARY KEY,
    comic_id TEXT NOT NULL,
    comic_title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued',
        'running',
        'pausing',
        'paused',
        'cancelling',
        'completed',
        'completed_with_errors',
        'cancelled',
        'failed'
    )),
    pause_reason TEXT CHECK (pause_reason IS NULL OR pause_reason IN ('user', 'config')),
    interactive_yielded INTEGER NOT NULL DEFAULT 0
        CHECK (interactive_yielded IN (0, 1)),
    error_code TEXT,
    error_summary TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX translation_batches_open_comic_idx
    ON translation_batches(comic_id)
    WHERE status NOT IN ('completed', 'cancelled');

CREATE INDEX translation_batches_schedule_idx
    ON translation_batches(status, created_at, batch_id);

CREATE TABLE translation_batch_items (
    batch_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES translation_batches(batch_id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending',
        'running',
        'completed',
        'skipped',
        'failed',
        'cancelled'
    )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code TEXT,
    error_summary TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (batch_id, chapter_id),
    UNIQUE (batch_id, position)
);

CREATE INDEX translation_batch_items_schedule_idx
    ON translation_batch_items(batch_id, status, position);

ALTER TABLE translation_generations
    ADD COLUMN batch_item_id TEXT
    REFERENCES translation_batch_items(batch_item_id) ON DELETE SET NULL;

CREATE UNIQUE INDEX translation_generations_batch_item_idx
    ON translation_generations(batch_item_id)
    WHERE batch_item_id IS NOT NULL;
