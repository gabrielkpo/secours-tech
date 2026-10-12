"""Turn PDFs into chunks that remember which document and page they came from."""

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class Chunk:
    document: str
    page: int  # 1-based, as printed in a PDF viewer
    text: str


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into windows of `size` characters; consecutive windows share `overlap`.

    The overlap avoids cutting a sentence in two with no context on either side.
    """
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("size must be > 0 and 0 <= overlap < size")
    text = " ".join(text.split())  # collapse newlines and repeated spaces
    if not text:
        return []
    step = size - overlap
    return [text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()]


def ingest_pdf(path: Path, size: int = 800, overlap: int = 100) -> list[Chunk]:
    """Extract text page by page so each chunk carries its page number."""
    reader = PdfReader(path)
    chunks: list[Chunk] = []
    for number, page in enumerate(reader.pages, start=1):
        for piece in chunk_text(page.extract_text() or "", size, overlap):
            chunks.append(Chunk(document=path.name, page=number, text=piece))
    return chunks
