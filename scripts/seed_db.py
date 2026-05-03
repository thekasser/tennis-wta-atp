#!/usr/bin/env python3
"""
seed_db.py — Populate `players` and `tournaments` tables from the existing
JS data files. Idempotent (UPSERT on conflict); safe to re-run after editing
data/players_*.js or data/tournaments.js.

USAGE
-----
    python3 scripts/seed_db.py              # seed both tables
    python3 scripts/seed_db.py --only players
    python3 scripts/seed_db.py --only tournaments

This is a one-time-ish bootstrap script: it converts the hand-curated bio
and calendar files into DB rows. The DB is then the source of truth; the
JS files become the inputs to occasional re-seeds (when a new player is
added, when the season calendar is set, etc.).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect, init_db, REPO_ROOT


PLAYERS_FILES = {
    "atp": REPO_ROOT / "data" / "players_atp.js",
    "wta": REPO_ROOT / "data" / "players_wta.js",
}
TOURNAMENTS_FILE = REPO_ROOT / "data" / "tournaments.js"


# ─── JS object-literal parsing ───────────────────────────────────────────────
# data/players_*.js and data/tournaments.js are JS literal arrays — keys are
# unquoted (e.g. `id:1`) and floats may be written as `.65`. Convert to
# JSON-parseable text, then `json.loads` does the rest.

_KEY_RE         = re.compile(r"(?P<sep>[\{,])\s*(?P<key>[A-Za-z_]\w*)\s*:")
_LEADING_DOT_RE = re.compile(r"(?<=[:\s,\[])(\.\d+)")        # ":  .65" → ":  0.65"
_TRAILING_COMMA = re.compile(r",(\s*[\]\}])")                # trailing commas in arrays/objects
_LINE_COMMENT   = re.compile(r"//[^\n]*")                    # `// foo` to end of line
_BLOCK_COMMENT  = re.compile(r"/\*[\s\S]*?\*/")              # `/* … */` (greedy-min)

def _to_json(js_object_literal: str) -> str:
    s = js_object_literal
    # Comments first — must run before key-quoting so we don't mangle commented-out keys.
    # NOTE: this assumes no `//` or `/*` appears inside a string literal in our data files
    # (they don't — no URLs, no embedded comment markers).
    s = _BLOCK_COMMENT.sub("", s)
    s = _LINE_COMMENT.sub("", s)
    s = _KEY_RE.sub(lambda m: f'{m.group("sep")}"{m.group("key")}":', s)
    s = _LEADING_DOT_RE.sub(r"0\1", s)
    s = _TRAILING_COMMA.sub(r"\1", s)
    return s


def parse_js_array(js_text: str, var_name: str) -> list[dict]:
    """Extract `const VAR_NAME = [...]` and return as Python list of dicts."""
    # Find the array — first '[' after `const VAR_NAME =`, balanced bracket-counted to its match.
    m = re.search(rf"const\s+{re.escape(var_name)}\s*=\s*\[", js_text)
    if not m:
        raise ValueError(f"could not find `const {var_name} = [` in source")
    start = m.end() - 1   # position of the `[`
    depth = 0
    end   = None
    for i, ch in enumerate(js_text[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError(f"unbalanced brackets in `{var_name}` array")
    array_src = js_text[start:end]
    return json.loads(_to_json(array_src))


# ─── Seed: players ───────────────────────────────────────────────────────────

def seed_players(conn) -> dict[str, int]:
    counts = {}
    for tour, path in PLAYERS_FILES.items():
        if not path.exists():
            print(f"  skipped {tour}: {path} not found", file=sys.stderr)
            counts[tour] = 0
            continue
        var = "PLAYERS_ATP" if tour == "atp" else "PLAYERS_WTA"
        rows = parse_js_array(path.read_text(encoding="utf-8"), var)
        n = 0
        for r in rows:
            mid = r.get("mid")
            if not mid:
                # Bios without `mid` haven't been linked to Matchstat yet — skip.
                # `link_bios_to_api.py` covers that flow when adding new players.
                continue
            meta = {
                "surf": r.get("surf"),
                "form": r.get("form"),
                "inj":  r.get("inj"),
            }
            conn.execute("""
                INSERT INTO players (mid, tour, bio_id, sid, name, ab, country, age, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mid) DO UPDATE SET
                    tour     = excluded.tour,
                    bio_id   = excluded.bio_id,
                    sid      = excluded.sid,
                    name     = excluded.name,
                    ab       = excluded.ab,
                    country  = excluded.country,
                    age      = excluded.age,
                    meta     = excluded.meta
            """, (
                mid,
                tour,
                r["id"],
                r.get("sid"),
                r["name"],
                r.get("ab"),
                r.get("nat"),
                r.get("age"),
                json.dumps(meta, separators=(",", ":")),
            ))
            n += 1
        counts[tour] = n
    conn.commit()
    return counts


# ─── Seed: tournaments ───────────────────────────────────────────────────────

def seed_tournaments(conn) -> int:
    if not TOURNAMENTS_FILE.exists():
        print(f"  skipped: {TOURNAMENTS_FILE} not found", file=sys.stderr)
        return 0

    js = TOURNAMENTS_FILE.read_text(encoding="utf-8")
    rows = parse_js_array(js, "TOURNAMENTS_DATA")

    # The PTS table sits below the array — extract it so we can attach the
    # right per-round points lookup to each tournament row.
    pts_match = re.search(r"const\s+PTS\s*=\s*(\{[\s\S]*?\});", js)
    pts_table = json.loads(_to_json(pts_match.group(1))) if pts_match else {}

    def points_table_for(t: dict) -> dict | None:
        """Pick the right PTS bucket for a tournament, mirroring tournaments.js logic."""
        ttype = t.get("type")
        if not ttype:
            return None
        # M1000 has two draw sizes (128 vs 96); the JS does the same lookup.
        if ttype == "M1000":
            ttype = "M1000_128" if t.get("draw") == 128 else "M1000_96"
        return pts_table.get(ttype)

    n = 0
    for r in rows:
        api = r.get("apiId") or {}
        conn.execute("""
            INSERT INTO tournaments
                (id, name, short, tour, type, surface, draw_size,
                 start_date, end_date, api_id_atp, api_id_wta, active, points_table)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name          = excluded.name,
                short         = excluded.short,
                tour          = excluded.tour,
                type          = excluded.type,
                surface       = excluded.surface,
                draw_size     = excluded.draw_size,
                start_date    = excluded.start_date,
                end_date      = excluded.end_date,
                api_id_atp    = excluded.api_id_atp,
                api_id_wta    = excluded.api_id_wta,
                active        = excluded.active,
                points_table  = excluded.points_table
        """, (
            r["id"],
            r["name"],
            r.get("short"),
            (r.get("tour") or "").lower(),
            r.get("type"),
            r.get("surf"),
            r.get("draw"),
            r.get("startDate"),
            r.get("endDate"),
            api.get("atp"),
            api.get("wta"),
            1 if r.get("active") else 0,
            json.dumps(points_table_for(r), separators=(",", ":")) if points_table_for(r) else None,
        ))
        n += 1
    conn.commit()
    return n


def reresolve_match_tournament_ids(conn) -> int:
    """After tournaments table is updated, fill in tournament_id for any
    existing matches whose tournament_api_id now resolves but didn't before.
    Idempotent. Returns number of rows updated."""
    cur = conn.execute("""
        UPDATE matches
           SET tournament_id = (
               SELECT t.id FROM tournaments t
               WHERE (matches.tour='atp' AND t.api_id_atp = matches.tournament_api_id)
                  OR (matches.tour='wta' AND t.api_id_wta = matches.tournament_api_id)
           )
         WHERE tournament_id IS NULL
           AND tournament_api_id IS NOT NULL
    """)
    conn.commit()
    return cur.rowcount


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--only", choices=["players", "tournaments"], default=None)
    args = p.parse_args()

    init_db()
    conn = connect()

    if args.only in (None, "players"):
        counts = seed_players(conn)
        print(f"[seed] players: ATP={counts.get('atp', 0)}, WTA={counts.get('wta', 0)}")
    if args.only in (None, "tournaments"):
        n = seed_tournaments(conn)
        print(f"[seed] tournaments: {n}")
        # Catalog expansion is the typical reason to re-seed tournaments —
        # also re-resolve any existing matches whose api_id now matches.
        m = reresolve_match_tournament_ids(conn)
        if m:
            print(f"[seed] re-resolved tournament_id on {m:,} existing matches")

    return 0


if __name__ == "__main__":
    sys.exit(main())
