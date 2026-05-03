#!/usr/bin/env python3
"""
sync_rankings.py — Pull T12M + YTD race rankings from Matchstat into
`rankings_snapshots`.

PHASE 1 — replaces the rankings portion of `refresh_rankings_api.py`.
Materialization of season_*.js is now handled by `materialize.py` reading
from the snapshots table.

USAGE
-----
    python3 scripts/sync_rankings.py                    # both tours
    python3 scripts/sync_rankings.py --tour wta
    python3 scripts/sync_rankings.py --top-n 300

Each run takes one snapshot per tour, dated today (UTC). Re-running on the
same day UPSERTs the existing rows. The PRIMARY KEY (tour, snapshot_date,
bio_id) keeps history append-only across days.

NOTE on WTA points
------------------
Matchstat returns WTA T12M `point` / `points` scaled ×100 vs. the official
WTA value (e.g. Sabalenka comes back with 1,000,000 instead of 10,000).
Race points are NOT scaled. We divide T12M by 100 for WTA so downstream
consumers see official numbers.
"""
from __future__ import annotations
import argparse
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect, init_db
from matchstat import MatchstatClient


DEFAULT_TOP_N = 200


# ─── Name matching for fallback when API row has no playerId ────────────────

def _normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str.lower().strip())


def _build_name_index(rows: list[dict]) -> dict[str, int]:
    """Map normalized name variants → mid for fallback when API omits playerId."""
    idx: dict[str, int] = {}
    for r in rows:
        mid = r["mid"]
        idx[_normalize(r["name"])] = mid
        if r.get("ab"):
            idx[_normalize(r["ab"])] = mid
        parts = (r["name"] or "").split()
        if parts:
            idx[_normalize(parts[-1])] = mid
            if len(parts) >= 2:
                idx[_normalize(f"{parts[0][0]}. {parts[-1]}")] = mid
    return idx


def _match_to_mid(api_row: dict, name_idx: dict[str, int]) -> int | None:
    # The actual response shape: {position, point, player: {id, name, countryAcr}}
    player_obj = api_row.get("player") or {}
    pid = (
        player_obj.get("id")
        or api_row.get("playerId") or api_row.get("player_id")
    )
    if pid:
        return pid
    nm = player_obj.get("name") or api_row.get("playerName") or api_row.get("name") or ""
    norm = _normalize(nm)
    if norm in name_idx:
        return name_idx[norm]
    last = norm.split()[-1] if norm else None
    return name_idx.get(last) if last else None


# ─── Fetch + upsert ─────────────────────────────────────────────────────────

def _log_fetch(conn, meta: dict, rows_inserted: int | None = None) -> None:
    conn.execute("""
        INSERT INTO api_fetch_log
            (fetched_at, endpoint, params, http_status, rows_returned,
             rows_inserted, ms_elapsed, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        meta.get("fetched_at"), meta.get("endpoint"), meta.get("params"),
        meta.get("http_status"), meta.get("rows_returned"), rows_inserted,
        meta.get("ms_elapsed"), meta.get("error"),
    ))


def sync_tour(client: MatchstatClient, conn, tour: str, top_n: int) -> dict:
    print(f"\n=== {tour.upper()} rankings sync ===")
    bios = [dict(r) for r in conn.execute(
        "SELECT mid, bio_id, name, ab FROM players WHERE tour = ?", (tour,)
    )]
    name_idx = _build_name_index(bios)
    mid_to_bio = {b["mid"]: b["bio_id"] for b in bios}

    # 1. T12M (singles)
    print(f"  → /ranking/singles (pageSize={top_n})")
    t12m_data, t12m_meta = client.rankings(tour, race=False, page_size=top_n)
    t12m_rows = (t12m_data or {}).get("data") or t12m_data or []
    print(f"    {t12m_meta['rows_returned']} rows, http={t12m_meta['http_status']}, ms={t12m_meta['ms_elapsed']}")

    # 2. Race (YTD)
    print(f"  → /ranking/race (pageSize={top_n})")
    race_data, race_meta = client.rankings(tour, race=True, page_size=top_n)
    race_rows = (race_data or {}).get("data") or race_data or []
    print(f"    {race_meta['rows_returned']} rows, http={race_meta['http_status']}, ms={race_meta['ms_elapsed']}")

    # 3. Reduce to {bio_id: {rank, pts, ytd_pts}}
    snapshot: dict[int, dict] = {}
    for r in t12m_rows:
        mid = _match_to_mid(r, name_idx)
        bio_id = mid_to_bio.get(mid) if mid else None
        if not bio_id:
            continue
        raw_pts = r.get("point") or r.get("points") or 0
        # WTA T12M comes back ×100 — divide. Race points are correct as-is.
        pts = round(raw_pts / 100) if tour == "wta" else raw_pts
        snapshot.setdefault(bio_id, {})["rank"] = r.get("position") or r.get("currentRank")
        snapshot[bio_id]["pts"] = pts

    for r in race_rows:
        mid = _match_to_mid(r, name_idx)
        bio_id = mid_to_bio.get(mid) if mid else None
        if not bio_id:
            continue
        snapshot.setdefault(bio_id, {})["ytd_pts"] = r.get("racePoints") or r.get("points") or 0

    # 4. Upsert into rankings_snapshots (one row per bio, dated today UTC).
    today = date.today().isoformat()
    inserted = 0
    for bio_id, vals in snapshot.items():
        cur = conn.execute("""
            INSERT INTO rankings_snapshots (tour, snapshot_date, bio_id, rank, pts, ytd_pts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tour, snapshot_date, bio_id) DO UPDATE SET
                rank    = excluded.rank,
                pts     = excluded.pts,
                ytd_pts = excluded.ytd_pts
        """, (tour, today, bio_id, vals.get("rank"), vals.get("pts"), vals.get("ytd_pts")))
        if cur.rowcount:
            inserted += 1

    _log_fetch(conn, t12m_meta, rows_inserted=inserted)
    _log_fetch(conn, race_meta, rows_inserted=0)
    conn.commit()

    print(f"  ✓ snapshotted {len(snapshot)} bios on {today}")
    return {"matched": len(snapshot), "t12m": len(t12m_rows), "race": len(race_rows)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=["atp", "wta", "both"], default="both")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = p.parse_args()

    init_db()
    conn = connect()
    client = MatchstatClient()

    tours = ["atp", "wta"] if args.tour == "both" else [args.tour]
    summary = {t: sync_tour(client, conn, t, args.top_n) for t in tours}

    print("\n=== summary ===")
    for t, s in summary.items():
        print(f"  {t.upper()}: matched {s['matched']} bios "
              f"(T12M {s['t12m']}, race {s['race']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
