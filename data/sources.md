# Data Sources

Single source of truth for where every piece of data on the dashboard comes from, how often it refreshes, what the known issues are, and what the upgrade path looks like if the project ever needs paid feeds.

Last reviewed: 2026-04-25

---

## Live data flow (what's running today)

```
                    ┌─────────────────────┐         ┌─────────────────────┐
                    │  atptour.com        │         │  wtatennis.com      │
                    │  /rankings/singles  │         │  /rankings/singles  │
                    └──────────┬──────────┘         └──────────┬──────────┘
                               │ Chrome MCP scrape             │ Chrome MCP scrape
                               ▼                                ▼
                       scripts/raw/atp_*.json         scripts/raw/wta_*.json
                               │                                │
                               └────────────┬───────────────────┘
                                            ▼
                               scripts/update_season.py
                                            │
                            ┌───────────────┴────────────────┐
                            ▼                                 ▼
                   data/season_atp.js              data/season_wta.js


                    ┌─────────────────────────────────────────┐
                    │  github.com/JeffSackmann/tennis_atp     │
                    │  github.com/JeffSackmann/tennis_wta     │
                    │  (annual match-level CSVs, after season)│
                    └──────────────────┬──────────────────────┘
                                       │ Chrome MCP fetch + in-browser aggregate
                                       ▼
                       scripts/cache/aggregates/{tour}_{year}.json
                                       │
                                       ▼
                       scripts/write_trapezoid_from_json.py
                                       │
                                       ▼
                       data/trapezoid_data.js
```

---

## Source registry

| Source | URL / Endpoint | License | Cadence | Coverage | Known issues |
| --- | --- | --- | --- | --- | --- |
| ATP rankings (T12M + race) | `atptour.com/en/rankings/singles/live` and `singles-race-to-turin` | Public web (no formal license) | Updated ~daily during weeks with results | Top 200, both rankings | Page structure changes occasionally — table selectors may need updates |
| WTA rankings (T12M + race) | `wtatennis.com/rankings/singles` and `race-singles` | Public web | Updated weekly Mondays + during slams | Top 50 visible by default; "Load More" sometimes hangs | **Active issue:** Load-More button doesn't always trigger (we saw this Apr 2026). Falls back to top-50 only. |
| WTA undocumented API | `api.wtatennis.com/tennis/players/ranked?page=N&pageSize=50&type=rankSingles` | Undocumented; assumed CC-equivalent | Real-time | All ranked players, paginated | No SLA, no documentation, can break without notice. **Use as fallback only.** Returns paginated JSON. |
| ATP active draws | `atptour.com/en/scores/current/<event>/<id>/draws` | Public web | Real-time during events | Singles draws | Different page layouts for different draw sizes (32/64/96/128) |
| WTA active draws | `wtatennis.com/tournaments/<slug>/draws` | Public web | Real-time during events | Singles draws | DOM uses `js-match-X-Y-LSN` class pattern; `LS{N}` numbers depend on draw size |
| Player bios | `data/players_atp.js`, `data/players_wta.js` (curated, manual) | n/a (our own) | Edited manually when player joins/leaves top 100 | Top 100 each tour | Manual maintenance — needs annual review for retirements/new entries |
| Match-level stats | `github.com/JeffSackmann/tennis_atp/atp_matches_YYYY.csv` and `tennis_wta/wta_matches_YYYY.csv` | **CC BY-NC-SA 4.0** | Annual, posted Jan-Feb of N+1 | All ATP/WTA main-draw matches with stats since 1968 (atp), since 2000 (wta) | Year `N` not available until early `N+1`. Currently ends at 2024 in our env. |
| Tournament calendar | `data/tournaments.js` (curated, manual) | n/a (our own) | Edited when tournament dates/active state change | All 2026 events | Manual — needs to be kept in sync with reality |

---

## Cadences

