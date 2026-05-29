# CLAUDE.md — Tennis Dashboard (ATP / WTA 2026)

Personal-use ATP/WTA analytics dashboard. **Data accuracy is the primary constraint.** Never fabricate player stats, rankings, or results — source everything from the data files or label it explicitly as a placeholder.

---

## What this is

Single-file HTML dashboard (`wta_analytics.html`) deployed as a static site on Cloudflare Pages, gated by Cloudflare Zero Trust (email allowlist, ~10 family/friends). No server-side compute. No build step. Data is pre-materialized into `data/*.js` files and committed to the repo.

**Live URL:** `https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics`
**Repo:** **public** GitHub at `github.com/thekasser/tennis-wta-atp`. Cloudflare Workers builds + deploys on push to `main`.

> **Public-repo posture:** Code, pipeline, curated inputs (players, tournaments), and the snapshot summary are public — that's the "show the work" part. **Raw Matchstat API payloads are NOT public** — RapidAPI/Matchstat ToS prohibits redistributing raw API data. As of 2026-05-24, `data/tennis.db.gz` is gitignored and lives in a private Cloudflare R2 bucket; the cron pulls from R2 on each run via `scripts/restore_db.py`. The dashboard reads aggregated/derived blobs from D1's `materialized` table (composite z-scores, rankings, projections) — never raw match rows, never stat blocks — so the public `/api/*` endpoints are ToS-compatible. Secrets stay out of the repo: `MATCHSTAT_API_KEY`, `ADMIN_SYNC_TOKEN`, and the R2 token (`R2_ACCOUNT_ID` + `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY`) live only in (a) `.env` on Connor's Mac (gitignored), (b) GitHub Actions repo secrets, (c) `wrangler secret put` for the Worker where applicable. The D1 `database_id` in `wrangler.toml` is committed but is not a secret — it's useless without Cloudflare account auth, similar to a Postgres database name. The `/api/*` read endpoints are intentionally public (Cloudflare Access bypass policy on `/api/*`); the dashboard at `/wta_analytics*` stays gated by email allowlist for ~10 family/friends. `/api/admin/*` requires a bearer token.

---

## Architecture (Phase 2 — D1 + Workers, live)

```
                ┌─── (cron start)
                ▼
   R2 (private) ─▶ restore_db.py ─▶ SQLite (data/tennis.db) ─▶ sync_* + materialize.py ─▶ data/*.js
        ▲                                       │                                            │
        │                                       ▼                                            ▼
   snapshot_db.py ◀──────────────── (cron end) ──┘             upload_to_worker.py ──▶ /api/admin/sync ──▶ D1.materialized
                                                                                                              │
                                                                                                              ▼
                                                                Dashboard ◀── fetch('/api/*') ◀── Worker ◀── D1
```

**Source of truth:** local `data/tennis.db` (driven by Matchstat). It rehydrates from `data/tennis.db.gz` at the start of every CI cron (downloaded fresh from private R2 — the .gz is gitignored), then incrementally syncs from Matchstat, then materializes derived JSON, then pushes to D1 over HTTP, then re-uploads the new snapshot to R2.

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

