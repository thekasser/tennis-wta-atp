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
    purely from match data.

    "Active" is determined by date window, NOT the manually-set tournaments.active
    flag — that was failing during weekly transitions (Madrid still showing as
    current after the final, Rome quals not picked up). The window is:
        start_date − 7 days  (qualifying rounds are typically -1 week)
        end_date   + 1 day   (1-day grace period after the final)
    A tournament inside that window with at least one match in our DB shows
    as active. The active flag in tournaments.js is now informational only.
    """
    today = date.today()
    api_id_col = f"api_id_{tour}"
    candidates = list(conn.execute(f"""
        SELECT id, name, type, draw_size, start_date, end_date, {api_id_col} AS api_id
        FROM tournaments
        WHERE (tour = ? OR tour = 'both')
          AND start_date IS NOT NULL AND end_date IS NOT NULL
    """, (tour,)))
    actives = []
    for c in candidates:
        try:
            sd = date.fromisoformat(c["start_date"])
            ed = date.fromisoformat(c["end_date"])
        except (ValueError, TypeError):
            continue
        quals_start = sd - timedelta(days=7)
        grace_end   = ed + timedelta(days=1)
        if quals_start <= today <= grace_end:
            actives.append(c)

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

        # Augment with fixtures-based players who haven't played yet but are
        # in the draw. Without this, the early-tournament player count
        # (Live Events stat-card) only shows players from QUALIFYING
        # matches that have completed, missing the ~80-100 main-draw
        # players who haven't taken the court yet. Fixtures has them all.
        # We mark them as alive at "scheduled" stage — exact round name
        # doesn't matter for the count, just elim=False to flag in-draw.
        # Track them in fixture_augmented_bios so the elim-cascade below
        # doesn't sweep them up: they haven't played yet, so they
        # CAN'T have been eliminated by tournament progress.
        scheduled_stage = sem.get("First", {}).get("played") or "R128"
        fixture_augmented_bios: set[int] = set()
        for r in conn.execute("""
            SELECT DISTINCT mid FROM (
              SELECT p1_mid AS mid FROM fixtures
              WHERE tournament_id = ? AND tour = ? AND p1_mid IS NOT NULL
              UNION
              SELECT p2_mid FROM fixtures
              WHERE tournament_id = ? AND tour = ? AND p2_mid IS NOT NULL
            )
        """, (t["id"], tour, t["id"], tour)):
            mid = r["mid"]
            bio_id = mid_to_bio.get(mid)
            if not bio_id or bio_id in players_block:
                continue
            players_block[bio_id] = {"r": scheduled_stage, "elim": False}
            fixture_augmented_bios.add(bio_id)

        # Top-seed bye augmentation. In 96-draw M1000s + 128-draw GS, top
        # 32 seeds get byes through R1 — they have NO match record (no
        # R1 match played) AND NO R1 fixture (R2 fixtures are scheduled
        # only after R1 completes). Without this step, players like
        # Sinner and Alcaraz disappear from the active draw entirely,
        # then show as "missing/eliminated" in the dashboard.
        #
        # Heuristic: if the tournament is M1000/W1000/GS with draw≥96 OR
        # ≥128, and a top-32 ranked player isn't already in players_block,
        # they're almost certainly seeded with a bye. Add them at the
        # "next" round (where byes enter) marked alive.
        # sqlite3.Row supports __getitem__ but not .get() — use bracket
        # access with a try/except for missing-key safety.
        try:
            tournament_type = t["type"]
        except (IndexError, KeyError):
            tournament_type = None
        is_byes_draw = (t["draw_size"] or 0) >= 96 and tournament_type in (
            "GS", "M1000", "W1000")
        if is_byes_draw:
            # Top-32 seeds skip R1 (bye) and enter at R2 — but for the
            # dashboard's Live Events count, all that matters is they're
            # in the draw and alive. Use scheduled_stage as the placeholder
            # round so all "scheduled-but-not-yet-played" players cluster
            # at the same stage label (avoids round-label confusion in
            # the UI).
            #
            # WITHDRAWAL DETECTION: once any "Third" match exists, R2 is
            # fully resolved — every byed seed who actually played should
            # now be in players_block via the matches loop, and anyone
            # still unplayed-but-scheduled is in players_block via the
            # fixture augmentation above. A top-32 bio that's STILL
            # missing here didn't play their R2 match and has no upcoming
            # fixture: late withdrawal (e.g. Anisimova & Mboko @ Rome '26).
            # Mark them WD/elim=True instead of falsely showing alive.
            byes_consumed = any(
                RD_NAME_DEPTH.get(m["round"], 0) >= RD_NAME_DEPTH["Third"]
                for m in matches
            )
            top_seeds = list(conn.execute("""
                SELECT bio_id, mid FROM players
                WHERE tour = ? AND bio_id <= 32
            """, (tour,)))
            for s in top_seeds:
                bid = s["bio_id"]
                if bid in players_block:
                    continue
                if byes_consumed:
                    players_block[bid] = {"r": "WD", "elim": True}
                else:
                    players_block[bid] = {"r": scheduled_stage, "elim": False}
                fixture_augmented_bios.add(bid)

        if not players_block:
            continue

        # Post-process: if the tournament has progressed FAR past a player's
        # stage AND that player actually played a match, they were
        # eliminated by an opponent outside our top-200 fetch set.
        #
        # GAP THRESHOLD: only cascade-elim when the deepest-played round
        # is ≥ 2 stages ahead of the player's status. A 1-stage gap can
        # easily mean "their next match hasn't been synced yet" — Matchstat
        # silent-throttle on WTA, or a 12h cron lag while R2 starts. Don't
        # mark winners as losers prematurely.
        #
        # Example: tournament at R32 (depth 3), R1 winner at R64 (depth 2),
        # gap=1 → don't cascade. Tournament at R16 (depth 4), R1 winner
        # at R64 (depth 2), gap=2 → cascade-elim.
        #
        # Also skip fixture-augmented bios entirely — they have an upcoming
        # match scheduled, so by definition they're alive.
        ELIM_GAP_THRESHOLD = 2
        tournament_deepest = deepest_played
        for bio_id, status in players_block.items():
            if bio_id in fixture_augmented_bios:
                continue
            cur = ROUND_DEPTH.get(status["r"], 0)
            gap = tournament_deepest - cur
            if gap >= ELIM_GAP_THRESHOLD and not status["elim"]:
                status["elim"] = True

        out.append({
            "id":      t["id"],
            "stage":   deepest_round_name or "?",
            "players": players_block,
        })
    return out


def _compute_results_per_player(conn, tour: str) -> dict[int, dict]:
    """For each bio, return {tournament_id: {r, pts}} of completed events.
    Excludes any tournament currently inside its active window (those are
    projected via activeTournaments[] separately so we don't double-count).
    """
    today = date.today()
    # Build set of currently-active tournament ids (date-windowed) to exclude.
    active_ids = set()
    for r in conn.execute("SELECT id, start_date, end_date FROM tournaments WHERE start_date IS NOT NULL AND end_date IS NOT NULL"):
        try:
            sd = date.fromisoformat(r["start_date"])
            ed = date.fromisoformat(r["end_date"])
        except (ValueError, TypeError):
            continue
        if (sd - timedelta(days=7)) <= today <= (ed + timedelta(days=1)):
            active_ids.add(r["id"])
    placeholders = ",".join("?" * len(active_ids)) if active_ids else "''"
    api_id_col = f"api_id_{tour}"
    rows = list(conn.execute(f"""
        SELECT m.date, m.round, m.p1_id, m.p2_id, m.winner_id,
               t.id AS tid, t.draw_size, t.points_table
        FROM matches m
        JOIN tournaments t ON t.id = m.tournament_id
        WHERE m.tour = ?
          {f"AND t.id NOT IN ({placeholders})" if active_ids else ""}
    """, (tour, *active_ids) if active_ids else (tour,)))
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


def _compute_synthetic_ytd(conn, mid: int | None, year: int) -> int:
    """Compute YTD race points by summing tournament results in `year`.

    Fallback for players the Matchstat race endpoint omits — typically
    anyone ranked outside top-200 in the race. We hit this regularly for
    bio'd players who had a strong T12M (e.g. Munar #38) but a quiet
    current year (race rank > 200).

    Mirrors backtest.synthetic_ranking's YTD branch — same SEMANTICS,
    same RD_NAME_DEPTH, same points_table lookup. Returns 0 if mid is
    missing or no in-year tournaments found.
    """
    if not mid:
        return 0
    year_start = f"{year}-01-01"
    rows = list(conn.execute("""
        SELECT m.tournament_id, m.round, m.winner_id,
               t.end_date, t.draw_size, t.points_table
        FROM matches m
        LEFT JOIN tournaments t ON m.tournament_id = t.id
        WHERE (m.p1_id = ? OR m.p2_id = ?)
          AND m.tournament_id IS NOT NULL
          AND t.points_table IS NOT NULL
          AND t.end_date >= ?
    """, (mid, mid, year_start)))

    by_tournament: dict[str, list] = {}
    for r in rows:
        by_tournament.setdefault(r["tournament_id"], []).append(r)

    total = 0
    for _, t_matches in by_tournament.items():
        end_date = t_matches[0]["end_date"]
        if not end_date or end_date < year_start:
            continue
        try:
            pts_table = json.loads(t_matches[0]["points_table"])
        except (json.JSONDecodeError, TypeError):
            continue
        sem = semantics_for(t_matches[0]["draw_size"])
        deepest_d, deepest_round, deepest_won = 0, None, False
        for m in t_matches:
            d = RD_NAME_DEPTH.get(m["round"], 0)
            if d > deepest_d:
                deepest_d = d
                deepest_round = m["round"]
                deepest_won = (m["winner_id"] == mid)
        if not deepest_round:
            continue
        sem_entry = sem.get(deepest_round)
        if not sem_entry:
            continue
        stage = sem_entry["next"] if deepest_won else sem_entry["played"]
        total += pts_table.get(stage, 0)
    return total


def materialize_season(conn, tour: str) -> bool:
    today = date.today().isoformat()
    today_year = date.today().year
    bios = list(conn.execute("""
        SELECT bio_id, name, mid FROM players WHERE tour = ? ORDER BY bio_id
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
    n_synth_ytd = 0
    for b in bios:
        bid = b["bio_id"]
        s = snap.get(bid) or {}
        prev_rank = baseline.get(bid)
        cur_rank  = s.get("rank") or bid
        rank_move = (prev_rank - cur_rank) if (prev_rank and cur_rank) else 0
        # YTD fallback: if the API didn't include this player in its race
        # response (typical for bio'd players ranked > 200 in the race),
        # compute synthetic YTD from their in-year tournament results.
        ytd_api = s.get("ytd_pts")
        if ytd_api is None:
            ytd = _compute_synthetic_ytd(conn, b["mid"], today_year)
            if ytd > 0:
                n_synth_ytd += 1
        else:
            ytd = ytd_api
        players_obj[bid] = {
            "rank":     cur_rank,
            "pts":      s.get("pts") or 0,
            "ytd":      ytd,
            "rankMove": rank_move,
            "results":  results_by_bio.get(bid, {}),
        }
    if n_synth_ytd:
        print(f"  [{tour}] synthetic YTD computed for {n_synth_ytd} player(s) "
              "(missing from API race response)")

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
        # Full UTC ISO timestamp for lastUpdated. The dashboard converts
        # to the user's browser timezone via toLocaleString() at render
        # time, so a viewer in PST sees "May 7, 2026, 12:30:33 PM PDT"
        # while a viewer in EST sees the same UTC instant rendered as
        # 3:30 PM EDT. UTC-on-the-wire keeps everything consistent.
        return (
            f"// season_{tour}.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f"// Do not edit manually. Last updated: {_now_utc()}\n"
            f"/* hash: {h} */\n"
            f"\n"
            f"const {var_name} = {{\n"
            f'  lastUpdated: "{_now_utc()}",\n'
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
    """Per top-N bio: last 30 matches with the form-bar / drill-down fields.
    Also exposes opponent's bio_id (oppB) and pre-match odds (myOdd / oppOdd)
    so the dashboard can compute model-vs-market deltas without a join."""
    out: dict[str, dict] = {"atp": {}, "wta": {}}
    bios = list(conn.execute("""
        SELECT mid, bio_id, tour, name FROM players
        WHERE bio_id <= ?
    """, (TOP_N,)))

    # Lookup: mid → (name, country) for opponent enrichment
    mid_lookup = {row["mid"]: (row["name"], row["country"]) for row in conn.execute(
        "SELECT mid, name, country FROM players"
    )}
    # Lookup: (tour, mid) → bio_id so we can attach opponent's bio_id to each
    # match. None for non-bio'd opponents (qualifier outside top-200, etc.).
    mid_to_bio: dict[tuple[str, int], int] = {
        (row["tour"], row["mid"]): row["bio_id"]
        for row in conn.execute("SELECT tour, mid, bio_id FROM players")
    }

    for b in bios:
        mid = b["mid"]
        tour = b["tour"]
        # Defense-in-depth dedup: even if sync_matches.gc_dup_matches missed
        # a duplicate (e.g., a dupe was just inserted and gc hasn't run yet),
        # we want the form bar to show each match once. Canonical key is
        # (date_day, unordered player pair) — same heuristic sync_matches
        # uses. Keep the row with most-populated fields, lowest id as
        # tiebreak. Window over the player's own match set, then LIMIT 30.
        rows = list(conn.execute("""
            SELECT date, round, tournament_name, tournament_api_id,
                   p1_id, p2_id, winner_id, score, raw
            FROM (
              SELECT date, round, tournament_name, tournament_api_id,
                     p1_id, p2_id, winner_id, score, raw, id,
                     ROW_NUMBER() OVER (
                       PARTITION BY substr(date, 1, 10),
                                    MIN(p1_id, p2_id), MAX(p1_id, p2_id)
                       ORDER BY (winner_id IS NOT NULL) DESC,
                                (score IS NOT NULL AND score != '') DESC,
                                (stat_p1 IS NOT NULL) DESC,
                                fetched_at ASC,
                                CAST(id AS INTEGER) ASC
                     ) AS rn
              FROM matches
              WHERE p1_id = ? OR p2_id = ?
            ) WHERE rn = 1
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
            opp_bio = mid_to_bio.get((tour, opp_mid))
            if opp_bio is not None:
                entry["oppB"] = opp_bio
            # Pre-match decimal odds. odd1/odd2 in raw map to p1/p2 by API
            # convention; flip if WE are p2 so consumers can read myOdd/oppOdd
            # without needing to know which side of the match we were on.
            if r["raw"]:
                try:
                    raw = json.loads(r["raw"])
                    o1 = raw.get("odd1")
                    o2 = raw.get("odd2")
                    if o1 is not None and o2 is not None:
                        my_odd  = o1 if we_p1 else o2
                        opp_odd = o2 if we_p1 else o1
                        # Round to 2dp for compactness; keep as float (datalist
                        # parses fine).
                        entry["myOdd"]  = round(float(my_odd), 2)
                        entry["oppOdd"] = round(float(opp_odd), 2)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            items.append(entry)
        out[tour][str(b["bio_id"])] = items

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
            SELECT m.date, m.round, m.tournament_name, m.tournament_id,
                   m.winner_id, m.p1_id, m.p2_id
            FROM matches m
            WHERE (m.p1_id = ? OR m.p2_id = ?) AND m.round IS NOT NULL
        """, (mid, mid)))
        # Group by (tournament_key, year) → deepest round.
        # Prefer tournament_id when resolved (stable across renames); fall
        # back to tournament_name when not (W125 / Challenger / unresolved).
        # The dashboard uses tournament_id to look up prior-year podiums
        # by stripping the year suffix (e.g. "rome26" → "rome25").
        by_te: dict[tuple[str, int], tuple[int, str, bool, str | None]] = {}
        for r in rows:
            tn  = r["tournament_name"] or ""
            tid = r["tournament_id"]
            key = tid or tn  # group by id when we have one; else by name
            try:
                yr = int(r["date"][:4])
            except (ValueError, TypeError):
                continue
            depth = RD_NAME_DEPTH.get(r["round"], 0)
            if depth == 0:
                continue
            won = (r["winner_id"] == mid) if r["winner_id"] else False
            cur = by_te.get((key, yr))
            if not cur or depth > cur[0]:
                by_te[(key, yr)] = (depth, r["round"], won, tid, tn)
        items = []
        for (_key, yr), (_d, rd, won, tid, tn) in by_te.items():
            items.append({"tn": tn, "year": yr, "round": rd, "won": won,
                          "tid": tid})
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
    bp_won = bp_chances = 0   # offensive break-point efficiency (own conversions)
    opp_svpt = opp_first_won = opp_second_won = 0
    tb_played = tb_won = dec_played = dec_won = 0
    used = 0
    # Rally-aggression counters — opponent-adjusted. Matchstat only populates
    # `winners` / `unforcedErrors` for Grand Slams (1556/11758 matches across
    # the DB; 0% non-slam). The one-sided W:UE ratio is misleading without
    # opp-strength normalization — see the design conversation at
    # ~/.claude/plans/wild-gliding-kernighan.md. We pair own + opp blobs per
    # match so the year-aggregate captures both YOUR aggression and how the
    # OPP fared — the only way to credibly answer "did they win because they
    # were good vs because opp was bad".
    #
    # `wu_seen_matches` is incremented ONLY when ALL FOUR fields are present
    # (own W, own UE, opp W, opp UE). Single-sided rows would corrupt the
    # delta — better to drop them.
    own_winners_tot = own_ue_tot = 0
    opp_winners_tot = opp_ue_tot = 0
    wu_seen_matches = 0
    # Min sample to emit the metric. Sub-5 slam appearances = too noisy.
    WU_MIN_MATCHES = 5
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
                # OFFENSIVE break-point counters — what we converted on
                # opponent's serve. Mirrors the defensive bp_faced/bp_saved
                # block above. Symmetric backfill: if our offensive counters
                # are missing, the opponent's defensive counters tell the
                # same story (opp_bp_faced was BPs we created against them,
                # opp_bp_saved was BPs we failed to convert).
                own_bp_conv    = _i(own, "breakPointsConverted","breakPointConverted","bpConv")
                own_bp_attempt = _i(own, "breakPointsConvertedOf","breakPointsAttempted",
                                        "breakPointAttempted","bpAttempt")
                opp_bp_faced   = _i(opp, "breakPointFaced","breakPointsFaced","bpFaced")
                opp_bp_saved   = _i(opp, "breakPointSaved","breakPointsSaved","bpSaved")
                # Backfill: if our bp counters are missing, use opponent's
                # converted/attempted (which are recorded against our serve).
                # This is the key fix that was lost when porting from
                # fetch_match_stats_api.py — most rows only have one side.
                if not own_bp_faced and opp_bp_attempt:
                    own_bp_faced = opp_bp_attempt
                if not own_bp_saved and own_bp_faced:
                    own_bp_saved = own_bp_faced - opp_bp_conv
                # Symmetric backfill for offensive side.
                if not own_bp_attempt and opp_bp_faced:
                    own_bp_attempt = opp_bp_faced
                if not own_bp_conv and opp_bp_faced and opp_bp_saved:
                    own_bp_conv = opp_bp_faced - opp_bp_saved

                svpt        += _i(own, "firstServeOf","totalServePointsAttempted","serveOf","serveOfGm","svpt")
                first_in    += _i(own, "firstServe","firstServeIn","1stIn")
                first_won   += _i(own, "winningOnFirstServe","firstServeWon","1stWon")
                second_won  += _i(own, "winningOnSecondServe","secondServeWon","2ndWon")
                aces        += _i(own, "aces","ace")
                df          += _i(own, "doubleFaults","doubleFault","df")
                bp_faced    += own_bp_faced
                bp_saved    += own_bp_saved
                bp_won      += own_bp_conv
                bp_chances  += own_bp_attempt
                opp_svpt        += _i(opp, "firstServeOf","totalServePointsAttempted","svpt")
                opp_first_won   += _i(opp, "winningOnFirstServe","firstServeWon","1stWon")
                opp_second_won  += _i(opp, "winningOnSecondServe","secondServeWon","2ndWon")
                # Rally aggression — paired read. Require ALL FOUR fields
                # (own W, own UE, opp W, opp UE) to be non-null; otherwise
                # the per-match deltas are corrupt. Matchstat populates
                # symmetrically (both sides or neither) at slams, so this
                # filter is essentially "is this a slam match with stats."
                w_keys  = ("winners","winnersCount")
                ue_keys = ("unforcedErrors","unforcedErrorsCount","unforced")
                def _has(blob, keys):
                    return blob and any(
                        k in blob and blob[k] not in (None, "", "NA") for k in keys
                    )
                if (_has(own, w_keys) and _has(own, ue_keys)
                        and _has(opp, w_keys) and _has(opp, ue_keys)):
                    own_winners_tot += _i(own, *w_keys)
                    own_ue_tot      += _i(own, *ue_keys)
                    opp_winners_tot += _i(opp, *w_keys)
                    opp_ue_tot      += _i(opp, *ue_keys)
                    wu_seen_matches += 1
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

    sv_gms     = round(svpt / 6.5) if svpt else 0
    opp_sv_gms = round(opp_svpt / 6.5) if opp_svpt else 0
    # Service games we lost = BPs we faced that opp converted = bp_faced − bp_saved.
    # A single service game can have multiple BPs but only one conversion ends it,
    # so opp's converted-BP count == games we got broken.
    sv_games_lost = max(0, bp_faced - bp_saved)
    return {
        "matches":              n,
        "matchWinPct":          round(100 * wins / n, 1) if n else None,
        "servePtsWonPct":       _safe_pct(first_won + second_won, svpt),
        "returnPtsWonPct":      _safe_pct(opp_svpt - opp_first_won - opp_second_won, opp_svpt),
        "totalPtsWonPct":       _safe_pct((first_won + second_won) + (opp_svpt - opp_first_won - opp_second_won),
                                          svpt + opp_svpt),
        "acesPerSvGm":          round(aces / sv_gms, 2) if sv_gms else None,
        "bpSavedPct":           _safe_pct(bp_saved, bp_faced),
        "bpWonPct":             _safe_pct(bp_won, bp_chances),
        # Service Games Won % — captures avoidance + saving in one number.
        # More game-relevant than BP Saved % alone (a server who never faces a
        # BP also has 100% SG Won but no BP Saved %). 2026-05-05 swap: this
        # replaces bpSavedPct in the composite metric set.
        "serviceGamesWonPct":   _safe_pct(sv_gms - sv_games_lost, sv_gms),
        # Return Games Won % — captures pressure + conversion in one number.
        # ≈ bp_per_game × bp_won_pct. More game-relevant than BP Won % alone
        # (a player creating few BPs and converting them all has high BP Won %
        # but low impact). 2026-05-05 swap: replaces bpWonPct in the composite.
        "returnGamesWonPct":    _safe_pct(bp_won, opp_sv_gms),
        "tbWinPct":             _safe_pct(tb_won, tb_played) if tb_played >= min_tb else None,
        "decSetWinPct":         _safe_pct(dec_won, dec_played) if dec_played >= min_dec else None,
        # Opp-adjusted rally aggression (Slams only — see WU_MIN_MATCHES floor
        # comment above). Three views:
        #   rallyDominance      — overall rally win rate, fully opp-adjusted.
        #     Numerator   = your winners + opp's UE  (rallies YOU won)
        #     Denominator = opp's winners + your UE  (rallies OPP won)
        #     Above 1.0 = net winning rallies. The headline metric.
        #   netWinnersPerMatch  — shotmaking edge (your W − opp W per match)
        #   netUEPerMatch       — consistency edge (opp UE − your UE per match)
        # Emit only when ≥ WU_MIN_MATCHES slam matches in window. Below that
        # the sample is too noisy to surface alongside year-aggregated metrics.
        "rallyDominance":       round(
                                    (own_winners_tot + opp_ue_tot) /
                                    max(opp_winners_tot + own_ue_tot, 1), 2)
                                  if wu_seen_matches >= WU_MIN_MATCHES else None,
        "netWinnersPerMatch":   round(
                                    (own_winners_tot - opp_winners_tot)
                                    / wu_seen_matches, 1)
                                  if wu_seen_matches >= WU_MIN_MATCHES else None,
        "netUEPerMatch":        round(
                                    (opp_ue_tot - own_ue_tot)
                                    / wu_seen_matches, 1)
                                  if wu_seen_matches >= WU_MIN_MATCHES else None,
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
            # Per-surface variants — tier already applied.
            # Threshold mirrors the UI's smallest min-matches dropdown
            # option (2 — see DROPDOWN_OPTS below). The dashboard's slider
            # is the source of truth above that floor; pre-filtering at 5
            # here used to hide clay rows even when users moved the slider
            # to 2+ (Rome/Madrid '26 surfaced the regression: WTA T3M clay
            # was empty at every slider position because few players had
            # 5+ tour-level clay matches in 90 days).
            by_surf: dict[str, list] = defaultdict(list)
            for m in window_tour:
                by_surf[m["surface"] or "H"].append(m)
            for surf, surf_ms in by_surf.items():
                if len(surf_ms) < 2:
                    continue
                surf_agg = _aggregate_year(surf_ms, mid, min_matches=2,
                                           tour_only=False)
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
        "tbWinPct", "decSetWinPct", "acesPerSvGm",
        "bpSavedPct", "bpWonPct",
        "serviceGamesWonPct", "returnGamesWonPct",
        "matchWinPct",
        # Opp-adjusted rally aggression (Slams-only — Matchstat doesn't
        # populate winners/UE outside the 4 majors). Headline + 2 components.
        # NOT in composite — opp-adjusted at per-match level but year-aggregated
        # over ≤25 slam matches; too narrow a window for the predictor.
        "rallyDominance", "netWinnersPerMatch", "netUEPerMatch",
    ]
    labels = {
        "matches":              "Matches Played",
        "servePtsWonPct":       "Serve Points Won %",
        "returnPtsWonPct":      "Return Points Won %",
        "totalPtsWonPct":       "Total Points Won %",
        "tbWinPct":             "Tiebreak Win %",
        "decSetWinPct":         "Deciding Set Win %",
        "acesPerSvGm":          "Aces / Service Game",
        "bpSavedPct":           "Break Points Saved %",
        "bpWonPct":             "Break Points Won %",
        "serviceGamesWonPct":   "Service Games Won %",
        "returnGamesWonPct":    "Return Games Won %",
        "matchWinPct":          "Match Win %",
        "rallyDominance":       "Rally Dominance (Slams)",
        "netWinnersPerMatch":   "Net Winners / Match (Slams)",
        "netUEPerMatch":        "Net UE / Match (Slams)",
    }

    # Per-window min-matches default = 25th percentile of match counts
    # across both tours combined for each window. Surfaces ~75% of rows
    # while filtering tiny-sample noise. Snapped to the nearest dropdown
    # option ≤ p25 in wta_analytics.html (options: 2, 5, 10, 20, 30, 50).
    # Combined ATP+WTA so the chart shows a consistent threshold
    # regardless of TOUR toggle. Falls back to 5 if a window has too few
    # rows to compute a percentile.
    DROPDOWN_OPTS = [2, 5, 10, 20, 30, 50]
    min_defaults: dict[str, int] = {}
    for window in years_seen:
        # Combine ATP+WTA all-surface rows for this window.
        rows = [r for r in flat_atp + flat_wta
                if r.get("year") == window and r.get("surf") == "All"]
        counts = sorted(r["matches"] for r in rows if "matches" in r)
        if len(counts) < 4:
            min_defaults[window] = 5
            continue
        # 25th percentile (nearest-rank method).
        p25 = counts[max(0, int(len(counts) * 0.25) - 1)]
        # Snap down to the largest dropdown option ≤ p25; floor at 2.
        snap = max((o for o in DROPDOWN_OPTS if o <= p25), default=2)
        min_defaults[window] = snap

    hash_input = json.dumps({"atp": flat_atp, "wta": flat_wta,
                             "metrics": metrics, "labels": labels,
                             "min_defaults": min_defaults},
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
            "// Per-window default min-matches = 25th percentile of match\n"
            "// counts in that window (combined ATP+WTA), snapped down to a\n"
            "// dropdown option. Surfaces ~75% of rows while keeping samples\n"
            "// statistically meaningful. Falls back to 5 if too few rows.\n"
            f"const TRAPEZOID_MIN_DEFAULTS = {json.dumps(min_defaults, indent=2)};\n"
        )

    return _write_if_changed(path, hash_input, render, label="trapezoid")


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--only", choices=["season", "recent_matches", "tournament_history",
                                      "h2h", "trapezoid", "upcoming", "predictions",
                                      "api_log"],
                   default=None)
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
    if args.only in (None, "upcoming"):
        if materialize_upcoming(conn): changed += 1
    if args.only in (None, "predictions"):
        if materialize_predictions(conn): changed += 1
    if args.only in (None, "api_log"):
        if materialize_api_log(conn): changed += 1

    print(f"\n{changed} file(s) changed.")
    return 0


