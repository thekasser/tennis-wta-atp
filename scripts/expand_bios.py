#!/usr/bin/env python3
"""
expand_bios.py — Add bio entries for any player who has made QF+ at a tour-level
event in the last 12 months but isn't currently in players_atp.js / players_wta.js.

The bio whitelist gates dashboard visibility (renderLive, rankings table,
matchup predictor). Without a bio entry, a player who's in the API data
simply doesn't render — so a 130-ranked player making Madrid QF would be
invisible if they weren't on the list. This script closes that gap by
auto-detecting players with notable T12M runs and appending stub bios.

Player names + country codes are extracted from the `raw` JSON column of
matches (which stores the full Matchstat payload, including embedded
player1/player2 objects). No API call needed.

Stub bios get default surface profile + empty form string. The dashboard
already overrides `form` from RECENT_MATCHES at render time, so the empty
default is harmless. Surface profile can be hand-tuned later if desired.

Usage:
    python3 scripts/expand_bios.py --dry-run   # preview only
    python3 scripts/expand_bios.py             # write to data/players_*.js
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tennis.db"
WTA_PATH = ROOT / "data" / "players_wta.js"
ATP_PATH = ROOT / "data" / "players_atp.js"

TOUR_TIERS = ("GS", "M1000", "W1000", "M500", "W500", "M250", "W250",
              "ATPFinals", "WTAFinals")
QF_PLUS_ROUNDS = ("1/4", "1/2", "Final")
T12M_CUTOFF = "2025-05-04"  # rolling 12 months from today (2026-05-04)


def known_mids_from_js(path: Path) -> set[int]:
    """Parse mids out of a players_*.js bio file. Source of truth — the DB
    can lag the JS file when seed_db hasn't been re-run after manual bio
    additions, so checking only the DB causes duplicate insertions.
    """
    text = path.read_text(encoding="utf-8")
    return {int(m.group(1)) for m in re.finditer(r"\bmid:\s*(\d+)", text)}


def find_missing_mids(con: sqlite3.Connection, tour: str,
                      known_mids: set[int]) -> list[dict]:
    """Return mids with QF+ T12M runs that aren't already bio'd. The known set
    must come from the JS bio file (source of truth), not just the DB."""
    bio_mids = set(known_mids) | {r[0] for r in con.execute(
        "SELECT mid FROM players WHERE tour = ? AND mid IS NOT NULL", (tour,))}

    # Both p1 and p2 sides of QF+ matches at tour-level events.
    sql = f"""
    WITH plays AS (
      SELECT m.p1_id AS mid, m.tournament_name, m.round, m.date
      FROM matches m LEFT JOIN tournaments t ON m.tournament_id = t.id
      WHERE m.date >= ? AND m.tour = ?
        AND t.type IN ({','.join('?'*len(TOUR_TIERS))})
        AND m.round IN ({','.join('?'*len(QF_PLUS_ROUNDS))})
      UNION ALL
      SELECT m.p2_id, m.tournament_name, m.round, m.date
      FROM matches m LEFT JOIN tournaments t ON m.tournament_id = t.id
      WHERE m.date >= ? AND m.tour = ?
        AND t.type IN ({','.join('?'*len(TOUR_TIERS))})
        AND m.round IN ({','.join('?'*len(QF_PLUS_ROUNDS))})
    )
    SELECT mid,
           COUNT(DISTINCT tournament_name || date) AS deep_runs,
           MAX(date) AS last_run,
           GROUP_CONCAT(DISTINCT round) AS rounds
    FROM plays WHERE mid IS NOT NULL
    GROUP BY mid ORDER BY deep_runs DESC, last_run DESC
    """
    params = (T12M_CUTOFF, tour, *TOUR_TIERS, *QF_PLUS_ROUNDS,
              T12M_CUTOFF, tour, *TOUR_TIERS, *QF_PLUS_ROUNDS)
    rows = con.execute(sql, params).fetchall()
    return [{"mid": r[0], "deep_runs": r[1], "last_run": r[2], "rounds": r[3]}
            for r in rows if r[0] not in bio_mids]


def lookup_player_meta(con: sqlite3.Connection, mid: int) -> tuple[str, str] | None:
    """Pull name + country from any raw match payload for this mid."""
    rows = con.execute(
        "SELECT raw FROM matches WHERE p1_id = ? OR p2_id = ? LIMIT 5",
        (mid, mid)).fetchall()
    for (raw_str,) in rows:
        if not raw_str:
            continue
        try:
            raw = json.loads(raw_str)
        except (json.JSONDecodeError, TypeError):
            continue
        for side in ("player1", "player2"):
            p = raw.get(side) or {}
            if p.get("id") == mid and p.get("name"):
                return p["name"], p.get("countryAcr") or "UNK"
    return None


def next_bio_id(bios_text: str) -> int:
    """Find max existing id in the JS file and return next free."""
    ids = [int(m.group(1)) for m in re.finditer(r"\bid:\s*(\d+)", bios_text)]
    return max(ids) + 1 if ids else 1


def make_bio_entry(bio_id: int, mid: int, name: str, nat: str,
                   ab: str, deep_runs: int, last_run: str,
                   rounds: str) -> str:
    """Compose one bio JS line. Defaults surface profile + empty form."""
    note = (f"  // auto-added: {deep_runs} QF+ T12M run"
            f"{'s' if deep_runs != 1 else ''}"
            f" (rounds: {rounds}, last: {last_run})")
    return (f'  {{id:{bio_id}, mid:{mid}, name:"{name}", '
            f'ab:"{ab}", nat:"{nat}", '
            f'surf:{{H:.55,C:.55,G:.55}},form:"",inj:false}},'
            f'{note}')


def short_name(name: str) -> str:
    """Best-effort: take the surname for the abbreviated label.
       Falls back to the full name if it doesn't split cleanly."""
    parts = name.strip().split()
    if len(parts) <= 1:
        return name.strip()
    return parts[-1]


