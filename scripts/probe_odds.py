#!/usr/bin/env python3
"""
probe_odds.py — Find the Matchstat odds endpoint for upcoming matches.

`getTournamentFixtures` returns scheduled matches but no odds. Past matches
DO have odd1/odd2 fields (we use them for the model-vs-market backtest), so
odds exist somewhere in the API — likely a separate endpoint keyed by
match_id, fixture_id, or tournament_id.

Tries a list of plausible paths against a known upcoming Rome 2026 fixture
(match_id 1198, Bautista Agut vs Maestrelli, 2026-05-06). First hit wins.

Cost: ~10 API calls. Re-runnable.
"""
from __future__ import annotations
import json, os, sys
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

# Real upcoming match ids from rome26 atp/wta fixtures (run today)
ROME_ATP_MATCH_ID = 1198      # Bautista Agut vs Maestrelli
ROME_WTA_MATCH_ID = 832       # Siegemund vs Bejlek
ROME26_ATP_TOURN  = 21326

PROBES = [
    # Match-id-keyed paths
    ("atp", f"odds/{ROME_ATP_MATCH_ID}",                None),
    ("atp", f"odds/match/{ROME_ATP_MATCH_ID}",          None),
    ("atp", f"match/{ROME_ATP_MATCH_ID}/odds",          None),
    ("atp", f"match/odds/{ROME_ATP_MATCH_ID}",          None),
    ("atp", f"fixture/{ROME_ATP_MATCH_ID}/odds",        None),
    ("atp", f"fixture/odds/{ROME_ATP_MATCH_ID}",        None),
    ("atp", f"odds/fixture/{ROME_ATP_MATCH_ID}",        None),
    # Tournament-scoped odds
    ("atp", f"odds/tournament/{ROME26_ATP_TOURN}",      None),
    ("atp", f"tournament/odds/{ROME26_ATP_TOURN}",      None),
    ("atp", f"odds/fixtures/tournament/{ROME26_ATP_TOURN}", None),
    # Bookmaker patterns
    ("atp", f"bookmaker/odds/{ROME_ATP_MATCH_ID}",      None),
    ("atp", f"bookmakers/{ROME_ATP_MATCH_ID}",          None),
    # Top-level
    ("atp", "odds",                                      None),
    ("atp", "odds/upcoming",                             None),
]

def main() -> int:
    api_key = os.environ.get("MATCHSTAT_API_KEY")
    if not api_key:
        print("MATCHSTAT_API_KEY not set", file=sys.stderr)
        return 1
    client = MatchstatClient(api_key=api_key)

    print(f"Probing {len(PROBES)} candidate paths…\n")
    hits = []
    for tour, ep, params in PROBES:
        try:
            data, meta = client.get(tour, ep, params=params)
        except Exception as e:
            msg = str(e).split("\n")[0][:90]
            print(f"  ✗ {ep:50}  {msg}")
            continue
        if isinstance(data, dict):
            shape = "dict[" + ",".join(list(data.keys())[:6]) + "]"
        elif isinstance(data, list):
            shape = f"array[{len(data)}]"
        else:
            shape = type(data).__name__
        status = meta.get("http_status")
        print(f"  ✓ {ep:50}  {status}  {shape}")
        sample = json.dumps(data, indent=2, default=str)[:600]
        print(f"     sample:\n{sample}\n")
        hits.append((tour, ep))

    print("\n" + "=" * 60)
    print(f"Hits: {len(hits)} of {len(PROBES)}")
    if not hits:
        print("All 404. The odds endpoint isn't in the tested patterns —")
        print("recommend checking RapidAPI 'getXxxOdds' operation in the docs UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
