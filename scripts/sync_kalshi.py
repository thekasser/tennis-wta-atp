#!/usr/bin/env python3
"""
sync_kalshi.py — Pull tennis market prices from Kalshi into kalshi_odds.

Kalshi is a CFTC-regulated event-contract market. Free read-only API
(no auth required for /events, /markets, orderbook). Fills coverage
gaps where Matchstat's traditional-sportsbook feed is silent —
especially qualifying matches where mainstream books don't post lines.

USAGE
-----
    python3 scripts/sync_kalshi.py                   # both tours
    python3 scripts/sync_kalshi.py --tour wta
    python3 scripts/sync_kalshi.py --include-settled # also pull resolved markets

API SHAPE
---------
GET https://api.elections.kalshi.com/trade-api/v2/events
  ?series_ticker=KXATPMATCH    (or KXWTAMATCH)
  &status=open                  (or settled)
  &with_nested_markets=true     (markets returned inline; saves N calls)
  &limit=200

Each event has 2 markets (one YES contract per player), e.g.:
  event_ticker = "KXATPMATCH-26MAY06TSIMAC"
  title        = "Tsitsipas vs Machac"
  markets[0].ticker = "KXATPMATCH-26MAY06TSIMAC-TSI"
  markets[0].title  = "Will Stefanos Tsitsipas win the Tsitsipas vs Machac match?"
  markets[0].yes_bid / yes_ask / last_price (cents 0-100)

We match each market's title back to our players table by full name.
A market without a name match is skipped (not in our bio set).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from db import connect, init_db
from matchstat import log_fetch  # reuse api_fetch_log writer for visibility


KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_BY_TOUR = {"atp": "KXATPMATCH", "wta": "KXWTAMATCH"}
DEFAULT_TIMEOUT = 20
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Kalshi has more generous rate limits than Matchstat for read-only endpoints,
# but we still throttle to be polite.
MIN_INTERVAL = 0.25
_last_request_at = 0.0


# ─── HTTP helpers ───────────────────────────────────────────────────────────

def _http_get(path: str, params: dict | None = None) -> tuple[dict, dict]:
    """Wrapper mirroring matchstat.MatchstatClient.get for consistency.
    Returns (parsed_json, fetch_meta). Logs to api_fetch_log."""
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)

    url = f"{KALSHI_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    meta: dict = {
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
        "endpoint":      f"kalshi/{path}",
        "params":        json.dumps(params, separators=(",", ":")) if params else None,
        "http_status":   None,
        "ms_elapsed":    None,
        "rows_returned": None,
        "error":         None,
    }
    t_start = time.time()
    _last_request_at = time.time()
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            meta["http_status"] = resp.status
    except urllib.error.HTTPError as e:
        meta["http_status"] = e.code
        meta["error"]       = e.read().decode("utf-8")[:300] if hasattr(e, "read") else str(e)
        meta["ms_elapsed"]  = int((time.time() - t_start) * 1000)
        log_fetch(meta)
        raise RuntimeError(f"Kalshi HTTP {e.code} on {path}: {meta['error']}") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        meta["error"]      = str(e)[:300]
        meta["ms_elapsed"] = int((time.time() - t_start) * 1000)
        log_fetch(meta)
        raise RuntimeError(f"Kalshi network/parse error on {path}: {e}") from e

    meta["ms_elapsed"] = int((time.time() - t_start) * 1000)
    if isinstance(data, dict):
        evts = data.get("events")
        if isinstance(evts, list):
            meta["rows_returned"] = len(evts)
    log_fetch(meta)
    return data, meta


# ─── Name matching ──────────────────────────────────────────────────────────

# Market title pattern: "Will {Full Name} win the {whatever} match?"
_TITLE_PAT = re.compile(r"^Will\s+(.+?)\s+win the ", re.IGNORECASE)


def _normalize(name: str) -> str:
    """ASCII-fold, lowercase, collapse whitespace. Mirrors
    sync_rankings._normalize so name lookups are consistent."""
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_str.lower().strip())


def extract_player_name(market_title: str) -> str | None:
    """'Will Stefanos Tsitsipas win the Tsitsipas vs Machac match?' →
    'Stefanos Tsitsipas'. Returns None if pattern doesn't match."""
    m = _TITLE_PAT.match(market_title or "")
    return m.group(1) if m else None


