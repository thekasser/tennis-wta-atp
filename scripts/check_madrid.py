#!/usr/bin/env python3
"""One-shot: check whether recent_matches.js has Madrid SF/F for Andreeva (10) and Kostyuk (27)."""
import json
import re
from pathlib import Path

raw = (Path(__file__).parent.parent / 'data' / 'recent_matches.js').read_text()
m = re.search(r'const RECENT_MATCHES\s*=\s*(\{.*\});', raw, re.DOTALL)
data = json.loads(m.group(1))
print(f"recent_matches.js lastUpdated: {data.get('lastUpdated')}\n")
for pid, name in [('10', 'Andreeva'), ('27', 'Kostyuk')]:
    print(f'Player {pid} ({name}):')
    for row in data['wta'].get(pid, [])[:5]:
        print(f"  {row['date']}  {row['rd']:>10}  vs {row['opp']:<28}  score={row.get('score','')}  won={row.get('won')}")
    print()
