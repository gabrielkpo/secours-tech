-- Versions of a guide. A new file at the same official URL is a new version: the previous
-- row is kept and marked retired, so an answer cited earlier can still be traced to its text.
ALTER TABLE documents ADD COLUMN retired_at timestamptz;

-- at most one current version per official URL
CREATE UNIQUE INDEX documents_current_url_idx
    ON documents (url) WHERE url IS NOT NULL AND retired_at IS NULL;
