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
- **Full-text vector computed by Postgres** (`tsv`, text search configuration `fr`: `unaccent`
  then the French stemmer), so it cannot drift from the chunk text and "electricite" finds
  "électricité". The French stemmer handles nouns and infinitives ("ventilation" finds
  "ventiler") but not every conjugated form ("ventilent"): semantic search covers that gap.
  Queries use `websearch_to_tsquery`, which never fails on free text.
- **Each chunk records how its text was extracted** (`extraction`: `text_layer` or `ocr`), so the
  effect of OCR on retrieval and citations can be measured.
- **Each page is routed, not guessed.** `pdftotext` (poppler) reads the text layer; `pdfimages`
  counts the images and the high-resolution 1-bit masks, which is how the ministry's Word
  exports draw lines of text. A page goes to `text_layer` (enough clean letters), `ocr` (3+ text
  masks, an image page, or a garbled layer) or `blank`; the `pages` table keeps the route and
  the measures. On the 28 official guides: 1,740 text, 1,069 OCR, 184 blank.
- **Official sources live in a manifest** (`corpus/sources.toml`: family, title, official URL,
  SHA-256), reviewed in PRs. The loader takes family, title and URL from it; a new file at the
  same URL becomes the next version and the previous one is marked `retired_at` (kept, so an
  earlier citation can still be traced).
- **Collection is watched, not forced.** The ministry's site (securite-civile.interieur.gouv.fr)
  answers 403 to automated clients. `collect check` identifies itself as a bot and does not pose
  as a browser; once a month a workflow runs it and opens a `corpus` issue when a page is blocked
  or differs from the manifest. A person downloads the PDFs in a browser and `collect register`
  files them and updates the manifest.
