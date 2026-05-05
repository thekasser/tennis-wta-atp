#!/usr/bin/env python3
"""
backtest.py — Point-in-Time (PiT) backtest of the dashboard's predictor.

PURPOSE
-------
The dashboard's calibration panel scores predictions using TODAY'S bios on
yesterday's matches — a soft backtest that leaks future info (composite,
form, rankings all reflect outcomes that happened AFTER the match). That
inflates apparent accuracy and obscures whether the +15-25 pp mid-bin
underconfidence is real model error or hindsight bias.

This script runs a true PiT backtest: for each completed match, every input
to matchProbBreakdown is recomputed using ONLY data dated < match_date.
Output is a CSV with one row per match (predicted prob, signal contribs,
actual outcome) so calibration analysis is downstream-agnostic.

V0 SKELETON (this file)
-----------------------
- Pulls T12M + YTD points from rankings_snapshots (latest ≤ match_date)
- Computes form string from prior matches (PiT, tour-only)
- Uses static bio surface profile (hand-curated; not hindsight)
- Counts H2H from prior meetings only
- SKIPS composite — model falls back to the no-composite weight branch.
  Composite is the heaviest PiT piece (needs weekly recompute of trapezoid
  metrics from match stats); deferring to v1.

V1 (next session)
-----------------
- Add weekly-cached PiT composite from trapezoid metrics
- Comparison: v0 (no composite) vs v1 (PiT composite) vs soft-backtest
  (today's bios) → quantifies hindsight bias contribution

USAGE
-----
    python3 scripts/backtest.py --tour wta --limit 100        # smoke test
    python3 scripts/backtest.py --tour both --year 2026       # full backtest
    python3 scripts/backtest.py --output backtest_v0.csv      # custom path

OUTPUT CSV columns
------------------
    match_id, date, tour, surface, p1_id, p2_id, p1_name, p2_name, won,
    p_pred, p1_pts, p1_ytd, p2_pts, p2_ytd, form_p1, form_p2,
    h2h_p1_wins, h2h_p2_wins, weights_json, signals_json

Per-row Brier and aggregate calibration are printed at the end.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from db import connect
# Reuse single-source-of-truth tables and aggregation logic from materialize.
# Don't re-define them here — divergence between backtest and materialize is
# exactly the kind of bug that makes calibration analysis untrustworthy.
from materialize import (
    SEMANTICS, ROUND_DEPTH, RD_NAME_DEPTH, semantics_for,
    _aggregate_year,                                # composite metric extractor
)


# ─── Model port: Python mirror of matchProbBreakdown ─────────────────────────
# Source: wta_analytics.html lines 969-1106. Defaults below MUST match the JS;
# the function accepts overrides so the backtest can sweep tuning values.
DEFAULT_SHARPEN          = 2.5
DEFAULT_BARE_ELO_WEIGHT  = 0.35   # bare-case (no comp, no h2h) Elo share
PYTHAGOREAN_K            = 1.5
PROB_FLOOR, PROB_CAP     = 0.05, 0.95
# Bare-case surf:form ratio preserved across bare_elo overrides. Original is
# surf=.30, form=.35 within the (1 - elo) = .65 remaining slice.
_BARE_SURF_SHARE = 0.30 / (0.30 + 0.35)   # ≈ 0.4615
_BARE_FORM_SHARE = 0.35 / (0.30 + 0.35)   # ≈ 0.5385


def effective_elo(pts: int, ytd: int) -> tuple[float, str]:
    """Trajectory-aware Elo: max of T12M floor and recency-projected (3·YTD).
    Returns (elo, source) where source is 't12m-floor' or 'trajectory'.
    """
    if (pts or 0) + (ytd or 0) == 0:
        return 0.0, "none"
    trajectory = 0.3 * pts + 3.0 * ytd
    strength   = max(pts, trajectory)
    source     = "t12m-floor" if strength == pts else "trajectory"
    return math.sqrt(strength) * 8, source


def _logit(p: float) -> float:
    c = max(0.02, min(0.98, p))
    return math.log(c / (1 - c))


def _pythag(a: float, b: float, k: float = PYTHAGOREAN_K) -> float:
    """Pythagorean (a^k / (a^k + b^k)). Returns 0.5 if both zero."""
    a_k = max(a, 0) ** k
    b_k = max(b, 0) ** k
    return a_k / (a_k + b_k) if (a_k + b_k) > 0 else 0.5


def form_pct(form_str: str, n: int = 10) -> float:
    """Win rate from last-n form string (W/L). Returns fraction in [0, 1]."""
    if not form_str:
        return 0.5
    last_n = form_str[-n:]
    if not last_n:
        return 0.5
    wins = sum(1 for c in last_n if c == "W")
    return wins / len(last_n)


def match_prob(pA: dict, pB: dict, surf_code: str,
               *, sharpen: float = DEFAULT_SHARPEN,
               bare_elo: float = DEFAULT_BARE_ELO_WEIGHT) -> dict:
    """Port of matchProbBreakdown. Returns {prob, weights, signals, raw}.

    surf_code: 'H' | 'C' | 'G'
    pA / pB shape: {id, pts, ytd, surf: {H,C,G}, form: str, composite: float|None}

    sharpen / bare_elo are exposed as kwargs so the backtest can sweep them
    without touching the dashboard's JS source.
    """
    eA_elo, eA_src = effective_elo(pA.get("pts", 0), pA.get("ytd", 0))
    eB_elo, eB_src = effective_elo(pB.get("pts", 0), pB.get("ytd", 0))
    elo_p = 1 / (1 + 10 ** ((eB_elo - eA_elo) / 400)) if (eA_elo + eB_elo) > 0 else 0.5

    sA = pA.get("surf", {}).get(surf_code, 0.55)
    sB = pB.get("surf", {}).get(surf_code, 0.55)
    surf_p = _pythag(sA, sB)

    fA = form_pct(pA.get("form", ""))
    fB = form_pct(pB.get("form", ""))
    form_p = _pythag(fA, fB)

    cA = pA.get("composite")
    cB = pB.get("composite")
    comp_avail = cA is not None and cB is not None
    comp_p = (1 / (1 + math.exp(-(cA - cB) * 0.7))) if comp_avail else 0.5

    h2h_a = pA.get("h2h_wins", 0)
    h2h_b = pB.get("h2h_wins", 0)
    h2h_avail = (h2h_a + h2h_b) > 0
    h2h_p = (h2h_a / (h2h_a + h2h_b)) if h2h_avail else 0.5

    if h2h_avail and comp_avail:
        weights = {"elo": .12, "surf": .20, "form": .18, "h2h": .12, "comp": .38}
    elif h2h_avail:
        weights = {"elo": .22, "surf": .28, "form": .25, "h2h": .25, "comp": 0}
    elif comp_avail:
        weights = {"elo": .14, "surf": .24, "form": .22, "h2h": 0,   "comp": .40}
    else:
        # Bare case — only Elo, surf, form. Original: 0.35 / 0.30 / 0.35.
        # We expose `bare_elo` so the backtest can damp it; surf:form ratio
        # is preserved across the freed weight.
        rest = 1.0 - bare_elo
        weights = {"elo": bare_elo,
                   "surf": rest * _BARE_SURF_SHARE,
                   "form": rest * _BARE_FORM_SHARE,
                   "h2h": 0, "comp": 0}

    surf_delta  = abs(sA - sB)
    surf_factor = min(1.0, 0.25 + surf_delta * 3.75)
    weights["surf"] *= surf_factor
    sum_w = sum(weights.values())
    if sum_w > 0:
        weights = {k: v / sum_w for k, v in weights.items()}

    sum_logit = (
        weights["elo"]  * _logit(elo_p)
        + weights["surf"] * _logit(surf_p)
        + weights["form"] * _logit(form_p)
        + weights["h2h"]  * _logit(h2h_p)
        + weights["comp"] * _logit(comp_p)
    )
    raw_prob = 1 / (1 + math.exp(-sum_logit * sharpen))
    prob = min(PROB_CAP, max(PROB_FLOOR, raw_prob))

    return {
        "prob": prob,
        "weights": weights,
        "signals": {"elo_p": elo_p, "surf_p": surf_p, "form_p": form_p,
                    "h2h_p": h2h_p, "comp_p": comp_p},
        "raw": {"eloA": eA_elo, "eloB": eB_elo, "elo_src_A": eA_src, "elo_src_B": eB_src,
                "fA": fA, "fB": fB, "sA": sA, "sB": sB,
                "h2h_avail": h2h_avail, "comp_avail": comp_avail},
    }


# ─── PiT data layer ──────────────────────────────────────────────────────────

# Tour-level tier labels — matches the dashboard's strict-tour form filter.
# Keep this in sync with _isTourMatch in wta_analytics.html.
TOUR_LEVEL_TYPES = {"GS", "M1000", "W1000", "M500", "W500", "M250", "W250",
                    "ATPFinals", "WTAFinals"}


def parse_bios(path: Path) -> dict[int, dict]:
    """Parse data/players_*.js into {bio_id: {name, ab, surf:{H,C,G}, mid}}.
    The JS files are JS literals; we extract per-field with regex rather than
    evaluate. Field order varies (id, mid, sid OR id, sid, mid; age may or
    may not appear; auto-added bios omit pic/age) — so we iterate bio blocks
    by counting braces, then extract each field independently.

    Drops bios with no `mid` (e.g. legacy entries with sid only — they can't
    be joined to Matchstat match data anyway).
    """
    text = path.read_text(encoding="utf-8")
    bios: dict[int, dict] = {}

    # Iterate balanced {...} blocks at the top level of the array. Tolerates
    # nested objects (surf:{...}, injNote may have braces).
    depth = 0
    start = -1
    blocks: list[str] = []
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append(text[start:i + 1])
                start = -1

    field = lambda blk, name, pat: (m.group(1) if (m := re.search(rf'\b{name}\s*:\s*{pat}', blk)) else None)
    for blk in blocks:
        bio_id = field(blk, "id",  r"(\d+)")
        mid    = field(blk, "mid", r"(\d+)")
        name   = field(blk, "name", r'"([^"]+)"')
        if not (bio_id and mid and name):
            continue
        ab  = field(blk, "ab",  r'"([^"]+)"') or name.split()[-1]
        nat = field(blk, "nat", r'"([^"]*)"') or "UNK"
        surf_match = re.search(
            r"surf\s*:\s*\{\s*H\s*:\s*([\d.]+)\s*,\s*C\s*:\s*([\d.]+)\s*,\s*G\s*:\s*([\d.]+)\s*\}",
            blk)
        if not surf_match:
            continue
        bios[int(bio_id)] = {
            "id":   int(bio_id),
            "mid":  int(mid),
            "name": name,
            "ab":   ab,
            "nat":  nat,
            "surf": {"H": float(surf_match.group(1)),
                     "C": float(surf_match.group(2)),
                     "G": float(surf_match.group(3))},
        }
    return bios


def synthetic_ranking(conn, mid: int, asof_date: str,
                      window_months: int = 6) -> tuple[int, int]:
    """Compute (window_pts, ytd_pts) by accumulating points from tournament
    results dated < asof_date.

    This replaces pulling rankings from rankings_snapshots (which only has
    history back to 2026-05-02 — the Phase 1 SQLite cutover). Computing
    from match data:
      - gives true PiT for any match in our DB (~2025-01-01 onward),
      - lets us pivot to a shorter strength window than T12M cleanly:
        change `window_months` and the "Elo from points" signal stays
        sample-consistent.

    Algorithm mirrors official ATP/WTA ranking:
      1. Group player's matches by tournament_id.
      2. For each tournament, find the deepest round reached (using
         the SEMANTICS table from materialize.py).
      3. Look up points from the tournament's points_table column.
      4. Sum across tournaments whose end_date is in the window.

    YTD = same logic but window starts at Jan 1 of asof_date's year. YTD
    matches the dashboard's "race" semantics — current calendar year only.
    """
    asof = date.fromisoformat(asof_date)
    window_start = (asof - timedelta(days=window_months * 30)).isoformat()
    ytd_start    = date(asof.year, 1, 1).isoformat()

    rows = list(conn.execute("""
        SELECT m.tournament_id, m.round, m.winner_id, m.date,
               t.start_date, t.end_date, t.draw_size, t.points_table
        FROM matches m
        LEFT JOIN tournaments t ON m.tournament_id = t.id
        WHERE m.date < ?
          AND (m.p1_id = ? OR m.p2_id = ?)
          AND m.tournament_id IS NOT NULL
          AND t.points_table IS NOT NULL
    """, (asof_date, mid, mid)))

    by_tournament: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_tournament[r["tournament_id"]].append(r)

    window_pts = 0
    ytd_pts    = 0
    for tid, t_matches in by_tournament.items():
        end_date = t_matches[0]["end_date"]
        if not end_date or (end_date < window_start and end_date < ytd_start):
            continue
        try:
            pts_table = json.loads(t_matches[0]["points_table"])
        except (json.JSONDecodeError, TypeError):
            continue
        sem = semantics_for(t_matches[0]["draw_size"])

        # Deepest round reached: max RD_NAME_DEPTH across the player's matches
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
        pts = pts_table.get(stage, 0)

        if end_date >= window_start:
            window_pts += pts
        if end_date >= ytd_start:
            ytd_pts += pts

    return window_pts, ytd_pts


def pit_form(conn, mid: int, asof_date: str, n: int = 10,
             tour_only: bool = True) -> str:
    """Last-n W/L string from matches before asof_date. Tour-only mirrors
    the dashboard's strict-tour form filter (see derivedForm + _isTourMatch).

    n=10 matches the JS `formPct(str, n=10)` default. We pull a few extra
    in case some have NULL winner_id (W/O, RET) and need to be filtered out.
    """
    placeholders = ",".join("?" * len(TOUR_LEVEL_TYPES)) if tour_only else None
    sql = """
        SELECT m.winner_id, m.p1_id, m.p2_id
        FROM matches m
    """
    if tour_only:
        sql += " LEFT JOIN tournaments t ON m.tournament_id = t.id "
    sql += " WHERE m.date < ? AND (m.p1_id = ? OR m.p2_id = ?) "
    sql += "   AND m.winner_id IS NOT NULL "
    if tour_only:
        sql += f" AND t.type IN ({placeholders}) "
    sql += " ORDER BY m.date DESC LIMIT ?"

    params: list[Any] = [asof_date, mid, mid]
    if tour_only:
        params = [asof_date, mid, mid] + list(TOUR_LEVEL_TYPES)
    params.append(n + 5)  # buffer for filtering

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return ""
    # Reverse to oldest→newest (matches the convention used by formBarHTML)
    chars: list[str] = []
    for r in reversed(rows):
        if r["winner_id"] == mid:
            chars.append("W")
        elif r["winner_id"] in (r["p1_id"], r["p2_id"]):
            chars.append("L")
        # else skip (winner not one of the two players — corrupt row)
    return "".join(chars[-n:])


def pit_h2h(conn, mid_a: int, mid_b: int, asof_date: str) -> tuple[int, int]:
    """(a_wins, b_wins) from matches before asof_date involving both players."""
    rows = conn.execute("""
        SELECT winner_id FROM matches
        WHERE date < ?
          AND ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?))
          AND winner_id IS NOT NULL
    """, (asof_date, mid_a, mid_b, mid_b, mid_a)).fetchall()
    a = sum(1 for r in rows if r["winner_id"] == mid_a)
    b = sum(1 for r in rows if r["winner_id"] == mid_b)
    return a, b


def mid_to_bio_index(conn) -> dict[int, dict]:
    """Build {mid: {bio_id, tour, name}} from the players table for joins."""
    out: dict[int, dict] = {}
    for r in conn.execute("SELECT mid, tour, bio_id, name FROM players"):
        if r["mid"]:
            out[r["mid"]] = dict(r)
    return out


# ─── PiT composite (v1) ──────────────────────────────────────────────────────
# Mirrors decorateTrapezoidComposite in wta_analytics.html — same metric set,
# same shrinkage prior, same z-score cohort definition. The PiT difference is
# the AGGREGATION WINDOW: instead of TRAPEZOID_*'s static T6M-from-now, we
# aggregate metrics over a 180-day window ending at asof_date, then z-score
# against a cohort computed at the same date.

COMPOSITE_METRICS = [
    "totalPtsWonPct",
    "serviceGamesWonPct",   # replaced bpSavedPct 2026-05-05 — captures hold
                            # rate (avoidance + saving), strictly more
                            # informative than per-chance BP saved.
    "returnGamesWonPct",    # replaced bpWonPct  2026-05-05 — captures break
                            # rate (pressure + conversion), strictly more
                            # informative than per-chance BP conversion.
    "tbWinPct", "decSetWinPct", "matchWinPct",
]
SHRINK_PRIOR          = 15      # matches `decorateTrapezoidComposite`
COMPOSITE_WINDOW_DAYS = 180     # ≈ T6M (lookupComposite default in dashboard)
COMPOSITE_MIN_ZS      = 3       # min metrics with valid z-score; below → None


def _player_matches_in_window(conn, mid: int, asof_date: str,
                              days: int) -> list[dict]:
    """Pull matches for this player in [asof_date - days, asof_date) shaped
    like _aggregate_year expects (dict access, t_type joined, JSON stat
    blobs intact)."""
    start = (date.fromisoformat(asof_date) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT m.id, m.p1_id, m.p2_id, m.winner_id, m.score, m.best_of,
               m.round, m.stat_p1, m.stat_p2, m.date,
               t.type AS t_type
        FROM matches m
        LEFT JOIN tournaments t ON m.tournament_id = t.id
        WHERE m.date >= ? AND m.date < ?
          AND (m.p1_id = ? OR m.p2_id = ?)
    """, (start, asof_date, mid, mid)).fetchall()
    return [dict(r) for r in rows]


