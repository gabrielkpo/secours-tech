"""OCR of the pages routed to `ocr`: Mistral OCR for the text that gets indexed, Tesseract as
a local checker.

Mistral OCR (`mistral-ocr-4-1`, pinned) reads the text that may be indexed (decision D5 revised on
16/10/2026: on the hand-checked samples 10 of 14 guides pass with it, 2 with Tesseract). Its
Markdown is turned into plain text. Tesseract stays for one job only, offline and without a key:
reading a page whose text layer looks garbled, so the loader can confirm it (`extract.py`); its
text is never indexed.

Both read the same input. Each page is copied with its pictures removed: the ministry's Word exports draw every line of
text as a tiny solid image carrying a 1-bit mask, while photos, drawings and box backgrounds are
ordinary images. Only the text is left (grey boxes no longer hide their text, drawings no longer
read as gibberish). The copy is rendered at 600 dpi with `pdftoppm` and read by Tesseract (LSTM
engine, Sauvola binarisation, `fra` model from tessdata_best pinned to one commit and checked by
SHA-256). Text (and Tesseract's mean word confidence) are stored on the page row with the engine
and its settings; a page read with another engine or other settings is read again. Settings chosen on a hand-checked sample and validated on another one
(`secours_tech.ocr_bench`). OCR text is not indexed here: a guide's OCR text is only
used once its sample passes the reliability check (`secours_tech.ocr_bench`).
"""

import argparse
import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from secours_tech.db import connect, migrate
from secours_tech.mistral import MistralError, api_key, post

log = logging.getLogger(__name__)

MODEL_DIR = Path("data/tessdata")
MODEL_COMMIT = "9ddc24e750eec0994223a9edc3fcb434a2244f3b"  # tesseract-ocr/tessdata_best
MODEL_URL = (
    f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{MODEL_COMMIT}/fra.traineddata"
)
MODEL_SHA256 = "907743d98915c91a3906dfbf6e48b97598346698fe53aaa797e1a064ffcac913"
DPI = 600
SETTINGS = "text-only 600dpi sauvola"  # stored with the text: a change of settings re-reads pages
MISTRAL_MODEL = "mistral-ocr-4-1"  # pinned: "mistral-ocr-latest" would move under our feet
MISTRAL_ENGINE = f"{MISTRAL_MODEL} text-only"


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None  # mean Tesseract word confidence, 0 to 100; None for Mistral


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
    """A line of text drawn as an image: a 1-bit stencil filled with the text colour, or a few
    pixels of colour cut out by a 1-bit soft mask (how the ministry's Word exports do it)."""
    if bool(image.get("/ImageMask", False)):
        return True
    small = int(image.get("/Width", 0)) <= 4 and int(image.get("/Height", 0)) <= 4
    return small and "/SMask" in image


def _drop_pictures(pdf: pikepdf.Pdf, owner, seen: set | None = None) -> None:
    """Remove the drawing of every image that is not a text mask, in a page or a form XObject.

    Each form is cleaned once, even when several parents share it or it refers to itself.
    """
    seen = set() if seen is None else seen
    dictionary = owner.obj if isinstance(owner, pikepdf.Page) else owner
    xobjects = dictionary.get("/Resources", {}).get("/XObject", {})
    pictures = set()
    for name, xobject in xobjects.items():
        if xobject.get("/Subtype") == "/Form":
            if xobject.objgen not in seen:
                seen.add(xobject.objgen)
                _drop_pictures(pdf, xobject, seen)
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


_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TAG = re.compile(r"<sup>(.*?)</sup>|<sub>(.*?)</sub>")
_TABLE_RULE = re.compile(r"^[\s|:-]+$")
_HEADING = re.compile(r"^\s*#+\s*")
_EMPHASIS = re.compile(r"\*\*|__|(?<!\w)\*(?=\S)|(?<=\S)\*(?!\w)")
_MATH = re.compile(r"\$([^$]*)\$")
_LATEX = re.compile(r"\\(?:mathrm|text|mathbf|textbf)\s*|[{}^_]")


def markdown_to_text(markdown: str) -> str:
    """Mistral OCR's Markdown as the plain text a reader sees on the page.

    Pictures are dropped (they hold no text here), table pipes and rules become spaces, heading
    marks and emphasis go, and superscripts ("accident³⁰", "m³", "$H_{2}$") become plain digits
    written inline, as in the PDF text layer.
    """
    text = _IMAGE.sub("", markdown.translate(_SUPERSCRIPTS))
    text = _TAG.sub(lambda m: m.group(1) or m.group(2), text)
    lines = []
    for line in text.splitlines():
        if line.strip() and _TABLE_RULE.fullmatch(line):
            continue
        line = _EMPHASIS.sub("", _HEADING.sub("", line).replace("|", " "))
        line = _MATH.sub(lambda m: _LATEX.sub("", m.group(1)).replace(r"\%", "%"), line)
        lines.append(" ".join(line.split()))
    return "\n".join(lines).strip()


def mistral_ocr_page(pdf: Path, page: int, key: str) -> OcrResult:
    """Read one page with Mistral OCR, sent as a one-page PDF without its pictures."""
    with tempfile.TemporaryDirectory() as tmp:
        single = Path(tmp) / "page.pdf"
        text_only_copy(pdf.resolve(), page, single)
        data = base64.b64encode(single.read_bytes()).decode()
    answer = post(
        "ocr",
        {
            "model": MISTRAL_MODEL,
            "document": {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{data}",
            },
            "include_image_base64": False,
        },
        key,
    )
    markdown = "\n".join(p.get("markdown", "") for p in answer.get("pages", []))
    return OcrResult(markdown_to_text(markdown), None)


def pdf_paths_by_sha(corpus: Path) -> dict[str, Path]:
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p for p in sorted(corpus.rglob("*.pdf"))}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OCR the pages routed to OCR and store the text.")
    parser.add_argument(
        "corpus", type=Path, help="folder with the loaded PDFs (searched recursively)"
    )
    parser.add_argument("--workers", type=int, default=2, help="pages read in parallel")
    parser.add_argument(
        "--engine",
        choices=("mistral", "tesseract"),
        default="mistral",
        help="mistral for the indexed text (D5); tesseract to compare, offline",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.engine == "mistral":
        try:
            key = api_key()
        except MistralError as error:
            raise SystemExit(str(error)) from None
        engine = MISTRAL_ENGINE

        def read(pdf: Path, page: int) -> OcrResult:
            return mistral_ocr_page(pdf, page, key)

    else:
        for tool in ("pdftoppm", "tesseract"):
            if shutil.which(tool) is None:
                raise SystemExit(f"{tool} not found: install poppler and tesseract")
        fetch_model()
        engine = engine_version()
        read = ocr_page
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
        # indexed OCR text was checked against the text being replaced: it goes until
        # `ocr_bench index` checks the new text again
        stale = conn.execute(
            "DELETE FROM chunks WHERE extraction = 'ocr' AND document_id = ANY(%s)",
            (sorted({doc for doc, _, _ in jobs}),),
        ).rowcount
        if stale:
            log.warning("%d indexed OCR chunk(s) withdrawn: run ocr_bench index again", stale)

        def work(job):
            doc, page, pdf = job
            try:
                return doc, page, read(pdf, page)
            except (subprocess.CalledProcessError, MistralError) as error:
                # one unreadable page must not stop the run: it keeps its previous text and engine,
                # so the next run reads it again
                log.warning("%s page %d: skipped (%s)", pdf.name, page, error)
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
