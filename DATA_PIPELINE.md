# Data Pipeline — Design Doc

Status: **Draft, awaiting decisions** (provider, budget, refresh schedule)
Last updated: 2026-04-25

---

## TL;DR

Replace today's mix of (Chrome-scraped ATP/WTA pages + Sackmann CSVs) with a single paid sports-data API as the source of truth for 2025+ data. Keep Sackmann's 2024 baseline as a frozen historical layer. Run a daily-during-tournaments / weekly-otherwise refresh against the API, materialize into the existing `data/*.js` files the dashboard consumes.

**Open decisions** that gate provider selection (asked at the end of this doc):
1. Monthly budget ceiling
2. Whether you want live/in-progress match data, or post-tournament only
3. Whether the API needs to cover historical 2025 backfill, or just current/future

---

## What we need from any API

The dashboard consumes 4 datasets. Each has different freshness needs.

| Dataset | Today's source | Fields needed | Refresh cadence |
| --- | --- | --- | --- |
| **Singles rankings (T12M + YTD race)** | atptour.com / wtatennis.com scrape | rank, points, prior-week rank, country | Weekly (Mondays), daily during slams |
| **Active tournament draws** | wtatennis.com draw scrape | tournament id/name, surface, draw size, per-player {round, eliminated} | Live during events |
| **Match-level box scores** (powers Trapezoid + form-hover) | Sackmann GitHub CSVs (2024 only) | per-match: date, surface, round, both players, score, full stats (aces/DFs/svpt/1stIn/1stWon/2ndWon/SvGms/bpSaved/bpFaced) per side | Within 24-72hrs of match end |
| **Player bios** | manual, players_*.js | name, country, age, abbreviation | Annual (Jan), or on top-100 churn |

Match-level box scores are the hardest data to acquire — that's the deciding factor in API selection.

---

## API options

I'm marking pricing as `[unverified]` where I'm working from training-data knowledge. Verify before you commit.

### Tier 1 — Premium / Enterprise

| Provider | Coverage | Strengths | Weaknesses | Price `[unverified]` |
| --- | --- | --- | --- | --- |
| **Sportradar Tennis** | Live + historical, full box scores, point-by-point on majors, betting odds | Industry standard; powers ESPN/Bet365 etc.; SLAs; full historical | Enterprise contracts; ~$10-30k/yr typical entry; not designed for hobbyists | $1-3k/mo entry tier; can scale much higher |
| **Enetpulse** | Similar coverage to Sportradar | Often used by smaller broadcasters | Same enterprise sales motion | $1-2k/mo entry |
| **Stats Perform / Opta** | Deep tactical data, shot-tracking | Best for advanced analytics | Even pricier than Sportradar | Custom pricing |

**Verdict:** overkill for a personal dashboard. Skip unless you're productizing.

### Tier 2 — Mid-market direct / RapidAPI

These are the realistic options for a personal/hobby budget. Pricing below is **verified from each provider's site on 2026-04-25** unless noted.

| Provider | Match-level box stats? | Verified price | Verdict for trapezoid |
| --- | --- | --- | --- |
| **api-tennis.com** | ❌ No — checked their docs. Endpoints return scores, fixtures, rankings, H2H, point-by-point timelines, odds. **No serve %, aces, BPs, etc.** | $40 / $60 / $80 per month (Starter / Premium / Business). 15% off annual. | **Rules out** for trapezoid. Could replace rankings + draws scraping only. |
| **SportsDataIO Tennis** | ❌ No — checked their endpoint list. Only `Match`/`MatchFinal` returns scores + sets, no statistics tables. | Subscription required (no public pricing) | **Rules out** for trapezoid. |
| **Goalserve** | 🟡 Marketing copy says "player statistics" — not specific. Need to confirm via free-trial what's actually returned. | $150/mo, $1000/6mo, $1200/year | Maybe — confirm via trial before committing. |
| **Matchstat Tennis API** | ✅ **Yes — explicitly lists** "aces, serve stats, return stats, tiebreak stats, average opponent rank, deciding set stats" filterable by year/surface/round/tournament. Their site is the only one explicitly matching every trapezoid metric. | **Hidden — sales-quote model.** No public price page. | **Best feature match. Need to email for a quote.** |
| **GitHub `n63li/Tennis-API`** | 🟡 Open-source scraper of ATP/WTA sites — same approach as our current pipeline | Free (self-host) | Same as what we have today. Skip. |

