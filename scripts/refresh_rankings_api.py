#!/usr/bin/env python3
"""
refresh_rankings_api.py — Replaces the Chrome-based rankings scraper.

Calls Matchstat's /ranking/singles endpoint (T12M + race) for both tours,
joins the results to player IDs in PLAYERS_*.js, and writes season_*.js.

Preserves:
  - activeTournaments block (the API doesn't expose draw status; that comes from a separate WTA scrape)
  - per-player results{} history

USAGE
-----
    python3 scripts/refresh_rankings_api.py [--tour atp|wta|both] [--dry-run]

ENV
    MATCHSTAT_API_KEY    (loaded from .env)
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Local module
sys.path.insert(0, str(Path(__file__).parent))
from api_client import MatchstatClient  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR  = REPO_ROOT / "data"
TOP_N     = 200             # how many ranks to pull
BASELINE_TTL_DAYS = 7       # how often the rank-movement baseline rolls forward


# ─── Reused helpers (lifted from update_season.py) ───────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', ascii_str.lower().strip())


def build_name_index(players_list: list) -> dict:
    """{normalized_name: player_id} with abbreviation + last-name + initial-last variants."""
    idx = {}
    for p in players_list:
        pid = p['id']
        idx[normalize_name(p['name'])] = pid
        if p.get('ab'):
            idx[normalize_name(p['ab'])] = pid
        parts = p['name'].split()
        if parts:
            idx[normalize_name(parts[-1])] = pid
        if len(parts) >= 2:
            idx[normalize_name(f"{parts[0][0]}. {parts[-1]}")] = pid
    return idx


def match_name(raw: str, idx: dict) -> int | None:
    norm = normalize_name(raw)
    if norm in idx:
        return idx[norm]
    parts = norm.split()
    if parts and parts[-1] in idx:
        return idx[parts[-1]]
    return None


def parse_js_array(js_text: str, var_name: str) -> list:
    """Parse PLAYERS_ATP / PLAYERS_WTA — convert JS object literal to JSON."""
    pattern = rf'const\s+{var_name}\s*=\s*(\[[\s\S]*?\]);'
    m = re.search(pattern, js_text)
    if not m: return []
    raw = m.group(1)
    raw = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    raw = re.sub(r':\s*\.(\d)', r': 0.\1', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  WARN: parse error {var_name}: {e}", file=sys.stderr)
        return []


# ─── Rank-move baseline (persisted weekly snapshot) ─────────────────────────
#
# Why: refresh_tour() used to compute rankMove by comparing current ranks
# against the existing season_*.js (which we then overwrite). That makes the
# diff window equal to "time between consecutive runs" — usually <24h, so the
# delta was always 0 and the dashboard's Movers / Biggest Riser stats were
# meaningless. The fix: keep a separate baseline JSON that only rolls forward
# every BASELINE_TTL_DAYS days, giving a true week-over-week comparison
# regardless of how often the script runs.

def baseline_path(tour: str) -> Path:
    return DATA_DIR / f'rank_baseline_{tour}.json'


def load_baseline(tour: str) -> dict:
    """Return {'date': 'YYYY-MM-DD', 'ranks': {bioId(int): rank(int)}} or {}."""
    p = baseline_path(tour)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        return {
            'date':  raw.get('date', ''),
            'ranks': {int(k): int(v) for k, v in (raw.get('ranks') or {}).items()},
        }
    except (json.JSONDecodeError, ValueError):
        return {}


def save_baseline(tour: str, ranks: dict, date_str: str) -> None:
    p = baseline_path(tour)
    payload = {
        'date':  date_str,
        'ranks': {str(k): int(v) for k, v in ranks.items() if v is not None},
        '_note': 'Rank-move baseline — rolls forward every BASELINE_TTL_DAYS days. Not edited by hand.',
    }
    p.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def baseline_is_stale(date_str: str) -> bool:
    if not date_str:
        return True
    try:
        baseline_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return True
    return (datetime.now().date() - baseline_date).days >= BASELINE_TTL_DAYS


def parse_js_season(js_text: str, var_name: str) -> dict:
    """Extract existing season_*.js to preserve results{} + activeTournaments."""
    pattern = rf'const\s+{var_name}\s*=\s*(\{{[\s\S]*?\}});\s*$'
    m = re.search(pattern, js_text, re.MULTILINE)
    if not m: return {}
    raw = m.group(1)

    players = {}
    pat = re.compile(
        r'(\d+)\s*:\s*\{\s*rank\s*:\s*(\d+)\s*,\s*pts\s*:\s*(\d+)\s*,\s*'
        r'ytd\s*:\s*(\d+)\s*,\s*rankMove\s*:\s*(-?\d+)\s*,\s*results\s*:\s*'
        r'(\{[^}]*\})\s*\}'
    )
    for pm in pat.finditer(raw):
        pid = int(pm.group(1))
        results = {}
        rpat = re.compile(r'(\w+)\s*:\s*\{[^}]*r\s*:\s*"([^"]+)"[^}]*pts\s*:\s*(\d+)[^}]*\}')
        for rm in rpat.finditer(pm.group(6)):
            results[rm.group(1)] = {'r': rm.group(2), 'pts': int(rm.group(3))}
        players[pid] = {
            'rank': int(pm.group(2)), 'pts': int(pm.group(3)),
            'ytd': int(pm.group(4)), 'rankMove': int(pm.group(5)),
            'results': results,
        }

    # Preserve activeTournaments block raw — slot back into output unchanged
    at_match = re.search(r'(activeTournaments:\s*\[[\s\S]*?\],)', raw)
    active_raw = at_match.group(1) if at_match else 'activeTournaments: [\n  ],'
    return {'players': players, 'activeTournamentsRaw': active_raw}


def derive_active_tournaments(tour: str) -> str:
    """Build the activeTournaments JS block by reading tournaments.js for any
    entry where active:true and tour matches (or 'BOTH'). This means we don't
    need to manually edit season_*.js when a new event starts — just flip the
    `active` flag in tournaments.js and the next pipeline run picks it up.

    Stage defaults to 'R32' as a sensible mid-tournament guess; the JS-side
    enrichActiveTournaments() in wta_analytics.html computes the real per-player
    status from RECENT_MATCHES anyway, so stage is mostly cosmetic.
    """
    tour_upper = tour.upper()
    text = (REPO_ROOT / 'data' / 'tournaments.js').read_text(encoding='utf-8')
    # Find every {...} block in TOURNAMENTS_DATA where active:true
    entries = []
    for m in re.finditer(r'\{[^{}]*?id:"([^"]+)"[^{}]*?active:true[^{}]*?\}', text):
        block = m.group(0)
        tid = m.group(1)
        # Pull `tour:` field from the block
        tour_match = re.search(r'tour:"([^"]+)"', block)
        if not tour_match: continue
        t_val = tour_match.group(1)
        if t_val != 'BOTH' and t_val != tour_upper: continue
        entries.append(tid)
    if not entries:
        return 'activeTournaments: [\n  ],'
    lines = ['activeTournaments: [']
    for tid in entries:
        lines += [
            '    {',
            f'      id: "{tid}",',
            f'      stage: "R32",',
            f'      players: {{',
            f'      }}',
            '    },',
        ]
    lines.append('  ],')
    return '\n'.join(lines)


# ─── Core refresh ────────────────────────────────────────────────────────────

def refresh_tour(client: MatchstatClient, tour: str, dry_run: bool) -> bool:
    print(f"\n{'='*54}")
    print(f"  Refreshing {tour.upper()} rankings (Matchstat API)")
    print(f"{'='*54}")

    # 1. Bios + existing season
    bio_var = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    bio_file = DATA_DIR / f'players_{tour}.js'
    bios = parse_js_array(bio_file.read_text(encoding='utf-8'), bio_var)
    if not bios:
        print(f"  ERROR: failed to parse {bio_file}", file=sys.stderr)
        return False
    print(f"  {len(bios)} player bios loaded")

    season_var  = 'SEASON_ATP' if tour == 'atp' else 'SEASON_WTA'
    season_file = DATA_DIR / f'season_{tour}.js'
    existing = parse_js_season(season_file.read_text(encoding='utf-8'), season_var)
    old_players = existing.get('players', {})
    active_raw  = existing.get('activeTournamentsRaw', 'activeTournaments: [\n  ],')

    # Auto-derive activeTournaments from tournaments.js (any active:true entry
    # for this tour). Replaces the existing block — keeps the tour in sync with
    # the curated tournament metadata without manual season_*.js editing.
    derived = derive_active_tournaments(tour)
    derived_count = derived.count('id:')
    if derived_count > 0:
        active_raw = derived
        print(f"  Auto-derived activeTournaments: {derived_count} event(s) flagged active in tournaments.js")
    else:
        existing_count = active_raw.count('id:')
        print(f"  No tournaments.js entries with active:true for {tour.upper()} — keeping existing ({existing_count} event(s))")

    name_idx = build_name_index(bios)
    id_set   = {p['id'] for p in bios}
    # mid → bioId map for exact-ID matching (set by scripts/link_bios_to_api.py).
    # Falls back to name_idx for bios that haven't been linked yet.
    mid_to_bio_id = {p['mid']: p['id'] for p in bios if p.get('mid')}
    print(f"  {len(mid_to_bio_id)} bios have linked Matchstat IDs (mid)")

    # 2. Live T12M rankings
    print(f"  → calling /ranking/singles (top {TOP_N})…")
    t12m_raw = client.get(tour, 'ranking/singles', {'pageSize': TOP_N})
    t12m_rows = (t12m_raw or {}).get('data') or t12m_raw or []
    print(f"    received {len(t12m_rows)} ranking rows")

    # 3. Race / YTD rankings
    print(f"  → calling /ranking/singles?race=true …")
    race_raw = client.get(tour, 'ranking/singles', {'pageSize': TOP_N, 'race': 'true'})
    race_rows = (race_raw or {}).get('data') or race_raw or []
    print(f"    received {len(race_rows)} race rows")

    # 4. Build maps keyed by player ID in OUR bios.
    # NOTE: Matchstat returns WTA T12M points scaled ×100 (e.g. Sabalenka returns
    # 1,089,500 for the 10,895 standard value). ATP T12M is correct as-is. Race
    # points are correct on both tours.
    pts_divisor = 100 if tour == 'wta' else 1

    def resolve_bio_id(player_obj: dict) -> int | None:
        """Match an API player to a bio: prefer mid (exact), else fuzzy name."""
        api_pid = player_obj.get('id')
        if api_pid and api_pid in mid_to_bio_id:
            return mid_to_bio_id[api_pid]
        # Fallback for un-linked bios
        bio_id = match_name(player_obj.get('name', ''), name_idx)
        return bio_id if bio_id in id_set else None

    rank_map = {}  # bioId -> {rank, pts}
    for r in t12m_rows:
        bio_id = resolve_bio_id(r.get('player') or {})
        if bio_id is None:
            continue
        raw_pts = r.get('point') or r.get('points') or 0
        rank_map[bio_id] = {'rank': r.get('position') or r.get('currentRank'),
                            'pts':  round(raw_pts / pts_divisor)}

    ytd_map = {}  # bioId -> ytd points
    for r in race_rows:
        bio_id = resolve_bio_id(r.get('player') or {})
        if bio_id is not None:
            ytd_map[bio_id] = r.get('racePoints') or r.get('points') or 0

    print(f"  matched {len(rank_map)}/{len(t12m_rows)} ranking rows to bios")
    print(f"  matched {len(ytd_map)}/{len(race_rows)} race rows to bios")

    # 5. Load (or initialize) the rank-move baseline.
    baseline = load_baseline(tour)
    today    = datetime.now().date().strftime('%Y-%m-%d')
    if not baseline:
        # First run with this script: seed the baseline from EXISTING season_*.js
        # so we have a starting reference. rankMove will be 0 until the baseline
        # rolls forward in BASELINE_TTL_DAYS days.
        seed_ranks = {pid: o.get('rank', pid) for pid, o in old_players.items()}
        save_baseline(tour, seed_ranks, today)
        baseline = {'date': today, 'ranks': seed_ranks}
        print(f"  Baseline missing → seeded from existing season_*.js ({len(seed_ranks)} bios, dated {today})")
    else:
        age_days = (datetime.now().date() - datetime.strptime(baseline['date'], '%Y-%m-%d').date()).days
        print(f"  Baseline loaded: {len(baseline['ranks'])} bios, dated {baseline['date']} ({age_days}d old)")

    # 6. Compose new player block, preserving results history.
    # rankMove = baseline_rank - current_rank (positive = moved up).
    new_players = {}
    for p in sorted(bios, key=lambda x: x['id']):
        pid = p['id']
        old = old_players.get(pid, {})
        new_rank = rank_map.get(pid, {}).get('rank') or old.get('rank') or pid
        new_pts  = rank_map.get(pid, {}).get('pts')  or old.get('pts') or 0
        new_ytd  = ytd_map.get(pid, old.get('ytd', 0))
        baseline_rank = baseline['ranks'].get(pid, new_rank)
        rank_move = baseline_rank - new_rank
        new_players[pid] = {
            'rank': new_rank, 'pts': new_pts, 'ytd': new_ytd,
            'rankMove': rank_move, 'results': dict(old.get('results', {})),
        }

    # 7. Roll baseline forward if it's stale.
    if baseline_is_stale(baseline.get('date', '')):
        save_baseline(tour, {pid: p['rank'] for pid, p in new_players.items()}, today)
        print(f"  Baseline was {BASELINE_TTL_DAYS}+ days old → rolled forward to {today}")
    else:
        # Show a sample of computed moves so user can confirm it's working.
        movers = sorted(new_players.items(), key=lambda kv: -abs(kv[1]['rankMove']))[:5]
        nonzero = [kv for kv in movers if kv[1]['rankMove'] != 0]
        if nonzero:
            sample = ', '.join(f"#{pid}:{'+' if p['rankMove']>0 else ''}{p['rankMove']}"
                                for pid, p in nonzero)
            print(f"  Top movers vs baseline: {sample}")
        else:
            print(f"  No rank movement vs baseline yet (will refresh in {BASELINE_TTL_DAYS}-day cycles)")

    # 8. Render JS
    now    = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    today  = datetime.now().strftime('%Y-%m-%d')
    var    = 'SEASON_ATP' if tour == 'atp' else 'SEASON_WTA'
    lines  = [
        f'// season_{tour}.js — {tour.upper()} season data. AUTO-UPDATED by scripts/refresh_rankings_api.py',
        f'// Source: Matchstat Tennis API (RapidAPI). Do not edit manually.',
        f'// Last updated: {now}',
        '',
        f'const {var} = {{',
        f'  lastUpdated: "{today}",',
        f'  ' + active_raw.replace('\n', '\n  '),
        f'  players: {{',
    ]
    for pid in sorted(new_players.keys()):
        p = new_players[pid]
        results_parts = [f'{tid}:{{r:"{rd["r"]}",pts:{rd["pts"]}}}'
                         for tid, rd in sorted(p['results'].items())]
        results_str = ', '.join(results_parts)
        lines.append(
            f'    {pid:3d}: {{ rank:{p["rank"]:<4}, pts:{p["pts"]:<7}, '
            f'ytd:{p["ytd"]:<6}, rankMove:{p["rankMove"]:<4}, '
            f'results:{{ {results_str} }} }},'
        )
    lines.append('  }')
    lines.append('};')
    lines.append('')
    output = '\n'.join(lines)

    if dry_run:
        print(f"\n  [DRY RUN] would write {len(output):,} chars to {season_file}")
        print(f"  Sample (first 800 chars):\n{output[:800]}")
    else:
        season_file.write_text(output, encoding='utf-8')
        print(f"\n  ✓ Wrote {season_file} ({len(output):,} chars)")

    return True


def main():
    p = argparse.ArgumentParser(description='Refresh season_*.js from Matchstat API')
    p.add_argument('--tour', choices=['atp', 'wta', 'both'], default='both')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    client = MatchstatClient()
    print(f"Matchstat API @ {client.host}")
    print(f"API key: {client.api_key[:6]}…{client.api_key[-4:]}")

    tours = ['atp', 'wta'] if args.tour == 'both' else [args.tour]
    for t in tours:
        refresh_tour(client, t, args.dry_run)

    print("\n✓ Done")


if __name__ == '__main__':
    main()
