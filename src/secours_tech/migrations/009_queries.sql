-- The query log of the web API (J5): one row per question asked. It holds no IP address and no
-- user identifier. The question itself may hold personal data typed by a user (the page asks
-- them not to), so rows are deleted after 90 days (secours_tech.api.RETENTION). The tokens feed
-- the daily cost cap; the status, the kinds of blocking checks and the pages cited feed the
-- dashboard to come.
CREATE TABLE queries (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asked_at          timestamptz NOT NULL DEFAULT now(),
    question          text NOT NULL,
    status            text NOT NULL,  -- answered, clarification, refused, blocked, error
    problems          text[] NOT NULL DEFAULT '{}',  -- kinds of check, not their details
    pages             text[] NOT NULL DEFAULT '{}',  -- "file.pdf#page" cited by the answer
    top_similarity    real,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    seconds           real NOT NULL
);
CREATE INDEX queries_asked_at_idx ON queries (asked_at);