def _isoweek_start(asof_date: str) -> str:
    """Monday of the ISO week containing asof_date — used as a cache bucket
    so we recompute the cohort at most once per week."""
    d = date.fromisoformat(asof_date)
    iso_year, iso_week, _ = d.isocalendar()
    return date.fromisocalendar(iso_year, iso_week, 1).isoformat()


def _build_composite_cohort(conn, tour: str, asof_date: str,
                            mid_idx: dict[int, dict],
                            window_days: int = COMPOSITE_WINDOW_DAYS) -> dict:
    """Aggregate the 6 composite metrics for every bio'd player on `tour`
    over the window ending at `asof_date`. Compute (mean, std) per metric
    across the cohort. O(N_bios × N_matches_per_bio) — call rarely (cached
    weekly by pit_composite).

    Returns:
      {
        'metrics':  {mid: {metric: value}},      # raw aggregations
        'stats':    {metric: (mean, std)},       # cohort distribution
        'matches':  {mid: n_matches},            # for shrinkage
      }
    """
    metrics: dict[int, dict] = {}
    matches_count: dict[int, int] = {}
    for mid, meta in mid_idx.items():
        if meta["tour"] != tour:
            continue
        ms = _player_matches_in_window(conn, mid, asof_date, window_days)
        agg = _aggregate_year(ms, mid)
        if agg is None:
            continue
        metrics[mid] = agg
        matches_count[mid] = agg.get("matches", 0)

    stats: dict[str, tuple[float, float]] = {}
    for m_name in COMPOSITE_METRICS:
        vals = [agg[m_name] for agg in metrics.values()
                if agg.get(m_name) is not None]
        if len(vals) < 3:
            continue
        mean = sum(vals) / len(vals)
        var  = sum((v - mean) ** 2 for v in vals) / len(vals)
        std  = math.sqrt(var)
        if std > 0:
            stats[m_name] = (mean, std)
    return {"metrics": metrics, "stats": stats, "matches": matches_count}


