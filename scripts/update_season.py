#!/usr/bin/env python3
"""
update_season.py — ATP/WTA season data updater
================================================
Reads raw scraped JSON files from scripts/raw/ and writes updated
data/season_atp.js + data/season_wta.js, preserving results{} history.

USAGE
-----
  python scripts/update_season.py [--tour atp|wta|both] [--dry-run]

SCRAPING STEP (run manually or via scheduled task)
---------------------------------------------------
Before running this script, Claude must scrape 4 pages using the Chrome
extension and save the raw JSON to scripts/raw/. See JS_EXTRACTIONS dict
below for the exact JS to run on each page, and save results like:

  python -c "import json; open('scripts/raw/atp_rankings.json','w').write(json.dumps(<PASTE_RESULT>))"

Or Claude can write the JSON directly using the Write tool.

JS EXTRACTIONS (run via mcp__Claude_in_Chrome__javascript_tool)
----------------------------------------------------------------
"""

import json
import re
import os
import sys
import argparse
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
REPO_ROOT   = SCRIPT_DIR.parent
DATA_DIR    = REPO_ROOT / "data"
RAW_DIR     = SCRIPT_DIR / "raw"

# ─── JS extraction snippets (copy-paste into javascript_tool) ─────────────────
JS_EXTRACTIONS = {
    "atp_rankings": {
        "url": "https://www.atptour.com/en/rankings/singles/live",
        "note": "Rank, T12M pts, inline tournament status per player",
        "js": r"""
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells = Array.from(r.querySelectorAll('td'));
    const rankRaw = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const nameRaw = (cells[1]?.innerText || '').trim();
    const lines   = nameRaw.split('\n').map(s => s.trim()).filter(Boolean);
    const ptsRaw  = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    // Some ATP pages embed e.g. "Houston R16" inside the name cell
    const tournMatch = nameRaw.match(/([A-Z][a-zA-Z ]+)\s+(R\d+|QF|SF|F|W)\b/);
    return {
      rank:     parseInt(rankRaw) || null,
      name:     lines[0] || '',
      pts:      parseInt(ptsRaw) || 0,
      activeTournament: tournMatch ? tournMatch[1].trim() : null,
      activeRound:      tournMatch ? tournMatch[2]        : null
    };
  }).filter(r => r.rank !== null));
})()
"""
    },
    "atp_race": {
        "url": "https://www.atptour.com/en/rankings/race",
        "note": "YTD race points per player",
        "js": r"""
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells  = Array.from(r.querySelectorAll('td'));
    const rank   = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name   = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ytd    = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { raceRank: parseInt(rank)||null, name, ytd: parseInt(ytd)||0 };
  }).filter(r => r.raceRank !== null));
})()
"""
    },
    "wta_rankings": {
        "url": "https://www.wtatennis.com/rankings/singles",
        "note": "Rank + T12M pts per player",
        "js": r"""
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells = Array.from(r.querySelectorAll('td'));
    const rankRaw = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name    = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ptsRaw  = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { rank: parseInt(rankRaw)||null, name, pts: parseInt(ptsRaw)||0 };
  }).filter(r => r.rank !== null));
})()
"""
    },
    "wta_race": {
        "url": "https://www.wtatennis.com/rankings/race-singles",
        "note": "YTD race points (column index 4) per player",
        "js": r"""
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells  = Array.from(r.querySelectorAll('td'));
    const rank   = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name   = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ytd    = (cells[4]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { raceRank: parseInt(rank)||null, name, ytd: parseInt(ytd)||0 };
  }).filter(r => r.raceRank !== null));
})()
"""
    }
}

# ─── Name normalization helpers ───────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', ascii_str.lower().strip())


def build_name_index(players_list: list) -> dict:
    """
    Returns {normalized_name: player_id} plus common abbreviation variants.
    """
    idx = {}
    for p in players_list:
        pid = p['id']
        # Full name
        idx[normalize_name(p['name'])] = pid
        # Abbreviation field
        if p.get('ab'):
            idx[normalize_name(p['ab'])] = pid
        # Last name only
        parts = p['name'].split()
        if parts:
            idx[normalize_name(parts[-1])] = pid
        # First initial + last name  e.g. "C. Alcaraz"
        if len(parts) >= 2:
            idx[normalize_name(f"{parts[0][0]}. {parts[-1]}")] = pid
    return idx


