# Tennis Dashboard (ATP / WTA, 2026 season)

Personal-use analytics dashboard tracking the men's and women's tours: live tournament status, T12M rankings + YTD race, the "Trapezoid of Excellence" metric explorer, and a matchup predictor.

**Live:** https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics (Cloudflare Access — email allowlisted, ~10 family/friends).

## What it shows

Four tabs in `wta_analytics.html`:

- **Live Events** — active tournament with per-player status (alive / eliminated / withdrawn / champion), guaranteed pts, projected pts, defending pts, net change. Active-tournament status is server-derived from match data + fixtures (no manual draw curation). Sortable column headers.
- **Rankings (T12M)** — sortable table with YTD race column, scatter chart, biggest movers, form bar + trend sparkline.
- **Trapezoid Metrics** — scatter explorer across 11 metrics (serve/return/total pts won, BP saved/won, service-games-won, return-games-won, tiebreak %, deciding-set %, aces/svGm, match win, composite z-score). Filters by year (CURR / T3M / T6M / T12M / 2024-2026), tour, surface, draw, and min-matches. Per-window 25th-percentile default. **Tier-aware** — composite z-scores are computed only over main-draw tour-level matches; W125 / Challenger / qualifying are excluded.
- **Matchup Predictor** — pairwise win probability blending Elo (from T12M+YTD points), surface, recent form, composite z-score, and Bayesian-shrunk H2H. Below the predictor: Recent Calls (last 15 model picks vs outcomes), Model Track Record (Brier vs Kalshi vs sportsbook), Model-vs-Market table (prospective only), Upcoming Matches (with sortable Δ-vs-Kalshi column to surface biggest disagreements).

### Measurement infrastructure

The model carries a real measurement loop, not vibes:

