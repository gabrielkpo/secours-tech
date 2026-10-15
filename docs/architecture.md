# Architecture (v3)

Goal: answer a question from official doctrine only, with a citation (document + page)
for every claim, and refuse when the documents do not contain the answer.

```
PDF (GDO/GTO/SSUAP) -> ingest -> chunks(doc, page, text) -> embeddings -> index
question -> guardrails -> retrieve top-k -> generate (Mistral) -> citation check -> answer
```

## Decisions
- **API key server-side only.** The browser talks to our API, never to Mistral. Requests are
  rate-limited per IP.
- **Chunks keep (document, page).** Citations are only possible if provenance survives ingestion.
- **Citation check by equality.** Every quoted passage in an answer must appear verbatim in the
  retrieved chunks, otherwise the answer is blocked.
- **Documents are not in Git.** They are downloaded by a script into `data/raw/`.
- **Evaluation first.** Expected answers are written before running the system and are never
  shown to the model.
- **One Postgres database** (pgvector) holds documents, chunks and, later, the query log and
  evaluation runs, so the dashboard can join them. Schema changes are plain SQL files in
  `src/secours_tech/migrations/`, applied once each in name order.
- **Full-text vector computed by Postgres** (`tsv`, French configuration), so it cannot drift
  from the chunk text. The French stemmer handles nouns and infinitives ("ventilation" finds
  "ventiler") but not every conjugated form ("ventilent"): semantic search covers that gap.
- **Textless pages are measured, not hidden.** `textless_page_count` counts pages that give no
  chunk. On the 29 current guides it is 758 of 3,139 pages (24%): many guides draw their text
  as images. OCR is what fixes it.
