#!/usr/bin/env python3
"""
recompute_trapezoid.py — Rebuild data/trapezoid_data.js from Sackmann CSVs.

Reads scripts/cache/atp_matches_YYYY.csv and wta_matches_YYYY.csv (one per
year) that were fetched separately, computes per-player trapezoid metrics,
and writes a year-tagged data/trapezoid_data.js consumed by trapezoid.html.

Source: github.com/JeffSackmann/tennis_atp & tennis_wta (CC BY-NC-SA 4.0).

USAGE
-----
    # 1. Fetch CSVs into scripts/cache/  (handled separately — see SKILL.md)
    # 2. Run the recompute:
    python3 scripts/recompute_trapezoid.py [--years 2024 2025 2026]

METRIC DEFINITIONS
------------------
Excludes W/O, RET, DEF (not full matches). Min 5 matches per player.

  servePtsWonPct   = (1stWon + 2ndWon) / svpt
  returnPtsWonPct  = (opp_svpt − opp_1stWon − opp_2ndWon) / opp_svpt
  totalPtsWonPct   = combined: own service points won + opponent points lost on
                     their serve, all divided by total points contested
  acesPerSvGm      = aces / service games
  bpSavedPct       = bp saved / bp faced
  matchWinPct      = wins / matches
  tbWinPct         = tiebreaks won / tiebreaks played   (NULL if <3 played)
  decSetWinPct     = match wins WHEN the match went the distance (Bo3 → 3-set
                     match, Bo5 → 5-set match), divided by # such matches.
                     A 3-set Bo5 win at a Slam does NOT count as a decider.
                     NULL if <3 deciding matches played.

NOTE: this `decSetWinPct` definition differs slightly from the original 2024
baseline (which counted any 3-set match). After running this script, expect
decSetWinPct values to shift downward for ATP players with many Slam matches.
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT  = SCRIPT_DIR.parent
CACHE_DIR  = SCRIPT_DIR / "cache"
DATA_DIR   = REPO_ROOT / "data"
DATA_FILE  = DATA_DIR / "trapezoid_data.js"


def load_sid_to_bio_id(tour: str) -> dict:
    """Build {sackmann_player_id: bio.id} map from PLAYERS_*.js.

    Bios linked via scripts/link_bios_to_sackmann.py have a `sid:"<id>"` field.
    Lets us tag trapezoid rows with bio.id so Sackmann 2024 + Matchstat 2025+
    data unify on the same canonical key.
    """
    bio_var = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    text = (DATA_DIR / f'players_{tour}.js').read_text(encoding='utf-8')
    pat = re.compile(rf'\{{\s*id:\s*(\d+)[^}}]*?sid:\s*"([^"]+)"')
    return {sid: int(bio_id) for bio_id, sid in pat.findall(text)}

# Match outcomes to skip (walkovers / retirements / defaults aren't real matches)
SKIP_SCORE_PATTERNS = (" RET", " W/O", " DEF", "W/O", "RET", "DEF")
MIN_MATCHES = 5  # Floor — UI offers 10/20/30/50 but compute keeps anyone with 5+


def is_real_match(score: str) -> bool:
    """Drop W/O, RET, DEF results."""
    if not score:
        return False
    s = score.upper()
    return not any(p.upper() in s for p in SKIP_SCORE_PATTERNS)


def safe_int(v):
    try:
        return int(v) if v not in (None, '', 'NA') else 0
    except (ValueError, TypeError):
        return 0


def safe_pct(num, denom):
    return round(100 * num / denom, 1) if denom > 0 else None


# Sackmann score notation:
#   "7-6(3) 6-4"      → set 1 went to a tiebreak, loser took 3 pts in the TB; set 2 ended 6-4
#   "6-2 6-7(5) 7-5"  → 3-set match, second set was a TB
# The number inside parentheses is just the LOSER'S TB points (winner reached 7+).
# So the regex captures `(\d+)-(\d+)(?:\((\d+)\))?` per set.
SET_RE = re.compile(r'(\d+)-(\d+)(?:\((\d+)\))?')


def aggregate_player(rows: list) -> dict:
    """rows: [{role:'W'|'L', match dict}] — one entry per match the player played."""
    matches = len(rows)
    wins = sum(1 for r in rows if r['role'] == 'W')

    svpt = first_in = first_won = second_won = sv_gms = aces = bp_saved = bp_faced = 0
    opp_svpt = opp_first_won = opp_second_won = 0

    tb_played = tb_won = 0
    dec_played = dec_won = 0

    for r in rows:
        role = r['role']
        m = r['m']
        prefix = 'w_' if role == 'W' else 'l_'
        opp_prefix = 'l_' if role == 'W' else 'w_'

        svpt        += safe_int(m.get(prefix + 'svpt'))
        first_in    += safe_int(m.get(prefix + '1stIn'))
        first_won   += safe_int(m.get(prefix + '1stWon'))
        second_won  += safe_int(m.get(prefix + '2ndWon'))
        sv_gms      += safe_int(m.get(prefix + 'SvGms'))
        aces        += safe_int(m.get(prefix + 'ace'))
        bp_saved    += safe_int(m.get(prefix + 'bpSaved'))
        bp_faced    += safe_int(m.get(prefix + 'bpFaced'))

        opp_svpt       += safe_int(m.get(opp_prefix + 'svpt'))
        opp_first_won  += safe_int(m.get(opp_prefix + '1stWon'))
        opp_second_won += safe_int(m.get(opp_prefix + '2ndWon'))

        # Parse sets — each set yields (winner_games, loser_games, optional_tb_loser_pts).
        # A set with `tb_pts is not None` was decided by a tiebreak.
        score = m.get('score', '') or ''
        parsed_sets = []
        for s in SET_RE.findall(score):
            wgames, lgames, tb_pts = s
            wg, lg = int(wgames), int(lgames)
            # Sanity: at least one side reached 6+ games (drop bogus matches like "0-0 RET")
            if max(wg, lg) < 6:
                continue
            parsed_sets.append((wg, lg, tb_pts))

        for (wg, lg, tb_pts) in parsed_sets:
            if tb_pts:
                tb_played += 1
                # In the CSV row, "wgames" is the MATCH winner's games for that set.
                # The TB winner is whoever won the set (had more games).
                set_match_winner_won_set = wg > lg
                set_winner_was_this_player = (
                    (role == 'W' and set_match_winner_won_set) or
                    (role == 'L' and not set_match_winner_won_set)
                )
                if set_winner_was_this_player:
                    tb_won += 1

        # decSetWinPct: % of matches won when the match went the distance.
        # Distance = max possible sets given best_of (3 or 5).
        # For a Bo5 slam that ends in 3 straight sets, that's NOT a decider — only 5-set Bo5 is.
        # Without best_of we conservatively skip. With best_of we count.
        n_sets = len(parsed_sets)
        best_of = safe_int(m.get('best_of'))
        if best_of in (3, 5) and n_sets >= best_of:
            dec_played += 1
            if role == 'W':
                dec_won += 1

    return {
        'matches':           matches,
        'matchWinPct':       round(100 * wins / matches, 1) if matches else None,
        'servePtsWonPct':    safe_pct(first_won + second_won, svpt),
        'returnPtsWonPct':   safe_pct(opp_svpt - opp_first_won - opp_second_won, opp_svpt),
        'totalPtsWonPct':    safe_pct(
                               (first_won + second_won) + (opp_svpt - opp_first_won - opp_second_won),
                               svpt + opp_svpt
                             ),
        'acesPerSvGm':       round(aces / sv_gms, 2) if sv_gms else None,
        'bpSavedPct':        safe_pct(bp_saved, bp_faced),
        'tbWinPct':          safe_pct(tb_won, tb_played) if tb_played >= 3 else None,
        'decSetWinPct':      safe_pct(dec_won, dec_played) if dec_played >= 3 else None,
    }


def process_year(tour: str, year: int) -> list:
    """Return [{id, bioId, name, ioc, year, tour, ...metrics}, ...] for one year/tour.
    `id` is Sackmann's player_id (preserved for traceability).
    `bioId` is our bio.id from PLAYERS_*.js when linked, else null.
    """
    csv_path = CACHE_DIR / f"{tour}_matches_{year}.csv"
    if not csv_path.exists():
        print(f"  WARN: {csv_path.name} missing — skipping", file=sys.stderr)
        return []

    sid_to_bio = load_sid_to_bio_id(tour)
    print(f"    loaded {len(sid_to_bio)} sid→bio.id mappings for {tour.upper()}")

    by_player = defaultdict(list)  # id → list of {role, m}
    bio = {}                       # id → {name, ioc}

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for m in reader:
            score = m.get('score', '')
            if not is_real_match(score):
                continue
            for role, prefix in (('W', 'winner'), ('L', 'loser')):
                pid  = m.get(f'{prefix}_id')
                name = m.get(f'{prefix}_name', '').strip()
                ioc  = m.get(f'{prefix}_ioc', '').strip()
                if not pid or not name:
                    continue
                by_player[pid].append({'role': role, 'm': m})
                if pid not in bio:
                    bio[pid] = {'name': name, 'ioc': ioc}

    rows = []
    for pid, pmatches in by_player.items():
        if len(pmatches) < MIN_MATCHES:
            continue
        agg = aggregate_player(pmatches)
        rows.append({
            'id':    pid,
            'bioId': sid_to_bio.get(pid),  # null when bio not linked (lower-ranked / new players)
            'name':  bio[pid]['name'],
            'ioc':  bio[pid]['ioc'],
            'year': year,
            'tour': tour.upper(),
            **agg,
        })
    # Sort by total pts won (the "trapezoid" anchor metric) desc; null at end
    rows.sort(key=lambda r: (r.get('totalPtsWonPct') or -1), reverse=True)
    return rows


def render_js(years: list, atp_by_year: dict, wta_by_year: dict) -> str:
    """Build the new trapezoid_data.js file."""
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    lines = []
    lines.append('/**')
    lines.append(' * trapezoid_data.js — AUTO-GENERATED by scripts/recompute_trapezoid.py')
    lines.append(' * Do not edit manually; rerun the recompute script.')
    lines.append(f' * Last updated: {now}')
    lines.append(f' * Years included: {", ".join(str(y) for y in years)}')
    lines.append(' *')
    lines.append(' * Data source: github.com/JeffSackmann/tennis_atp & tennis_wta')
    lines.append(' *              Licensed under CC BY-NC-SA 4.0. Derivative work inherits the license.')
    lines.append(' *')
    lines.append(' * Schema: each row = one player\'s aggregated metrics for one season.')
    lines.append(' *   {id, name, ioc, year, tour, matches, matchWinPct,')
    lines.append(' *    servePtsWonPct, returnPtsWonPct, totalPtsWonPct,')
    lines.append(' *    acesPerSvGm, bpSavedPct, tbWinPct, decSetWinPct}')
    lines.append(' */')
    lines.append('')
    lines.append(f'const TRAPEZOID_YEARS = {json.dumps(years)};')
    lines.append('')

    # ATP rows (all years combined) — UI filters by year
    atp_rows = []
    for y in years:
        atp_rows.extend(atp_by_year.get(y, []))
    lines.append('const TRAPEZOID_ATP = ' + json.dumps(atp_rows, separators=(',', ':')) + ';')
    lines.append('')

    wta_rows = []
    for y in years:
        wta_rows.extend(wta_by_year.get(y, []))
    lines.append('const TRAPEZOID_WTA = ' + json.dumps(wta_rows, separators=(',', ':')) + ';')
    lines.append('')

    # Metric keys + display labels — kept stable, app reads them
    lines.append("""const TRAPEZOID_METRICS = ['matches','servePtsWonPct','returnPtsWonPct','totalPtsWonPct','tbWinPct','decSetWinPct','acesPerSvGm','bpSavedPct','matchWinPct'];