- **Prospective prediction log** (`predictions` table) — every cron records pre-match predictions for upcoming fixtures and recent active-tournament matches. Once a match completes, the JOIN on `matches.winner_id` gives clean prospective Brier with no hindsight contamination.
- **Two market benchmarks** — Matchstat sportsbook odds (where available, mostly main-draw) + Kalshi prediction-market (free public API, ~100% coverage of bio'd-player matches including qualifying). Brier comparison surfaces "are we beating the market on the matches both have an opinion on."
- **PiT backtest framework** (`scripts/backtest.py`) — reconstructs predictions from historical match data using PiT-correct inputs (synthetic ranking from accumulated tournament results, last-N form filtered by date, weekly-cached composite cohort). Modes: default PiT, `--soft` (today's bios for hindsight comparison), `--prospective` (read the live log), `--feature-attribution` (per-signal Brier + net helpful/harmful contribution), `--sweep-sharpen` (one-pass tuning sweep).

Click any player name → drill-down modal: bio, all metrics across periods + surfaces, last-15 matches, top H2H records.

## Architecture

```
GitHub Actions cron (3×/day at 07:00 + 17:00 + 23:00 UTC)
    │
    ├── pip install boto3 ← only non-stdlib runtime dep
    ├── restore_db.py     ← R2 (private) → data/tennis.db.gz → data/tennis.db
    ├── seed_db.py        ← players_*.js + tournaments.js → players/tournaments tables
    │                       (idempotent — picks up new manual bios)
    ├── sync_rankings.py  ← Matchstat /ranking/singles + race
    ├── sync_matches.py   ← Matchstat /past-matches (decide_fetch heuristic;
    │                       cold-starts get full backfill, active-window players
    │                       get current year only, others skipped)
    ├── sync_fixtures.py  ← Matchstat /fixtures/tournament/{id} + dedup pass
    ├── sync_kalshi.py    ← Kalshi /events?series_ticker=KX{ATP|WTA}MATCH
    │                       (free, no auth; pre-match snapshots only;
    │                        consolidate_match_ids() rewrites stale fixture-id
    │                        rows to canonical matches.id at end of every sync)
    ├── log_predictions.py← run match_prob on fixtures + recent matches → predictions table
    │                       (must run BEFORE materialize so today's predictions land
    │                        in today's predictions.js)
    ├── materialize.py    ← SQLite → JSON blobs (data/*.js, local only).
    │                       Kalshi join filters is_pre_match=1; computes edge_a +
    │                       edge_calibration buckets in the predictions blob.
    ├── snapshot_db.py    ← gzip data/tennis.db → data/tennis.db.gz + UPLOAD to R2
    │                       (.gz is GITIGNORED — R2 is the canonical store)
    ├── validate.py       ← row-count, recency, tier-pass-through, resolution-rate,
    │                       unmapped-tour-event, date-drift, Kalshi-Brier-floor guards
    ├── audit_tournaments.py ← informational; surfaces unresolved api_ids
    ├── upload_to_worker.py ← POST /api/admin/sync (bearer-auth'd)
    └── git commit data/snapshot_summary.txt + any curated-input edits

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
    ├── /api/upcoming           → JSON (fixtures + Kalshi prices)
    ├── /api/predictions        → JSON (prospective log + Brier vs markets)
    ├── /api/health             → liveness probe
    └── /api/admin/sync (POST)  → bearer-auth'd D1 writer

D1 (tennis)
    ├── players, tournaments, matches, rankings_snapshots,
    │   fixtures, predictions, kalshi_odds, api_fetch_log
    │     (raw match + market data, mirrors local SQLite)
    └── materialized
          (chunked JSON blobs, what /api/* actually serves)
```

The dashboard fetches from `/api/*` on load (parallel via `Promise.all`); the Worker reads chunked JSON blobs from D1's `materialized` table and reassembles them. Cache: 60s browser / 5min CF edge.

## Repo

This is a **public** repo so the pipeline, model, and curated inputs can be shown — that's the "show the work" part. **Raw Matchstat API payloads are NOT public.** RapidAPI/Matchstat ToS prohibits redistributing raw API responses, so since 2026-05-24 the SQLite snapshot (`data/tennis.db.gz`, which contains `matches.raw` + per-player stat JSON for every match) is gitignored and lives in a private Cloudflare R2 bucket. The cron pulls it from R2 on every run via `scripts/restore_db.py`. The dashboard reads only aggregated/derived blobs from D1's `materialized` table (composite z-scores, rankings, projections) — never raw rows — so the public `/api/*` endpoints are ToS-compatible.

What's in the public repo:
- All code (scripts, Worker, dashboard HTML)
- Curated inputs (`data/tournaments.js`, `data/players_*.js`, rank baselines)
- `data/snapshot_summary.txt` — row counts + active tournaments, PR-diffable manifest

Secrets live only in `.env` (gitignored), GitHub Actions repo secrets, and Workers Secrets — never committed. That includes `MATCHSTAT_API_KEY`, `ADMIN_SYNC_TOKEN`, and the four R2 variables (`R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`). The D1 `database_id` in `wrangler.toml` is committed but is not a secret on its own (useless without Cloudflare account auth).

## Local setup

```bash
# Python deps: stdlib + sqlite3 + boto3 (R2 access for the snapshot)
pip3 install boto3

# Set up .env (gitignored). Copy from .env.example and fill in:
#   MATCHSTAT_API_KEY      (RapidAPI subscription key)
#   ADMIN_SYNC_TOKEN       (matches the Workers Secret, for D1 sync)
#   R2_ACCOUNT_ID          (32-hex from Cloudflare dashboard URL)
#   R2_BUCKET=tennis-snapshots
#   R2_ACCESS_KEY_ID       (from R2 Manage API Tokens)
#   R2_SECRET_ACCESS_KEY   (from R2 Manage API Tokens, shown once)
cp .env.example .env && $EDITOR .env

# Bootstrap the DB
python3 scripts/db.py init        # apply migrations to data/tennis.db
python3 scripts/restore_db.py     # download snapshot from R2 and rehydrate

# Optional — run the pipeline locally (re-uses your Matchstat budget)
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both --years 2025 2026
python3 scripts/materialize.py
python3 scripts/snapshot_db.py    # writes .gz AND uploads to R2
python3 scripts/validate.py

# Optional — push to D1 (requires ADMIN_SYNC_TOKEN)
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
│   ├── sync_fixtures.py     # pull upcoming-match schedule + dedup pass
│   ├── sync_kalshi.py       # pull pre-match Kalshi prices + consolidate_match_ids()
│   ├── sync_catalog.py      # auto-discover Matchstat tournament IDs → apiId fields
│   ├── log_predictions.py   # log prospective predictions → predictions table
│   ├── materialize.py       # SQLite → all data/*.js (hash-based change detect)
│   ├── backtest.py          # PiT backtest framework (default / soft / prospective /
│   │                        # feature-attribution / sweep-sharpen modes)
│   ├── snapshot_db.py       # gzip → data/tennis.db.gz + upload to R2 (canonical)
│   ├── restore_db.py        # download from R2 → gunzip → data/tennis.db
│   ├── r2.py                # boto3 wrapper for the R2 (S3-compat) snapshot store
│   ├── validate.py          # pre-commit data sanity guards (incl. unmapped-tour-event,
│   │                        # date-drift, Kalshi-Brier-floor detectors)
│   ├── audit_tournaments.py # list unresolved tournament_api_ids for catalog updates
│   ├── upload_to_worker.py  # POST data/*.js blobs to /api/admin/sync
│   ├── push_to_d1.py        # generate D1 INSERT SQL (alternative to upload)
│   ├── export_for_d1.py     # generate D1 INSERT SQL for raw rows (initial seed)
│   ├── link_bios_to_api.py      # match new player bios → Matchstat IDs
│   ├── link_bios_to_sackmann.py # match new player bios → Sackmann IDs
│   └── migrations/
│       ├── 001_initial.sql           # base schema
│       ├── 002_add_materialized.sql  # D1 read cache table
│       ├── 003_add_fixtures.sql      # upcoming match schedule
│       ├── 004_add_predictions.sql   # prospective prediction log
│       └── 005_add_kalshi_odds.sql   # Kalshi market prices
├── data/
│   ├── tennis.db.gz         # GITIGNORED — private (raw Matchstat payloads). Canonical
│   │                        # store is private R2 bucket `tennis-snapshots`.
│   ├── snapshot_summary.txt # committed; PR-diffable row counts + active tournaments
│   ├── players_atp.js       # curated bio list
│   ├── players_wta.js       # curated bio list
│   └── tournaments.js       # curated calendar
├── .env.example             # template for required environment variables
├── .github/workflows/refresh.yml  # GitHub Actions cron (3×/day)
├── CLAUDE.md                # project notes for AI-assisted work
└── CREDITS.md               # attribution
```

## Data licensing & sources

| Data | Source | License | Public? |
|------|--------|---------|---------|
| Live rankings + match-level stats (2025+) | Matchstat Tennis API via RapidAPI (jjrm365) | Commercial; $10/mo Pro tier, redistribution prohibited | **No** — raw payloads kept in private R2 |
| Match-level stats (2024 only, frozen) | Jeff Sackmann's tennis_atp / tennis_wta CSVs | CC BY-NC-SA 4.0 | Yes (CC license permits) |
| Kalshi market prices | api.elections.kalshi.com (free, no auth) | Public API | Yes |
| Tournament calendar | Manual entry | n/a | Yes |
| Player bios | Manual curation | n/a | Yes |

The Matchstat ToS prohibits redistributing raw API responses publicly. The dashboard exposes only aggregated/derived metrics (composite z-scores, rankings, projections) via D1's `materialized` table — the public `/api/*` endpoints never serve raw match rows. Raw payloads live in a private Cloudflare R2 bucket; only the cron pipeline has read access.

This dashboard inherits **CC BY-NC-SA 4.0** from the Sackmann data layer used for 2024. Personal-use and family/friends sharing is explicitly permitted; commercial use is not.

## Built with help from

Claude Code (Anthropic) — extensive AI-assisted iteration on the data pipelines, Worker code, and dashboard logic. The dashboard layout was hand-curated; the architecture and most code were AI-generated under direction.
