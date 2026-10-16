"""Section tree of a guide, read from its printed table of contents (D18).

The guides carry almost no PDF bookmarks, but every one prints a table of contents: one line
per heading, its title, a leader (dots, underscores or blank space) and the printed page number.
This module reads those lines from the layout-preserving text (`pdftotext -layout`, which keeps
the numbering beside the title and the indentation), gives each a level, finds where each
heading starts in the PDF (printed numbers can be shifted from the PDF's own page count), and
tells which pages are the table of contents itself, so they can be kept out of the search.

A guide that prints no usable table of contents falls back on its headings (`detect_headings`):
a fiche code line ("VEN-STR-PRO-1") with the title under it, "Chapitre"/"Section"/"Annexe"
lines, and dotted numbering ("6.1. Choix du ventilateur", or "6.1." alone with the title on the
next line).

Level of a line, from the strongest clue to the weakest: "Chapitre"/"Annexe"/"Partie" is 1;
"Section" is the level under it; a dotted numbering ("1.2.1") gives its depth; other lines take
their rank of indentation. When a guide prints nothing usable, it simply has no sections.
"""

import re
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

TOC_SCAN_PAGES = 25  # tables of contents sit in the first pages
MIN_TOC_LINES = 5  # a page with fewer such lines is not a table of contents
MAX_SHIFT = 6  # printed page numbers can be shifted this much from the PDF page
MIN_FOUND = 0.5  # share of headings found on their page for a table of contents to be trusted

_LEADER_LINE = re.compile(
    r"^(?P<indent> *)(?P<label>\S.*?)\s*(?:[.…_·]{2,}|\s{3,})\s*(?P<page>\d{1,3})\s*$"
)
_TOP = re.compile(r"^(chapitre|annexe|partie|titre)\b", re.IGNORECASE)
_SECTION = re.compile(r"^section\b", re.IGNORECASE)
_DOTTED = re.compile(r"^(?P<number>(?:\d+|[IVXLC]+)(?:[.\-]\d+)*)[.)]?\s+\S")
_LETTER = re.compile(r"^[a-z][.)]\s+\S")
_PSE_CODE = re.compile(r"^\[(?P<code>\w+) / [\d-]+\]")
_DASH_HEAD = re.compile(r"^-\s+\S")


@dataclass(frozen=True)
class Entry:
    """One line of a printed table of contents."""

    title: str
    printed_page: int
    level: int
    toc_page: int  # PDF page of the table of contents where the line is printed


@dataclass(frozen=True)
class Section:
    title: str
    level: int
    page: int  # PDF page where the heading is printed
    path: tuple[str, ...]  # titles from the top level down to this one


def _layout_pages(pdf: Path, last: int) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", "-l", str(last), "-enc", "UTF-8", str(pdf.resolve()), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.split("\f")


def _toc_lines(text: str) -> list[tuple[int, str, int]]:
    """(indent, label, printed page) for every line of a page that reads as a contents line."""
    found = []
    for line in text.splitlines():
        match = _LEADER_LINE.match(line)
        if match and re.search(r"[^\W\d_]{2,}", match["label"]):
            label = " ".join(match["label"].rstrip(" .…_·").split())
            found.append((len(match["indent"]), label, int(match["page"])))
    return found


def _levels(lines: list[tuple[int, str, int]]) -> list[int]:
    labels = [label for _, label, _ in lines]
    has_top = any(_TOP.match(label) for label in labels)
    has_section = any(_SECTION.match(label) for label in labels)
    below = 1 + has_top + has_section  # first level under chapters and sections
    # indents a couple of spaces apart are the same column, set in another font size
    columns: list[int] = []
    for indent in sorted(
        {i for i, label, _ in lines if not (_TOP.match(label) or _SECTION.match(label))}
    ):
        if not columns or indent - columns[-1] > 2:
            columns.append(indent)

    def rank(indent: int) -> int:
        return max((r for r, column in enumerate(columns) if column <= indent + 2), default=0)

    levels: list[int] = []
    parent = below  # level of the last line that is not a lettered item
    for indent, label, _ in lines:
        if _TOP.match(label) or _DASH_HEAD.match(label):
            level = 1
        elif _SECTION.match(label):
            level = 1 + has_top
        elif _PSE_CODE.match(label):
            level = 2
        elif dotted := _DOTTED.match(label):
            level = below + len(re.split(r"[.\-]", dotted["number"])) - 1
        elif _LETTER.match(label):
            levels.append(parent + 1)
            continue
        else:
            level = below + rank(indent)
        parent = level
        levels.append(level)
    return levels


