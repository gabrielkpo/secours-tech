"""Run records: every retrieval score `bench run` prints is also written to `eval/runs/` and
versioned, so a published number can be traced to the code, the index and the cases it came
from.

A record holds scores only, never doctrine text: the commit, the setup (retriever, models and
their revisions, candidates, fusion constant), a fingerprint of the indexed chunks and of the
cases of the split, the scores per category, the seconds per question, and the rank of each
expected page per case id. `bench report` turns records into the Markdown tables of the README.
"""

import hashlib
import json
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from secours_tech.tools import run_tool


@dataclass(frozen=True)
class Setup:
    split: str
    retriever: str  # fulltext, vector or hybrid
    model: str | None  # embedding model key (mistral, e5), None for full text
    rerank: str  # none, bge or m3

    @property
    def label(self) -> str:
        return "-".join([self.split, self.retriever, self.model or "none", self.rerank])


def _models(setup: Setup) -> dict:
    from secours_tech.bench import CANDIDATES, RRF_K

    models: dict = {"candidates": CANDIDATES}
    if setup.retriever == "hybrid":
        models["rrf_k"] = RRF_K
    if setup.model == "mistral":
        from secours_tech.embed import MISTRAL_MODEL

        models["embedding"] = {"model": MISTRAL_MODEL, "revision": None}
    elif setup.model == "e5":
        from secours_tech.embed import E5_MODEL, E5_REVISION

        models["embedding"] = {"model": E5_MODEL, "revision": E5_REVISION}
    if setup.rerank != "none":
        from secours_tech import rerank

        model, revision = {
            "bge": (rerank.BGE_RERANKER, rerank.BGE_RERANKER_REVISION),
            "m3": (rerank.BGE_M3, rerank.BGE_M3_REVISION),
        }[setup.rerank]
        models["reranker"] = {"model": model, "revision": revision}
    return models


def _code() -> dict:
    """The commit the run used, and whether the working tree differed from it."""
    try:
        commit = run_tool("git", "rev-parse", "HEAD", timeout=30).strip()
        changes = run_tool("git", "status", "--porcelain", "--untracked-files=no", timeout=30)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": bool(changes.strip())}


def index_fingerprint(conn: psycopg.Connection) -> dict:
    """What was searched: the SHA-256 of the sorted input hashes of the current official chunks
    (any change of text, context or chunking changes it), their count by extraction method, and
    the OCR engines of the indexed OCR pages."""
    hashes = conn.execute(
        "SELECT c.input_sha256 FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE d.retired_at IS NULL AND d.source = 'web' ORDER BY c.input_sha256"
    ).fetchall()
    digest = hashlib.sha256("\n".join(h for (h,) in hashes).encode()).hexdigest()
    counts: dict[str, int] = dict(
        conn.execute(
            "SELECT c.extraction, count(*) FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.retired_at IS NULL AND d.source = 'web' GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    engines = [
        engine
        for (engine,) in conn.execute(
            "SELECT DISTINCT p.ocr_engine FROM pages p JOIN documents d ON d.id = p.document_id"
            " WHERE d.retired_at IS NULL AND p.ocr_engine IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]
    return {"sha256": digest, "chunks": counts, "ocr_engines": engines}


def record(
    conn: psycopg.Connection,
    setup: Setup,
    cases_sha256: str,
    ranks: dict[str, list[int | None]],
    scores: dict[str, dict[str, float]],
    seconds: float,
) -> dict:
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": _code(),
        "setup": asdict(setup),
        "models": _models(setup),
        "index": index_fingerprint(conn),
        "cases": {"split": setup.split, "answered": len(ranks), "sha256": cases_sha256},
        "machine": f"{platform.system()} {platform.machine()}",
        "seconds_per_question": round(seconds, 2),
        "scores": {
            group: {k: round(v, 4) for k, v in row.items()} for group, row in scores.items()
        },
        "ranks": dict(sorted(ranks.items())),
    }


def write(folder: Path, data: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = data["date"].replace("-", "").replace(":", "").split("+")[0]
    base = f"{stamp}Z-{Setup(**data['setup']).label}"
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    for attempt in range(1, 100):
        path = folder / (f"{base}.json" if attempt == 1 else f"{base}-{attempt}.json")
        try:
            with path.open("x", encoding="utf-8") as out:  # a record is never overwritten
                out.write(text)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"too many records named {base}")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def earlier_final(folder: Path, setup: Setup, cases_sha256: str) -> Path | None:
    """A record of the same test cases with the same setup: the test split is scored once (a
    new freeze of the test split is a new set, scored once in turn)."""
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        data = load(path)
        if data["setup"] == asdict(setup) and data["cases"]["sha256"] == cases_sha256:
            return path
    return None


def table(paths: Sequence[Path], ks: Sequence[int] = (5, 20)) -> str:
    """The README table of a set of records: one row per run, the scores of all its cases."""
    header = ["Split", "Retriever", "Embedding", "Reranker", "n"]
    for k in ks:
        header += [f"hit@{k}", f"recall@{k}"]
    header += ["MRR", "s / question", "Record"]
    rows = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for path in paths:
        data = load(path)
        setup, scores = data["setup"], data["scores"]["all"]
        models = data["models"]
        cells = [
            setup["split"],
            setup["retriever"],
            f"`{models['embedding']['model']}`" if "embedding" in models else "-",
            f"`{models['reranker']['model']}`" if "reranker" in models else "-",
            str(int(scores["n"])),
        ]
        for k in ks:
            cells += [f"{scores[f'hit@{k}']:.2f}", f"{scores[f'recall@{k}']:.2f}"]
        cells += [f"{scores['mrr']:.2f}", f"{data['seconds_per_question']:.1f}", f"`{path.name}`"]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)
