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
# Reuse the round-semantics tables already maintained in materialize.py.
# Don't re-define them here — single source of truth.
from materialize import SEMANTICS, ROUND_DEPTH, RD_NAME_DEPTH, semantics_for


# ─── Model port: Python mirror of matchProbBreakdown ─────────────────────────
# Source: wta_analytics.html lines 969-1106. Constants must stay in sync with
# the JS — any tweak there should be reflected here, and vice versa.
SHARPEN          = 2.5
PYTHAGOREAN_K    = 1.5
PROB_FLOOR, PROB_CAP = 0.05, 0.95


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


def match_prob(pA: dict, pB: dict, surf_code: str) -> dict:
    """Port of matchProbBreakdown. Returns {prob, weights, signals, raw}.

    surf_code: 'H' | 'C' | 'G'
    pA / pB shape: {id, pts, ytd, surf: {H,C,G}, form: str, composite: float|None}
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
        weights = {"elo": .35, "surf": .30, "form": .35, "h2h": 0,   "comp": 0}

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
    raw_prob = 1 / (1 + math.exp(-sum_logit * SHARPEN))
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


# ─── Backtest driver ─────────────────────────────────────────────────────────

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


def backtest(conn, tour: str, year: int, limit: int | None,
             output_csv: Path, window_months: int = 6) -> dict:
    """Score every completed match for (tour, year). Writes CSV; returns
    summary stats (n, brier, calibration table)."""
    bios_atp = parse_bios(ROOT / "data" / "players_atp.js")
    bios_wta = parse_bios(ROOT / "data" / "players_wta.js")
    mid_idx  = mid_to_bio_index(conn)

    matches = load_completed_matches(conn, tour, year, limit)
    print(f"[backtest] {len(matches)} candidate match(es) "
          f"for tour={tour} year={year}{f' (limit={limit})' if limit else ''}")

    rows: list[BacktestRow] = []
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

        # PiT inputs — compute strength from accumulated tournament points
        # over the configured window (default 6 months). Replaces the old
        # rankings_snapshots-based path which had no history before 2026-05-02.
        a_pts, a_ytd = synthetic_ranking(conn, mid_a, m["date"], window_months)
        b_pts, b_ytd = synthetic_ranking(conn, mid_b, m["date"], window_months)
        if (a_pts + a_ytd == 0) or (b_pts + b_ytd == 0):
            skipped["no_ranking"] += 1
            continue
        fA = pit_form(conn, mid_a, m["date"])
        fB = pit_form(conn, mid_b, m["date"])
        h_a, h_b = pit_h2h(conn, mid_a, mid_b, m["date"])

        pA = {"id": bA["id"], "pts": a_pts, "ytd": a_ytd,
              "surf": bA["surf"], "form": fA,
              "composite": None, "h2h_wins": h_a}
        pB = {"id": bB["id"], "pts": b_pts, "ytd": b_ytd,
              "surf": bB["surf"], "form": fB,
              "composite": None, "h2h_wins": h_b}

        result = match_prob(pA, pB, m["surface"])
        won = 1 if m["winner_id"] == mid_a else 0

        rows.append(BacktestRow(
            match_id=m["id"], date=m["date"], tour=match_tour,
            surface=m["surface"],
            p1_mid=mid_a, p2_mid=mid_b,
            p1_name=bA["name"], p2_name=bB["name"],
            p1_pts=a_pts, p1_ytd=a_ytd, p2_pts=b_pts, p2_ytd=b_ytd,
            form_p1=fA, form_p2=fB, h2h_p1=h_a, h2h_p2=h_b,
            p_pred=result["prob"],
            won=won,
            weights_json=json.dumps(result["weights"], separators=(",", ":")),
            signals_json=json.dumps(result["signals"], separators=(",", ":")),
        ))

    print(f"[backtest] scored {len(rows)} matches; skipped: {skipped}")

    # Write CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].__dict__.keys()))
            writer.writeheader()
            for r in rows:
                writer.writerow(r.__dict__)
    print(f"[backtest] wrote {output_csv}")

    return summarize(rows)


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


def summarize(rows: list[BacktestRow]) -> dict:
    brier = brier_score(rows)
    cal   = calibration_table(rows)
    print(f"\n[summary] n={len(rows)}  Brier={brier:.4f}  "
          f"(0.25 = uninformed; 0.0 = perfect)")
    print(f"\n  bin            n   pred   actual   Δ(pp)")
    print(f"  --------------------------------------------")
    for c in cal:
        if c["n"] == 0:
            print(f"  {c['bin']:<12} {c['n']:>4}     —       —       —")
        else:
            print(f"  {c['bin']:<12} {c['n']:>4}  {c['pred_avg']*100:>5.1f}%  "
                  f"{c['actual']*100:>5.1f}%  {c['delta_pp']:>+6.1f}")
    return {"n": len(rows), "brier": brier, "calibration": cal}


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--tour", choices=("atp", "wta", "both"), default="both")
    p.add_argument("--year", type=int, default=date.today().year)
    p.add_argument("--limit", type=int, default=None,
                   help="Score only first N matches (smoke test)")
    p.add_argument("--output", type=Path,
                   default=ROOT / "data" / "backtest_v0.csv",
                   help="CSV path; default data/backtest_v0.csv")
    p.add_argument("--window-months", type=int, default=6,
                   help="Strength window for synthetic ranking. 12 ≈ official "
                        "T12M; 6 (default) is more responsive to current form. "
                        "Tune to match where the dashboard's strength signal "
                        "is heading.")
    args = p.parse_args()

    conn = connect(read_only=True)
    backtest(conn, args.tour, args.year, args.limit, args.output,
             window_months=args.window_months)
    return 0


if __name__ == "__main__":
    sys.exit(main())