**Updated verdict:** of the public/known providers, **only Matchstat explicitly delivers box-score stats**. api-tennis.com and SportsDataIO — despite their marketing — return scores not stats. Goalserve is ambiguous and costs more than api-tennis without proven feature-fit.

The clean path forward:
1. **Email Matchstat for an API quote.** Ask explicitly: "Do you serve per-match box-score statistics (aces, double faults, 1st-serve in/won, 2nd-serve won, service games, BPs faced/saved) for ATP and WTA singles, and what's the price/month?"
2. **In parallel, use api-tennis.com Starter ($40/mo)** as a near-term replacement for the rankings + active-draw scraping. Trapezoid stays on Sackmann's frozen 2024 baseline until Matchstat is sorted.
3. **If Matchstat is too expensive (>$200/mo) or has gaps**, revisit Goalserve's free trial.

### Tier 3 — Aggregators with limits

| Provider | Coverage | Notes |
| --- | --- | --- |
| **OddsJam / SportsGameOdds** | Fixtures + odds, some stats | Betting-focused, may have what we need as a side feature |
| **The Sports DB** | Free, community-maintained | Coverage spotty; not reliable for stats |

---

## Recommended provider (verified 2026-04-25)

**Single recommendation: Matchstat Tennis API via RapidAPI, Pro tier $10/mo.**

Matchstat publishes on RapidAPI under the developer alias `jjrm365` as "Tennis API - ATP WTA ITF" (`rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf`). Confirmed it's actually Matchstat by the email contact (`tennisapi@matchstat.com`) and docs URL (`tennisapidoc.matchstat.com`) on the RapidAPI page.

### What you get for $10/mo

- **10,000 requests/month**, 5 req/sec rate limit
- **Coverage**: ATP, WTA, ITF singles + doubles, history back to 1930, weekly ranking updates
- **Endpoints we'll use**:
  - `GET /tennis/v2/{atp|wta}/player` — list all ranked players (replaces our rankings scrape)
  - `GET /tennis/v2/{atp|wta}/player/profile/{id}` — bio + ranking + surface points (replaces parts of `players_*.js` curation)
  - `GET /tennis/v2/{atp|wta}/player/past-matches/{id}?include=stat&filter=GameYear:2025,2026` — recent matches with per-match serve/return stats. Powers both the form-bar hover AND trapezoid 2025+ aggregation.
  - `GET /tennis/v2/{atp|wta}/player/match-stats/{id}` — career-aggregated stats (alternative trapezoid feeder)
  - `GET /tennis/v2/{atp|wta}/player/surface-summary/{id}` — yearly W/L by surface (could replace `surf:{H,C,G}` in our bios)

### Field mapping vs Sackmann

| Sackmann | Matchstat | Notes |
| --- | --- | --- |
| `w_ace` / `l_ace` | `serviceStats.acesGm` | ✅ |
| `w_df` / `l_df` | `serviceStats.doubleFaultsGm` | ✅ |
| `w_svpt` / `l_svpt` | derive: `firstServeOfGm + winningOnSecondServeOfGm` | ✅ |
| `w_1stIn` / `l_1stIn` | `firstServeGm` | ✅ |
| `w_1stWon` / `l_1stWon` | `winningOnFirstServeGm` | ✅ |
| `w_2ndWon` / `l_2ndWon` | `winningOnSecondServeGm` | ✅ |
| `w_bpSaved` / `l_bpSaved` | `breakPointSavedGm` | ✅ |
| `w_bpFaced` / `l_bpFaced` | `breakPointFacedGm` | ✅ |
| `w_SvGms` / `l_SvGms` (service games count) | **Not in published docs** | ⚠️ test on first call; may be in `include=stat` payload not shown in docs. Fallback: estimate `svpt / 6.5` for the `acesPerSvGm` metric. |

### Volume math

Our estimated usage:
- Weekly: 2 calls (rankings ATP + WTA list endpoints) × 4 weeks/mo = **8 calls/mo for rankings**
- Daily-during-tournaments: ~4 calls/day × 25 tournament weeks/year ÷ 12 ≈ **70 calls/mo for active draws**
- Daily incremental match stats: 200 active players × past-matches query, but with include=stat filtered to last 7 days = **~50 calls/day during active weeks** ≈ **1,500 calls/mo**
- One-time 2025 backfill: 200 ATP + 200 WTA players × 1 paginated past-matches call each = **~800 calls one-time**

