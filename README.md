# SecoursTech

[![CI](https://github.com/gabrielkpo/secours-tech/actions/workflows/ci.yml/badge.svg)](https://github.com/gabrielkpo/secours-tech/actions/workflows/ci.yml)

Assistant that answers operational questions from the official French firefighter doctrine
(GDO, GTO, SSUAP), and **always cites the document and page** it relied on.

> Status: work in progress (v3, rebuilt from scratch). See `docs/architecture.md`.

## Why a rebuild
v2 called the LLM straight from the browser, which exposed the API key in the JS bundle and
shipped 170+ MB of PDFs inside Git. v3 keeps the key on the server, rate-limits the API and
downloads the documents at build time.

## Quick start
```
uv sync --group dev
uv run python -m secours_tech.ingest path/to/pdfs data/index/chunks.jsonl
uv run pytest
```
The ingestion step cuts on sentence boundaries, drops repeated headers and footers, and logs a
warning for every page with no extractable text (scanned pages need OCR, not handled yet).

## License
The code is released under the [MIT License](LICENSE). The doctrine documents are not part of
this repository: they are published by the French Ministry of the Interior, whose reproduction
rights are reserved and strictly limited, and they may not be used for commercial purposes.
