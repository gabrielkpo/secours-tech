"""The docs stay true to the code: every decision number cited is defined."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_every_decision_number_cited_is_defined():
    defined = set(re.findall(r"^\| (D\d+) \|", (ROOT / "docs/decisions.md").read_text(), re.M))
    files = [ROOT / "README.md", *(ROOT / "docs").glob("*.md"), *(ROOT / "src").rglob("*.py")]
    cited = {d for f in files for d in re.findall(r"\bD\d{1,2}\b", f.read_text(encoding="utf-8"))}
    assert cited - defined == set()
