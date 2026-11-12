# Running the pipeline

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/); `uv sync --locked` installs the locked
  dependencies, `uv sync --locked --extra local` adds torch and transformers for the local
  embedding and the rerankers.
- Postgres 17 with the `pgvector` and `unaccent` extensions (migration 001 creates them).
- poppler (`pdftotext`, `pdfimages`, `pdftoppm`) and Tesseract (`tesseract`, a local checker of
  suspect text layers; its French model is fetched and checked by SHA-256 on first use).
- A `.env` copied from `.env.example`: `DATABASE_URL`, and `MISTRAL_API_KEY` for OCR and
  embeddings. The environment, when set, takes precedence over `.env`.
- The official PDFs listed in `corpus/sources.toml`. The ministry's site blocks automated
  clients, so they are downloaded by hand in a browser, then filed and checked:

```bash
uv run python -m secours_tech.collect register ~/Downloads
```

`register` files each PDF under `data/raw/<family>/` when its SHA-256 matches the manifest. A
file whose fingerprint changed is refused, with both editions described, until
`--accept-new-version <file name>` names it; the manifest is then updated, for a pull request.

## The pipeline, in order

| Step | Command | Paid API | Notes |
|---|---|---|---|
| Load | `python -m secours_tech.load data/raw` | no | Text layer, page routes, sections, chunks. Refuses a PDF outside the manifest (`--allow-local` loads it as a local document that retrieval never returns). Running it twice changes nothing; `--reextract` applies a change of extraction. |
| OCR | `python -m secours_tech.ocr data/raw --dry-run`, then without it | Mistral OCR, one call per page routed to OCR (about 1,200) | `--limit N` reads at most N pages; failed or empty readings are kept for the next run. |
| OCR gate and index | `python -m secours_tech.ocr_bench index` | no | Needs the hand transcriptions in `data/ocr-reference/` (not published); writes `corpus/ocr-validation.toml`. |
| Embeddings | `python -m secours_tech.embed --model mistral --dry-run`, then without it | Mistral embeddings, only the chunk inputs without a vector | `--model e5` runs locally (local extra). |
| Bench check | `python -m secours_tech.bench check` | no | Needs the cases in `data/eval/cases/` (not published). |
| Bench run | `python -m secours_tech.bench run --retriever vector --model e5` | only with `--model mistral` (one short call per question) | Writes a record to `eval/runs/`; `bench report eval/runs/*.json` prints the table. |

Every command is run with `uv run` from the repository root. A run's record names the commit it
used and warns when the working tree had uncommitted changes.

## With Docker

The image (`Dockerfile`) holds the package, its locked dependencies, poppler and tesseract, and
runs as an unprivileged user; `compose.yaml` adds a local Postgres with pgvector, listening on
the loopback only. Set `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` in `.env`, then:

```bash
docker compose up -d --build db migrate                  # image, database, role and schema
docker compose run --rm pipeline secours_tech.load data/raw
```

`--build` rebuilds the image after a code change (without it, the last image built keeps
running). The bench runs from the checkout, not the container: a run's record names its commit,
which the image does not carry. Point it at the compose database:
`DATABASE_URL=postgresql://secours_tech:<APP_DB_PASSWORD>@localhost:5433/secours_tech uv run python -m secours_tech.bench run ...`.

The pipeline connects as `secours_tech`, owner of its database but not a superuser; the
extensions are created once by `deploy/compose/initdb.sql`. `data/`, `eval/` and `corpus/` are
mounted: `data/raw` must hold the PDF files themselves (a symbolic link to a folder outside the
repository is not visible in the container), and on Linux the container's user (uid 10001) must
be able to write to them.

## Tests

```bash
createdb secours_tech_test
TEST_DATABASE_URL=postgresql:///secours_tech_test uv run pytest --cov
```

The database tests wipe the schema of a database whose name must end in `_test`. The tests of
the local extra (`tests/test_local_extra.py`) run when torch is installed; CI runs them weekly
and on dependency changes.
