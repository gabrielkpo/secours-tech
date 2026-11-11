"""Evaluation bench (J2): the cases, their checks, the frozen test split and retrieval scores.

Cases are written from the guide pages before the system runs (scoping §6.1, revised on
17/10/2026): Claude drafts each one from the PDFs, never through the system under test, and the
firefighter reviewing them validates, corrects or rejects it. They live outside Git
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
import tomllib
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CASES = Path("data/eval/cases")
FINGERPRINT = Path("eval/frozen-test.sha256")
RAW = Path("data/raw")
CATEGORIES = ("simple", "complex", "function", "out_of_scope", "trap", "medical")
SPLITS = ("dev", "test")
TEST_SHARE = 0.3  # scoping §6.1: about 30 % of the cases are kept for one final run
SEED = 11112026
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
    excerpts = tuple(
        Excerpt(str(e["document"]), int(e["page"]), str(e["text"]))
        for e in data.get("excerpts", [])
    )
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
    """Compare text, not typography: same quotes and spaces, lines joined, ligature spelled out."""
    text = unicodedata.normalize("NFC", text)
    for old, new in (("’", "'"), ("‘", "'"), (" ", " "), (" ", " "), ("œ", "oe")):
        text = text.replace(old, new)
    text = re.sub(r"-\s*\n\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=512)
def _pdf_page(path: Path, page: int, layout: bool) -> str:
    options = ["-layout"] if layout else []
    return subprocess.run(
        ["pdftotext", *options, "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


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
            texts = [ocr_text(excerpt.document, excerpt.page) or ""]
            if pdf is not None:
                texts += [_pdf_page(pdf, excerpt.page, layout) for layout in (False, True)]
            texts = [normalize(text) for text in texts]
            fragments = [normalize(part) for part in re.split(r"\[…\]|\[\.\.\.\]", excerpt.text)]
            fragments = [part for part in fragments if part.strip(" .")]
            if not any(all(part in text for part in fragments) for text in texts):
                missing.append((case.id, excerpt))
    return missing


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
    rng = random.Random(seed)
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


def fingerprint(cases: Sequence[Case]) -> str:
    """SHA-256 of the test cases, file name and bytes, in name order."""
    digest = hashlib.sha256()
    for case in sorted((c for c in cases if c.split == "test"), key=lambda c: c.id):
        digest.update(case.path.name.encode() + b"\0" + case.path.read_bytes() + b"\0")
    return digest.hexdigest()


def frozen_fingerprint(path: Path = FINGERPRINT) -> str | None:
    return path.read_text(encoding="utf-8").split()[0] if path.exists() else None


# --- retrieval scores -------------------------------------------------------------------------


def first_hit(case: Case, ranking: Sequence[tuple[str, int]]) -> int | None:
    """Rank (1-based) of the first expected page, pages counted once, in retrieval order."""
    seen: list[tuple[str, int]] = []
    for page in ranking:
        if page in seen:
            continue
        seen.append(page)
        if page in case.expected_pages:
            return len(seen)
    return None


def summarize(ranks: Sequence[int | None], ks: Sequence[int] = (5, 20)) -> dict[str, float]:
    """recall@k: share of cases with an expected page in the first k pages; MRR: mean of
    1 / rank of the first expected page (0 when none is found)."""
    n = len(ranks)
    scores = {f"recall@{k}": sum(r is not None and r <= k for r in ranks) / n for k in ks}
    scores["mrr"] = sum(1 / r for r in ranks if r is not None) / n
    return scores


def evaluate(
    cases: Sequence[Case], retriever: Retriever, ks: Sequence[int] = (5, 20)
) -> dict[str, dict[str, float]]:
    """Scores over the answered cases, all together and per category. Refusal cases have no
    page to find: they are scored when answers are generated (J4), not here."""
    ranks: dict[str, list[int | None]] = {}
    for case in cases:
        if case.refusal:
            continue
        rank = first_hit(case, retriever(case.question))
        ranks.setdefault("all", []).append(rank)
        ranks.setdefault(case.category, []).append(rank)
    return {group: {"n": len(r), **summarize(r, ks)} for group, r in ranks.items()}


def fulltext(conn, limit: int = 50) -> Retriever:
    """Baseline: Postgres French full text over the chunks of the current guides, any word of
    the question (OR), ranked by ts_rank_cd."""

    def retrieve(question: str) -> list[tuple[str, int]]:
        words = re.findall(r"\w+", question)
        if not words:
            return []
        rows = conn.execute(
            """
            SELECT d.filename, c.page
            FROM chunks c JOIN documents d ON d.id = c.document_id,
                 websearch_to_tsquery('fr', %s) q
            WHERE d.retired_at IS NULL AND c.tsv @@ q
            ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id
            LIMIT %s
            """,
            (" or ".join(words), limit),
        ).fetchall()
        return [(filename, page) for filename, page in rows]

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
    run = sub.add_parser("run", help="retrieval scores (recall@k, MRR) on a split")
    run.add_argument("--retriever", choices=("fulltext",), default="fulltext")
    run.add_argument("--split", choices=SPLITS, default="dev")
    run.add_argument("--k", type=int, nargs="+", default=[5, 20])
    run.add_argument(
        "--final", action="store_true", help="required to score the frozen test split, once"
    )
    args = parser.parse_args(argv)

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
        if not any(case.split == "test" for case in cases):
            raise SystemExit("no test case: run split first")
        args.fingerprint.parent.mkdir(parents=True, exist_ok=True)
        tests = sum(case.split == "test" for case in cases)
        args.fingerprint.write_text(f"{fingerprint(cases)}  {tests} test cases\n", encoding="utf-8")
        print(f"frozen: {tests} test cases, fingerprint written to {args.fingerprint}")
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
            if missing:
                raise SystemExit(1)
            return
        if args.split == "test" and not args.final:
            raise SystemExit("the test split is scored once, at the end: pass --final")
        chosen = [case for case in cases if case.split == args.split]
        scores = evaluate(chosen, fulltext(conn), args.k)
        for group in ["all", *CATEGORIES]:
            if group in scores:
                row = scores[group]
                metrics = "  ".join(f"{k} {v:.2f}" for k, v in row.items() if k != "n")
                print(f"{group:13} n={int(row['n']):2}  {metrics}")


if __name__ == "__main__":
    main()