def build_name_index(conn) -> dict[str, int]:
    """Map normalized full name → mid. Used to resolve Kalshi market
    titles to our player IDs. Also indexes "{first_initial}. {last}"
    and surname-only forms for fuzzier matches."""
    idx: dict[str, int] = {}
    for r in conn.execute("SELECT mid, name FROM players WHERE mid IS NOT NULL"):
        mid, name = r["mid"], r["name"]
        if not name:
            continue
        idx[_normalize(name)] = mid
        parts = name.split()
        if len(parts) >= 2:
            # Surname-only (collisions possible — last write wins; rely on
            # name-match path returning the bio'd top-200 player most often)
            idx[_normalize(parts[-1])] = mid
            idx[_normalize(f"{parts[0][0]}. {parts[-1]}")] = mid
    return idx


def resolve_player(market_title: str, name_idx: dict[str, int]) -> int | None:
    """Try (full → surname → initial-form) lookups. Return mid or None."""
    name = extract_player_name(market_title)
    if not name:
        return None
    norm = _normalize(name)
    if norm in name_idx:
        return name_idx[norm]
    # Surname fallback — covers WTA name-changes ("Sabalenka" stays Sabalenka,
    # but for some bios the API and Kalshi spellings disagree mid-tournament).
    last = norm.split()[-1]
    return name_idx.get(last)


# ─── Match resolution ───────────────────────────────────────────────────────

def find_match_id(conn, mid_a: int, mid_b: int, kalshi_event: str
                  ) -> tuple[str | None, str | None]:
    """Find the local match_id (and is_pre_match flag) for this Kalshi
    event. Searches matches table first (post-match), then fixtures
    (upcoming). Match keyed by (date, both player IDs).

    Returns (match_id, kind) where kind is 'match' / 'fixture' / None.
    is_pre_match is derived: 'fixture' → True, 'match' → False (already
    completed).
    """
    # Kalshi event ticker has date encoded: KXATPMATCH-26MAY06TSIMAC
    # Parse the date portion (chars 11-18 in YYMMDDD form). Kalshi uses
    # a YY-MMM-DD-ish format with the month as 3-letter abbrev.
    date_str = parse_event_date(kalshi_event)
    if not date_str:
        return None, None

    # Date fuzzy-match: Kalshi's event-ticker date doesn't always match
    # our match date (timezone differences, schedule listing day vs play
    # day). Allow ±1 day window. ID-based matching keeps this safe — we
    # require both player mids to match, so wrong-date collisions are
    # essentially impossible.
    from datetime import date as _date, timedelta as _timedelta
    try:
        d = _date.fromisoformat(date_str)
    except ValueError:
        return None, None
    date_min = (d - _timedelta(days=1)).isoformat()
    date_max = (d + _timedelta(days=1)).isoformat()

    # Prefer the matches table when both exist. fixtures and matches use
    # DIFFERENT id spaces — when a match completes, its fixture row stays
    # around briefly (gc'd later) but the canonical record is the matches
    # row. predictions.match_id always uses the matches.id once available,
    # so kalshi_odds.match_id must do the same to JOIN cleanly.
    row = conn.execute("""
        SELECT id FROM matches
        WHERE date BETWEEN ? AND ?
          AND ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?))
        LIMIT 1
    """, (date_min, date_max, mid_a, mid_b, mid_b, mid_a)).fetchone()
    if row:
        return row["id"], "match"

    # Fall back to fixtures for genuinely upcoming events that haven't
    # been played yet. Exclude doubles rows ("/" in player name) so a
    # singles Kalshi event can't accidentally bind to a doubles fixture
    # that reuses an id from a previously-gc'd singles row.
    row = conn.execute("""
        SELECT id FROM fixtures
        WHERE substr(date, 1, 10) BETWEEN ? AND ?
          AND ((p1_mid = ? AND p2_mid = ?) OR (p1_mid = ? AND p2_mid = ?))
          AND (p1_name IS NULL OR p1_name NOT LIKE '%/%')
          AND (p2_name IS NULL OR p2_name NOT LIKE '%/%')
        LIMIT 1
    """, (date_min, date_max, mid_a, mid_b, mid_b, mid_a)).fetchone()
    if row:
        return str(row["id"]), "fixture"

    return None, None


_MONTH_MAP = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
              "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
              "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}


