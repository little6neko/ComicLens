ALTER TABLE translation_batches
    ADD COLUMN resume_requested INTEGER NOT NULL DEFAULT 0
    CHECK (resume_requested IN (0, 1));
