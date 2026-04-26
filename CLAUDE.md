# CLAUDE.md — Tennis Dashboard

Personal-use ATP/WTA analytics dashboard. **Data accuracy is the primary constraint.** Never fabricate player stats, rankings, or results — source everything or say it's a placeholder.

---

## What this project is

Two browser-opened HTML dashboards plus automated data pipelines:

- `wta_analytics.html` — rankings (T12M + YTD), active draws, matchup predictor
- `trapezoid.html` — per-player serve/return/tiebreak/clutch scatter, filtered by year and tour

Not a web app. Opens from `file://` on Connor's Mac. No build step, no server.

---

## File map (read these before touching anything)

| What you need | Where to look |
|---|---|
| Data architecture + source registry | `data/sources.md` |
| Scrape runbook (step-by-step JS + Python) | `scripts/SCRAPE_INSTRUCTIONS.md` |
| Trapezoid refresh runbook | `scripts/trapezoid_refresh_SKILL.md` |
| Tournament calendar logic | `data/tournaments.js` |
| Player bios (static, curated) | `data/players_atp.js`, `data/players_wta.js` |
| Live season data (auto-updated) | `data/season_atp.js`, `data/season_wta.js` |
| Trapezoid metrics | `data/trapezoid_data.js` |

---

## Data model (commit this to memory)

```js
// season_*.js structure
SEASON_ATP.players[id] = {
  rank,       // current singles ranking
  pts,        // T12M points (read from rankings page — NOT computed)
  ytd,        // YTD race points (from race standings page)
  rankMove,   // +/- vs prior week
  results: {  // per-tournament performance
    [tournId]: { r, pts }
  }
}
SEASON_ATP.activeTournaments = [{ id, stage, players: { [pid]: { r, elim } } }]
```

Player IDs in `players_atp.js` / `players_wta.js` are 1–100, approximately ranking order at the time of the last manual refresh (id:1 = Alcaraz for ATP). Name matching between scraped data and player IDs is handled by `update_season.py`.

---

## Pipeline summary

### Rankings + draws (daily/weekly)
1. Chrome MCP scrapes ATP + WTA rankings and race pages (4 pages total)
2. Results written to `scripts/raw/*.json`
3. `python scripts/update_season.py` processes JSON → writes `data/season_*.js`

Scheduled as `tennis-dashboard-update` task in Cowork. Full runbook in `scripts/SCRAPE_INSTRUCTIONS.md`.

### Trapezoid metrics (weekly/annual)
Fetches Jeff Sackmann's match-level CSVs from GitHub → aggregates in-browser → `scripts/write_trapezoid_from_json.py` writes `data/trapezoid_data.js`.

Sackmann CSVs update annually (year N lands in Jan-Feb of N+1). The 2025 file won't appear until early 2026.

---

## Known issues (don't debug these without checking first)

- **WTA load-more bug**: `wtatennis.com/rankings/singles` sometimes only returns top-50 when the "Load More" button doesn't trigger. Fallback: use the undocumented `api.wtatennis.com/tennis/players/ranked?page=N&pageSize=50` endpoint. See `data/sources.md` → Path A.
- **activeTournaments not updating for WTA**: WTA rankings page doesn't embed tournament status inline (unlike ATP). Manual update or separate draw scrape required.
- **Race tab heatmap mostly empty**: per-tournament results in `results{}` only populate if a tournament transitions from active → complete while the scraper is running. Currently manual. See `data/sources.md` → Action Item 3.
- **Player name matching warnings**: `update_season.py` prints `WARN: X not found — skipping` for unmatched names. Match rate < 80 suggests page structure changed.

---

## License constraint — important

`data/trapezoid_data.js` is derived from Jeff Sackmann's data, licensed **CC BY-NC-SA 4.0**.

- ✅ Personal use, learning, sharing
- ❌ Ads, commercial products, paid tiers

This is **non-negotiable and non-upgradable** without replacing the entire match-level data layer. Don't suggest commercial features without flagging this.

---

## How to work in this project

**Before modifying any pipeline script**, read `scripts/SCRAPE_INSTRUCTIONS.md` and check what the script already handles. `update_season.py` preserves `results{}` history — don't overwrite it with a naive rewrite.

**Before modifying dashboard HTML/JSX**, check whether data is coming from `season_*.js` vs `players_*.js` vs `trapezoid_data.js`. Getting the source wrong produces stale or missing data.

**When adding players**: edit `data/players_atp.js` or `data/players_wta.js` — do NOT hardcode players inside the HTML. IDs must not conflict with existing entries.

**When adding tournaments**: edit `data/tournaments.js` only. The `TOURNAMENTS_DATA[]` array and `PTS{}` lookup table both need updating.

**Scraper JS snippets are fragile**: ATP and WTA change their page DOM periodically. If match rate drops, inspect the live page and update the selectors. The current working snippets are in `scripts/SCRAPE_INSTRUCTIONS.md` — don't modify them without testing.

---

## Response style

BLUF (bottom line first). No fluff. If something is a hypothesis or estimate, label it explicitly. Cite the specific file/line when making claims about code behavior.