def read_toc(pdf: Path) -> tuple[list[Entry], set[int]]:
    """Contents lines of a guide with their level, and the PDF pages that hold the contents."""
    pages = _layout_pages(pdf, TOC_SCAN_PAGES)
    lines: list[tuple[int, str, int]] = []
    origin: list[int] = []
    toc_pages = set()
    for number, text in enumerate(pages, start=1):
        found = _toc_lines(text)
        if len(found) >= MIN_TOC_LINES:
            toc_pages.add(number)
            lines += found
            origin += [number] * len(found)
    entries = [
        Entry(label, page, level, toc_page)
        for (_, label, page), level, toc_page in zip(lines, _levels(lines), origin, strict=True)
    ]
    return entries, toc_pages


def _key(text: str) -> str:
    """Lower case, no accents, letters and digits only: headings and their TOC line match."""
    text = unicodedata.normalize("NFD", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c)))


def _heading_words(title: str) -> str:
    """The words of a title without its numbering or chapter label, for finding it on a page."""
    title = _PSE_CODE.sub("", title)
    title = re.sub(
        r"^(chapitre|annexe|partie|titre|section)\s+\S+\s*[-–:]?\s*", "", title, flags=re.IGNORECASE
    )
    title = re.sub(r"^(?:\d+|[IVXLC]+)(?:[.\-]\d+)*[.)]?\s+", "", title)
    title = re.sub(r"^[a-z][.)]\s+|^-\s+|^PSE\S*\s+", "", title)
    return " ".join(_key(title).split()[:6])


def locate(entries: list[Entry], page_texts: list[str], toc_pages: set[int]) -> list[Section]:
    """Place each contents line on the PDF page where its heading is printed.

    The shift between printed and PDF page numbers is the one under which most titles are found
    on their page; each heading is then looked for on its page and the next one. Entries never
    found keep the shifted page. Returns the sections in order, each with its path of titles.
    """
    keys = [_key(text) for text in page_texts]

    def found(entry: Entry, page: int) -> bool:
        words = _heading_words(entry.title)
        return (
            bool(words)
            and 0 < page <= len(keys)
            and page not in toc_pages
            and words in keys[page - 1]
        )

    votes = Counter(
        shift
        for entry in entries
        for shift in range(-MAX_SHIFT, MAX_SHIFT + 1)
        if found(entry, entry.printed_page + shift)
    )
    shift = votes.most_common(1)[0][0] if votes else 0
    if not entries or votes[shift] < MIN_FOUND * len(entries):
        return []  # a garbled or misread table of contents gives no structure rather than a wrong one
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for entry in entries:
        page = entry.printed_page + shift
        if not found(entry, page) and found(entry, page + 1):
            page += 1
        if not 0 < page <= len(page_texts):
            continue
        while stack and stack[-1][0] >= entry.level:
            stack.pop()
        stack.append((entry.level, entry.title))
        sections.append(Section(entry.title, entry.level, page, tuple(t for _, t in stack)))
    return sections


def section_at(starts: list[int], page: int) -> int | None:
    """Index of the last section, in contents order, that starts on or before `page`."""
    found = None
    for index, start in enumerate(starts):
        if start <= page:
            found = index
    return found


def _starts_heading(keys: list[str], n: int, words: str) -> bool:
    """Whether the heading `words` starts on line n, alone or wrapping onto the next line."""
    if not words:
        return False
    line = keys[n].strip()
    if words in line:
        return True
    following = keys[n + 1].strip() if n + 1 < len(keys) else ""
    at = f"{line} {following}".find(words)
    return 0 <= at < len(line) and words not in following  # starts on line n, ends on n + 1


