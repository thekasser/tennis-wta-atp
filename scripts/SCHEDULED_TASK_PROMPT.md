# Updated `tennis-dashboard-update` task

The existing scheduled task in Cowork (cron `0 3 * * *`) was wired to the old
Chrome-scrape pipeline. Replace its **prompt** with the content below to use
the Matchstat API pipeline instead.

To update: open Cowork → Scheduled Tasks → `tennis-dashboard-update` → Edit
prompt. Paste the block between the markers below (do NOT include the markers
themselves).

Suggested **description** (one-liner):
> Daily: refresh ATP/WTA rankings via Matchstat API. Mondays: also refresh match-level stats + trapezoid metrics.

---PROMPT-START---

Refresh the tennis dashboard data. Repo: /Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboardd

DAILY (run every time this task fires):

1. Run the rankings refresh:
   ```
   cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboardd"
   python3 scripts/refresh_rankings_api.py --tour both
   ```
   This pulls T12M rankings + YTD race standings from the Matchstat API (RapidAPI), updates `data/season_atp.js` and `data/season_wta.js`, and maintains the rank-baseline files (`data/rank_baseline_*.json`) used for week-over-week rankMove tracking. Cost: ~4 API calls. Don't proceed if it errors.

WEEKLY (run only on Mondays — check that `date +%u` returns 1; otherwise skip steps 2 and 3):

2. Run the match-stats fetch:
   ```
   python3 scripts/fetch_match_stats_api.py --tour both --years 2025 2026
   ```
   Pulls per-match stats for every bio with a Matchstat ID, computes per-year + T12M + T6M trapezoid aggregates, and writes `data/recent_matches.js` (used by the form-bar hover and Live Events draw enrichment). Cost: ~400–600 API calls (most served from cache).

3. Merge aggregates into the trapezoid dataset:
   ```
   python3 scripts/write_trapezoid_from_json.py --years 2024 2025 2026
   ```
   This combines the per-year + rolling-window aggregate JSON files into `data/trapezoid_data.js`.

VERIFICATION:
- Each script prints row counts. If any step prints 0 rows or fails with an error, STOP and report the failure — do not commit broken data.
- Common failure: Cloudflare 1010 = User-Agent rejection (already mitigated in `scripts/api_client.py`). Other network errors usually mean RapidAPI quota exceeded.

NOTES:
- The dashboard renders directly from the `data/*.js` files via `wta_analytics.html`. No build step.
- Matchstat budget is 10K calls/month on the Pro plan. Daily rankings + weekly full-pipeline keeps usage under ~3k/month.
- Skip silently Sunday and Tuesday-Saturday for the weekly steps; those days only refresh rankings.

Report a one-line summary at the end: which steps ran, how many rows updated per tour, and any errors.

---PROMPT-END---

## Why daily/weekly split

Matchstat Pro tier caps at 10K API calls/month. Full pipeline costs ~500
calls; running daily would burn ~15K/mo. Daily rankings (~4 calls) + weekly
full pipeline (~500 calls) totals ~620/mo — comfortable margin.

Trade-off: trapezoid + form-bar tooltip data has up to 1-week staleness. For
active tournament tracking that's a real gap, but acceptable for a personal
dashboard. If you want fresher Live Events data during active tournaments,
either upgrade the API tier or add a separate "tournament-active" task that
runs the match-stats fetch only when something is in progress.