def materialize_predictions(conn) -> bool:
    """Read predictions JOIN matches → write data/predictions.js with the
    last 30 resolved predictions + aggregate stats (Brier, calibration).

    Includes pre-match odds (de-vigged) when available so the dashboard
    can show a Brier-vs-market comparison. Odds are a benchmark, not an
    input feature — used to measure whether our model beats market
    consensus on the matches we both opined on.
    """
    rows = list(conn.execute("""
        SELECT
            p.match_id, p.tour, p.surface, p.date,
            p.p1_mid, p.p2_mid, p.p1_name, p.p2_name,
            p.p_pred, p.predicted_at, p.model_version,
            p.tournament_id,
            m.winner_id, m.raw, m.score,
            -- Latest Kalshi snapshot for this match (any pre/post-match
            -- since both have value). Keyed by p.p1_mid match because
            -- kalshi_odds uses our slot convention (lower mid → A).
            k.p1_yes_price AS kalshi_p1_yes,
            k.p2_yes_price AS kalshi_p2_yes,
            k.is_pre_match AS kalshi_pre_match
        FROM predictions p
        JOIN matches m ON CAST(p.match_id AS TEXT) = m.id
        LEFT JOIN (
          SELECT match_id, p1_yes_price, p2_yes_price, is_pre_match,
                 ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY fetched_at DESC) AS rn
          FROM kalshi_odds
          WHERE p1_yes_price IS NOT NULL AND p2_yes_price IS NOT NULL
        ) k ON k.match_id = p.match_id AND k.rn = 1
        WHERE m.winner_id IS NOT NULL
          AND p.predicted_at = (
            SELECT MIN(predicted_at) FROM predictions p2
            WHERE p2.match_id = p.match_id
          )
        ORDER BY p.date DESC, p.match_id DESC
    """))

    def _devigged_p1(odd1, odd2):
        """Convert odds to de-vigged implied probability of player 1 winning.
        Standard market normalization: divide by sum of inverses to remove
        bookmaker overround."""
        try:
            ip1 = 1.0 / float(odd1)
            ip2 = 1.0 / float(odd2)
            tot = ip1 + ip2
            return ip1 / tot if tot > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    enriched = []
    for r in rows:
        won_a = 1 if r["winner_id"] == r["p1_mid"] else 0
        odd1 = odd2 = None
        market_p_a = None
        if r["raw"]:
            try:
                raw = json.loads(r["raw"])
                # Matchstat: raw.odd1 = odds for player1 in match payload.
                # Slot A in our predictions is lower-mid, may be p1 or p2 in raw.
                # Need to map back.
                raw_p1 = raw.get("player1Id") or raw.get("p1Id")
                if raw_p1 == r["p1_mid"]:
                    odd1, odd2 = raw.get("odd1"), raw.get("odd2")
                else:
                    odd1, odd2 = raw.get("odd2"), raw.get("odd1")
                market_p_a = _devigged_p1(odd1, odd2)
            except (json.JSONDecodeError, TypeError):
                pass

        # Kalshi de-vig: each side has its own YES price; sum to ~1.0 with
        # small bid-ask spread. Normalize so the two sum to exactly 1.
        kalshi_p_a = None
        if r["kalshi_p1_yes"] is not None and r["kalshi_p2_yes"] is not None:
            tot = r["kalshi_p1_yes"] + r["kalshi_p2_yes"]
            if tot > 0:
                kalshi_p_a = r["kalshi_p1_yes"] / tot

        enriched.append({
            "match_id":  r["match_id"],
            "date":      r["date"],
            "tour":      r["tour"],
            "surface":   r["surface"],
            "tournament_id": r["tournament_id"],
            "p1_name":   r["p1_name"],
            "p2_name":   r["p2_name"],
            "p_pred":    round(r["p_pred"], 4),
            "won_a":     won_a,
            "market_p_a":        round(market_p_a, 4) if market_p_a is not None else None,
            "kalshi_p_a":        round(kalshi_p_a, 4) if kalshi_p_a is not None else None,
            "kalshi_pre_match":  bool(r["kalshi_pre_match"]) if r["kalshi_pre_match"] is not None else None,
            "score":     r["score"],
            "model_version": r["model_version"],
        })

    n = len(enriched)
    if n:
        brier_us = sum((e["p_pred"] - e["won_a"]) ** 2 for e in enriched) / n
        with_odds = [e for e in enriched if e["market_p_a"] is not None]
        if with_odds:
            brier_odds = sum((e["market_p_a"] - e["won_a"]) ** 2 for e in with_odds) / len(with_odds)
            brier_us_subset = sum((e["p_pred"] - e["won_a"]) ** 2 for e in with_odds) / len(with_odds)
        else:
            brier_odds = brier_us_subset = None

        # Same comparison for Kalshi (separate subset; partial overlap with
        # sportsbook odds — Kalshi covers more matches but not all).
        with_kalshi = [e for e in enriched if e["kalshi_p_a"] is not None]
        if with_kalshi:
            brier_kalshi = sum((e["kalshi_p_a"] - e["won_a"]) ** 2 for e in with_kalshi) / len(with_kalshi)
            brier_us_kalshi_subset = sum((e["p_pred"] - e["won_a"]) ** 2 for e in with_kalshi) / len(with_kalshi)
        else:
            brier_kalshi = brier_us_kalshi_subset = None
    else:
        brier_us = brier_odds = brier_us_subset = None
        brier_kalshi = brier_us_kalshi_subset = None
        with_odds = []
        with_kalshi = []

    # 10-bin calibration table
    bins: list[list] = [[] for _ in range(10)]
    for e in enriched:
        idx = min(int(e["p_pred"] * 10), 9)
        bins[idx].append(e)
    calibration = []
    for i, bin_rows in enumerate(bins):
        lo, hi = i / 10, (i + 1) / 10
        if not bin_rows:
            calibration.append({"lo": lo, "hi": hi, "n": 0,
                                "pred": None, "actual": None})
            continue
        pred = sum(e["p_pred"] for e in bin_rows) / len(bin_rows)
        actual = sum(e["won_a"] for e in bin_rows) / len(bin_rows)
        calibration.append({"lo": lo, "hi": hi, "n": len(bin_rows),
                            "pred": round(pred, 3),
                            "actual": round(actual, 3)})

    payload = {
        # Keep up to 500 most-recent resolved predictions in the dashboard
        # blob. Rationale: a year of prospective predictions is ~700-1000;
        # 500 covers the meaningful window without bloating the JSON. The
        # dashboard's panels each slice as needed (Recent Calls takes 15,
        # MvM takes 25), so this just sets the upstream pool size — and
        # a too-small pool was crowding out earlier-tour predictions in
        # WTA-heavy recent days.
        "recent": enriched[:500],
        "stats": {
            "n":               n,
            "brier_us":        round(brier_us, 4) if brier_us is not None else None,
            "brier_odds":      round(brier_odds, 4) if brier_odds is not None else None,
            "brier_us_subset": round(brier_us_subset, 4) if brier_us_subset is not None else None,
            "n_with_odds":     len(with_odds),
            "brier_kalshi":           round(brier_kalshi, 4) if brier_kalshi is not None else None,
            "brier_us_kalshi_subset": round(brier_us_kalshi_subset, 4) if brier_us_kalshi_subset is not None else None,
            "n_with_kalshi":          len(with_kalshi),
            "model_version":   enriched[0]["model_version"] if enriched else None,
        },
        "calibration": calibration,
    }
    hash_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "predictions.js"

    def render(h: str) -> str:
        return (
            f"// predictions.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f"// Do not edit manually. Last updated: {_now_utc()}\n"
            f"/* hash: {h} */\n"
            f"\n"
            f"const PREDICTIONS_DATA = {json.dumps(payload, indent=2)};\n"
        )

    return _write_if_changed(path, hash_input, render, label="predictions")


