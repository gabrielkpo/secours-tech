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
  Queries use `websearch_to_tsquery`, which never fails on free text. The vector covers the
  chunk's context ("guide › chapter › section") as well as its text.
- **Sections come from the printed table of contents (D18).** The guides carry almost no PDF
  bookmarks but print a table of contents. `structure.py` reads its lines from the
  layout-preserving text (`pdftotext -layout`), gives each a level (chapter or section label,
  then dotted numbering, then indentation), finds the shift between printed and PDF page
  numbers by looking for the titles on the pages, and drops a table of contents whose headings
  are mostly not found (a garbled one). Each page is cut where its headings are printed, so no
  chunk straddles two sections; each chunk gets its `section_id` and its `context`; the table
  of contents pages give no chunk. On the 28 guides: 12 have a usable table of contents (1,426
  sections, 44 contents pages left out) and 88.5 % of the chunks have a section. A guide
  without a usable table of contents falls back on its headings (`detect_headings`): fiche
  codes (`VEN-STR-PRO-1`, the family code above its fiches), chapter and section labels with
  their number or letter, and dotted numbering, a number alone on its line taking the next line
  as its title; running headers are removed first. A page where fiche codes make up a third of
  the lines, or where headings followed by page numbers that only go up make up half of them,
  is a printed table of contents: it gives neither headings nor chunks. Each section records its
  `origin`: `contents`, `headings` (text layer, at load time) or `ocr`. The OCR guides have no
  text layer to read at load time, so `ocr_bench index` detects their headings on the OCR text
  of a guide that passes, stores them as `ocr` (replaced at each run, kept by `--reextract`) and
  cuts the OCR pages at those headings like text pages. Ventilation gets 163 sections this
  way; the PSE flowcharts have no headings and stay without.
- **Each chunk records how its text was extracted** (`extraction`: `text_layer` or `ocr`), so the
  effect of OCR on retrieval and citations can be measured.
- **Each page is routed, not guessed.** `pdftotext` (poppler) reads the text layer; `pdfimages`
  counts the images and the high-resolution 1-bit masks, which is how the ministry's Word
  exports draw lines of text. A page goes to `text_layer` (enough clean letters), `ocr` (3+ text
  masks, an image page, or a garbled layer) or `blank`; the `pages` table keeps the route and
  the measures. A layer is garbled when it holds control characters, or almost no French
  function words that OCR does not confirm (a font without a ToUnicode table: the page shows
  the right letters, the layer holds others). Accents stored as separate combining marks are
  read in raw order and composed (NFC). `load --reextract` applies an extraction change to the
  guides already loaded, keeping their OCR text. On the 28 official guides: 1,617 text,
  1,192 OCR, 184 blank.
- **Official sources live in a manifest** (`corpus/sources.toml`: family, title, official URL,
  SHA-256), reviewed in PRs. The loader takes family, title and URL from it; a new file at the
  same URL becomes the next version and the previous one is marked `retired_at` (kept, so an
  earlier citation can still be traced).
- **Collection is watched, not forced.** The ministry's site (securite-civile.interieur.gouv.fr)
  answers 403 to automated clients. `collect check` identifies itself as a bot and does not pose
  as a browser; once a month a workflow runs it and opens a `corpus` issue when a page is blocked
  or differs from the manifest. A person downloads the PDFs in a browser and `collect register`
  files them and updates the manifest.
- **OCR is stored, then gated.** `python -m secours_tech.ocr` copies each page routed to `ocr`
  without its pictures (`pikepdf`: every image that is not a few-pixel text mask is removed, so
  photos, drawings and grey boxes neither hide text nor read as gibberish), renders it at
  600 dpi (`pdftoppm`) and reads it with Tesseract 5 (LSTM, Sauvola binarisation) and the French
  `tessdata_best` model, pinned to one commit and checked by SHA-256. These settings were chosen
  on the first hand-checked sample: guides passing went from 1 to 5 of 12, then 4 of 12 on the
  validation sample. Text, mean word confidence and the engine with its settings go on the page
  row; a page read with other settings is read again. Nothing is indexed yet. A guide's OCR text becomes chunks only if a hand-transcribed
  sample of its pages passes `python -m secours_tech.ocr_bench score`: every number read exactly
  (none missing, none added) and a character error rate of at most 1 %. Otherwise the guide
  stays uncovered. Only pages with text drawn as images, or whose text layer was rejected as
  garbled, are sampled and can be indexed; image pages (photos, diagrams) are OCRed for
  measurement but stay uncovered. The score compares
  text, not layout: the running footer is removed from the OCR text the way chunking removes
  it (lines repeated on half the pages, compared by their words so logo noise such as "@&"
  does not hide them). On both sides, list bullets (read by the OCR as a lone "e", "o" or
  dash), French typographic spaces, dash and quote styles, table-of-contents dot leaders and
  words cut at a line end are ignored. In v0 only text drawn as text counts: labels inside
  pictures (diagrams, sketches, posters) are neither scored nor indexed; they are transcribed
  apart (`img-p<N>.txt`) and `--with-image-labels` scores them for information. Two samples:
  one to tune the engine, and a validation sample drawn with another seed that excludes it
  (`sample --exclude`), so a setting is judged on pages it was not tuned on. Transcriptions
  live outside Git (`data/ocr-reference/`): they are doctrine text. `ocr_bench index` scores
  each guide on both samples together, gives the guides that pass OCR chunks
  (`extraction = 'ocr'`) and takes them away from the others, and writes the decision to
  `corpus/ocr-validation.toml` (scores only), versioned with the code.
