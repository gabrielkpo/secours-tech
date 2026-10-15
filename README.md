# SecoursTech

Assistant that answers operational questions from the official French firefighter doctrine
(GDO, GTO, SSUAP, PSE), and **always cites the document and page** it relied on.

> Status: work in progress (v3, rebuilt from scratch). See `docs/architecture.md`.

## Why a rebuild
v2 called the LLM straight from the browser, which exposed the API key in the JS bundle and
shipped 170+ MB of PDFs inside Git. v3 keeps the key on the server, rate-limits the API and
downloads the documents at build time.

## Quick start
Needs Postgres 17 with the pgvector extension (e.g. `brew install postgresql@17 pgvector`).
```
uv sync --group dev
createdb secours_tech
export DATABASE_URL=postgresql:///secours_tech
uv run python -m secours_tech.load data/raw      # applies migrations, then loads every PDF
```
Until the scraper exists, `data/raw/` is filled by hand with the official PDFs (kept out of Git).
Loading is idempotent: a file whose SHA-256 is already in `documents` is skipped.

Tests: the database tests run against a dedicated database whose name must end in `_test`
(they wipe it), and are skipped when `TEST_DATABASE_URL` is not set.
```
createdb secours_tech_test
TEST_DATABASE_URL=postgresql:///secours_tech_test uv run pytest
```
The ingestion step cuts on sentence boundaries, drops repeated headers and footers, and logs a
warning for every page with no extractable text (scanned pages need OCR, not handled yet).
