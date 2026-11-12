# Decisions

The code and the notes refer to these decisions by number. Each line gives the decision, the
option set aside and why. Dates are the day the decision was taken or last revised.

| # | Decision | Set aside | Why | Date |
|---|---|---|---|---|
| D5 | Mistral OCR (`mistral-ocr-4-1`, one page sent as a text-only PDF) reads the text that may be indexed; Tesseract (French `tessdata_best`) stays as a local checker of suspect text layers at load time | Tesseract alone; macOS Vision | On the hand-checked sample, 10 of 14 guides read reliably with Mistral OCR against 2 with tuned Tesseract; the checker stays local and needs no key | 2026-10-16 |
| D7 | Every chunk starts with a context header (`guide › chapter › section`), for the embedding and the full-text search | No context; a context written by an LLM | A simple form of contextual retrieval: free, and a chunk keeps the meaning its section gives it | 2026-10-15 |
| D10 | Hybrid retrieval: vectors plus French full text, fused by reciprocal rank (k = 60) | Vectors only | The doctrine is full of acronyms, distances and form numbers that exact matching finds | 2026-10-15 |
| D11 | Rerank the first chunks with `bge-reranker-v2-m3` (local) only if the bench shows a gain | Always; never | No reranker on the Mistral API; measure before adding a stage | 2026-10-15 |
| D18 | Sections come from the printed table of contents (fallback: headings detected in the text); each chunk records its section path; pages are cut at section boundaries; contents pages are left out of the search | Chunks with no structure | Contents pages pollute the search; a procedure needs its whole section; PDF bookmarks are missing from almost every guide | 2026-10-15 |
| D20 | Multi-vector reranking (late interaction, one vector per token) with BGE-M3, local, on the first 50 chunks, without storing per-token vectors; compared with D11 on the bench | Per-token vectors stored in the database (gigabytes; pgvector cannot compute the max-sim score) | The doctrine's acronyms, form codes and figures are diluted in a single vector; BGE-M3 is multilingual, local and free | 2026-11-11 |

Measured outcomes of D10, D11 and D20 are in [EVALUATION.md](EVALUATION.md): on the current
bench the vectors alone beat the hybrid fusion, and reranking gains precision at a latency far
above the 8 s target, so neither is in the planned serving path yet.
