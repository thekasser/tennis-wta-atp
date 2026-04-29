#!/usr/bin/env python3
"""
fetch_match_stats_api.py — Pull 2025+ match-level stats from Matchstat API,
aggregate into per-player trapezoid rows, and capture last-10 form for the
form-bar hover.

WHY THIS EXISTS
---------------
Sackmann data (`recompute_trapezoid.py`) only covers through 2024. For 2025
and onward we use Matchstat's RapidAPI endpoint, which returns box-score
stats per match. This script:

  1. For each bio with `mid:<n>` in players_*.js, fetches
     /atp|wta/player/past-matches/{mid}?include=stat,round,tournament
     filtered to 2025 and 2026.
  2. Aggregates per (player, year) using the same metric definitions as
     recompute_trapezoid.py. Min 5 matches per player per year.
  3. Writes aggregate JSON files to scripts/cache/aggregates/{tour}_{year}.json
     (consumed by write_trapezoid_from_json.py to merge into trapezoid_data.js).
  4. Writes data/recent_matches.js: last 10 matches per bio (date, tournament,
     opponent, round, score, won/lost) for the form-bar hover in the UI.

USAGE
-----
    # Run locally (sandbox can't reach rapidapi.com — must run on host).
    # Reads MATCHSTAT_API_KEY from .env via api_client.py.
    python3 scripts/fetch_match_stats_api.py [--tour atp|wta|both] \\
        [--years 2025 2026] [--top-n 200] [--dry-run] [--limit N]

    # Then merge new aggregates into trapezoid_data.js:
    python3 scripts/write_trapezoid_from_json.py --years 2024 2025 2026

FIELD MAP (Matchstat → Sackmann-equivalent)
-------------------------------------------
  svpt      = firstServeOf                       (total serve points)
  1stIn     = firstServe
  1stWon    = winningOnFirstServe
  2ndWon    = winningOnSecondServe (else: totalSecondServeWon)
  ace       = aces
  df        = doubleFaults
  bpFaced   = breakPointFaced (else: opp.breakPointsAttempted)
  bpSaved   = breakPointSaved (else: bpFaced − opp.breakPointsConverted)
  SvGms     = NOT IN API — estimated as round(svpt / 6.5)

If your API responses use slightly different keys, log them and extend the
map below. Run with --debug to dump the raw `stat` object of the first match.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
from api_client import MatchstatClient                                   # noqa: E402
from refresh_rankings_api import parse_js_array                          # noqa: E402
from recompute_trapezoid import SET_RE, safe_pct, MIN_MATCHES            # noqa: E402

REPO_ROOT  = Path(__file__).parent.parent
DATA_DIR   = REPO_ROOT / "data"
AGG_DIR    = REPO_ROOT / "scripts" / "cache" / "aggregates"
RECENT_OUT  = DATA_DIR / "recent_matches.js"
HISTORY_OUT = DATA_DIR / "tournament_history.js"
DEFAULT_YEARS = [2025, 2026]
DEFAULT_TOP_N = 200
PAGE_SIZE = 50


# ─── Field accessors (defensive — API field names vary slightly) ─────────────

def _get(d: dict, *keys, default=0):
    """Try multiple keys, return first non-null/non-empty value."""
    for k in keys:
        if k in d and d[k] not in (None, '', 'NA'):
            return d[k]
    return default


def _safe_int(v):
    try:
        return int(v) if v not in (None, '', 'NA') else 0
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0


def extract_match_stats(match: dict, our_mid: int) -> tuple[dict, dict] | None:
    """Return (own_stats, opp_stats) as dicts of integer counters, or None
    if the match has no usable stat block (skip from aggregation)."""
    p1id = _get(match, 'player1Id', 'p1Id')
    p2id = _get(match, 'player2Id', 'p2Id')
    we_are_p1 = (p1id == our_mid)

    stat = match.get('stat') or match.get('stats') or {}
    if not isinstance(stat, dict):
        return None

    # Common shapes:
    #   stat = { 'stat1': {...}, 'stat2': {...} }
    #   stat = { 'p1': {...},    'p2': {...} }
    #   stat = { '1': {...},     '2': {...} }
    s1 = stat.get('stat1') or stat.get('p1') or stat.get('1') or stat.get('player1') or {}
    s2 = stat.get('stat2') or stat.get('p2') or stat.get('2') or stat.get('player2') or {}
    if not s1 and not s2:
        return None

    own = s1 if we_are_p1 else s2
    opp = s2 if we_are_p1 else s1

    if not own:
        return None

    def to_counters(s: dict) -> dict:
        return {
            'svpt':      _safe_int(_get(s, 'firstServeOf', 'totalServePointsAttempted', 'serveOf', 'serveOfGm', 'svpt')),
            '1stIn':     _safe_int(_get(s, 'firstServe', 'firstServeIn', '1stIn')),
            '1stWon':    _safe_int(_get(s, 'winningOnFirstServe', 'firstServeWon', '1stWon')),
            '2ndWon':    _safe_int(_get(s, 'winningOnSecondServe', 'secondServeWon', '2ndWon')),
            'ace':       _safe_int(_get(s, 'aces', 'ace')),
            'df':        _safe_int(_get(s, 'doubleFaults', 'doubleFault', 'df')),
            'bpFaced':   _safe_int(_get(s, 'breakPointFaced', 'breakPointsFaced', 'bpFaced')),
            'bpSaved':   _safe_int(_get(s, 'breakPointSaved', 'breakPointsSaved', 'bpSaved')),
            'bpConv':    _safe_int(_get(s, 'breakPointsConverted', 'breakPointConverted', 'bpConv')),
            # Matchstat: 'breakPointsConvertedOf' = opponent's BP attempts vs me on my serve.
            'bpAttempt': _safe_int(_get(s, 'breakPointsConvertedOf', 'breakPointsAttempted',
                                          'breakPointAttempted', 'bpAttempt')),
        }

    own_c = to_counters(own)
    opp_c = to_counters(opp)

    # Backfill bpFaced/bpSaved using opponent's converted/attempted if API didn't provide direct.
    if not own_c['bpFaced'] and opp_c['bpAttempt']:
        own_c['bpFaced'] = opp_c['bpAttempt']
    if not own_c['bpSaved'] and own_c['bpFaced']:
        own_c['bpSaved'] = own_c['bpFaced'] - opp_c['bpConv']

    return (own_c, opp_c)


def parse_score_for_sets(score_str: str) -> list[tuple[int, int, str | None]]:
    """Reuse Sackmann SET_RE — yields list of (winner_games, loser_games, tb_pts_or_None).
    'winner' here = whoever won the match (NOT necessarily the requested player)."""
    if not score_str:
        return []
    out = []
    for s in SET_RE.findall(score_str):
        wg, lg, tb_pts = s
        wg_i, lg_i = int(wg), int(lg)
        if max(wg_i, lg_i) < 6:
            continue
        out.append((wg_i, lg_i, tb_pts or None))
    return out


def is_real_match(score: str) -> bool:
    if not score:
        return False
    s = score.upper()
    return not any(p in s for p in (' RET', ' W/O', ' DEF', 'W/O', 'RET', 'DEF'))


# ─── Aggregation (mirrors recompute_trapezoid.aggregate_player) ──────────────

def aggregate_year(matches: list[dict], our_mid: int, min_matches: int = MIN_MATCHES) -> dict | None:
    """Return metric dict matching the trapezoid schema, or None if too few matches."""
    real = [m for m in matches if is_real_match(_get(m, 'result', 'score', default=''))]
    if len(real) < min_matches:
        return None

    matches_n = len(real)
    wins = 0
    svpt = first_in = first_won = second_won = aces = bp_saved = bp_faced = 0
    df = 0
    opp_svpt = opp_first_won = opp_second_won = 0
    tb_played = tb_won = 0
    dec_played = dec_won = 0
    sv_gms_estimate = 0  # we'll estimate from svpt÷6.5

    used_for_serve = 0  # count matches that contributed serve stats (some W/O lack stat block)

    for m in real:
        # Matchstat: `match_winner` is the winning player's ID (NOT 1/2).
        # Other APIs sometimes use winnerId / winner_id.
        winner_id = _get(m, 'winnerId', 'winner_id', 'match_winner', 'matchWinner',
                         default=None)
        if winner_id not in (None, 0, ''):
            won = (winner_id == our_mid)
        else:
            won_flag = m.get('won') or m.get('winLoss') or m.get('result_outcome')
            won = won_flag in (True, 'W', 'win', 'won', 1, '1')
        if won:
            wins += 1

        # Stats
        stats = extract_match_stats(m, our_mid)
        if stats:
            own, opp = stats
            svpt        += own['svpt']
            first_in    += own['1stIn']
            first_won   += own['1stWon']
            second_won  += own['2ndWon']
            aces        += own['ace']
            df          += own['df']
            bp_saved    += own['bpSaved']
            bp_faced    += own['bpFaced']
            opp_svpt       += opp['svpt']
            opp_first_won  += opp['1stWon']
            opp_second_won += opp['2ndWon']
            used_for_serve += 1

        # Score-derived: tiebreaks + deciding sets
        score = _get(m, 'result', 'score', default='')
        sets = parse_score_for_sets(score)
        for (wg, lg, tb_pts) in sets:
            if tb_pts is not None:
                tb_played += 1
                # TB winner is whoever won the set (had more games).
                set_match_winner_won_set = wg > lg
                set_winner_was_us = (won and set_match_winner_won_set) or (not won and not set_match_winner_won_set)
                if set_winner_was_us:
                    tb_won += 1

        n_sets = len(sets)
        best_of = _safe_int(m.get('bestOf') or m.get('best_of'))
        if not best_of:
            # Heuristic: if we ever see 4+ sets, must be Bo5; otherwise assume Bo3
            best_of = 5 if n_sets >= 4 else 3
        if n_sets >= best_of:
            dec_played += 1
            if won:
                dec_won += 1

    sv_gms_estimate = round(svpt / 6.5) if svpt else 0

    return {
        'matches':           matches_n,
        'matchWinPct':       round(100 * wins / matches_n, 1) if matches_n else None,
        'servePtsWonPct':    safe_pct(first_won + second_won, svpt),
        'returnPtsWonPct':   safe_pct(opp_svpt - opp_first_won - opp_second_won, opp_svpt),
        'totalPtsWonPct':    safe_pct(
                                (first_won + second_won) + (opp_svpt - opp_first_won - opp_second_won),
                                svpt + opp_svpt
                              ),
        'acesPerSvGm':       round(aces / sv_gms_estimate, 2) if sv_gms_estimate else None,
        'bpSavedPct':        safe_pct(bp_saved, bp_faced),
        'tbWinPct':          safe_pct(tb_won, tb_played) if tb_played >= 3 else None,
        'decSetWinPct':      safe_pct(dec_won, dec_played) if dec_played >= 3 else None,
        '_meta': {
            'svpt':           svpt,
            'matchesWithStats': used_for_serve,
            'tbPlayed':        tb_played,
            'decPlayed':       dec_played,
        },
    }


def build_form_match(m: dict, our_mid: int) -> dict:
    """One row for the form-bar hover."""
    p1id = _get(m, 'player1Id', 'p1Id')
    p2id = _get(m, 'player2Id', 'p2Id')
    we_are_p1 = (p1id == our_mid)
    p_self = m.get('player1') if we_are_p1 else m.get('player2')
    p_opp  = m.get('player2') if we_are_p1 else m.get('player1')
    opp_name = (p_opp or {}).get('name') or (p_opp or {}).get('fullName') or '?'
    # `match_winner` is the winning player's ID in Matchstat.
    winner_id = _get(m, 'winnerId', 'winner_id', 'match_winner', 'matchWinner',
                     default=None)
    won = (winner_id == our_mid) if winner_id not in (None, 0, '') else None

    tour_obj = m.get('tournament') or {}
    # Matchstat returns `round` as a dict {id, name}, not a string.
    rd_obj = m.get('round')
    if isinstance(rd_obj, dict):
        rd_str = rd_obj.get('shortName') or rd_obj.get('name') or ''
    else:
        rd_str = rd_obj or m.get('roundCode') or ''
    return {
        'date':  (m.get('date') or m.get('matchDate') or '')[:10],
        'tn':    tour_obj.get('name') or tour_obj.get('tournamentName') or '',
        'rd':    rd_str,
        'opp':   opp_name,
        'oppC':  (p_opp or {}).get('countryAcr') or (p_opp or {}).get('country') or '',
        'score': _get(m, 'result', 'score', default=''),
        'won':   won,
    }


# ─── Per-player fetcher ──────────────────────────────────────────────────────

def fetch_player_year_matches(client: MatchstatClient, tour: str, mid: int,
                              year: int) -> list[dict]:
    """Fetch ALL matches for a given player+year, paginating until exhausted."""
    matches = []
    page = 1
    while True:
        try:
            resp = client.past_matches(tour, mid, year=year,
                                        include='stat,round,tournament',
                                        page_size=PAGE_SIZE, page_no=page)
        except RuntimeError as e:
            print(f"      ! mid={mid} year={year} page={page}: {e}", file=sys.stderr)
            break
        rows = (resp or {}).get('data') or resp or []
        if not isinstance(rows, list) or not rows:
            break
        matches.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
        if page > 10:  # hard safety cap (~500 matches)
            break
    return matches


# ─── Main ────────────────────────────────────────────────────────────────────

def process_tour(client: MatchstatClient, tour: str, years: list[int],
                 top_n: int, limit: int | None, debug: bool) -> dict:
    print(f"\n{'='*54}")
    print(f"  Fetching {tour.upper()} match stats for {years}")
    print(f"{'='*54}")

    bio_var  = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    bio_path = DATA_DIR / f'players_{tour}.js'
    bios = parse_js_array(bio_path.read_text(encoding='utf-8'), bio_var)

    # Only process bios within top-N AND with a Matchstat ID
    eligible = [b for b in bios if b.get('mid') and b['id'] <= top_n]
    if limit:
        eligible = eligible[:limit]
    print(f"  {len(eligible)} bios eligible (have mid, id≤{top_n}{', limit='+str(limit) if limit else ''})")

    rows_by_year: dict[int, list] = defaultdict(list)
    recent_by_bio: dict[int, list] = {}
    history_by_bio: dict[int, list] = {}
    debug_dumped = False

    # Round-depth ordering for "furthest round reached at this tournament".
    # Higher = deeper in the bracket. Lower than -1 = not a real round.
    ROUND_DEPTH = {
        'First': 1, 'Second': 2, 'Third': 3, 'Fourth': 4,
        '1/8': 4, '1/4': 5, '1/2': 6, 'Final': 7,
    }

    for i, b in enumerate(eligible, 1):
        mid = b['mid']
        bio_id = b['id']
        name = b['name']
        print(f"  [{i:3d}/{len(eligible)}] bio#{bio_id:3d} mid={mid:<8} {name}")

        all_year_matches: list[dict] = []
        for y in years:
            ym = fetch_player_year_matches(client, tour, mid, y)
            print(f"        · {y}: {len(ym)} matches")
            all_year_matches.extend(ym)

            # Debug-dump first match to verify field shape
            if debug and not debug_dumped and ym:
                debug_dumped = True
                print(f"        DEBUG dump (first match keys): {sorted(ym[0].keys())}")
                stat_obj = ym[0].get('stat') or ym[0].get('stats')
                if isinstance(stat_obj, dict):
                    print(f"        DEBUG stat block keys:        {sorted(stat_obj.keys())}")
                    for sk, sv in stat_obj.items():
                        if isinstance(sv, dict):
                            print(f"          stats.{sk} keys: {sorted(sv.keys())}")
                elif isinstance(stat_obj, list):
                    print(f"        DEBUG stats is a list of {len(stat_obj)} items")
                    if stat_obj and isinstance(stat_obj[0], dict):
                        print(f"          stats[0] keys: {sorted(stat_obj[0].keys())}")

            # Aggregate per year
            agg = aggregate_year(ym, mid)
            if agg:
                meta = agg.pop('_meta', {})
                rows_by_year[y].append({
                    'id':    str(mid),
                    'bioId': bio_id,
                    'name':  name,
                    'ioc':   b.get('nat', ''),
                    'year':  y,
                    'tour':  tour.upper(),
                    **agg,
                })
                print(f"          → aggregated: matches={agg['matches']} "
                      f"servePtsWon={agg['servePtsWonPct']}% "
                      f"matchWin={agg['matchWinPct']}% "
                      f"(usable stat blocks: {meta.get('matchesWithStats')})")

        # ── Rolling-window aggregations (T12M, T6M) ──
        # Computed from the cross-year accumulator above; no extra API calls.
        # Tagged with string year (`'T12M'` / `'T6M'`) so the UI dropdown can
        # distinguish them from calendar years. Also emits surface-specific
        # variants tagged with `'surf': 'H'|'C'|'G'` for the surface filter.
        today = date.today()
        windows = {
            'T12M': today - timedelta(days=365),
            'T6M':  today - timedelta(days=180),
            'T3M':  today - timedelta(days=90),
        }
        # Matchstat court IDs → surface code (H/C/G). Defaults to H for unknowns.
        SURFACE_MAP = {1:'H', 2:'H', 3:'C', 4:'C', 5:'G', 6:'C', 7:'C'}
        def _surf(m):
            t = m.get('tournament') or {}
            return SURFACE_MAP.get(t.get('courtId'), 'H')
        def _parse_dt(m):
            s = m.get('date') or m.get('matchDate') or ''
            try:
                return datetime.strptime(s[:10], '%Y-%m-%d').date()
            except ValueError:
                return None

        for tag, cutoff in windows.items():
            window_matches = [m for m in all_year_matches
                              if (d := _parse_dt(m)) and d >= cutoff]
            if not window_matches:
                continue
            # All-surface aggregate (existing behavior)
            agg = aggregate_year(window_matches, mid)
            if agg:
                agg.pop('_meta', None)
                rows_by_year[tag].append({
                    'id':    str(mid), 'bioId': bio_id, 'name': name,
                    'ioc':   b.get('nat', ''), 'year': tag,
                    'surf':  'All', 'tour': tour.upper(),
                    **agg,
                })
            # Per-surface aggregates — only emit if ≥ 5 matches on that surface
            from collections import defaultdict as _dd2
            by_surf = _dd2(list)
            for m in window_matches:
                by_surf[_surf(m)].append(m)
            for surf, surf_matches in by_surf.items():
                if len(surf_matches) < 5: continue
                surf_agg = aggregate_year(surf_matches, mid)
                if not surf_agg: continue
                surf_agg.pop('_meta', None)
                key = f'{tag}_{surf}'   # e.g. 'T12M_C'
                rows_by_year[key].append({
                    'id':    str(mid), 'bioId': bio_id, 'name': name,
                    'ioc':   b.get('nat', ''), 'year': tag,
                    'surf':  surf, 'tour': tour.upper(),
                    **surf_agg,
                })

        # ── Current-tournament aggregate ("CURR") ──────────────────────────────
        # Straight 14-day rolling window — covers one tournament week plus
        # qualifying. Consistent across all players (no per-player tournament
        # detection, which breaks when cached data lags behind the live draw).
        # Lower floor (2+) so players early in a draw still appear.
        curr_cutoff = today - timedelta(days=14)
        curr_matches = [m for m in all_year_matches
                        if (d := _parse_dt(m)) and d >= curr_cutoff]
        if curr_matches:
            curr_agg = aggregate_year(curr_matches, mid, min_matches=2)
            if curr_agg:
                curr_agg.pop('_meta', None)
                rows_by_year['CURR'].append({
                    'id':    str(mid), 'bioId': bio_id, 'name': name,
                    'ioc':   b.get('nat', ''), 'year': 'CURR',
                    'surf':  'All', 'tour': tour.upper(),
                    **curr_agg,
                })

        # Last 30 across ALL years — used for the form bar (last 10) AND the
        # form-trend sparkline (full 30-match window with rolling 5-match WR).
        if all_year_matches:
            sorted_m = sorted(
                all_year_matches,
                key=lambda m: (m.get('date') or m.get('matchDate') or ''),
                reverse=True,
            )[:30]
            recent_by_bio[bio_id] = [build_form_match(m, mid) for m in sorted_m]

        # ── Tournament history (powers Live Events "defending pts") ──
        # For each (tournament_name, year), find the bio's deepest round.
        # Output a flat list per bio: [{tn, year, round, won}, ...]
        from collections import defaultdict as _dd
        by_tournament: dict = _dd(list)
        for m in all_year_matches:
            tn = ((m.get('tournament') or {}).get('name') or '').strip()
            d  = (m.get('date') or m.get('matchDate') or '')[:10]
            if not tn or len(d) < 4:
                continue
            try:
                yr = int(d[:4])
            except ValueError:
                continue
            by_tournament[(tn, yr)].append(m)

        bio_hist = []
        for (tn, yr), tmatches in by_tournament.items():
            # Pull the round string from each (round may be a dict {id,name} or a bare string)
            def _rd(m):
                ro = m.get('round')
                return (ro.get('name') if isinstance(ro, dict) else ro) or ''
            deepest = max(tmatches, key=lambda m: ROUND_DEPTH.get(_rd(m), 0))
            rd_str = _rd(deepest)
            if not rd_str or ROUND_DEPTH.get(rd_str, 0) == 0:
                continue   # qualifying / round-robin / unknown
            winner_id = deepest.get('match_winner') or 0
            won_deepest = (winner_id == mid)
            bio_hist.append({
                'tn':    tn,
                'year':  yr,
                'round': rd_str,
                'won':   won_deepest,
            })
        if bio_hist:
            history_by_bio[bio_id] = bio_hist

    return {
        'rows_by_year': dict(rows_by_year),
        'recent':       recent_by_bio,
        'history':      history_by_bio,
    }


def write_aggregates(tour: str, rows_by_year: dict, dry_run: bool):
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    for y, rows in rows_by_year.items():
        # Calendar years stay numeric; rolling tags ('T12M'/'T6M') go lowercase.
        suffix = str(y).lower() if isinstance(y, str) else str(y)
        out = AGG_DIR / f'{tour}_{suffix}.json'
        if dry_run:
            print(f"  [DRY RUN] would write {len(rows):3d} rows → {out.name}")
            continue
        out.write_text(json.dumps(rows, separators=(',', ':')), encoding='utf-8')
        print(f"  ✓ wrote {len(rows):3d} rows → {out.name}")


def write_recent_matches(atp_recent: dict, wta_recent: dict, dry_run: bool):
    """Emit data/recent_matches.js consumed by the form-bar hover."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {
        'lastUpdated': now,
        'atp':         atp_recent,
        'wta':         wta_recent,
    }
    js = (
        f'/**\n'
        f' * recent_matches.js — AUTO-GENERATED by scripts/fetch_match_stats_api.py\n'
        f' * Last updated: {now}\n'
        f' * Source: Matchstat Tennis API (RapidAPI, CC BY-NC-SA-equivalent terms via vendor licence).\n'
        f' *\n'
        f' * Schema: keyed by tour, then by bio.id (string); value = last 10 matches:\n'
        f' *   [{{date, tn, rd, opp, oppC, score, won}}, ...]\n'
        f' */\n'
        f'const RECENT_MATCHES = ' + json.dumps(payload, separators=(',', ':')) + ';\n'
    )
    if dry_run:
        print(f"  [DRY RUN] would write {len(js):,} chars → {RECENT_OUT}")
        return
    RECENT_OUT.write_text(js, encoding='utf-8')
    print(f"  ✓ wrote {RECENT_OUT.relative_to(REPO_ROOT)} ({len(js):,} chars; "
          f"ATP {len(atp_recent)} bios, WTA {len(wta_recent)} bios)")


