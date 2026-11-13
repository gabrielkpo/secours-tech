"""Answer bench (J4): the cases of a split asked end to end, scored by code, graded by a person.

What code can score is written to a record in `eval/answers/` (versioned, scores only, never
doctrine text): the status of each answer (answered, clarification, refused, blocked), whether
a case the doctrine answers got an answer and a case it does not got none, whether the answer
cites an expected page, why a draft was blocked (the kind of check, not its details), the
similarity of the nearest passage (to calibrate the refusal threshold), tokens and seconds.

What code cannot score (right, partial or wrong; a serious fault) is graded by the firefighter
who reviewed the cases, from a sheet written next to the cases, out of Git
(`data/eval/answers/`): each question with its expected answer, its serious-fault rule and the
system's answer as shown to a user.
"""

import argparse
import json
import re
import time
import tomllib
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from secours_tech.answer import (
    GENERATION_MODEL,
    PASSAGES,
    SEED,
    VERIFICATION_MODEL,
    Answer,
    Chat,
    answer,
    render,
    retrieve,
)
from secours_tech.bench import CASES, FINGERPRINT, SPLITS, Case, fingerprint, frozen_fingerprint

RECORDS = Path("eval/answers")
SHEETS = Path("data/eval/answers")
# the kind of check that blocked a draft, from its problem message; details (numbers, ids) are
# left out of the record, since they can come from the doctrine
PROBLEM_KINDS = (
    ("no citation", "no_citation"),
    ("unknown passage", "unknown_passage"),
    ("quote shorter", "short_quote"),
    ("word for word", "quote_not_found"),
    ("in no quote", "number_not_quoted"),
    ("link or markup", "markup"),
    ("not supported", "not_supported"),
    ("no verdict", "no_verdict"),
    ("without a sentence", "empty_answer"),
    ("clarification without", "bad_clarification"),
    ("off the schema", "off_schema"),
    ("finish_reason", "cut"),
    ("unreadable", "unreadable"),
)


def problem_kind(problem: str) -> str:
    return next((kind for text, kind in PROBLEM_KINDS if text in problem), "other")


@dataclass(frozen=True)
class Outcome:
    """What code can tell about one answer."""

    case: str
    category: str
    refusal_expected: bool
    status: str
    top_similarity: float | None
    cited_pages: int  # distinct pages cited
    cited_expected: int  # of which expected pages
    problems: tuple[str, ...]  # kinds, see PROBLEM_KINDS
    prompt_tokens: int
    completion_tokens: int
    seconds: float

    @property
    def responded(self) -> bool:
        """The user got sentences: an answer, or variants with a clarification question."""
        return self.status in ("answered", "clarification")


def outcome(case: Case, result: Answer, top: float | None, seconds: float) -> Outcome:
    cited = {(c.passage.document, c.passage.page) for s in result.sentences for c in s.citations}
    tokens = Counter[str]()
    for usage in result.usage.values():
        tokens.update(usage)
    return Outcome(
        case.id,
        case.category,
        case.refusal,
        result.status,
        None if top is None else round(top, 4),
        len(cited),
        len(cited & case.expected_pages),
        tuple(sorted({problem_kind(p) for p in result.problems})),
        tokens["prompt_tokens"],
        tokens["completion_tokens"],
        round(seconds, 2),
    )


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def scores(outcomes: Sequence[Outcome]) -> dict:
    """Shares over the cases the doctrine answers and over those it does not, plus the status
    and blocking counts. The grades of a person come on top (right, partial, wrong, serious)."""
    answerable = [o for o in outcomes if not o.refusal_expected]
    unanswerable = [o for o in outcomes if o.refusal_expected]
    responded = [o for o in answerable if o.responded]
    return {
        "n": len(outcomes),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "status": dict(sorted(Counter(o.status for o in outcomes).items())),
        # a case the doctrine answers, left without an answer (refused or blocked)
        "no_answer_rate": _rate(len(answerable) - len(responded), len(answerable)),
        # among the answers given, those citing at least one page the case expects
        "cites_expected_page_rate": _rate(
            sum(o.cited_expected > 0 for o in responded), len(responded)
        ),
        # a case the doctrine does not answer, answered anyway: a candidate serious fault
        "answered_unanswerable_rate": _rate(
            sum(o.responded for o in unanswerable), len(unanswerable)
        ),
        "blocked_by": dict(sorted(Counter(k for o in outcomes for k in o.problems).items())),
        "tokens_per_question": {
            "prompt": round(sum(o.prompt_tokens for o in outcomes) / max(len(outcomes), 1)),
            "completion": round(sum(o.completion_tokens for o in outcomes) / max(len(outcomes), 1)),
        },
        "seconds_per_question": round(sum(o.seconds for o in outcomes) / max(len(outcomes), 1), 2),
    }


def _case_text(case: Case) -> dict:
    """The fields of a case file the sheet shows (they quote the doctrine: never in a record)."""
    return tomllib.loads(case.path.read_text(encoding="utf-8"))


