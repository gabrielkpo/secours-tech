"""Minimal Mistral API client: the key, one JSON call, retries on rate limits.

The key is read from the environment (`MISTRAL_API_KEY`), or from `.env` in the working
directory when the variable is not set. It is sent only to api.mistral.ai and never logged.
The standard library is enough here; the official SDK would add a dependency for one call.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.mistral.ai/v1"
RETRIES = 5


class MistralError(RuntimeError):
    pass


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
    """POST JSON to the API; waits and retries when the rate limit (429) or a 5xx is hit."""
    request = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            if retryable and attempt < RETRIES - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            # the body explains the refusal; the request (and its key) is never echoed
            raise MistralError(f"{path}: HTTP {error.code} {error.read()[:300]!r}") from None
    raise MistralError(f"{path}: no answer after {RETRIES} attempts")
