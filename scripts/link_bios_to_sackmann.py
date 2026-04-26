#!/usr/bin/env python3
"""
link_bios_to_sackmann.py — Add Sackmann player_id (`sid`) to each bio.

Reads Sackmann player records from data/trapezoid_data.js (the 2024 baseline,
which contains every player with stat-eligible matches that year). Matches
against PLAYERS_*.js bios and writes the player_id back as `sid:<int>`.

Without this, the trapezoid year-toggle would render duplicate rows when
Matchstat's 2025+ data joins the same scatter — one row from Sackmann's
"Coco Gauff" + one from Matchstat's "Cori Gauff" instead of one unified
player.

Match strategy: re-uses the same logic as link_bios_to_api.py (full-name +
country, last-name + first-name, subset match, word order, sibling rejection).

USAGE
-----
    python3 scripts/link_bios_to_sackmann.py [--dry-run] [--tour atp|wta|both]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Reuse country/name helpers from the Matchstat linker
sys.path.insert(0, str(Path(__file__).parent))
from link_bios_to_api import norm, country_matches, find_match, parse_bios, patch_bios_file  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
TRAP_FILE = REPO_ROOT / "data" / "trapezoid_data.js"
DATA_DIR  = REPO_ROOT / "data"


def parse_trapezoid(text: str, var_name: str) -> list:
    """Extract Sackmann player records from TRAPEZOID_ATP / TRAPEZOID_WTA."""
    pattern = rf'const\s+{var_name}\s*=\s*(\[[\s\S]*?\]);'
    m = re.search(pattern, text)
    if not m:
        return []
    return json.loads(m.group(1))


def to_api_shape(sackmann_rows: list) -> list:
    """Re-shape Sackmann records to match the structure find_match() expects:
       {pid, name, country}.  We dedupe on pid + use the first (2024) record per player.
    """
    seen = {}
    for r in sackmann_rows:
        pid = r['id']  # Sackmann player_id (string in source, keep as-is)
        if pid in seen:
            continue
        seen[pid] = {
            'pid':     pid,
            'name':    r['name'],
            'country': r.get('ioc') or r.get('country') or '',
        }
    return list(seen.values())


def link_tour(tour: str, dry_run: bool) -> None:
    print(f"\n{'='*54}")
    print(f"  Linking {tour.upper()} bios → Sackmann IDs")
    print(f"{'='*54}")

    # Load Sackmann data via TRAPEZOID_ATP / TRAPEZOID_WTA
    trap_text = TRAP_FILE.read_text(encoding='utf-8')
    var_name = 'TRAPEZOID_ATP' if tour == 'atp' else 'TRAPEZOID_WTA'
    sackmann_rows = parse_trapezoid(trap_text, var_name)
    api_list = to_api_shape(sackmann_rows)
    print(f"  Sackmann players: {len(api_list)}")

    # Bios
    bio_var  = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    bio_path = DATA_DIR / f'players_{tour}.js'
    bios     = parse_bios(bio_path.read_text(encoding='utf-8'), bio_var)
    print(f"  Bios:             {len(bios)}")

    mapping = {}
    rows = []
    for b in bios:
        match, kind = find_match(b, api_list)
        rows.append((b, match, kind))
        if match:
            mapping[b['id']] = match['pid']

    counts = {}
    for _, _, k in rows:
        key = k.split(' ')[0] if k.startswith(('ambiguous','rejected')) else k
        counts[key] = counts.get(key, 0) + 1
    print(f"\n  Match summary: {counts}")
    print(f"  Total mapped:  {len(mapping)}/{len(bios)}")

    for b, match, kind in rows:
        if kind == 'exact_full':
            continue
        marker = '✓' if match else '✗'
        match_label = (f"{match['name']} (sid {match['pid']}, {match['country']})"
                       if match else 'NO MATCH')
        print(f"  {marker} bio#{b['id']:3d} {b['name']:<32} ({b.get('nat','?'):3}) → {match_label}  [{kind}]")

    if dry_run:
        print(f"\n  [DRY RUN] would patch {bio_path}")
        return

    # patch_bios_file() inserts `mid:<n>` after `id:<n>`. We need `sid:<n>` instead,
    # and we want to insert it AFTER `mid` if mid already exists (or after `id` if not).
    # Implement inline rather than reusing patch_bios_file.
    text = bio_path.read_text(encoding='utf-8')
    patched = 0

    def replace(match):
        nonlocal patched
        bio_id_str = match.group(1)
        bio_id = int(bio_id_str)
        if bio_id not in mapping:
            return match.group(0)
        sid = mapping[bio_id]
        # Idempotent: skip if `sid:` already present in the bio entry
        if 'sid:' in match.group(0):
            return match.group(0)
        patched += 1
        # Try to insert AFTER `mid:<n>,` if present, else after `id:<n>,`
        line = match.group(0)
        if re.search(rf'mid:\d+\s*,', line):
            return re.sub(rf'(mid:\d+\s*,)', rf'\1 sid:"{sid}",', line, count=1)
        return re.sub(rf'(id:\s*{bio_id_str}\s*,)', rf'\1 sid:"{sid}",', line, count=1)

    new_text = re.sub(r'(?m)^\s*\{\s*id:(\d+)[^\n]*\}', replace, text)
    if new_text != text:
        bio_path.write_text(new_text, encoding='utf-8')
    print(f"\n  ✓ Patched {patched} entries in {bio_path.name}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tour', choices=['atp','wta','both'], default='both')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    tours = ['atp','wta'] if args.tour == 'both' else [args.tour]
    for t in tours:
        link_tour(t, args.dry_run)
    print('\n✓ Done')


if __name__ == '__main__':
    main()
