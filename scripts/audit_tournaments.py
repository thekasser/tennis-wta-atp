#!/usr/bin/env python3
"""
audit_tournaments.py — List tournaments seen in matches but not yet
catalogued in `data/tournaments.js`.

Output: a paste-ready table + JS skeleton block per unresolved tournament.

USAGE
-----
    python3 scripts/audit_tournaments.py                 # all unresolved
    python3 scripts/audit_tournaments.py --min-matches 5 # filter low-volume
    python3 scripts/audit_tournaments.py --tour wta      # one tour only
"""
from __future__ import annotations
import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect, REPO_ROOT


TOURNAMENTS_JS = REPO_ROOT / "data" / "tournaments.js"


def _suggest_id(name: str, year_hint: int | None) -> str:
    """Build a likely tournaments.js id slug from the tournament name."""
    # 'Mutua Madrid Open - Madrid' → 'madrid'
    # 'Mubadala Citi DC Open' → 'mubadalacitidcopen'
    base = name.split(" - ")[-1] if " - " in name else name
    slug = re.sub(r"[^a-z0-9]", "", base.lower())[:18] or "event"
    yy = str(year_hint)[-2:] if year_hint else "26"
    return f"{slug}{yy}"


def _wk_of(date_str: str) -> int:
    try:
        return date.fromisoformat(date_str).isocalendar()[1]
    except (ValueError, TypeError):
        return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--min-matches", type=int, default=1,
                   help="minimum match count to surface (default 1)")
    p.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    args = p.parse_args()

    conn = connect(read_only=True)

    # Group only by tour + api_id (and inferred name). NULL api_ids become
    # one bucket — those are matches we can't link by api_id even after
    # the catalog grows.
    where_extra = "" if args.tour == "both" else f"AND m.tour = '{args.tour}'"
    rows = list(conn.execute(f"""
        SELECT
            m.tour,
            m.tournament_api_id        AS api_id,
            MAX(m.tournament_name)     AS name,
            MIN(m.date)                AS first_date,
            MAX(m.date)                AS last_date,
            COUNT(*)                   AS match_count,
            MAX(m.surface)             AS surf_sample,
            COUNT(DISTINCT m.surface)  AS n_surfaces,
            COUNT(DISTINCT m.date)     AS n_distinct_dates
        FROM matches m
        LEFT JOIN tournaments t
          ON (m.tour='atp' AND t.api_id_atp = m.tournament_api_id)
          OR (m.tour='wta' AND t.api_id_wta = m.tournament_api_id)
        WHERE m.tournament_api_id IS NOT NULL
          AND t.id IS NULL
          {where_extra}
        GROUP BY m.tour, m.tournament_api_id
        HAVING match_count >= ?
        ORDER BY match_count DESC
    """, (args.min_matches,)))

    if not rows:
        print("[audit] no unresolved tournaments — every match's api_id "
              "resolves to an entry in data/tournaments.js. ")
        return 0

    total = sum(r["match_count"] for r in rows)
    print(f"# Unresolved tournaments: {len(rows)} events covering "
          f"{total:,} matches across the DB\n")

    # Header table
    print(f"{'TOUR':<5} {'API_ID':<8} {'M':>5}  {'DATES':<23}  {'SURF':<5}  NAME")
    print(f"{'-'*5} {'-'*8} {'-'*5}  {'-'*23}  {'-'*5}  {'-'*40}")
    for r in rows:
        dates = f"{r['first_date']} → {r['last_date']}"
        print(f"{r['tour'].upper():<5} {r['api_id']:<8} "
              f"{r['match_count']:>5}  {dates:<23}  "
              f"{(r['surf_sample'] or '?'):<5}  {(r['name'] or '?')[:50]}")

    print("\n# JS skeletons — paste into TOURNAMENTS_DATA[] in "
          "data/tournaments.js, set type/draw, then run scripts/seed_db.py.\n")

    for r in rows:
        first = r["first_date"] or ""
        last  = r["last_date"]  or first
        try:
            year = int(first[:4]); month = int(first[5:7])
        except ValueError:
            year, month = 0, 0
        wk = _wk_of(first)
        slug = _suggest_id(r["name"] or "event", year)
        api_field = "atp" if r["tour"] == "atp" else "wta"
        # Active flag from end-date heuristic.
        try:
            done = date.fromisoformat(last) < date.today()
        except ValueError:
            done = True
        active = "false" if done else "true"
        complete = "true" if done else "false"
        print(f'  {{ id:"{slug}", name:{r["name"]!r}, short:"?", '
              f'tour:"{r["tour"].upper()}", type:"?", '
              f'surf:"{r["surf_sample"] or "H"}", draw:?, '
              f'wk:{wk}, month:{month}, '
              f'startDate:"{first}", endDate:"{last}", '
              f'active:{active}, complete:{complete}, '
              f'apiId:{{{api_field}:{r["api_id"]}}} }},  // {r["match_count"]} matches')

    # Quick summary block by inferred event size.
    print("\n# Heuristic groupings (by match count) — tier guesses:")
    bands = [
        (120, "Likely GS / M1000 / W1000 (128-draw or 96-draw, deep field)"),
        (60,  "Likely M500 / W500 (32-draw, often two weeks)"),
        (25,  "Likely M250 / W250 (28-32 draw)"),
        (10,  "Likely W125 / Challenger / smaller"),
        (1,   "Tiny — qualifying-only or limited capture"),
    ]
    for lo, label in bands:
        bucket = [r for r in rows if r["match_count"] >= lo
                  and r["match_count"] < (bands[bands.index((lo, label))-1][0] if lo != bands[0][0] else 99999)]
        if not bucket:
            continue
        print(f"\n  {label}: {len(bucket)} events")
        for r in bucket[:8]:
            print(f"    {r['tour'].upper()} {r['name'][:50]:<50}  {r['match_count']:>4} matches  "
                  f"{r['first_date']}–{r['last_date']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
