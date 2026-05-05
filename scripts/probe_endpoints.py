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

client = MatchstatClient(api_key=API_KEY)

# Madrid 2026 ATP api_id (confirmed in our DB)
MADRID_ATP = 21325
MADRID_WTA = 16721
ROME_2025_ATP = 20337  # in case 2026 IDs missing

# Probe matrix: (tour, endpoint, params, hypothesis)
PROBES = [
    ("atp", f"tournament/{MADRID_ATP}/matches",        None, "all matches at tournament"),
    ("atp", f"tournament/{MADRID_ATP}/draw",           None, "tournament draw"),
    ("atp", f"tournament/{MADRID_ATP}/upcoming",       None, "upcoming-only at tournament"),
    ("atp", f"tournament/{MADRID_ATP}/schedule",       None, "schedule"),
    ("atp", f"tournament/{MADRID_ATP}",                None, "tournament info"),
    ("atp", f"event/{MADRID_ATP}",                     None, "event alias"),
    ("atp", f"event/{MADRID_ATP}/matches",             None, "event matches"),
    ("atp", "match/upcoming",        {"tournamentId": MADRID_ATP}, "upcoming + tour filter"),
    ("atp", "match/list",  {"tournamentId": MADRID_ATP, "date": "2026-05-04"}, "by date"),
    ("atp", "match/today",                             None, "today's matches"),
    ("atp", "matches/upcoming",                        None, "matches/upcoming"),
    ("atp", "tournaments/active",                      None, "active list"),
    ("atp", "tournaments/upcoming",                    None, "upcoming list"),
    ("atp", "live",                                    None, "live root"),
    ("atp", "live/matches",                            None, "live matches"),
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
