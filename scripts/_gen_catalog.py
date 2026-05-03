#!/usr/bin/env python3
"""
_gen_catalog.py — One-off helper to generate `tournaments.js` entries for
unresolved tour-level events.

Tier per tour:
  - ATP entries: tier ∈ {GS, M1000, M500, M250, ATPFinals}
  - WTA entries: tier ∈ {GS, W1000, W500, W250, WTAFinals}

For shared events (Slams, IW, Miami, Madrid, Rome, Cincinnati), entries with
the same canonical id are MERGED so one row carries `apiId:{atp:..., wta:...}`.

Output goes to stdout. Hand-review then append to data/tournaments.js, then
delete this script (it's one-off).
"""
from __future__ import annotations
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect


# ─── Tier classification by tournament-name substring ───────────────────────
# (substring, atp_tier, wta_tier, draw, surf, short, slug)
# atp_tier or wta_tier = None means "not on this tour."
KNOWN_EVENTS = [
    # ── Grand Slams ──────────────────────────────────────────────────────────
    ("Australian Open",                  "GS",     "GS",     128, "H", "AO",          "ao"),
    ("French Open",                      "GS",     "GS",     128, "C", "RG",          "rg"),
    ("Roland Garros",                    "GS",     "GS",     128, "C", "RG",          "rg"),
    ("Wimbledon",                        "GS",     "GS",     128, "G", "Wimbledon",   "wimbledon"),
    ("U.S. Open",                        "GS",     "GS",     128, "H", "US Open",     "uso"),
    ("US Open",                          "GS",     "GS",     128, "H", "US Open",     "uso"),

    # ── M1000 / W1000 ────────────────────────────────────────────────────────
    ("BNP Paribas Open",                 "M1000",  "W1000",  96,  "H", "IW",          "iw"),
    ("Miami Open",                       "M1000",  "W1000",  96,  "H", "Miami",       "miami"),
    ("Mutua Madrid Open",                "M1000",  "W1000",  96,  "C", "Madrid",      "madrid"),
    ("Internazionali BNL d'Italia",      "M1000",  "W1000",  96,  "C", "Rome",        "rome"),
    ("Cincinnati Open",                  "M1000",  "W1000",  96,  "H", "Cincinnati",  "cincinnati"),
    ("Western & Southern Open",          "M1000",  "W1000",  96,  "H", "Cincinnati",  "cincinnati"),
    ("Monte-Carlo Rolex Masters",        "M1000",  None,     56,  "C", "Monte-Carlo", "montecarlo"),
    ("Rolex Paris Masters",              "M1000",  None,     56,  "H", "Paris",       "paris"),
    ("Shanghai Rolex Masters",           "M1000",  None,     96,  "H", "Shanghai",    "shanghai"),
    ("Omnium Banque Nationale",          None,     "W1000",  56,  "H", "Montreal",    "montreal"),
    ("National Bank Open",               "M1000",  "W1000",  56,  "H", "Toronto",     "toronto"),  # ATP=Toronto, WTA=Montreal
    ("China Open",                       "M1000",  "W1000",  64,  "H", "Beijing",     "beijing"),
    ("Wuhan Open",                       None,     "W1000",  56,  "H", "Wuhan",       "wuhan"),
    ("Dubai Duty Free Championships",    None,     "W1000",  56,  "H", "Dubai",       "dubai_w"),

    # ── M500 / W500 ──────────────────────────────────────────────────────────
    ("ABN AMRO Open",                    "M500",   None,     32,  "H", "Rotterdam",   "rdam"),
    ("Qatar TotalEnergies Open",         None,     "W500",   32,  "H", "Doha",        "doha_w"),
    ("Qatar ExxonMobil Open",            "M500",   None,     32,  "H", "Doha",        "doha_a"),
    ("Dubai Duty Free Tennis Championships", "M500", None,   32,  "H", "Dubai",       "dubai_a"),
    ("Mubadala Abu Dhabi Open",          None,     "W500",   32,  "H", "Abu Dhabi",   "abudhabi"),
    ("Mubadala Citi DC Open",            None,     "W500",   32,  "H", "Washington",  "washington_w"),
    ("Citi Open",                        "M500",   None,     32,  "H", "Washington",  "washington_a"),
    ("Brisbane International",           "M250",   "W500",   32,  "H", "Brisbane",    "brisbane"),
    ("Adelaide International",           "M250",   "W500",   32,  "H", "Adelaide",    "adelaide"),
    ("Porsche Tennis Grand Prix",        None,     "W500",   32,  "H", "Stuttgart",   "stuttgart_w"),
    ("Credit One Charleston Open",       None,     "W500",   48,  "C", "Charleston",  "charleston"),
    ("Rothesay International",           "M250",   "W500",   32,  "G", "Eastbourne",  "eastbourne"),
    ("Berlin Ladies Open",               None,     "W500",   32,  "G", "Berlin",      "berlin"),
    ("Bad Homburg Open",                 None,     "W500",   32,  "G", "Bad Homburg", "badhomburg"),
    ("LTA London Championships",         None,     "W500",   32,  "G", "London",      "london_w"),
    ("Toray Pan Pacific Open",           None,     "W500",   32,  "H", "Tokyo",       "tokyo_w"),
    ("Korea Open",                       None,     "W500",   32,  "H", "Seoul",       "seoul"),
    ("Cinch Championships",              "M500",   None,     32,  "G", "Queen's",     "queens"),
    ("cinch Championships",              "M500",   None,     32,  "G", "Queen's",     "queens"),
    ("Terra Wortmann Open",              "M500",   None,     32,  "G", "Halle",       "halle"),
    ("Hamburg Open",                     "M500",   None,     32,  "C", "Hamburg",     "hamburg"),
    ("Erste Bank Open",                  "M500",   None,     32,  "H", "Vienna",      "vienna"),
    ("Japan Open Tennis Championships",  "M500",   None,     32,  "H", "Tokyo",       "tokyo_a"),
    ("Abierto Mexicano Telcel",          "M500",   None,     32,  "H", "Acapulco",    "acapulco"),
    ("Barcelona Open Banc Sabadell",     "M500",   None,     48,  "C", "Barcelona",   "barcelona"),
    ("Swiss Indoors Basel",              "M500",   None,     32,  "H", "Basel",       "basel"),
    ("Upper Austria Ladies Linz",        None,     "W500",   32,  "C", "Linz",        "linz"),
    ("Ningbo Open",                      None,     "W500",   32,  "H", "Ningbo",      "ningbo"),

    # ── M250 / W250 ──────────────────────────────────────────────────────────
    ("ASB Classic",                      "M250",   "W250",   28,  "H", "Auckland",    "auckland"),
    ("Hobart International",             None,     "W250",   32,  "H", "Hobart",      "hobart"),
    ("Hong Kong Tennis Open",            "M250",   None,     28,  "H", "Hong Kong",   "hongkong_a"),
    ("Argentina Open",                   "M250",   None,     28,  "C", "Buenos Aires","buenosaires"),
    ("Dallas Open",                      "M250",   None,     28,  "H", "Dallas",      "dallas"),
    ("Delray Beach Open",                "M250",   None,     32,  "H", "Delray",      "delraybeach"),
    ("Houston Open",                     "M250",   None,     28,  "C", "Houston",     "houston"),
    ("Grand Prix Hassan II",             "M250",   None,     28,  "C", "Marrakech",   "marrakech"),
    ("BMW Open",                         "M250",   None,     28,  "C", "Munich",      "munich"),
    ("Boss Open",                        "M250",   None,     28,  "G", "Stuttgart",   "stuttgart_a"),
    ("Mallorca Championships",           "M250",   None,     28,  "G", "Mallorca",    "mallorca"),
    ("Nordea Open",                      "M250",   None,     28,  "C", "Båstad",      "bastad"),
    ("Palermo Open",                     None,     "W250",   32,  "C", "Palermo",     "palermo"),
    ("Internationaux de Strasbourg",     None,     "W250",   32,  "H", "Strasbourg",  "strasbourg"),
    ("Tennis in the Land",               None,     "W250",   32,  "H", "Cleveland",   "cleveland"),
    ("Merida Open Akron",                None,     "W250",   32,  "H", "Mérida",      "merida"),
    ("Transylvania Open",                None,     "W250",   32,  "C", "Cluj",        "cluj"),
    ("Rothesay Open",                    None,     "W250",   32,  "G", "Nottingham",  "nottingham"),
    ("Japan Open - Osaka",               None,     "W250",   32,  "H", "Osaka",       "osaka"),
    ("Abierto GNP Seguros",              None,     "W250",   32,  "H", "Monterrey",   "monterrey"),
    ("Winston-Salem Open",               "M250",   None,     48,  "H", "Winston-Salem","winstonsalem"),
    ("Next Gen ATP Finals",              "ATPFinals", None,  8,   "H", "NextGen",     "nextgen"),
]

