# CLAUDE.md — Tennis Dashboard (ATP / WTA 2026)

Personal-use ATP/WTA analytics dashboard. **Data accuracy is the primary constraint.** Never fabricate player stats, rankings, or results — source everything from the data files or label it explicitly as a placeholder.

---

## What this is

Single-file HTML dashboard (`wta_analytics.html`) deployed as a static site on Cloudflare Pages, gated by Cloudflare Zero Trust (email allowlist, ~10 family/friends). No server-side compute. No build step. Data is pre-materialized into `data/*.js` files and committed to the repo.

**Live URL:** `https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics`
**Repo:** **public** GitHub at `github.com/thekasser/tennis-wta-atp`. Cloudflare Workers builds + deploys on push to `main`.

> **Public-repo posture:** All match/ranking data and pipeline code is public — fine, it's all derived from a paid Matchstat API + Sackmann's CC-licensed CSVs. Secrets stay out of the repo: `MATCHSTAT_API_KEY` and `ADMIN_SYNC_TOKEN` live only in (a) `.env` on Connor's Mac (gitignored), (b) GitHub Actions repo secrets, (c) `wrangler secret put` for the Worker. The D1 `database_id` in `wrangler.toml` is committed but is not a secret — it's useless without Cloudflare account auth, similar to a Postgres database name. The `/api/*` read endpoints are intentionally public (Cloudflare Access bypass policy on `/api/*`); the dashboard at `/wta_analytics*` stays gated by email allowlist for ~10 family/friends. `/api/admin/*` requires a bearer token.

---

## Architecture (Phase 2 — D1 + Workers, live)

```
Python pipeline (cron) ───▶ SQLite (data/tennis.db) ───▶ materialize.py ───▶ data/*.js
       │                                                                       │
       │                                                                       ▼
       └─────────▶ scripts/upload_to_worker.py ──▶ POST /api/admin/sync ──▶ D1.materialized
                                                          │
                                                          ▼
                  Dashboard ◀── fetch('/api/*') ◀── Worker (workers/src/index.ts) ◀── D1
```

**Source of truth:** local `data/tennis.db` (driven by Matchstat). It rehydrates from `data/tennis.db.gz` at the start of every CI cron, then incrementally syncs from Matchstat, then materializes derived JSON, then pushes to D1 over HTTP.

**Serving:** Cloudflare Worker (`tennis-wta-atp`) serves the static dashboard from `[assets]` AND `/api/*` JSON endpoints backed by D1's `materialized` table (chunked because D1 caps statements at ~100KB; reassembled on read). Edge cache: 60s browser / 5min CF.

**Auth:** Cloudflare Access gates `/wta_analytics*` (email allowlist, ~10 family/friends). A Bypass policy on `/api/*` lets the dashboard's own fetches through unauthenticated. `POST /api/admin/sync` requires a `Bearer ADMIN_SYNC_TOKEN` (Workers Secret matches a GH Actions secret of the same name).

**API endpoints** (open):
- `GET /api/season/:tour` (atp|wta)
- `GET /api/players/:tour` · `GET /api/tournaments`
- `GET /api/recent-matches` · `GET /api/h2h`
- `GET /api/tournament-history` · `GET /api/trapezoid`
- `GET /api/health` — quick liveness probe

**API endpoints** (auth-gated):
- `POST /api/admin/sync` — body `{blobs: {name: jsonString, …}}`, replaces D1's `materialized` table.

**Local development:** `python3 scripts/db.py status` to inspect the local SQLite. `wrangler dev` to run the Worker locally against remote D1. `python3 scripts/upload_to_worker.py --dry-run` to preview a sync without sending. Don't edit `data/*.js` by hand — they're materializer output, gitignored as of phase 2 (D1 is canonical).

