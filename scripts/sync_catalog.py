#!/usr/bin/env python3
"""
sync_catalog.py — Auto-discover Matchstat tournament IDs via getTournamentCalendar
and populate apiId fields in data/tournaments.js.

Eliminates the manual-API-ID-curation class of bugs (rome26 had no IDs at all
until we discovered them via getTournamentSeasons; this script finds every
2026 tournament's IDs in two API calls per tour and matches them by name + date).

Usage:
    python3 scripts/sync_catalog.py --dry-run            # default: report only
    python3 scripts/sync_catalog.py --year 2026          # which year to sync
    python3 scripts/sync_catalog.py --years 2025 2026    # multi-year backfill
    python3 scripts/sync_catalog.py --write              # actually update tournaments.js

Cost: ~3-5 API calls per (year, tour) combo (paginated). 2026 ATP+WTA = 6-10 calls.
Re-runs are cheap; only writes when --write is set and proposed changes exist.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "data" / "tournaments.js"

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(ROOT / "scripts"))
from matchstat import MatchstatClient

PAGE_SIZE = 60   # max we'll attempt; if API caps lower we'll just paginate more
MAX_PAGES = 10

# Tier mapping: Matchstat's `tier` string → our `type` field. For sanity-cross-
# checking entries that match by name but disagree on tier (catch catalog bugs).
TIER_MAP = {
    # ATP / WTA tour
    "ATP 250": "M250", "ATP 500": "M500", "ATP 1000": "M1000",
    "WTA 250": "W250", "WTA 500": "W500", "WTA 1000": "W1000",
    # Slams + finals
    "Grand Slam": "GS", "ATP Finals": "ATPFinals", "WTA Finals": "WTAFinals",
    "Championships": "WTAFinals",  # alt label seen in API
}


def fetch_calendar(client: MatchstatClient, tour: str, year: int) -> list[dict]:
    """Paginate getTournamentCalendar for one (tour, year). The year is a path
    parameter (path: `tournament/calendar/{year}`), not a query parameter —
    confirmed via cURL inspection of the RapidAPI tester."""
    path = f"tournament/calendar/{year}"
    all_items: list[dict] = []
    seen_ids: set[int] = set()
    for page in range(1, MAX_PAGES + 1):
        params = {"pageSize": PAGE_SIZE, "pageNo": page}
        try:
            data, meta = client.get(tour, path, params=params)
        except Exception as e:
            print(f"  [{tour} y{year} p{page}] error: {str(e)[:120]}", file=sys.stderr)
            break
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list) or not items:
            break
        new_count = 0
        for it in items:
            iid = it.get("id")
            if iid is None or iid in seen_ids:
                continue
            seen_ids.add(iid)
            all_items.append(it)
            new_count += 1
        if new_count == 0 or len(items) < PAGE_SIZE:
            break
    print(f"  [{tour} y{year}] fetched {len(all_items)} tournaments")
    return all_items


# ── Catalog parsing (regex against tournaments.js) ─────────────────────────────
_ID_PAT = re.compile(r'\bid:"([a-z0-9_]+)"')

def _balanced_object(text: str, start: int) -> int:
    """Given start position pointing at `{`, return the index AFTER the
    matching closing brace (handles nested apiId{} too)."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def parse_catalog(text: str) -> list[dict]:
    """Return list of catalog entries with text spans so we can edit in place."""
    entries: list[dict] = []
    for m in _ID_PAT.finditer(text):
        # Walk back to find the opening `{` for this entry
        i = m.start()
        while i > 0 and text[i] != '{':
            i -= 1
        if text[i] != '{':
            continue
        end = _balanced_object(text, i)
        obj_text = text[i:end]
        def f(field: str, default=None):
            r = re.search(rf'\b{field}:"([^"]+)"', obj_text)
            return r.group(1) if r else default
        # apiId{atp:N, wta:M}
        atp_m = re.search(r'apiId:\s*\{[^}]*?atp:\s*(\d+)', obj_text)
        wta_m = re.search(r'apiId:\s*\{[^}]*?wta:\s*(\d+)', obj_text)
        entries.append({
            "id":         f("id"),
            "name":       f("name"),
            "tour":       f("tour"),
            "type":       f("type"),
            "startDate":  f("startDate"),
            "endDate":    f("endDate"),
            "current_atp": int(atp_m.group(1)) if atp_m else None,
            "current_wta": int(wta_m.group(1)) if wta_m else None,
            "_start":     i,
            "_end":       end,
        })
    return entries


# ── Matching ───────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _split_head(s: str) -> str:
    return _norm(s).split(" - ")[0].strip()


