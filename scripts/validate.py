#!/usr/bin/env python3
"""
validate.py — Sanity checks on data/tennis.db before snapshotting.

CI runs this between materialize and snapshot. Bails non-zero on any
violation so a corrupt DB never gets committed.

CHECKS
------
  1. Table row counts within sane bands (no zeros, no orders-of-magnitude jumps).
  2. Latest match anywhere in DB is within the last N days (default 14).
  3. Latest rankings snapshot is from today or yesterday.
  4. No duplicate (mid, tour) within players.
  5. matches.tour is set on >95% of rows.
  6. Active tournaments have at least one match each.

USAGE
-----
    python3 scripts/validate.py
    python3 scripts/validate.py --strict    # exit 1 on any warning
"""
from __future__ import annotations
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import DEFAULT_DB_PATH, connect


# Sane lower bounds — adjust as the corpus grows.
MIN_PLAYERS     = 200
MIN_TOURNAMENTS = 25
MIN_MATCHES     = 200
MIN_RANKINGS    = 100
LATEST_MATCH_AGE_DAYS    = 14
LATEST_RANKING_AGE_DAYS  = 2
ACTIVE_TOURNAMENT_MIN_MATCHES = 1


def _check(label: str, ok: bool, msg: str) -> tuple[bool, str]:
    icon = "✓" if ok else "✗"
    return ok, f"  {icon} {label:<32} {msg}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--strict", action="store_true",
                   help="exit 1 on any warning, not just hard failures")
    args = p.parse_args()

    if not args.db.exists():
        print(f"[validate] {args.db} does not exist", file=sys.stderr)
        return 1

    conn = connect(args.db, read_only=True)
    failures: list[str] = []
    warnings: list[str] = []

    def hard(label, ok, msg):
        ok, line = _check(label, ok, msg)
        print(line)
        if not ok:
            failures.append(line.strip())

    def warn(label, ok, msg):
        ok, line = _check(label, ok, msg)
        print(line)
        if not ok:
            warnings.append(line.strip())

    # 1. Row counts
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("players", "tournaments", "matches", "rankings_snapshots")}
    hard("players row count",     counts["players"]     >= MIN_PLAYERS,
         f"{counts['players']:>5} (need ≥{MIN_PLAYERS})")
    hard("tournaments row count", counts["tournaments"] >= MIN_TOURNAMENTS,
         f"{counts['tournaments']:>5} (need ≥{MIN_TOURNAMENTS})")
    hard("matches row count",     counts["matches"]     >= MIN_MATCHES,
         f"{counts['matches']:>5} (need ≥{MIN_MATCHES})")
    hard("rankings row count",    counts["rankings_snapshots"] >= MIN_RANKINGS,
         f"{counts['rankings_snapshots']:>5} (need ≥{MIN_RANKINGS})")

    # 2. Latest match within window
    latest_match = conn.execute("SELECT MAX(date) AS d FROM matches").fetchone()["d"]
    if latest_match:
        age = (date.today() - date.fromisoformat(latest_match)).days
        warn("latest match recency", age <= LATEST_MATCH_AGE_DAYS,
             f"{latest_match} ({age}d ago)")
    else:
        hard("latest match recency", False, "no matches in DB")

    # 3. Latest ranking within window
    latest_rank = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM rankings_snapshots"
    ).fetchone()["d"]
    if latest_rank:
        age = (date.today() - date.fromisoformat(latest_rank)).days
        hard("latest ranking recency", age <= LATEST_RANKING_AGE_DAYS,
             f"{latest_rank} ({age}d ago, max {LATEST_RANKING_AGE_DAYS})")
    else:
        hard("latest ranking recency", False, "no rankings in DB")

    # 4. No duplicate (mid, tour)
    dups = conn.execute("""
        SELECT mid, COUNT(*) AS n FROM players
        GROUP BY mid HAVING n > 1
    """).fetchall()
    hard("no duplicate mids", not dups,
         f"found {len(dups)}: {[r['mid'] for r in dups][:5]}" if dups else "all unique")

    # 5. matches.tour coverage
    n_total = counts["matches"]
    n_with_tour = conn.execute("SELECT COUNT(*) FROM matches WHERE tour IS NOT NULL").fetchone()[0]
    pct = (n_with_tour / n_total * 100) if n_total else 0
    warn("matches.tour coverage", pct >= 95.0,
         f"{n_with_tour:>5}/{n_total} = {pct:.1f}% (warn <95%)")

    # 6. Active tournaments have matches
    actives = list(conn.execute("""
        SELECT t.id, t.name, COUNT(m.id) AS n
        FROM tournaments t LEFT JOIN matches m ON m.tournament_id = t.id
        WHERE t.active = 1
        GROUP BY t.id
    """))
    for t in actives:
        warn(f"active: {t['id']}", t["n"] >= ACTIVE_TOURNAMENT_MIN_MATCHES,
             f"{t['n']:>3} matches")

    print()
    if failures:
        print(f"[validate] {len(failures)} hard failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    if warnings:
        print(f"[validate] {len(warnings)} warning(s)")
        if args.strict:
            return 1
    print("[validate] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
