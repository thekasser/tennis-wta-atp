# CLAUDE.md — Tennis Dashboard (ATP / WTA 2026)

Personal-use ATP/WTA analytics dashboard. **Data accuracy is the primary constraint.** Never fabricate player stats, rankings, or results — source everything from the data files or label it explicitly as a placeholder.

---

## What this is

Single-file HTML dashboard (`wta_analytics.html`) deployed as a static site on Cloudflare Pages, gated by Cloudflare Zero Trust (email allowlist, ~10 family/friends). No server-side compute. No build step. Data is pre-materialized into `data/*.js` files and committed to the repo.

**Live URL:** `https://tennis-wta-atp.kasserconnor.workers.dev/wta_analytics`
**Repo:** private GitHub → auto-deploys to Cloudflare Pages on push to `main`

---

## Architecture

Python scripts → JSON cache → `data/*.js` → committed to git → Cloudflare Pages serves them → `wta_analytics.html` renders in the browser.

The API key (`.env`) lives only on Connor's Mac. Never in the repo. Never on Cloudflare.

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

## Data files (auto-generated, committed to repo)

```
data/
├── tournaments.js          # 2026 calendar — set active:true to surface in Live Events
├── players_atp.js          # 110 ATP bios (top-100 + recent top-50 absentees)
├── players_wta.js          # 111 WTA bios
├── season_atp.js           # T12M rankings + YTD race + activeTournaments
├── season_wta.js           # same
├── rank_baseline_atp.json  # weekly snapshot for rankMove computation
├── rank_baseline_wta.json  # same
├── trapezoid_data.js       # all metrics × period × surface (2024 Sackmann + 2025+ API)
├── recent_matches.js       # last 30 matches per bio (form-bar hover + Live Events draw)
├── tournament_history.js   # per-bio tournament results across years
└── h2h.js                  # head-to-head records
```

---

## Scripts

```
scripts/
├── api_client.py                # Matchstat API wrapper — auth, cache, rate-limit retry
├── refresh_rankings_api.py      # Rankings refresh (STEP 1 of pipeline)
├── fetch_match_stats_api.py     # Match-level stats fetch (STEP 2 of pipeline)
├── write_trapezoid_from_json.py # Merge aggregates → trapezoid_data.js (STEP 3)
├── build_h2h.py                 # H2H aggregator — no API calls, reads cache only
├── patch_wta_active.py          # Manual: inject WTA active-tournament status
├── link_bios_to_api.py          # One-time: match bio names → Matchstat IDs
├── link_bios_to_sackmann.py     # One-time: match bio names → Sackmann IDs
├── recompute_trapezoid.py       # DEPRECATED Sackmann CSV fallback — do not run
└── update_season.py             # DEPRECATED Chrome scraper — do not run
```

**Never run** `recompute_trapezoid.py` or `update_season.py` — both replaced by the API pipeline.

---

## Data sources & licensing

| Data | Source | License |
|------|--------|---------|
| T12M/YTD rankings + match-level stats (2025+) | Matchstat Tennis API via RapidAPI (`jjrm365`) | Commercial; $10/mo Pro tier; 10k calls/month |
| Match-level stats (2024 only, frozen) | Jeff Sackmann tennis CSVs | CC BY-NC-SA 4.0 — personal use ✓, commercial ✗ |
| Tournament calendar | Manual entry | N/A |
| Player bios | Manual curation | N/A |

**Budget:** Matchstat Pro = $10/mo. `player/past-matches` has a 6-hour TTL cache — mid-week runs of step 2 cost ~10–50 API calls (only players who played in the last 6 hours need fresh fetches). Weekly full pipeline ≈ 500 API calls on a cold cache. Daily runs during active tournaments ≈ 50–100/day. Keep total under ~3k/month to stay well within the 10k cap.

**License hard stop:** `data/trapezoid_data.js` inherits CC BY-NC-SA 4.0 from Sackmann. Commercial use is prohibited and cannot be unlocked without replacing the entire match-level data layer. Don't propose monetization features without flagging this.

---

## Refresh pipeline

