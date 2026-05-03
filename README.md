# Tennis Dashboard (ATP / WTA, 2026 season)

Personal-use analytics dashboard tracking the men's and women's tours: live tournament status, T12M rankings + YTD race, the "Trapezoid of Excellence" metric explorer, and a matchup predictor.

**Live:** https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics (Cloudflare Access — email allowlisted, ~10 family/friends).

## What it shows

Four tabs in `wta_analytics.html`:

- **Live Events** — active tournament with per-player status (alive / eliminated / withdrawn / champion), guaranteed pts, projected pts, defending pts, net change. Active-tournament status is server-derived from match data (no manual draw curation).
- **Rankings (T12M)** — sortable table with YTD race column, scatter chart, biggest movers, form bar + trend sparkline.
- **Trapezoid Metrics** — scatter explorer across 9 metrics (serve/return/total pts won, BP saved, tiebreak %, deciding-set %, aces per service game, match win, composite z-score). Filters by year (CURR / T3M / T6M / T12M / 2024-2026), tour, surface, draw, and min-matches. Per-window 25th-percentile default for min-matches. **Tier-aware** — composite z-scores are computed only over WTA/ATP main-draw tour-level matches; W125 / Challenger / qualifying rounds are excluded so a circuit-grinder doesn't outrank Sabalenka.
- **Matchup Predictor** — pairwise win probability with surface bias, real head-to-head from match data, form ratio.

Click any player name → drill-down modal: bio, all metrics across periods + surfaces, last-15 matches, top H2H records.

## Architecture

```
GitHub Actions cron (every 4h)
    │
    ├── restore_db.py     ← unzip data/tennis.db.gz
    ├── sync_rankings.py  ← Matchstat /ranking/singles + race
    ├── sync_matches.py   ← Matchstat /past-matches (incremental;
    │                       only fetches recently-active players)
    ├── materialize.py    ← SQLite → JSON blobs (data/*.js, local only)
    ├── snapshot_db.py    ← gzip → data/tennis.db.gz (committed)
    ├── validate.py       ← row-count + recency + tier-pass-through guards
    ├── upload_to_worker.py ← POST /api/admin/sync (bearer-auth'd)
    └── git commit data/tennis.db.gz + snapshot_summary.txt

Cloudflare Worker (tennis-wta-atp)
    │
    ├── /                       → static dashboard (Cloudflare Access gated)
    ├── /wta_analytics*         → static dashboard (gated)
    ├── /api/season/{atp,wta}   → JSON
    ├── /api/players/{atp,wta}  → JSON
    ├── /api/tournaments        → JSON
    ├── /api/recent-matches     → JSON
    ├── /api/h2h                → JSON
    ├── /api/tournament-history → JSON
    ├── /api/trapezoid          → JSON
    ├── /api/health             → liveness probe
    └── /api/admin/sync (POST)  → bearer-auth'd D1 writer

D1 (tennis)
    ├── players, tournaments, matches, rankings_snapshots,
    │   api_fetch_log  (raw match-level data, mirrors local SQLite)
    └── materialized   (chunked JSON blobs, what /api/* actually serves)
```

The dashboard fetches from `/api/*` on load (parallel via `Promise.all`); the Worker reads chunked JSON blobs from D1's `materialized` table and reassembles them. Cache: 60s browser / 5min CF edge.

## Repo

This is a **public** repo. All match data and pipeline code is open. Secrets live only in `.env` (gitignored), GitHub Actions repo secrets, and Workers Secrets — never committed. The D1 `database_id` in `wrangler.toml` is committed but is not a secret on its own (useless without Cloudflare account auth).

## Local setup

