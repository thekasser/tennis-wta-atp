#!/usr/bin/env python3
"""
restore_db.py — Rehydrate data/tennis.db from the snapshot.

The snapshot lives at:
    data/tennis.db.gz                   (gitignored local cache — used if present)
    s3://<R2_BUCKET>/tennis.db.gz       (canonical store — downloaded if local missing)

If neither exists, the script no-ops with a friendly message — this is
expected on the very first cold-start before the first snapshot has been
uploaded to R2.

USAGE
-----
    python3 scripts/restore_db.py            # defaults
    python3 scripts/restore_db.py --force    # overwrite an existing DB
    python3 scripts/restore_db.py --no-download  # require a local .gz; never hit R2

Used at the start of CI runs:
    1. checkout repo (no data/tennis.db.gz — it's gitignored)
    2. python3 scripts/restore_db.py         ← downloads from R2 then rehydrates
    3. python3 scripts/sync_rankings.py
    4. python3 scripts/sync_matches.py
    5. python3 scripts/materialize.py
    6. python3 scripts/snapshot_db.py        ← writes .gz + uploads to R2
    7. git commit data/snapshot_summary.txt + data/*.js (NOT the .gz)
"""
from __future__ import annotations
import argparse
import gzip
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DEFAULT_DB_PATH, REPO_ROOT
import r2


SNAPSHOT_GZ = REPO_ROOT / "data" / "tennis.db.gz"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing DB")
    p.add_argument("--no-download", action="store_true",
                   help="require a local .gz; never fall back to R2 download")
    args = p.parse_args()

    if args.db.exists() and not args.force:
        print(f"[restore] {args.db.relative_to(REPO_ROOT)} already exists "
              f"({args.db.stat().st_size:,} bytes). Use --force to overwrite.")
        return 0

    # Pull the snapshot from R2 when there's no local cache. The .gz file
    # is gitignored (private — contains raw Matchstat API payloads), so on
    # a fresh checkout (e.g. GHA's actions/checkout) it won't be present.
    if not SNAPSHOT_GZ.exists() and not args.no_download:
        if r2.is_configured():
            try:
                r2.download(SNAPSHOT_GZ)
            except Exception as e:
                print(f"[restore] R2 download failed: {e}", file=sys.stderr)
                return 1
        else:
            print(f"[restore] {SNAPSHOT_GZ} not found and R2 env vars unset — "
                  "nothing to restore. This is expected on the very first run "
                  "(no snapshot yet).")
            return 0

    if not SNAPSHOT_GZ.exists():
        print(f"[restore] {SNAPSHOT_GZ} not found (after R2 attempt) — "
              "nothing to restore.")
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
