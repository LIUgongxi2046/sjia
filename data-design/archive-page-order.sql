ALTER TABLE sjia.archive_page ADD COLUMN ordinal integer NOT NULL;
ALTER TABLE sjia.archive_page ADD CONSTRAINT archive_page_ordinal_positive CHECK (ordinal > 0);
ALTER TABLE sjia.archive_page ADD CONSTRAINT archive_page_batch_ordinal_unique UNIQUE (batch_id, ordinal);
