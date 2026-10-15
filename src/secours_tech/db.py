"""Connect to Postgres and apply the SQL migrations shipped with the package."""

import logging
import os
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set (e.g. postgresql:///secours_tech)")
    # autocommit: nothing is pending by accident; writes that belong together are wrapped
    # in an explicit `with conn.transaction():` block
    return psycopg.connect(url, autocommit=True)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply, in name order, every migration file not applied yet. Returns the applied names.

    Each file runs in its own transaction with its bookkeeping row, so a failing file
    leaves the database as it was before that file.
    """
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
    done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        log.info("applied migration %s", path.name)
        applied.append(path.name)
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connect() as conn:
        applied = migrate(conn)
    log.info("%d migration(s) applied", len(applied))


if __name__ == "__main__":
    main()
