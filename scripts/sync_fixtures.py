#!/usr/bin/env python3
"""
sync_fixtures.py — Fetch upcoming-match fixtures via getTournamentFixtures
for each tournament currently in its active window. Idempotent: re-running
upserts (insert new fixtures, update odds when bookmakers post lines).

Active window mirrors materialize.py's _compute_active_tournaments:
  start_date − 7 days  (covers qualifying)
  end_date   + 1 day   (grace period; finished fixtures gc'd anyway)

Cost: 1 API call per active tournament per run. Today (May 4 2026) with Rome
in its active window = 2 calls. Steady-state ≈ 2-4 calls/day.

Usage:
    python3 scripts/sync_fixtures.py            # default: both tours, all active
    python3 scripts/sync_fixtures.py --tour atp
    python3 scripts/sync_fixtures.py --gc-only  # delete stale fixtures, no fetch
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from db import connect, init_db
from matchstat import MatchstatClient

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def active_tournaments(conn, tour: str) -> list[dict]:
    """Tournaments in the active window (date-derived). Mirrors materialize.py."""
    today = date.today()
    api_col = f"api_id_{tour}"
    rows = list(conn.execute(f"""
        SELECT id, name, start_date, end_date, {api_col} AS api_id
        FROM tournaments
        WHERE (tour = ? OR tour = 'both')
          AND {api_col} IS NOT NULL
          AND start_date IS NOT NULL AND end_date IS NOT NULL
    """, (tour,)))
    out = []
    for r in rows:
        try:
            sd = date.fromisoformat(r["start_date"])
            ed = date.fromisoformat(r["end_date"])
        except (ValueError, TypeError):
            continue
        if (sd - timedelta(days=7)) <= today <= (ed + timedelta(days=1)):
            out.append(dict(r))
    return out


def fetch_tournament_fixtures(client: MatchstatClient, tour: str,
                              api_id: int) -> list[dict]:
    """Paginate getTournamentFixtures for one tournament. Returns list of
    raw fixture dicts."""
    all_items: list[dict] = []
    seen: set[int] = set()
    for page in range(1, 11):
        try:
            data, _ = client.get(tour, f"fixtures/tournament/{api_id}",
                                 params={"pageSize": 100, "pageNo": page,
                                         "filter": "PlayerGroup:both;"})
        except Exception as e:
            print(f"  [{tour} t{api_id} p{page}] error: {str(e)[:120]}",
                  file=sys.stderr)
            break
        items = data.get("data", []) if isinstance(data, dict) else (data or [])
        if not items:
            break
        new = 0
        for it in items:
            iid = it.get("id")
            if iid is None or iid in seen:
                continue
            seen.add(iid)
            all_items.append(it)
            new += 1
        if new == 0 or len(items) < 100:
            break
    return all_items


def upsert_fixture(conn, fix: dict, tour: str, api_id: int,
                   tournament_catalog_id: str | None) -> None:
    """Idempotent insert/update — overwrites everything except 'id'. Re-run
    safely; if odds publish later, they'll appear on the next sync."""
    p1 = fix.get("player1") or {}
    p2 = fix.get("player2") or {}
    conn.execute("""
        INSERT INTO fixtures (id, tour, tournament_id, tournament_api_id,
                              date, round_id,
                              p1_mid, p1_name, p1_country,
                              p2_mid, p2_name, p2_country,
                              odd1, odd2, raw, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            date = excluded.date,
            round_id = excluded.round_id,
            p1_mid = excluded.p1_mid, p1_name = excluded.p1_name, p1_country = excluded.p1_country,
            p2_mid = excluded.p2_mid, p2_name = excluded.p2_name, p2_country = excluded.p2_country,
            odd1 = COALESCE(excluded.odd1, fixtures.odd1),
            odd2 = COALESCE(excluded.odd2, fixtures.odd2),
            raw = excluded.raw,
            fetched_at = excluded.fetched_at
    """, (
        fix["id"], tour, tournament_catalog_id, api_id,
        fix.get("date"), fix.get("roundId"),
        fix.get("player1Id"), p1.get("name"), p1.get("countryAcr"),
        fix.get("player2Id"), p2.get("name"), p2.get("countryAcr"),
        fix.get("odd1"), fix.get("odd2"),
        json.dumps(fix, separators=(",", ":")),
        datetime.now(timezone.utc).isoformat(),
    ))


def gc_stale(conn) -> int:
    """Drop fixtures whose date is in the past — they should now be in the
    `matches` table via sync_matches.py (or never played). Idempotent."""
    today = date.today().isoformat()
    cur = conn.execute("DELETE FROM fixtures WHERE date < ?", (today,))
    return cur.rowcount


def gc_dups(conn) -> int:
    """Dedupe fixtures by (tournament_id, date_day, p1_mid, p2_mid).
    Matchstat occasionally issues different fixture IDs for the SAME
    matchup across pulls (e.g., schedule shifts produce a new id but the
    old row stays). Keep only the row with the latest fetched_at per
    canonical key. Without this, the matchup predictor renders the same
    match twice (different fixture ids → two upsert paths → two rows)."""
    cur = conn.execute("""
        DELETE FROM fixtures
        WHERE id IN (
          SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
              PARTITION BY tournament_id, substr(date, 1, 10),
                           MIN(p1_mid, p2_mid), MAX(p1_mid, p2_mid)
              ORDER BY fetched_at DESC
            ) AS rn
            FROM fixtures
            WHERE p1_mid IS NOT NULL AND p2_mid IS NOT NULL
          ) WHERE rn > 1
        )
    """)
    return cur.rowcount


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tour", choices=("atp", "wta", "both"), default="both")
    p.add_argument("--gc-only", action="store_true",
                   help="Just delete stale fixtures, no API calls")
    args = p.parse_args()

    conn = init_db(verbose=False)   # idempotent — applies any pending migrations

    if args.gc_only:
        n = gc_stale(conn)
        n_dup = gc_dups(conn)
        conn.commit()
        print(f"[gc] deleted {n} past fixtures, {n_dup} duplicates")
        return 0

    api_key = os.environ.get("MATCHSTAT_API_KEY")
    if not api_key:
        print("MATCHSTAT_API_KEY not set", file=sys.stderr)
        return 1
    client = MatchstatClient(api_key=api_key, auto_log=True)

    total_inserted = 0
    tours = ("atp", "wta") if args.tour == "both" else (args.tour,)
    for tour in tours:
        actives = active_tournaments(conn, tour)
        print(f"=== {tour.upper()} fixtures sync — {len(actives)} active tournament(s) ===")
        for t in actives:
            api_id = t["api_id"]
            cat_id = t["id"]
            items = fetch_tournament_fixtures(client, tour, api_id)
            for fix in items:
                upsert_fixture(conn, fix, tour, api_id, cat_id)
            total_inserted += len(items)
            with_odds = sum(1 for f in items if f.get("odd1") and f.get("odd2"))
            print(f"  {cat_id:25} ({api_id})  {len(items)} fixtures  ({with_odds} with odds)")
        conn.commit()

    n_gc = gc_stale(conn)
    n_dup = gc_dups(conn)
    conn.commit()
    print(f"\n[gc] cleaned {n_gc} past fixtures, {n_dup} duplicate fixtures")
    print(f"[summary] upserted {total_inserted} fixtures total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
