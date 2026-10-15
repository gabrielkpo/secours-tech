"""Turn PDFs into chunks that remember which document and page they came from."""

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from secours_tech.extract import extract_pages

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


@dataclass(frozen=True)
class Chunk:
    document: str
    page: int  # 1-based, as printed in a PDF viewer
    text: str


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


def strip_repeated_lines(pages: list[str], min_pages: int = 3, ratio: float = 0.5) -> list[str]:
    """Remove headers and footers: short lines that appear on at least half of the pages.

    Only pages that have text count: pages left empty (routed to OCR, or blank) would otherwise
    raise the threshold beyond reach in mostly-image guides. Lines are compared by their words of
    three letters or more, so "Page 3" and "Page 4" count as the same line, and so does an OCR'd
    footer next to a logo read as "@&" on one page and "À\\" on the next.
    """
    with_text = sum(1 for page in pages if page.strip())
    if with_text < min_pages:
        return pages

    def key(line: str) -> str:
        return " ".join(re.findall(r"[^\W\d_]{3,}", line))

    counts: dict[str, int] = {}
    for page in pages:
        for k in {key(line) for line in page.splitlines()} - {""}:
            counts[k] = counts.get(k, 0) + 1
    threshold = max(min_pages, ratio * with_text)

    def is_boilerplate(line: str) -> bool:
        return len(line) < 120 and counts.get(key(line) or None, 0) >= threshold

    return ["\n".join(ln for ln in p.splitlines() if not is_boilerplate(ln)) for p in pages]


def pages_to_chunks(document: str, page_texts: list[str], size: int = 800) -> list[Chunk]:
    """Chunk already-extracted page texts. Pages with no text are reported, not hidden."""
    chunks: list[Chunk] = []
    for number, text in enumerate(strip_repeated_lines(page_texts), start=1):
        if not text.strip():
            log.warning("%s page %d has no extractable text (scanned page?)", document, number)
            continue
        chunks.extend(Chunk(document, number, piece) for piece in chunk_text(text, size))
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