def expand_tour(con: sqlite3.Connection, tour: str, path: Path,
                dry_run: bool) -> int:
    bios_text = path.read_text(encoding="utf-8")
    known_mids = known_mids_from_js(path)
    missing = find_missing_mids(con, tour, known_mids)
    if not missing:
        print(f"[{tour}] no missing players — bio list already covers all "
              "QF+ T12M players")
        return 0

    next_id = next_bio_id(bios_text)
    new_lines: list[str] = []
    skipped: list[int] = []
    for m in missing:
        meta = lookup_player_meta(con, m["mid"])
        if not meta:
            skipped.append(m["mid"])
            continue
        name, nat = meta
        line = make_bio_entry(next_id, m["mid"], name, nat,
                              short_name(name), m["deep_runs"],
                              m["last_run"], m["rounds"])
        new_lines.append(line)
        next_id += 1

    print(f"[{tour}] adding {len(new_lines)} bios "
          f"(skipped {len(skipped)} with no name in raw data)")
    for line in new_lines:
        print("  +", line.split("//")[0].strip())

    if dry_run or not new_lines:
        return len(new_lines)

    # Insert before the closing `];` of the bio array.
    insertion = "\n  // ────── auto-expanded by expand_bios.py ──────\n" + "\n".join(new_lines) + "\n"
    new_text = re.sub(r"(\n\];\s*\n?\s*$)", insertion + r"\1",
                      bios_text, count=1)
    if new_text == bios_text:
        # Didn't find the closing — bail rather than corrupt
        print(f"[{tour}] couldn't locate closing `];`, skipping write",
              file=sys.stderr)
        return 0
    path.write_text(new_text, encoding="utf-8")
    print(f"[{tour}] wrote {path.relative_to(ROOT)}")
    return len(new_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing files")
    parser.add_argument("--tour", choices=("atp", "wta", "both"),
                        default="both")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}. "
              "Run `python3 scripts/restore_db.py --force` first.",
              file=sys.stderr)
        return 1

    con = sqlite3.connect(DB_PATH)
    total = 0
    if args.tour in ("wta", "both"):
        total += expand_tour(con, "wta", WTA_PATH, args.dry_run)
    if args.tour in ("atp", "both"):
        total += expand_tour(con, "atp", ATP_PATH, args.dry_run)
    con.close()

    if total == 0:
        print("\nNo new bios added.")
    elif args.dry_run:
        print(f"\nDry run — would add {total} bios. "
              "Re-run without --dry-run to write.")
    else:
        print(f"\nAdded {total} bios. Next steps:")
        print("  python3 scripts/seed_db.py        # plumb new bios into DB")
        print("  python3 scripts/materialize.py    # regenerate data/*.js")
        print("  python3 scripts/upload_to_worker.py  # push to D1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