| Refresh | What runs | When |
| --- | --- | --- |
| **Daily during slams** | `tennis-dashboard-update` scheduled task → `scripts/update_season.py` after Chrome scrape | Manually triggered, or scheduled in Cowork |
| **Weekly (Mondays)** | Same task; rankings only update weekly outside slams | Mondays |
| **Weekly (Mondays, separate)** | `trapezoid-refresh` scheduled task → fetch Sackmann CSVs via Chrome → in-browser aggregate → `write_trapezoid_from_json.py` | Mondays. Most weeks no-op until N+1 file appears. |
| **Manual** | `tournaments.js` edits when calendar shifts | As needed |
| **Manual** | `players_*.js` edits for top-100 churn | Annual (Jan) |

---

## Why we don't pay for an API (yet)

The dashboard's match-level data sits on Sackmann's CC BY-NC-SA license. Per ShareAlike, our derived `trapezoid_data.js` inherits the license, which means:

- ✅ Personal use, sharing, learning, derivative dashboards
- ❌ Hosted commercial product, ads tied to the dashboard, paid tiers

Paying for a live data feed costs $1k-30k/year (typical) and only buys real-time speed — it does not unlock commercial use, because that constraint is on Sackmann's data not our compute path. So unless we replace the *match-level* layer entirely (Sportradar etc.), there's no commercial ROI to a paid API.

---

## Upgrade paths (if/when we want them)

Listed in order of cost and complexity.

### Path A — harden the unofficial WTA API (free, low risk)

Switch the WTA scrape from "click load-more in Chrome" to "fetch `api.wtatennis.com/tennis/players/ranked?page=1..4&pageSize=50`". Pulls top-200 reliably, no DOM dependency.

- **Pros:** fixes the load-more bug; ~10x faster than scraping; same data; free
- **Cons:** undocumented endpoint, can break, terms of service unclear (likely fine for personal use)
- **Effort:** small — replace the JS scraper with a fetch loop

### Path B — paid mid-tier feed (sportdata.io / RapidAPI)

Sport-data services running ~$50-300/mo for tennis rankings + match results.

- **Pros:** documented, has SLA, replaces both ATP + WTA scrapers
- **Cons:** doesn't include match-level service stats (Sackmann is unique there); still doesn't unlock commercial use
- **Effort:** medium — wire API client, handle auth, rate limits
- **When to consider:** if scraping breaks and we want a stable replacement

### Path C — Sportradar / Enetpulse (the gold standard)

Real-time streams used by major broadcasters and betting operators.

- **Pros:** in-tournament live point-by-point, ranking changes within minutes, full historical backfill, commercial-use rights
- **Cons:** $10k-30k+/year, contract-based, overkill for personal dashboards
- **Effort:** large — full integration project; legal review for contract
- **When to consider:** only if monetizing or productizing this dashboard

### Path D — Match Charting Project (Sackmann's separate repo)

`github.com/JeffSackmann/tennis_MatchChartingProject` — point-by-point data hand-charted by volunteers.

- **Pros:** more granular than the annual CSVs, includes shot-type / direction; same license
- **Cons:** small sample (~6k matches all-time), volunteer-driven coverage, slow to update
- **Effort:** medium — different schema; would feed a separate "shot patterns" tab, not the trapezoid
- **When to consider:** if we want to add shot-pattern analysis

---

## Action items

Things we'd do if we wanted to harden the pipeline without spending money:

1. **Wire the unofficial WTA API as a fallback** when DOM scraping returns < 100 rows. Fixes the load-more bug.
2. **Add freshness alarms** — if `season_*.js` `lastUpdated` is older than 9 days during a tournament week, log a warning in the dashboard header.
3. **Auto-freeze tournament results** in `update_season.py` — when a tournament transitions `active:true` → `complete:true`, copy the active-tournament players' final round into their `results` dict. Currently this is manual, which is why the Race tab's per-tournament heatmap is mostly empty for ATP.
4. **Document the player bio refresh** — add a `data/players_*.js` review reminder (Jan 1 each year) for retirements / new top-100 entrants.
