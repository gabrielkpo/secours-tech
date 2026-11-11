import os
from pathlib import Path

import psycopg
import pytest

from secours_tech.db import migrate

TEST_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def conn():
    """A migrated, empty test database; skipped when TEST_DATABASE_URL is not set."""
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        name = conn.execute("SELECT current_database()").fetchone()[0]
        # the fixture wipes the schema: refuse anything that is not a test database
        assert name.endswith("_test"), f"refusing to wipe {name!r}"
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        migrate(conn)
        yield conn


def write_pdf(path: Path, pages: list[str]) -> Path:
    """Write a minimal PDF with one line of Helvetica text per page ("" gives a blank page)."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET" if text else ""
        data = stream.encode("cp1252")
        objects.append(f"<< /Length {len(data)} >>\nstream\n{stream}\nendstream")
        content = len(objects)
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("cp1252")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def make_pdf(tmp_path: Path):
    return lambda pages, name="doc.pdf": write_pdf(tmp_path / name, pages)
