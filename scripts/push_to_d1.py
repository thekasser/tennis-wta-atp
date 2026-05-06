#!/usr/bin/env python3
"""
push_to_d1.py — Extract the JSON literal from each data/*.js file and emit
a single SQL file that uploads them to D1's `materialized` table.

USAGE
-----
    python3 scripts/push_to_d1.py
    # writes workers/seed/materialized.sql
    wrangler d1 execute tennis --remote --yes --file=workers/seed/materialized.sql

The Worker reads from this table:
    SELECT payload FROM materialized WHERE name = ?

Names emitted (each maps to one row in `materialized`):
    season_atp, season_wta, recent_matches, h2h, trapezoid,
    tournament_history, players_atp, players_wta, tournaments
"""
from __future__ import annotations
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import REPO_ROOT


DATA_DIR = REPO_ROOT / "data"
OUT_FILE = REPO_ROOT / "workers" / "seed" / "materialized.sql"


# Map (name in materialized table) → (file, marker preceding the literal,
# open/close brackets to balance-scan). Bracket-balanced beats regex because
# materialize.py's pretty-printed season_*.js has nested braces inside
# `players: {…}`, which a non-greedy regex would truncate.
SOURCES = [
    ("season_atp",         "season_atp.js",         "const SEASON_ATP",        "{", "}"),
    ("season_wta",         "season_wta.js",         "const SEASON_WTA",        "{", "}"),
    ("recent_matches",     "recent_matches.js",     "const RECENT_MATCHES",    "{", "}"),
    ("h2h",                "h2h.js",                "const H2H",               "{", "}"),
    ("tournament_history", "tournament_history.js", "const TOURNAMENT_HISTORY","{", "}"),
    ("tournaments",        "tournaments.js",        "const TOURNAMENTS_DATA",  "[", "]"),
    ("players_atp",        "players_atp.js",        "const PLAYERS_ATP",       "[", "]"),
    ("players_wta",        "players_wta.js",        "const PLAYERS_WTA",       "[", "]"),
    ("upcoming_matches",   "upcoming_matches.js",   "const UPCOMING_MATCHES",  "{", "}"),
    ("predictions",        "predictions.js",        "const PREDICTIONS_DATA",  "{", "}"),
    # Trapezoid handled separately — 5 const exports to bundle.
]


def _extract_balanced(text: str, marker: str, open_ch: str, close_ch: str) -> str:
    """Find `marker` in `text`, then return the substring from the first
    `open_ch` after it to its bracket-balanced matching `close_ch`."""
    idx = text.find(marker)
    if idx < 0:
        raise ValueError(f"marker {marker!r} not found")
    start = text.find(open_ch, idx)
    if start < 0:
        raise ValueError(f"{open_ch!r} not found after marker {marker!r}")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError(f"unbalanced {open_ch}{close_ch} from char {start}")


def _extract(path: Path, marker: str, open_ch: str, close_ch: str) -> str:
    """Read file, balance-scan the literal, normalize JS→JSON, parse + redump."""
    text = path.read_text(encoding="utf-8")
    raw = _extract_balanced(text, marker, open_ch, close_ch)
    raw = _to_json(raw)
    parsed = json.loads(raw)
    return json.dumps(parsed, separators=(",", ":"))


_KEY_RE         = re.compile(r"(?P<sep>[\{,])\s*(?P<key>\w+)\s*:")
_LEADING_DOT_RE = re.compile(r"(?<=[:\s,\[])(\.\d+)")
_TRAILING_COMMA = re.compile(r",(\s*[\]\}])")
_LINE_COMMENT   = re.compile(r"//[^\n]*")
_BLOCK_COMMENT  = re.compile(r"/\*[\s\S]*?\*/")


def _to_json(s: str) -> str:
    """JS object literal → JSON. Mirrors logic in scripts/seed_db.py."""
    s = _BLOCK_COMMENT.sub("", s)
    s = _LINE_COMMENT.sub("", s)
    s = _KEY_RE.sub(lambda m: f'{m.group("sep")}"{m.group("key")}":', s)
    s = _LEADING_DOT_RE.sub(r"0\1", s)
    s = _TRAILING_COMMA.sub(r"\1", s)
    return s


