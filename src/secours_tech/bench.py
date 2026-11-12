"""Evaluation bench (J2): the cases, their checks, the frozen test split and retrieval scores.

Cases are written from the guide pages before the system runs (docs/EVALUATION.md): Claude
drafts each one from the PDFs, never through the system under test, and the firefighter
reviewing them validates, corrects or rejects it. They live outside Git
(`data/eval/cases/<id>.toml`) because their expected answers quote the doctrine verbatim. Only
the fingerprint of the frozen test split is versioned (`eval/frozen-test.sha256`), so anyone can
check the test was not edited after it was frozen without the doctrine being published.

A case file holds `id` (its file name), `category`, `split` (`dev` or `test`), `question`,
`expected_answer`, `facts`, `serious_fault_if`, `refusal` (true when the expected answer is a
refusal) and its `[[excerpts]]` (`document`, `page`, `text`). Every excerpt must appear word for
word on its page; the pages of the excerpts are the pages retrieval is expected to find.
"""

import argparse
import hashlib
import random
import re
import subprocess
import time
import tomllib
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from secours_tech.tools import run_tool

CASES = Path("data/eval/cases")
FINGERPRINT = Path("eval/frozen-test.sha256")
RUNS = Path("eval/runs")
HISTORY = "frozen-test-history.md"  # next to the fingerprint
RAW = Path("data/raw")
CATEGORIES = ("simple", "complex", "function", "out_of_scope", "trap", "medical")
SPLITS = ("dev", "test")
TEST_SHARE = 0.3  # about 30 % of the cases are kept for one final run (docs/EVALUATION.md)
SEED = 11112026
RRF_K = 60  # the usual constant of reciprocal rank fusion
CANDIDATES = 50  # chunks a first stage hands to scoring or to a reranker
REQUIRED = ("id", "category", "split", "question", "expected_answer", "serious_fault_if")

Retriever = Callable[[str], Sequence[tuple[str, int]]]  # question -> ranked (document, page)


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class Excerpt:
    document: str
    page: int
    text: str


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    split: str
    question: str
    refusal: bool
    excerpts: tuple[Excerpt, ...]
    path: Path

    @property
    def expected_pages(self) -> frozenset[tuple[str, int]]:
        return frozenset((e.document, e.page) for e in self.excerpts)


