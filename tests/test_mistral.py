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

    monkeypatch.setattr(mistral._OPENER, "open", fake_urlopen)
    monkeypatch.setattr(mistral.time, "sleep", lambda s: None)
    assert post("ocr", {"model": "m"}, "secret-key") == {"ok": True}
    assert len(calls) == 2
    assert calls[0].get_header("Authorization") == "Bearer secret-key"

    def refused(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"unauthorized"))

    monkeypatch.setattr(mistral._OPENER, "open", refused)
    with pytest.raises(MistralError) as error:
        post("ocr", {}, "secret-key")
    assert "401" in str(error.value) and "secret-key" not in str(error.value)


def test_post_retries_network_errors_then_reports_them(monkeypatch):
    attempts = []

    def offline(request, timeout):
        attempts.append(1)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(mistral._OPENER, "open", offline)
    monkeypatch.setattr(mistral.time, "sleep", lambda s: None)
    with pytest.raises(MistralError, match="network error"):
        post("ocr", {}, "k")
    assert len(attempts) == mistral.RETRIES


def test_a_redirect_is_never_followed_with_the_key(monkeypatch):
    handler = mistral._NoRedirect()
    request = mistral.urllib.request.Request("https://api.mistral.ai/v1/ocr")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere/") is None

    def redirected(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(mistral._OPENER, "open", redirected)
    with pytest.raises(MistralError, match="redirect refused"):
        post("ocr", {}, "secret-key")


def test_retry_after_is_honoured_and_a_truncated_answer_is_retried(monkeypatch):
    waits, calls = [], []

    def flaky(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "busy", {"Retry-After": "7"}, io.BytesIO(b"")
            )
        if len(calls) == 2:
            return _Answer(b'{"truncated')
        return _Answer(b'{"ok": true}')

    monkeypatch.setattr(mistral._OPENER, "open", flaky)
    monkeypatch.setattr(mistral.time, "sleep", waits.append)
    assert post("ocr", {}, "k") == {"ok": True}
    assert 7 <= waits[0] < 8 and len(waits) == 2
