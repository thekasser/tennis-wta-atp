#!/usr/bin/env python3
"""
snapshot_db.py — Compress data/tennis.db into a git-committable snapshot.

Outputs:
    data/tennis.db.gz       gzipped sqlite3 .dump (binary-friendly)
    data/snapshot_summary.txt human-readable manifest (so PR diffs are useful)

USAGE
-----
    python3 scripts/snapshot_db.py            # write both files
    python3 scripts/snapshot_db.py --dry-run  # print what would be written

Used by the daily CI job after sync_matches + materialize. The committed
.gz file is what `restore_db.py` rehydrates on the next run.
"""
from __future__ import annotations
import argparse
import gzip
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DEFAULT_DB_PATH, REPO_ROOT, connect


SNAPSHOT_GZ = REPO_ROOT / "data" / "tennis.db.gz"
SUMMARY_TXT = REPO_ROOT / "data" / "snapshot_summary.txt"


def dump_sql(db_path: Path) -> bytes:
    """Run `sqlite3 <db> .dump` and return its UTF-8 bytes."""
    result = subprocess.run(
        ["sqlite3", str(db_path), ".dump"],
        check=True, capture_output=True,
    )
    return result.stdout


def write_summary(db_path: Path, gz_path: Path, summary_path: Path,
                  *, dry_run: bool = False) -> str:
    """Build a human-readable summary so PR diffs show what actually changed."""
    conn = connect(db_path, read_only=True)
    counts = {}
    for table in ("players", "tournaments", "matches", "rankings_snapshots", "api_fetch_log"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # latest match per active tournament
    actives = list(conn.execute("""
        SELECT t.id, t.name, MAX(m.date) AS latest, COUNT(m.id) AS n
        FROM tournaments t
        LEFT JOIN matches m ON m.tournament_id = t.id
        WHERE t.active = 1
        GROUP BY t.id
    """))
    # top 5 latest matches across whole DB
    latest = list(conn.execute("""
        SELECT date, round, tournament_name, p1_id, p2_id, winner_id
        FROM matches
        ORDER BY date DESC, round DESC LIMIT 5
    """))
    # rankings snapshot date
    snap_date = conn.execute(
        "SELECT MAX(snapshot_date) FROM rankings_snapshots"
    ).fetchone()[0]

    lines = [
        f"# Tennis DB snapshot summary",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        f"# Source DB: {db_path.relative_to(REPO_ROOT)}",
        f"# Compressed: {gz_path.relative_to(REPO_ROOT)}",
        f"",
        f"## Row counts",
    ]
    for table, n in counts.items():
        lines.append(f"  {table:<22} {n:>10,}")

    lines += [
        f"",
        f"## Latest rankings snapshot: {snap_date}",
        f"",
        f"## Active tournaments",
    ]
    if actives:
        for t in actives:
            lines.append(f"  {t['id']:<14} {t['name']:<32} matches={t['n']:>3}  latest={t['latest']}")
    else:
        lines.append("  (none currently active)")

    lines += [
        f"",
        f"## 5 most recent matches anywhere in DB",
    ]
    for m in latest:
        won = m["winner_id"] or "?"
        lines.append(f"  {m['date']}  {m['round']:<8}  p1={m['p1_id']} p2={m['p2_id']} winner={won}  ({m['tournament_name']})")

    body = "\n".join(lines) + "\n"
    if dry_run:
        print(body)
        return body
    summary_path.write_text(body, encoding="utf-8")
    return body


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.db.exists():
        print(f"ERROR: {args.db} does not exist — run sync_* first", file=sys.stderr)
        return 1

    print(f"[snapshot] dumping {args.db}")
    sql = dump_sql(args.db)
    print(f"[snapshot] dump size: {len(sql):,} bytes")

    if args.dry_run:
        print(f"[snapshot] DRY RUN — would write to {SNAPSHOT_GZ}")
        write_summary(args.db, SNAPSHOT_GZ, SUMMARY_TXT, dry_run=True)
        return 0

    SNAPSHOT_GZ.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(SNAPSHOT_GZ, "wb", compresslevel=9) as f:
        f.write(sql)
    gz_size = SNAPSHOT_GZ.stat().st_size
    print(f"[snapshot] wrote {SNAPSHOT_GZ.relative_to(REPO_ROOT)} "
          f"({gz_size:,} bytes, {gz_size / len(sql) * 100:.1f}% of raw)")

    write_summary(args.db, SNAPSHOT_GZ, SUMMARY_TXT)
    print(f"[snapshot] wrote {SUMMARY_TXT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
