#!/usr/bin/env python3
"""
restore_db.py — Rehydrate data/tennis.db from data/tennis.db.gz.

USAGE
-----
    python3 scripts/restore_db.py            # defaults
    python3 scripts/restore_db.py --force    # overwrite an existing DB

Used at the start of CI runs:
    1. checkout repo (gets data/tennis.db.gz)
    2. python3 scripts/restore_db.py         ← rebuilds data/tennis.db locally
    3. python3 scripts/sync_rankings.py
    4. python3 scripts/sync_matches.py
    5. python3 scripts/materialize.py
    6. python3 scripts/snapshot_db.py        ← updates data/tennis.db.gz
    7. git commit data/tennis.db.gz + data/snapshot_summary.txt + data/*.js

If the snapshot is missing OR the local DB is already populated AND
--force is not given, this script no-ops with a friendly message.
"""
from __future__ import annotations
import argparse
import gzip
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DEFAULT_DB_PATH, REPO_ROOT


SNAPSHOT_GZ = REPO_ROOT / "data" / "tennis.db.gz"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing DB")
    args = p.parse_args()

    if not SNAPSHOT_GZ.exists():
        print(f"[restore] {SNAPSHOT_GZ} not found — nothing to restore. "
              f"This is expected on the very first run (no snapshot yet).")
        return 0

    if args.db.exists() and not args.force:
        print(f"[restore] {args.db.relative_to(REPO_ROOT)} already exists "
              f"({args.db.stat().st_size:,} bytes). Use --force to overwrite.")
        return 0

    if args.db.exists():
        args.db.unlink()

    print(f"[restore] rehydrating from {SNAPSHOT_GZ.relative_to(REPO_ROOT)} "
          f"({SNAPSHOT_GZ.stat().st_size:,} bytes)")
    args.db.parent.mkdir(parents=True, exist_ok=True)

    with gzip.open(SNAPSHOT_GZ, "rb") as f:
        sql = f.read()
    print(f"[restore] decompressed: {len(sql):,} bytes of SQL")

    # Pipe to sqlite3 to recreate the DB.
    proc = subprocess.run(
        ["sqlite3", str(args.db)],
        input=sql, check=False, capture_output=True,
    )
    if proc.returncode != 0:
        print(f"[restore] sqlite3 returned {proc.returncode}", file=sys.stderr)
        print(proc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return proc.returncode

    print(f"[restore] wrote {args.db.relative_to(REPO_ROOT)} "
          f"({args.db.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
