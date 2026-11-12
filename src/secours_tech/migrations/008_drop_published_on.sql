-- documents.published_on was never written nor read: the manifest records no publication date,
-- and an edition is told apart by its fingerprint and version.
ALTER TABLE documents DROP COLUMN published_on;
