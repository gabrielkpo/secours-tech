import io
import json
import urllib.error

import pytest

from secours_tech import mistral
from secours_tech.mistral import MistralError, api_key, post


def test_key_comes_from_the_environment_first(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MISTRAL_API_KEY=from-file\n")
    monkeypatch.setenv("MISTRAL_API_KEY", "from-env")
    assert api_key(env) == "from-env"
    monkeypatch.delenv("MISTRAL_API_KEY")
    assert api_key(env) == "from-file"
    env.write_text("MISTRAL_API_KEY=\n")
    with pytest.raises(MistralError, match="not set"):
        api_key(env)


class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_retries_on_rate_limit_and_never_echoes_the_key(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "busy", {}, io.BytesIO(b"slow down")
            )
        return _Answer(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(mistral.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mistral.time, "sleep", lambda s: None)
    assert post("ocr", {"model": "m"}, "secret-key") == {"ok": True}
    assert len(calls) == 2
    assert calls[0].get_header("Authorization") == "Bearer secret-key"

    def refused(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"unauthorized"))

    monkeypatch.setattr(mistral.urllib.request, "urlopen", refused)
    with pytest.raises(MistralError) as error:
        post("ocr", {}, "secret-key")
    assert "401" in str(error.value) and "secret-key" not in str(error.value)


def test_post_retries_network_errors_then_reports_them(monkeypatch):
    attempts = []

    def offline(request, timeout):
        attempts.append(1)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(mistral.urllib.request, "urlopen", offline)
    monkeypatch.setattr(mistral.time, "sleep", lambda s: None)
    with pytest.raises(MistralError, match="network error"):
        post("ocr", {}, "k")
    assert len(attempts) == mistral.RETRIES
