#!/usr/bin/env python3
"""
probe_endpoints.py — Try a list of plausible Matchstat endpoints to find
ones that return upcoming matches / draws / live data. One-time discovery
script; once we know the right paths, integrate into sync_matches.py.

Burns ~15 API calls. Safe to re-run; results are read-only.

Usage:
    python3 scripts/probe_endpoints.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# Load .env so we don't need to export
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from matchstat import MatchstatClient

API_KEY = os.environ.get("MATCHSTAT_API_KEY")
if not API_KEY:
    print("MATCHSTAT_API_KEY not set", file=sys.stderr)
    sys.exit(1)

client = MatchstatClient(api_key=API_KEY, auto_log=True)

# Confirmed in RapidAPI docs (2026-05-04 user-discovered):
#   getTournamentSeasons  — list editions of a tournament series across years
#   getTournamentFixtures — match fixtures for a specific tournament edition
#   getAllFixtures        — broad, only takes tour, probably too noisy
# Path shape inferred from the docs UI: tournament/{id}/seasons + fixtures.
ROME_2025_ATP = 20337  # canonical historical Rome ATP id

PROBES = [
    # 1) Verify getTournamentSeasons — should return all Rome editions
    ("atp", f"tournament/{ROME_2025_ATP}/seasons",          None, "getTournamentSeasons (path-style)"),
    ("atp", "tournament/seasons",  {"tournament": ROME_2025_ATP}, "getTournamentSeasons (query-style)"),
    # 2) Verify getTournamentFixtures with the same id
    ("atp", f"tournament/{ROME_2025_ATP}/fixtures",         None, "getTournamentFixtures (path-style)"),
    ("atp", "tournament/fixtures", {"tournament": ROME_2025_ATP, "filter": "PlayerGroup:both;"}, "getTournamentFixtures (query-style)"),
    # 3) getAllFixtures
    ("atp", "fixtures",                                     None, "getAllFixtures (path-style)"),
    ("atp", "all-fixtures",                                 None, "getAllFixtures (alt)"),
]

print(f"Probing {len(PROBES)} endpoints… (each ✓ = found, ✗ = 4xx/5xx)")
print("=" * 72)
hits = []
for tour, ep, params, hyp in PROBES:
    try:
        data, meta = client.get(tour, ep, params)
        status = meta.get("http_status")
        if isinstance(data, dict):
            shape = "dict[" + ",".join(list(data.keys())[:5]) + "]"
        elif isinstance(data, list):
            shape = f"array[{len(data)}]"
            if data and isinstance(data[0], dict):
                shape += " items: dict[" + ",".join(list(data[0].keys())[:5]) + "]"
        else:
            shape = type(data).__name__
        print(f"  ✓ {ep:42}  {status}  {shape}")
        print(f"     hyp: {hyp}")
        sample = json.dumps(data, indent=2)[:400]
        print(f"     sample: {sample}")
        print()
        hits.append((ep, params, shape))
    except Exception as e:
        msg = str(e).split("\n")[0][:100]
        print(f"  ✗ {ep:42}  {msg}")

print("=" * 72)
print(f"\n{len(hits)} hit(s).  Next step: pick the most useful endpoint(s)")
print("and integrate into sync_matches.py to fetch upcoming-with-odds data.")
