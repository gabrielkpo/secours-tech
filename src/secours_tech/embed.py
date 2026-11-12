"""Embeddings of the chunks (J3): `mistral-embed` through the API, `multilingual-e5-base` locally.

What is embedded is the chunk's context header followed by its text (D7: "guide › chapter ›
section", then the passage). Vectors are stored per model and per hash of that input
(`chunks.input_sha256`, computed by Postgres), so rebuilding the chunks keeps every vector whose
input did not change and only new inputs are sent to a model.

`multilingual-e5-base` is the local reference (decision of 16/10/2026, scoping §6.4): it shows
what the Mistral embedding brings. It needs the optional `local` dependencies
(`uv sync --extra local`) and downloads the model from Hugging Face on first use.
"""

import argparse
import logging
from collections.abc import Sequence
from typing import Protocol

from secours_tech.mistral import api_key, post

log = logging.getLogger(__name__)

MISTRAL_MODEL = "mistral-embed-2312"  # pinned: the alias "mistral-embed" may move
E5_MODEL = "intfloat/multilingual-e5-base"
E5_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"  # pinned commit (Hugging Face)
BATCH = 32  # ~32 x 250 tokens per request, well under the API limit


class Embedder(Protocol):
    name: str

    def passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class MistralEmbedder:
    name = MISTRAL_MODEL

    def __init__(self, key: str | None = None):
        self.key = key or api_key()

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        answer = post("embeddings", {"model": MISTRAL_MODEL, "input": list(texts)}, self.key)
        rows = sorted(answer["data"], key=lambda row: row["index"])
        if len(rows) != len(texts):
            raise RuntimeError(f"{len(texts)} texts sent, {len(rows)} embeddings received")
        return [row["embedding"] for row in rows]

    def passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts)

    def query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class E5Embedder:
    """e5 expects "passage: " and "query: " prefixes, mean pooling and L2 normalisation."""

    name = E5_MODEL

    def __init__(self):
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError:
            raise SystemExit("e5 needs the local extra: uv sync --extra local") from None
        self.torch = torch
        # pinned revision, safetensors weights only (no pickle), no remote code
        self.tokenizer = AutoTokenizer.from_pretrained(E5_MODEL, revision=E5_REVISION)
        self.model = AutoModel.from_pretrained(
            E5_MODEL, revision=E5_REVISION, use_safetensors=True
        ).eval()

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        torch = self.torch
        batch = self.tokenizer(
            list(texts), max_length=512, padding=True, truncation=True, return_tensors="pt"
        )
        with torch.no_grad():
            hidden = self.model(**batch).last_hidden_state
        mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        mean = (hidden * mask).sum(dim=1) / mask.sum(dim=1)
        return torch.nn.functional.normalize(mean, dim=1).tolist()

    def passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed([f"passage: {text}" for text in texts])

    def query(self, text: str) -> list[float]:
        return self._embed([f"query: {text}"])[0]


EMBEDDERS: dict[str, type[MistralEmbedder] | type[E5Embedder]] = {
    "mistral": MistralEmbedder,
    "e5": E5Embedder,
}


def as_vector(values: Sequence[float]) -> str:
    """pgvector's text form, cast with ::vector in SQL."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def missing_inputs(conn, model: str) -> list[tuple[str, str]]:
    """(hash, input) of the current chunks that have no vector for `model` yet."""
    return conn.execute(
        """
        SELECT DISTINCT c.input_sha256, chunk_input(c.context, c.text)
        FROM chunks c JOIN documents d ON d.id = c.document_id
        WHERE d.retired_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM embeddings e
                          WHERE e.model = %s AND e.input_sha256 = c.input_sha256)
        ORDER BY c.input_sha256
        """,
        (model,),
    ).fetchall()


def embed_corpus(conn, embedder: Embedder, batch: int = BATCH, limit: int | None = None) -> int:
    """Embed every chunk input still missing for this model (at most `limit`); each batch is
    stored as it comes, so an interrupted run resumes where it stopped."""
    todo = missing_inputs(conn, embedder.name)[:limit]
    for start in range(0, len(todo), batch):
        part = todo[start : start + batch]
        vectors = embedder.passages([text for _, text in part])
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embeddings (model, input_sha256, embedding)"
                " VALUES (%s, %s, %s::vector) ON CONFLICT DO NOTHING",
                [
                    (embedder.name, sha, as_vector(v))
                    for (sha, _), v in zip(part, vectors, strict=True)
                ],
            )
        log.info("%s: %d / %d inputs", embedder.name, min(start + batch, len(todo)), len(todo))
    return len(todo)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Embed the chunks still missing a vector.")
    parser.add_argument("--model", choices=sorted(EMBEDDERS), required=True)
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument(
        "--dry-run", action="store_true", help="count the inputs a run would send, send none"
    )
    parser.add_argument("--limit", type=int, help="embed at most this many inputs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from secours_tech.db import connect, migrate

    model = EMBEDDERS[args.model]
    with connect() as conn:
        migrate(conn)
        if args.dry_run:
            todo = missing_inputs(conn, model.name)[: args.limit]
            characters = sum(len(text) for _, text in todo)
            # about 4 characters per token in French: enough to see what a paid run would cost
            print(f"{model.name}: {len(todo)} inputs, about {characters // 4} tokens, none sent")
            return
        done = embed_corpus(conn, model(), args.batch, args.limit)
    print(f"{model.name}: {done} inputs embedded")


if __name__ == "__main__":
    main()
