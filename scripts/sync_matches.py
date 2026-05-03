#!/usr/bin/env python3
"""
sync_matches.py — Pull match-level data from Matchstat into the SQLite DB.

PHASE 1 — replaces the fetch portion of `fetch_match_stats_api.py`. The DB
is the source of truth; this script just keeps it current. Aggregations
(trapezoid metrics, h2h, recent matches) live in `materialize.py`.

USAGE
-----
    python3 scripts/sync_matches.py                        # both tours, current+last year
    python3 scripts/sync_matches.py --tour wta --years 2026
    python3 scripts/sync_matches.py --limit 30             # debug: only first N players
    python3 scripts/sync_matches.py --force                # ignore needs_refetch heuristic

REFETCH HEURISTIC
-----------------
For each top-N player, decide whether to call the API:

  1. Cold start (no matches in DB for this mid)             → fetch
  2. Player has a match in the last 14d at an active        → fetch
     tournament (active=1 in tournaments table)
  3. Latest match in DB is older than yesterday             → fetch
  4. Otherwise                                              → skip

This keeps active-tournament players fresh on every 4h cron, while quiet
players cost ~0 API calls between events. Steady-state: ~30-50 calls per
run during active tournaments, ~5-15 calls between.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect, init_db
from matchstat import MatchstatClient


PAGE_SIZE     = 50
DEFAULT_TOP_N = 200
DEFAULT_YEARS = (date.today().year - 1, date.today().year)

# Matchstat court IDs → surface code. Defaults to 'H' for unknowns.
SURFACE_MAP = {1: "H", 2: "H", 3: "C", 4: "C", 5: "G", 6: "C", 7: "C"}


# ─── Refetch decision ───────────────────────────────────────────────────────

def needs_refetch(conn, mid: int) -> tuple[bool, str]:
    """Decide whether to (re)fetch this player's matches. Returns (bool, reason)."""
    row = conn.execute(
        "SELECT MAX(date) AS latest FROM matches WHERE p1_id=? OR p2_id=?",
        (mid, mid),
    ).fetchone()
    latest = row["latest"]
    if not latest:
        return True, "cold start"

    today      = date.today().isoformat()
    yesterday  = (date.today() - timedelta(days=1)).isoformat()
    fortnight  = (date.today() - timedelta(days=14)).isoformat()

    is_active = conn.execute("""
        SELECT 1 FROM matches m
        JOIN tournaments t ON m.tournament_id = t.id
        WHERE (m.p1_id = ? OR m.p2_id = ?)
          AND m.date >= ?
          AND t.active = 1
        LIMIT 1
    """, (mid, mid, fortnight)).fetchone()
    if is_active:
        return True, "in active tournament"

    if latest < yesterday:
        return True, f"latest match {latest} > 1d old"

    return False, f"latest match {latest}, no active event"


# ─── Match row construction ─────────────────────────────────────────────────

def _round_str(rd: dict | str | None) -> str | None:
    if isinstance(rd, dict):
        return rd.get("shortName") or rd.get("name")
    return rd

