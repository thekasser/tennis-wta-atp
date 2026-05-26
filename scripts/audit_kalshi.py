#!/usr/bin/env python3
"""
audit_kalshi.py — Surface gaps in Kalshi market coverage.

Three failure modes to detect:

  1. MATCH WITHOUT KALSHI:  a fixture/match exists locally but no
     kalshi_odds row points to it. Either Kalshi never listed the
     market, our name-matcher failed, or sync_kalshi was offline when
     the market opened.

  2. POST-MATCH ONLY:  kalshi_odds rows exist but all have is_pre_match=0.
     Every snapshot was captured AFTER the match started — useless for
     Brier scoring (materialize.py filters is_pre_match=1). Means
     sync_kalshi ran too late vs the match clock.

  3. PRE-MATCH SATURATED:  pre-match snapshot exists but yes_price is
     saturated (>0.98 or <0.02) — Kalshi market opened with extreme
     pricing or barely traded. Less actionable but worth tracking.

USAGE
-----
    python3 scripts/audit_kalshi.py                  # last 14 days
    python3 scripts/audit_kalshi.py --days 30        # wider window
    python3 scripts/audit_kalshi.py --tournament rg26  # one tournament

The script is read-only. Intended to run in CI (continue-on-error)
alongside audit_tournaments.py so coverage degradations surface in the
cron log before anyone notices missing market data on the dashboard.
"""
from __future__ import annotations
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--days", type=int, default=14,
                   help="lookback window in days (default 14)")
    p.add_argument("--tournament", type=str, default=None,
                   help="restrict to one tournament_id (e.g. 'rg26')")
    p.add_argument("--show-misses", type=int, default=15,
                   help="how many individual no-Kalshi matches to print "
                        "(default 15; 0 to suppress)")
    args = p.parse_args()

    conn = connect(read_only=True)
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    tour_filter = ""
    params: list = [cutoff]
    if args.tournament:
        tour_filter = "AND m.tournament_id = ?"
        params.append(args.tournament)

    # ── Aggregate per (tour, tournament) ────────────────────────────────────
    # LEFT JOIN: every match in window, with Kalshi columns NULL if no rows.
    # MAX(is_pre_match) tells us if ANY snapshot captured pre-match (1) or
    # only post-match (0); NULL means no snapshot at all.
    rows = list(conn.execute(f"""
        WITH window_matches AS (
            SELECT id, tour, tournament_id, date
            FROM matches m
            WHERE date >= ? {tour_filter}
        )
        SELECT
            wm.tour,
            wm.tournament_id,
            COUNT(DISTINCT wm.id) AS matches,
            COUNT(DISTINCT CASE WHEN k.match_id IS NOT NULL THEN wm.id END) AS with_any,
            COUNT(DISTINCT CASE WHEN k.is_pre_match = 1 THEN wm.id END) AS with_pre,
            COUNT(DISTINCT CASE WHEN k.match_id IS NOT NULL
                                AND k.is_pre_match = 0
                                AND wm.id NOT IN (
                                  SELECT DISTINCT match_id FROM kalshi_odds
                                  WHERE is_pre_match = 1
                                ) THEN wm.id END) AS post_only
        FROM window_matches wm
        LEFT JOIN kalshi_odds k ON k.match_id = wm.id
        GROUP BY wm.tour, wm.tournament_id
        HAVING matches >= 3
        ORDER BY (matches - with_any) DESC, matches DESC
    """, params))

    print(f"# Kalshi coverage audit — last {args.days}d "
          f"(cutoff {cutoff})\n")
    totals = {"matches": 0, "with_any": 0, "with_pre": 0, "post_only": 0}
    print(f"  {'TOUR':<5} {'TOURNAMENT':<22} {'MATCHES':>7} "
          f"{'NO-KALSHI':>9} {'PRE':>5} {'POST-ONLY':>9} {'COVERAGE':>9}")
    print(f"  {'-'*5} {'-'*22} {'-'*7} {'-'*9} {'-'*5} {'-'*9} {'-'*9}")
    for r in rows:
        miss = r["matches"] - r["with_any"]
        cov = (r["with_any"] / r["matches"] * 100) if r["matches"] else 0
        pre_pct = (r["with_pre"] / r["matches"] * 100) if r["matches"] else 0
        tname = (r["tournament_id"] or "(unresolved)")[:22]
        # Highlight rows with >25% miss rate or <20% pre-match rate
        flag = ""
        if cov < 75: flag = " ←low coverage"
        elif pre_pct < 20 and r["with_any"] > 0: flag = " ←mostly post-match"
        print(f"  {r['tour'].upper():<5} {tname:<22} {r['matches']:>7} "
              f"{miss:>9} {r['with_pre']:>5} {r['post_only']:>9} "
              f"{cov:>8.1f}%{flag}")
        for k in totals: totals[k] += r[k] if k != "matches" else r["matches"]

    # ── Totals ──────────────────────────────────────────────────────────────
    if totals["matches"]:
        cov_pct  = totals["with_any"] / totals["matches"] * 100
        pre_pct  = totals["with_pre"] / totals["matches"] * 100
        post_pct = totals["post_only"] / totals["matches"] * 100
        print(f"\n  TOTALS: {totals['matches']} matches  |  "
              f"with-Kalshi {totals['with_any']} ({cov_pct:.1f}%)  |  "
              f"pre-match {totals['with_pre']} ({pre_pct:.1f}%)  |  "
              f"post-only {totals['post_only']} ({post_pct:.1f}%)")
        # Useful headline number — what fraction of matches are usable for
        # Brier (have at least one is_pre_match=1 row)
        if cov_pct < 60:
            print("  ⚠ Coverage below 60% — likely sync_kalshi name/timing issue")
        if pre_pct < 25 and totals["matches"] >= 20:
            print("  ⚠ Pre-match rate below 25% — cron isn't catching markets "
                  "before kickoff. Consider increasing sync_kalshi frequency.")

    # ── Show worst individual misses ────────────────────────────────────────
    if args.show_misses > 0:
        misses = list(conn.execute(f"""
            SELECT m.id, m.date, m.tour, m.tournament_id, m.tournament_name,
                   m.round, m.p1_id, m.p2_id,
                   p1.name AS p1_name, p2.name AS p2_name
            FROM matches m
            LEFT JOIN players p1 ON p1.mid = m.p1_id
            LEFT JOIN players p2 ON p2.mid = m.p2_id
            LEFT JOIN kalshi_odds k ON k.match_id = m.id
            WHERE m.date >= ? {tour_filter}
              AND k.match_id IS NULL
              AND m.round NOT LIKE 'Q%'  -- skip qualifying; Kalshi rarely lists
            GROUP BY m.id
            ORDER BY m.date DESC, m.tournament_id, m.id
            LIMIT ?
        """, params + [args.show_misses]))
        if misses:
            print(f"\n# Sample of {len(misses)} main-draw matches with NO "
                  "Kalshi data (most recent first):\n")
            for m in misses:
                p1 = (m["p1_name"] or f"mid={m['p1_id']}")[:20]
                p2 = (m["p2_name"] or f"mid={m['p2_id']}")[:20]
                tid = (m["tournament_id"] or "?")[:14]
                print(f"  {m['date']}  {tid:<14}  {m['round']:<8}  "
                      f"{p1:<20} vs {p2:<20}  (match_id={m['id']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
