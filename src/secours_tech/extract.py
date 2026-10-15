"""Read each PDF page with poppler and decide how its text must be obtained.

Every page gets a route:
- `text_layer`: the PDF holds real characters and they read cleanly; `pdftotext` is enough.
- `ocr`: the text is drawn as images (one 1-bit stencil mask per line, how the ministry's Word
  exports store some fonts), the page is mostly an image, or the text layer is garbled.
- `blank`: no text and no image (versos, separators).
"""

import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MIN_LETTERS = 50  # below this, a page with images is treated as an image page
MIN_TEXT_MASKS = 3  # lines of text drawn as 1-bit masks; one or two can be a logo or a pictogram
MIN_MASK_PPI = 300  # text masks are high resolution; photos and icons are not
MIN_QUALITY = 0.6  # share of ordinary characters below which a text layer is garbled

_ORDINARY = set(".,;:!?'’\"«»()[]-–—/%°€$&+=*•…·")


@dataclass(frozen=True)
class Page:
    number: int  # 1-based, as printed in a PDF viewer
    text: str  # the text layer when route is "text_layer", empty otherwise
    route: str  # "text_layer", "ocr" or "blank"
    letters: int
    text_masks: int
    images: int
    quality: float


def text_quality(text: str) -> float:
    """Share of non-space characters that are letters, digits or ordinary punctuation."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 1.0
    return sum(c.isalnum() or c in _ORDINARY for c in chars) / len(chars)


def route(letters: int, text_masks: int, images: int, quality: float) -> str:
    if text_masks >= MIN_TEXT_MASKS:
        return "ocr"  # mixed pages included: OCR reads the real text and the drawn text alike
    if letters >= MIN_LETTERS:
        return "text_layer" if quality >= MIN_QUALITY else "ocr"
    if images:
        return "ocr"
    return "text_layer" if letters else "blank"  # a divider page with only a title keeps it


def _run(*command: str) -> str:
    if shutil.which(command[0]) is None:
        raise RuntimeError(
            f"{command[0]} not found: install poppler (brew install poppler, "
            "or apt-get install poppler-utils)"
        )
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def _images_per_page(path: Path) -> tuple[Counter[int], Counter[int]]:
    """Images, and high-resolution 1-bit masks (lines of text drawn as images), per page."""
    images: Counter[int] = Counter()
    masks: Counter[int] = Counter()
    # columns: page num type width height color comp bpc enc interp object-id x-ppi ...
    for line in _run("pdfimages", "-list", str(path)).splitlines()[2:]:
        cols = line.split()
        if len(cols) < 13 or not cols[0].isdigit():
            continue
        page = int(cols[0])
        images[page] += 1
        ppi = int(cols[12]) if cols[12].isdigit() else 0
        if cols[2] in ("mask", "smask") and cols[7] == "1" and ppi >= MIN_MASK_PPI:
            masks[page] += 1
    return images, masks


def extract_pages(path: Path) -> list[Page]:
    """Every page of a PDF with its text layer and the route its text must take."""
    path = path.resolve()  # an absolute path can never be mistaken for an option like "-q"
    parts = _run("pdftotext", "-enc", "UTF-8", str(path), "-").split("\f")
    if parts and parts[-1] == "":
        parts.pop()  # pdftotext ends every page, the last one included, with a form feed
    images, masks = _images_per_page(path)
    pages = []
    for number, text in enumerate(parts, start=1):
        letters = sum(c.isalpha() for c in text)
        quality = text_quality(text)
        way = route(letters, masks[number], images[number], quality)
        pages.append(
            Page(
                number,
                text if way == "text_layer" else "",
                way,
                letters,
                masks[number],
                images[number],
                round(quality, 3),
            )
        )
    return pages