def pit_composite(conn, mid: int, tour: str, asof_date: str,
                  cache: dict[tuple[str, str], dict],
                  mid_idx: dict[int, dict]) -> float | None:
    """Composite z-score for `mid` as of `asof_date`. Z-scored against the
    cohort of all bio'd players on `tour` aggregated at the same week.
    Cohort is computed once per (tour, ISO-week) tuple and reused.

    Returns None if the player has fewer than COMPOSITE_MIN_ZS valid metrics
    (insufficient stat coverage in the window).
    """
    week = _isoweek_start(asof_date)
    key  = (tour, week)
    if key not in cache:
        cache[key] = _build_composite_cohort(conn, tour, week, mid_idx)
    cohort = cache[key]

    raw = cohort["metrics"].get(mid)
    if not raw:
        return None
    zs = []
    for m_name in COMPOSITE_METRICS:
        v = raw.get(m_name)
        s = cohort["stats"].get(m_name)
        if v is None or not s:
            continue
        zs.append((v - s[0]) / s[1])
    if len(zs) < COMPOSITE_MIN_ZS:
        return None
    raw_z = sum(zs) / len(zs)
    n = cohort["matches"].get(mid, 0)
    shrinkage = n / (n + SHRINK_PRIOR)
    return round(raw_z * shrinkage, 2)


