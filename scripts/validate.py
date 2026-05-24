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

# Resolution-rate floor: of last-180d matches, % that resolve to a known
# tournament. Below this means tournaments.js is missing real tour events
# (the bug class that broke the trapezoid tab today).
MIN_RESOLUTION_RATE_PCT = 55.0   # warn floor; matches at W125/Challenger/ITF stay unresolved

# Tier-pass-through floor: of bios with bio_id ≤ 200, how many should clear
# the dashboard's default min-matches in T6M (≥10 tour-level main-draw
# matches). If this drops dramatically vs prior runs, the materializer
# tier filter probably broke (today's regression: this dropped to 1).
MIN_T6M_TOUR_PLAYERS_PER_TOUR = 15

# Tour-level miss detector: any tournament_api_id with ≥N matches in the
# last 60d that doesn't resolve to a tournaments row, AND whose name lacks
# the lower-tier markers, is almost certainly a 250+ event we forgot to
# add. Caught Geneva/Strasbourg/Rabat in the 2026-05-24 audit. Real W250+
# events with qualifying typically run ≥28 matches; W125s top out at ~25,
# so 28 gives a clean signal/noise tradeoff. A W250 with no qualifying
# could be missed at this threshold, but the lookback window will catch
# it on a subsequent cron when more matches accumulate.
TOUR_LEVEL_MISS_MIN_MATCHES = 28
TOUR_LEVEL_MISS_LOOKBACK_DAYS = 60
# Name fragments that mark non-tour-level events we intentionally skip.
LOWER_TIER_NAME_MARKERS = (
    "Challenger", "ITF", "W75", "W50", "W35", "W15",
    "125",                          # WTA 125
    "Cup, Group", "BJK Cup",        # team events
    "Davis Cup", "Laver Cup", "United Cup",  # team / exhibition
)