EXISTING_IDS = {
    "ao26", "doha26", "dubai_w26", "rdam26", "dubai_a26", "buenosaires26",
    "iw26", "miami26", "charleston26", "houston26", "marrakech26", "madrid26",
    "rome26", "rg26", "eastbourne26", "queens26", "halle26", "wimbledon26",
    "hamburg26", "bastad26", "palermo26", "montreal26", "toronto26",
    "cincinnati26", "uso26", "beijing26", "shanghai26", "wuhan26",
    "wta_finals26", "atp_finals26",
}


def classify(name: str, tour: str):
    """Return (tier, draw, surf, short, slug, is_both) for a known event."""
    nl = name.lower()
    for sub, atp_tier, wta_tier, draw, surf, short, slug in KNOWN_EVENTS:
        if sub.lower() not in nl:
            continue
        is_both = (atp_tier is not None and wta_tier is not None)
        if tour == "atp" and atp_tier:
            return (atp_tier, draw, surf, short, slug, is_both)
        if tour == "wta" and wta_tier:
            return (wta_tier, draw, surf, short, slug, is_both)
    return None


def main() -> int:
    conn = connect(read_only=True)
    rows = list(conn.execute("""
        SELECT m.tour, m.tournament_api_id AS api_id,
               MAX(m.tournament_name) AS name,
               MIN(m.date) AS first_date, MAX(m.date) AS last_date,
               COUNT(*) AS match_count, MAX(m.surface) AS surf_sample
        FROM matches m
        LEFT JOIN tournaments t
          ON (m.tour='atp' AND t.api_id_atp = m.tournament_api_id)
          OR (m.tour='wta' AND t.api_id_wta = m.tournament_api_id)
        WHERE m.tournament_api_id IS NOT NULL
          AND t.id IS NULL
          AND m.tournament_name IS NOT NULL
        GROUP BY m.tour, m.tournament_api_id
        HAVING match_count >= 5
        ORDER BY match_count DESC
    """))

    # First pass: group ATP and WTA rows for the same logical event.
    # Key by (slug, year2). When we see both tours for the same key with the
    # SAME tier (e.g., GS, M1000=W1000), merge into one BOTH entry. When the
    # tiers differ (e.g., Brisbane = ATP M250 + WTA W500), emit TWO entries
    # with tour-suffixed slugs so each carries the correct `type`.
    raw_by_key: dict[tuple, list[dict]] = defaultdict(list)
    skipped = []
    for r in rows:
        c = classify(r["name"], r["tour"])
        if c is None:
            skipped.append(r)
            continue
        tier, draw, surf, short, slug, is_both = c
        try:
            year2 = r["first_date"][:4][2:]
            month = int(r["first_date"][5:7])
        except (ValueError, TypeError, IndexError):
            year2, month = "00", 0
        try:
            wk = _date.fromisoformat(r["first_date"]).isocalendar()[1]
        except (ValueError, TypeError):
            wk = 0
        raw_by_key[(slug, year2)].append({
            "tour": r["tour"], "tier": tier, "draw": draw, "surf": surf,
            "short": short, "slug": slug, "year2": year2, "month": month,
            "wk": wk, "name": r["name"], "first": r["first_date"],
            "last": r["last_date"], "api_id": r["api_id"],
            "match_count": r["match_count"],
        })

    # Tier-equivalence: M1000 ↔ W1000, M500 ↔ W500, M250 ↔ W250, GS ↔ GS.
    # Two tours share a tier iff their tier strings normalize to the same
    # numeric class. Use the M-flavor (or "GS") as the canonical key.
    def _tier_class(t: str) -> str:
        if t in ("GS", "ATPFinals", "WTAFinals"): return t
        if t.startswith("W"): return "M" + t[1:]   # W1000 → M1000
        return t

    by_id: dict[str, dict] = {}
    for (slug, year2), tour_rows in raw_by_key.items():
        # Bucket by tier-class so M1000+W1000 collapse. If both tours fall
        # into the same class → one BOTH entry. Otherwise → split.
        by_class = defaultdict(list)
        for tr in tour_rows:
            by_class[_tier_class(tr["tier"])].append(tr)

        merged_buckets = []
        for tcls, group in by_class.items():
            tours_in_group = {tr["tour"] for tr in group}
            if tours_in_group == {"atp", "wta"}:
                merged_buckets.append(("BOTH", tcls, group))
            else:
                for tr in group:
                    merged_buckets.append((tr["tour"].upper(), tr["tier"], [tr]))

        for bucket_tour, bucket_tier, group in merged_buckets:
            if bucket_tour == "BOTH":
                cid = f"{slug}{year2}"
            elif len(merged_buckets) > 1:
                # Split entries — disambiguate slug by tour.
                cid = f"{slug}_{bucket_tour[0].lower()}{year2}"
            else:
                cid = f"{slug}{year2}"
            if cid in EXISTING_IDS:
                for tr in group:
                    skipped.append({**tr, "_existing": cid})
                continue

            # Display tier: prefer the M-flavor for BOTH events (matches
            # existing tournaments.js convention; PTS table has M1000 keyed
            # to the BOTH variant).
            display_tier = bucket_tier
            if bucket_tour == "BOTH" and bucket_tier.startswith("W"):
                display_tier = "M" + bucket_tier[1:]
            elif bucket_tour == "BOTH" and bucket_tier == "GS":
                display_tier = "GS"

            primary = group[0]
            entry = {
                "id":          cid,
                "name":        primary["name"],
                "short":       primary["short"],
                "tour":        bucket_tour,
                "type":        display_tier,
                "surf":        primary["surf"],
                "draw":        primary["draw"],
                "wk":          primary["wk"],
                "month":       primary["month"],
                "startDate":   min(tr["first"] for tr in group),
                "endDate":     max(tr["last"] for tr in group),
                "active":      False,
                "complete":    True,
                "api_ids":     {tr["tour"]: tr["api_id"] for tr in group},
                "match_count": sum(tr["match_count"] for tr in group),
            }
            by_id[cid] = entry

    # Sort by date.
    entries = sorted(by_id.values(), key=lambda x: (x["startDate"], x["id"]))

    print(f"# Generated {len(entries)} unique entries from {len(rows)} unresolved tournament-tour pairs.")
    print(f"# Skipped {len(skipped)} (Challenger/W125/ITF or already-cataloged).\n")

    cur_year_month = (None, None)
    for e in entries:
        ym = (e["startDate"][:4], e["month"])
        if ym != cur_year_month:
            print(f'\n  // ── {ym[0]}-{e["month"]:02d} ─────────────────────────')
            cur_year_month = ym
        api_field_str = "{" + ", ".join(
            f'{k}:{v}' for k, v in sorted(e["api_ids"].items())
        ) + "}"
        active = "false"
        complete = "true"
        print(f'  {{ id:"{e["id"]}", name:"{e["name"]}", short:"{e["short"]}", '
              f'tour:"{e["tour"]}", type:"{e["type"]}", surf:"{e["surf"]}", '
              f'draw:{e["draw"]}, wk:{e["wk"]}, month:{e["month"]}, '
              f'startDate:"{e["startDate"]}", endDate:"{e["endDate"]}", '
              f'active:{active}, complete:{complete}, '
              f'apiId:{api_field_str} }},  // {e["match_count"]} matches')

    print(f"\n# === Skipped (no tier match) — {len(skipped)} ===")
    def _f(r, k):
        try: return r.get(k) if hasattr(r, "get") else r[k]
        except (KeyError, IndexError): return None
    no_match = [r for r in skipped if not _f(r, "_existing")]
    for r in no_match[:40]:
        nm = _f(r, "name") or "?"
        print(f"  # {(_f(r, 'tour') or '?').upper()} {_f(r, 'api_id'):<8} "
              f"{_f(r, 'match_count'):>4}m  {nm[:55]}")
    if len(no_match) > 40:
        print(f"  # ... and {len(no_match) - 40} more (Challenger/W125/ITF)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
