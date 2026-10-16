-- D18: the section tree of each guide, read from its printed table of contents. Each chunk
-- belongs to the section where its text is printed and carries the path "guide › chapter ›
-- section" as context, for full-text search now and for the embedding later; the table of
-- contents pages give no chunk.
CREATE TABLE sections (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal     integer NOT NULL CHECK (ordinal >= 0),  -- order in the table of contents
    level       integer NOT NULL CHECK (level > 0),
    title       text NOT NULL CHECK (title <> ''),
    path        text NOT NULL,  -- titles from the top level down, joined with " › "
    page        integer NOT NULL CHECK (page > 0),  -- PDF page where the heading is printed
    UNIQUE (document_id, ordinal)
);

ALTER TABLE pages ADD COLUMN is_toc boolean NOT NULL DEFAULT false;

ALTER TABLE chunks
    ADD COLUMN section_id bigint REFERENCES sections (id) ON DELETE SET NULL,
    ADD COLUMN context text;  -- "guide › chapter › section", never shown as a quote

-- the search vector now covers the context too: a passage is found by its section's words
DROP INDEX chunks_tsv_idx;
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('fr', coalesce(context, '') || ' ' || text)) STORED;
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_section_idx ON chunks (section_id);