# Date-drift detector: catalog start_date should be within 14d of the
# tournament's actual match dates. Wider drift means the catalog row has
# stale dates (Hamburg 2026: catalog Jul, actual May → 58d drift, never
# detected as active despite playing the Final yesterday).
DATE_DRIFT_MAX_DAYS = 14


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

    # 6. Active tournaments have matches — date-window definition matching
    # materialize.py. tournaments.active flag is informational only.
    actives = list(conn.execute("""
        SELECT t.id, t.name, COUNT(m.id) AS n
        FROM tournaments t LEFT JOIN matches m ON m.tournament_id = t.id
        WHERE t.start_date IS NOT NULL AND t.end_date IS NOT NULL
          AND date(t.start_date, '-7 days') <= date('now')
          AND date(t.end_date,   '+1 day')  >= date('now')
        GROUP BY t.id
    """))
    for t in actives:
        warn(f"active: {t['id']}", t["n"] >= ACTIVE_TOURNAMENT_MIN_MATCHES,
             f"{t['n']:>3} matches")

    # 7. Resolution-rate floor (last 180d matches that resolve to a tournament)
    # Catches the class of bug where tournaments.js entries lack apiId — would
    # have caught today's regression that left only 1 player in T6M view.
    rate = conn.execute("""
        SELECT
            CAST(SUM(CASE WHEN tournament_id IS NOT NULL THEN 1 ELSE 0 END) AS REAL)
            / NULLIF(COUNT(*), 0) * 100.0 AS pct,
            COUNT(*) AS total
        FROM matches WHERE date >= date('now', '-180 days')
    """).fetchone()
    if rate["total"]:
        warn("resolution rate (180d)",
             rate["pct"] >= MIN_RESOLUTION_RATE_PCT,
             f"{rate['pct']:.1f}% of {rate['total']:,} matches "
             f"(warn <{MIN_RESOLUTION_RATE_PCT}%)")
    else:
        warn("resolution rate (180d)", False, "no recent matches")

    # 8. T6M tour-level pass-through: how many bios have ≥10 tour-level
    # main-draw matches in last 180d. Today's bug dropped this to 1 (was 30).
    # Catches tier-filter regressions before they hit the dashboard.
    for tour in ("atp", "wta"):
        cnt = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT p.bio_id, COUNT(*) AS n
                FROM players p
                JOIN matches m ON (m.p1_id=p.mid OR m.p2_id=p.mid)
                JOIN tournaments t ON t.id = m.tournament_id
                WHERE p.tour = ?
                  AND p.bio_id <= 200
                  AND m.date >= date('now', '-180 days')
                  AND m.round NOT LIKE 'Q%'
                  AND t.type IN ('GS','M1000','W1000','M500','W500',
                                 'M250','W250','ATPFinals','WTAFinals')
                GROUP BY p.bio_id
                HAVING n >= 10
            )
        """, (tour,)).fetchone()[0]
        warn(f"T6M tour-level players ({tour})",
             cnt >= MIN_T6M_TOUR_PLAYERS_PER_TOUR,
             f"{cnt:>3} bios with ≥10 tour-level main-draw matches "
             f"(warn <{MIN_T6M_TOUR_PLAYERS_PER_TOUR})")

    # 9. Tour-level miss detector — recent unresolved events that look
    # tour-level by match count + name. Catches the bug class where
    # Matchstat returns matches at an event whose row isn't in
    # tournaments.js yet (Geneva/Strasbourg/Rabat 2026-05-24).
    not_like = " AND ".join(
        [f"m.tournament_name NOT LIKE '%{frag}%'" for frag in LOWER_TIER_NAME_MARKERS]
    )
    misses = list(conn.execute(f"""
        SELECT m.tour, m.tournament_api_id AS api_id,
               MAX(m.tournament_name) AS name,
               COUNT(*) AS n, MIN(m.date) AS d1, MAX(m.date) AS d2
        FROM matches m
        LEFT JOIN tournaments t
          ON (m.tour='atp' AND t.api_id_atp = m.tournament_api_id)
          OR (m.tour='wta' AND t.api_id_wta = m.tournament_api_id)
        WHERE m.tournament_api_id IS NOT NULL
          AND t.id IS NULL
          AND m.date >= date('now', '-{TOUR_LEVEL_MISS_LOOKBACK_DAYS} days')
          AND {not_like}
        GROUP BY m.tour, m.tournament_api_id
        HAVING n >= ?
        ORDER BY n DESC
    """, (TOUR_LEVEL_MISS_MIN_MATCHES,)))
    if misses:
        warn(f"unmapped tour events ({TOUR_LEVEL_MISS_LOOKBACK_DAYS}d)", False,
             f"{len(misses)} likely-tour-level events with no tournaments row")
        for m in misses[:5]:
            print(f"      → {m['tour'].upper()} api={m['api_id']} "
                  f"{m['n']:>3}m {m['d1']}→{m['d2']}  {m['name']}")
        if len(misses) > 5:
            print(f"      … and {len(misses)-5} more — run scripts/audit_tournaments.py")
    else:
        warn(f"unmapped tour events ({TOUR_LEVEL_MISS_LOOKBACK_DAYS}d)", True,
             "no tour-level misses")

    # 10. Date-drift detector — catalog start_date diverges from actual
    # match dates by >14d. Catches stale rows where dates were copied
    # from a prior year that's since rescheduled (Hamburg 2026: catalog
    # Jul 13, actual May 17 → 58d drift, never showed as active).
    drifts = list(conn.execute(f"""
        SELECT t.id, t.tour, t.start_date AS catalog_start,
               MIN(m.date) AS actual_start, COUNT(*) AS n,
               CAST(julianday(t.start_date) - julianday(MIN(m.date)) AS INTEGER) AS drift
        FROM tournaments t
        JOIN matches m ON m.tournament_id = t.id
        WHERE t.start_date IS NOT NULL
        GROUP BY t.id
        HAVING ABS(drift) > {DATE_DRIFT_MAX_DAYS}
        ORDER BY ABS(drift) DESC
    """))
    if drifts:
        warn(f"tournament date drift (>{DATE_DRIFT_MAX_DAYS}d)", False,
             f"{len(drifts)} row(s) with catalog dates far from actual matches")
        for d in drifts[:5]:
            print(f"      → {d['id']:<25} catalog {d['catalog_start']} "
                  f"vs actual {d['actual_start']}  ({d['drift']:+d}d, {d['n']} matches)")
        if len(drifts) > 5:
            print(f"      … and {len(drifts)-5} more")
    else:
        warn(f"tournament date drift (>{DATE_DRIFT_MAX_DAYS}d)", True,
             "all catalog dates within tolerance")

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
