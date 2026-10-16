"""Load PDFs into Postgres: one row per document in `documents`, its chunks in `chunks`."""

import argparse
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

from secours_tech.collect import MANIFEST, Entry, Manifest
from secours_tech.db import connect, migrate
from secours_tech.extract import Page, extract_pages
from secours_tech.ingest import Chunk, pages_to_chunks, strip_repeated_lines
from secours_tech.structure import Section, detect_headings, locate, read_toc

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceDocument:
    filename: str
    sha256: str
    page_texts: list[str]
    pages: tuple[Page, ...] = ()  # per-page route and measures, when read from a PDF
    sections: tuple[Section, ...] = ()  # from the printed table of contents (D18)
    toc_pages: frozenset[int] = frozenset()  # the table of contents itself: no chunk
    sections_origin: str = "contents"  # "contents" (printed) or "headings" (fallback of D18)

    @property
    def content_sha256(self) -> str:
        """Fingerprint of the extracted text, page breaks included.

        A document with no text at all (fully scanned) falls back to its file fingerprint:
        two different scans of the same length would otherwise share the empty-text hash.
        """
        if not any(text.strip() for text in self.page_texts):
            return self.sha256
        return hashlib.sha256("\f".join(self.page_texts).encode("utf-8")).hexdigest()


def family_of(filename: str) -> str:
    """Doctrine family, read from the official file name.

    SSUAP is tested first because its guide is named "GDO-SSUAP-...". An unknown name is an
    error rather than a guess: a document filed in the wrong family would skew the metrics.
    """
    name = filename.upper()
    if "SSUAP" in name:
        return "SSUAP"
    if re.search(r"(?<![A-Z])PSE(?![A-Z])", name):
        return "PSE"
    for family in ("GTO", "GDO"):
        if name.startswith(family):
            return family
    raise ValueError(f"cannot tell the doctrine family of {filename!r}")


def title_of(filename: str) -> str:
    """Readable title from the file name, until the scraper reads the real one on the web page."""
    return " ".join(re.split(r"[-_\s]+", Path(filename).stem)).strip()


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_source(path: Path, data: bytes) -> SourceDocument:
    """Fingerprint the bytes already read, extract every page and read the section tree."""
    pages = tuple(extract_pages(path))
    texts = [page.text for page in pages]
    entries, toc_pages = read_toc(path)
    sections, origin = tuple(locate(entries, texts, toc_pages)), "contents"
    if not sections:
        # no usable printed contents: the headings in the text, and the fiche listings as contents
        found, toc_pages = detect_headings(strip_repeated_lines(texts))
        sections, origin = tuple(found), "headings"
    return SourceDocument(
        path.name, sha256_of(data), texts, pages, sections, frozenset(toc_pages), origin
    )


def is_loaded(conn: psycopg.Connection, sha256: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM documents WHERE sha256 = %s", (sha256,)).fetchone() is not None
    )


def _textless_pages(doc: SourceDocument, chunks: list[Chunk]) -> int:
    # a page "without text" is one that gives no chunk, not only a blank page: many guides
    # draw their text as images and only the page number survives the extraction
    return len(doc.page_texts) - len({chunk.page for chunk in chunks})


def insert_sections(
    conn: psycopg.Connection,
    document_id: int,
    title: str,
    sections: Sequence[Section],
    origin: str = "contents",
) -> list[tuple[int, str]]:
    """Store a document's sections; returns (id, context "guide › … › section") per section."""
    rows = []
    (start,) = conn.execute(
        "SELECT coalesce(max(ordinal) + 1, 0) FROM sections WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    for ordinal, section in enumerate(sections, start=start):
        path = " › ".join(section.path)
        (section_id,) = conn.execute(
            "INSERT INTO sections (document_id, ordinal, level, title, path, page, origin)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (document_id, ordinal, section.level, section.title, path, section.page, origin),
        ).fetchone()
        rows.append((section_id, f"{title} › {path}"))
    return rows


def copy_chunks(
    conn: psycopg.Connection,
    document_id: int,
    chunks: list[Chunk],
    start: int = 0,
    extraction: str = "text_layer",
    sections: Sequence[tuple[int, str]] = (),
    title: str | None = None,
) -> None:
    """Write chunks with their section and context; `sections` is insert_sections' result and
    `title` the context of a chunk outside any section."""
    # COPY streams all rows in one round trip: thousands of chunks load in about a second
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY chunks (document_id, page, position, text, extraction, section_id, context)"
            " FROM STDIN"
        ) as copy,
    ):
        for position, chunk in enumerate(chunks, start=start):
            section_id, context = (
                sections[chunk.section] if chunk.section is not None else (None, title)
            )
            copy.write_row(
                (document_id, chunk.page, position, chunk.text, extraction, section_id, context)
            )


