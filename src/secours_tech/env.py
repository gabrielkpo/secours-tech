"""Settings read from the environment, or from `.env` in the working directory when unset.

`.env` holds the local values (the Mistral key, the database URL); it is ignored by Git and
`.env.example` lists what it may hold.
"""

import os
from pathlib import Path


def setting(name: str, env_file: Path = Path(".env")) -> str:
    """The value of `name`: the environment first, then a `NAME=value` line of `.env`; "" when
    neither sets it."""
    value = os.environ.get(name, "").strip()
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip().strip("\"'")
    return value