**Total ongoing: ~1,600 calls/mo. Buffer of 8,400 to overage limit. $10/mo flat.**

### Risks (small)

| Risk | Mitigation |
| --- | --- | 
| Service-game count missing → can't compute `acesPerSvGm` | Estimate from svpt; or upgrade to Ultra ($39/mo) and test their richer payload |
| RapidAPI middleman adds latency / outages | All responses cached locally; dashboard reads from cache, not API directly |
| Pricing change at Matchstat | We'll see it in the RapidAPI billing email; can switch providers since our pipeline reads from a normalized JSON cache, not the raw API |

### Why this beats every other option I evaluated

- **api-tennis.com**: $40/mo, no box-score stats. 4× cost for less functionality.
- **SportsDataIO**: hidden pricing, no stats endpoints.
- **Goalserve**: $150/mo, ambiguous about box-score coverage.
- **Sportradar**: $1k+/mo enterprise. Overkill.
- **Build our own scraper**: 3-4 weeks dev + ongoing maintenance. $10/mo Matchstat = ~5 hours of your time per year. Don't build what you can rent for $10/mo.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  Tennis API (api-sports / api-tennis)        │
│  REST + JSON                                 │
└────────────────────┬─────────────────────────┘
                     │ HTTPS, API key in env
                     ▼
┌──────────────────────────────────────────────┐
│  scripts/api_client.py                       │
│  - Auth (API key from .env)                  │
│  - Rate-limit aware retry                    │
│  - Caches responses to scripts/cache/api/    │
└────────┬───────────┬───────────┬─────────────┘
         │           │           │
         ▼           ▼           ▼
   rankings.json  draws.json   matches.json
   (refreshed     (live during  (incremental,
    weekly)        events)       last 14 days +
                                 backfill on demand)
         │           │           │
         └───────────┼───────────┘
                     ▼