# ─── Backtest driver ─────────────────────────────────────────────────────────

@dataclass
class MatchFeatures:
    """All PiT data for one match — output of collect_features(), input to
    score_features(). Decoupling lets us pull data once and re-score under
    multiple SHARPEN / bare_elo configs without re-querying the DB."""
    match_id: str
    date: str
    tour: str
    surface: str
    mid_a: int
    mid_b: int
    bio_a: dict
    bio_b: dict
    pts_a: int
    ytd_a: int
    pts_b: int
    ytd_b: int
    form_a: str
    form_b: str
    h2h_a: int
    h2h_b: int
    comp_a: float | None
    comp_b: float | None
    won_a: int          # 1 if A won; 0 if B won (deterministic-by-mid slot)


@dataclass
class BacktestRow:
    match_id: str
    date: str
    tour: str
    surface: str
    p1_mid: int
    p2_mid: int
    p1_name: str
    p2_name: str
    p1_pts: int
    p1_ytd: int
    p2_pts: int
    p2_ytd: int
    form_p1: str
    form_p2: str
    h2h_p1: int
    h2h_p2: int
    comp_p1: float | None
    comp_p2: float | None
    p_pred: float
    won: int          # 1 if p1 won (matches p_pred for p1); 0 if p2 won
    weights_json: str
    signals_json: str


