# trapezoid-refresh — Scheduled Task

Refresh `data/trapezoid_data.js` from Jeff Sackmann's match-level CSVs (github.com/JeffSackmann/tennis_atp & tennis_wta, CC BY-NC-SA 4.0).

The Cowork sandbox cannot reach `raw.githubusercontent.com` directly (egress allowlist), so we fetch the CSVs through the connected Chrome instance, save them to `scripts/cache/`, then run the local Python recompute.

## Cadence

Recommended: **once per week, Mondays 7am local**. Sackmann updates after each tournament finishes — usually within 5 days. A weekly refresh keeps trapezoid_data.js no more than ~7 days behind reality.

For mid-Slam refreshes, run on demand.

---

## STEP 1 — Determine which years to refresh

Default: 2024 (frozen baseline), 2025 (full season), and the current calendar year. Use `bash` to get today's year if needed.

```bash
date +%Y
```

---

## STEP 2 — Fetch CSVs through Chrome

For each year × tour combination, navigate Chrome to the raw CSV URL and save the response. Use `mcp__Claude_in_Chrome__navigate` then `mcp__Claude_in_Chrome__get_page_text`.

URLs (replace YEAR):
- ATP: `https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_YEAR.csv`
- WTA: `https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_YEAR.csv`

For each fetched URL:
1. `navigate` to it
2. Wait 2 seconds (`mcp__Claude_in_Chrome__computer` action `wait`)
3. `get_page_text` to capture the CSV body
4. Write the body to `scripts/cache/{tour}_matches_{YEAR}.csv` using the Write tool

Quick sanity check after each save: file should start with `tourney_id,tourney_name,...` header line and be at least a few hundred KB for a full season (smaller for in-progress current year).

---

## STEP 3 — Run the recompute

```bash
cd "/path/to/WTA Tennis Dashboardd"
mkdir -p scripts/cache
python3 scripts/recompute_trapezoid.py --years 2024 2025 2026
```

This reads the cached CSVs, computes per-player metrics for each year, and rewrites `data/trapezoid_data.js` with year-tagged rows. The dashboard's year selector picks them up automatically.

---

## STEP 4 — Verify

- `data/trapezoid_data.js` now has a `Last updated` line near today's date
- `TRAPEZOID_YEARS` const lists every requested year
- Open `trapezoid.html` and confirm the Year dropdown shows multiple years
- Switching between years should change the player list & their metrics

---

## STEP 5 — Cache hygiene

`scripts/cache/` is in `.gitignore` — CSVs are large (tens of MB combined for full seasons) and Sackmann's repo is the canonical source, so we never commit them.

Stale cache cleanup (only if size becomes a problem):
```bash
# Keep latest year only; delete older cached CSVs
find scripts/cache -name '*_matches_*.csv' -not -name "*$(date +%Y).csv" -mtime +30 -delete
```

---

## Failure modes & what to do

- **`get_page_text` returns empty / 404** — Sackmann hasn't uploaded that year yet (early January for the new year). Skip and retry next week.
- **CSV malformed / parse errors in Python** — Sackmann occasionally publishes mid-edit. Re-fetch in 24 hours.
- **Chrome MCP not connected** — abort the task, surface a note. Don't fall back to direct curl/web_fetch — they're blocked.
- **Sackmann license** — CC BY-NC-SA 4.0. We credit in `CREDITS.md` and `data/trapezoid_data.js` header, and the derived dashboard inherits NonCommercial + ShareAlike. Do not monetize this dashboard without first contacting Sackmann.

---

## Manual one-off run

If the user just wants a one-time refresh without setting up the schedule:
```bash
# (Run from a shell with internet access — your Mac, not the sandbox)
cd "/path/to/WTA Tennis Dashboardd"
mkdir -p scripts/cache
for year in 2024 2025 2026; do
  curl -sL "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_${year}.csv" -o "scripts/cache/atp_matches_${year}.csv"
  curl -sL "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_${year}.csv" -o "scripts/cache/wta_matches_${year}.csv"
done
python3 scripts/recompute_trapezoid.py --years 2024 2025 2026
```
