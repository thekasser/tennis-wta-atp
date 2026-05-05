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

# Use Sinner (mid 36558) as a test player — almost certain to have upcoming
# matches (or recent ones) at the active event.
SINNER_MID = 36558  # ATP

# Probe pattern is `player/{resource}/{mid}` since that's the known shape:
# `player/profile/{mid}` and `player/past-matches/{mid}` both work. The
# upcoming-match endpoint should be a sibling.
PROBES = [
    # Most likely — mirror of past-matches
    ("atp", f"player/upcoming-matches/{SINNER_MID}",   None, "mirror of past-matches"),
    ("atp", f"player/next-matches/{SINNER_MID}",       None, "next-matches variant"),
    ("atp", f"player/scheduled-matches/{SINNER_MID}",  None, "scheduled variant"),
    ("atp", f"player/upcoming/{SINNER_MID}",           None, "short upcoming"),
    ("atp", f"player/scheduled/{SINNER_MID}",          None, "short scheduled"),
    ("atp", f"player/next/{SINNER_MID}",               None, "short next"),
    # past-matches with a status param — maybe one endpoint for both
    ("atp", f"player/past-matches/{SINNER_MID}",  {"status": "upcoming"}, "past-matches w/ upcoming flag"),
    ("atp", f"player/past-matches/{SINNER_MID}",  {"upcoming": "true"}, "past-matches w/ upcoming bool"),
    ("atp", f"player/matches/{SINNER_MID}",            None, "generic matches endpoint"),
    ("atp", f"player/matches/{SINNER_MID}",       {"type": "upcoming"}, "matches w/ type filter"),
    # H2H is also likely a player-scoped endpoint we could discover
    ("atp", f"player/h2h/{SINNER_MID}",                None, "h2h root"),
    # Docs/index endpoints sometimes exist
    ("atp", "endpoints",                               None, "self-describing index"),
    ("atp", "",                                        None, "tour root"),
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