def _extract_trapezoid(path: Path) -> str:
    """Bundle the 6 trapezoid exports into one JSON object."""
    text = path.read_text(encoding="utf-8")
    payload = {}
    sources = [
        ("atp",         "const TRAPEZOID_ATP",          "[", "]"),
        ("wta",         "const TRAPEZOID_WTA",          "[", "]"),
        ("years",       "const TRAPEZOID_YEARS",        "[", "]"),
        ("metrics",     "const TRAPEZOID_METRICS",      "[", "]"),
        ("labels",      "const TRAPEZOID_LABELS",       "{", "}"),
        ("minDefaults", "const TRAPEZOID_MIN_DEFAULTS", "{", "}"),
    ]
    for key, marker, open_ch, close_ch in sources:
        try:
            raw = _extract_balanced(text, marker, open_ch, close_ch)
            payload[key] = json.loads(_to_json(raw))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  warn: trapezoid_data.js missing {key}: {e}", file=sys.stderr)
    return json.dumps(payload, separators=(",", ":"))


def _sql_escape(s: str) -> str:
    return s.replace("'", "''")


# D1 caps individual SQL statements at ~100KB. Chunk each JSON blob into
# pieces well under that to leave headroom for SQL syntax + escaped quotes.
CHUNK_SIZE = 40_000


def _emit_inserts(name: str, json_str: str, now: str) -> tuple[list[str], int]:
    """Chunk `json_str` and return (insert_statements, n_chunks)."""
    out = []
    n = 0
    for chunk_no, start in enumerate(range(0, len(json_str), CHUNK_SIZE)):
        piece = json_str[start:start + CHUNK_SIZE]
        out.append(
            f"INSERT OR REPLACE INTO materialized "
            f"(name, chunk_no, payload, size_bytes, updated_at) "
            f"VALUES ('{name}', {chunk_no}, '{_sql_escape(piece)}', "
            f"{len(piece)}, '{now}');"
        )
        n += 1
    return out, n


def main() -> int:
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        f"-- Generated by scripts/push_to_d1.py at {now}",
        "-- Apply: wrangler d1 execute tennis --remote --yes --file=workers/seed/materialized.sql",
        "-- Chunked at ~40KB per row to stay under D1's per-statement cap.",
        "",
        # Wipe old rows first so a re-run with fewer chunks doesn't leave orphans.
        "DELETE FROM materialized;",
        "",
    ]

    summaries = []
    for name, fname, marker, open_ch, close_ch in SOURCES:
        path = DATA_DIR / fname
        if not path.exists():
            print(f"  skip {name}: {path} not found", file=sys.stderr)
            continue
        try:
            json_str = _extract(path, marker, open_ch, close_ch)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  ERROR {name}: {e}", file=sys.stderr)
            return 1
        inserts, n_chunks = _emit_inserts(name, json_str, now)
        lines.extend(inserts)
        summaries.append((name, len(json_str), n_chunks))

    # Trapezoid (6-in-1 bundle)
    trap_path = DATA_DIR / "trapezoid_data.js"
    if trap_path.exists():
        try:
            trap_json = _extract_trapezoid(trap_path)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  ERROR trapezoid: {e}", file=sys.stderr)
            return 1
        inserts, n_chunks = _emit_inserts("trapezoid", trap_json, now)
        lines.extend(inserts)
        summaries.append(("trapezoid", len(trap_json), n_chunks))

    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    total_chunks = sum(c for _, _, c in summaries)
    print(f"[push] wrote {OUT_FILE.relative_to(REPO_ROOT)} ({OUT_FILE.stat().st_size:,} bytes, "
          f"{total_chunks} chunks)")
    print(f"[push] {len(summaries)} blobs:")
    for name, size, chunks in sorted(summaries, key=lambda x: -x[1]):
        print(f"  {name:<22} {size:>10,} bytes  →  {chunks:>3} chunk(s)")
    print(f"\nApply with:")
    print(f"  wrangler d1 execute tennis --remote --yes --file={OUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
