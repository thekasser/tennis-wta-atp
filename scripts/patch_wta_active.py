#!/usr/bin/env python3
"""Patch the activeTournaments block in season_wta.js with the WTA Madrid draw."""
import re
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DRAW_FILE = REPO_ROOT / 'scripts' / 'raw' / 'wta_madrid_draw.json'
PLAYERS_FILE = REPO_ROOT / 'data' / 'players_wta.js'
SEASON_FILE = REPO_ROOT / 'data' / 'season_wta.js'

TOURNAMENT_ID = 'madrid26'
CURRENT_STAGE = 'SF'

# Load draw data
draw_data = json.loads(DRAW_FILE.read_text())

# Parse PLAYERS_WTA bios
wta_js = PLAYERS_FILE.read_text(encoding='utf-8')
players = []
for m in re.finditer(r'\{id:(\d+)[^}]*?name:"([^"]+)"[^}]*?ab:"([^"]+)"', wta_js):
    players.append({'id': int(m.group(1)), 'name': m.group(2), 'ab': m.group(3)})

print(f'Loaded {len(players)} WTA bios, {len(draw_data)} draw entries')

# Match by ab field (case-insensitive) with fallbacks:
# 1. exact ab match
# 2. ab after the dot (e.g. "Q.Zheng" -> "Zheng")
# 3. last word of name
matched = {}
unmatched = []
for entry in draw_data:
    ln = entry['ln'].lower()
    found = False
    for p in players:
        ab = p['ab'].lower()
        ab_tail = ab.split('.')[-1] if '.' in ab else ab
        name_tail = p['name'].split()[-1].lower()
        if ab == ln or ab_tail == ln or name_tail == ln:
            matched[p['id']] = {'r': entry['round'], 'elim': entry['elim']}
            found = True
            break
    if not found:
        unmatched.append(entry['ln'])

print(f'Matched {len(matched)} players, unmatched: {unmatched}')

# Build players block
lines = []
for pid, s in sorted(matched.items()):
    elim_str = 'true' if s['elim'] else 'false'
    lines.append(f'        {pid}: {{r:"{s["r"]}", elim:{elim_str}}}')
players_block = ',\n'.join(lines)

# Patch season_wta.js activeTournaments
content = SEASON_FILE.read_text(encoding='utf-8')

new_block = f'''  activeTournaments: [
    {{
      id: "{TOURNAMENT_ID}",
      stage: "{CURRENT_STAGE}",
      players: {{
{players_block}
      }}
    }},
  ],'''

# Replace whatever is between activeTournaments: [ ... ],
content_new = re.sub(
    r'activeTournaments:\s*\[[\s\S]*?\],',
    new_block,
    content,
    count=1
)

if content == content_new:
    print('ERROR: failed to substitute activeTournaments block', file=sys.stderr)
    sys.exit(1)

SEASON_FILE.write_text(content_new, encoding='utf-8')
print(f'Patched {len(matched)} players into {SEASON_FILE.name} activeTournaments[{TOURNAMENT_ID}]')