def materialize_api_log(conn) -> bool:
    """Read api_fetch_log → write data/api_log.js with API-usage stats for
    the dashboard's Pipeline tab. Self-monitoring so we can see if we're
    on track to blow the 10k/mo Matchstat cap, and which endpoints
    dominate.

    Source classification heuristic: cron runs are tight bursts (~3 min)
    starting at fixed UTC hours. Calls in those windows are tagged with
    the cron name; everything else is 'manual'. Misclassifies only if a
    user manually triggers AT exactly a cron hour, which is rare.
    """
    today = date.today()
    cutoff_30d = (today - timedelta(days=30)).isoformat()
    month_prefix = today.strftime("%Y-%m")

    # Daily totals, last 30 days
    daily = []
    for r in conn.execute("""
        SELECT substr(fetched_at, 1, 10) AS d, COUNT(*) AS n
        FROM api_fetch_log
        WHERE substr(fetched_at, 1, 10) >= ?
        GROUP BY d ORDER BY d
    """, (cutoff_30d,)):
        daily.append({"date": r["d"], "total": r["n"]})

    # Month-to-date with on-pace projection
    mtd = conn.execute("""
        SELECT COUNT(*) AS n FROM api_fetch_log
        WHERE substr(fetched_at, 1, 7) = ?
    """, (month_prefix,)).fetchone()["n"]
    days_elapsed = today.day
    # Days in month — use first-of-next-month minus one day
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    days_in_month = (next_month - timedelta(days=1)).day
    on_pace = round(mtd / days_elapsed * days_in_month) if days_elapsed > 0 else 0

    # Recent runs — group by UTC hour bucket so each cron's burst becomes
    # one row. Source is inferred from hour:
    #   07:xx UTC = 12am PDT cron
    #   17:xx UTC = 10am PDT cron
    #   23:xx UTC = 4pm  PDT cron
    #   anything else = manual
    runs_rows = list(conn.execute("""
        SELECT
          strftime('%Y-%m-%dT%H:00:00Z', fetched_at) AS hour_bucket,
          CASE
            WHEN strftime('%H', fetched_at) = '07' THEN 'cron-12am-pdt'
            WHEN strftime('%H', fetched_at) = '17' THEN 'cron-10am-pdt'
            WHEN strftime('%H', fetched_at) = '23' THEN 'cron-4pm-pdt'
            ELSE 'manual'
          END AS source,
          COUNT(*) AS calls,
          MIN(fetched_at) AS first_call,
          MAX(fetched_at) AS last_call,
          SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors
        FROM api_fetch_log
        WHERE fetched_at >= datetime('now', '-7 days')
        GROUP BY hour_bucket, source
        ORDER BY hour_bucket DESC LIMIT 25
    """))
    recent_runs = []
    for r in runs_rows:
        # Duration: last - first call within the bucket, in seconds.
        # Crude but informative.
        try:
            t0 = datetime.fromisoformat(r["first_call"])
            t1 = datetime.fromisoformat(r["last_call"])
            duration_s = int((t1 - t0).total_seconds())
        except (ValueError, TypeError):
            duration_s = None
        recent_runs.append({
            "ts":       r["hour_bucket"],
            "source":   r["source"],
            "calls":    r["calls"],
            "duration": duration_s,
            "errors":   r["errors"] or 0,
        })

    # Today's endpoints — bucket past-matches per tour, rankings,
    # kalshi, others. Helps spot a runaway endpoint.
    today_iso = today.isoformat()
    endpoints = []
    for r in conn.execute("""
        SELECT
          CASE
            WHEN endpoint LIKE 'wta/player/past-matches/%' THEN 'wta past-matches'
            WHEN endpoint LIKE 'atp/player/past-matches/%' THEN 'atp past-matches'
            WHEN endpoint LIKE '%ranking/singles'           THEN 'rankings'
            WHEN endpoint LIKE 'kalshi/%'                    THEN 'kalshi'
            WHEN endpoint LIKE '%fixtures/tournament/%'      THEN 'fixtures'
            WHEN endpoint LIKE '%tournament/calendar/%'      THEN 'tournament catalog'
            ELSE 'other'
          END AS bucket,
          COUNT(*) AS calls
        FROM api_fetch_log
        WHERE substr(fetched_at, 1, 10) = ?
        GROUP BY bucket ORDER BY calls DESC
    """, (today_iso,)):
        endpoints.append({"endpoint": r["bucket"], "calls": r["calls"]})

    payload = {
        "lastUpdated": _now_utc(),
        "monthToDate": {
            "total":         mtd,
            "cap":           10000,
            "daysElapsed":   days_elapsed,
            "daysInMonth":   days_in_month,
            "projected":     on_pace,
        },
        "daily":        daily,
        "recentRuns":   recent_runs,
        "todayEndpoints": endpoints,
    }
    hash_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "api_log.js"

    def render(h: str) -> str:
        return (
            f"// api_log.js — AUTO-GENERATED by scripts/materialize.py\n"
            f"// Self-monitoring: API-usage stats for the Pipeline tab.\n"
            f"/* hash: {h} */\n"
            f"\n"
            f"const API_LOG_DATA = {json.dumps(payload, indent=2)};\n"
        )

    return _write_if_changed(path, hash_input, render, label="api_log")