┌──────────────────────────────────────────────┐
│  scripts/build_dashboard_data.py             │
│  - Reads cached API responses                │
│  - Joins to players_*.js bios                │
│  - Renders all data/*.js consumed by HTML    │
└────────────────────┬─────────────────────────┘
                     ▼
        data/season_atp.js   (rankings + active draws)
        data/season_wta.js   (rankings + active draws)
        data/trapezoid_data.js (per-player aggregates, 2024 + ours)
        data/recent_matches.js (last 10 matches per player, for hover)
```

**Key principle: cache aggressively.** API calls cost money + count against rate limits. Every API response gets stored in `scripts/cache/api/{endpoint}/{date}.json`. The `build_dashboard_data.py` reads from cache; the API client only re-fetches when cache is older than the freshness threshold (configurable per dataset).

---

## Refresh schedule (proposed)

| Dataset | Trigger | Calls/run | Calls/month estimate |
| --- | --- | --- | --- |
| Rankings (ATP + WTA) | Mondays + every day during slams (4 weeks/year) | 2-4 calls (paginated) | ~30 |
| Active draws (live) | Every 2 hours during in-progress events (~25 weeks/year) | ~10 calls | ~2,500 |
| Match-level stats (incremental) | Daily | ~50 calls (matches finished in last 24h) | ~1,500 |
| Match-level stats (2025 backfill, one-time) | Manual | ~6,000 calls one-time | one-time |
| Player metadata | Monthly | ~2 calls | ~2 |

**Total monthly API call estimate: ~4,000 calls** during tournament weeks, less off-season. Most $30-50 tiers handle 10k-100k calls/month, so we're well within budget.

---

## Data layering: 2024 (Sackmann) + 2025+ (our API)

```
data/trapezoid_data.js
├── TRAPEZOID_YEARS = [2024, 2025, 2026]
├── TRAPEZOID_ATP   (combined: 2024 from Sackmann, 2025+ from API)
└── TRAPEZOID_WTA   (combined: 2024 from Sackmann, 2025+ from API)
```

Each row tagged with `{year, source: "sackmann"|"api"}` so we can debug discrepancies. The dashboard's year selector already handles year-tagged rows — no UI change needed.

**Edge case:** if API's stats schema differs from Sackmann's by a field (e.g., they call it `aces` vs `w_ace`), our `build_dashboard_data.py` normalizes both to the same canonical schema. The bridge function is small; gets defined once during integration.

---

## Migration plan

Sequenced to minimize risk:

### Phase 1 — Provider selection + smoke test (1 week, ~$30 spend)
- [ ] Sign up for chosen API on a monthly plan
- [ ] Cancel-able — if their match-stats coverage is bad, we eat $30 and try the alternative
- [ ] Build `scripts/api_client.py` with auth + 1 endpoint working (e.g., fetch ATP top-10)
- [ ] Validate: schema, rate limits, data accuracy vs current scrape

### Phase 2 — Rankings & draws migration (1 week)
- [ ] Replace `update_season.py`'s Chrome-scraping with API calls
- [ ] Same output files (`season_atp.js`, `season_wta.js`)
- [ ] Run in parallel with old scraper for 1 week to compare outputs
- [ ] Cut over once confidence is high; archive the Chrome scrapers

### Phase 3 — Match-level pipeline + 2025 backfill (1-2 weeks)
- [ ] Build `fetch_matches.py` → cache responses → emit normalized JSON
- [ ] One-time backfill: pull all completed 2025 + 2026-to-date matches
- [ ] Run `build_dashboard_data.py` to merge with 2024 Sackmann baseline
- [ ] Verify trapezoid_data.js looks correct: top players match expectations, per-year toggles work

### Phase 4 — Form-hover wiring (~3 hours)
- [ ] Build `recent_matches.js` from the matches dataset (last 10 per player)
- [ ] Wire hover into the dashboard's form-bar boxes
- [ ] Format: `Apr 23 · Madrid R32 · def. Sinner 7-6 6-4`

### Phase 5 — Schedule + monitoring (~2 hours)
- [ ] Cowork scheduled task for daily incremental + weekly rankings
- [ ] Freshness alerts in dashboard header (warn if `lastUpdated` > 9 days during tournament weeks)
- [ ] Decommission Chrome-based scrapers (keep code in git history as fallback)

**Total engineering effort: ~3-4 weeks of part-time work, ~$30-50 first month, ~$30/mo ongoing if API-Sports works.**

---

## Risk register

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| API match-stats schema doesn't match Sackmann's (missing fields like `bpSaved`) | Medium | Phase 1 smoke test catches this before major spend |
| API price hike or service shutdown | Low-Medium | All data is cached locally → snapshot survives provider death; switching providers requires refactor of `api_client.py` only |
| Rate limit lower than estimate | Medium | Caching + nightly batch; if tight, upgrade to next tier |
| 2025 historical not available from API | Low-Medium | Some APIs only carry recent N months. Verify in Phase 1. Worst case: fill 2025 from Sackmann if/when he publishes it (your call to skip Sackmann; revisit if API doesn't deliver) |
| API stats look "off" vs reality | Medium | Spot-check 5 random matches against atptour.com box scores; alarm if anything > ±2% deviation |

---

## Open decisions (need your input before I start Phase 1)

1. **Monthly budget ceiling.** $30 (Basic), $50 (Pro), $100+ (Ultra)?
2. **Live in-progress match data needed?** Or is "post-tournament within 72hrs" acceptable for everything except the Live tab (which already polls active draws separately)?
3. **2025 historical backfill via API or skip?** API may charge per call for backfill (e.g., 6,000 calls × $0.005/call = $30 one-time). Acceptable, or do we just start fresh from "today forward"?
4. **Where does the daily refresh run?** Cowork scheduled task (yes if you'll have Cowork open daily anyway), GitHub Action (runs in cloud, doesn't need your machine), or local cron on your Mac (most reliable but ties to one machine)?

---

## Footnote: why I had been recommending against paid APIs

For full transparency: I'd been steering toward Sackmann + scraping because (a) you mentioned the dashboard is personal-use, and (b) the inherited CC BY-NC-SA license already prevents monetization, so "investment in real-time API" had no commercial ROI. That logic ignored the *user-experience* benefits of reliability and richness. You corrected me — paid API is the right call for this kind of project even without a commercial angle. Doc above reflects the corrected framing.