def _date_proximity_score(catalog_date: str | None, api_date_iso: str) -> int:
    """+25 if within ±7d, +5 if ±21d, -15 if very far. 0 if either missing."""
    if not catalog_date or not api_date_iso:
        return 0
    try:
        d1 = datetime.fromisoformat(catalog_date[:10])
        d2 = datetime.fromisoformat(api_date_iso[:10])
        days = abs((d1 - d2).days)
        if days <= 7:  return 25
        if days <= 21: return 5
        if days <= 60: return -5
        return -15
    except (ValueError, TypeError):
        return 0


def match_api_to_catalog(catalog: list[dict], api_entries: list[dict],
                         tour: str, year: int) -> tuple[dict, list]:
    """For each API entry, score against catalog candidates (matching tour, year-
    suffix, name similarity, date proximity). Returns:
      proposals: dict {catalog_id: (api_id, tier_from_api, score)}
      unmatched: list of (api_entry, best_score, best_catalog_or_None) — for review.
    """
    yr_suffix = str(year)[-2:]
    proposals: dict[str, tuple[int, str, int]] = {}
    unmatched: list = []

    for api in api_entries:
        api_id = api.get("id")
        api_name = api.get("name") or ""
        api_date = (api.get("date") or "")[:10]
        api_tier = api.get("tier") or ""
        if not api_id or not api_date:
            continue
        api_n  = _norm(api_name)
        api_h  = _split_head(api_name)

        # Restrict candidates: same year, compatible tour
        cands: list[tuple[int, dict]] = []
        for c in catalog:
            if not (c["id"] or "").endswith(yr_suffix):
                continue
            if c["tour"] not in ("BOTH", tour.upper()):
                continue
            cn = _norm(c["name"])
            ch = _split_head(c["name"])
            score = 0
            if cn and cn == api_n:                  score = 100
            elif cn and (api_n.startswith(cn) or cn.startswith(api_n)): score = 80
            elif ch and ch == api_h:                score = 70
            elif ch and (api_h in cn or ch in api_n): score = 50
            if score == 0:
                continue
            score += _date_proximity_score(c["startDate"], api_date)
            cands.append((score, c))

        if not cands:
            unmatched.append((api, 0, None))
            continue
        cands.sort(key=lambda x: -x[0])
        best_score, best = cands[0]
        if best_score < 60:                  # weak match — surface for review
            unmatched.append((api, best_score, best))
            continue
        proposals[best["id"]] = (api_id, api_tier, best_score)

    return proposals, unmatched


# ── Catalog mutation ───────────────────────────────────────────────────────────
def _add_or_update_apiid(obj_text: str, tour: str, api_id: int) -> str:
    """Inject apiId.{tour}=N into a tournament-object string. If apiId{} exists,
    update the value or add the tour. If absent, add `apiId:{...}` before the
    closing brace."""
    tour = tour.lower()
    apiid_m = re.search(r'apiId:\s*(\{[^}]*\})', obj_text)
    if apiid_m:
        block = apiid_m.group(1)            # like '{atp:21326}' or '{atp:21326, wta:NULL}'
        # Strip TODO comment if present
        block_clean = re.sub(r'/\*[^*]*\*/', '', block).strip()
        kv_m = re.search(rf'\b{tour}:\s*(\d+|null|NULL)', block_clean)
        if kv_m:
            new_block = re.sub(rf'\b{tour}:\s*(\d+|null|NULL)', f'{tour}:{api_id}', block_clean)
        else:
            # Add the tour key. Keep braces.
            inside = block_clean.strip("{}").strip()
            inside = (inside + ", " if inside else "") + f"{tour}:{api_id}"
            new_block = "{" + inside + "}"
        return obj_text.replace(apiid_m.group(0), f"apiId:{new_block}")
    # No apiId at all → insert before the trailing `}` of the object
    # Strip trailing comma-and-comment patterns first
    return re.sub(r'\s*\}\s*$', f', apiId:{{{tour}:{api_id}}} }}', obj_text, count=1)


def apply_proposals(catalog_text: str, catalog: list[dict],
                    proposals_atp: dict, proposals_wta: dict) -> tuple[str, int]:
    """Apply ATP and WTA proposals to the catalog text. Returns (new_text, n_changes)."""
    # Walk entries in REVERSE order so spans stay valid as we edit.
    n = 0
    new_text = catalog_text
    for c in sorted(catalog, key=lambda x: -x["_start"]):
        cid = c["id"]
        a_prop = proposals_atp.get(cid)
        w_prop = proposals_wta.get(cid)
        # Skip if already correctly set
        wants_atp = a_prop and a_prop[0] != c["current_atp"]
        wants_wta = w_prop and w_prop[0] != c["current_wta"]
        if not (wants_atp or wants_wta):
            continue
        obj_text = catalog_text[c["_start"]:c["_end"]]
        if wants_atp:
            obj_text = _add_or_update_apiid(obj_text, "atp", a_prop[0])
        if wants_wta:
            obj_text = _add_or_update_apiid(obj_text, "wta", w_prop[0])
        new_text = new_text[:c["_start"]] + obj_text + new_text[c["_end"]:]
        n += 1
    return new_text, n