def materialize_upcoming(conn) -> bool:
    """Read fixtures table → write data/upcoming_matches.js. Each entry has
    everything the dashboard needs to render a fixture row + load the matchup
    into the predictor (bio_id resolved when possible, raw name as fallback).

    Joins kalshi_odds for every fixture where we have a Kalshi market.
    Matchstat's fixture odds (odd1/odd2) are typically NULL until matches
    complete, but Kalshi posts pre-match prices on most events — so the
    dashboard's 'Upcoming' panel can show the model-vs-Kalshi comparison
    on actually-upcoming matches."""
    today = date.today().isoformat()
    rows = list(conn.execute("""
        SELECT f.id, f.tour, f.tournament_id, f.tournament_api_id, f.date,
               f.round_id, f.p1_mid, f.p1_name, f.p1_country,
               f.p2_mid, f.p2_name, f.p2_country, f.odd1, f.odd2,
               t.name AS tn, t.short AS tn_short, t.surface AS surf,
               t.draw_size AS draw_size, t.type AS tier,
               -- Latest Kalshi snapshot for this fixture. kalshi_odds.match_id
               -- stores the fixture id (string). p1_yes_price corresponds to
               -- kalshi_odds.p1_mid which uses lower-mid-as-A convention —
               -- may not match this fixture's p1, so also pull the kalshi
               -- p1_mid so we can re-align slots in Python.
               k.p1_yes_price  AS kalshi_p_lo,
               k.p2_yes_price  AS kalshi_p_hi,
               k.p1_mid        AS kalshi_p1_mid
        FROM fixtures f
        LEFT JOIN tournaments t ON t.id = f.tournament_id
        LEFT JOIN (
          SELECT match_id, p1_mid, p1_yes_price, p2_yes_price,
                 ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY fetched_at DESC) AS rn
          FROM kalshi_odds
          WHERE p1_yes_price IS NOT NULL AND p2_yes_price IS NOT NULL
        ) k ON k.match_id = CAST(f.id AS TEXT) AND k.rn = 1
        WHERE f.date >= ?
          -- Singles only. Matchstat encodes doubles as combined names
          -- like "Bhambri/Venus"; we never want them in the matchup
          -- predictor. sync_fixtures filters at the source AND gc's
          -- doubles rows, but this query-level guard prevents any
          -- straggler from reaching the dashboard.
          AND (f.p1_name IS NULL OR f.p1_name NOT LIKE '%/%')
          AND (f.p2_name IS NULL OR f.p2_name NOT LIKE '%/%')
        ORDER BY f.date ASC, f.id ASC
    """, (today,)))
    # Build mid → bio_id maps (per-tour; mids overlap across tours rarely)
    mid_to_bio_atp = {r["mid"]: r["bio_id"] for r in conn.execute(
        "SELECT mid, bio_id FROM players WHERE tour='atp' AND mid IS NOT NULL")}
    mid_to_bio_wta = {r["mid"]: r["bio_id"] for r in conn.execute(
        "SELECT mid, bio_id FROM players WHERE tour='wta' AND mid IS NOT NULL")}

    out = {"atp": [], "wta": []}
    for r in rows:
        m2b = mid_to_bio_atp if r["tour"] == "atp" else mid_to_bio_wta
        entry = {
            "id":     r["id"],
            "date":   r["date"],
            "tId":    r["tournament_api_id"],
            "tCat":   r["tournament_id"],   # may be None
            "tn":     r["tn"] or "?",
            "short":  r["tn_short"] or "?",
            "surf":   r["surf"] or "H",
            "tier":   r["tier"] or "?",
            "drawSize": r["draw_size"],
            "rdId":   r["round_id"],
            "p1": {"mid": r["p1_mid"], "name": r["p1_name"], "ioc": r["p1_country"],
                   "bio": m2b.get(r["p1_mid"])},
            "p2": {"mid": r["p2_mid"], "name": r["p2_name"], "ioc": r["p2_country"],
                   "bio": m2b.get(r["p2_mid"])},
        }
        if r["odd1"] is not None: entry["o1"] = r["odd1"]
        if r["odd2"] is not None: entry["o2"] = r["odd2"]
        # Kalshi de-vigged prob for fixture's p1 winning. Kalshi stores
        # prices keyed by lower-mid-as-A, so if kalshi's p1_mid matches
        # the fixture's p1_mid, kalshi_p_lo IS p1's price. Otherwise
        # the slot is flipped — fixture's p1 is kalshi's p2 and we
        # use kalshi_p_hi.
        if r["kalshi_p_lo"] is not None and r["kalshi_p_hi"] is not None:
            tot = r["kalshi_p_lo"] + r["kalshi_p_hi"]
            if tot > 0:
                if r["kalshi_p1_mid"] == r["p1_mid"]:
                    p1_devigged = r["kalshi_p_lo"] / tot
                else:
                    p1_devigged = r["kalshi_p_hi"] / tot
                entry["k1"] = round(p1_devigged, 4)        # p1's de-vigged Kalshi prob
                entry["k2"] = round(1 - p1_devigged, 4)
        out[r["tour"]].append(entry)

    hash_input = json.dumps(out, sort_keys=True, separators=(",", ":"))
    path = DATA_DIR / "upcoming_matches.js"

    def render(h: str) -> str:
        payload = {"lastUpdated": _now_utc(), "atp": out["atp"], "wta": out["wta"]}
        return (
            "/**\n"
            " * upcoming_matches.js — AUTO-GENERATED by scripts/materialize.py from data/tennis.db\n"
            f" * Last updated: {_now_utc()}\n"
            f" * hash: {h}\n"
            " * Schema: keyed by tour. Each entry: {id, date, tId, tCat, tn, surf,\n"
            " *   tier, drawSize, rdId, p1{mid,name,ioc,bio}, p2{...}, o1?, o2?}.\n"
            " * Odds (o1/o2) only present once bookmakers post lines.\n"
            " */\n"
            f"const UPCOMING_MATCHES = {json.dumps(payload, separators=(',', ':'))};\n"
        )

    return _write_if_changed(path, hash_input, render, label="upcoming_matches")


if __name__ == "__main__":
    sys.exit(main())
