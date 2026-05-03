# CLAUDE.md — Tennis Dashboard (ATP / WTA 2026)

Personal-use ATP/WTA analytics dashboard. **Data accuracy is the primary constraint.** Never fabricate player stats, rankings, or results — source everything from the data files or label it explicitly as a placeholder.

---

## What this is

Single-file HTML dashboard (`wta_analytics.html`) deployed as a static site on Cloudflare Pages, gated by Cloudflare Zero Trust (email allowlist, ~10 family/friends). No server-side compute. No build step. Data is pre-materialized into `data/*.js` files and committed to the repo.

**Live URL:** `https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics`
**Repo:** private GitHub → auto-deploys to Cloudflare Pages on push to `main`

---

## Architecture (Phase 1 — SQLite-backed pipeline)

Python scripts → SQLite DB (`data/tennis.db`) → materializer → `data/*.js` → committed to git → Cloudflare Pages serves them → `wta_analytics.html` renders in the browser.

The DB is the **single source of truth**. The committed `data/tennis.db.gz` snapshot lets CI rehydrate the DB on each run. The `data/*.js` files are pure projections via `scripts/materialize.py` — never edit them by hand.

The API key (`.env`) lives only on Connor's Mac. Never in the repo. Never on Cloudflare.

> **Phase 2** (planned): promote SQLite → Cloudflare D1, port the materializer
> to Workers, dashboard fetches `/api/*` instead of static `data/*.js`. See
> `~/.claude/plans/the-match-history-for-async-pie.md`.

---

## The four tabs

| Tab | Data source | What it shows |
|-----|-------------|---------------|
| **Live Events** | `season_*.js` + `tournaments.js` | Active tournament per-player status: alive/eliminated/withdrawn, guaranteed pts, projected pts, defending pts, net change |
| **Rankings (T12M)** | `season_*.js` | Sortable T12M + YTD race standings, scatter chart, biggest movers, form bar + trend sparkline |
| **Trapezoid Metrics** | `trapezoid_data.js` | Scatter explorer across 9 metrics (serve %, return %, BP saved, tiebreak %, aces/SvGm, etc.) filterable by year/tour/surface/min-matches |
| **Matchup Predictor** | `h2h.js` + `season_*.js` | Pairwise win probability with surface bias, real H2H records, form ratio |

Click any player name → drill-down modal: bio, metrics by period/surface, last-15 matches, top H2H records.

---

## Data files

**Inputs (hand-curated, committed):**
```
data/
├── tournaments.js          # 2026 calendar — set active:true to surface in Live Events
├── players_atp.js          # ATP bios (top-100ish + recent top-50 absentees)
└── players_wta.js          # WTA bios
```

**The DB (durable state, committed as compressed snapshot):**
```
data/
├── tennis.db.gz            # gzipped SQLite dump — rehydrated by restore_db.py
├── snapshot_summary.txt    # human-readable manifest (PR-diffable)
└── tennis.db               # working DB — gitignored; built by restore_db.py
```

**Outputs (materialized from the DB, committed):**
```
data/
├── season_atp.js           # T12M rankings + YTD race + activeTournaments
├── season_wta.js
├── recent_matches.js       # last 30 matches per top-200 bio
├── tournament_history.js   # per-bio deepest round per (tournament, year)
├── h2h.js                  # head-to-head pair records
└── trapezoid_data.js       # metrics × period × surface (2024 Sackmann + 2025+ API)
```

**Never edit `data/*.js` by hand.** The materializer overwrites them on each pipeline run. To change a value, edit the DB or the input files (`tournaments.js` / `players_*.js`) and re-run the pipeline.

---

## Scripts