### Scheduled task (auto)
`tennis-dashboard-update` runs **every day at 10pm PST** via Cowork Scheduled Tasks. Runs all three steps + QA check + git commit + push → Cloudflare Pages auto-deploys. The 6-hour `past-matches` cache makes daily step 2 runs cheap (~10–50 calls on non-Monday runs).

### Manual refresh
```bash
cd "/Users/connorkasser/Documents/Claude/Projects/ATP/WTA Tennis Dashboard"

# Step 1 — Rankings (~4 API calls)
python3 scripts/refresh_rankings_api.py --tour both

# Step 2 — Match-level stats (cache-aware: ~10–50 API calls mid-week, ~500 cold)
python3 scripts/fetch_match_stats_api.py --tour both --years 2025 2026

# Step 3 — Merge trapezoid metrics (no API calls)
python3 scripts/write_trapezoid_from_json.py --years 2024 2025 2026

# H2H rebuild (no API calls, run after step 2)
python3 scripts/build_h2h.py

# QA — verify alive/eliminated counts look sane before committing
python3 -c "
import re
from pathlib import Path
ok = True
for tour in ['atp', 'wta']:
    c = Path(f'data/season_{tour}.js').read_text()
    m = re.search(r'activeTournaments:\s*\[([\s\S]*?)\],', c)
    if not m:
        print(f'{tour.upper()}: WARNING — no activeTournaments block found')
        ok = False
        continue
    block = m.group(0)
    alive = block.count('elim:false')
    elim  = block.count('elim:true')
    stage = re.search(r'stage:\s*\"([^\"]+)\"', block)
    stage = stage.group(1) if stage else '?'
    print(f'{tour.upper()}: stage={stage}, alive={alive}, eliminated={elim}')
    if alive == 0 and elim == 0:
        print(f'  WARNING — players block is empty, draw status will come from recent_matches only')
        ok = False
    if alive > 64:
        print(f'  WARNING — alive count suspiciously high ({alive}), check for stale elim:false entries')
        ok = False
print('QA passed' if ok else 'QA FAILED — review before committing')
"

# Commit + push → triggers Cloudflare Pages auto-deploy
git add data/
git commit -m "chore: data refresh $(date +%Y-%m-%d)"
git push
```

---

## Making common changes

**Add a player:** Edit `data/players_atp.js` or `data/players_wta.js`. Assign a unique integer ID (no conflicts). Then run `link_bios_to_api.py` to match the new name to a Matchstat ID. Do not hardcode players inside the HTML.

**Add a tournament:** Edit `data/tournaments.js` only — update both `TOURNAMENTS_DATA[]` and `PTS{}` lookup.

**Activate a live tournament:** Set `active: true` in `data/tournaments.js`. For WTA (API doesn't expose draw status), use `patch_wta_active.py` or set manually.

**Modify dashboard UI:** Edit `wta_analytics.html` directly — it is the compiled output. `wta_analytics_dashboard.jsx` is a JSX reference source only; it is not used in deployment.

**Before modifying any pipeline script:** Check whether data flows from `season_*.js` vs `players_*.js` vs `trapezoid_data.js`. Getting the source wrong produces stale or missing data. `refresh_rankings_api.py` preserves `results{}` history — don't overwrite it with a naive rewrite.

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

- **WTA rankings partial results:** API sometimes returns top-50 only when pagination doesn't advance. Fallback: `api.wtatennis.com` undocumented endpoint. See comments in `refresh_rankings_api.py`.
- **WTA active draw status:** Not in Matchstat API. Set manually in `tournaments.js` or use `patch_wta_active.py`.
- **`SvGms` field missing:** Per-match service games count isn't always in the API payload. If `acesPerSvGm` looks wrong, check what `fetch_match_stats_api.py` is actually receiving.
- **Cloudflare 1010 errors:** User-Agent rejection — already mitigated in `api_client.py`. If it recurs, update the UA string there.
- **Race tab heatmap sparse:** Requires per-tournament results data; fills in during active events.
- **`scripts/cache/` is gitignored:** Local only. Don't delete it — saves ~500 API calls on every weekly run.
- **`.env` is gitignored:** `MATCHSTAT_API_KEY` lives only on Connor's Mac. Never commit it.

---

## Response style

BLUF first. No fluff. If something is a hypothesis or estimate, label it explicitly. Cite the specific file/line when making claims about code behavior.
