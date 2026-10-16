"""OCR of the pages routed to `ocr`, with Tesseract and its French model.

Each page is copied with its pictures removed: the ministry's Word exports draw every line of
text as a tiny solid image carrying a 1-bit mask, while photos, drawings and box backgrounds are
ordinary images. Only the text is left (grey boxes no longer hide their text, drawings no longer
read as gibberish). The copy is rendered at 600 dpi with `pdftoppm` and read by Tesseract (LSTM
engine, Sauvola binarisation, `fra` model from tessdata_best pinned to one commit and checked by
SHA-256). Text and mean word confidence are stored on the page row; a page read with other
settings is read again. Settings chosen on a hand-checked sample and validated on another one
(`secours_tech.ocr_bench`). OCR text is not indexed here: a guide's OCR text is only
used once its sample passes the reliability check (`secours_tech.ocr_bench`).
"""

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from secours_tech.db import connect, migrate

log = logging.getLogger(__name__)

MODEL_DIR = Path("data/tessdata")
MODEL_COMMIT = "9ddc24e750eec0994223a9edc3fcb434a2244f3b"  # tesseract-ocr/tessdata_best
MODEL_URL = (
    f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{MODEL_COMMIT}/fra.traineddata"
)
MODEL_SHA256 = "907743d98915c91a3906dfbf6e48b97598346698fe53aaa797e1a064ffcac913"
DPI = 600
SETTINGS = "text-only 600dpi sauvola"  # stored with the text: a change of settings re-reads pages


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float  # mean Tesseract word confidence, 0 to 100


def fetch_model(directory: Path = MODEL_DIR) -> Path:
    """Download the French model once, and refuse a file whose fingerprint is not the pinned one."""
    target = directory / "fra.traineddata"
    if not target.exists():
        directory.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
            target.write_bytes(response.read())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        target.unlink()
        raise RuntimeError(f"fra.traineddata has SHA-256 {digest}, expected {MODEL_SHA256}")
    return target


def engine_version() -> str:
    first_line = subprocess.run(
        ["tesseract", "--version"], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    return f"{first_line} fra best {MODEL_COMMIT[:7]} {SETTINGS}"


def text_from_tsv(tsv: str) -> OcrResult:
    """Rebuild the page text line by line from Tesseract's TSV, with the mean word confidence."""
    lines: dict[tuple[str, str, str], list[str]] = {}
    confidences = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 rows are words
            continue
        word = cols[11].strip()
        if not word:
            continue
        lines.setdefault((cols[2], cols[3], cols[4]), []).append(word)  # block, paragraph, line
        confidences.append(float(cols[10]))
    text = "\n".join(" ".join(words) for words in lines.values())
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResult(text, round(confidence, 1))


def _is_text_mask(image) -> bool:
    """A line of text drawn as an image: a few pixels of colour cut out by a 1-bit mask."""
    small = int(image.get("/Width", 0)) <= 4 and int(image.get("/Height", 0)) <= 4
    return small and ("/SMask" in image or bool(image.get("/ImageMask", False)))


def _drop_pictures(pdf: pikepdf.Pdf, owner) -> None:
    """Remove the drawing of every image that is not a text mask, in a page or a form XObject."""
    dictionary = owner.obj if isinstance(owner, pikepdf.Page) else owner
    xobjects = dictionary.get("/Resources", {}).get("/XObject", {})
    pictures = set()
    for name, xobject in xobjects.items():
        if xobject.get("/Subtype") == "/Form":
            _drop_pictures(pdf, xobject)
        elif xobject.get("/Subtype") == "/Image" and not _is_text_mask(xobject):
            pictures.add(str(name))
    if not pictures:
        return
    kept = [
        (operands, operator)
        for operands, operator in pikepdf.parse_content_stream(owner)
        if not (str(operator) == "Do" and str(operands[0]) in pictures)
    ]
    stream = pikepdf.unparse_content_stream(kept)
    if isinstance(owner, pikepdf.Page):
        owner.obj.Contents = pdf.make_stream(stream)  # one stream, even if the page had several
    else:
        owner.write(stream)


def text_only_copy(pdf: Path, page: int, target: Path) -> None:
    """Write `page` of `pdf` alone to `target`, without its pictures."""
    with pikepdf.open(pdf) as source, pikepdf.new() as copy:
        copy.pages.append(source.pages[page - 1])
        _drop_pictures(copy, copy.pages[0])
        copy.save(target)


def ocr_page(pdf: Path, page: int, model_dir: Path = MODEL_DIR) -> OcrResult:
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "page"
        single = Path(tmp) / "page.pdf"
        text_only_copy(pdf.resolve(), page, single)
        subprocess.run(
            ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-singlefile", str(single), str(image)],
            check=True,
            capture_output=True,
        )  # fmt: skip
        tsv = subprocess.run(
            [
                "tesseract",
                f"{image}.png",
                "-",
                "-l",
                "fra",
                "--oem",
                "1",
                "--psm",
                "3",
                "--dpi",
                str(DPI),
                "-c",
                "thresholding_method=2",  # Sauvola: local thresholds, robust to shaded boxes
                # TSV output set directly: the "tsv" config file lives in the system tessdata,
                # not in our model folder
                "-c",
                "tessedit_create_tsv=1",
                "-c",
                "tessedit_create_txt=0",
            ],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "TESSDATA_PREFIX": str(model_dir.resolve())},
        ).stdout
    return text_from_tsv(tsv)


def pdf_paths_by_sha(corpus: Path) -> dict[str, Path]:
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in sorted(corpus.rglob("*.pdf"))}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OCR the pages routed to OCR and store the text.")
    parser.add_argument(
        "corpus", type=Path, help="folder with the loaded PDFs (searched recursively)"
    )
    parser.add_argument("--workers", type=int, default=3, help="pages read in parallel")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    for tool in ("pdftoppm", "tesseract"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not found: install poppler and tesseract")
    fetch_model()
    engine = engine_version()
    paths = pdf_paths_by_sha(args.corpus)

    with connect() as conn:
        migrate(conn)
        todo = conn.execute(
            "SELECT d.id, d.sha256, d.title, p.page FROM pages p"
            " JOIN documents d ON d.id = p.document_id"
            " WHERE p.route = 'ocr' AND p.ocr_engine IS DISTINCT FROM %s"
            " AND d.retired_at IS NULL ORDER BY d.id, p.page",
            (engine,),
        ).fetchall()
        missing = {sha for _, sha, _, _ in todo if sha not in paths}
        if missing:
            log.warning("%d document(s) not found in %s, skipped", len(missing), args.corpus)
        jobs = [(doc, page, paths[sha]) for doc, sha, _, page in todo if sha in paths]
        log.info("%d page(s) to read", len(jobs))

        def work(job):
            doc, page, pdf = job
            try:
                return doc, page, ocr_page(pdf, page)
            except subprocess.CalledProcessError as error:
                # one unreadable page must not stop the run; it stays NULL and is retried next time
                log.warning("%s page %d: %s failed, skipped", pdf.name, page, error.cmd[0])
                return doc, page, None

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for done, (doc, page, result) in enumerate(pool.map(work, jobs), start=1):
                if result is None:
                    continue
                conn.execute(
                    "UPDATE pages SET ocr_text = %s, ocr_confidence = %s, ocr_engine = %s,"
                    " ocr_at = now() WHERE document_id = %s AND page = %s",
                    (result.text, result.confidence, engine, doc, page),
                )
                if done % 50 == 0:
                    log.info("%d / %d pages", done, len(jobs))
    log.info("done")


if __name__ == "__main__":
    main()
