#!/usr/bin/env python3
"""
materialize.py — Read the SQLite DB, write all `data/*.js` files the
dashboard expects.

PHASE 1 — replaces the output side of `refresh_rankings_api.py`,
`fetch_match_stats_api.py`, `write_trapezoid_from_json.py`, `build_h2h.py`,
and (importantly) `patch_wta_active.py`. The DB is now the only source of
truth; this script is a pure projection.

Outputs:
    data/season_atp.js          rankings + activeTournaments
    data/season_wta.js
    data/recent_matches.js      last 30 matches per top-200 bio
    data/tournament_history.js  per-bio deepest round per (tournament, year)
    data/h2h.js                 head-to-head pair records
    data/trapezoid_data.js      metrics × period × surface (2024 preserved
                                from prior file; 2025+ derived from DB)

Each writer computes a SHA-256 of the payload and embeds it in the file
header. On re-run, if the new hash equals the existing file's hash, the
file is left untouched — no spurious git diffs.

USAGE
-----
    python3 scripts/materialize.py               # all outputs
    python3 scripts/materialize.py --only season # one specific output
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect, init_db, REPO_ROOT


DATA_DIR = REPO_ROOT / "data"
TOP_N    = 200


# ─── Round semantics (was in wta_analytics.html enrichActiveTournaments) ────
# Maps the API's round string for each draw size to (played, next) stage
# strings. "played" = stage the player WAS IN (if they LOST that match).
# "next"   = stage they ADVANCE INTO (if they WON).
SEMANTICS: dict[int, dict[str, dict[str, str]]] = {
    128: {
        "First":  {"played": "R128", "next": "R64"},
        "Second": {"played": "R64",  "next": "R32"},
        "Third":  {"played": "R32",  "next": "R16"},
        "Fourth": {"played": "R16",  "next": "QF"},
        "1/8":    {"played": "R16",  "next": "QF"},
        "1/4":    {"played": "QF",   "next": "SF"},
        "1/2":    {"played": "SF",   "next": "F"},
        "Final":  {"played": "F",    "next": "W"},
    },
    96: {
        "First":  {"played": "R64",  "next": "R64"},
        "Second": {"played": "R64",  "next": "R32"},
        "Third":  {"played": "R32",  "next": "R16"},
        "Fourth": {"played": "R16",  "next": "QF"},
        "1/4":    {"played": "QF",   "next": "SF"},
        "1/2":    {"played": "SF",   "next": "F"},
        "Final":  {"played": "F",    "next": "W"},
    },
    64: {
        "First":  {"played": "R64",  "next": "R32"},
        "Second": {"played": "R32",  "next": "R16"},
        "Third":  {"played": "R16",  "next": "QF"},
        "1/4":    {"played": "QF",   "next": "SF"},
        "1/2":    {"played": "SF",   "next": "F"},
        "Final":  {"played": "F",    "next": "W"},
    },
    32: {
        "First":  {"played": "R32",  "next": "R16"},
        "Second": {"played": "R16",  "next": "QF"},
        "1/4":    {"played": "QF",   "next": "SF"},
        "1/2":    {"played": "SF",   "next": "F"},
        "Final":  {"played": "F",    "next": "W"},
    },
}

ROUND_DEPTH = {"R128":1, "R64":2, "R32":3, "R16":4, "QF":5, "SF":6, "F":7, "W":8}
RD_NAME_DEPTH = {
    "First":1, "Second":2, "Third":3, "Fourth":4, "1/8":4,
    "1/4":5, "1/2":6, "Final":7,
}

def semantics_for(draw_size: int | None) -> dict:
    if not draw_size:
        return SEMANTICS[32]
    if draw_size >= 128: return SEMANTICS[128]
    if draw_size >= 96:  return SEMANTICS[96]
    if draw_size >= 64:  return SEMANTICS[64]
    return SEMANTICS[32]


# ─── Hash-based change detection ────────────────────────────────────────────
HASH_HEADER_RE = re.compile(r"\* hash:\s*([0-9a-f]{16,64})\s*\*")

def _existing_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    head = path.read_text(encoding="utf-8")[:600]
    m = HASH_HEADER_RE.search(head)
    return m.group(1) if m else None


def _content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_if_changed(path: Path, payload_hash_input: str, render: callable,
                      *, label: str) -> bool:
    """Compute hash from `payload_hash_input` (a stable JSON string of the
    underlying data — excludes timestamps). If unchanged, leave file alone.
    Otherwise, call render(hash) → file body, write it.
    Returns True if file was written, False if skipped.
    """
    h = _content_hash(payload_hash_input)
    if _existing_hash(path) == h:
        print(f"  ✓ {label:<24} unchanged (hash {h})")
        return False
    body = render(h)
    path.write_text(body, encoding="utf-8")
    print(f"  ✓ {label:<24} wrote {len(body):>9,} chars (hash {h})")
    return True


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── 1. season_*.js ─────────────────────────────────────────────────────────

def _compute_active_tournaments(conn, tour: str) -> list[dict]:
    """For each active tournament, derive {id, stage, players: {bio_id: {r, elim}}}
    purely from match data. Replaces patch_wta_active.py + the client-side
    enrichActiveTournaments() path.
    """
    api_id_col = f"api_id_{tour}"
    actives = list(conn.execute(f"""
        SELECT id, name, draw_size, {api_id_col} AS api_id
        FROM tournaments
        WHERE active = 1 AND (tour = ? OR tour = 'both')
    """, (tour,)))

    out = []
    for t in actives:
        # All matches at this tournament for this tour. Match.tour is set on
        # ingest; tournament_id is set if the api_id mapping resolved.
        matches = list(conn.execute("""
            SELECT date, round, p1_id, p2_id, winner_id
            FROM matches
            WHERE tour = ? AND tournament_id = ?
            ORDER BY date ASC
        """, (tour, t["id"])))

        if not matches:
            continue

        sem = semantics_for(t["draw_size"])

        # Build map: mid → list of (date, round, won_bool)
        by_mid: dict[int, list] = defaultdict(list)
        for m in matches:
            for pid in (m["p1_id"], m["p2_id"]):
                won = (m["winner_id"] == pid) if m["winner_id"] else None
                by_mid[pid].append((m["date"], m["round"], won, m["winner_id"]))

        # Bio mapping for this tour (mid → bio_id) so we can write bio-keyed players.
        mid_to_bio = {row["mid"]: row["bio_id"] for row in conn.execute(
            "SELECT mid, bio_id FROM players WHERE tour = ?", (tour,)
        )}

        players_block: dict[int, dict] = {}
        deepest_played = 0
        deepest_round_name = None
        deepest_winner = None  # for tournament-level "stage" → W detection

        for mid, ms in by_mid.items():
            bio_id = mid_to_bio.get(mid)
            if not bio_id:
                continue  # not a tracked bio (qualifier, lower-ranked, etc.)
            # Latest match for this player (newest first by date, then by round depth).
            ms_sorted = sorted(ms, key=lambda x: (x[0], RD_NAME_DEPTH.get(x[1], 0)),
                               reverse=True)
            latest_dt, latest_rd, latest_won, latest_winner = ms_sorted[0]
            if latest_rd not in sem:
                continue
            mapping = sem[latest_rd]
            if latest_won is True:
                r = mapping["next"]
                elim = False
            else:
                # Lost OR unknown winner (W/O, RET, or missing API field).
                # Treating unknown as "out" is correct for finished tournaments
                # — if we can't prove they advanced, they didn't.
                r = mapping["played"]
                elim = True
            players_block[bio_id] = {"r": r, "elim": elim}

            # Track tournament-level deepest round for the `stage` field.
            d = ROUND_DEPTH.get(r, 0)
            if d > deepest_played:
                deepest_played = d
                deepest_round_name = r
                deepest_winner = latest_winner if latest_won else None

        if not players_block:
            continue

        # Post-process: if the tournament has progressed past a player's stage
        # (i.e. someone played a deeper round than them) and we don't have a
        # later match for that player, they were eliminated by an opponent
        # outside our top-200 fetch set. Mark elim:true so the dashboard
        # doesn't show them as "Active in <early round>" when the tournament
        # is well past it.
        tournament_deepest = deepest_played
        for bio_id, status in players_block.items():
            cur = ROUND_DEPTH.get(status["r"], 0)
            if cur < tournament_deepest and not status["elim"]:
                status["elim"] = True

        out.append({
            "id":      t["id"],
            "stage":   deepest_round_name or "?",
            "players": players_block,
        })
    return out


def _compute_results_per_player(conn, tour: str) -> dict[int, dict]:
    """For each bio, return {tournament_id: {r, pts}} of completed events.
    Only non-active tournaments (active=0) are counted as "results"; active
    tournaments are projected via activeTournaments[] separately.
    """
    api_id_col = f"api_id_{tour}"
    rows = list(conn.execute(f"""
        SELECT m.date, m.round, m.p1_id, m.p2_id, m.winner_id,
               t.id AS tid, t.draw_size, t.points_table, t.active
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE m.tour = ? AND t.active = 0
    """, (tour,)))
    mid_to_bio = {row["mid"]: row["bio_id"] for row in conn.execute(
        "SELECT mid, bio_id FROM players WHERE tour = ?", (tour,)
    )}

    # For each (bio, tournament): track deepest round + whether they won that round.
    deepest: dict[tuple[int, str], tuple[int, str, bool]] = {}
    tournament_meta: dict[str, dict] = {}
    for r in rows:
        sem = semantics_for(r["draw_size"])
        rd = r["round"]
        if rd not in sem:
            continue
        if r["tid"] not in tournament_meta:
            tournament_meta[r["tid"]] = {
                "draw_size": r["draw_size"],
                "points":    json.loads(r["points_table"]) if r["points_table"] else {},
            }
        for pid in (r["p1_id"], r["p2_id"]):
            bio_id = mid_to_bio.get(pid)
            if not bio_id:
                continue
            won = (r["winner_id"] == pid) if r["winner_id"] else False
            mapping = sem[rd]
            stage = mapping["next"] if won else mapping["played"]
            d = ROUND_DEPTH.get(stage, 0)
            key = (bio_id, r["tid"])
            if key not in deepest or d > deepest[key][0]:
                deepest[key] = (d, stage, won)

    results: dict[int, dict] = defaultdict(dict)
    for (bio_id, tid), (depth, stage, _won) in deepest.items():
        pts_table = tournament_meta.get(tid, {}).get("points") or {}
        pts = pts_table.get(stage, 0)
        results[bio_id][tid] = {"r": stage, "pts": pts}
    return results


def materialize_season(conn, tour: str) -> bool:
    today = date.today().isoformat()
    bios = list(conn.execute("""
        SELECT bio_id, name FROM players WHERE tour = ? ORDER BY bio_id
    """, (tour,)))

    # Latest snapshot per bio.
    latest_date_row = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM rankings_snapshots WHERE tour = ?", (tour,)
    ).fetchone()
    latest_date = latest_date_row["d"]
    snap = {row["bio_id"]: dict(row) for row in conn.execute("""
        SELECT bio_id, rank, pts, ytd_pts FROM rankings_snapshots
        WHERE tour = ? AND snapshot_date = ?
    """, (tour, latest_date))} if latest_date else {}

    # rankMove: compare against snapshot ≥7 days ago (most recent snapshot in that window).
    baseline_cutoff = (date.today() - timedelta(days=7)).isoformat()
    baseline_date_row = conn.execute("""
        SELECT MAX(snapshot_date) AS d FROM rankings_snapshots
        WHERE tour = ? AND snapshot_date <= ?
    """, (tour, baseline_cutoff)).fetchone()
    baseline_date = baseline_date_row["d"] if baseline_date_row else None
    baseline = {row["bio_id"]: row["rank"] for row in conn.execute("""
        SELECT bio_id, rank FROM rankings_snapshots
        WHERE tour = ? AND snapshot_date = ?
    """, (tour, baseline_date))} if baseline_date else {}

    results_by_bio = _compute_results_per_player(conn, tour)
    active_tournaments = _compute_active_tournaments(conn, tour)

    # Build the players block in bio_id order so the file diff is stable.
    players_obj: dict[int, dict] = {}
    for b in bios:
        bid = b["bio_id"]
        s = snap.get(bid) or {}
        prev_rank = baseline.get(bid)
        cur_rank  = s.get("rank") or bid
        rank_move = (prev_rank - cur_rank) if (prev_rank and cur_rank) else 0
        players_obj[bid] = {
            "rank":     cur_rank,
            "pts":      s.get("pts") or 0,
            "ytd":      s.get("ytd_pts") or 0,
            "rankMove": rank_move,
            "results":  results_by_bio.get(bid, {}),
        }

    # Hash input must EXCLUDE timestamps so unchanged data → unchanged hash.
    payload = {
        "activeTournaments": active_tournaments,
        "players":           players_obj,
    }
    hash_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    var_name = f"SEASON_{tour.upper()}"
    path     = DATA_DIR / f"season_{tour}.js"

    def render(h: str) -> str:
        # Pretty-print the players block to mirror the existing layout.
        active_str = _format_active_tournaments(active_tournaments)
        players_str = _format_players_block(players_obj)
        return (
            f"// season_{tour}.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f"// Do not edit manually. Last updated: {_now_utc()}\n"
            f"/* hash: {h} */\n"
            f"\n"
            f"const {var_name} = {{\n"
            f'  lastUpdated: "{today}",\n'
            f"  activeTournaments: [\n"
            f"{active_str}"
            f"  ],\n"
            f"  players: {{\n"
            f"{players_str}"
            f"  }}\n"
            f"}};\n"
        )

    return _write_if_changed(path, hash_input, render, label=f"season_{tour}")


def _format_active_tournaments(actives: list[dict]) -> str:
    out = []
    for at in actives:
        players_lines = ",\n".join(
            f'      {bid}: {{r:"{v["r"]}", elim:{str(v["elim"]).lower()}}}'
            for bid, v in sorted(at["players"].items())
        )
        out.append(
            f"    {{\n"
            f'      id: "{at["id"]}",\n'
            f'      stage: "{at["stage"]}",\n'
            f"      players: {{\n"
            f"{players_lines}\n"
            f"      }}\n"
            f"    }},\n"
        )
    return "".join(out)


def _format_players_block(players: dict[int, dict]) -> str:
    lines = []
    for bid in sorted(players):
        p = players[bid]
        if p["results"]:
            res_str = ",".join(
                f'{tid}:{{r:"{r["r"]}",pts:{r["pts"]}}}'
                for tid, r in sorted(p["results"].items())
            )
            res_blob = "{ " + res_str + " }"
        else:
            res_blob = "{ }"
        lines.append(
            f"    {bid}: {{ rank:{p['rank']}, pts:{p['pts']}, "
            f"ytd:{p['ytd']}, rankMove:{p['rankMove']}, results:{res_blob} }}"
        )
    return ",\n".join(lines) + "\n"


# ─── 2. recent_matches.js ───────────────────────────────────────────────────

def materialize_recent_matches(conn) -> bool:
    """Per top-N bio: last 30 matches with the form-bar / drill-down fields."""
    out: dict[str, dict] = {"atp": {}, "wta": {}}
    bios = list(conn.execute("""
        SELECT mid, bio_id, tour, name FROM players
        WHERE bio_id <= ?
    """, (TOP_N,)))

    # Lookup: mid → (name, country) for opponent enrichment
    mid_lookup = {row["mid"]: (row["name"], row["country"]) for row in conn.execute(
        "SELECT mid, name, country FROM players"
    )}

    for b in bios:
        mid = b["mid"]
        rows = list(conn.execute("""
            SELECT date, round, tournament_name, tournament_api_id,
                   p1_id, p2_id, winner_id, score
            FROM matches
            WHERE p1_id = ? OR p2_id = ?
            ORDER BY date DESC, round DESC
            LIMIT 30
        """, (mid, mid)))
        items = []
        for r in rows:
            we_p1 = (r["p1_id"] == mid)
            opp_mid = r["p2_id"] if we_p1 else r["p1_id"]
            opp_name, opp_country = mid_lookup.get(opp_mid, ("?", ""))
            won = (r["winner_id"] == mid) if r["winner_id"] else None
            entry = {
                "date":  r["date"],
                "tn":    r["tournament_name"] or "?",
                "rd":    r["round"] or "",
                "opp":   opp_name,
                "oppC":  opp_country or "",
                "score": r["score"] or "",
                "won":   won,
            }
            if r["tournament_api_id"]:
                entry["tId"] = r["tournament_api_id"]
            items.append(entry)
        out[b["tour"]][str(b["bio_id"])] = items

    hash_input = json.dumps(out, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "recent_matches.js"

    def render(h: str) -> str:
        payload = {"lastUpdated": _now_utc(), "atp": out["atp"], "wta": out["wta"]}
        return (
            "/**\n"
            " * recent_matches.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f" * Last updated: {_now_utc()}\n"
            f" * hash: {h}\n"
            " */\n"
            f"const RECENT_MATCHES = {json.dumps(payload, separators=(',', ':'))};\n"
        )

    return _write_if_changed(path, hash_input, render, label="recent_matches")


# ─── 3. tournament_history.js ───────────────────────────────────────────────

def materialize_tournament_history(conn) -> bool:
    """Per bio, deepest round per (tournament, year). For results display
    on the player drill-down + Live Events 'defending pts' lookup.
    """
    out: dict[str, dict] = {"atp": {}, "wta": {}}
    bios = list(conn.execute(
        "SELECT mid, bio_id, tour FROM players WHERE bio_id <= ?", (TOP_N,)
    ))
    for b in bios:
        mid = b["mid"]
        rows = list(conn.execute("""
            SELECT m.date, m.round, m.tournament_name, m.winner_id, m.p1_id, m.p2_id
            FROM matches m
            WHERE (m.p1_id = ? OR m.p2_id = ?) AND m.round IS NOT NULL
        """, (mid, mid)))
        # group by (tn, year) → deepest round
        by_te: dict[tuple[str, int], tuple[int, str, bool]] = {}
        for r in rows:
            tn = r["tournament_name"] or ""
            try:
                yr = int(r["date"][:4])
            except (ValueError, TypeError):
                continue
            depth = RD_NAME_DEPTH.get(r["round"], 0)
            if depth == 0:
                continue
            won = (r["winner_id"] == mid) if r["winner_id"] else False
            cur = by_te.get((tn, yr))
            if not cur or depth > cur[0]:
                by_te[(tn, yr)] = (depth, r["round"], won)
        items = []
        for (tn, yr), (_d, rd, won) in by_te.items():
            items.append({"tn": tn, "year": yr, "round": rd, "won": won})
        if items:
            out[b["tour"]][str(b["bio_id"])] = items

    hash_input = json.dumps(out, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "tournament_history.js"

    def render(h: str) -> str:
        payload = {"lastUpdated": _now_utc(), "atp": out["atp"], "wta": out["wta"]}
        return (
            "/**\n"
            " * tournament_history.js — AUTO-GENERATED by scripts/materialize.py\n"
            f" * Last updated: {_now_utc()}\n"
            f" * hash: {h}\n"
            " */\n"
            f"const TOURNAMENT_HISTORY = {json.dumps(payload, separators=(',', ':'))};\n"
        )

    return _write_if_changed(path, hash_input, render, label="tournament_history")


# ─── 4. h2h.js ──────────────────────────────────────────────────────────────

def materialize_h2h(conn) -> bool:
    """Per (bio_a, bio_b) within a tour: aW/bW/per-surface/recent-form/last-date."""
    out: dict[str, dict] = {"atp": {}, "wta": {}}
    bios = list(conn.execute(
        "SELECT mid, bio_id, tour FROM players WHERE bio_id <= ?", (TOP_N,)
    ))
    mid_to_bio = {(b["tour"], b["mid"]): b["bio_id"] for b in bios}

    # Walk all matches; only count when BOTH sides are top-N bios.
    for tour in ("atp", "wta"):
        rows = list(conn.execute("""
            SELECT date, p1_id, p2_id, winner_id, surface
            FROM matches
            WHERE tour = ? AND winner_id IS NOT NULL
            ORDER BY date ASC
        """, (tour,)))
        # accumulator: (a_bio, b_bio) → {aW, bW, surfaces:{H:[aW,bW], C:[..], G:[..]}, results:[date, A_won_bool], last}
        acc: dict[tuple[int, int], dict] = {}
        for r in rows:
            a_bio = mid_to_bio.get((tour, r["p1_id"]))
            b_bio = mid_to_bio.get((tour, r["p2_id"]))
            if not a_bio or not b_bio:
                continue
            # canonical order
            if a_bio > b_bio:
                a_bio, b_bio = b_bio, a_bio
                # swap winner perspective
                a_won = (r["winner_id"] == r["p2_id"])
            else:
                a_won = (r["winner_id"] == r["p1_id"])
            key = (a_bio, b_bio)
            d = acc.setdefault(key, {
                "aW": 0, "bW": 0,
                "surfaces": defaultdict(lambda: [0, 0]),
                "results": [],   # list of (date, a_won) tuples in chrono order
                "last":    None,
            })
            if a_won: d["aW"] += 1
            else:     d["bW"] += 1
            surf = r["surface"] or "H"
            if a_won: d["surfaces"][surf][0] += 1
            else:     d["surfaces"][surf][1] += 1
            d["results"].append((r["date"], a_won))
            d["last"] = r["date"] if not d["last"] or r["date"] > d["last"] else d["last"]

        # Render each entry
        out[tour] = {}
        for (a, b), d in sorted(acc.items()):
            surf_str = {s: f"{v[0]}-{v[1]}" for s, v in d["surfaces"].items()}
            recent = d["results"][-5:]      # last 5 from A's POV
            rec = [1 if won else 2 for (_dt, won) in recent]
            out[tour][f"{a}-{b}"] = {
                "aW":   d["aW"],
                "bW":   d["bW"],
                "s":    surf_str,
                "rec":  rec,
                "last": d["last"],
            }

    hash_input = json.dumps(out, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "h2h.js"

    def render(h: str) -> str:
        return (
            "/**\n"
            " * h2h.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f" * Last updated: {_now_utc()}\n"
            f" * hash: {h}\n"
            " *\n"
            " * Schema: keyed by tour, then by 'bioA-bioB' (sorted, low first).\n"
            " *   aW/bW   = wins for each side\n"
            " *   s       = per-surface 'aW-bW' strings (H/C/G)\n"
            " *   rec     = last 5 outcomes from A's POV (1=W, 2=L)\n"
            " *   last    = date of most recent meeting\n"
            " */\n"
            f"const H2H = {json.dumps(out, separators=(',', ':'))};\n"
        )

    return _write_if_changed(path, hash_input, render, label="h2h")


# ─── 5. trapezoid_data.js ───────────────────────────────────────────────────
# 2024 data is preserved from the existing file (Sackmann origin, frozen).
# 2025+ aggregations are derived from the DB via the same logic that
# fetch_match_stats_api.py used to use.

SET_RE = re.compile(r"(\d+)-(\d+)(?:\((\d+)\))?")
MIN_MATCHES = 5

def _safe_pct(num: int, denom: int) -> float | None:
    return round(100 * num / denom, 1) if denom else None

def _is_real_match(score: str | None) -> bool:
    if not score:
        return False
    s = score.upper()
    return not any(p in s for p in (" RET", " W/O", " DEF", "W/O", "RET", "DEF"))

# Tier filter: matches at events in this set are "tour-level competition."
# Anything else (W125, M125, ITF, Challenger, NULL) is excluded from the
# trapezoid composite by default. See plan: ~/.claude/plans/the-match-history-for-async-pie.md
TOUR_TIERS = {"GS", "M1000", "W1000", "M500", "W500", "M250", "W250",
              "ATPFinals", "WTAFinals"}

def _is_tour_level(m: dict) -> bool:
    return (m.get("t_type") in TOUR_TIERS)

def _is_main_draw(m: dict) -> bool:
    """Drop qualifying rounds (Q1/Q2/Q3/Q4 — Matchstat 'round' values)."""
    rd = m.get("round") or ""
    return not rd.startswith("Q")


def _aggregate_year(matches: list[dict], mid: int,
                    min_matches: int = MIN_MATCHES,
                    min_tb: int = 3, min_dec: int = 3,
                    tour_only: bool = True) -> dict | None:
    real = [m for m in matches if _is_real_match(m.get("score"))]
    if tour_only:
        real = [m for m in real if _is_tour_level(m) and _is_main_draw(m)]
    if len(real) < min_matches:
        return None
    n = len(real); wins = 0
    svpt = first_in = first_won = second_won = aces = bp_saved = bp_faced = df = 0
    opp_svpt = opp_first_won = opp_second_won = 0
    tb_played = tb_won = dec_played = dec_won = 0
    used = 0
    for m in real:
        we_p1 = (m["p1_id"] == mid)
        won_match = (m["winner_id"] == mid) if m["winner_id"] else False
        if won_match: wins += 1

        own_blob = m["stat_p1"] if we_p1 else m["stat_p2"]
        opp_blob = m["stat_p2"] if we_p1 else m["stat_p1"]
        own = json.loads(own_blob) if own_blob else None
        opp = json.loads(opp_blob) if opp_blob else None
        if own and opp:
            def _g(d, *keys):
                for k in keys:
                    if k in d and d[k] not in (None, "", "NA"):
                        return d[k]
                return 0
            def _i(d, *keys):
                try:
                    return int(_g(d, *keys) or 0)
                except (TypeError, ValueError):
                    try:
                        return int(float(_g(d, *keys)))
                    except (TypeError, ValueError):
                        return 0
            try:
                # Per-side counters. Matchstat key names vary across endpoints,
                # so we try multiple aliases in order.
                own_bp_faced  = _i(own, "breakPointFaced","breakPointsFaced","bpFaced")
                own_bp_saved  = _i(own, "breakPointSaved","breakPointsSaved","bpSaved")
                opp_bp_conv   = _i(opp, "breakPointsConverted","breakPointConverted","bpConv")
                opp_bp_attempt = _i(opp, "breakPointsConvertedOf","breakPointsAttempted",
                                       "breakPointAttempted","bpAttempt")
                # Backfill: if our bp counters are missing, use opponent's
                # converted/attempted (which are recorded against our serve).
                # This is the key fix that was lost when porting from
                # fetch_match_stats_api.py — most rows only have one side.
                if not own_bp_faced and opp_bp_attempt:
                    own_bp_faced = opp_bp_attempt
                if not own_bp_saved and own_bp_faced:
                    own_bp_saved = own_bp_faced - opp_bp_conv

                svpt        += _i(own, "firstServeOf","totalServePointsAttempted","serveOf","serveOfGm","svpt")
                first_in    += _i(own, "firstServe","firstServeIn","1stIn")
                first_won   += _i(own, "winningOnFirstServe","firstServeWon","1stWon")
                second_won  += _i(own, "winningOnSecondServe","secondServeWon","2ndWon")
                aces        += _i(own, "aces","ace")
                df          += _i(own, "doubleFaults","doubleFault","df")
                bp_faced    += own_bp_faced
                bp_saved    += own_bp_saved
                opp_svpt        += _i(opp, "firstServeOf","totalServePointsAttempted","svpt")
                opp_first_won   += _i(opp, "winningOnFirstServe","firstServeWon","1stWon")
                opp_second_won  += _i(opp, "winningOnSecondServe","secondServeWon","2ndWon")
                used += 1
            except (TypeError, ValueError):
                pass

        # tiebreak / decider derivation from score
        sets = []
        for s in SET_RE.findall(m.get("score") or ""):
            wg, lg, tb_pts = s
            wg_i, lg_i = int(wg), int(lg)
            if max(wg_i, lg_i) >= 6:
                sets.append((wg_i, lg_i, tb_pts or None))
        for (wg, lg, tb_pts) in sets:
            if tb_pts is not None:
                tb_played += 1
                set_winner_was_us = ((won_match and wg > lg) or (not won_match and wg < lg))
                if set_winner_was_us: tb_won += 1
        n_sets = len(sets)
        bo = m.get("best_of") or (5 if n_sets >= 4 else 3)
        if n_sets >= bo:
            dec_played += 1
            if won_match: dec_won += 1

    sv_gms = round(svpt / 6.5) if svpt else 0
    return {
        "matches":         n,
        "matchWinPct":     round(100 * wins / n, 1) if n else None,
        "servePtsWonPct":  _safe_pct(first_won + second_won, svpt),
        "returnPtsWonPct": _safe_pct(opp_svpt - opp_first_won - opp_second_won, opp_svpt),
        "totalPtsWonPct":  _safe_pct((first_won + second_won) + (opp_svpt - opp_first_won - opp_second_won),
                                     svpt + opp_svpt),
        "acesPerSvGm":     round(aces / sv_gms, 2) if sv_gms else None,
        "bpSavedPct":      _safe_pct(bp_saved, bp_faced),
        "tbWinPct":        _safe_pct(tb_won, tb_played) if tb_played >= min_tb else None,
        "decSetWinPct":    _safe_pct(dec_won, dec_played) if dec_played >= min_dec else None,
    }


def _trapezoid_rows(conn, tour: str, all_year_matches: dict[int, list[dict]],
                    bios: list[dict]) -> dict[str, list[dict]]:
    """Return {year_key: [rows]} where year_key in {'2025','2026','T12M','T6M','T3M','CURR'} + surface variants."""
    today = date.today()
    windows = {
        "T12M": today - timedelta(days=365),
        "T6M":  today - timedelta(days=180),
        "T3M":  today - timedelta(days=90),
    }

    rows_by: dict[str, list[dict]] = defaultdict(list)
    for b in bios:
        mid    = b["mid"]
        bio_id = b["bio_id"]
        all_ms = all_year_matches.get(mid, [])

        # Per-calendar-year (2025, 2026, ...)
        by_cal: dict[int, list] = defaultdict(list)
        for m in all_ms:
            try:
                by_cal[int(m["date"][:4])].append(m)
            except (ValueError, TypeError):
                continue
        for yr, ms in by_cal.items():
            if yr == 2024:  # 2024 preserved separately from existing file
                continue
            agg = _aggregate_year(ms, mid)
            if agg:
                rows_by[str(yr)].append({
                    "id": str(mid), "bioId": bio_id, "name": b["name"],
                    "ioc": b["country"] or "", "year": str(yr), "tour": tour.upper(),
                    "surf": "All", **agg,
                })

        # Rolling windows (T12M/T6M/T3M) all-surface
        def _dt(m):
            try:
                return date.fromisoformat(m["date"][:10])
            except (ValueError, TypeError):
                return None
        for tag, cutoff in windows.items():
            window_ms = [m for m in all_ms if (d := _dt(m)) and d >= cutoff]
            if not window_ms:
                continue
            # Apply tier filter ONCE here so the per-surface threshold below
            # also operates on tour-level matches only.
            window_tour = [m for m in window_ms
                           if _is_tour_level(m) and _is_main_draw(m)]
            agg = _aggregate_year(window_tour, mid, tour_only=False)
            if agg:
                rows_by[tag].append({
                    "id": str(mid), "bioId": bio_id, "name": b["name"],
                    "ioc": b["country"] or "", "year": tag, "tour": tour.upper(),
                    "surf": "All", **agg,
                })
            # Per-surface variants — tier already applied
            by_surf: dict[str, list] = defaultdict(list)
            for m in window_tour:
                by_surf[m["surface"] or "H"].append(m)
            for surf, surf_ms in by_surf.items():
                if len(surf_ms) < 5:
                    continue
                surf_agg = _aggregate_year(surf_ms, mid, tour_only=False)
                if not surf_agg:
                    continue
                rows_by[f"{tag}_{surf}"].append({
                    "id": str(mid), "bioId": bio_id, "name": b["name"],
                    "ioc": b["country"] or "", "year": tag, "tour": tour.upper(),
                    "surf": surf, **surf_agg,
                })

        # CURR — last 14 days, lower threshold to surface in-tournament players.
        # Keep tour_only=False so a player active at a W125 still appears as
        # "what they're doing right now" — this view is about presence, not metric ranking.
        curr_ms = [m for m in all_ms
                   if (d := _dt(m)) and d >= (today - timedelta(days=14))]
        if curr_ms:
            curr_agg = _aggregate_year(curr_ms, mid,
                                       min_matches=1, min_tb=1, min_dec=1,
                                       tour_only=False)
            if curr_agg:
                rows_by["CURR"].append({
                    "id": str(mid), "bioId": bio_id, "name": b["name"],
                    "ioc": b["country"] or "", "year": "CURR", "tour": tour.upper(),
                    "surf": "All", **curr_agg,
                })

    return rows_by


def _load_existing_2024(path: Path) -> dict:
    """Pull 2024 rows from the current trapezoid_data.js so we don't lose
    Sackmann-baked data that the DB doesn't have."""
    if not path.exists():
        return {"atp": [], "wta": []}
    raw = path.read_text(encoding="utf-8")
    out = {"atp": [], "wta": []}
    for tour in ("atp", "wta"):
        m = re.search(rf"const\s+TRAPEZOID_{tour.upper()}\s*=\s*(\[[\s\S]*?\]);", raw)
        if not m:
            continue
        try:
            rows = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        out[tour] = [r for r in rows if r.get("year") == "2024"]
    return out