```bash
# Python (no virtualenv required; only stdlib + sqlite3)
python3 scripts/db.py init        # apply migrations to data/tennis.db
python3 scripts/restore_db.py     # rehydrate from data/tennis.db.gz snapshot

# Optional — to run the pipeline locally
echo 'MATCHSTAT_API_KEY=your_rapidapi_key' > .env
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both --years 2025 2026
python3 scripts/materialize.py
python3 scripts/snapshot_db.py
python3 scripts/validate.py

# Optional — push to D1 (requires ADMIN_SYNC_TOKEN)
export ADMIN_SYNC_TOKEN='...'
python3 scripts/upload_to_worker.py
```

## Worker setup

```bash
npm install -g wrangler
wrangler login
wrangler deploy             # from repo root
wrangler dev                # local dev against remote D1
```

Workers Secrets needed (set via `wrangler secret put`):
- `ADMIN_SYNC_TOKEN` — bearer for `POST /api/admin/sync`

## Repo layout

```
.
├── wta_analytics.html       # single-file dashboard (boots from /api/* on load)
├── wrangler.toml            # Worker config (D1 binding, ASSETS binding)
├── workers/
│   ├── src/index.ts         # Worker code (route dispatch, D1 reads, admin sync)
│   ├── schema.sql           # D1 schema (mirror of scripts/migrations/001_initial.sql)
│   ├── migrations_002.sql   # adds the chunked `materialized` table
│   ├── package.json         # Workers TypeScript deps
│   └── tsconfig.json
├── scripts/
│   ├── db.py                # SQLite connection + migration runner
│   ├── matchstat.py         # Matchstat API client (throttle + 429 retry)
│   ├── seed_db.py           # bootstrap players + tournaments from JS files
│   ├── sync_rankings.py     # pull T12M + race rankings → SQLite
│   ├── sync_matches.py      # pull past-matches → SQLite (smart incremental)
│   ├── materialize.py       # SQLite → all data/*.js (hash-based change detect)
│   ├── snapshot_db.py       # gzip → data/tennis.db.gz
│   ├── restore_db.py        # gunzip → data/tennis.db
│   ├── validate.py          # pre-commit data sanity guards
│   ├── upload_to_worker.py  # POST data/*.js blobs to /api/admin/sync
│   ├── push_to_d1.py        # generate D1 INSERT SQL (alternative to upload)
│   ├── export_for_d1.py     # generate D1 INSERT SQL for raw rows (initial seed)
│   ├── audit_tournaments.py # list unresolved tournaments for catalog updates
│   ├── link_bios_to_api.py     # match new player bios → Matchstat IDs
│   ├── link_bios_to_sackmann.py # match new player bios → Sackmann IDs
│   └── migrations/
│       ├── 001_initial.sql           # base schema
│       └── 002_add_materialized.sql  # D1 read cache table
├── data/
│   ├── tennis.db.gz         # committed SQLite snapshot (rehydrated by CI)
│   ├── snapshot_summary.txt # human-readable manifest
│   ├── players_atp.js       # curated bio list
│   ├── players_wta.js       # curated bio list
│   └── tournaments.js       # curated calendar
├── .github/workflows/refresh.yml  # GitHub Actions cron
├── CLAUDE.md                # project notes for AI-assisted work
└── CREDITS.md               # attribution
```

## Data licensing & sources

| Data | Source | License |
|------|--------|---------|
| Live rankings + match-level stats (2025+) | Matchstat Tennis API via RapidAPI (jjrm365) | Commercial; $10/mo Pro tier |
| Match-level stats (2024 only, frozen) | Jeff Sackmann's tennis_atp / tennis_wta CSVs | CC BY-NC-SA 4.0 |
| Tournament calendar | Manual entry | n/a |
| Player bios | Manual curation | n/a |

This dashboard inherits **CC BY-NC-SA 4.0** from the Sackmann data layer used for 2024. Personal-use and family/friends sharing is explicitly permitted; commercial use is not.

## Built with help from

Claude Code (Anthropic) — extensive AI-assisted iteration on the data pipelines, Worker code, and dashboard logic. The dashboard layout was hand-curated; the architecture and most code were AI-generated under direction.