def load_completed_matches(conn, tour: str | None, year: int,
                           limit: int | None = None) -> list[sqlite3.Row]:
    """Pull scoreable matches: known winner, known surface, both players
    are bio'd (we have surface profile, can look up bio_id)."""
    sql = """
        SELECT m.id, m.date, m.tour, m.surface, m.p1_id, m.p2_id, m.winner_id
        FROM matches m
        WHERE m.winner_id IS NOT NULL
          AND m.surface IN ('H', 'C', 'G')
          AND substr(m.date, 1, 4) = ?
    """
    params: list[Any] = [str(year)]
    if tour and tour != "both":
        sql += " AND m.tour = ?"
        params.append(tour)
    sql += " ORDER BY m.date ASC, m.id ASC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def collect_features(conn, tour: str, year: int, limit: int | None,
                     window_months: int = 6, use_composite: bool = True,
                     soft: bool = False) -> tuple[list[MatchFeatures], dict]:
    """Pass 1: pull every PiT input for every scoreable match. Returns
    (features, skipped_counts). Heavy — does all SQL + composite cohort
    cohort builds. Run this once per (year, soft, window_months) config.
    """
    bios_atp = parse_bios(ROOT / "data" / "players_atp.js")
    bios_wta = parse_bios(ROOT / "data" / "players_wta.js")
    mid_idx  = mid_to_bio_index(conn)

    matches = load_completed_matches(conn, tour, year, limit)
    mode = "SOFT (today's bios)" if soft else "PiT"
    comp_str = "with composite" if use_composite else "no composite"
    print(f"[collect] {len(matches)} candidate match(es) "
          f"for tour={tour} year={year}{f' (limit={limit})' if limit else ''} "
          f"— {mode}, {comp_str}, window={window_months}m")

    today_iso = date.today().isoformat()
    composite_cache: dict[tuple[str, str], dict] = {}

    features: list[MatchFeatures] = []
    skipped = {"no_bio": 0, "no_ranking": 0}
    for m in matches:
        # Slot assignment: deterministic by mid (lower mid → A). Decoupling
        # slot from match-recorded order is critical because Matchstat
        # ALWAYS puts the winner in p1_id (we verified: 3471 of 3471 2026
        # matches have winner_id == p1_id). If we used Matchstat's order,
        # `won` would always be 1 and calibration would be meaningless.
        if m["p1_id"] < m["p2_id"]:
            mid_a, mid_b = m["p1_id"], m["p2_id"]
        else:
            mid_a, mid_b = m["p2_id"], m["p1_id"]
        meta_a = mid_idx.get(mid_a)
        meta_b = mid_idx.get(mid_b)
        if not meta_a or not meta_b:
            skipped["no_bio"] += 1
            continue
        match_tour = m["tour"] or meta_a["tour"]
        bios = bios_atp if match_tour == "atp" else bios_wta
        bA = bios.get(meta_a["bio_id"])
        bB = bios.get(meta_b["bio_id"])
        if not bA or not bB:
            skipped["no_bio"] += 1
            continue

        # asof_date governs every data lookup. soft=True pretends "now" is
        # today regardless of when the match happened — that's exactly how
        # the dashboard's soft calibration panel works. PiT (the default)
        # uses the match date itself.
        asof = today_iso if soft else m["date"]

        a_pts, a_ytd = synthetic_ranking(conn, mid_a, asof, window_months)
        b_pts, b_ytd = synthetic_ranking(conn, mid_b, asof, window_months)
        if (a_pts + a_ytd == 0) or (b_pts + b_ytd == 0):
            skipped["no_ranking"] += 1
            continue
        fA = pit_form(conn, mid_a, asof)
        fB = pit_form(conn, mid_b, asof)
        h_a, h_b = pit_h2h(conn, mid_a, mid_b, asof)

        if use_composite:
            cA = pit_composite(conn, mid_a, match_tour, asof, composite_cache, mid_idx)
            cB = pit_composite(conn, mid_b, match_tour, asof, composite_cache, mid_idx)
        else:
            cA = cB = None

        features.append(MatchFeatures(
            match_id=m["id"], date=m["date"], tour=match_tour,
            surface=m["surface"],
            mid_a=mid_a, mid_b=mid_b,
            bio_a=bA, bio_b=bB,
            pts_a=a_pts, ytd_a=a_ytd, pts_b=b_pts, ytd_b=b_ytd,
            form_a=fA, form_b=fB, h2h_a=h_a, h2h_b=h_b,
            comp_a=cA, comp_b=cB,
            won_a=1 if m["winner_id"] == mid_a else 0,
        ))

    print(f"[collect] kept {len(features)} matches; skipped: {skipped}")
    return features, skipped


