#!/usr/bin/env python3
"""
db.py — SQLite connection helper + migration runner for the tennis dashboard.

USAGE
-----
    from db import connect, init_db

    # Apply any unapplied migrations (idempotent). Call once at process start.
    init_db()

    # Open a connection. WAL + foreign_keys + Row factory pre-configured.
    with connect() as conn:
        rows = conn.execute("SELECT mid, name FROM players WHERE tour='wta'").fetchall()

CLI
---
    python3 scripts/db.py init          # apply migrations to data/tennis.db
    python3 scripts/db.py status        # show schema version + row counts
    python3 scripts/db.py shell         # open sqlite3 CLI on the DB

MIGRATIONS
----------
Add a new file under `scripts/migrations/` named `NNN_description.sql` (NNN is
zero-padded). On the next `init_db()` call, any unapplied migrations are
executed in lexical order and recorded in the `_migrations` table.
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT       = Path(__file__).parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "tennis.db"
MIGRATIONS_DIR  = Path(__file__).parent / "migrations"


def connect(path: Path = DEFAULT_DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with project-standard PRAGMAs.

    - WAL journal mode: readers don't block the writer (lets the dashboard's
      future API serve queries while sync_matches.py writes).
    - Foreign keys ON: no SQLite default — must opt in.
    - Row factory: results behave like dicts (`row['name']`).
    """
    if read_only:
        # SQLite URI form lets us open read-only without surprising the user.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")  # WAL-safe + ~10× faster writes
    return conn


def init_db(path: Path = DEFAULT_DB_PATH, *, verbose: bool = True) -> sqlite3.Connection:
    """Apply any unapplied migrations. Returns an open connection."""
    conn = connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()

    applied = {row["version"] for row in conn.execute("SELECT version FROM _migrations")}
    available = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not available:
        raise RuntimeError(f"No migration files found in {MIGRATIONS_DIR}")

    pending = [f for f in available if f.stem not in applied]
    if not pending:
        if verbose:
            print(f"[db] up to date — {len(applied)} migration(s) applied")
        return conn

    for sql_file in pending:
        version = sql_file.stem
        sql     = sql_file.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO _migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(f"migration {version} failed: {e}") from e
        if verbose:
            print(f"[db] applied migration {version}")

    return conn


def status(path: Path = DEFAULT_DB_PATH) -> None:
    """Print schema version + row counts for top-level tables."""
    if not path.exists():
        print(f"[db] {path} does not exist — run `python3 scripts/db.py init` first")
        return
    conn = connect(path, read_only=True)
    print(f"[db] path: {path}  ({path.stat().st_size / 1024:.1f} KB)")
    print(f"[db] migrations applied:")
    for row in conn.execute("SELECT version, applied_at FROM _migrations ORDER BY version"):
        print(f"      · {row['version']:<30} ({row['applied_at']})")
    print(f"[db] row counts:")
    tables = ["players", "tournaments", "matches", "rankings_snapshots", "api_fetch_log"]
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            print(f"      · {t:<22} {n:>10,}")
        except sqlite3.OperationalError as e:
            print(f"      · {t:<22} (missing — {e})")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("cmd", choices=["init", "status", "shell"])
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = p.parse_args()

    if args.cmd == "init":
        init_db(args.db)
    elif args.cmd == "status":
        status(args.db)
    elif args.cmd == "shell":
        import os
        os.execvp("sqlite3", ["sqlite3", str(args.db)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