def load_case(path: Path) -> Case:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise CaseError(f"{path.name}: {error}") from None
    missing = [key for key in REQUIRED if not data.get(key)]
    if missing:
        raise CaseError(f"{path.name}: missing {', '.join(missing)}")
    if data["id"] != path.stem:
        raise CaseError(f"{path.name}: id {data['id']!r} differs from the file name")
    if data["category"] not in CATEGORIES:
        raise CaseError(f"{path.name}: unknown category {data['category']!r}")
    if data["split"] not in SPLITS:
        raise CaseError(f"{path.name}: unknown split {data['split']!r}")
    try:
        excerpts = tuple(
            Excerpt(str(e["document"]), int(e["page"]), str(e["text"]))
            for e in data.get("excerpts", [])
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CaseError(f"{path.name}: malformed excerpt ({error!r})") from None
    refusal = bool(data.get("refusal", False))
    if not refusal and not excerpts:
        raise CaseError(f"{path.name}: an answered case needs at least one excerpt")
    return Case(
        data["id"], data["category"], data["split"], data["question"], refusal, excerpts, path
    )


def load_cases(folder: Path = CASES) -> list[Case]:
    cases = [load_case(path) for path in sorted(folder.glob("*.toml"))]
    if not cases:
        raise CaseError(f"no case in {folder}")
    return cases


# --- excerpts are quoted word for word -----------------------------------------------------


def normalize(text: str) -> str:
    """Compare text, not typography: same quotes and spaces, list bullets dropped, lines joined,
    ligature spelled out, an accent over a dotless i (as some PDFs store "î") composed on a
    plain i."""
    text = unicodedata.normalize("NFC", text.replace("ı", "i"))
    spaces = (("\u00a0", " "), ("\u202f", " "), ("•", " "))  # no-break, thin no-break, bullet
    for old, new in (("’", "'"), ("‘", "'"), ("œ", "oe"), *spaces):
        text = text.replace(old, new)
    text = re.sub(r"-\s*\n\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=512)
def _pdf_page(path: Path, page: int, mode: str) -> str:
    """Text of one page in a reading order of pdftotext ("plain", "raw" or "layout"); empty
    when pdftotext cannot read it (a page past the end, say)."""
    options = [] if mode == "plain" else [f"-{mode}"]
    page_range = ("-f", str(page), "-l", str(page))
    try:
        return run_tool(
            "pdftotext", *options, "-enc", "UTF-8", *page_range, str(path.resolve()), "-"
        )
    except subprocess.SubprocessError:
        return ""


def _readings(excerpt: Excerpt, pdf: Path | None, ocr_text: Callable[[str, int], str | None]):
    """The page's text, cheapest reading first: OCR text, then plain, raw and layout orders (raw
    order keeps whole the words whose accents are stored apart, as the loader reads them)."""
    yield normalize(ocr_text(excerpt.document, excerpt.page) or "")
    if pdf is not None:
        for mode in ("plain", "raw", "layout"):
            yield normalize(_pdf_page(pdf, excerpt.page, mode))


def missing_excerpts(
    cases: Sequence[Case],
    raw: Path = RAW,
    ocr_text: Callable[[str, int], str | None] = lambda document, page: None,
) -> list[tuple[str, Excerpt]]:
    """Excerpts not found word for word on their page: in the text layer (plain or layout
    reading order) or in the OCR text. Fragments cut with « […] » are looked for one by one."""
    pdfs = {path.name: path for path in raw.rglob("*.pdf")}
    missing = []
    for case in cases:
        for excerpt in case.excerpts:
            pdf = pdfs.get(excerpt.document)
            fragments = _fragments(excerpt)
            if not fragments or not any(
                all(part in text for part in fragments)
                for text in _readings(excerpt, pdf, ocr_text)
            ):
                missing.append((case.id, excerpt))
    return missing


def _fragments(excerpt: Excerpt) -> list[str]:
    """The excerpt's parts, cut at « […] », normalised."""
    parts = [normalize(part) for part in re.split(r"\[…\]|\[\.\.\.\]", excerpt.text)]
    return [part for part in parts if part.strip(" .")]


def merge_chunks(texts: Sequence[str]) -> str:
    """A page's chunks joined back in order, the sentence repeated at the start of a chunk (the
    overlap of D7) kept once, so an excerpt that runs across two chunks is found."""
    merged = ""
    for text in texts:
        overlap = next(
            (
                k
                for k in range(min(len(merged), len(text)), 0, -1)
                if text[k - 1] in ".!?;:"  # whole sentences only, as the chunker repeats them
                and (k == len(text) or text[k] == " ")
                and merged.endswith(text[:k])
                and (k == len(merged) or merged[-k - 1] == " ")
            ),
            0,
        )
        rest = text[overlap:].lstrip()
        merged = f"{merged} {rest}".strip() if rest else merged
    return merged


def indexed_page_text(conn) -> Callable[[str, int], str | None]:
    """The indexed text of a page of a current official guide, None when no chunk holds it."""

    def read(document: str, page: int) -> str | None:
        rows = conn.execute(
            "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.filename = %s AND c.page = %s AND d.retired_at IS NULL"
            " AND d.source = 'web' ORDER BY c.position",
            (document, page),
        ).fetchall()
        return merge_chunks([text for (text,) in rows]) if rows else None

    return read


def unindexed_excerpts(
    cases: Sequence[Case], page_text: Callable[[str, int], str | None]
) -> list[tuple[str, Excerpt, str]]:
    """Excerpts of answered cases that retrieval and the citation check cannot match: their page
    gives no chunk ("page not indexed"), or the indexed text of the page does not hold them word
    for word ("not verbatim"). Refusal cases may quote guides the index leaves out."""
    found = []
    for case in cases:
        if case.refusal:
            continue
        for excerpt in case.excerpts:
            text = page_text(excerpt.document, excerpt.page)
            if text is None:
                found.append((case.id, excerpt, "page not indexed"))
            elif not all(part in normalize(text) for part in _fragments(excerpt)):
                found.append((case.id, excerpt, "not verbatim"))
    return found


# --- the frozen test split --------------------------------------------------------------------


def assign_splits(
    cases: Sequence[Case], share: float = TEST_SHARE, seed: int = SEED
) -> dict[str, str]:
    """Draw about `share` of the cases for the test split, in every category: each category gets
    its rounded-down quota, and the remaining places go to the largest remainders."""
    by_category: dict[str, list[str]] = {}
    for case in cases:
        by_category.setdefault(case.category, []).append(case.id)
    quotas = {category: len(ids) * share for category, ids in by_category.items()}
    taken = {category: int(quota) for category, quota in quotas.items()}
    left = round(len(cases) * share) - sum(taken.values())
    for category in sorted(quotas, key=lambda c: (taken[c] - quotas[c], c))[:left]:
        taken[category] += 1
    rng = random.Random(seed)  # noqa: S311 - a reproducible draw, not a secret
    test = set()
    for category in sorted(by_category):
        test.update(rng.sample(sorted(by_category[category]), taken[category]))
    return {case.id: "test" if case.id in test else "dev" for case in cases}


def write_split(case: Case, split: str) -> None:
    text = case.path.read_text(encoding="utf-8")
    new, count = re.subn(r'(?m)^split = "\w+"$', f'split = "{split}"', text, count=1)
    if count != 1:
        raise CaseError(f"{case.path.name}: no split line to update")
    case.path.write_text(new, encoding="utf-8")


def fingerprint(cases: Sequence[Case], split: str = "test") -> str:
    """SHA-256 of the cases of a split, file name and bytes, in name order."""
    digest = hashlib.sha256()
    for case in sorted((c for c in cases if c.split == split), key=lambda c: c.id):
        digest.update(case.path.name.encode() + b"\0" + case.path.read_bytes() + b"\0")
    return digest.hexdigest()


def frozen_fingerprint(path: Path = FINGERPRINT) -> str | None:
    return path.read_text(encoding="utf-8").split()[0] if path.exists() else None


# --- retrieval scores -------------------------------------------------------------------------


def page_ranks(case: Case, ranking: Sequence[tuple[str, int]]) -> list[int | None]:
    """Rank (1-based) of each expected page of the case, pages counted once in retrieval order;
    None for an expected page that was not retrieved. In the order of the sorted expected pages."""
    order: dict[tuple[str, int], int] = {}
    for page in ranking:
        order.setdefault(page, len(order) + 1)
    return [order.get(page) for page in sorted(case.expected_pages)]


def summarize(
    cases: Sequence[Sequence[int | None]], ks: Sequence[int] = (5, 20)
) -> dict[str, float]:
    """Mean over the cases, each given as the ranks of its expected pages:

    - hit@k: an expected page is among the first k pages (one of a case's 2 to 13 pages is
      enough, so it says nothing about the other pages an answer may need);
    - recall@k: share of the case's expected pages among the first k pages;
    - all@k: every expected page is among the first k pages;
    - mrr: 1 / rank of the first expected page found (0 when none is).
    """
    n = len(cases)
    found = [[r for r in ranks if r is not None] for ranks in cases]
    scores: dict[str, float] = {}
    for k in ks:
        scores[f"hit@{k}"] = sum(any(r <= k for r in f) for f in found) / n
        scores[f"recall@{k}"] = (
            sum(sum(r <= k for r in f) / len(ranks) for f, ranks in zip(found, cases, strict=True))
            / n
        )
        scores[f"all@{k}"] = (
            sum(
                len(f) == len(ranks) and all(r <= k for r in f)
                for f, ranks in zip(found, cases, strict=True)
            )
            / n
        )
    scores["mrr"] = sum(1 / min(f) for f in found if f) / n
    return scores


def rank_cases(cases: Sequence[Case], retriever: Retriever) -> dict[str, list[int | None]]:
    """The ranks of each answered case's expected pages, by case id. Refusal cases have no page
    to find: they are scored when answers are generated (J4), not here."""
    return {
        case.id: page_ranks(case, retriever(case.question)) for case in cases if not case.refusal
    }


def score(
    cases: Sequence[Case], ranks: dict[str, list[int | None]], ks: Sequence[int] = (5, 20)
) -> dict[str, dict[str, float]]:
    """Scores over the ranked cases, all together and per category."""
    groups: dict[str, list[list[int | None]]] = {}
    for case in cases:
        if case.id in ranks:
            groups.setdefault("all", []).append(ranks[case.id])
            groups.setdefault(case.category, []).append(ranks[case.id])
    return {group: {"n": len(r), **summarize(r, ks)} for group, r in groups.items()}


def evaluate(
    cases: Sequence[Case], retriever: Retriever, ks: Sequence[int] = (5, 20)
) -> dict[str, dict[str, float]]:
    """Scores over the answered cases, all together and per category."""
    return score(cases, rank_cases(cases, retriever), ks)


def _fulltext_chunks(conn, question: str, limit: int) -> list[tuple[int, str, int]]:
    """(chunk id, document, page) by French full text, any word of the question (OR), in the
    current official guides only (a file outside the manifest is never cited as doctrine)."""
    words = re.findall(r"\w+", question)
    if not words:
        return []
    return conn.execute(
        """
        SELECT c.id, d.filename, c.page
        FROM chunks c JOIN documents d ON d.id = c.document_id,
             websearch_to_tsquery('fr', %s) q
        WHERE d.retired_at IS NULL AND d.source = 'web' AND c.tsv @@ q
        ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id
        LIMIT %s
        """,
        (" or ".join(words), limit),
    ).fetchall()


def _vector_chunks(conn, embedder, question: str, limit: int) -> list[tuple[int, str, int]]:
    """(chunk id, document, page) by cosine distance to the question's embedding."""
    from secours_tech.embed import as_vector

    return conn.execute(
        """
        SELECT c.id, d.filename, c.page
        FROM chunks c JOIN documents d ON d.id = c.document_id
             JOIN embeddings e ON e.model = %s AND e.input_sha256 = c.input_sha256
        WHERE d.retired_at IS NULL AND d.source = 'web'
        ORDER BY e.embedding <=> %s::vector, c.id
        LIMIT %s
        """,
        (embedder.name, as_vector(embedder.query(question)), limit),
    ).fetchall()


def rrf(*rankings: Sequence[tuple[int, str, int]], k: int = RRF_K) -> list[tuple[int, str, int]]:
    """Reciprocal rank fusion: each chunk scores the sum of 1 / (k + rank) over the rankings
    it appears in (D10). Ties keep the order of the first ranking."""
    scores: dict[int, float] = {}
    rows: dict[int, tuple[int, str, int]] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking, start=1):
            scores[row[0]] = scores.get(row[0], 0.0) + 1 / (k + rank)
            rows.setdefault(row[0], row)
    return sorted(rows.values(), key=lambda row: -scores[row[0]])


def _hybrid_chunks(conn, embedder, question: str, limit: int) -> list[tuple[int, str, int]]:
    """Full text and vectors fused by reciprocal rank (D10)."""
    fused = rrf(
        _fulltext_chunks(conn, question, limit),
        _vector_chunks(conn, embedder, question, limit),
    )
    return fused[:limit]


def _pages(candidates: Callable[[str], Sequence[tuple[int, str, int]]]) -> Retriever:
    return lambda question: [(d, p) for _, d, p in candidates(question)]


def fulltext(conn, limit: int = CANDIDATES) -> Retriever:
    """Baseline: Postgres French full text over the chunks of the current guides, any word of
    the question (OR), ranked by ts_rank_cd."""
    return _pages(lambda question: _fulltext_chunks(conn, question, limit))


def vector(conn, embedder, limit: int = CANDIDATES) -> Retriever:
    return _pages(lambda question: _vector_chunks(conn, embedder, question, limit))


def hybrid(conn, embedder, limit: int = CANDIDATES) -> Retriever:
    return _pages(lambda question: _hybrid_chunks(conn, embedder, question, limit))


def reranked(
    conn, candidates: Callable[[str], Sequence[tuple[int, str, int]]], reranker
) -> Retriever:
    """The first chunks of a retriever, reordered by a reranker that reads each chunk's input
    (context header and text, D7) with the question (D11, D20)."""
    from secours_tech.rerank import rerank

    def retrieve(question: str) -> list[tuple[str, int]]:
        rows = list(candidates(question))
        texts = dict(
            conn.execute(
                "SELECT id, chunk_input(context, text) FROM chunks WHERE id = ANY(%s)",
                ([row[0] for row in rows],),
            ).fetchall()
        )
        ordered = rerank(reranker, question, rows, [texts[row[0]] for row in rows])
        return [(d, p) for _, d, p in ordered]

    return retrieve


def _ocr_text(conn) -> Callable[[str, int], str | None]:
    def read(document: str, page: int) -> str | None:
        row = conn.execute(
            "SELECT p.ocr_text FROM pages p JOIN documents d ON d.id = p.document_id"
            " WHERE d.filename = %s AND p.page = %s AND d.retired_at IS NULL",
            (document, page),
        ).fetchone()
        return row[0] if row else None

    return read


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluation bench: check, split, freeze, run.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--fingerprint", type=Path, default=FINGERPRINT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="every excerpt is found word for word on its page")
    sub.add_parser("split", help="draw the test split (refused once the test is frozen)")
    sub.add_parser("freeze", help="write the fingerprint of the test split")
    refreeze = sub.add_parser(
        "refreeze", help="freeze corrected test cases again, while the test split is unscored"
    )
    refreeze.add_argument("--reason", required=True, help="what changed and why, for the log")
    refreeze.add_argument("--runs", type=Path, default=RUNS)
    run = sub.add_parser("run", help="retrieval scores (hit@k, recall@k, all@k, MRR) on a split")
    run.add_argument("--retriever", choices=("fulltext", "vector", "hybrid"), default="fulltext")
    run.add_argument("--model", choices=("mistral", "e5"), default="mistral")
    run.add_argument(
        "--rerank", choices=("none", "bge", "m3"), default="none", help="rerank the first 50"
    )
    run.add_argument("--split", choices=SPLITS, default="dev")
    run.add_argument("--k", type=int, nargs="+", default=[5, 20])
    run.add_argument(
        "--final", action="store_true", help="required to score the frozen test split, once"
    )
    run.add_argument(
        "--runs", type=Path, default=RUNS, help="folder of the run records (scores only)"
    )
    run.add_argument(
        "--force", action="store_true", help="score the test split again with the same setup"
    )
    report = sub.add_parser("report", help="a Markdown table of run records")
    report.add_argument("records", type=Path, nargs="+")
    report.add_argument(
        "--compact", action="store_true", help="leave out the split and record columns"
    )
    args = parser.parse_args(argv)

    if args.command == "report":
        from secours_tech.runs import table

        print(table(args.records, full=not args.compact))
        return

    cases = load_cases(args.cases)
    frozen = frozen_fingerprint(args.fingerprint)
    if args.command == "split":
        if frozen:
            raise SystemExit("the test split is frozen: it is not drawn again")
        splits = assign_splits(cases)
        for case in cases:
            write_split(case, splits[case.id])
        cases = load_cases(args.cases)
        print(f"test split: {sorted(c.id for c in cases if c.split == 'test')}")
        return
    if args.command == "freeze":
        if frozen:
            raise SystemExit(f"already frozen ({args.fingerprint}): a new freeze would hide edits")
        if not any(case.split == "test" for case in cases):
            raise SystemExit("no test case: run split first")
        args.fingerprint.parent.mkdir(parents=True, exist_ok=True)
        tests = sum(case.split == "test" for case in cases)
        args.fingerprint.write_text(f"{fingerprint(cases)}  {tests} test cases\n", encoding="utf-8")
        print(f"frozen: {tests} test cases, fingerprint written to {args.fingerprint}")
        return
    if args.command == "refreeze":
        if not frozen:
            raise SystemExit("nothing is frozen yet: run freeze")
        scored = sorted(args.runs.glob("*-test-*.json")) if args.runs.exists() else []
        if scored:
            # once the test split has been scored, a corrected set would be tuned on its results
            raise SystemExit(f"the test split was already scored ({scored[0].name}): no refreeze")
        new = fingerprint(cases)
        if new == frozen:
            raise SystemExit("the test cases did not change: nothing to refreeze")
        tests = sum(case.split == "test" for case in cases)
        args.fingerprint.write_text(f"{new}  {tests} test cases\n", encoding="utf-8")
        history = args.fingerprint.with_name(HISTORY)
        if not history.exists():
            history.write_text(
                "# Frozen test split: changes before it was scored\n\n", encoding="utf-8"
            )
        with history.open("a", encoding="utf-8") as log:
            log.write(f"- {date.today().isoformat()}: {frozen[:12]} -> {new[:12]}, {tests} test"
                      f" cases. {args.reason}\n")  # fmt: skip
        print(f"refrozen: {tests} test cases, change logged in {history}")
        return
    if frozen and fingerprint(cases) != frozen:
        raise SystemExit("the test cases differ from the frozen fingerprint: they were edited")

    from secours_tech.db import connect

    with connect() as conn:
        if args.command == "check":
            missing = missing_excerpts(cases, ocr_text=_ocr_text(conn))
            for case_id, excerpt in missing:
                print(
                    f"NOT FOUND {case_id} {excerpt.document} p.{excerpt.page}: {excerpt.text[:80]}"
                )
            total = sum(len(case.excerpts) for case in cases)
            print(f"{total - len(missing)}/{total} excerpts found word for word on their page")
            unindexed = unindexed_excerpts(cases, indexed_page_text(conn))
            for case_id, excerpt, reason in unindexed:
                print(f"{reason.upper()} {case_id} {excerpt.document} p.{excerpt.page}")
            answered = sum(len(case.excerpts) for case in cases if not case.refusal)
            print(
                f"{answered - len(unindexed)}/{answered} excerpts of answered cases found word for"
                " word in the indexed text of their page"
            )
            if missing or unindexed:
                raise SystemExit(1)
            return
        if args.split == "test" and not args.final:
            raise SystemExit("the test split is scored once, at the end: pass --final")
        from secours_tech.runs import Setup, earlier_final, record, write

        setup = Setup(args.split, args.retriever, None, args.rerank)
        if args.retriever != "fulltext":
            setup = Setup(args.split, args.retriever, args.model, args.rerank)
        cases_sha256 = fingerprint(cases, args.split)
        if (
            args.split == "test"
            and not args.force
            and (done := earlier_final(args.runs, setup, cases_sha256))
        ):
            raise SystemExit(f"the test split was already scored with this setup ({done})")
        chosen = [case for case in cases if case.split == args.split]
        embedder = None
        if args.retriever != "fulltext":
            from secours_tech.embed import EMBEDDERS, missing_inputs

            model = EMBEDDERS[args.model]
            # a chunk without a vector is invisible to the vector search: scores would be biased;
            # checked before the model is loaded
            if absent := len(missing_inputs(conn, model.name)):
                raise SystemExit(
                    f"{absent} chunk inputs have no {model.name} vector: "
                    f"run `python -m secours_tech.embed --model {args.model}` first"
                )
            embedder = model()
        candidates = {
            "fulltext": lambda question: _fulltext_chunks(conn, question, CANDIDATES),
            "vector": lambda question: _vector_chunks(conn, embedder, question, CANDIDATES),
            "hybrid": lambda question: _hybrid_chunks(conn, embedder, question, CANDIDATES),
        }[args.retriever]
        if args.rerank == "none":
            retriever = _pages(candidates)
        else:
            from secours_tech.rerank import RERANKERS

            retriever = reranked(conn, candidates, RERANKERS[args.rerank]())
        started = time.perf_counter()
        ranks = rank_cases(chosen, retriever)
        seconds = (time.perf_counter() - started) / max(len(ranks), 1)
        scores = score(chosen, ranks, args.k)
        print(f"{seconds:.2f} s per question")
        data = record(conn, setup, cases_sha256, ranks, scores, seconds)
        path = write(args.runs, data)
        print(f"record written to {path}")
        if data["code"]["dirty"]:
            print("warning: uncommitted changes; this record does not describe its commit")
        for group in ["all", *CATEGORIES]:
            if group in scores:
                row = scores[group]
                metrics = "  ".join(f"{k} {v:.2f}" for k, v in row.items() if k != "n")
                print(f"{group:13} n={int(row['n']):2}  {metrics}")


if __name__ == "__main__":
    main()
