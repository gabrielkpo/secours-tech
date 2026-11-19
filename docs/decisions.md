# Decisions

The code and the docs refer to these decisions by number. Each line gives the decision, the
option set aside and why. Dates are the day the decision was taken or last revised.

| # | Decision | Set aside | Why | Date |
|---|---|---|---|---|
| D5 | Mistral OCR (`mistral-ocr-4-1`, one page sent as a text-only PDF) reads the text that may be indexed; Tesseract (French `tessdata_best`) stays as a local checker of suspect text layers at load time | Tesseract alone; macOS Vision | On the hand-checked sample, 10 of 14 guides read reliably with Mistral OCR against 2 with tuned Tesseract; the checker stays local and needs no key | 2026-10-16 |
| D7 | Every chunk starts with a context header (`guide › chapter › section`), for the embedding and the full-text search | No context; a context written by an LLM | A simple form of contextual retrieval: free, and a chunk keeps the meaning its section gives it | 2026-10-15 |
| D10 | Hybrid retrieval: vectors plus French full text, fused by reciprocal rank (k = 60) | Vectors only | The doctrine is full of acronyms, distances and form numbers that exact matching finds | 2026-10-15 |
| D11 | Rerank the first chunks with `bge-reranker-v2-m3` (local) only if the bench shows a gain | Always; never | No reranker on the Mistral API; measure before adding a stage | 2026-10-15 |
| D13 | The citation check is code that can only block: every quote is found word for word in the passage it cites, and every figure of a sentence in one of its quotes | Trusting the model to quote | The central safeguard: a model writes fluent figures it did not read, and the guides must be quoted faithfully | 2026-10-15 |
| D14 | A web API (FastAPI) with a limit of questions per client; a read-only MCP server later | An interface only | The API serves the page, the MCP server and tests alike; the doctrine is public | 2026-10-15 |
| D16 | Complex situations are answered by fixed steps: sub-questions, a search for each, then an ordered procedure, each step cited | An agent that chooses its own tools | A fixed pipeline stays testable step by step | 2026-10-15 |
| D17 | A second model call reads each sentence with its quotes and says whether they support it; it can only block | String equality alone | Equality proves the quotes, not the sentence that rewords them | 2026-10-15 |
| D18 | Sections come from the printed table of contents (fallback: headings detected in the text); each chunk records its section path; pages are cut at section boundaries; contents pages are left out of the search | Chunks with no structure | Contents pages pollute the search; a procedure needs its whole section; PDF bookmarks are missing from almost every guide | 2026-10-15 |
| D20 | Multi-vector reranking (late interaction, one vector per token) with BGE-M3, local, on the first 50 chunks, without storing per-token vectors; compared with D11 on the bench | Per-token vectors stored in the database (gigabytes; pgvector cannot compute the max-sim score) | The doctrine's acronyms, form codes and figures are diluted in a single vector; BGE-M3 is multilingual, local and free | 2026-11-11 |
| D21 | Product first: ship the whole chain before tuning retrieval; production retrieves with `mistral-embed` vectors, without reranking | More retrieval trials first; reranking in production | Retrieval already reaches its recall@5 target; reranking costs tens of seconds per question on a CPU | 2026-11-11 |
| D22 | Kubernetes: k3s on a free ARM virtual machine, packaged with Docker and a Helm chart | A paid server; managed Kubernetes; Docker Compose only | Real Kubernetes in production at no cost | 2026-11-11 |
| D23 | Postgres with pgvector in the cluster, on a persistent volume, run by CloudNativePG, with a daily backup outside the machine (S3-compatible storage) | A managed database | One disk on one free machine: without an outside backup, losing the machine loses the query log | 2026-11-12 |
| D24 | The first version is the whole product, private: retrieval, generation and checks, behind an identity proxy that admits a list of people; rate limits and a daily token cap kept as defence in depth | A public version; retrieval only, without a model; a self-hosted open-weight model | Nothing built is dropped, and the API key serves invited people only | 2026-11-11 |
| D25 | Identity proxy: oauth2-proxy in the cluster, described in the Helm chart (GitHub or Google sign-in, an allowlist of e-mail addresses in the values); an sslip.io host name with Let's Encrypt HTTPS | A hosted access service and tunnel; a bought domain | Everything stays in the repository and reproducible, at no cost; the address changes if the machine's IP does | 2026-11-11 |

Measured outcomes of D10, D11 and D20 are in [EVALUATION.md](EVALUATION.md): on the current
bench the vectors alone beat the hybrid fusion, and reranking gains precision at a latency far
above the 8 s target, so neither is in the planned serving path yet.