def sheet(cases: Sequence[Case], results: dict[str, Answer], outcomes: Sequence[Outcome]) -> str:
    """The grading sheet: per case, what was expected and what the system showed, then a line
    to fill in."""
    by_id = {o.case: o for o in outcomes}
    lines = [
        "# Answer grading sheet",
        "",
        "For each case, fill `grade` with right, partial or wrong (a refusal or a block on a case",
        "the doctrine answers is wrong) and `serious` with yes or no (see serious_fault_if).",
        "",
    ]
    for case in cases:
        data, result, seen = _case_text(case), results[case.id], by_id[case.id]
        lines += [
            f"## {case.id} ({case.category}{', refusal expected' if case.refusal else ''})",
            "",
            f"**Question**: {data['question']}",
            "",
            f"**Expected**: {data['expected_answer']}",
            "",
            f"**Serious fault if**: {data['serious_fault_if']}",
            "",
            f"**Status**: {result.status}"
            + (f" ({', '.join(seen.problems)})" if seen.problems else ""),
            "",
            "```text",
            render(result),
            "```",
            "",
            "grade = ",
            "serious = ",
            "",
        ]
    return "\n".join(lines)


def _code() -> dict:
    from secours_tech.runs import _code as code

    return code()


def run(conn, embedder, chat: Chat, cases: Sequence[Case], min_similarity: float | None = None):
    """Ask every case; returns the answers and their outcomes, in case order."""
    results, outcomes = {}, []
    for case in cases:
        started = time.perf_counter()
        passages = retrieve(conn, embedder, case.question)
        result = answer(case.question, passages, chat, min_similarity=min_similarity)
        seconds = time.perf_counter() - started
        top = passages[0].similarity if passages else None
        results[case.id] = result
        outcomes.append(outcome(case, result, top, seconds))
        print(f"{case.id} {result.status:13} {seconds:5.1f} s", flush=True)
    return results, outcomes


def record(conn, split: str, cases_sha256: str, outcomes: Sequence[Outcome], options: dict) -> dict:
    from secours_tech.embed import MISTRAL_MODEL
    from secours_tech.runs import index_fingerprint

    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": _code(),
        "setup": {
            "split": split,
            "embedding": MISTRAL_MODEL,
            "passages": PASSAGES,
            "generation": GENERATION_MODEL,
            "verification": VERIFICATION_MODEL,
            "seed": SEED,
            **options,
        },
        "index": index_fingerprint(conn),
        "cases": {"split": split, "n": len(outcomes), "sha256": cases_sha256},
        "scores": scores(outcomes),
        "outcomes": [asdict(o) for o in outcomes],
        "grades": None,  # filled from the grading sheet, by a person
    }


def write(folder: Path, data: dict, suffix: str = ".json") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[-:]", "", data["date"]).split("+")[0]
    for attempt in range(1, 100):
        name = f"{stamp}Z-{data['setup']['split']}-answers"
        path = folder / (f"{name}{suffix}" if attempt == 1 else f"{name}-{attempt}{suffix}")
        try:
            with path.open("x", encoding="utf-8") as out:  # never overwritten
                out.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            return path
        except FileExistsError:
            continue
    raise RuntimeError("too many records with the same name")


def scored_test(folder: Path = RECORDS) -> list[Path]:
    """Answer records of the test split: once one exists, the test split is not frozen again."""
    return sorted(folder.glob("*-test-answers*.json")) if folder.exists() else []


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Answer bench: ask every case of a split (three Mistral calls per question,"
        " about 3,000 tokens), write a record of scores and a grading sheet."
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--fingerprint", type=Path, default=FINGERPRINT)
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--final", action="store_true", help="required for the test split, once")
    parser.add_argument("--min-similarity", type=float, help="refuse below this similarity")
    parser.add_argument("--records", type=Path, default=RECORDS)
    parser.add_argument("--sheets", type=Path, default=SHEETS)
    args = parser.parse_args(argv)

    from secours_tech.bench import load_cases

    cases = load_cases(args.cases)
    frozen = frozen_fingerprint(args.fingerprint)
    if frozen and fingerprint(cases) != frozen:
        raise SystemExit("the test cases differ from the frozen fingerprint: they were edited")
    if args.split == "test" and not args.final:
        raise SystemExit("the test split is scored once, at the end: pass --final")
    if args.split == "test" and (done := scored_test(args.records)):
        raise SystemExit(f"the test split was already answered ({done[0].name})")
    chosen = [case for case in cases if case.split == args.split]

    from secours_tech.db import connect
    from secours_tech.embed import MistralEmbedder, missing_inputs
    from secours_tech.mistral import api_key, post

    key = api_key()
    with connect() as conn:
        if absent := len(missing_inputs(conn, MistralEmbedder.name)):
            raise SystemExit(f"{absent} chunk inputs have no {MistralEmbedder.name} vector")
        results, outcomes = run(
            conn,
            MistralEmbedder(key),
            lambda payload: post("chat/completions", payload, key),
            chosen,
            args.min_similarity,
        )
        data = record(
            conn,
            args.split,
            fingerprint(cases, args.split),
            outcomes,
            {"min_similarity": args.min_similarity},
        )
    path = write(args.records, data)
    args.sheets.mkdir(parents=True, exist_ok=True)
    grading = args.sheets / path.with_suffix(".md").name
    grading.write_text(sheet(chosen, results, outcomes), encoding="utf-8")
    print(f"record written to {path}\ngrading sheet written to {grading}")
    if data["code"]["dirty"]:
        print("warning: uncommitted changes; this record does not describe its commit")
    print(json.dumps(data["scores"], indent=2))


if __name__ == "__main__":
    main()
