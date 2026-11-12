"""Minimal Mistral API client: the key, one JSON call, retries on rate limits.

The key is read from the environment (`MISTRAL_API_KEY`), or from `.env` in the working
directory when the variable is not set. It is sent only to api.mistral.ai and never logged: the
client follows no redirect, since a redirected request would carry the key to another host.
The standard library is enough here; the official SDK would add a dependency for one call.
"""

import http.client
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.mistral.ai/v1"
RETRIES = 5


class MistralError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the 3xx is raised as an HTTPError instead of being followed


_OPENER = urllib.request.build_opener(_NoRedirect)


def _wait(attempt: int, retry_after: str | None = None) -> float:
    """Seconds before the next attempt: the server's Retry-After when it gives a number of
    seconds, else exponential backoff; plus up to a second of jitter."""
    if retry_after and re.fullmatch(r"[0-9]+", retry_after.strip()):
        return min(int(retry_after), 60) + random.random()  # noqa: S311 - jitter, not a secret
    return 2 ** (attempt + 1) + random.random()  # noqa: S311


def api_key(env_file: Path = Path(".env")) -> str:
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("MISTRAL_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise MistralError("MISTRAL_API_KEY is not set (environment or .env)")
    return key


def post(path: str, payload: dict, key: str, timeout: int = 120) -> dict:
    """POST JSON to the API; waits and retries on the rate limit (429), a 5xx or a network error."""
    request = urllib.request.Request(  # noqa: S310 - API is a constant https URL
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(RETRIES):
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise MistralError(f"{path}: HTTP {error.code} redirect refused") from None
            retryable = error.code == 429 or error.code >= 500
            if retryable and attempt < RETRIES - 1:
                time.sleep(_wait(attempt, error.headers.get("Retry-After")))
                continue
            # the body explains the refusal; the request (and its key) is never echoed
            raise MistralError(f"{path}: HTTP {error.code} {error.read()[:300]!r}") from None
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.HTTPException,  # a truncated body (IncompleteRead)
            json.JSONDecodeError,
        ) as error:
            if attempt < RETRIES - 1:
                time.sleep(_wait(attempt))
                continue
            raise MistralError(f"{path}: network error {type(error).__name__}") from None
    raise MistralError(f"{path}: no answer after {RETRIES} attempts")
