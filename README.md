# Tennis Dashboard (ATP / WTA, 2026 season)

Personal-use analytics dashboard for tracking the men's and women's tours: live tournament status, T12M rankings + YTD race, the "Trapezoid of Excellence" metric explorer, and a matchup predictor.

Single-file HTML dashboard (`wta_analytics.html`) — opens from `file://` locally, deploys as a static site for family/friends.

## What it shows

Four tabs in `wta_analytics.html`:

- **Live Events** — active tournament with per-player status (alive / eliminated / withdrawn), guaranteed pts, projected pts (with hover breakdown), defending pts, net change.
- **Rankings (T12M)** — sortable rankings table with YTD race column, scatter chart, biggest movers, form bar + trend sparkline.
- **Trapezoid Metrics** — scatter explorer across 9 metrics (serve/return/total pts won, BP saved, tiebreak %, deciding-set %, aces/SvGm, match win, composite z-score). Filters by year (T6M / T12M / 2024 / 2025 / 2026), tour, surface, and min-matches. Period-delta mode for trend comparisons.
- **Matchup Predictor** — pairwise win-probability with surface bias, real H2H from match data, form ratio.

Click any player name to open a drill-down modal: bio, all metrics across periods + surfaces, last-15 matches, top H2H records.

## Data pipelines

### Rankings (daily)
Matchstat Tennis API via RapidAPI. `scripts/refresh_rankings_api.py` pulls T12M + race standings, computes weekly-baseline rankMove, auto-derives `activeTournaments` from `data/tournaments.js`.

### Match-level stats (weekly, Mondays)
Same API. `scripts/fetch_match_stats_api.py` pulls per-match stats for every bio with a Matchstat ID, aggregates per-year + T6M + T12M + per-surface, plus last-30 matches per bio + tournament history. `scripts/write_trapezoid_from_json.py` merges into `data/trapezoid_data.js`.

### H2H (manual / on demand)
`scripts/build_h2h.py` — pure-Python aggregation of cached past-matches files into `data/h2h.js`. No API calls.

### Schedule (Cowork)
Daily 10pm PT via `tennis-dashboard-update` scheduled task. Steps 2-3 (full pipeline) only fire on Mondays — keeps API usage to ~600/mo on the 10K Pro tier.

## Data files (auto-generated, in repo)

```
data/
├── tournaments.js          # 2026 calendar — flip active:true to surface in Live Events
├── players_atp.js          # 110 ATP bios (top-100 + recent top-50 absentees)
├── players_wta.js          # 111 WTA bios
├── season_atp.js           # rankings + activeTournaments (refresh_rankings_api.py)
├── season_wta.js           # "
├── rank_baseline_atp.json  # weekly snapshot for rankMove computation
├── rank_baseline_wta.json  # "
├── trapezoid_data.js       # all metrics × period × surface (write_trapezoid_from_json.py)
├── recent_matches.js       # last 30 matches per bio (fetch_match_stats_api.py)
├── tournament_history.js   # per-bio tournament results across years
└── h2h.js                  # head-to-head records (build_h2h.py)
```

## Manual refresh

```bash
# Daily — rankings only (~4 API calls)
python3 scripts/refresh_rankings_api.py --tour both

# Weekly — full pipeline (~600 API calls)
python3 scripts/fetch_match_stats_api.py --tour both --years 2025 2026
python3 scripts/build_h2h.py
python3 scripts/write_trapezoid_from_json.py --years 2024 2025 2026
```

## Hosting (Cloudflare Pages + Access)

This is a private dashboard for ~10 family/friends, gated by email-based auth. Free for our scale.

- **Repo:** private GitHub (this directory)
- **Deploy:** Cloudflare Pages auto-builds on `main` push (no build step — static site)
- **Auth:** Cloudflare Zero Trust → Access → Self-hosted application with allowlisted emails
- **Refresh flow:** scheduled task on Connor's Mac runs the pipeline, commits `data/*` changes, pushes to GitHub → CF Pages auto-deploys

The Matchstat API key (`.env`) lives **only on Connor's Mac** — never in the repo, never on Cloudflare. Static site only serves pre-aggregated data, never proxies API calls.

## Compliance

- **License:** CC BY-NC-SA 4.0 (inherited from Sackmann match-charting data). Personal use + family/friends sharing is explicitly permitted; commercial use is not.
- **Matchstat ToS:** Personal-use dashboard with attribution is within their website ToS scope. Footer credits Matchstat. Email confirmation from support@matchstat.com saved before broader rollout.
- **RapidAPI ToS:** Defers data-use rules to the API Provider (Matchstat); no direct restriction on this use case.
- **Attribution:** Footer in `wta_analytics.html` credits Matchstat + Sackmann. `data/sources.md` tracks every data source.

## Repo layout

```
.
├── wta_analytics.html              # the dashboard
├── data/                           # auto-generated, committed
├── scripts/
│   ├── api_client.py               # Matchstat API wrapper (cache TTLs, UA spoof)
│   ├── refresh_rankings_api.py     # daily rankings refresh
│   ├── fetch_match_stats_api.py    # weekly match-level fetch
│   ├── build_h2h.py                # H2H aggregator
│   ├── write_trapezoid_from_json.py # merge per-year aggregates
│   ├── recompute_trapezoid.py      # legacy Sackmann fallback
│   ├── link_bios_to_api.py         # bio ↔ Matchstat ID linker
│   ├── link_bios_to_sackmann.py    # bio ↔ Sackmann ID linker
│   ├── SCHEDULED_TASK_PROMPT.md    # paste-into-Cowork prompt
│   ├── SCRAPE_INSTRUCTIONS.md      # historical (pre-API) reference
│   └── cache/                      # gitignored — API responses + CSVs
├── CLAUDE.md                       # AI-assistant project notes
├── DATA_PIPELINE.md                # design history
├── CREDITS.md                      # attribution
└── LICENSE                         # CC BY-NC-SA 4.0
```

## Built with help from

Claude Code + Cowork mode (Anthropic) — extensive AI-assisted iteration. The dashboard layout is hand-curated; the pipelines and analytics were largely AI-generated under direction.