def score_features(features: list[MatchFeatures], *,
                   sharpen: float = DEFAULT_SHARPEN,
                   bare_elo: float = DEFAULT_BARE_ELO_WEIGHT) -> list[BacktestRow]:
    """Pass 2: run match_prob on each MatchFeatures and produce CSV-shaped
    rows. Cheap — no DB access. Re-callable with different sharpen/bare_elo
    to sweep tuning without re-collecting features.
    """
    rows: list[BacktestRow] = []
    for f in features:
        pA = {"id": f.bio_a["id"], "pts": f.pts_a, "ytd": f.ytd_a,
              "surf": f.bio_a["surf"], "form": f.form_a,
              "composite": f.comp_a, "h2h_wins": f.h2h_a}
        pB = {"id": f.bio_b["id"], "pts": f.pts_b, "ytd": f.ytd_b,
              "surf": f.bio_b["surf"], "form": f.form_b,
              "composite": f.comp_b, "h2h_wins": f.h2h_b}
        result = match_prob(pA, pB, f.surface,
                            sharpen=sharpen, bare_elo=bare_elo)
        rows.append(BacktestRow(
            match_id=f.match_id, date=f.date, tour=f.tour, surface=f.surface,
            p1_mid=f.mid_a, p2_mid=f.mid_b,
            p1_name=f.bio_a["name"], p2_name=f.bio_b["name"],
            p1_pts=f.pts_a, p1_ytd=f.ytd_a, p2_pts=f.pts_b, p2_ytd=f.ytd_b,
            form_p1=f.form_a, form_p2=f.form_b,
            h2h_p1=f.h2h_a, h2h_p2=f.h2h_b,
            comp_p1=f.comp_a, comp_p2=f.comp_b,
            p_pred=result["prob"], won=f.won_a,
            weights_json=json.dumps(result["weights"], separators=(",", ":")),
            signals_json=json.dumps(result["signals"], separators=(",", ":")),
        ))
    return rows


def write_csv(rows: list[BacktestRow], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].__dict__.keys()))
            writer.writeheader()
            for r in rows:
                writer.writerow(r.__dict__)
    print(f"[csv] wrote {output_csv}")


def backtest(conn, tour: str, year: int, limit: int | None,
             output_csv: Path, window_months: int = 6,
             use_composite: bool = True, soft: bool = False,
             sharpen: float = DEFAULT_SHARPEN,
             bare_elo: float = DEFAULT_BARE_ELO_WEIGHT,
             composite_only: bool = False,
             show_misses: int = 10) -> dict:
    """Convenience wrapper for single-config runs."""
    features, _ = collect_features(conn, tour, year, limit,
                                   window_months=window_months,
                                   use_composite=use_composite, soft=soft)
    rows = score_features(features, sharpen=sharpen, bare_elo=bare_elo)
    write_csv(rows, output_csv)
    return summarize(rows, composite_only=composite_only,
                     sharpen=sharpen, bare_elo=bare_elo,
                     show_misses=show_misses)


# ─── Brier + calibration ─────────────────────────────────────────────────────