**The DB (durable state, private R2 bucket + gitignored local cache):**
```
data/
├── tennis.db.gz            # gzipped SQLite dump — GITIGNORED (raw Matchstat
│                           # payloads inside). Lives in private R2 bucket
│                           # `tennis-snapshots`; downloaded by restore_db.py.
├── snapshot_summary.txt    # human-readable manifest (committed, PR-diffable)
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
│                          # bio'd-player matches inc. qualifying. Cron runs WITHOUT
│                          # --include-settled since 2026-05-23 — settlement-time prices
│                          # saturate at 0.99/0.01 and destroy Brier scoring (see gotchas).
│                          # consolidate_match_ids() runs at end of every sync, rewriting
│                          # stale fixture-id-keyed rows to canonical matches.id (fixtures
│                          # gets gc'd, severing joins otherwise).
├── log_predictions.py     # Run match_prob on every fixture + recent active-tournament
│                          # match; write to predictions table with model_version stamp.
│                          # MUST run before materialize so today's new predictions land
│                          # in data/predictions.js on the same cron. Zero API calls.
├── materialize.py         # Read DB → write all data/*.js (hash-based change detection).
│                          # Includes synthetic_ranking fallback for players missing from
│                          # the API race response. Joins kalshi_odds into upcoming + into
│                          # the predictions blob. Kalshi join filters is_pre_match=1.
│                          # Computes edge_a (model − Kalshi) per match + edge_calibration
│                          # buckets in the predictions blob (powers the Edge column +
│                          # calibration block in the dashboard).
├── backtest.py            # PiT backtest framework. Modes:
│                          #   (default)             reconstruct PiT from history
│                          #   --soft                today's bios on past (= hindsight)
│                          #   --prospective         score the predictions log
│                          #   --feature-attribution per-signal Brier + net contribution
│                          #   --sweep-sharpen "..."  one-pass tuning sweep
├── snapshot_db.py         # data/tennis.db → data/tennis.db.gz + UPLOAD to R2
├── restore_db.py          # DOWNLOAD from R2 → data/tennis.db.gz → data/tennis.db
├── r2.py                  # Thin boto3 wrapper for the R2 (S3-compat) snapshot store
├── audit_tournaments.py   # Find tournament_api_ids in matches that don't resolve to a
│                          # tournaments.js row. Cron runs continue-on-error after validate
│                          # for visibility. Use --min-matches to filter low-volume.
├── validate.py            # Pre-commit sanity gate. Row counts + recency + dup-mid +
│                          # resolution-rate + T6M tier-pass-through. New guards added
│                          # 2026-05-24: unmapped-tour-event detector (≥28 matches, 60d
│                          # window, name not Challenger/125/Cup), date-drift detector
│                          # (catalog start_date drifting >14d from actual match dates),
│                          # Kalshi Brier floor (<0.10 on n≥50 = post-settlement bug).
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

**Budget:** Matchstat Pro = $10/mo, 10k calls/mo cap. Cron runs **3×/day** (07:00 + 17:00 + 23:00 UTC = midnight + 10am + 4pm PDT). `sync_matches.py` decide_fetch heuristic: cold-start players get full backfill, players in date-windowed-active tournaments get current year, everyone else skipped. WTA endpoint silently throttles bursts → matchstat.py uses 0.75s `_min_interval` (vs 0.25s for ATP-only loads) + retries on `{"data": null}` empty responses. Steady-state: ~250-500 calls per cron during active week, ~5-15 quiet. Monthly target ~5-9k; 3×/day pushes us closer to the 10k cap during heavy active stretches.

**Kalshi is free, no API key.** sync_kalshi.py fills market-odds gaps where Matchstat is silent (qualifying mostly). Counts toward zero on the Matchstat budget.

**License hard stop:** `data/trapezoid_data.js` inherits CC BY-NC-SA 4.0 from Sackmann. Commercial use is prohibited and cannot be unlocked without replacing the entire match-level data layer. Don't propose monetization features without flagging this.

---

## Refresh pipeline

### Scheduled (auto)
`.github/workflows/refresh.yml` runs **3×/day** (07:00 + 17:00 + 23:00 UTC = midnight + 10am + 4pm PDT) via GitHub Actions. Each run, in order:
1. `pip install boto3` (~5s; only non-stdlib runtime dep, used by `scripts/r2.py`).
2. Restore `data/tennis.db` from R2 (`scripts/restore_db.py` downloads `tennis.db.gz` from the private bucket, then `sqlite3 .read`s it back).
3. Seed bios + tournaments (`seed_db.py`) — picks up any manual bio additions in players_*.js since the last snapshot.
4. Sync rankings (`sync_rankings.py`).
5. Sync matches (`sync_matches.py` with decide_fetch heuristic).
6. Sync fixtures (`sync_fixtures.py` — upcoming matches + dedup pass).
7. Sync Kalshi odds (`sync_kalshi.py` — free, no API budget). Pre-match snapshots only; `--include-settled` was dropped in the cron 2026-05-23 to stop polluting the table with saturated settlement prices.
8. Log predictions (`log_predictions.py` — must run BEFORE materialize so new predictions land in today's predictions.js).
9. Materialize all `data/*.js` from the DB.
10. Update the snapshot (`snapshot_db.py` writes the local .gz AND uploads to R2; local .gz is gitignored — R2 is the canonical store).
11. Validate row counts, recency, resolution-rate, T6M tier-pass-through, unmapped-tour-events, date-drift, Kalshi Brier floor.
12. Audit unresolved tournaments (informational, continue-on-error).
13. Push fresh blobs to D1 via `/api/admin/sync`.
14. Commit + push `data/snapshot_summary.txt` + any curated-input changes (tournaments.js / players_*.js). The .gz is gitignored — R2 has it.

Cloudflare Workers picks up on push and serves the static dashboard from `[assets]`.

### Manual refresh (Connor's Mac)
```bash
cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard"

# (Optional) restore latest snapshot from R2. Skip if your local data/tennis.db
# is already current. Requires R2_* env vars in .env (auto-loaded by r2.py).
python3 scripts/restore_db.py --force

# Sync rankings + matches (Matchstat budget)
python3 scripts/sync_rankings.py --tour both
python3 scripts/sync_matches.py  --tour both       # decide_fetch picks years per player

# Sync fixtures (upcoming matches; cheap, ~2-4 calls per active tournament)
python3 scripts/sync_fixtures.py --tour both

# Sync Kalshi (free, no auth required). Pre-match snapshots only — DO NOT pass
# --include-settled in routine runs; it pollutes the table with saturated
# settlement prices and breaks Kalshi Brier scoring.
python3 scripts/sync_kalshi.py

# Log prospective predictions BEFORE materialize so they land in today's predictions.js
python3 scripts/log_predictions.py

# Materialize all data/*.js (no API calls)
python3 scripts/materialize.py

# Snapshot DB + upload to R2 (canonical store)
python3 scripts/snapshot_db.py

# Pre-commit gate
python3 scripts/validate.py

# Commit + push → triggers Worker redeploy (HTML/assets) but NOT D1 refresh.
# D1 only updates when the cron runs upload_to_worker.py. To push fresh blobs
# to D1 manually: export ADMIN_SYNC_TOKEN=... ; python3 scripts/upload_to_worker.py
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

**Before modifying any pipeline script:** All data flows through `data/tennis.db`. The DB is the authority. `data/*.js` files are projections via `scripts/materialize.py` and get overwritten on every pipeline run — never edit them by hand. The snapshot (`tennis.db.gz`) is regenerated by `snapshot_db.py` and lives in private R2 — see `scripts/r2.py`.

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
- **Top-seed byes (96-draw Masters only — NOT Grand Slams):** Top-32 seeds in 96-draw M1000s/W1000s skip R1 → no R1 match, no R1 fixture, would disappear from the active draw. `_compute_active_tournaments` augments them at scheduled_stage if the tournament type is M1000/W1000 and draw≥96. **Grand Slams are 128-draw with NO byes — every player contests R1 — so GS is deliberately excluded.** Including GS (the pre-2026-05 behavior) falsely resurrected withdrawn top-32 seeds as alive for the whole event (Korda @ RG '26: 2-month injury gap, no RG match, shown alive at R128). Legit GS seeds are covered by the matches loop (once R1 plays) or fixture augmentation (R1 scheduled).
- **Late withdrawals at byes draws:** Bye augmentation flips to withdrawal detection once Third-round matches exist. A top-32 bio with no match record AND no fixture, when R2 is fully resolved, didn't play their R2 match → marked `{r:"WD", elim:true}` instead of falsely alive. Caught Anisimova + Mboko at Rome '26. Caveat: this can't distinguish "withdrew after entering" from "never entered the field"; for personal-use we treat both as WD.
- **Cascade-elim gap threshold:** A R1 winner with no R2 match in DB used to get cascade-marked eliminated as soon as ANY R2 match completed (1-stage gap). Now requires ≥2-stage gap before the cascade fires — handles 12h cron lag between rounds.
- **Fixture dups:** Matchstat issues different fixture IDs for the same matchup across pulls. `sync_fixtures.gc_dups()` dedupes by canonical `(tournament, date, sorted-mid-pair)` after every sync.
- **Kalshi date drift:** Kalshi event tickers use a YYMMMDD date that doesn't always match our match.date (timezone, listing-day vs play-day). `sync_kalshi` uses ±1-day fuzzy match on player IDs.
- **predictions.match_id uses matches.id when both exist:** If a fixture has migrated to matches (post-completion), `kalshi_odds.match_id` and `predictions.match_id` should both prefer matches.id so the JOIN works. sync_kalshi's `find_match_id` checks matches first. **`consolidate_match_ids()` in sync_kalshi.py runs at end of every sync** to rewrite stale fixture-id-keyed rows to canonical matches.id — fixtures gets gc'd, severing the join otherwise. One-shot retro pass on 2026-05-23 rewrote 691 rows.
- **Kalshi Brier requires pre-match snapshots ONLY.** materialize.py's Kalshi JOIN filters `is_pre_match = 1`. Without that filter, the latest snapshot per match is settlement-time (saturated 0.99/0.01) → Brier collapses near 0, looking like the model "beats the market" by miles. validate.py hard-fails if `brier_kalshi < 0.10` on n ≥ 50. Cron's `sync_kalshi.py` no longer passes `--include-settled` (use that flag only for one-off backtest backfills, then revert).
- **Edge framework:** `edge_a = p_pred − kalshi_p_a` per match, plus `edge_abs` buckets in `data/predictions.js → stats.edge_calibration`. Powers the Edge column in Recent Calls + Edge calibration block in the dashboard. Big edges (|edge| ≥ 0.10) should NOT calibrate higher than small ones — if they do, model is over-confident on the disagreements and needs sharpening pulled back.
- **Live Events filters are date-driven** as of 2026-05-24. The dashboard's `completed`/`upcoming` buckets compare `endDate < today` / `startDate > today` instead of reading the manual `complete:` / `active:` flags from tournaments.js (which were chronically stale — Madrid stayed `active:true` 3 weeks after its Final). The server's `activeTournaments[]` from `materialize._compute_active_tournaments` remains authoritative for "currently playing." Manual flags in tournaments.js are now cosmetic.
- **Trapezoid 2024 data is preserved from the existing file**, not in the DB. Sackmann CSVs were the source for 2024; the rebuild keeps those rows untouched.
- **`.env` is gitignored:** `MATCHSTAT_API_KEY`, `ADMIN_SYNC_TOKEN`, and the four R2 vars (`R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`) live only on Connor's Mac and in GitHub Actions secrets. Never commit any of them.
- **`data/tennis.db` AND `data/tennis.db.gz` are both gitignored** since 2026-05-24. The .gz contains raw Matchstat API payloads (matches.raw, stat_p1, stat_p2) that ToS doesn't permit redistributing publicly. Canonical store: private R2 bucket `tennis-snapshots`. Restore first to inspect: `python3 scripts/restore_db.py --force` (needs R2 env vars in .env). **Historical `tennis.db.gz` is still in git log** from pre-2026-05-24 commits — pending one-time `git filter-repo` scrub + force-push to main + all branches.
- **wta_analytics.html JS edits:** validate with `node --check` on the extracted main `<script>` before commit. Duplicate `const` declarations or other parse-time syntax errors blank the dashboard silently. See `~/.claude/projects/.../memory/workflow_nodecheck_html.md`.

---

## Response style

BLUF first. No fluff. If something is a hypothesis or estimate, label it explicitly. Cite the specific file/line when making claims about code behavior.