def parse_event_date(event_ticker: str) -> str | None:
    """Extract ISO date from a Kalshi event ticker like
    'KXATPMATCH-26MAY06TSIMAC' → '2026-05-06'. Format: YY MMM DD
    (2 digits, 3-letter month, 2 digits)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker or "")
    if not m:
        return None
    yy, mmm, dd = m.group(1), m.group(2).upper(), m.group(3)
    if mmm not in _MONTH_MAP:
        return None
    return f"20{yy}-{_MONTH_MAP[mmm]}-{dd}"


# ─── Sync ───────────────────────────────────────────────────────────────────

def fetch_events(tour: str, status: str = "open") -> list[dict]:
    """Pull up to 200 events for the given tour series + status. Each
    event includes its markets inline (with_nested_markets=true)."""
    series = SERIES_BY_TOUR[tour]
    data, _ = _http_get("events", params={
        "series_ticker": series,
        "status": status,
        "with_nested_markets": "true",
        "limit": 200,
    })
    return data.get("events") or []


def upsert_kalshi_odds(conn, mid_idx: dict, event: dict, tour: str
                       ) -> tuple[bool, str]:
    """Resolve the event's two markets to our players, find local
    match_id, and upsert one row into kalshi_odds. Returns
    (success, reason).
    """
    markets = event.get("markets") or []
    if len(markets) < 2:
        return False, "fewer than 2 markets"

    # Resolve both markets to mids.
    m1, m2 = markets[0], markets[1]
    mid1 = resolve_player(m1.get("title", ""), mid_idx)
    mid2 = resolve_player(m2.get("title", ""), mid_idx)
    if not mid1 or not mid2:
        return False, f"name-match failed: {m1.get('title','?')[:40]} | {m2.get('title','?')[:40]}"

    # Slot assignment: lower mid → A (matches our convention everywhere).
    if mid1 < mid2:
        mid_a, mid_b = mid1, mid2
        market_a, market_b = m1, m2
    else:
        mid_a, mid_b = mid2, mid1
        market_a, market_b = m2, m1

    match_id, kind = find_match_id(conn, mid_a, mid_b, event["event_ticker"])
    if not match_id:
        return False, f"no local match for {event['event_ticker']}"

    is_pre_match = 1 if kind == "fixture" else 0

    # Kalshi reports prices in two places depending on market status:
    #   open    → last_price / yes_bid / yes_ask  (cents 0-100)
    #   settled → previous_price_dollars / previous_yes_ask_dollars   (dollars 0-1)
    # Settled markets also have settlement_value_dollars (1.00 winner, 0.00 loser)
    # but we want the PRE-match price for backtest fairness, not the post-
    # settlement value.
    def _price(m: dict) -> float | None:
        # Open: cents-based fields
        last = m.get("last_price")
        if last is not None:
            return last / 100.0
        bid, ask = m.get("yes_bid"), m.get("yes_ask")
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0 / 100.0
        # Settled: dollars-based fields, sometimes returned as strings.
        # previous_* captures the last pre-settlement state; last_price_*
        # is post-settlement and biased to 0/1 once the market resolves.
        def _f(v):
            if v is None:
                return None
            try:
                f = float(v)
                return f if f > 0 else None
            except (TypeError, ValueError):
                return None
        prev_last = _f(m.get("previous_price_dollars")) or _f(m.get("last_price_dollars"))
        if prev_last is not None:
            return prev_last
        prev_bid = _f(m.get("previous_yes_bid_dollars"))
        prev_ask = _f(m.get("previous_yes_ask_dollars"))
        if prev_bid is not None and prev_ask is not None:
            return (prev_bid + prev_ask) / 2.0
        return None

    p1_yes = _price(market_a)
    p2_yes = _price(market_b)

    conn.execute("""
        INSERT OR REPLACE INTO kalshi_odds (
            match_id, kalshi_event, kalshi_market_p1, kalshi_market_p2,
            fetched_at, p1_mid, p2_mid,
            p1_yes_price, p2_yes_price,
            p1_volume, p2_volume,
            is_pre_match, raw
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        match_id,
        event["event_ticker"],
        market_a.get("ticker"),
        market_b.get("ticker"),
        datetime.now(timezone.utc).isoformat(),
        mid_a, mid_b,
        p1_yes, p2_yes,
        market_a.get("volume"), market_b.get("volume"),
        is_pre_match,
        json.dumps({"event": event}, separators=(",", ":")),
    ))
    return True, f"ok ({kind})"