def brier_score(rows: list[BacktestRow]) -> float:
    """Mean (p_pred - won)². Lower is better; 0.25 = 50/50 baseline."""
    if not rows:
        return float("nan")
    return sum((r.p_pred - r.won) ** 2 for r in rows) / len(rows)


def calibration_table(rows: list[BacktestRow], n_bins: int = 10
                      ) -> list[dict]:
    """Bin predictions into [0, 0.1), [0.1, 0.2), … and report
    (predicted_avg, actual_winrate, n) per bin."""
    bins: list[list[BacktestRow]] = [[] for _ in range(n_bins)]
    for r in rows:
        idx = min(int(r.p_pred * n_bins), n_bins - 1)
        bins[idx].append(r)
    out = []
    for i, bin_rows in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bin_rows:
            out.append({"bin": f"[{lo:.2f},{hi:.2f})", "n": 0,
                        "pred_avg": None, "actual": None, "delta_pp": None})
            continue
        pred_avg = sum(r.p_pred for r in bin_rows) / len(bin_rows)
        actual   = sum(r.won    for r in bin_rows) / len(bin_rows)
        out.append({
            "bin":      f"[{lo:.2f},{hi:.2f})",
            "n":        len(bin_rows),
            "pred_avg": pred_avg,
            "actual":   actual,
            "delta_pp": (actual - pred_avg) * 100,
        })
    return out


def top_misses(rows: list[BacktestRow], n: int = 10) -> list[dict]:
    """Find the model's worst calls — matches where the picked side had the
    highest pre-match confidence but lost. Useful sanity check: if the top
    misses are dominated by injury withdrawals or known upsets, the model
    isn't broken; if they look like normal matchups the model badly
    miscalled, that's a real signal of model weakness.

    Returns a list of dicts ordered by descending confidence-on-wrong-side.
    """
    misses = []
    for r in rows:
        # p_pred is prob of slot A winning. Picked side = whichever is favored.
        if r.p_pred >= 0.5:
            picked_won = (r.won == 1)
            confidence = r.p_pred
            picked_name, picked_pts   = r.p1_name, r.p1_pts
            actual_name, actual_pts   = r.p2_name, r.p2_pts
            picked_comp, actual_comp  = r.comp_p1, r.comp_p2
        else:
            picked_won = (r.won == 0)
            confidence = 1 - r.p_pred
            picked_name, picked_pts   = r.p2_name, r.p2_pts
            actual_name, actual_pts   = r.p1_name, r.p1_pts
            picked_comp, actual_comp  = r.comp_p2, r.comp_p1
        if picked_won:
            continue
        misses.append({
            "date": r.date, "tour": r.tour, "surface": r.surface,
            "confidence": confidence,
            "picked": picked_name, "picked_pts": picked_pts,
            "picked_comp": picked_comp,
            "actual": actual_name, "actual_pts": actual_pts,
            "actual_comp": actual_comp,
        })
    misses.sort(key=lambda x: -x["confidence"])
    return misses[:n]


def print_top_misses(rows: list[BacktestRow], n: int = 10) -> None:
    misses = top_misses(rows, n)
    if not misses:
        print("\n[misses] no losing favorites in this set")
        return
    print(f"\n[misses] top {len(misses)} highest-confidence wrong calls:")
    print(f"  date        tour  surf  conf    favored (pts, comp)         →  winner (pts, comp)")
    print(f"  " + "─" * 100)
    for m in misses:
        pc = f"{m['picked_comp']:+.2f}" if m['picked_comp'] is not None else "  — "
        ac = f"{m['actual_comp']:+.2f}" if m['actual_comp'] is not None else "  — "
        picked_str = f"{m['picked']} ({m['picked_pts']:,}, {pc})"
        actual_str = f"{m['actual']} ({m['actual_pts']:,}, {ac})"
        print(f"  {m['date']}  {m['tour']:<3}   {m['surface']:<3}   "
              f"{m['confidence']*100:>4.1f}%   {picked_str:<32}  →  {actual_str}")


