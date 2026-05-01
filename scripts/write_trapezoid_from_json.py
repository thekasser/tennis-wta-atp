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
import argparse, json, re, sys
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


def load_existing_rows(tour: str) -> list:
    """Parse TRAPEZOID_ATP or TRAPEZOID_WTA from the current trapezoid_data.js.
    Returns [] if the file is missing or unparseable."""
    if not OUT_FILE.exists():
        return []
    raw = OUT_FILE.read_text(encoding='utf-8')
    var = 'TRAPEZOID_ATP' if tour == 'atp' else 'TRAPEZOID_WTA'
    # The array can be large — use a bracket-depth walk rather than a fragile regex.
    marker = f'const {var} = ['
    pos = raw.find(marker)
    if pos == -1:
        return []
    depth, i = 0, pos + len(marker) - 1  # start at the opening '['
    while i < len(raw):
        if raw[i] == '[':
            depth += 1
        elif raw[i] == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[pos + len(marker) - 1: i + 1])
                except Exception:
                    return []
        i += 1
    return []


def merge_existing_into_bucket(bucket: dict, existing_rows: list, tour: str) -> None:
    """For any (bioId, year, surf) not already in `bucket`, carry forward the
    existing row from the prior trapezoid_data.js.  This prevents a CI run that
    only fetches 2 WTA players from wiping the other 100+ from the output."""
    # Build coverage set from what the new aggregate files provided.
    covered: set = set()
    for rows in bucket.values():
        for r in rows:
            key = (r.get('bioId'), str(r.get('year', '')), r.get('surf', 'All'))
            covered.add(key)

    # Walk existing rows; if not covered, slot them back into the bucket.
    preserved = 0
    for r in existing_rows:
        if str(r.get('tour', '')).upper() != tour.upper():
            continue
        yr   = r.get('year')
        surf = r.get('surf', 'All')
        key  = (r.get('bioId'), str(yr), surf)
        if key in covered:
            continue  # new data already has this player×period×surface
        # Use yr as bucket key; create bucket entry if needed.
        bucket.setdefault(yr, []).append(r)
        preserved += 1

    if preserved:
        print(f'  {tour.upper()}: preserved {preserved} rows from existing trapezoid_data.js '
              f'(not in current aggregate files)')


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

    # Rolling windows (string keys: 'CURR', 'T3M', 'T6M', 'T12M') — auto-detect on disk.
    # All-surface file: {tour}_t12m.json / _t6m.json / _t3m.json / _curr.json
    # Per-surface files: {tour}_t12m_h.json / _c.json / _g.json (T3M same pattern)
    for tag in ('CURR', 'T3M', 'T6M', 'T12M'):
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

    # Merge-on-write: carry forward any (bioId, year, surf) rows from the existing
    # trapezoid_data.js that aren't covered by the freshly-fetched aggregate files.
    # This prevents a GH Actions run that only fetches 2 WTA players from wiping the
    # other 100+ players that are already in the committed file.
    merge_existing_into_bucket(atp_by_year, load_existing_rows('atp'), 'atp')
    merge_existing_into_bucket(wta_by_year, load_existing_rows('wta'), 'wta')

    # Order: CURR first (most recent), then rolling windows, then calendar years descending.
    int_years   = sorted({y for y in args.years
                          if y in atp_by_year or y in wta_by_year}, reverse=True)
    rolling     = [t for t in ('CURR', 'T3M', 'T6M', 'T12M')
                   if t in atp_by_year or t in wta_by_year]
    years_with_data = rolling + int_years

    js = render_js(years_with_data, atp_by_year, wta_by_year)
    OUT_FILE.write_text(js, encoding='utf-8')
    print(f'Wrote {OUT_FILE} ({len(js):,} chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
