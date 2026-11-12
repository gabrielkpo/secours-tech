"""One way to run the external tools (poppler's pdftotext, pdfimages, pdftoppm; tesseract; git).

Every call has a timeout, so a malformed PDF cannot hang a run (`subprocess.TimeoutExpired` is a
`SubprocessError`, which callers already treat as a failed page), reads its output as UTF-8
whatever the machine's locale (the route of a page must not depend on the machine), and says
which package to install when the tool is missing.
"""

import shutil
import subprocess

TIMEOUT = 300  # seconds; a whole guide reads in a few seconds, a 600 dpi OCR page in under 30
INSTALL = {
    "pdftotext": "poppler (brew install poppler, or apt-get install poppler-utils)",
    "pdfimages": "poppler (brew install poppler, or apt-get install poppler-utils)",
    "pdftoppm": "poppler (brew install poppler, or apt-get install poppler-utils)",
    "tesseract": "tesseract (brew install tesseract, or apt-get install tesseract-ocr)",
}


def run_tool(*command: str, timeout: int = TIMEOUT, env: dict[str, str] | None = None) -> str:
    """The standard output of a tool, decoded as UTF-8; raises CalledProcessError on failure and
    TimeoutExpired past `timeout` seconds."""
    if shutil.which(command[0]) is None:
        raise RuntimeError(f"{command[0]} not found: install {INSTALL.get(command[0], command[0])}")
    return subprocess.run(
        command, check=True, capture_output=True, encoding="utf-8", timeout=timeout, env=env
    ).stdout
