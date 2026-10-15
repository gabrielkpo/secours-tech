"""Reliability check of the OCR, guide by guide, against pages transcribed by hand.

Rule (scoping, 15/10/2026): OCR text is used only if it is reliable. On a sample of each
guide's OCR pages, every number must be read exactly (no number missing, none added) and at
least 99 % of the characters must be right (character error rate <= 1 %). A guide that fails
stays uncovered.

References live outside Git (`data/ocr-reference/<pdf stem>/p<page>.txt`): they are doctrine
text. Only the scores go into the repository.
"""

import argparse
import random
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from secours_tech.db import connect

REFERENCES = Path("data/ocr-reference")
MAX_CER = 0.01
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "«": '"', "»": '"', " ": " ", " ": " "})


def normalize(text: str) -> str:
    """Compare text, not layout: same quotes, single spaces, no line breaks."""
    return " ".join(text.translate(_QUOTES).split())


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def numbers(text: str) -> Counter[str]:
    return Counter(_NUMBER.findall(text))


@dataclass
class PageScore:
    page: int
    errors: int
    length: int
    missing_numbers: list[str]
    extra_numbers: list[str]


def score_page(page: int, reference: str, ocr: str) -> PageScore:
    ref, hyp = normalize(reference), normalize(ocr)
    expected, found = numbers(ref), numbers(hyp)
    return PageScore(
        page,
        edit_distance(ref, hyp),
        len(ref),
        sorted((expected - found).elements()),
        sorted((found - expected).elements()),
    )


@dataclass
class GuideScore:
    guide: str
    pages: list[PageScore] = field(default_factory=list)

    @property
    def cer(self) -> float:
        length = sum(p.length for p in self.pages)
        return sum(p.errors for p in self.pages) / length if length else 1.0

    @property
    def numbers_exact(self) -> bool:
        return all(not p.missing_numbers and not p.extra_numbers for p in self.pages)

    @property
    def passed(self) -> bool:
        return bool(self.pages) and self.numbers_exact and self.cer <= MAX_CER


def sample(conn, per_guide: int, seed: int) -> dict[str, list[int]]:
    """Pages to transcribe: a seeded random draw among each guide's OCR pages with text masks."""
    rows = conn.execute(
        "SELECT d.filename, p.page FROM pages p JOIN documents d ON d.id = p.document_id"
        " WHERE p.route = 'ocr' AND p.text_masks > 0 AND d.retired_at IS NULL"
        " ORDER BY d.filename, p.page"
    ).fetchall()
    by_guide: dict[str, list[int]] = {}
    for filename, page in rows:
        by_guide.setdefault(filename, []).append(page)
    rng = random.Random(seed)
    return {
        name: sorted(rng.sample(pages, min(per_guide, len(pages))))
        for name, pages in by_guide.items()
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Check OCR reliability against hand transcriptions."
    )
    parser.add_argument("--references", type=Path, default=REFERENCES)
    sub = parser.add_subparsers(dest="command", required=True)
    draw = sub.add_parser("sample", help="render the sample pages to transcribe")
    draw.add_argument("corpus", type=Path, help="folder with the loaded PDFs")
    draw.add_argument("--per-guide", type=int, default=2)
    draw.add_argument("--seed", type=int, default=15102026)
    sub.add_parser("score", help="score the OCR text against the transcriptions")
    args = parser.parse_args(argv)

    with connect() as conn:
        if args.command == "sample":
            pdfs = {p.name: p for p in args.corpus.rglob("*.pdf")}
            for filename, pages in sample(conn, args.per_guide, args.seed).items():
                folder = args.references / Path(filename).stem
                folder.mkdir(parents=True, exist_ok=True)
                for page in pages:
                    image = folder / f"p{page}"
                    subprocess.run(
                        ["pdftoppm", "-f", str(page), "-l", str(page), "-r", "150", "-png",
                         "-singlefile", str(pdfs[filename].resolve()), str(image)],
                        check=True,
                    )  # fmt: skip
                    print(f"to transcribe: {image}.png -> {image}.txt")
            return

        guides: dict[str, GuideScore] = {}
        for reference in sorted(args.references.glob("*/p*.txt")):
            page = int(reference.stem[1:])
            row = conn.execute(
                "SELECT d.filename, p.ocr_text FROM pages p JOIN documents d"
                " ON d.id = p.document_id WHERE d.filename = %s AND p.page = %s"
                " AND d.retired_at IS NULL",
                (f"{reference.parent.name}.pdf", page),
            ).fetchone()
            if row is None or row[1] is None:
                print(f"no OCR text yet for {reference}")
                continue
            guide = guides.setdefault(row[0], GuideScore(row[0]))
            guide.pages.append(score_page(page, reference.read_text(encoding="utf-8"), row[1]))

    for guide in guides.values():
        verdict = "PASS" if guide.passed else "FAIL"
        print(f"{verdict}  CER {guide.cer:6.2%}  numbers {'exact' if guide.numbers_exact else 'WRONG'}"
              f"  {len(guide.pages)} page(s)  {guide.guide}")  # fmt: skip
        for p in guide.pages:
            if p.missing_numbers or p.extra_numbers:
                print(f"      p{p.page}: missing {p.missing_numbers} extra {p.extra_numbers}")


if __name__ == "__main__":
    main()
