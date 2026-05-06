#!/usr/bin/env python3
"""
log_predictions.py — Prospective prediction log.

For every upcoming fixture (and recently-completed active-tournament match
not yet predicted), runs match_prob with the SAME inputs the live dashboard
uses, writes a row to the `predictions` table. Each row carries the model's
version stamp so a future SHARPEN/composite change doesn't pollute prior
data.

Once matches finish (matches.winner_id IS NOT NULL), predictions can be
JOINed against outcomes for a clean prospective Brier — no PiT
reconstruction, no synthetic-ranking guesswork. This is the gold-standard
measurement source going forward.

USAGE
-----
    python3 scripts/log_predictions.py           # default: log all fixtures + recent matches
    python3 scripts/log_predictions.py --dry-run # preview row counts, don't insert

This script makes ZERO API calls. All inputs come from the local DB.
Designed to run after materialize.py in the CI workflow.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from db import connect, init_db
from backtest import (
    match_prob, parse_bios, mid_to_bio_index,
    pit_form, pit_h2h, pit_composite, synthetic_ranking,
    DEFAULT_SHARPEN, DEFAULT_BARE_ELO_WEIGHT,
)


# Bumps when ANY model logic changes (SHARPEN, weight branches, composite recipe).
# Keeps prediction history segmentable: filter `WHERE model_version = '...'`
# to compare apples-to-apples after a tuning change.
MODEL_VERSION = (
    f"sharpen={DEFAULT_SHARPEN}"
    f"/bare_elo={DEFAULT_BARE_ELO_WEIGHT}"
    f"/comp=games-v1"   # bump suffix when COMPOSITE_METRICS changes
)


def matches_to_predict(conn) -> list[dict]:
    """All matches we want a prediction logged for THIS run.

    Sources:
      1. Every row in `fixtures` with a date >= today (upcoming).
      2. Every row in `matches` whose tournament is currently in its
         active window — covers cases where a match completes between
         our last fixture-sync and this run, so the fixture is gone but
         we still want to capture the model's pre-match prediction.
         Skips any (match_id, date(predicted_at)) we already logged today.

    Returns dicts shaped like fixtures rows for uniform downstream handling.
    """
    today = date.today().isoformat()
    rows: list[dict] = []

    # Fixtures (upcoming matches)
    for r in conn.execute("""
        SELECT id AS match_id, tour, tournament_id,
               substr(date, 1, 10) AS date,
               p1_mid, p1_name, p2_mid, p2_name,
               raw
        FROM fixtures
        WHERE p1_mid IS NOT NULL AND p2_mid IS NOT NULL
          AND substr(date, 1, 10) >= ?
    """, (today,)):
        d = dict(r)
        # Surface comes from the tournament — pull it
        if d["tournament_id"]:
            t = conn.execute("SELECT surface FROM tournaments WHERE id = ?",
                             (d["tournament_id"],)).fetchone()
            d["surface"] = t["surface"] if t else None
        else:
            d["surface"] = None
        rows.append(d)

    # Recently-completed active-tournament matches (yesterday + today)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for r in conn.execute("""
        SELECT m.id AS match_id, m.tour, m.tournament_id, m.date,
               m.p1_id AS p1_mid, m.p2_id AS p2_mid, m.surface
        FROM matches m
        JOIN tournaments t ON m.tournament_id = t.id
        WHERE m.date >= ?
          AND date(t.start_date, '-7 days') <= date('now')
          AND date(t.end_date, '+1 day') >= date('now')
    """, (yesterday,)):
        d = dict(r)
        d["p1_name"] = None  # filled in by mid_idx below
        d["p2_name"] = None
        rows.append(d)

    return rows


def already_predicted_today(conn, match_id: str) -> bool:
    """Skip if we already logged a prediction for this match today.
    Avoids duplicate rows when CI runs twice per day on the same fixture."""
    today_str = date.today().isoformat()
    row = conn.execute("""
        SELECT 1 FROM predictions
        WHERE match_id = ? AND substr(predicted_at, 1, 10) = ?
        LIMIT 1
    """, (str(match_id), today_str)).fetchone()
    return row is not None


def predict_one(conn, m: dict, mid_idx: dict, bios_atp: dict, bios_wta: dict,
                composite_cache: dict) -> dict | None:
    """Compute prediction inputs + run match_prob. Returns row dict for
    INSERT, or None if data is insufficient.
    """
    # Slot assignment by lower mid (matches backtest convention)
    p1, p2 = m["p1_mid"], m["p2_mid"]
    if not p1 or not p2:
        return None
    mid_a, mid_b = (p1, p2) if p1 < p2 else (p2, p1)

    meta_a = mid_idx.get(mid_a)
    meta_b = mid_idx.get(mid_b)
    if not meta_a or not meta_b:
        return None

    tour = m.get("tour") or meta_a["tour"]
    bios = bios_atp if tour == "atp" else bios_wta
    bA = bios.get(meta_a["bio_id"])
    bB = bios.get(meta_b["bio_id"])
    if not bA or not bB:
        return None

    surface = m.get("surface") or "H"
    if surface not in ("H", "C", "G"):
        surface = "H"

    asof = m.get("date") or date.today().isoformat()

    a_pts, a_ytd = synthetic_ranking(conn, mid_a, asof, window_months=12)
    b_pts, b_ytd = synthetic_ranking(conn, mid_b, asof, window_months=12)
    if (a_pts + a_ytd == 0) or (b_pts + b_ytd == 0):
        return None

    fA = pit_form(conn, mid_a, asof)
    fB = pit_form(conn, mid_b, asof)
    h_a, h_b = pit_h2h(conn, mid_a, mid_b, asof)
    cA = pit_composite(conn, mid_a, tour, asof, composite_cache, mid_idx)
    cB = pit_composite(conn, mid_b, tour, asof, composite_cache, mid_idx)

    pA = {"id": bA["id"], "pts": a_pts, "ytd": a_ytd,
          "surf": bA["surf"], "form": fA, "composite": cA, "h2h_wins": h_a}
    pB = {"id": bB["id"], "pts": b_pts, "ytd": b_ytd,
          "surf": bB["surf"], "form": fB, "composite": cB, "h2h_wins": h_b}

    result = match_prob(pA, pB, surface)

    return {
        "match_id":       str(m["match_id"]),
        "predicted_at":   datetime.now(timezone.utc).isoformat(),
        "model_version":  MODEL_VERSION,
        "tour":           tour,
        "surface":        surface,
        "date":           asof,
        "tournament_id":  m.get("tournament_id"),
        "p1_mid":         mid_a, "p2_mid": mid_b,
        "p1_name":        bA["name"], "p2_name": bB["name"],
        "p_pred":         result["prob"],
        "pts_p1":         a_pts, "ytd_p1": a_ytd,
        "pts_p2":         b_pts, "ytd_p2": b_ytd,
        "form_p1":        fA, "form_p2": fB,
        "h2h_p1":         h_a, "h2h_p2": h_b,
        "comp_p1":        cA, "comp_p2": cB,
        "signals_json":   json.dumps(result["signals"], separators=(",", ":")),
        "weights_json":   json.dumps(result["weights"], separators=(",", ":")),
    }


def insert_prediction(conn, row: dict) -> bool:
    """Insert one prediction. Returns True if inserted, False if duplicate
    (already logged for this match today)."""
    try:
        conn.execute("""
            INSERT INTO predictions (
                match_id, predicted_at, model_version,
                tour, surface, date, tournament_id,
                p1_mid, p2_mid, p1_name, p2_name,
                p_pred,
                pts_p1, ytd_p1, pts_p2, ytd_p2,
                form_p1, form_p2, h2h_p1, h2h_p2,
                comp_p1, comp_p2,
                signals_json, weights_json
            ) VALUES (
                :match_id, :predicted_at, :model_version,
                :tour, :surface, :date, :tournament_id,
                :p1_mid, :p2_mid, :p1_name, :p2_name,
                :p_pred,
                :pts_p1, :ytd_p1, :pts_p2, :ytd_p2,
                :form_p1, :form_p2, :h2h_p1, :h2h_p2,
                :comp_p1, :comp_p2,
                :signals_json, :weights_json
            )
        """, row)
        return True
    except sqlite3.IntegrityError:
        # Duplicate (match_id, predicted_at) — happens if log runs twice in
        # the same UTC second. Safe to ignore.
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--dry-run", action="store_true",
                   help="Preview row counts, don't insert")
    args = p.parse_args()

    init_db(verbose=False)
    conn = connect()
    bios_atp = parse_bios(ROOT / "data" / "players_atp.js")
    bios_wta = parse_bios(ROOT / "data" / "players_wta.js")
    mid_idx  = mid_to_bio_index(conn)

    candidates = matches_to_predict(conn)
    print(f"[log_predictions] {len(candidates)} candidate match(es) to predict")

    composite_cache: dict[tuple[str, str], dict] = {}
    n_pred = n_skip_dup = n_skip_data = 0
    for m in candidates:
        if already_predicted_today(conn, m["match_id"]):
            n_skip_dup += 1
            continue
        row = predict_one(conn, m, mid_idx, bios_atp, bios_wta, composite_cache)
        if not row:
            n_skip_data += 1
            continue
        if not args.dry_run:
            if insert_prediction(conn, row):
                n_pred += 1
        else:
            n_pred += 1

    if not args.dry_run:
        conn.commit()

    print(f"[log_predictions] {'would log' if args.dry_run else 'logged'} "
          f"{n_pred} prediction(s)")
    print(f"[log_predictions] skipped: dup-today={n_skip_dup}, no-data={n_skip_data}")
    print(f"[log_predictions] model_version={MODEL_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