The Matchstat API key (`.env`) lives only on Connor's Mac and as a GitHub Actions secret. Never in the repo. Never on Cloudflare.

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
├── season_atp.js           # T12M rankings + YTD race + activeTournaments (full UTC ISO lastUpdated)
├── season_wta.js
├── recent_matches.js       # last 30 matches per top-200 bio
├── tournament_history.js   # per-bio deepest round per (tournament, year)
├── h2h.js                  # head-to-head pair records
├── trapezoid_data.js       # metrics × period × surface (2024 Sackmann + 2025+ API)
├── upcoming_matches.js     # scheduled fixtures + Kalshi prices joined per match
└── predictions.js          # prospective prediction log + Brier vs Kalshi/sportsbook
```

**Never edit `data/*.js` by hand.** The materializer overwrites them on each pipeline run. To change a value, edit the DB or the input files (`tournaments.js` / `players_*.js`) and re-run the pipeline.

---

## Scripts

```
scripts/
├── db.py                  # SQLite connection + migration runner (init/status/shell)
├── matchstat.py           # Matchstat API client — throttle (0.75s WTA-safe), 429 retry,
│                          # silent-throttle empty-response retry
├── seed_db.py             # Bootstrap/refresh players + tournaments from data/players_*.js
│                          # + data/tournaments.js. Idempotent. CI runs this every cron so
│                          # newly-added bios get into the DB without manual reseed.
├── sync_rankings.py       # Pull T12M + race rankings → rankings_snapshots
├── sync_matches.py        # Smart-fetch past-matches → matches (decide_fetch heuristic:
│                          # cold-start → both years; in active-window tournament → current
│                          # year; otherwise skip).
├── sync_fixtures.py       # Pull upcoming-match schedule via getTournamentFixtures.
│                          # Includes gc_dups() that dedupes by canonical (tournament,
│                          # date, sorted-mid-pair) so the matchup predictor doesn't
│                          # render the same match twice.
├── sync_kalshi.py         # Pull Kalshi prediction-market prices (free, no auth).
│                          # Second market benchmark alongside sportsbook odds; ~100% on
│                          # bio'd-player matches inc. qualifying. --include-settled grabs
│                          # post-match for backtest backfill.
├── log_predictions.py     # Run match_prob on every fixture + recent active-tournament
│                          # match; write to predictions table with model_version stamp.
│                          # MUST run before materialize so today's new predictions land
│                          # in data/predictions.js on the same cron. Zero API calls.
├── materialize.py         # Read DB → write all data/*.js (hash-based change detection).
│                          # Includes synthetic_ranking fallback for players missing from
│                          # the API race response. Joins kalshi_odds into upcoming + into
│                          # the predictions blob.
├── backtest.py            # PiT backtest framework. Modes:
│                          #   (default)             reconstruct PiT from history
│                          #   --soft                today's bios on past (= hindsight)
│                          #   --prospective         score the predictions log
│                          #   --feature-attribution per-signal Brier + net contribution
│                          #   --sweep-sharpen "..."  one-pass tuning sweep
├── snapshot_db.py         # data/tennis.db → data/tennis.db.gz (+ snapshot_summary.txt)
├── restore_db.py          # data/tennis.db.gz → data/tennis.db (CI rehydration)
├── validate.py            # Pre-commit sanity gate (row counts, recency, dup-mid checks)
├── link_bios_to_api.py    # One-time: match new bio names → Matchstat ids
├── link_bios_to_sackmann.py  # One-time: match new bio names → Sackmann ids (2024)
└── migrations/            # 001 initial · 002 materialized · 003 fixtures
                           # · 004 predictions · 005 kalshi_odds
```

**Model state (current):**
- `wta_analytics.html` matchProbBreakdown: SHARPEN=1.25, bare-case Elo=0.35
- Composite metrics: `[totalPtsWonPct, serviceGamesWonPct, returnGamesWonPct,
  tbWinPct, decSetWinPct, matchWinPct]` — games-based, swapped 2026-05-05.
  COMPOSITE_METRICS in scripts/backtest.py and the `COMP` array in
  wta_analytics.html MUST stay in sync.
- H2H Bayesian shrinkage: `(aW + 0.5·5) / (total + 5)`. PRIOR_N=5 means
  ~5 prior meetings before we trust H2H 50%. 1-0 record gives 0.583, not 1.0.
- Model version stamp on prospective predictions:
  `sharpen=1.25/bare_elo=0.35/comp=games-v1/h2h=shrunk5`. Bump suffix on
  any model logic change so the predictions table stays segmentable.

---

## Data sources & licensing

| Data | Source | License |
|------|--------|---------|
| T12M/YTD rankings + match-level stats (2025+) | Matchstat Tennis API via RapidAPI (`jjrm365`) | Commercial; $10/mo Pro tier; 10k calls/month |
| Match-level stats (2024 only, frozen) | Jeff Sackmann tennis CSVs | CC BY-NC-SA 4.0 — personal use ✓, commercial ✗ |
| Tournament calendar | Manual entry | N/A |
| Player bios | Manual curation | N/A |

**Budget:** Matchstat Pro = $10/mo, 10k calls/mo cap. Cron runs **2×/day** (00:00 + 12:00 UTC). `sync_matches.py` decide_fetch heuristic: cold-start players get full backfill, players in date-windowed-active tournaments get current year, everyone else skipped. WTA endpoint silently throttles bursts → matchstat.py uses 0.75s `_min_interval` (vs 0.25s for ATP-only loads) + retries on `{"data": null}` empty responses. Steady-state: ~250-500 calls per cron during active week, ~5-15 quiet. Monthly: ~5-9k.

**Kalshi is free, no API key.** sync_kalshi.py fills market-odds gaps where Matchstat is silent (qualifying mostly). Counts toward zero on the Matchstat budget.

**License hard stop:** `data/trapezoid_data.js` inherits CC BY-NC-SA 4.0 from Sackmann. Commercial use is prohibited and cannot be unlocked without replacing the entire match-level data layer. Don't propose monetization features without flagging this.

---

## Refresh pipeline

### Scheduled (auto)
`.github/workflows/refresh.yml` runs **2×/day** (00:00 + 12:00 UTC = 5pm + 5am PDT) via GitHub Actions. Each run, in order:
1. Restore `data/tennis.db` from `data/tennis.db.gz`.
2. Seed bios + tournaments (`seed_db.py`) — picks up any manual bio additions in players_*.js since the last snapshot.
3. Sync rankings (`sync_rankings.py`).
4. Sync matches (`sync_matches.py` with decide_fetch heuristic).
5. Sync fixtures (`sync_fixtures.py` — upcoming matches + dedup pass).
6. Sync Kalshi odds (`sync_kalshi.py --include-settled` — free, no API budget).
7. Log predictions (`log_predictions.py` — must run BEFORE materialize so new predictions land in today's predictions.js).
8. Materialize all `data/*.js` from the DB.
9. Update the snapshot.
10. Validate row counts and recency.
11. Push fresh blobs to D1 via `/api/admin/sync`.
12. Commit + push if any committed file changed.

Cloudflare Workers picks up on push and serves the static dashboard from `[assets]`.

### Manual refresh (Connor's Mac)
```bash
cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard"

# (Optional) restore the latest committed snapshot. Skip if your local
# data/tennis.db is already current.
python3 scripts/restore_db.py --force

# Sync rankings + matches (Matchstat budget)
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both       # decide_fetch picks years per player

# Sync fixtures (upcoming matches; cheap, ~2-4 calls per active tournament)
python3 scripts/sync_fixtures.py --tour both

# Sync Kalshi (free, no auth required)
python3 scripts/sync_kalshi.py --include-settled

# Log prospective predictions BEFORE materialize so they land in today's predictions.js
python3 scripts/log_predictions.py

# Materialize all data/*.js (no API calls)
python3 scripts/materialize.py

# Update the committed DB snapshot
python3 scripts/snapshot_db.py

# Pre-commit gate
python3 scripts/validate.py

# Commit + push → triggers Cloudflare Pages auto-deploy
# (pull --rebase first to absorb any cron-pushed data refresh)
git pull --rebase
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

**The cron-collision problem.** GitHub Actions runs `.github/workflows/refresh.yml` every 4 hours and pushes data-refresh commits to `main`. This means a stale local clone (no pull in >4h) will fail any `git push` with `non-fast-forward`. This is structural, not occasional — assume it on every push.

**Correct pattern (every commit/push, no exceptions):**

Claude has just edited files via Read/Edit/Write. The working tree is dirty. Stage + commit FIRST, then rebase, then push:

```bash
git add <files> && git commit -m "message" && git pull --rebase && git push
```

This order is mandatory because of a subtle bug: if you put `git pull --rebase` first, it fails with "you have unstaged changes" — Claude's own edit blocks the rebase. Stage + commit first to clean the working tree, THEN rebase.

**Bulletproof variant (works regardless of staging order):**
```bash
git pull --rebase --autostash && git add <files> && git commit -m "message" && git push
```

`--autostash` auto-stashes any dirty working tree, rebases, and pops the stash. Works even if Claude forgets to tell you to stage first. Use this as the default — it's defensive against pattern errors.

**One-time setup** (run once, never think about it again):
```bash
git config --global pull.rebase true
git config --global rebase.autostash true
```

Now `git pull` always rebases-with-autostash by default. The full pattern collapses to `git pull && git push` and just works. **This is the right answer.** If these configs are set, the `--rebase --autostash` flags are redundant but harmless.

**Recovery patterns:**

- **"you have unstaged changes" during pull:** caused by ordering. Stage + commit Claude's edits first, then re-run pull.
  ```bash
  git add -A && git commit -m "message" && git pull && git push
  ```

- **"non-fast-forward" during push:** cron pushed while you were working. With `pull.rebase=true` set, just:
  ```bash
  git pull && git push
  ```

**Do NOT use `git fetch && git reset --hard origin/main` as a recovery.** That deletes any local commits the user has already made and forces them to redo the work. The original version of this doc recommended it; it was wrong. Always use rebase.

**If lock files appear** (from a previous session where Claude violated rule #1):
```bash
rm -f "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard/.git/HEAD.lock"
rm -f "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard/.git/index.lock"
```

---

## Known gotchas

- **WTA T12M points are scaled ×100** in the API response — `sync_rankings.py` divides by 100 to match official WTA values. Race points are NOT scaled. Don't fix this in two places.
- **WTA past-matches silent throttle:** Matchstat's WTA endpoint returns `200 OK` with `{"data": null}` (instead of 429) when bursts exceed its per-key limit. `matchstat.py` retries on empty responses + uses `_min_interval=0.75s` (vs the original 0.25s). 2026-05-06 incident: 774 calls all returned NULL before the fix.
- **`SvGms` field missing:** Per-match service-games count isn't always in the API payload. `_aggregate_year` in `materialize.py` falls back to estimating from `svpt / 6.5`.
- **Cloudflare 1010 errors:** User-Agent rejection — mitigated in `matchstat.py` with a browser-like UA. If it recurs, update the UA there.
- **WTA YTD missing from API race response** for ranks > 200: `materialize._compute_synthetic_ytd` falls back to summing in-year tournament results when `rankings_snapshots.ytd_pts IS NULL`. ~70 bio'd players currently rely on this.
- **Top-seed byes:** Top-32 seeds in 96-draw M1000s + 128-draw GS skip R1 → no R1 match, no R1 fixture, would disappear from the active draw. `_compute_active_tournaments` augments them at scheduled_stage if the tournament type is GS/M1000/W1000 and draw≥96.
- **Late withdrawals at byes draws:** Bye augmentation flips to withdrawal detection once Third-round matches exist. A top-32 bio with no match record AND no fixture, when R2 is fully resolved, didn't play their R2 match → marked `{r:"WD", elim:true}` instead of falsely alive. Caught Anisimova + Mboko at Rome '26. Caveat: this can't distinguish "withdrew after entering" from "never entered the field"; for personal-use we treat both as WD.
- **Cascade-elim gap threshold:** A R1 winner with no R2 match in DB used to get cascade-marked eliminated as soon as ANY R2 match completed (1-stage gap). Now requires ≥2-stage gap before the cascade fires — handles 12h cron lag between rounds.
- **Fixture dups:** Matchstat issues different fixture IDs for the same matchup across pulls. `sync_fixtures.gc_dups()` dedupes by canonical `(tournament, date, sorted-mid-pair)` after every sync.
- **Kalshi date drift:** Kalshi event tickers use a YYMMMDD date that doesn't always match our match.date (timezone, listing-day vs play-day). `sync_kalshi` uses ±1-day fuzzy match on player IDs.
- **predictions.match_id uses matches.id when both exist:** If a fixture has migrated to matches (post-completion), `kalshi_odds.match_id` and `predictions.match_id` should both prefer matches.id so the JOIN works. sync_kalshi's `find_match_id` checks matches first.
- **Trapezoid 2024 data is preserved from the existing file**, not in the DB. Sackmann CSVs were the source for 2024; the rebuild keeps those rows untouched.
- **`.env` is gitignored:** `MATCHSTAT_API_KEY` and `ADMIN_SYNC_TOKEN` live only on Connor's Mac and in GitHub Actions secrets. Never commit either.
- **`data/tennis.db` is gitignored** — only the `.gz` snapshot is committed. Restore first to inspect: `python3 scripts/restore_db.py --force`.
- **wta_analytics.html JS edits:** validate with `node --check` on the extracted main `<script>` before commit. Duplicate `const` declarations or other parse-time syntax errors blank the dashboard silently. See `~/.claude/projects/.../memory/workflow_nodecheck_html.md`.

---

## Response style

BLUF first. No fluff. If something is a hypothesis or estimate, label it explicitly. Cite the specific file/line when making claims about code behavior.
