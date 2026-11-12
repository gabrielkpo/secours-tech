"""The arithmetic of the `local` extra (e5 mean pooling, the cross-encoder's batching, BGE-M3's
max-sim), run with the real torch on tiny hand-made tensors, without downloading any model.

Skipped unless torch is installed (`uv sync --extra local`); the `Local extra` workflow runs it
weekly and on every pull request that changes the dependencies, so a torch or transformers
bump is exercised before it is merged.
"""

import pytest

torch = pytest.importorskip("torch")

from secours_tech.embed import E5Embedder  # noqa: E402
from secours_tech.rerank import CrossEncoder, MultiVector, _local  # noqa: E402


class Output:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def batch(ids, mask):
    return {"input_ids": torch.tensor(ids), "attention_mask": torch.tensor(mask)}


def test_the_local_extra_imports():
    loaded_torch, transformers = _local()
    assert loaded_torch is torch and transformers.__version__


def test_e5_mean_pools_the_real_tokens_and_normalises():
    embedder = object.__new__(E5Embedder)
    embedder.torch = torch
    seen = []

    def tokenizer(texts, **options):
        seen.extend(texts)
        return batch([[1, 2, 0]], [[1, 1, 0]])  # the third token is padding

    embedder.tokenizer = tokenizer
    hidden = torch.tensor([[[3.0, 0.0], [1.0, 0.0], [100.0, 100.0]]])
    embedder.model = lambda **inputs: Output(last_hidden_state=hidden)
    assert embedder.query("Couper le gaz ?") == pytest.approx([1.0, 0.0])  # mean (2, 0), unit
    assert seen == ["query: Couper le gaz ?"]


def test_the_cross_encoder_keeps_the_passages_in_order_across_batches(monkeypatch):
    monkeypatch.setattr("secours_tech.rerank.BATCH", 2)
    encoder = object.__new__(CrossEncoder)
    encoder.torch = torch

    def tokenizer(questions, passages, **options):
        return {"scores": torch.tensor([float(len(p)) for p in passages])}

    encoder.tokenizer = tokenizer
    encoder.model = lambda scores: Output(logits=scores.view(-1, 1))
    assert encoder.scores("?", ["a", "bbb", "cc"]) == [1.0, 3.0, 2.0]


def test_multi_vector_scores_each_question_token_by_its_best_match():
    scorer = object.__new__(MultiVector)
    scorer.torch = torch
    vectors = {  # first token (CLS) dropped by _vectors; then one row per token
        "q": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        "a": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.0]],  # matches only the first question token
        "b": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],  # matches both
    }

    def tokenizer(texts, **options):
        return {"rows": [vectors[t] for t in texts], "attention_mask": torch.ones(len(texts), 3)}

    scorer.tokenizer = tokenizer
    scorer.model = lambda rows, attention_mask: Output(last_hidden_state=torch.tensor(rows))
    scorer.colbert = torch.nn.Identity()
    assert scorer.scores("q", ["a", "b"]) == pytest.approx([0.5, 1.0])
