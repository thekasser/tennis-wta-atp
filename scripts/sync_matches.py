#!/usr/bin/env python3
"""
sync_matches.py — Pull match-level data from Matchstat into the SQLite DB.

PHASE 1 — replaces the fetch portion of `fetch_match_stats_api.py`. The DB
is the source of truth; this script just keeps it current. Aggregations
(trapezoid metrics, h2h, recent matches) live in `materialize.py`.

USAGE
-----
    python3 scripts/sync_matches.py                        # both tours, current year
    python3 scripts/sync_matches.py --tour wta --years 2026
    python3 scripts/sync_matches.py --limit 30             # debug: only first N players
    python3 scripts/sync_matches.py --force                # ignore decide_fetch heuristic

REFETCH HEURISTIC (rewritten 2026-05-05 after API-cap incident)
---------------------------------------------------------------
For each top-N player, decide whether to call the API and which years:

  1. Cold start (no matches in DB)               → fetch all DEFAULT_YEARS
  2. Player has a match at a tournament whose
     date window (start-7d to end+1d) contains
     today                                        → fetch [current_year]
  3. Otherwise                                    → skip

This mirrors materialize._compute_active_tournaments — the date-window
definition of "active" — instead of the stale `tournaments.active=1` flag,
which gets left set after tournaments end. Crucially, dropping the
`latest < yesterday` trigger fixes the bug where ~85% of top-200 players
got refetched on every cron (they don't play every day, so the test
always passed).

Expected steady-state cost:
    Active week (1 tour-tour event): ~80–120 calls/run
    Active week (combined slam):     ~200 calls/run
    Quiet week:                      ~0 calls/run (cold-starts only)
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
# Default to current year only — 2025 history is largely frozen, no need to
# refetch every cron run. Cold-starts override this and pull (year-1, year)
# so new bios still get full T12M backfill.
DEFAULT_YEARS         = (date.today().year,)
COLD_START_YEARS      = (date.today().year - 1, date.today().year)

# Matchstat court IDs → surface code. Defaults to 'H' for unknowns.
SURFACE_MAP = {1: "H", 2: "H", 3: "C", 4: "C", 5: "G", 6: "C", 7: "C"}


# ─── Refetch decision ───────────────────────────────────────────────────────

# Onboarding window: brief broad-sweep to discover which top-200 are in the
# draw. After this window, the precision filter takes over (only players who
# already have a match at an active tournament). The window is intentionally
# tight to keep the API budget under 10k/mo:
#   start - 2d = covers late wildcards / draw edits (quals players aren't in
#                our top-200 bio set, so no need to extend further back)
#   start + 2d = covers R1 (1-2 days). By R2 every drawn top-200 has a match.
# The ACTIVE WINDOW (used by `active_ids` and materialize.py) is wider —
# [start - 7d, end + 1d] — and reflects "show as active in the dashboard,"
# which is a separate concern from "broadly sweep the API."
ONBOARDING_PRE_DAYS  = 2
ONBOARDING_POST_DAYS = 2
ACTIVE_PRE_DAYS      = 7   # wider for "active" detection; mirrors materialize.py
ACTIVE_GRACE_DAYS    = 1   # post-final grace; mirrors materialize.py


def active_window_state(conn, tour: str) -> tuple[set[str], bool]:
    """Return (active_tournament_ids, any_in_onboarding) FOR THIS TOUR.

    Scoped by tour so that a WTA-only event in onboarding doesn't trigger an
    ATP broad sweep (and vice versa). `tour='both'` tournaments count for
    both calls.

    `active_tournament_ids` mirrors materialize._compute_active_tournaments —
    a tournament is active if today ∈ [start_date - 7d, end_date + 1d].

    `any_in_onboarding` is True if any active tournament for this tour is
    still in its [start - 7d, start + 3d] onboarding window. While true, we
    sweep the full candidate list so new draw entrants get picked up
    (otherwise the precision filter creates a chicken-and-egg: a player with
    no DB match at the tournament never gets fetched, so never gets a DB
    match).
    """
    today = date.today()
    actives: set[str] = set()
    onboarding = False
    for r in conn.execute("""
        SELECT id, start_date, end_date FROM tournaments
        WHERE start_date IS NOT NULL AND end_date IS NOT NULL
          AND (tour = ? OR tour = 'both')
    """, (tour,)):
        try:
            sd = date.fromisoformat(r["start_date"])
            ed = date.fromisoformat(r["end_date"])
        except (ValueError, TypeError):
            continue
        if (sd - timedelta(days=ACTIVE_PRE_DAYS)) <= today <= (ed + timedelta(days=ACTIVE_GRACE_DAYS)):
            actives.add(r["id"])
            if (sd - timedelta(days=ONBOARDING_PRE_DAYS)) <= today <= (sd + timedelta(days=ONBOARDING_POST_DAYS)):
                onboarding = True
    return actives, onboarding


def decide_fetch(conn, mid: int, active_ids: set[str], any_onboarding: bool
                 ) -> tuple[list[int], str]:
    """Decide WHICH YEARS to fetch for this player. Returns (years, reason).

    Empty list = skip. Logic:
      1. No matches in DB                          → COLD_START_YEARS (backfill)
      2. Any active tournament still onboarding    → DEFAULT_YEARS (broad sweep)
      3. Has match at an active tournament         → DEFAULT_YEARS (precision)
      4. Otherwise                                  → [] (skip)
    """
    row = conn.execute(
        "SELECT MAX(date) AS latest FROM matches WHERE p1_id=? OR p2_id=?",
        (mid, mid),
    ).fetchone()
    latest = row["latest"]
    if not latest:
        return list(COLD_START_YEARS), "cold start"

    if not active_ids:
        return [], f"latest match {latest}, no tournament in active window"

    if any_onboarding:
        return list(DEFAULT_YEARS), "active-window tournament onboarding"

    placeholders = ",".join("?" * len(active_ids))
    in_active = conn.execute(f"""
        SELECT 1 FROM matches
        WHERE (p1_id = ? OR p2_id = ?)
          AND tournament_id IN ({placeholders})
        LIMIT 1
    """, (mid, mid, *active_ids)).fetchone()
    if in_active:
        return list(DEFAULT_YEARS), "in active-window tournament"

    return [], f"latest match {latest}, not in any active-window tournament"


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
    # Defensive: drop doubles. Matchstat returns combined names like
    # "Bhambri/Venus" for teams; past-matches is per-singles-player so
    # this is normally a no-op, but the check is cheap insurance.
    p1n = ((m.get("player1") or {}).get("name") or "")
    p2n = ((m.get("player2") or {}).get("name") or "")
    if "/" in p1n or "/" in p2n:
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

def sync_tour(client: MatchstatClient, conn, tour: str, override_years: list[int] | None,
              top_n: int, limit: int | None, force: bool) -> dict:
    """Sync one tour. If override_years is set, fetch those years for every
    candidate (used by --years and --force). Otherwise decide_fetch picks
    per-player years based on active-tournament status.
    """
    label = f"override years {override_years}" if override_years else "decide_fetch heuristic"
    print(f"\n=== {tour.upper()} sync — {label}, top_n={top_n} ===")
    cur = conn.execute("""
        SELECT mid, bio_id, name FROM players
        WHERE tour = ? AND bio_id <= ?
        ORDER BY bio_id
    """, (tour, top_n))
    players = [dict(r) for r in cur.fetchall()]
    if limit:
        players = players[:limit]
    print(f"  candidates: {len(players)}")

    active_ids, any_onboarding = active_window_state(conn, tour)
    if active_ids:
        mode = "ONBOARDING (broad sweep)" if any_onboarding else "mid-tournament (precision)"
        print(f"  active-window tournaments: {sorted(active_ids)} — {mode}")
    else:
        print(f"  no tournaments currently in active window — only cold-starts will fetch")

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
        if force or override_years:
            years_to_fetch = list(override_years) if override_years else list(COLD_START_YEARS)
            reason = "force" if force else "override years"
        else:
            years_to_fetch, reason = decide_fetch(conn, mid, active_ids, any_onboarding)
        if not years_to_fetch:
            print(f"  [{i:3d}/{len(players)}] bio#{bio_id:3d} mid={mid:<7} {name:<32} skip ({reason})")
            skipped += 1
            continue

        for y in years_to_fetch:
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
                      f"{len(raw_ms):>3} fetched ({len(rows)} usable), {inserted:>3} new ({reason})")
        fetched += 1
        conn.commit()

    return {
        "candidates": len(players), "fetched": fetched, "skipped": skipped,
        "rows_seen": total_seen, "rows_inserted": total_inserted,
    }


def gc_dup_matches(conn) -> int:
    """Dedupe matches by (date_day, MIN(p1,p2), MAX(p1,p2)). Matchstat
    occasionally issues different match IDs for the SAME match across pulls
    (e.g., schedule revisions or repeat fetches produce a new id but the old
    row is also present). Without this pass, the same match shows twice in
    form-bar tooltips, twice in recent_matches, and skews aggregates that
    don't dedup at read time. Tennis players don't play each other twice in
    a single day, so (day, unordered-pair) is a sound canonical key.

    Tiebreak (which row to KEEP per group):
      1. winner_id IS NOT NULL  (completed match data)
      2. score is non-empty
      3. stat_p1 IS NOT NULL    (per-side stats)
      4. earliest fetched_at    (closest to original ingest)
      5. lowest id              (older Matchstat ID, more likely referenced
                                 by kalshi_odds / predictions / h2h)
    Returns rows deleted."""
    cur = conn.execute("""
        DELETE FROM matches
        WHERE id IN (
          SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
              PARTITION BY substr(date, 1, 10),
                           MIN(p1_id, p2_id), MAX(p1_id, p2_id)
              ORDER BY (winner_id IS NOT NULL) DESC,
                       (score IS NOT NULL AND score != '') DESC,
                       (stat_p1 IS NOT NULL) DESC,
                       fetched_at ASC,
                       CAST(id AS INTEGER) ASC
            ) AS rn
            FROM matches
          ) WHERE rn > 1
        )
    """)
    return cur.rowcount


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    p.add_argument("--years", nargs="+", type=int, default=None,
                   help="override decide_fetch — fetch these years for every candidate. "
                        "When unset (the cron default), decide_fetch picks per player: "
                        "current year for active-window players, both years for cold-starts.")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--limit", type=int, default=None,
                   help="hard cap on candidate players per tour (debug)")
    p.add_argument("--force", action="store_true",
                   help="ignore decide_fetch heuristic; fetch every candidate")
    p.add_argument("--gc-only", action="store_true",
                   help="just run the duplicate-match GC pass, no API calls")
    args = p.parse_args()

    init_db()
    conn = connect()

    if args.gc_only:
        n = gc_dup_matches(conn)
        conn.commit()
        print(f"[gc] deleted {n} duplicate match rows")
        return 0

    client = MatchstatClient()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    summary = {}
    for t in tours:
        summary[t] = sync_tour(client, conn, t, args.years, args.top_n, args.limit, args.force)

    n_dup = gc_dup_matches(conn)
    conn.commit()
    if n_dup:
        print(f"\n[gc] deleted {n_dup} duplicate match rows")

    print("\n=== summary ===")
    for t, s in summary.items():
        print(f"  {t.upper()}: {s['fetched']}/{s['candidates']} fetched ({s['skipped']} skipped), "
              f"{s['rows_seen']} rows seen, {s['rows_inserted']} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