def summarize(rows: list[BacktestRow], *, composite_only: bool = False,
              sharpen: float | None = None,
              bare_elo: float | None = None,
              show_misses: int = 10) -> dict:
    """Print n / Brier / calibration table. composite_only filters to matches
    where both players had a non-None PiT composite — that's the model in
    its strongest configuration (composite-available weight branch)."""
    n_total = len(rows)
    if composite_only:
        rows = [r for r in rows
                if r.comp_p1 is not None and r.comp_p2 is not None]
    brier = brier_score(rows)
    cal   = calibration_table(rows)
    tag_bits = []
    if sharpen is not None:  tag_bits.append(f"sharpen={sharpen}")
    if bare_elo is not None: tag_bits.append(f"bare_elo={bare_elo}")
    if composite_only:       tag_bits.append(f"composite-only")
    tag = f" [{', '.join(tag_bits)}]" if tag_bits else ""
    n_str = f"n={len(rows)}" + (f"/{n_total}" if composite_only else "")
    print(f"\n[summary{tag}] {n_str}  Brier={brier:.4f}  "
          f"(0.25 = uninformed; 0.0 = perfect)")
    print(f"\n  bin            n   pred   actual   Δ(pp)")
    print(f"  --------------------------------------------")
    for c in cal:
        if c["n"] == 0:
            print(f"  {c['bin']:<12} {c['n']:>4}     —       —       —")
        else:
            print(f"  {c['bin']:<12} {c['n']:>4}  {c['pred_avg']*100:>5.1f}%  "
                  f"{c['actual']*100:>5.1f}%  {c['delta_pp']:>+6.1f}")
    if show_misses > 0:
        print_top_misses(rows, n=show_misses)
    return {"n": len(rows), "brier": brier, "calibration": cal,
            "top_misses": top_misses(rows, n=show_misses) if show_misses else []}


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=("atp", "wta", "both"), default="both")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--limit", type=int, default=None,
                   help="Score only first N matches (smoke test)")
    p.add_argument("--output", type=Path, default=None,
                   help="CSV path. Defaults to data/backtest_<mode>.csv where "
                        "mode reflects --soft and --no-composite flags.")
    p.add_argument("--window-months", type=int, default=6,
                   help="Strength window for synthetic ranking. 12 ≈ official "
                        "T12M; 6 (default) is more responsive to current form.")
    p.add_argument("--no-composite", action="store_true",
                   help="Skip composite signal — model uses bare-case weight "
                        "branch. Use this to compare against v0 results.")
    p.add_argument("--soft", action="store_true",
                   help="Soft baseline: replace per-match asof_date with "
                        "today for ALL lookups. Mirrors the dashboard's "
                        "calibration panel (today's bios on past matches). "
                        "Use to measure hindsight bias as PiT-vs-soft delta.")
    # Tuning knobs — exposed so the backtest can sweep without editing the JS.
    p.add_argument("--sharpen", type=float, default=DEFAULT_SHARPEN,
                   help=f"Logit-blend sharpening multiplier. "
                        f"Default {DEFAULT_SHARPEN} mirrors the dashboard. "
                        f"Lower = less extreme predictions.")
    p.add_argument("--bare-elo-weight", type=float,
                   default=DEFAULT_BARE_ELO_WEIGHT,
                   help=f"Elo's share in the bare-case branch (no comp, no "
                        f"H2H). Default {DEFAULT_BARE_ELO_WEIGHT} mirrors "
                        f"the dashboard. Lower → surf+form get more weight.")
    p.add_argument("--composite-only", action="store_true",
                   help="Restrict calibration analysis to matches where both "
                        "players have non-None PiT composite — the model's "
                        "strongest configuration.")
    p.add_argument("--sweep-sharpen", type=str, default=None,
                   help="Comma-separated SHARPEN values, e.g. '1.0,1.5,2.0,2.5'. "
                        "Collects features once, scores under each value, "
                        "prints comparison. Skips CSV writing.")
    p.add_argument("--show-misses", type=int, default=10,
                   help="Print the top N highest-confidence wrong calls "
                        "(losing favorites) at the end of every run. "
                        "Set to 0 to disable. Default 10.")
    args = p.parse_args()

    if args.output is None:
        suffix = "_soft" if args.soft else "_pit"
        suffix += "_v0" if args.no_composite else "_v1"
        if args.sharpen != DEFAULT_SHARPEN:
            suffix += f"_s{args.sharpen}"
        if args.bare_elo_weight != DEFAULT_BARE_ELO_WEIGHT:
            suffix += f"_be{args.bare_elo_weight}"
        args.output = ROOT / "data" / f"backtest{suffix}.csv"

    conn = connect(read_only=True)

    if args.sweep_sharpen:
        sharpen_vals = [float(s.strip()) for s in args.sweep_sharpen.split(",")]
        features, _ = collect_features(
            conn, args.tour, args.year, args.limit,
            window_months=args.window_months,
            use_composite=not args.no_composite,
            soft=args.soft,
        )
        print(f"\n[sweep] scoring {len(features)} matches across "
              f"{len(sharpen_vals)} SHARPEN value(s)…")
        results = []
        for s in sharpen_vals:
            rows = score_features(features, sharpen=s,
                                  bare_elo=args.bare_elo_weight)
            res = summarize(rows, composite_only=args.composite_only,
                            sharpen=s, bare_elo=args.bare_elo_weight,
                            show_misses=0)   # suppress in sweep — too noisy
            results.append((s, res))
        # Compact comparison table
        print("\n[sweep] Brier comparison:")
        print("  sharpen   n     Brier")
        print("  ------------------------")
        for s, r in results:
            print(f"  {s:>5.2f}  {r['n']:>5}   {r['brier']:.4f}")
        return 0

    backtest(conn, args.tour, args.year, args.limit, args.output,
             window_months=args.window_months,
             use_composite=not args.no_composite,
             soft=args.soft,
             sharpen=args.sharpen,
             bare_elo=args.bare_elo_weight,
             composite_only=args.composite_only,
             show_misses=args.show_misses)
    return 0


if __name__ == "__main__":
    sys.exit(main())
