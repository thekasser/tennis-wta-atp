#!/usr/bin/env python3
"""
test_upcoming_in_past.py — Does player/past-matches/{mid} also return UPCOMING
matches (with odds)? If so, we don't need a separate odds endpoint.

Usage:
    python3 scripts/test_upcoming_in_past.py             # default: Bautista Agut atp
    python3 scripts/test_upcoming_in_past.py --tour wta --mid 4177  # Siegemund
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
    p.add_argument("--mid", type=int, default=1837,
                   help="Player mid. Default 1837 = Bautista Agut atp.")
    args = p.parse_args()

    api_key = os.environ.get("MATCHSTAT_API_KEY")
    if not api_key:
        print("MATCHSTAT_API_KEY not set", file=sys.stderr)
        return 1
    client = MatchstatClient(api_key=api_key)
    today = datetime.now(timezone.utc).date().isoformat()

    print(f"GET {args.tour}/player/past-matches/{args.mid}\n")
    data, meta = client.get(args.tour, f"player/past-matches/{args.mid}",
                             params={"pageSize": 50, "pageNo": 1})
    items = data.get("data", []) if isinstance(data, dict) else (data or [])
    print(f"Returned {len(items)} item(s)\n")
    if not items:
        print("(no items)")
        return 0

    sample = items[0]
    print(f"Sample keys: {sorted(sample.keys())}\n")

    # Bucket items
    upcoming = []
    completed = []
    for it in items:
        date_s = (it.get("date") or "")[:10]
        winner = it.get("match_winner") or it.get("winner_id")
        if date_s > today and not winner:
            upcoming.append(it)
        else:
            completed.append(it)

    print(f"Upcoming (date > today, no winner): {len(upcoming)}")
    print(f"Completed:                          {len(completed)}\n")
    if upcoming:
        print("Sample UPCOMING item (key answer to whether odds are here):")
        print(json.dumps(upcoming[0], indent=2, default=str)[:800])
        print()
        odds_present = sum(1 for u in upcoming
                           if u.get("odd1") not in (None, 0)
                           and u.get("odd2") not in (None, 0))
        print(f"Upcoming items with odds:  {odds_present}/{len(upcoming)}")
        if odds_present > 0:
            print()
            print("✓✓ HYPOTHESIS A CONFIRMED — past-matches endpoint includes")
            print("    upcoming matches WITH ODDS. No separate endpoint needed.")
        else:
            print()
            print("✗ Upcoming matches present but no odds yet. Either:")
            print("    (a) bookmakers post lines closer to match time, or")
            print("    (b) odds live on a different endpoint we haven't found.")
    else:
        print("No upcoming matches in past-matches response.")
        print("→ HYPOTHESIS B: past-matches is past-only. Need separate odds endpoint.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
