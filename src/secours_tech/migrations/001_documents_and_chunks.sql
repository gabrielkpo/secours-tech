-- Documents and their chunks. One database holds the index, and later the query log and
-- the evaluation runs, so the dashboard can join them.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename           text NOT NULL,
    title              text NOT NULL,
    family             text NOT NULL CHECK (family IN ('GDO', 'GTO', 'SSUAP', 'PSE')),
    -- 'local' = copied by hand; 'web' = downloaded by the scraper (url is then required)
    source             text NOT NULL CHECK (source IN ('local', 'web')),
    url                text CHECK (source = 'local' OR url IS NOT NULL),
    -- the fingerprint of the file: the same bytes are never loaded twice
    sha256             text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    version            integer NOT NULL DEFAULT 1 CHECK (version > 0),
    published_on       date,
    collected_at       timestamptz NOT NULL DEFAULT now(),
    page_count         integer NOT NULL CHECK (page_count > 0),
    -- pages that give no chunk: scanned, text drawn as images, or only a header and a page
    -- number left once boilerplate is removed. They wait for OCR.
    textless_page_count integer NOT NULL CHECK (textless_page_count BETWEEN 0 AND page_count)
);

CREATE TABLE chunks (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    page        integer NOT NULL CHECK (page > 0),  -- 1-based, as printed in a PDF viewer
    position    integer NOT NULL CHECK (position >= 0),  -- order of the chunk in its document
    text        text NOT NULL CHECK (text <> ''),
    -- full-text search vector, computed by Postgres so it can never drift from `text`
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('french', text)) STORED,
    -- mistral-embed returns 1024 numbers; filled by the embedding step, empty until then
    embedding   vector(1024),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, position)
);

CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_document_page_idx ON chunks (document_id, page);