def renumber_chunks(conn: psycopg.Connection, document_id: int) -> None:
    """Make `position` follow the reading order again (page, then order on the page) after chunks
    of another extraction were added. Two steps, so the unique (document, position) never clashes.
    """
    conn.execute(
        "UPDATE chunks SET position = position + 1000000 WHERE document_id = %s", (document_id,)
    )
    conn.execute(
        """
        UPDATE chunks c SET position = r.n FROM (
            SELECT id, row_number() OVER (ORDER BY page, position) - 1 AS n
            FROM chunks WHERE document_id = %s
        ) r WHERE c.id = r.id
        """,
        (document_id,),
    )


def store_document(
    conn: psycopg.Connection, doc: SourceDocument, entry: Entry | None = None
) -> int | None:
    """Insert a document and its chunks in one transaction.

    `entry` is the manifest record of an official guide (family, title, URL). When a current
    version with the same URL exists, it is retired and the new row gets the next version.
    Returns the number of chunks, or None when a file with the same bytes or the same extracted
    text is already loaded, so running the loader twice changes nothing.
    """
    with conn.transaction():
        duplicate = conn.execute(
            "SELECT 1 FROM documents WHERE sha256 = %s OR content_sha256 = %s",
            (doc.sha256, doc.content_sha256),
        ).fetchone()
        if duplicate:
            return None
        chunks = pages_to_chunks(
            doc.filename, doc.page_texts, sections=doc.sections, skip=doc.toc_pages
        )
        textless = _textless_pages(doc, chunks)
        version = 1
        if entry is not None:
            previous = conn.execute(
                "UPDATE documents SET retired_at = now()"
                " WHERE url = %s AND retired_at IS NULL RETURNING version",
                (entry.url,),
            ).fetchone()
            if previous:
                version = previous[0] + 1
        row = conn.execute(
            """
            INSERT INTO documents (filename, title, family, source, url, sha256, content_sha256,
                                   version, page_count, textless_page_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                doc.filename,
                entry.title if entry else title_of(doc.filename),
                entry.family if entry else family_of(doc.filename),
                "web" if entry else "local",
                entry.url if entry else None,
                doc.sha256,
                doc.content_sha256,
                version,
                len(doc.page_texts),
                textless,
            ),
        ).fetchone()
        title = entry.title if entry else title_of(doc.filename)
        sections = insert_sections(conn, row[0], title, doc.sections, doc.sections_origin)
        copy_chunks(conn, row[0], chunks, sections=sections, title=title)
        if doc.pages:
            with (
                conn.cursor() as cur,
                cur.copy(
                    "COPY pages (document_id, page, route, letters, text_masks, images, quality,"
                    " is_toc) FROM STDIN"
                ) as copy,
            ):
                for p in doc.pages:
                    copy.write_row(
                        (row[0], p.number, p.route, p.letters, p.text_masks, p.images, p.quality,
                         p.number in doc.toc_pages)
                    )  # fmt: skip
    return len(chunks)


def reextract_document(conn: psycopg.Connection, document_id: int, doc: SourceDocument) -> int:
    """Replace a loaded document's chunks and page routes with a new extraction of the same file.

    Used when the extraction itself changes (a garbled text layer now detected, accents mended).
    OCR text already stored on a page is kept: a page newly routed to OCR has none yet and is
    picked up by the next OCR run. Returns the number of chunks.
    """
    chunks = pages_to_chunks(
        doc.filename, doc.page_texts, sections=doc.sections, skip=doc.toc_pages
    )
    with conn.transaction():
        conn.execute(
            "DELETE FROM chunks WHERE document_id = %s AND extraction = 'text_layer'",
            (document_id,),
        )
        # OCR sections belong to `ocr_bench index`, which rebuilds them with the OCR chunks
        conn.execute(
            "DELETE FROM sections WHERE document_id = %s AND origin <> 'ocr'", (document_id,)
        )
        (title,) = conn.execute(
            "SELECT title FROM documents WHERE id = %s", (document_id,)
        ).fetchone()
        sections = insert_sections(conn, document_id, title, doc.sections, doc.sections_origin)
        start = conn.execute(
            "SELECT coalesce(max(position) + 1, 0) FROM chunks WHERE document_id = %s",
            (document_id,),
        ).fetchone()[0]
        copy_chunks(conn, document_id, chunks, start, sections=sections, title=title)
        renumber_chunks(conn, document_id)
        for p in doc.pages:
            conn.execute(
                "UPDATE pages SET route = %s, letters = %s, text_masks = %s, images = %s,"
                " quality = %s, is_toc = %s WHERE document_id = %s AND page = %s",
                (p.route, p.letters, p.text_masks, p.images, p.quality,
                 p.number in doc.toc_pages, document_id, p.number),
            )  # fmt: skip
        conn.execute(
            "UPDATE documents SET content_sha256 = %s, textless_page_count = %s WHERE id = %s",
            (doc.content_sha256, _textless_pages(doc, chunks), document_id),
        )
    return len(chunks)


def sync_with_manifest(conn: psycopg.Connection, entries: list[Entry]) -> tuple[int, int]:
    """Make the manifest decide which official guides are current.

    A row is current exactly when its (URL, SHA-256) is in the manifest: a guide dropped from it
    (withdrawn, or replaced by an edition at another URL) is retired, and a guide put back is
    restored. Returns (retired, restored).
    """
    listed = ([entry.url for entry in entries], [entry.sha256 for entry in entries])
    pairs = "(url, sha256) IN (SELECT * FROM unnest(%s::text[], %s::text[]))"
    with conn.transaction():
        retired = conn.execute(
            "UPDATE documents SET retired_at = now()"
            f" WHERE source = 'web' AND retired_at IS NULL AND NOT {pairs}",
            listed,
        ).rowcount
        restored = conn.execute(
            f"UPDATE documents SET retired_at = NULL WHERE source = 'web' AND retired_at IS NOT NULL AND {pairs}",
            listed,
        ).rowcount
    return retired, restored


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load every PDF of a folder into Postgres.")
    parser.add_argument("source", type=Path, help="folder containing PDFs (searched recursively)")
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST, help="official sources (family, title, URL)"
    )
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="extract the PDFs already loaded again (after a change to the extraction)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # the per-page "no extractable text" warnings are summed up in textless_page_count instead
    logging.getLogger("secours_tech.ingest").setLevel(logging.ERROR)
    pdfs = sorted(args.source.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF found in {args.source}")
    official = {}
    if args.manifest.exists():
        official = {doc.sha256: doc for doc in Manifest.load(args.manifest).documents}
    else:
        log.warning("no manifest at %s: guides are loaded without official URL", args.manifest)
    with connect() as conn:
        migrate(conn)
        for pdf in pdfs:
            data = pdf.read_bytes()
            sha = sha256_of(data)
            # check the fingerprint before parsing: a rerun skips loaded files without the
            # extraction cost
            if is_loaded(conn, sha):
                if not args.reextract:
                    log.info("%s: already loaded, skipped", pdf.name)
                    continue
                (document_id,) = conn.execute(
                    "SELECT id FROM documents WHERE sha256 = %s", (sha,)
                ).fetchone()
                count = reextract_document(conn, document_id, read_source(pdf, data))
                log.info("%s: extracted again, %d chunks", pdf.name, count)
                continue
            count = store_document(conn, read_source(pdf, data), official.get(sha))
            if count is None:
                log.info("%s: same text as a loaded document, skipped", pdf.name)
            else:
                log.info("%s: %d chunks", pdf.name, count)
        if official:
            retired, restored = sync_with_manifest(conn, list(official.values()))
            if retired or restored:
                log.info("manifest sync: %d guide(s) retired, %d restored", retired, restored)


if __name__ == "__main__":
    main()
