"""Reliability check of the OCR, guide by guide, against pages transcribed by hand.

Rule (scoping, 15/10/2026): OCR text is used only if it is reliable. On a sample of each
guide's OCR pages, every number must be read exactly (no number missing, none added) and at
least 99 % of the characters must be right (character error rate <= 1 %). A guide that fails
stays uncovered.

Only pages whose text is drawn as images (text masks, see `extract.MIN_TEXT_MASKS`) are sampled,
and only those pages can be indexed once their guide passes: image pages (photos, diagrams) are
OCRed for measurement but stay uncovered, since their labels are neither sampled nor reliable.

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
from secours_tech.extract import MIN_LETTERS, MIN_TEXT_MASKS
from secours_tech.ingest import Chunk, chunk_text, strip_repeated_lines
from secours_tech.load import copy_chunks, renumber_chunks
from secours_tech.structure import section_at

REFERENCES = Path("data/ocr-reference")
SAMPLES = (REFERENCES, REFERENCES / "validation")  # tuning sample, then held-out validation
RECORD = Path("corpus/ocr-validation.toml")
# Pages whose OCR text can be indexed: text drawn as images (text masks) or a text layer that was
# rejected as garbled. Image pages (photos, diagrams) are read for measurement only.
INDEXABLE = f"p.route = 'ocr' AND (p.text_masks >= {MIN_TEXT_MASKS} OR p.letters >= {MIN_LETTERS})"
MAX_CER = 0.01
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_BULLET = re.compile(r"^\s*[•●▪■◦○·*°✓✔eo\-–—]\s+")
_QUOTES = str.maketrans(
    {"’": "'", "‘": "'", "«": '"', "»": '"', "\u00a0": " ", "\u202f": " ", "–": "-", "—": "-"}
)
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n(?=\w)")
# French typography puts a (thin) space before ; : ! ? and inside quotes; engines keep or drop it
_TYPO_SPACE = re.compile(r'\s+(?=[;:!?])|\s*"\s*')
_LEADER = re.compile(r"\.{4,}")  # table-of-contents dot leaders, whatever their length


def normalize(text: str) -> str:
    """Compare text, not layout.

    Same quotes and dashes, no list bullets, no space before ; : ! ? or around quotes, "…" as
    "...", no table-of-contents dot leaders, words cut at a line end joined back, single spaces.
    Transcriptions leave bullets and leaders out; the OCR reads a bullet as a lone "e", "o", "*"
    or dash at the start of the line, which would count as errors on every list item.
    """
    text = _LINE_BREAK_HYPHEN.sub(r"\1-", text.translate(_QUOTES))
    text = " ".join(_BULLET.sub("", line) for line in text.splitlines())
    text = _TYPO_SPACE.sub(lambda m: '"' if '"' in m.group() else "", text.replace("…", "..."))
    return " ".join(_LEADER.sub(" ", text).split())


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


_TRANSCRIPTION = re.compile(r"p(\d+)\.txt")


def transcriptions(folder: Path) -> list[tuple[Path, str, int]]:
    """Hand transcriptions in a sample folder: (file, pdf stem, page), from `<stem>/p<page>.txt`."""
    found = []
    for path in sorted(folder.glob("*/p*.txt")):
        match = _TRANSCRIPTION.fullmatch(path.name)
        if match:
            found.append((path, path.parent.name, int(match.group(1))))
    return found


def sample(
    conn, per_guide: int, seed: int, exclude: frozenset[tuple[str, int]] = frozenset()
) -> dict[str, list[int]]:
    """Pages to transcribe: a seeded random draw among each guide's indexable OCR pages.

    Pages in `exclude` (pdf stem, page), already transcribed for an earlier sample, are not drawn
    again: a validation sample stays apart from the one the engine was tuned on.
    """
    rows = conn.execute(
        "SELECT d.filename, p.page FROM pages p JOIN documents d ON d.id = p.document_id"
        f" WHERE {INDEXABLE} AND d.retired_at IS NULL ORDER BY d.filename, p.page"
    ).fetchall()
    by_guide: dict[str, list[int]] = {}
    for filename, page in rows:
        if (Path(filename).stem, page) not in exclude:
            by_guide.setdefault(filename, []).append(page)
    rng = random.Random(seed)
    return {
        name: sorted(rng.sample(pages, min(per_guide, len(pages))))
        for name, pages in by_guide.items()
    }


def ocr_pages(conn, filename: str) -> dict[int, str]:
    """OCR text of a current guide's pages, without its running headers and footers.

    The repeated lines are found across all the guide's OCR pages with the function chunking
    uses on a guide's text pages; transcriptions leave them out.
    """
    rows = conn.execute(
        "SELECT p.page, p.ocr_text FROM pages p JOIN documents d ON d.id = p.document_id"
        " WHERE d.filename = %s AND d.retired_at IS NULL AND p.ocr_text IS NOT NULL"
        " ORDER BY p.page",
        (filename,),
    ).fetchall()
    cleaned = strip_repeated_lines([text for _, text in rows])
    return {page: text for (page, _), text in zip(rows, cleaned, strict=True)}


def score_guides(
    conn, folders: list[Path], with_image_labels: bool = False
) -> dict[str, GuideScore]:
    """Score each guide's stored OCR text against every transcribed page of the given samples."""
    guides: dict[str, GuideScore] = {}
    ocr_by_guide: dict[str, dict[int, str]] = {}
    for folder in folders:
        for reference, stem, page in transcriptions(folder):
            filename = f"{stem}.pdf"
            if filename not in ocr_by_guide:
                ocr_by_guide[filename] = ocr_pages(conn, filename)
            ocr = ocr_by_guide[filename].get(page)
            if ocr is None:
                print(f"no OCR text yet for {reference}")
                continue
            text = reference.read_text(encoding="utf-8")
            labels = reference.with_name(f"img-{reference.name}")
            if with_image_labels and labels.exists():
                text += "\n" + labels.read_text(encoding="utf-8")
            guides.setdefault(filename, GuideScore(filename)).pages.append(
                score_page(page, text, ocr)
            )
    return guides