```
scripts/
├── db.py                  # SQLite connection + migration runner (entry point: `init`/`status`/`shell`)
├── matchstat.py           # Matchstat API client — throttle + 429 retry, no JSON file cache
├── seed_db.py             # Bootstrap players + tournaments from data/players_*.js + data/tournaments.js
├── sync_rankings.py       # Pull T12M + race rankings → rankings_snapshots
├── sync_matches.py        # Smart-fetch past-matches → matches (INSERT OR IGNORE; idempotent)
├── materialize.py         # Read DB → write all data/*.js (hash-based change detection)
├── snapshot_db.py         # data/tennis.db → data/tennis.db.gz (+ snapshot_summary.txt)
├── restore_db.py          # data/tennis.db.gz → data/tennis.db (CI rehydration)
├── validate.py            # Pre-commit sanity gate (row counts, recency, dup-mid checks)
├── link_bios_to_api.py    # One-time: match new bio names → Matchstat ids
├── link_bios_to_sackmann.py  # One-time: match new bio names → Sackmann ids (legacy 2024)
└── migrations/
    └── 001_initial.sql    # base schema. Add 002_*.sql to evolve.
```

---

## Data sources & licensing

| Data | Source | License |
|------|--------|---------|
| T12M/YTD rankings + match-level stats (2025+) | Matchstat Tennis API via RapidAPI (`jjrm365`) | Commercial; $10/mo Pro tier; 10k calls/month |
| Match-level stats (2024 only, frozen) | Jeff Sackmann tennis CSVs | CC BY-NC-SA 4.0 — personal use ✓, commercial ✗ |
| Tournament calendar | Manual entry | N/A |
| Player bios | Manual curation | N/A |

**Budget:** Matchstat Pro = $10/mo, 10k calls/mo cap. The new pipeline does **incremental sync only** — `sync_matches.py` skips players whose latest DB match is < 24h old AND who aren't in an active tournament. Steady-state: ~30–50 calls per cron run during active tournaments, ~5–15 calls between. With 6×/day cron, monthly usage ≈ 1.5–3k calls (well under 10k).

**License hard stop:** `data/trapezoid_data.js` inherits CC BY-NC-SA 4.0 from Sackmann. Commercial use is prohibited and cannot be unlocked without replacing the entire match-level data layer. Don't propose monetization features without flagging this.

---

## Refresh pipeline

### Scheduled (auto)
`.github/workflows/refresh.yml` runs **every 4 hours** (6×/day) via GitHub Actions. Each run:
1. Restores `data/tennis.db` from `data/tennis.db.gz`.
2. Syncs rankings + matches from Matchstat (incremental — skips quiet players).
3. Materializes all `data/*.js` files from the DB.
4. Updates the snapshot.
5. Validates row counts and recency.
6. Commits + pushes only if any data file actually changed.

Cloudflare Pages auto-deploys on push.

### Manual refresh (Connor's Mac)
```bash
cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard"

# (Optional) restore the latest committed snapshot. Skip if your local
# data/tennis.db is already current.
python3 scripts/restore_db.py --force

# Sync (~30–50 API calls during active tournaments, ~5–15 quiet)
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both --years 2025 2026

# Materialize all data/*.js (no API calls)
python3 scripts/materialize.py

# Update the committed DB snapshot
python3 scripts/snapshot_db.py

# Pre-commit gate
python3 scripts/validate.py

# Commit + push → triggers Cloudflare Pages auto-deploy
git add data/
git commit -m "chore: data refresh $(date +%Y-%m-%d)"
git push
```

### Bootstrap (first time, or after wiping `data/tennis.db`)
```bash
python3 scripts/db.py init        # apply migrations
python3 scripts/seed_db.py        # populate players + tournaments
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both --years 2025 2026
python3 scripts/materialize.py
python3 scripts/snapshot_db.py
```

---

## Making common changes

**Add a player:** Edit `data/players_atp.js` or `data/players_wta.js`. Assign a unique integer `id` (no conflicts within the tour). Then run `link_bios_to_api.py` to match the new name to a Matchstat `mid`. Re-run `seed_db.py` to push the bio into the DB. Do not hardcode players inside the HTML.