def write_tournament_history(atp_hist: dict, wta_hist: dict, dry_run: bool):
    """Emit data/tournament_history.js consumed by Live Events 'defending' column.

    Schema: keyed by tour, then by bio.id (string); value = list of:
      [{tn, year, round, won}, ...]
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    payload = {
        'lastUpdated': now,
        'atp':         atp_hist,
        'wta':         wta_hist,
    }
    js = (
        f'/**\n'
        f' * tournament_history.js — AUTO-GENERATED by scripts/fetch_match_stats_api.py\n'
        f' * Last updated: {now}\n'
        f' * Source: Matchstat Tennis API.\n'
        f' *\n'
        f' * Schema: keyed by tour, then by bio.id (string); value = list of\n'
        f' *   {{tn, year, round, won}}  — one entry per (tournament, year) the\n'
        f' *   bio appeared in. `round` is the deepest round reached as the raw\n'
        f' *   Matchstat label (First/Second/Third/Fourth/1/8/1/4/1/2/Final).\n'
        f' *   `won` = whether the deepest-round match was won (Final win → champion).\n'
        f' */\n'
        f'const TOURNAMENT_HISTORY = ' + json.dumps(payload, separators=(',', ':')) + ';\n'
    )
    if dry_run:
        print(f"  [DRY RUN] would write {len(js):,} chars → {HISTORY_OUT}")
        return
    HISTORY_OUT.write_text(js, encoding='utf-8')
    print(f"  ✓ wrote {HISTORY_OUT.relative_to(REPO_ROOT)} ({len(js):,} chars; "
          f"ATP {len(atp_hist)} bios, WTA {len(wta_hist)} bios)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tour', choices=['atp', 'wta', 'both'], default='both')
    p.add_argument('--years', nargs='+', type=int, default=DEFAULT_YEARS)
    p.add_argument('--top-n', type=int, default=DEFAULT_TOP_N,
                   help='Only fetch bios with id ≤ this (default 200)')
    p.add_argument('--limit', type=int, default=None,
                   help='Hard cap on # of players per tour (debug)')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--debug', action='store_true', help='Dump first match payload')
    args = p.parse_args()

    client = MatchstatClient()
    print(f"Matchstat API @ {client.host}")
    print(f"API key: {client.api_key[:6]}…{client.api_key[-4:]}")
    print(f"Years:   {args.years}")
    print(f"Tour:    {args.tour}")

    tours = ['atp', 'wta'] if args.tour == 'both' else [args.tour]
    atp_recent, wta_recent = {}, {}
    atp_hist, wta_hist = {}, {}
    for t in tours:
        result = process_tour(client, t, args.years, args.top_n, args.limit, args.debug)
        write_aggregates(t, result['rows_by_year'], args.dry_run)
        if t == 'atp':
            atp_recent = result['recent']
            atp_hist   = result.get('history', {})
        else:
            wta_recent = result['recent']
            wta_hist   = result.get('history', {})

    write_recent_matches(atp_recent, wta_recent, args.dry_run)
    write_tournament_history(atp_hist, wta_hist, args.dry_run)
    print('\n✓ Done.')
    print('Next: python3 scripts/write_trapezoid_from_json.py --years '
          + ' '.join(str(y) for y in [2024] + args.years))


if __name__ == '__main__':
    main()
