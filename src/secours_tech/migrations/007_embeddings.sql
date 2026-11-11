-- Embeddings are keyed by model and by a hash of the text embedded (the chunk's context header,
-- then its text: D7), not by chunk id: rebuilding the chunks (--reextract, ocr_bench index)
-- keeps every vector whose input did not change, so no model is paid for twice.

-- What a model embeds for a chunk: its context header, a new line, its text.
CREATE FUNCTION chunk_input(context text, body text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN CASE WHEN coalesce(context, '') = '' THEN body ELSE context || E'\n' || body END;

-- convert_to is only STABLE (it depends on the server encoding in general); with the target
-- encoding fixed to UTF8 the result cannot change, so the wrapper may be declared IMMUTABLE and
-- used in a generated column.
CREATE FUNCTION chunk_input_sha256(context text, body text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN encode(sha256(convert_to(chunk_input(context, body), 'UTF8')), 'hex');

ALTER TABLE chunks
    ADD COLUMN input_sha256 text GENERATED ALWAYS AS (chunk_input_sha256(context, text)) STORED;
CREATE INDEX chunks_input_sha256_idx ON chunks (input_sha256);

-- One table for every model: dimensions differ (1024 for mistral-embed, 768 for e5-base), so the
-- column is an untyped vector. A few thousand chunks are searched exactly, without an index.
CREATE TABLE embeddings (
    model        text NOT NULL,
    input_sha256 text NOT NULL,
    embedding    vector NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model, input_sha256)
);

-- The vector column planned in 001 is replaced by this table (vectors per model and per input).
ALTER TABLE chunks DROP COLUMN embedding;
