"""Read each PDF page with poppler and decide how its text must be obtained.

Every page gets a route:
- `text_layer`: the PDF holds real characters and they read cleanly; `pdftotext` is enough.
- `ocr`: the text is drawn as images (one 1-bit stencil mask per line, how the ministry's Word
  exports store some fonts), the page is mostly an image, or the text layer is garbled.

A text layer can be garbled in two ways that a character count does not see:
- a font with a custom encoding and no ToUnicode table (Type 3 fonts in some exports): the page
  shows the right letters but the text layer maps them to the wrong ones ("EFGHIJKL…", control
  characters). A page with control characters goes to OCR; so does a page with almost no French
  function words when OCR, which reads the drawn letters, does not find its words either;
- accents stored as separate combining marks: `pdftotext` then inserts a space after each one
  ("poussiè res"). Those pages are read again in raw order (`pdftotext -raw`), which keeps the
  word whole, and all text is normalised to composed characters (NFC).
- `blank`: no text and no image (versos, separators).
"""

import logging
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

MIN_LETTERS = 50  # below this, a page with images is treated as an image page
MIN_TEXT_MASKS = 3  # lines of text drawn as 1-bit masks; one or two can be a logo or a pictogram
MIN_MASK_PPI = 300  # text masks are high resolution; photos and icons are not
MIN_QUALITY = 0.6  # share of ordinary characters below which a text layer is garbled
MIN_FRENCH = 0.08  # share of French function words below which a text layer is suspect
MIN_WORDS = 20  # fewer words (a title, a code) say nothing about the language
MIN_OCR_OVERLAP = 0.5  # share of the text layer's words that OCR must find for it to be trusted
MIN_SPLIT_ACCENTS = 3  # combining marks followed by a space before a page is read in raw order
MAX_CONTROL_CHARS = 3  # control characters (\x14…) never occur in real text; a few are noise

log = logging.getLogger(__name__)
_WORD = re.compile(r"[a-zàâçéèêëîïôûùüÿœ]{2,}")
_LONG_WORD = re.compile(r"[a-zàâçéèêëîïôûùüÿœ]{3,}")
_SPLIT_ACCENT = re.compile(r"[\u0300-\u036f] (?=\w)")
_FUNCTION_WORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "de",
        "des",
        "du",
        "un",
        "une",
        "et",
        "en",
        "au",
        "aux",
        "pour",
        "par",
        "sur",
        "dans",
        "est",
        "sont",
        "qui",
        "que",
        "ne",
        "pas",
        "il",
        "elle",
        "se",
        "ce",
        "ces",
        "son",
        "sa",
        "ses",
        "leur",
        "leurs",
        "avec",
        "ou",
        "plus",
    ]
)

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


def french_share(text: str) -> float | None:
    """Share of French function words among the words, or None when there are too few words."""
    words = _WORD.findall(text.lower())
    if len(words) < MIN_WORDS:
        return None
    return sum(w in _FUNCTION_WORDS for w in words) / len(words)


def word_overlap(layer: str, ocr: str) -> float:
    """Share of the text layer's words (3+ letters, counted with repeats) that OCR also reads.

    Words, not characters in order: a two-column page or a table is read in another order by
    OCR and the text layer alike, but holds the same words; a garbled layer holds none of them.
    """
    found = Counter(_LONG_WORD.findall(ocr.lower()))
    words = Counter(_LONG_WORD.findall(layer.lower()))
    total = sum(words.values())
    return sum((words & found).values()) / total if total else 1.0


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


def _pdftotext(path: Path, *options: str) -> list[str]:
    parts = _run("pdftotext", *options, "-enc", "UTF-8", str(path), "-").split("\f")
    if parts and parts[-1] == "":
        parts.pop()  # pdftotext ends every page, the last one included, with a form feed
    return parts


def _read_text(path: Path) -> list[str]:
    """Text layer of every page, split accents mended, in composed Unicode (NFC)."""
    parts = _pdftotext(path)
    split = [len(_SPLIT_ACCENT.findall(text)) >= MIN_SPLIT_ACCENTS for text in parts]
    if any(split):
        raw = _pdftotext(path, "-raw")
        if len(raw) == len(parts):
            parts = [r if s else t for t, r, s in zip(parts, raw, split, strict=True)]
            log.info("%s: %d page(s) read in raw order (split accents)", path.name, sum(split))
    return [unicodedata.normalize("NFC", text) for text in parts]


def control_chars(text: str) -> int:
    """Control characters other than line breaks and tabs: the mark of a font without ToUnicode."""
    return sum(unicodedata.category(c) == "Cc" and c not in "\n\r\t\f" for c in text)


def _is_garbled(path: Path, number: int, text: str) -> bool:
    """A text layer full of control characters, or with almost no French function words which
    OCR does not confirm either."""
    if control_chars(text) > MAX_CONTROL_CHARS:
        log.warning("%s p%d: text layer full of control characters, sent to OCR", path.name, number)
        return True
    share = french_share(text)
    if share is None or share >= MIN_FRENCH:
        return False
    from secours_tech.ocr import MODEL_DIR, ocr_page  # OCR is needed only for suspect pages

    if shutil.which("tesseract") is None or not (MODEL_DIR / "fra.traineddata").exists():
        log.warning("%s p%d: suspect text layer, OCR unavailable to check it", path.name, number)
        return True  # never index a text layer that could not be checked
    overlap = word_overlap(text, ocr_page(path, number).text)
    if overlap < MIN_OCR_OVERLAP:
        log.warning("%s p%d: garbled text layer (OCR finds %.0f%% of its words), sent to OCR",
                    path.name, number, 100 * overlap)  # fmt: skip
        return True
    return False


def extract_pages(path: Path) -> list[Page]:
    """Every page of a PDF with its text layer and the route its text must take."""
    path = path.resolve()  # an absolute path can never be mistaken for an option like "-q"
    parts = _read_text(path)
    images, masks = _images_per_page(path)
    pages = []
    for number, text in enumerate(parts, start=1):
        letters = sum(c.isalpha() for c in text)
        quality = text_quality(text)
        way = route(letters, masks[number], images[number], quality)
        if way == "text_layer" and letters and _is_garbled(path, number, text):
            way = "ocr"
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