def index_guides(conn, guides: dict[str, GuideScore]) -> dict[str, int]:
    """Make the OCR chunks of every current guide match the reliability check.

    A guide that passes gets the OCR text of its indexable pages as chunks (`extraction = 'ocr'`);
    any other guide, failed or never sampled, keeps none. A guide whose pages were not all read
    with one engine and one set of settings is not indexed: its sample would not vouch for the
    rest. Returns the chunks written per guide.
    """
    written = {}
    documents = conn.execute(
        "SELECT DISTINCT d.id, d.filename FROM documents d JOIN pages p ON p.document_id = d.id"
        f" WHERE d.retired_at IS NULL AND {INDEXABLE} ORDER BY d.filename"
    ).fetchall()
    for document_id, filename in documents:
        with conn.transaction():
            conn.execute(
                "DELETE FROM chunks WHERE document_id = %s AND extraction = 'ocr'", (document_id,)
            )
            guide = guides.get(filename)
            if guide is None or not guide.passed:
                continue
            (engines,) = conn.execute(
                "SELECT count(DISTINCT ocr_engine) + count(*) FILTER (WHERE ocr_engine IS NULL)"
                f" FROM pages p WHERE p.document_id = %s AND {INDEXABLE}",
                (document_id,),
            ).fetchone()
            if engines != 1:
                print(f"{filename}: pages read with several OCR settings, not indexed")
                continue
            texts = ocr_pages(conn, filename)
            pages = [
                page
                for (page,) in conn.execute(
                    f"SELECT p.page FROM pages p WHERE p.document_id = %s AND {INDEXABLE}"
                    " ORDER BY p.page",
                    (document_id,),
                ).fetchall()
            ]
            (title,) = conn.execute(
                "SELECT title FROM documents WHERE id = %s", (document_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT id, page, path FROM sections WHERE document_id = %s ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            sections = [(section_id, f"{title} › {path}") for section_id, _, path in rows]

            # OCR text is not cut at headings: a page goes to the section open at its top
            starts = [start for _, start, _ in rows]
            chunks = [
                Chunk(
                    filename,
                    page,
                    piece,
                    section_at(starts, page),
                )
                for page in pages
                for piece in chunk_text(texts.get(page, ""))
            ]
            (start,) = conn.execute(
                "SELECT coalesce(max(position) + 1, 0) FROM chunks WHERE document_id = %s",
                (document_id,),
            ).fetchone()
            copy_chunks(conn, document_id, chunks, start, "ocr", sections=sections, title=title)
            renumber_chunks(conn, document_id)
            written[filename] = len(chunks)
    return written


def write_record(
    path: Path, guides: dict[str, GuideScore], written: dict[str, int], engines: dict[str, str]
) -> None:
    """The reliability record, versioned: scores and decisions only, never doctrine text."""
    lines = [
        "# OCR reliability check per guide (python -m secours_tech.ocr_bench index).",
        "# Rule: every number exact and a character error rate of at most 1 % on the",
        "# hand-transcribed pages (tuning and validation samples). Only scores live here.",
        "",
    ]
    for name, guide in sorted(guides.items()):
        lines += [
            "[[guide]]",
            f'file = "{name}"',
            f'engine = "{engines.get(name, "")}"',
            f"pages_checked = {sorted(p.page for p in guide.pages)}",
            f"character_error_rate = {guide.cer:.4f}",
            f"numbers_exact = {str(guide.numbers_exact).lower()}",
            f"passed = {str(guide.passed).lower()}",
            f"chunks_indexed = {written.get(name, 0)}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


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
    draw.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="folder of an earlier sample, whose transcribed pages are not drawn again (repeatable)",
    )
    index = sub.add_parser(
        "index", help="index the OCR text of the guides that pass, and write the record"
    )
    index.add_argument(
        "samples", type=Path, nargs="*", default=list(SAMPLES), help="transcription folders"
    )
    index.add_argument("--record", type=Path, default=RECORD)
    score = sub.add_parser("score", help="score the OCR text against the transcriptions")
    score.add_argument(
        "--with-image-labels",
        action="store_true",
        help="also expect the labels drawn inside pictures (img-p<N>.txt); out of scope in v0",
    )
    args = parser.parse_args(argv)

    with connect() as conn:
        if args.command == "sample":
            pdfs = {p.name: p for p in args.corpus.rglob("*.pdf")}
            exclude = frozenset(
                (stem, page) for folder in args.exclude for _, stem, page in transcriptions(folder)
            )
            for filename, pages in sample(conn, args.per_guide, args.seed, exclude).items():
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

        if args.command == "index":
            guides = score_guides(conn, args.samples)
            written = index_guides(conn, guides)
            engines = dict(
                conn.execute(
                    "SELECT d.filename, string_agg(DISTINCT p.ocr_engine, ' | ') FROM pages p"
                    " JOIN documents d ON d.id = p.document_id"
                    " WHERE d.retired_at IS NULL GROUP BY d.filename"
                ).fetchall()
            )
            write_record(args.record, guides, written, engines)
            print(f"{sum(written.values())} OCR chunks indexed for {len(written)} guide(s);"
                  f" record written to {args.record}")  # fmt: skip
        else:
            guides = score_guides(conn, [args.references], args.with_image_labels)

    for guide in guides.values():
        verdict = "PASS" if guide.passed else "FAIL"
        print(f"{verdict}  CER {guide.cer:6.2%}  numbers {'exact' if guide.numbers_exact else 'WRONG'}"
              f"  {len(guide.pages)} page(s)  {guide.guide}")  # fmt: skip
        for p in guide.pages:
            if p.missing_numbers or p.extra_numbers:
                print(f"      p{p.page}: missing {p.missing_numbers} extra {p.extra_numbers}")


if __name__ == "__main__":
    main()