def consolidate_match_ids(conn) -> tuple[int, int]:
    """Rewrite fixture-keyed kalshi_odds.match_id values to the canonical
    matches.id once a fixture has migrated to matches.

    Problem this fixes: when a Kalshi sync runs BEFORE a match exists in
    the matches table, find_match_id falls back to fixtures.id (small
    integer, e.g. 1289). After the match completes and gets a matches
    row (with the 9-digit Matchstat id), no one rewrites the old
    kalshi_odds rows — they stay orphaned. The materialize JOIN against
    predictions.match_id (which uses matches.id) silently drops them.
    Worse: the fixtures table gets gc'd by sync_fixtures, so by the time
    we look for the fixture row to resolve dates+players, it's gone.
    Hence we resolve using kalshi_odds's own (p1_mid, p2_mid) + the date
    encoded in kalshi_event.

    Strategy: for each distinct fixture-keyed kalshi_odds row, parse the
    event date from kalshi_event and look up matches by (date ±1 day,
    p1_mid, p2_mid). If found, rewrite all rows for that old match_id
    to the canonical matches.id. fetched_at conflicts (already a row at
    the same fetched_at on the canonical id) are dropped explicitly.

    Returns (rows_rewritten, rows_dropped_as_dup).
    """
    rewritten = dropped = 0
    # Find distinct fixture-keyed kalshi_odds rows that don't already map
    # to a matches.id. Use kalshi_odds's own columns (p1_mid, p2_mid,
    # kalshi_event) — the fixtures row may be gone.
    candidates = list(conn.execute("""
        SELECT k.match_id AS old_mid,
               k.kalshi_event,
               k.p1_mid, k.p2_mid
        FROM kalshi_odds k
        LEFT JOIN matches m_via_id ON m_via_id.id = k.match_id
        WHERE m_via_id.id IS NULL                    -- not already a matches.id
          AND k.p1_mid IS NOT NULL
          AND k.p2_mid IS NOT NULL
        GROUP BY k.match_id
    """))
    for c in candidates:
        date_str = parse_event_date(c["kalshi_event"] or "")
        if not date_str:
            continue
        target = conn.execute("""
            SELECT id FROM matches
            WHERE date BETWEEN date(?, '-1 day') AND date(?, '+1 day')
              AND ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?))
            LIMIT 1
        """, (date_str, date_str,
              c["p1_mid"], c["p2_mid"], c["p2_mid"], c["p1_mid"])).fetchone()
        if not target:
            continue
        new_mid = target["id"]
        # Drop dups first so the UPDATE doesn't violate the
        # (match_id, fetched_at) PK.
        dup_set = {r["fetched_at"] for r in conn.execute(
            "SELECT fetched_at FROM kalshi_odds WHERE match_id = ?",
            (new_mid,)
        ).fetchall()}
        if dup_set:
            placeholders = ",".join("?" * len(dup_set))
            n_drop = conn.execute(
                f"DELETE FROM kalshi_odds WHERE match_id = ? AND fetched_at IN ({placeholders})",
                (c["old_mid"], *dup_set)
            ).rowcount
            dropped += n_drop
        n_up = conn.execute(
            "UPDATE kalshi_odds SET match_id = ? WHERE match_id = ?",
            (new_mid, c["old_mid"])
        ).rowcount
        rewritten += n_up
    conn.commit()
    return rewritten, dropped


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=("atp", "wta", "both"), default="both")
    p.add_argument("--include-settled", action="store_true",
                   help="Also pull settled markets (post-match prices). "
                        "Useful for one-time backfill of historical Kalshi "
                        "data; cron should leave this off and only sync open.")
    args = p.parse_args()

    init_db(verbose=False)
    conn = connect()
    mid_idx = build_name_index(conn)
    print(f"[kalshi] resolved {len(mid_idx)} name index entries")

    tours = ("atp", "wta") if args.tour == "both" else (args.tour,)
    statuses = ("open",) + (("settled",) if args.include_settled else ())

    grand_inserted = grand_skipped_name = grand_skipped_match = 0
    for tour in tours:
        for status in statuses:
            print(f"\n=== {tour.upper()} / {status} ===")
            try:
                events = fetch_events(tour, status=status)
            except RuntimeError as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                continue
            print(f"  {len(events)} event(s) returned")
            inserted = name_skip = match_skip = 0
            for ev in events:
                ok, reason = upsert_kalshi_odds(conn, mid_idx, ev, tour)
                if ok:
                    inserted += 1
                elif reason.startswith("name-match"):
                    name_skip += 1
                elif reason.startswith("no local match"):
                    match_skip += 1
            conn.commit()
            print(f"  upserted {inserted}, name-unmatched {name_skip}, "
                  f"no-local-match {match_skip}")
            grand_inserted    += inserted
            grand_skipped_name += name_skip
            grand_skipped_match += match_skip

    print(f"\n[summary] {grand_inserted} prices upserted, "
          f"{grand_skipped_name} skipped on name, "
          f"{grand_skipped_match} skipped (no local match)")

    # Consolidation pass: rewrite any kalshi_odds rows still keyed by a
    # fixtures.id whose match has since resolved into the matches table.
    # Without this, pre-match snapshots stay orphaned and never join to
    # predictions. Cheap (only touches stale-keyed rows).
    rewritten, dropped = consolidate_match_ids(conn)
    if rewritten or dropped:
        print(f"[consolidate] rewrote {rewritten} fixture-keyed match_id "
              f"references to matches.id ({dropped} dropped as dup)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