**Add a tournament:** Edit `data/tournaments.js` only — update both `TOURNAMENTS_DATA[]` and `PTS{}` lookup. Re-run `seed_db.py` to push it into the DB.

**Activate a live tournament:** Set `active: true` in `data/tournaments.js`, re-run `seed_db.py`. The pipeline auto-derives draw status from match data — no `patch_wta_active.py` step needed (that script was deleted in the SQLite rebuild).

**Modify dashboard UI:** Edit `wta_analytics.html` directly — it is the compiled output. `wta_analytics_dashboard.jsx` is a JSX reference source only; it is not used in deployment. The `enrichActiveTournaments()` function is now a no-op stub — server is authoritative for activeTournaments[].

**Before modifying any pipeline script:** All data flows through `data/tennis.db`. The DB is the authority. `data/*.js` files are projections via `scripts/materialize.py` and get overwritten on every pipeline run — never edit them by hand. The committed snapshot (`tennis.db.gz`) is regenerated by `snapshot_db.py`.

---

## Git workflow — Claude must follow this

**Claude must never run `git` commands from the sandbox.** The sandbox mounts the macOS filesystem via Linux; any git lock files it creates have macOS ownership and cannot be removed by the sandbox (`Operation not permitted`). Partial git operations from the sandbox leave permanent `HEAD.lock` / `index.lock` files that block all subsequent git use until Connor manually removes them.

**Correct pattern:**
1. Claude edits files using Read/Edit/Write tools only.
2. Claude tells Connor to commit and push from Mac Terminal:
   ```bash
   git add -A && git commit -m "message" && git push
   ```
3. If push fails with non-fast-forward (remote is ahead due to prior plumbing commits), Connor runs:
   ```bash
   git fetch && git reset --hard origin/main
   git add -A && git commit -m "message" && git push
   ```

**If lock files appear** (from a previous session where this rule wasn't followed):
```bash
rm -f "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard/.git/HEAD.lock"
rm -f "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard/.git/index.lock"
```

---

## Known gotchas

- **WTA T12M points are scaled ×100** in the API response — `sync_rankings.py` divides by 100 to match official WTA values. Race points are NOT scaled. Don't fix this in two places.
- **WTA active draw status not in Matchstat API:** the rebuild solved this by deriving status from match results in the DB rather than scraping. The dashboard is correct as soon as a match is in the DB. See `_compute_active_tournaments` in `scripts/materialize.py`.
- **`SvGms` field missing:** Per-match service-games count isn't always in the API payload. `_aggregate_year` in `materialize.py` falls back to estimating from `svpt / 6.5`. If `acesPerSvGm` looks wrong, check the raw stat blob in `matches.stat_p1` / `stat_p2`.
- **Cloudflare 1010 errors:** User-Agent rejection — mitigated in `matchstat.py` with a browser-like UA. If it recurs, update the UA there.
- **Rate limit (HTTP 429):** Pro plan caps ~5 req/s. `matchstat.py` throttles to ≥250ms between requests + retries 429s with `Retry-After` (or 2/4/8s backoff). If you see persistent 429s, raise `_min_interval` to 0.4s.
- **Trapezoid 2024 data is preserved from the existing file**, not in the DB. Sackmann CSVs were the source for 2024; the rebuild keeps those rows untouched. If 2024 data is ever needed in the DB, write a separate Sackmann importer.
- **`.env` is gitignored:** `MATCHSTAT_API_KEY` lives only on Connor's Mac and in GitHub Actions secrets. Never commit it.
- **`data/tennis.db` is gitignored** — only the `.gz` snapshot is committed. If you want to inspect data, restore first: `python3 scripts/restore_db.py --force`.

---

## Response style

BLUF first. No fluff. If something is a hypothesis or estimate, label it explicitly. Cite the specific file/line when making claims about code behavior.