def materialize_trapezoid(conn) -> bool:
    bios_atp = list(conn.execute("SELECT mid, bio_id, name, country FROM players WHERE tour='atp' AND bio_id <= ?", (TOP_N,)))
    bios_wta = list(conn.execute("SELECT mid, bio_id, name, country FROM players WHERE tour='wta' AND bio_id <= ?", (TOP_N,)))
    bios_atp = [dict(r) for r in bios_atp]
    bios_wta = [dict(r) for r in bios_wta]

    # Pull all matches once per tour, group by mid. Pull tournaments.type via
    # LEFT JOIN so the tier filter in _aggregate_year can decide tour vs
    # non-tour level. Unresolved matches (no tournament_id) get t_type=NULL
    # which TOUR_TIERS rejects → automatically excluded.
    def _matches_by_mid(tour: str) -> dict[int, list[dict]]:
        cur = conn.execute("""
            SELECT m.date, m.round, m.p1_id, m.p2_id, m.winner_id, m.score,
                   m.surface, m.best_of, m.stat_p1, m.stat_p2,
                   m.tournament_id, t.type AS t_type
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE m.tour = ?
        """, (tour,))
        out: dict[int, list[dict]] = defaultdict(list)
        for r in cur:
            out[r["p1_id"]].append(dict(r))
            out[r["p2_id"]].append(dict(r))
        return out

    atp_by_mid = _matches_by_mid("atp")
    wta_by_mid = _matches_by_mid("wta")

    rows_atp_by_year = _trapezoid_rows(conn, "atp", atp_by_mid, bios_atp)
    rows_wta_by_year = _trapezoid_rows(conn, "wta", wta_by_mid, bios_wta)

    # Preserve 2024 from existing file (Sackmann data not in DB)
    existing_2024 = _load_existing_2024(DATA_DIR / "trapezoid_data.js")
    rows_atp_by_year["2024"] = existing_2024["atp"]
    rows_wta_by_year["2024"] = existing_2024["wta"]

    # Flatten into TRAPEZOID_ATP / TRAPEZOID_WTA / TRAPEZOID_YEARS
    flat_atp = [r for rows in rows_atp_by_year.values() for r in rows]
    flat_wta = [r for rows in rows_wta_by_year.values() for r in rows]
    years_seen = sorted(set(rows_atp_by_year) | set(rows_wta_by_year), key=lambda y: (
        # Calendar years descending, then rolling tags
        (-int(y) if y.isdigit() else 0, y)
    ))

    metrics = [
        "matches", "servePtsWonPct", "returnPtsWonPct", "totalPtsWonPct",
        "tbWinPct", "decSetWinPct", "acesPerSvGm", "bpSavedPct", "matchWinPct",
    ]
    labels = {
        "matches":         "Matches Played",
        "servePtsWonPct":  "Serve Points Won %",
        "returnPtsWonPct": "Return Points Won %",
        "totalPtsWonPct":  "Total Points Won %",
        "tbWinPct":        "Tiebreak Win %",
        "decSetWinPct":    "Deciding Set Win %",
        "acesPerSvGm":     "Aces / Service Game",
        "bpSavedPct":      "Break Points Saved %",
        "matchWinPct":     "Match Win %",
    }

    hash_input = json.dumps({"atp": flat_atp, "wta": flat_wta,
                             "metrics": metrics, "labels": labels},
                            sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "trapezoid_data.js"

    def render(h: str) -> str:
        return (
            "/**\n"
            " * trapezoid_data.js — AUTO-GENERATED by scripts/materialize.py\n"
            f" * Last updated: {_now_utc()}\n"
            f" * Years: {', '.join(years_seen)}\n"
            f" * hash: {h}\n"
            " * Source (2025+): Matchstat Tennis API via SQLite materialization.\n"
            " * Source (2024):  github.com/JeffSackmann/tennis_atp & tennis_wta (CC BY-NC-SA 4.0)\n"
            " */\n"
            f"const TRAPEZOID_ATP = {json.dumps(flat_atp, separators=(',', ':'))};\n"
            f"const TRAPEZOID_WTA = {json.dumps(flat_wta, separators=(',', ':'))};\n"
            f"const TRAPEZOID_YEARS = {json.dumps(years_seen)};\n"
            f"const TRAPEZOID_METRICS = {json.dumps(metrics)};\n"
            f"const TRAPEZOID_LABELS = {json.dumps(labels, indent=2)};\n"
        )

    return _write_if_changed(path, hash_input, render, label="trapezoid")


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--only", choices=["season", "recent_matches", "tournament_history",
                                      "h2h", "trapezoid"], default=None)
    args = p.parse_args()

    init_db(verbose=False)
    conn = connect()

    print(f"=== materializing data/*.js from data/tennis.db ===")
    changed = 0

    if args.only in (None, "season"):
        if materialize_season(conn, "atp"): changed += 1
        if materialize_season(conn, "wta"): changed += 1
    if args.only in (None, "recent_matches"):
        if materialize_recent_matches(conn): changed += 1
    if args.only in (None, "tournament_history"):
        if materialize_tournament_history(conn): changed += 1
    if args.only in (None, "h2h"):
        if materialize_h2h(conn): changed += 1
    if args.only in (None, "trapezoid"):
        if materialize_trapezoid(conn): changed += 1

    print(f"\n{changed} file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