def match_name(raw_name: str, index: dict) -> int | None:
    """Try to find player ID from a scraped name string."""
    norm = normalize_name(raw_name)
    if norm in index:
        return index[norm]
    # Try last-name-only fallback
    parts = norm.split()
    if parts:
        last = parts[-1]
        if last in index:
            return index[last]
    return None

# ─── JS file parsing ──────────────────────────────────────────────────────────

def parse_js_array(js_text: str, var_name: str) -> list:
    """
    Very lightweight JS array parser — reads the PLAYERS_ATP / PLAYERS_WTA
    arrays by converting their JS object literals to JSON.
    Only handles the flat key:value format used in players_*.js.
    """
    # Extract the array literal between [ ... ]
    pattern = rf'const\s+{var_name}\s*=\s*(\[[\s\S]*?\]);'
    m = re.search(pattern, js_text)
    if not m:
        return []
    raw = m.group(1)
    # Convert JS object keys to quoted keys  {id:1 -> {"id":1
    raw = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
    # Remove trailing commas before } or ]
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    # Convert JS true/false
    raw = raw.replace(':true', ':true').replace(':false', ':false')
    raw = re.sub(r':\s*true\b', ': true', raw)
    raw = re.sub(r':\s*false\b', ': false', raw)
    # surf sub-object: {H:.75,C:.82,G:.79}
    # Keys already handled above; decimal values like .75 need leading zero
    raw = re.sub(r':\s*\.(\d)', r': 0.\1', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARN: JSON parse error for {var_name}: {e}", file=sys.stderr)
        return []


def parse_js_season(js_text: str, var_name: str) -> dict:
    """
    Parse existing season_*.js into a Python dict, preserving the
    players{} sub-dict with results history.
    Returns {} on failure.
    """
    # Extract the full object literal for SEASON_ATP / SEASON_WTA
    pattern = rf'const\s+{var_name}\s*=\s*(\{{[\s\S]*?\}});\s*$'
    m = re.search(pattern, js_text, re.MULTILINE)
    if not m:
        return {}
    raw = m.group(1)

    # We need the players{} block specifically — use regex to pull each player line
    # Format:  123: { rank:1, pts:9750, ytd:2950, rankMove:0, results:{ ... } },
    players = {}
    player_pat = re.compile(
        r'(\d+)\s*:\s*\{\s*rank\s*:\s*(\d+)\s*,\s*pts\s*:\s*(\d+)\s*,\s*'
        r'ytd\s*:\s*(\d+)\s*,\s*rankMove\s*:\s*(-?\d+)\s*,\s*results\s*:\s*'
        r'(\{[^}]*\})\s*\}'
    )
    for pm in player_pat.finditer(raw):
        pid   = int(pm.group(1))
        rank  = int(pm.group(2))
        pts   = int(pm.group(3))
        ytd   = int(pm.group(4))
        rmove = int(pm.group(5))
        res_raw = pm.group(6)
        # Parse results sub-object:  { ao26:{r:"W",pts:2000}, ... }
        results = {}
        res_pat = re.compile(r'(\w+)\s*:\s*\{[^}]*r\s*:\s*"([^"]+)"[^}]*pts\s*:\s*(\d+)[^}]*\}')
        for rm in res_pat.finditer(res_raw):
            results[rm.group(1)] = {'r': rm.group(2), 'pts': int(rm.group(3))}
        players[pid] = {'rank': rank, 'pts': pts, 'ytd': ytd,
                        'rankMove': rmove, 'results': results}

    # Parse activeTournaments array — simplified: look for id:"xxx" + stage:"xxx"
    active = []
    act_pat = re.compile(r'id\s*:\s*"(\w+)"[^}]*stage\s*:\s*"([^"]+)"')
    for am in act_pat.finditer(raw):
        active.append({'id': am.group(1), 'stage': am.group(2), 'players': {}})

    return {'players': players, 'activeTournaments': active}

# ─── Raw data loaders ─────────────────────────────────────────────────────────

def load_raw(filename: str) -> list:
    path = RAW_DIR / filename
    if not path.exists():
        print(f"  WARN: {path} not found — skipping", file=sys.stderr)
        return []
    with open(path) as f:
        return json.load(f)

# ─── Core update logic ────────────────────────────────────────────────────────

def update_tour(tour: str, dry_run: bool = False):
    """
    tour: 'atp' or 'wta'
    """
    print(f"\n{'='*50}")
    print(f"  Updating {tour.upper()} season data")
    print(f"{'='*50}")

    # Load static bio data
    bio_var = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    bio_file = DATA_DIR / f'players_{tour}.js'
    players_bio = parse_js_array(bio_file.read_text(encoding='utf-8'), bio_var)
    if not players_bio:
        print(f"  ERROR: could not parse {bio_file}", file=sys.stderr)
        return False
    print(f"  Loaded {len(players_bio)} player bios")

    # Load existing season data (to preserve results history)
    season_var  = 'SEASON_ATP' if tour == 'atp' else 'SEASON_WTA'
    season_file = DATA_DIR / f'season_{tour}.js'
    existing    = parse_js_season(season_file.read_text(encoding='utf-8'), season_var)
    old_players = existing.get('players', {})
    print(f"  Loaded {len(old_players)} existing season entries")

    # Build name → id index
    name_idx = build_name_index(players_bio)
    id_set   = {p['id'] for p in players_bio}

    # Load scraped rankings (rank + T12M pts)
    rankings_raw = load_raw(f'{tour}_rankings.json')
    # Load scraped race (YTD pts)
    race_raw     = load_raw(f'{tour}_race.json')

    # Build lookup maps: id → {rank, pts}  and  id → {ytd}
    rank_map = {}  # id -> {rank, pts, activeTournament, activeRound}
    for row in rankings_raw:
        pid = match_name(row.get('name', ''), name_idx)
        if pid and pid in id_set:
            rank_map[pid] = {
                'rank':             row.get('rank', 0),
                'pts':              row.get('pts', 0),
                'activeTournament': row.get('activeTournament'),
                'activeRound':      row.get('activeRound'),
            }

    ytd_map = {}   # id -> ytd pts
    for row in race_raw:
        pid = match_name(row.get('name', ''), name_idx)
        if pid and pid in id_set:
            ytd_map[pid] = row.get('ytd', 0)

    matched_rank = len(rank_map)
    matched_ytd  = len(ytd_map)
    print(f"  Matched {matched_rank}/{len(rankings_raw)} ranking rows")
    print(f"  Matched {matched_ytd}/{len(race_raw)} race rows")

    # Build updated players dict
    new_players = {}
    for p in sorted(players_bio, key=lambda x: x['id']):
        pid = p['id']
        old = old_players.get(pid, {})

        new_rank  = rank_map.get(pid, {}).get('rank',  old.get('rank',  pid))
        new_pts   = rank_map.get(pid, {}).get('pts',   old.get('pts',   0))
        new_ytd   = ytd_map.get(pid, old.get('ytd', 0))
        old_rank  = old.get('rank', new_rank)
        rank_move = old_rank - new_rank   # positive = moved up

        # Preserve existing results history unchanged
        results = dict(old.get('results', {}))

        new_players[pid] = {
            'rank':      new_rank,
            'pts':       new_pts,
            'ytd':       new_ytd,
            'rankMove':  rank_move,
            'results':   results,
        }

    # Detect active tournaments from rankings page (ATP inline data)
    # Only ATP rankings page includes inline tournament status
    active_tourns = []
    if tour == 'atp' and rankings_raw:
        tourn_players = {}
        for row in rankings_raw:
            if row.get('activeTournament') and row.get('activeRound'):
                pid  = match_name(row['name'], name_idx)
                tkey = row['activeTournament'].lower().replace(' ', '')
                if pid:
                    if tkey not in tourn_players:
                        tourn_players[tkey] = {}
                    tourn_players[tkey][pid] = {'r': row['activeRound'], 'elim': False}
        # Map short names to tournament IDs using simple heuristics
        TOURN_ALIASES = {
            'houston':    'houston26',
            'marrakech':  'marrakech26',
            'madrid':     'madrid26',
            'rome':       'rome26',
            'roland':     'rg26',
            'rolandgarros':'rg26',
            'wimbledon':  'wimbledon26',
            'montreal':   'montreal26',
            'toronto':    'toronto26',
            'cincinnati': 'cincinnati26',
            'usopen':     'uso26',
            'shanghai':   'shanghai26',
        }
        for tkey, tp in tourn_players.items():
            tid = TOURN_ALIASES.get(tkey, tkey + '26')
            active_tourns.append({'id': tid, 'players': tp})

    # For WTA, preserve existing activeTournaments from current season file
    if tour == 'wta':
        active_tourns = existing.get('activeTournaments', [])

    # ─── Render output JS ──────────────────────────────────────────────────
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    today = datetime.now().strftime('%Y-%m-%d')

    lines = []
    lines.append(f'// season_{tour}.js — {tour.upper()} season data. AUTO-UPDATED by scripts/update_season.py')
    lines.append(f'// Do not edit manually — changes will be overwritten on next update run.')
    lines.append(f'// Last updated: {now}')
    lines.append(f'// Source: {"atptour.com/rankings/singles/live + atptour.com/rankings/race" if tour=="atp" else "wtatennis.com/rankings/singles + wtatennis.com/rankings/race-singles"}')
    lines.append('')

    var_name = 'SEASON_ATP' if tour == 'atp' else 'SEASON_WTA'
    lines.append(f'const {var_name} = {{')
    lines.append(f'  lastUpdated: "{today}",')

    # activeTournaments
    lines.append('  activeTournaments: [')
    for at in active_tourns:
        tid = at['id']
        ap  = at.get('players', {})
        stage = at.get('stage', '')
        lines.append(f'    {{')
        lines.append(f'      id: "{tid}",')
        if stage:
            lines.append(f'      stage: "{stage}",')
        lines.append(f'      players: {{')
        for ppid, pdata in ap.items():
            r    = pdata.get('r', '')
            elim = 'true' if pdata.get('elim') else 'false'
            lines.append(f'        {ppid}: {{ r:"{r}", elim:{elim} }},')
        lines.append(f'      }}')
        lines.append(f'    }},')
    lines.append('  ],')

    # players
    lines.append('  players: {')
    for pid in sorted(new_players.keys()):
        p = new_players[pid]
        results_parts = []
        for tid, rdata in sorted(p['results'].items()):
            results_parts.append(f'{tid}:{{r:"{rdata["r"]}",pts:{rdata["pts"]}}}')
        results_str = ', '.join(results_parts)
        rm = p['rankMove']
        lines.append(
            f'    {pid:3d}: {{ rank:{p["rank"]:<4}, pts:{p["pts"]:<7}, '
            f'ytd:{p["ytd"]:<6}, rankMove:{rm:<4}, '
            f'results:{{ {results_str} }} }},'
        )
    lines.append('  }')
    lines.append('};')
    lines.append('')

    output = '\n'.join(lines)

    if dry_run:
        print(f"\n  [DRY RUN] Would write {len(output)} chars to {season_file}")
        print(f"  Sample (first 500 chars):\n{output[:500]}")
    else:
        season_file.write_text(output, encoding='utf-8')
        print(f"  ✓ Wrote {season_file} ({len(output):,} chars)")

    # Print match quality report
    unmatched = [p['id'] for p in players_bio if p['id'] not in rank_map]
    if unmatched:
        print(f"  Unmatched players (no ranking data): {unmatched[:10]}{'...' if len(unmatched)>10 else ''}")

    return True

# ─── Entrypoint ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Update ATP/WTA season JS files from scraped data')
    parser.add_argument('--tour', choices=['atp','wta','both'], default='both',
                        help='Which tour to update (default: both)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print output without writing files')
    parser.add_argument('--print-js', action='store_true',
                        help='Print the JS extraction snippets and exit')
    args = parser.parse_args()

    if args.print_js:
        for key, info in JS_EXTRACTIONS.items():
            print(f"\n{'─'*60}")
            print(f"  {key.upper()}")
            print(f"  URL: {info['url']}")
            print(f"  Note: {info['note']}")
            print(f"  JS:\n{info['js']}")
        return

    # Ensure raw dir exists
    RAW_DIR.mkdir(exist_ok=True)

    tours = ['atp', 'wta'] if args.tour == 'both' else [args.tour]
    for t in tours:
        update_tour(t, dry_run=args.dry_run)

    print("\n✓ Done")

if __name__ == '__main__':
    main()
