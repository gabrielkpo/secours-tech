"""Reranking of the first retrieved chunks (J3, D11 and D20), with two local models.

- `bge-reranker-v2-m3` (D11): a cross-encoder that reads the question and the chunk together and
  gives one relevance score.
- BGE-M3 multi-vector (D20, late interaction): one vector per token for the question and for the
  chunk; each question token keeps its best match in the chunk, and the score is their mean. Token
  matching suits a doctrine full of acronyms, form codes and figures that a single vector dilutes.

Both rerank the first 50 chunks of a retriever; nothing is stored. They need the optional
`local` extra and download their model from Hugging Face on first use, pinned to one commit.
BGE-M3 only publishes pickle weights: they are loaded with torch's `weights_only` unpickler,
which accepts tensors and nothing else.
"""

import os
from collections.abc import Sequence
from typing import Protocol

BGE_RERANKER = "BAAI/bge-reranker-v2-m3"
BGE_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
BGE_M3 = "BAAI/bge-m3"
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MAX_TOKENS = 512
BATCH = 8


class Reranker(Protocol):
    name: str

    def scores(self, question: str, passages: Sequence[str]) -> list[float]: ...


def _local():
    try:
        import torch
        import transformers
    except ImportError:
        raise SystemExit("reranking needs the local extra: uv sync --extra local") from None
    return torch, transformers


class CrossEncoder:
    name = BGE_RERANKER

    def __init__(self):
        self.torch, transformers = _local()
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            BGE_RERANKER, revision=BGE_RERANKER_REVISION
        )
        self.model = transformers.AutoModelForSequenceClassification.from_pretrained(
            BGE_RERANKER, revision=BGE_RERANKER_REVISION, use_safetensors=True
        ).eval()

    def scores(self, question: str, passages: Sequence[str]) -> list[float]:
        out: list[float] = []
        for start in range(0, len(passages), BATCH):
            part = list(passages[start : start + BATCH])
            batch = self.tokenizer(
                [question] * len(part),
                part,
                padding=True,
                truncation=True,
                max_length=MAX_TOKENS,
                return_tensors="pt",
            )
            with self.torch.no_grad():
                out += self.model(**batch).logits.view(-1).float().tolist()
        return out


class MultiVector:
    """BGE-M3's ColBERT head: a linear layer over the token states (the first token excluded),
    L2-normalised; score = mean over question tokens of the best dot product in the chunk."""

    name = f"{BGE_M3} multi-vector"

    def __init__(self):
        # the pinned revision has no safetensors file: without this, transformers starts a
        # background request asking the Hub to convert the third-party repository
        os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
        self.torch, transformers = _local()
        from huggingface_hub import hf_hub_download

        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            BGE_M3, revision=BGE_M3_REVISION
        )
        self.model = transformers.AutoModel.from_pretrained(BGE_M3, revision=BGE_M3_REVISION).eval()
        head = self.torch.load(
            hf_hub_download(BGE_M3, "colbert_linear.pt", revision=BGE_M3_REVISION),
            map_location="cpu",
            weights_only=True,
        )
        size = self.model.config.hidden_size
        self.colbert = self.torch.nn.Linear(size, head["weight"].shape[0])
        self.colbert.load_state_dict(head)

    def _vectors(self, texts: Sequence[str]):
        torch = self.torch
        batch = self.tokenizer(
            list(texts), padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt"
        )
        with torch.no_grad():
            states = self.model(**batch).last_hidden_state
            vectors = self.colbert(states[:, 1:])
        mask = batch["attention_mask"][:, 1:]
        vectors = torch.nn.functional.normalize(vectors, dim=-1) * mask.unsqueeze(-1)
        return vectors, mask

    def scores(self, question: str, passages: Sequence[str]) -> list[float]:
        torch = self.torch
        query, query_mask = self._vectors([question])
        out: list[float] = []
        for start in range(0, len(passages), BATCH):
            docs, _ = self._vectors(passages[start : start + BATCH])
            # (docs, question tokens, doc tokens): best match per question token, then the mean
            sims = torch.einsum("qd,ntd->nqt", query[0], docs)
            best = sims.max(dim=2).values * query_mask[0]
            out += (best.sum(dim=1) / query_mask[0].sum()).tolist()
        return out


RERANKERS = {"bge": CrossEncoder, "m3": MultiVector}


def rerank(reranker: Reranker, question: str, chunks: Sequence[tuple], texts: Sequence[str]):
    """The chunks reordered by the reranker's scores (stable for equal scores)."""
    scored = reranker.scores(question, texts)
    order = sorted(range(len(chunks)), key=lambda i: -scored[i])
    return [chunks[i] for i in order]