def _to_match_row(m: dict, tour: str, tournament_id_by_api_id: dict[int, str]
                  ) -> tuple[dict, dict | None] | None:
    """Convert an API match payload to a DB row dict + JSON stat blobs.

    Returns None if the match is unusable (no date, no players, no id).
    """
    match_id = m.get("id") or m.get("matchId")
    if not match_id:
        return None
    p1id = m.get("player1Id") or m.get("p1Id")
    p2id = m.get("player2Id") or m.get("p2Id")
    if not p1id or not p2id:
        return None
    raw_date = m.get("date") or m.get("matchDate") or ""
    if len(raw_date) < 10:
        return None
    iso_date = raw_date[:10]

    tour_obj = m.get("tournament") or {}
    api_tid  = tour_obj.get("id")
    surface  = SURFACE_MAP.get(tour_obj.get("courtId"))

    stat = m.get("stat") or m.get("stats") or {}
    if isinstance(stat, dict):
        s1 = stat.get("stat1") or stat.get("p1") or stat.get("1") or stat.get("player1")
        s2 = stat.get("stat2") or stat.get("p2") or stat.get("2") or stat.get("player2")
    else:
        s1 = s2 = None

    winner = m.get("match_winner") or m.get("winnerId") or m.get("winner_id")

    return {
        "id":                str(match_id),
        "tournament_id":     tournament_id_by_api_id.get(api_tid) if api_tid else None,
        "tournament_api_id": api_tid,
        "tournament_name":   tour_obj.get("name") or tour_obj.get("tournamentName"),
        "date":              iso_date,
        "round":             _round_str(m.get("round")),
        "surface":           surface,
        "tour":              tour,
        "p1_id":             p1id,
        "p2_id":             p2id,
        "winner_id":         winner if winner not in (None, 0, "") else None,
        "score":             m.get("result") or m.get("score"),
        "best_of":           m.get("bestOf") or m.get("best_of"),
        "stat_p1":           json.dumps(s1, separators=(",", ":")) if s1 else None,
        "stat_p2":           json.dumps(s2, separators=(",", ":")) if s2 else None,
        "fetched_at":        datetime.now(timezone.utc).isoformat(),
        "raw":               json.dumps(m, separators=(",", ":")),
    }


def _upsert_matches(conn, rows: list[dict]) -> int:
    """INSERT OR IGNORE; bumps fetched_at on existing rows. Returns inserted count."""
    if not rows:
        return 0
    inserted = 0
    for r in rows:
        cur = conn.execute("""
            INSERT OR IGNORE INTO matches
                (id, tournament_id, tournament_api_id, tournament_name, date, round,
                 surface, tour, p1_id, p2_id, winner_id, score, best_of,
                 stat_p1, stat_p2, fetched_at, raw)
            VALUES
                (:id, :tournament_id, :tournament_api_id, :tournament_name, :date, :round,
                 :surface, :tour, :p1_id, :p2_id, :winner_id, :score, :best_of,
                 :stat_p1, :stat_p2, :fetched_at, :raw)
        """, r)
        if cur.rowcount:
            inserted += 1
            continue
        # Existing row — refresh fetched_at + winner/score in case they were
        # NULL (W/O, RET) when first ingested and have since been corrected.
        conn.execute("""
            UPDATE matches
            SET fetched_at = :fetched_at,
                winner_id  = COALESCE(winner_id, :winner_id),
                score      = COALESCE(NULLIF(score, ''), :score),
                stat_p1    = COALESCE(stat_p1, :stat_p1),
                stat_p2    = COALESCE(stat_p2, :stat_p2)
            WHERE id = :id
        """, r)
    return inserted


# ─── Per-player fetch (paginated) ───────────────────────────────────────────

def _fetch_year(client: MatchstatClient, conn, tour: str, mid: int, year: int
                ) -> tuple[list[dict], list[dict]]:
    """Return (raw_matches, fetch_metas) for one (player, year). Logs to api_fetch_log."""
    matches: list[dict] = []
    metas:   list[dict] = []
    page = 1
    while True:
        try:
            data, meta = client.past_matches(tour, mid, year=year,
                                             page_size=PAGE_SIZE, page_no=page)
        except RuntimeError as e:
            print(f"      ! mid={mid} year={year} page={page}: {e}", file=sys.stderr)
            # We may not have a meta if the throw was pre-meta; log a synthetic one.
            metas.append({
                "fetched_at":    datetime.now(timezone.utc).isoformat(),
                "endpoint":      f"{tour}/player/past-matches/{mid}",
                "params":        json.dumps({"year": year, "page": page}, separators=(",", ":")),
                "http_status":   None, "ms_elapsed": None, "rows_returned": 0,
                "error":         str(e)[:300],
            })
            break
        rows = (data or {}).get("data") or data or []
        if not isinstance(rows, list):
            metas.append(meta)
            break
        matches.extend(rows)
        metas.append(meta)
        if len(rows) < PAGE_SIZE:
            break
        page += 1
        if page > 10:  # ~500-match safety cap
            break
    return matches, metas


