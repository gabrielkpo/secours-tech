"""Turn PDFs into chunks that remember which document and page they came from."""

import argparse
import json
import logging
import re
from collections import Counter
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import asdict, dataclass
from pathlib import Path

from secours_tech.extract import extract_pages
from secours_tech.structure import Section, split_page

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


@dataclass(frozen=True)
class Chunk:
    document: str
    page: int  # 1-based, as printed in a PDF viewer
    text: str
    section: int | None = None  # index in the document's sections, None when it has none


def chunk_text(text: str, size: int = 800, overlap: int = 1) -> list[str]:
    """Pack whole sentences into chunks of at most `size` characters.

    Cutting on sentence boundaries keeps each chunk readable on its own, which matters
    both for retrieval and for quoting. The last `overlap` sentences of a chunk are
    repeated at the start of the next one so context is not lost at the boundary.
    A single sentence longer than `size` is split hard, as a last resort.
    """
    if size <= 0 or overlap < 0:
        raise ValueError("size must be > 0 and overlap >= 0")
    text = " ".join(text.split())  # collapse newlines and repeated spaces
    if not text:
        return []

    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        pieces.extend(sentence[i : i + size] for i in range(0, len(sentence), size))

    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and len(" ".join([*current, piece])) > size:
            chunks.append(" ".join(current))
            current = current[-overlap:] if overlap else []
            if current and len(" ".join([*current, piece])) > size:
                current = []  # the carried context does not fit next to this piece
        current.append(piece)
    if current:
        chunks.append(" ".join(current))
    return chunks


EDGE = 3  # non-empty lines at the top and at the bottom of a page that may be a header or footer
_WORD = re.compile(r"[^\W\d_]{3,}")
_NUMBER = re.compile(r"\d+")


def _zones(lines: list[str]) -> dict[int, set[str]]:
    """The header and footer zones each line index belongs to (a short page's line can be in
    both); lines in the middle of the page are in none and are never removed."""
    filled = [i for i, line in enumerate(lines) if line.strip()]
    zones: dict[int, set[str]] = {}
    for i in filled[:EDGE]:
        zones.setdefault(i, set()).add("top")
    for i in filled[-EDGE:]:
        zones.setdefault(i, set()).add("bottom")
    return zones


def strip_repeated_lines(
    pages: list[str],
    min_pages: int = 3,
    ratio: float = 0.5,
    numbers: Sequence[int] | None = None,
) -> list[str]:
    """Remove running headers and footers, and nothing else.

    Only the first and last `EDGE` non-empty lines of a page are candidates, and a line goes
    only if the same line sits in the same zone (top or bottom) on at least half of the pages,
    once per page: a line repeated within its page (a flowchart's "oui" and "non") is content.
    Lines are compared by their words of three letters or more, so "Page 3" and "Page 4" count
    as the same line, and so does an OCR'd footer next to a logo read as "@&" on one page and
    "À\\" on the next. A line without such words but with digits goes only if it is a page
    number: printed number minus the page's number (`numbers`, by default 1, 2, 3...) equal to
    an offset shared by half of the pages. A table value such as "50" stays wherever it is.

    Only pages that have text count: pages left empty (routed to OCR, or blank) would otherwise
    raise the threshold beyond reach in mostly-image guides.
    """
    with_text = sum(1 for page in pages if page.strip())
    if with_text < min_pages:
        return pages
    numbers = list(numbers) if numbers is not None else list(range(1, len(pages) + 1))
    if len(numbers) != len(pages):
        raise ValueError("one page number per page")

    def key(line: str) -> str:
        words = _WORD.findall(line)
        return " ".join(words) if words else _NUMBER.sub("#", line.strip())

    split = [page.splitlines() for page in pages]
    zoned = [_zones(lines) for lines in split]
    once = []  # per page, the keys that occur on a single line of the page
    for lines in split:
        seen = Counter(key(line) for line in lines if line.strip())
        once.append({k for k, times in seen.items() if times == 1})
    counts: dict[tuple[str, str], int] = {}
    offsets: dict[int, int] = {}
    for lines, zones, number, single in zip(split, zoned, numbers, once, strict=True):
        keys, shifts = set(), set()
        for i, names in zones.items():
            line = lines[i].strip()
            if _WORD.search(line) or not _NUMBER.search(line):
                if key(line) in single:
                    keys.update((zone, key(line)) for zone in names)
            else:
                shifts.update(int(n) - number for n in _NUMBER.findall(line))
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        for s in shifts:
            offsets[s] = offsets.get(s, 0) + 1
    threshold = max(min_pages, ratio * with_text)
    sequence = {shift for shift, pages_with in offsets.items() if pages_with >= threshold}

    def is_boilerplate(line: str, names: set[str], number: int, single: set[str]) -> bool:
        line = line.strip()
        if len(line) >= 120:
            return False
        if _WORD.search(line) or not _NUMBER.search(line):
            k = key(line)
            return k in single and any(counts.get((z, k), 0) >= threshold for z in names)
        # a bare number goes only as a page number (its other occurrences on the page do not
        # matter: tables are full of bare numbers)
        return any(int(n) - number in sequence for n in _NUMBER.findall(line))

    return [
        "\n".join(
            line
            for i, line in enumerate(lines)
            if not (i in zones and is_boilerplate(line, zones[i], number, single))
        )
        for lines, zones, number, single in zip(split, zoned, numbers, once, strict=True)
    ]


def pages_to_chunks(
    document: str,
    page_texts: list[str],
    size: int = 800,
    sections: Sequence[Section] = (),
    skip: AbstractSet[int] = frozenset(),
) -> list[Chunk]:
    """Chunk already-extracted page texts. Pages with no text are reported, not hidden.

    With the guide's sections, a page is first cut where its headings are printed, so that no
    chunk straddles two sections and each knows its own. Pages in `skip` (the table of contents)
    give no chunk.
    """
    chunks: list[Chunk] = []
    for number, text in enumerate(strip_repeated_lines(page_texts), start=1):
        if number in skip:
            continue
        if not text.strip():
            log.warning("%s page %d has no extractable text (scanned page?)", document, number)
            continue
        for section, piece in split_page(text, number, list(sections)):
            chunks.extend(
                Chunk(document, number, part, section) for part in chunk_text(piece, size)
            )
    return chunks


def read_pages(path: Path) -> list[str]:
    """Text layer of every page, in order. A page routed to OCR or blank gives an empty string."""
    return [page.text for page in extract_pages(path)]


def ingest_pdf(path: Path, size: int = 800) -> list[Chunk]:
    return pages_to_chunks(path.name, read_pages(path), size)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest every PDF of a folder into a JSONL file.")
    parser.add_argument("source", type=Path, help="folder containing PDFs (searched recursively)")
    parser.add_argument("output", type=Path, help="JSONL file to write, one chunk per line")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with args.output.open("w", encoding="utf-8") as out:
        for pdf in sorted(args.source.rglob("*.pdf")):
            chunks = ingest_pdf(pdf)
            for chunk in chunks:
                out.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
            total += len(chunks)
            log.info("%s: %d chunks", pdf.name, len(chunks))
    log.info("done: %d chunks in %s", total, args.output)


if __name__ == "__main__":
    main()