def split_page(text: str, page: int, sections: list[Section]) -> list[tuple[int | None, str]]:
    """Cut a page's text where the headings of the sections starting on it are printed.

    Returns (index of the section in `sections`, text) pieces in reading order: the text before
    the first heading belongs to the section already open on the previous page. A heading is
    looked for on one line, or on two when the title wraps; one that is not found starts at the
    top of the page.
    """
    current = section_at([section.page for section in sections], page - 1)
    starting = [i for i, section in enumerate(sections) if section.page == page]
    lines = text.splitlines(keepends=True)
    keys = [_key(line) for line in lines]
    cuts: list[tuple[int, int]] = []  # (line where the heading starts, section index)
    after = 0
    for index in starting:
        words = _heading_words(sections[index].title)
        at = next((n for n in range(after, len(lines)) if _starts_heading(keys, n, words)), None)
        if at is None:
            at = after if not cuts else cuts[-1][0]
        cuts.append((at, index))
        after = at
    pieces: list[tuple[int | None, str]] = []
    start, owner = 0, current
    for at, index in cuts:
        if at > start:
            pieces.append((owner, "".join(lines[start:at])))
        start, owner = at, index
    pieces.append((owner, "".join(lines[start:])))
    return [(owner, piece) for owner, piece in pieces if piece.strip()]


_FICHE_CODE = re.compile(r"^[A-Z]{2,6}(?:-[A-Z0-9]{1,6}){2,}$")
_NUMBERED = re.compile(r"^(?P<number>(?:\d{1,2}\.)+\d{0,2}\.?)\s*(?P<title>.*)$")
MAX_HEADING = 100  # a longer line is a sentence, not a heading


def _title_like(text: str) -> bool:
    text = text.strip()
    return (
        2 < len(text) <= MAX_HEADING
        and text[0].isupper()
        and not text.endswith((";", ",", ":"))
        and not re.search(r"(?:\.{3,}|_{3,})\s*\d+$", text)  # a contents line, not a heading
    )


MIN_LISTED = 4  # a table of contents: a page naming this many fiche codes, or this many headings
# with their page number making up most of its lines; its lines are not headings, it gives no chunk
_PAGE_REFERENCE = re.compile(r"\D(?:\s|\.{2,}|…|_{2,})+\d{1,3}$")  # "Titre 12", "Titre...12"


def _is_contents(lines: list[str]) -> bool:
    codes = sum(bool(_FICHE_CODE.match(line.split()[0])) for line in lines)
    entries = sum(
        bool(_TOP.match(line) or _SECTION.match(line) or _NUMBERED.match(line))
        and bool(_PAGE_REFERENCE.search(line))
        for line in lines
    )
    return codes >= MIN_LISTED or (entries >= MIN_LISTED and 2 * entries >= len(lines))


def detect_headings(
    page_texts: list[str], skip: AbstractSet[int] = frozenset()
) -> tuple[list[Section], set[int]]:
    """Sections found from the headings printed in the text, for a guide without a usable table
    of contents, and the pages that list fiches or headings with their page (contents pages,
    printed in the guide even when the PDF has no usable one). Pages in `skip` are not read."""
    found: list[tuple[int, str, int]] = []  # (level, title, page)
    listing: set[int] = set()
    for number, text in enumerate(page_texts, start=1):
        if number in skip:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if _is_contents(lines):
            listing.add(number)
            continue
        for i, line in enumerate(lines):
            following = lines[i + 1] if i + 1 < len(lines) else ""
            code, _, rest = line.partition(" ")
            if _FICHE_CODE.match(code) and _title_like(rest.strip() or following):
                # a family code (VEN-STR-PRO) sits above its fiches (VEN-STR-PRO-1); the title
                # follows the code on its line or on the next one
                found.append((2 + code.count("-"), f"{code} {rest.strip() or following}", number))
            elif _TOP.match(line) or _SECTION.match(line):
                if _title_like(line):
                    found.append((1 if _TOP.match(line) else 2, line, number))
            elif numbered := _NUMBERED.match(line):
                title = numbered["title"] or following
                depth = len([n for n in numbered["number"].split(".") if n])
                if _title_like(title):
                    label = (
                        f"{numbered['number']} {title}" if numbered["title"] else f"{line} {title}"
                    )
                    found.append((10 + depth, " ".join(label.split()), number))
    if not found:
        return [], listing
    # levels: chapters, sections, fiche families then fiches, then numbering; renumbered from 1
    ranks = {level: rank for rank, level in enumerate(sorted({level for level, _, _ in found}), 1)}
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for level, title, page in found:
        level = ranks[level]
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        sections.append(Section(title, level, page, tuple(t for _, t in stack)))
    return sections, listing