const TRAPEZOID_LABELS = {
    matches:          'Matches Played',
    servePtsWonPct:   'Serve Points Won %',
    returnPtsWonPct:  'Return Points Won %',
    totalPtsWonPct:   'Total Points Won %',
    tbWinPct:         'Tiebreak Win %',
    decSetWinPct:     'Deciding Set Win %',
    acesPerSvGm:      'Aces / Service Game',
    bpSavedPct:       'Break Points Saved %',
    matchWinPct:      'Match Win %',
};
""")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Rebuild trapezoid_data.js from Sackmann CSVs')
    parser.add_argument('--years', nargs='+', type=int, default=[2024, 2025, 2026])
    parser.add_argument('--out', default=str(DATA_FILE))
    parser.add_argument('--force', action='store_true',
                        help='Write the output file even if no players were aggregated')
    args = parser.parse_args()

    print(f'Recomputing trapezoid metrics for years: {args.years}')
    print(f'Reading CSVs from: {CACHE_DIR}')

    atp_by_year = {}
    wta_by_year = {}
    for y in args.years:
        atp_rows = process_year('atp', y)
        wta_rows = process_year('wta', y)
        print(f'  {y}: ATP {len(atp_rows):4d} players  |  WTA {len(wta_rows):4d} players')
        if atp_rows: atp_by_year[y] = atp_rows
        if wta_rows: wta_by_year[y] = wta_rows

    total = sum(len(v) for v in atp_by_year.values()) + sum(len(v) for v in wta_by_year.values())
    if total == 0 and not args.force:
        print(
            '\nERROR: zero players aggregated across all requested years.\n'
            'Refusing to overwrite the existing trapezoid_data.js with an empty file.\n'
            f'Cached CSVs expected in: {CACHE_DIR}\n'
            'Pass --force to write anyway (e.g. when intentionally clearing the dataset).',
            file=sys.stderr
        )
        return 1

    js = render_js(args.years, atp_by_year, wta_by_year)
    Path(args.out).write_text(js, encoding='utf-8')
    print(f'Wrote {args.out} ({len(js):,} chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
