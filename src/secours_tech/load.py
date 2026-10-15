"""Load PDFs into Postgres: one row per document in `documents`, its chunks in `chunks`."""

import argparse
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from secours_tech.db import connect, migrate
from secours_tech.ingest import pages_to_chunks, read_pages

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceDocument:
    filename: str
    sha256: str
    page_texts: list[str]


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


def read_source(path: Path) -> SourceDocument:
    data = path.read_bytes()
    return SourceDocument(path.name, hashlib.sha256(data).hexdigest(), read_pages(path))


def store_document(conn: psycopg.Connection, doc: SourceDocument) -> int | None:
    """Insert a document and its chunks in one transaction.

    Returns the number of chunks, or None when a file with the same bytes is already loaded,
    so running the loader twice changes nothing.
    """
    with conn.transaction():
        chunks = pages_to_chunks(doc.filename, doc.page_texts)
        # a page "without text" is one that gives no chunk, not only a blank page: many guides
        # draw their text as images and only the page number survives the extraction
        textless = len(doc.page_texts) - len({chunk.page for chunk in chunks})
        row = conn.execute(
            """
            INSERT INTO documents (filename, title, family, source, sha256, page_count,
                                   textless_page_count)
            VALUES (%s, %s, %s, 'local', %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            RETURNING id
            """,
            (
                doc.filename,
                title_of(doc.filename),
                family_of(doc.filename),
                doc.sha256,
                len(doc.page_texts),
                textless,
            ),
        ).fetchone()
        if row is None:
            return None
        # COPY streams all rows in one round trip: thousands of chunks load in about a second
        with (
            conn.cursor() as cur,
            cur.copy("COPY chunks (document_id, page, position, text) FROM STDIN") as copy,
        ):
            for position, chunk in enumerate(chunks):
                copy.write_row((row[0], chunk.page, position, chunk.text))
    return len(chunks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load every PDF of a folder into Postgres.")
    parser.add_argument("source", type=Path, help="folder containing PDFs (searched recursively)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # the per-page "no extractable text" warnings are summed up in textless_page_count instead
    logging.getLogger("secours_tech.ingest").setLevel(logging.ERROR)
    pdfs = sorted(args.source.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDF found in {args.source}")
    with connect() as conn:
        migrate(conn)
        for pdf in pdfs:
            count = store_document(conn, read_source(pdf))
            if count is None:
                log.info("%s: already loaded, skipped", pdf.name)
            else:
                log.info("%s: %d chunks", pdf.name, count)


if __name__ == "__main__":
    main()
