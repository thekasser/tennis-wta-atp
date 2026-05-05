#!/usr/bin/env python3
"""
test_fixtures.py — One-off test of getTournamentFixtures for an active or
upcoming tournament. Tells us whether the response contains:
  - Scheduled-future matches (date > today) → unlocks Phase 2/3 upcoming view
  - odd1/odd2 fields → unlocks model-vs-market on upcoming matches

Usage:
    python3 scripts/test_fixtures.py                 # rome26 atp by default
    python3 scripts/test_fixtures.py --tour wta --id 16722
    python3 scripts/test_fixtures.py --id 21325      # madrid26 atp
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(ROOT / "scripts"))
from matchstat import MatchstatClient


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tour", default="atp", choices=["atp", "wta"])
    p.add_argument("--id", type=int, default=21326,
                   help="Tournament api_id. Default 21326 = rome26 atp.")
    p.add_argument("--page-size", type=int, default=100)
    args = p.parse_args()

    api_key = os.environ.get("MATCHSTAT_API_KEY")
    if not api_key:
        print("MATCHSTAT_API_KEY not set in env or .env", file=sys.stderr)
        return 1

    client = MatchstatClient(api_key=api_key, auto_log=True)
    path = f"fixtures/tournament/{args.id}"
    print(f"GET {args.tour}/{path}\n")

    try:
        data, meta = client.get(args.tour, path,
                                 params={"pageSize": args.page_size, "pageNo": 1,
                                         "filter": "PlayerGroup:both;"})
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    items = data.get("data", []) if isinstance(data, dict) else (data or [])
    print(f"Returned {len(items)} item(s)")
    print(f"hasNextPage: {data.get('hasNextPage')}\n")
    if not items:
        print("(empty response — tournament may not have fixtures yet)")
        return 0

    # Schema fingerprint
    sample = items[0]
    print(f"Sample item keys: {sorted(sample.keys())}")
    print(f"\nFull sample item:\n{json.dumps(sample, indent=2, default=str)}\n")

    # Counts of interest
    today = datetime.now(timezone.utc).date().isoformat()
    has_winner = sum(1 for i in items if i.get("match_winner") or i.get("winner_id"))
    has_odds   = sum(1 for i in items if i.get("odd1") not in (None, 0)
                                       and i.get("odd2") not in (None, 0))
    future     = sum(1 for i in items if (i.get("date") or "") > today + "T")

    print("─" * 60)
    print(f"With winner field:    {has_winner}/{len(items)}  (= already played)")
    print(f"Without winner:       {len(items) - has_winner}/{len(items)}  (= scheduled / in progress)")
    print(f"Date > today:         {future}/{len(items)}  (= future fixtures)")
    print(f"Has odds (odd1/odd2): {has_odds}/{len(items)}")
    print()
    if (len(items) - has_winner) > 0:
        print("✓ Endpoint returns scheduled-future fixtures — unlocks upcoming-match view")
    else:
        print("✗ Only completed matches returned — same data as past-matches endpoint")
    if has_odds > has_winner * 0.8:
        print("✓ Odds present on most items — model-vs-market deltas viable")
    else:
        print("? Odds sparse — may need a different odds endpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
