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
