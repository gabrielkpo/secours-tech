# Evaluation

## Protocol

- **Cases first.** Each case is a question asked as a firefighter would ask it, with its expected
  answer, the facts it must contain, what would be a serious fault, and the verbatim excerpts
  (document, page, text) that back it. Claude drafts each case from the guide pages, read
  straight from the PDFs and never through the system under test; the author, a firefighter,
  validates, corrects or rejects it. This happens before the system runs, and expected answers
  are never shown to a model.
- **The set (J2).** 31 cases: 11 simple questions, 5 complex situations, 5 tasks per function,
  5 questions outside the doctrine, 3 traps and 2 medical ones. 282 excerpts back them;
  `bench check` finds every one word for word on its PDF page and, for the 274 excerpts of
  answered cases, in the indexed text of that page.
- **Out of Git.** The cases quote the doctrine, whose reproduction rights are reserved, so they
  stay in `data/eval/cases/`. What is versioned: the fingerprint of the test split
  (`eval/frozen-test.sha256`), its change log (`eval/frozen-test-history.md`) and the scores of
  every run (`eval/runs/`).
- **A frozen test split.** 9 cases (30 %, drawn in every category with a fixed seed) are kept
  for one final run (J10). The bench refuses to run when a test case differs from the
  fingerprint, scores the test split only with `--final`, and once per setup and case set. One
  correction was made before any test score: 18 excerpts re-copied from the indexed text and one
  excerpt on a contents page removed, with questions and expected answers unchanged.
- **Retrieval scores.** A run ranks the pages of the first 50 chunks, each page counted once.
  For each answered case (refusal cases are scored with the answers, J4):
  - hit@k: an expected page is among the first k pages;
  - recall@k: the share of the case's expected pages (2 to 13) among the first k;
  - all@k: every expected page is among the first k;
  - MRR: the mean of 1 / rank of the first expected page.

  hit@k alone overstates retrieval on multi-page cases; recall@k is the measure to read for an
  answer that needs several pages.
- **Records.** `bench run` writes `eval/runs/<time>-<split>-<retriever>-<embedding>-<reranker>.json`:
  commit, models and revisions, the fingerprints of the indexed chunks and of the cases, scores
  per category, seconds per question and the ranks per case id. `bench report` turns records into
  the tables below.

## Retrieval (J3), development split

16 answered cases. Seconds per question on a laptop CPU (Apple M1, 8 GB), embedding and reranking
included; the reranking runs shared the machine with other work, so their times are an upper
bound.

| Split | Retriever | Embedding | Reranker | n | hit@5 | recall@5 | hit@20 | recall@20 | MRR | s / question | Record |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | fulltext | - | - | 16 | 0.62 | 0.26 | 0.81 | 0.50 | 0.43 | 0.1 | `20261112T135038Z-dev-fulltext-none-none.json` |
| dev | vector | `mistral-embed-2312` | - | 16 | 0.88 | 0.53 | 1.00 | 0.80 | 0.84 | 0.9 | `20261112T135053Z-dev-vector-mistral-none.json` |
| dev | hybrid | `mistral-embed-2312` | - | 16 | 0.88 | 0.54 | 0.94 | 0.70 | 0.78 | 0.7 | `20261112T135104Z-dev-hybrid-mistral-none.json` |
| dev | vector | `intfloat/multilingual-e5-base` | - | 16 | 0.94 | 0.61 | 0.94 | 0.74 | 0.94 | 0.2 | `20261112T135127Z-dev-vector-e5-none.json` |
| dev | hybrid | `intfloat/multilingual-e5-base` | - | 16 | 0.94 | 0.55 | 0.94 | 0.69 | 0.86 | 0.2 | `20261112T135141Z-dev-hybrid-e5-none.json` |
| dev | vector | `mistral-embed-2312` | `BAAI/bge-reranker-v2-m3` | 16 | 0.94 | 0.66 | 1.00 | 0.78 | 0.94 | 52.4 | `20261112T140550Z-dev-vector-mistral-bge.json` |
| dev | vector | `mistral-embed-2312` | `BAAI/bge-m3` | 16 | 0.94 | 0.64 | 1.00 | 0.81 | 0.90 | 53.7 | `20261112T142029Z-dev-vector-mistral-m3.json` |
| dev | hybrid | `mistral-embed-2312` | `BAAI/bge-reranker-v2-m3` | 16 | 0.94 | 0.66 | 1.00 | 0.75 | 0.91 | 49.6 | `20261112T143405Z-dev-hybrid-mistral-bge.json` |
| dev | hybrid | `mistral-embed-2312` | `BAAI/bge-m3` | 16 | 0.94 | 0.64 | 1.00 | 0.77 | 0.95 | 42.1 | `20261112T144533Z-dev-hybrid-mistral-m3.json` |
| dev | vector | `intfloat/multilingual-e5-base` | `BAAI/bge-reranker-v2-m3` | 16 | 0.94 | 0.67 | 0.94 | 0.76 | 0.91 | 36.3 | `20261112T145529Z-dev-vector-e5-bge.json` |
| dev | vector | `intfloat/multilingual-e5-base` | `BAAI/bge-m3` | 16 | 0.94 | 0.67 | 0.94 | 0.77 | 0.94 | 66.2 | `20261112T151322Z-dev-vector-e5-m3.json` |
| dev | hybrid | `intfloat/multilingual-e5-base` | `BAAI/bge-reranker-v2-m3` | 16 | 0.94 | 0.68 | 0.94 | 0.74 | 0.91 | 47.3 | `20261112T152620Z-dev-hybrid-e5-bge.json` |
| dev | hybrid | `intfloat/multilingual-e5-base` | `BAAI/bge-m3` | 16 | 0.94 | 0.67 | 0.94 | 0.74 | 0.94 | 56.0 | `20261112T154131Z-dev-hybrid-e5-m3.json` |

Scores per category (simple, complex, function, trap, medical) are in each record.

## Reading

- **Vectors beat full text by far.** Postgres French full text, any word of the question, finds
  an expected page in the first 5 for 10 cases of 16; both embeddings do for 14 or 15.
- **Hybrid fusion does not help here.** Fused with equal weight, the weaker full-text ranking
  pushes some right pages down: recall@20 and MRR drop for both embeddings (D10 stays in the
  design, not in the default).
- **The free local model is level with `mistral-embed`.** It ranks the first right page higher
  (MRR 0.94 against 0.84) and finds fewer of the expected pages within 20 (0.74 against 0.80):
  differences of about one case.
- **Reranking raises recall, not affordably.** Reordering the first 50 chunks with a local model
  lifts recall@5 from 0.53 to 0.66 after `mistral-embed`, at 36 to 66 s per question on a laptop
  CPU, far above the 8 s target: it needs fewer chunks, a GPU or a server before it can serve
  users (D11, D20).

## Limits

- 16 development cases: one case is 6 points. The tables rank options; they do not prove small
  gaps. The set grows to 60 to 100 cases before the final evaluation (J10).
- The cases are not published: others can check that the test split was not edited (its
  fingerprint) and how each score was produced (its record), not rerun it.
- Scores measure retrieval only: whether the pages an answer needs are found. Answers, citations
  and refusals are scored once generation exists (J4).
