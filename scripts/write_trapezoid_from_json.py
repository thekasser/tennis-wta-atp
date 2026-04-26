#!/usr/bin/env python3
"""
write_trapezoid_from_json.py — Write data/trapezoid_data.js from
pre-aggregated JSON files in scripts/cache/aggregates/.

Used when CSVs are too large to fetch into the sandbox, so aggregation runs
in the browser and we ship only the per-player rows.

Files expected:
    scripts/cache/aggregates/{tour}_{year}.json   (each = list of player rows)

USAGE
-----
    python3 scripts/write_trapezoid_from_json.py [--years 2024 2025 2026]
"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
AGG_DIR   = REPO_ROOT / "scripts" / "cache" / "aggregates"
OUT_FILE  = REPO_ROOT / "data" / "trapezoid_data.js"


def render_js(years, atp_by_year, wta_by_year):
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    # Concatenate ALL rows — including surface-variant buckets ('T12M_C', etc.)
    # which aren't in the `years` list (UI dropdown shows clean years only,
    # but the data array carries every row so the surface filter can hit them).
    atp, wta = [], []
    for rows in atp_by_year.values(): atp.extend(rows)
    for rows in wta_by_year.values(): wta.extend(rows)
    lines = [
        '/**',
        ' * trapezoid_data.js — AUTO-GENERATED',
        f' * Last updated: {now}',
        f' * Years: {", ".join(str(y) for y in years)}',
        ' * Source: github.com/JeffSackmann/tennis_atp & tennis_wta (CC BY-NC-SA 4.0)',
        ' * Inputs were aggregated from Sackmann match-level CSVs in-browser, then',
        ' * persisted as JSON via this script. See recompute_trapezoid.py for the',
        ' * canonical aggregation logic / metric definitions.',
        ' */',
        '',
        f'const TRAPEZOID_YEARS = {json.dumps(years)};',
        '',
        'const TRAPEZOID_ATP = ' + json.dumps(atp, separators=(',', ':')) + ';',
        '',
        'const TRAPEZOID_WTA = ' + json.dumps(wta, separators=(',', ':')) + ';',
        '',
        "const TRAPEZOID_METRICS = ['matches','servePtsWonPct','returnPtsWonPct','totalPtsWonPct','tbWinPct','decSetWinPct','acesPerSvGm','bpSavedPct','matchWinPct'];",
        '',
        'const TRAPEZOID_LABELS = {',
        "    matches:          'Matches Played',",
        "    servePtsWonPct:   'Serve Points Won %',",
        "    returnPtsWonPct:  'Return Points Won %',",
        "    totalPtsWonPct:   'Total Points Won %',",
        "    tbWinPct:         'Tiebreak Win %',",
        "    decSetWinPct:     'Deciding Set Win %',",
        "    acesPerSvGm:      'Aces / Service Game',",
        "    bpSavedPct:       'Break Points Saved %',",
        "    matchWinPct:      'Match Win %',",
        '};',
        '',
    ]
    return '\n'.join(lines)


def load_sid_to_bio_id(tour: str) -> dict:
    """{sackmann_player_id_string: bio.id_int} from PLAYERS_*.js."""
    import re
    bio_var = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    text = (REPO_ROOT / 'data' / f'players_{tour}.js').read_text(encoding='utf-8')
    pat = re.compile(rf'\{{\s*id:\s*(\d+)[^}}]*?sid:\s*"([^"]+)"')
    return {sid: int(bio_id) for bio_id, sid in pat.findall(text)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--years', nargs='+', type=int, default=[2024, 2025, 2026])
    args = p.parse_args()

    atp_by_year, wta_by_year = {}, {}
    found_any = False

    # Calendar years (numeric keys: 2024, 2025, 2026)
    for y in args.years:
        for tour, bucket in (('atp', atp_by_year), ('wta', wta_by_year)):
            f = AGG_DIR / f'{tour}_{y}.json'
            if f.exists():
                rows = json.loads(f.read_text())
                sid_map = load_sid_to_bio_id(tour)
                for r in rows:
                    r.setdefault('year', y)
                    r.setdefault('tour', tour.upper())
                    if 'bioId' not in r:
                        r['bioId'] = sid_map.get(str(r.get('id', '')))
                bucket[y] = rows
                print(f'  {tour.upper()} {y}: {len(rows)} players')
                found_any = True
            else:
                print(f'  {tour.upper()} {y}: missing ({f.name})')

    # Rolling windows (string keys: 'T12M', 'T6M') — auto-detect on disk.
    # All-surface file: {tour}_t12m.json
    # Per-surface files: {tour}_t12m_h.json / _c.json / _g.json
    for tag in ('T12M', 'T6M'):
        for tour, bucket in (('atp', atp_by_year), ('wta', wta_by_year)):
            sid_map = load_sid_to_bio_id(tour)
            # All-surface
            f = AGG_DIR / f'{tour}_{tag.lower()}.json'
            if f.exists():
                rows = json.loads(f.read_text())
                for r in rows:
                    r.setdefault('year', tag)
                    r.setdefault('tour', tour.upper())
                    r.setdefault('surf', 'All')
                    if 'bioId' not in r:
                        r['bioId'] = sid_map.get(str(r.get('id', '')))
                bucket[tag] = rows
                print(f'  {tour.upper()} {tag} (All): {len(rows)} players')
                found_any = True
            # Per-surface variants
            for surf in ('H', 'C', 'G'):
                fs = AGG_DIR / f'{tour}_{tag.lower()}_{surf.lower()}.json'
                if not fs.exists():
                    continue
                rows = json.loads(fs.read_text())
                for r in rows:
                    r.setdefault('year', tag)
                    r.setdefault('tour', tour.upper())
                    r.setdefault('surf', surf)
                    if 'bioId' not in r:
                        r['bioId'] = sid_map.get(str(r.get('id', '')))
                # Append surface rows to the same bucket so they all appear in the JS array
                bucket.setdefault(f'{tag}_{surf}', rows)
                print(f'  {tour.upper()} {tag} ({surf}): {len(rows)} players')
                found_any = True

    if not found_any:
        print('ERROR: no aggregate JSON files found, refusing to write empty data.', file=sys.stderr)
        return 1

    # Order: rolling windows first (T12M, T6M), then calendar years descending.
    int_years   = sorted({y for y in args.years
                          if y in atp_by_year or y in wta_by_year}, reverse=True)
    rolling     = [t for t in ('T12M', 'T6M')
                   if t in atp_by_year or t in wta_by_year]
    years_with_data = rolling + int_years

    js = render_js(years_with_data, atp_by_year, wta_by_year)
    OUT_FILE.write_text(js, encoding='utf-8')
    print(f'Wrote {OUT_FILE} ({len(js):,} chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
