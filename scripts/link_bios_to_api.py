#!/usr/bin/env python3
"""
link_bios_to_api.py — Add Matchstat player_id (`mid`) to each bio.

Matches PLAYERS_*.js bios against the Matchstat /ranking/singles results
(cached in scripts/cache/api/{atp,wta}_player_list.json) and writes the
matched player_id back into each bio entry as `mid:<int>`.

Match strategy (highest confidence first):
  1. last-name (lowercase, accent-stripped) matches AND nationality matches → exact
  2. Full name normalized matches (handles hyphens) AND nationality matches  → exact
  3. last-name matches, only ONE candidate in the API list → confident
  4. last-name matches, MULTIPLE candidates, no country tie-breaker → ambiguous (reported)
  5. No last-name match → unmatched (reported)

USAGE
-----
    python3 scripts/link_bios_to_api.py [--dry-run] [--tour atp|wta|both]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT  = Path(__file__).parent.parent
CACHE_DIR  = REPO_ROOT / "scripts" / "cache" / "api"
DATA_DIR   = REPO_ROOT / "data"


def norm(s: str) -> str:
    nfkd = unicodedata.normalize('NFKD', s or '')
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r'[\s\-\']+', ' ', ascii_str.lower().strip())


# Country-code aliases so 'CRO' (bio) matches 'CRO' or 'HRV' (api) etc.
# Common ISO/IOC variants
COUNTRY_ALIASES = {
    'CRO': {'CRO', 'HRV'},
    'GBR': {'GBR', 'UK'},
    'USA': {'USA', 'US'},
    'CHN': {'CHN', 'TPE'},   # tie-break only — sometimes Taiwan comes back as TPE
    # Otherwise codes generally match across both systems.
}


def country_matches(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a.upper() == b.upper():
        return True
    aliases = COUNTRY_ALIASES.get(a.upper(), {a.upper()})
    return b.upper() in aliases


def find_match(bio: dict, api_list: list) -> tuple[dict | None, str]:
    """
    Returns (matched_api_record_or_None, classification_string).
    Classifications: exact_full, exact_last+country, single_last, ambiguous, unmatched
    """
    bio_name_n = norm(bio['name'])
    bio_last_n = norm(bio['name'].split()[-1])
    bio_ab_n   = norm(bio.get('ab', ''))
    bio_country = (bio.get('nat') or '').upper()

    # Pass 1: exact full-name + country
    for r in api_list:
        if norm(r['name']) == bio_name_n and country_matches(bio_country, r['country']):
            return r, 'exact_full'

    # Pass 1b: same words different order (handles "Zhang Zhizhen" vs "Zhizhen Zhang")
    bio_word_set = set(bio_name_n.split())
    if len(bio_word_set) >= 2:
        for r in api_list:
            if set(norm(r['name']).split()) == bio_word_set and country_matches(bio_country, r['country']):
                return r, 'word_order'

    # Pass 2: last-name match + country tie
    last_match = []
    for r in api_list:
        api_last_n = norm(r['name'].split()[-1])
        if api_last_n == bio_last_n:
            last_match.append(r)
        elif bio_ab_n and api_last_n == bio_ab_n.split('.')[-1]:
            last_match.append(r)

    last_match = list({r['pid']: r for r in last_match}.values())  # dedupe by pid

    if len(last_match) == 1:
        only = last_match[0]
        # Real signal is FIRST NAME match (handles same-surname siblings + country switches).
        # Country can change mid-career (Kasatkina RUS→AUS, Potapova → AUT, Rakhimova → UZB),
        # so don't reject on country alone. But siblings (Mikael/Elias Ymer) share country,
        # so we can't accept on country alone either. First-name match is the deciding test.
        bio_first = bio_name_n.split()[0]
        api_first = norm(only['name']).split()[0]
        first_match = (bio_first == api_first) or (
            len(bio_first) >= 2 and len(api_first) >= 2 and bio_first[:2] == api_first[:2]
        )
        country_match = country_matches(bio_country, only['country'])
        if first_match and country_match:
            return only, 'exact_last+country'
        if first_match and not country_match:
            return only, 'last+first (country diff — likely nationality switch)'
        if not first_match and country_match:
            return None, f'rejected_diff_first ({only["name"]} vs bio first "{bio_first}")'
        # Neither matches → very likely different person
        return None, f'rejected_diff_first+country ({only["name"]} {only["country"]} vs bio {bio_country})'

    if len(last_match) > 1:
        # Multiple candidates — country must disambiguate
        with_country = [r for r in last_match if country_matches(bio_country, r['country'])]
        if len(with_country) == 1:
            return with_country[0], 'exact_last+country'
        return None, f'ambiguous ({len(last_match)} last-name candidates: {[r["name"] for r in last_match[:5]]})'

    # Pass 3: API name CONTAINS all of bio's words (handles middle/maternal names).
    # "Jaume Munar" (bio) ⊂ "Jaume Antoni Munar Clar" (API)
    # "Camila Osorio" (bio) ⊂ "Maria Camila Osorio Serrano" (API)
    # "Pedro Martinez" (bio) ⊂ "Pedro Martinez Portero" (API)
    # Country must match to avoid false positives across same-name players.
    bio_words = bio_name_n.split()
    candidates = []
    for r in api_list:
        api_words = set(norm(r['name']).split())
        if all(w in api_words for w in bio_words):
            candidates.append(r)

    same_country = [r for r in candidates if country_matches(bio_country, r['country'])]
    if len(same_country) == 1:
        return same_country[0], 'subset_match+country'
    if len(candidates) == 1 and not same_country:
        # Single candidate, country differs — could be nationality switch
        return candidates[0], 'subset_match (country diff)'
    if len(same_country) > 1:
        return None, f'ambiguous_subset ({len(same_country)} candidates: {[r["name"] for r in same_country[:3]]})'

    return None, 'unmatched'


# ─── JS file editing ────────────────────────────────────────────────────────

def parse_bios(text: str, var_name: str) -> list:
    pattern = rf'const\s+{var_name}\s*=\s*(\[[\s\S]*?\]);'
    m = re.search(pattern, text)
    if not m:
        return []
    raw = m.group(1)
    raw = re.sub(r'([{,])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    raw = re.sub(r':\s*\.(\d)', r': 0.\1', raw)
    return json.loads(raw)


def patch_bios_file(path: Path, mapping: dict, dry_run: bool) -> int:
    """Insert `mid:<n>` after `id:<n>` in each bio line. Returns number patched."""
    text = path.read_text(encoding='utf-8')
    patched = 0

    def replace(match):
        nonlocal patched
        bio_id_str = match.group(1)
        bio_id = int(bio_id_str)
        if bio_id not in mapping:
            return match.group(0)  # no change
        mid = mapping[bio_id]
        # Don't double-patch
        if re.search(rf'^\s*\{{\s*id:{bio_id_str}\s*,\s*mid:', match.group(0)):
            return match.group(0)
        patched += 1
        # Insert mid right after the id field
        return re.sub(rf'(id:\s*{bio_id_str})\s*,', rf'\1, mid:{mid},', match.group(0), count=1)

    new_text = re.sub(r'(?m)^\s*\{\s*id:(\d+)[^\n]*\}', replace, text)
    if not dry_run and new_text != text:
        path.write_text(new_text, encoding='utf-8')
    return patched


# ─── Core ────────────────────────────────────────────────────────────────────

def link_tour(tour: str, dry_run: bool) -> None:
    print(f"\n{'='*54}")
    print(f"  Linking {tour.upper()} bios → Matchstat IDs")
    print(f"{'='*54}")

    api_list = json.loads((CACHE_DIR / f'{tour}_player_list.json').read_text())
    bio_var  = 'PLAYERS_ATP' if tour == 'atp' else 'PLAYERS_WTA'
    bio_path = DATA_DIR / f'players_{tour}.js'
    bios     = parse_bios(bio_path.read_text(encoding='utf-8'), bio_var)
    print(f"  Bios:    {len(bios)}")
    print(f"  API list: {len(api_list)}")

    mapping = {}
    rows = []
    for b in bios:
        match, kind = find_match(b, api_list)
        rows.append((b, match, kind))
        if match:
            mapping[b['id']] = match['pid']

    counts = {}
    for _, _, k in rows:
        key = k.split(' ')[0] if k.startswith('ambiguous') else k
        counts[key] = counts.get(key, 0) + 1
    print(f"\n  Match summary: {counts}")
    print(f"  Total mapped:  {len(mapping)}/{len(bios)}")

    # Show non-trivial cases
    for b, match, kind in rows:
        if kind in ('exact_full',):
            continue
        marker = '✓' if match else '✗'
        match_label = f"{match['name']} (pid {match['pid']}, {match['country']})" if match else 'NO MATCH'
        print(f"  {marker} bio#{b['id']:3d} {b['name']:<32} ({b.get('nat','?'):3}) → {match_label}  [{kind}]")

    if dry_run:
        print(f"\n  [DRY RUN] would patch {bio_path}")
        return

    n = patch_bios_file(bio_path, mapping, dry_run)
    print(f"\n  ✓ Patched {n} entries in {bio_path.name}")


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