def report(catalog: list[dict], proposals_atp: dict, proposals_wta: dict,
           unmatched_atp: list, unmatched_wta: list) -> None:
    print()
    print("=" * 76)
    print(f"PROPOSED CATALOG UPDATES")
    print("=" * 76)
    catalog_by_id = {c["id"]: c for c in catalog}
    by_id: dict[str, dict] = {}
    for cid, (api_id, tier, score) in proposals_atp.items():
        by_id.setdefault(cid, {})["atp"] = (api_id, tier, score)
    for cid, (api_id, tier, score) in proposals_wta.items():
        by_id.setdefault(cid, {})["wta"] = (api_id, tier, score)
    for cid in sorted(by_id):
        c = catalog_by_id.get(cid, {})
        cur = []
        if c.get("current_atp"): cur.append(f"atp:{c['current_atp']}")
        if c.get("current_wta"): cur.append(f"wta:{c['current_wta']}")
        cur_s = ", ".join(cur) or "—"
        new = []
        for t in ("atp", "wta"):
            if t in by_id[cid]:
                api_id, tier, score = by_id[cid][t]
                tag = "" if c.get(f"current_{t}") else " (NEW)"
                changed = c.get(f"current_{t}") != api_id
                new.append(f"{t}:{api_id}{tag}" if changed else f"{t}:{api_id}=ok")
        new_s = ", ".join(new)
        print(f"  {cid:25}  {c.get('name','?')[:40]:40}  {cur_s:25} → {new_s}")
    print()
    if unmatched_atp or unmatched_wta:
        print("=" * 76)
        print("UNMATCHED API ENTRIES (no catalog row found — possibly need to add)")
        print("=" * 76)
        for tour, lst in (("ATP", unmatched_atp), ("WTA", unmatched_wta)):
            for api, score, hint in lst:
                hint_s = f"  (best guess: {hint['id']} score={score})" if hint else ""
                print(f"  {tour}  id={api.get('id'):6}  {api.get('date','')[:10]}  "
                      f"{api.get('name','?'):45}  tier={api.get('tier','')}{hint_s}")
        print()


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, action="append",
                   help="Year to sync (repeatable). Default: 2026.")
    p.add_argument("--years", type=int, nargs="+",
                   help="Multiple years to sync at once.")
    p.add_argument("--write", action="store_true",
                   help="Apply changes to data/tournaments.js. Default: dry-run.")
    args = p.parse_args()

    years = args.years or args.year or [2026]
    api_key = os.environ.get("MATCHSTAT_API_KEY")
    if not api_key:
        print("MATCHSTAT_API_KEY not set", file=sys.stderr)
        return 1
    client = MatchstatClient(api_key=api_key)

    text = CATALOG_PATH.read_text(encoding="utf-8")
    catalog = parse_catalog(text)
    print(f"Parsed {len(catalog)} catalog entries from {CATALOG_PATH.name}")

    all_props_atp: dict = {}
    all_props_wta: dict = {}
    all_unmatched_atp: list = []
    all_unmatched_wta: list = []

    for y in years:
        print(f"\n— Fetching calendar for year {y} —")
        atp_items = fetch_calendar(client, "atp", y)
        wta_items = fetch_calendar(client, "wta", y)
        props_atp, unm_atp = match_api_to_catalog(catalog, atp_items, "atp", y)
        props_wta, unm_wta = match_api_to_catalog(catalog, wta_items, "wta", y)
        all_props_atp.update(props_atp)
        all_props_wta.update(props_wta)
        all_unmatched_atp.extend(unm_atp)
        all_unmatched_wta.extend(unm_wta)

    report(catalog, all_props_atp, all_props_wta, all_unmatched_atp, all_unmatched_wta)

    n_proposed = len({*all_props_atp, *all_props_wta})
    if not args.write:
        print(f"DRY RUN — {n_proposed} catalog entries would be updated.")
        print("Re-run with --write to apply.")
        return 0

    new_text, n_applied = apply_proposals(text, catalog,
                                          all_props_atp, all_props_wta)
    if n_applied == 0:
        print("No changes needed (all proposed updates already present).")
        return 0
    CATALOG_PATH.write_text(new_text, encoding="utf-8")
    print(f"WROTE {n_applied} updates to {CATALOG_PATH.relative_to(ROOT)}.")
    print("Next: python3 scripts/seed_db.py to plumb new IDs into the DB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