def _log_fetch(conn, meta: dict, rows_inserted: int | None = None) -> None:
    conn.execute("""
        INSERT INTO api_fetch_log
            (fetched_at, endpoint, params, http_status, rows_returned,
             rows_inserted, ms_elapsed, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        meta.get("fetched_at"),
        meta.get("endpoint"),
        meta.get("params"),
        meta.get("http_status"),
        meta.get("rows_returned"),
        rows_inserted,
        meta.get("ms_elapsed"),
        meta.get("error"),
    ))


# ─── Main ──────────────────────────────────────────────────────────────────

def sync_tour(client: MatchstatClient, conn, tour: str, years: list[int],
              top_n: int, limit: int | None, force: bool) -> dict:
    print(f"\n=== {tour.upper()} sync — years {years}, top_n={top_n} ===")
    cur = conn.execute("""
        SELECT mid, bio_id, name FROM players
        WHERE tour = ? AND bio_id <= ?
        ORDER BY bio_id
    """, (tour, top_n))
    players = [dict(r) for r in cur.fetchall()]
    if limit:
        players = players[:limit]
    print(f"  candidates: {len(players)}")

    # Build tournament api_id → tournament_id maps once (one per tour).
    api_id_col = f"api_id_{tour}"
    tid_map = {
        row[api_id_col]: row["id"]
        for row in conn.execute(f"SELECT id, {api_id_col} FROM tournaments WHERE {api_id_col} IS NOT NULL")
    }

    fetched = skipped = total_inserted = total_seen = 0
    for i, p in enumerate(players, 1):
        mid    = p["mid"]
        name   = p["name"]
        bio_id = p["bio_id"]
        if not force:
            do_fetch, reason = needs_refetch(conn, mid)
            if not do_fetch:
                print(f"  [{i:3d}/{len(players)}] bio#{bio_id:3d} mid={mid:<7} {name:<32} skip ({reason})")
                skipped += 1
                continue

        for y in years:
            raw_ms, metas = _fetch_year(client, conn, tour, mid, y)
            rows = []
            for m in raw_ms:
                row = _to_match_row(m, tour, tid_map)
                if row:
                    rows.append(row)
            inserted = _upsert_matches(conn, rows)
            total_inserted += inserted
            total_seen     += len(rows)
            for j, m in enumerate(metas):
                # First page's row count gets credit for inserts; others log 0.
                _log_fetch(conn, m, rows_inserted=inserted if j == 0 else 0)
            if raw_ms:
                print(f"  [{i:3d}/{len(players)}] bio#{bio_id:3d} mid={mid:<7} {name:<32} {y}: "
                      f"{len(raw_ms):>3} fetched ({len(rows)} usable), {inserted:>3} new")
        fetched += 1
        conn.commit()

    return {
        "candidates": len(players), "fetched": fetched, "skipped": skipped,
        "rows_seen": total_seen, "rows_inserted": total_inserted,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    p.add_argument("--years", nargs="+", type=int, default=list(DEFAULT_YEARS))
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--limit", type=int, default=None,
                   help="hard cap on candidate players per tour (debug)")
    p.add_argument("--force", action="store_true",
                   help="ignore needs_refetch heuristic; fetch every candidate")
    args = p.parse_args()

    init_db()
    conn = connect()
    client = MatchstatClient()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    summary = {}
    for t in tours:
        summary[t] = sync_tour(client, conn, t, args.years, args.top_n, args.limit, args.force)

    print("\n=== summary ===")
    for t, s in summary.items():
        print(f"  {t.upper()}: {s['fetched']}/{s['candidates']} fetched ({s['skipped']} skipped), "
              f"{s['rows_seen']} rows seen, {s['rows_inserted']} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
