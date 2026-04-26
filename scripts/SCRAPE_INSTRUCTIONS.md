# Tennis Dashboard — Scrape & Update Instructions

Run these steps (in order) to update the season data files.
Claude follows this runbook automatically when the scheduled task fires.

## Step 1 — Scrape ATP Rankings (rank + T12M pts + active tournament status)

Navigate to: `https://www.atptour.com/en/rankings/singles/live`

Run this JS via the Chrome extension javascript_tool and save result to `scripts/raw/atp_rankings.json`:

```js
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells = Array.from(r.querySelectorAll('td'));
    const rankRaw = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const nameRaw = (cells[1]?.innerText || '').trim();
    const lines   = nameRaw.split('\n').map(s => s.trim()).filter(Boolean);
    const ptsRaw  = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const tournMatch = nameRaw.match(/([A-Z][a-zA-Z ]+)\s+(R\d+|QF|SF|F|W)\b/);
    return {
      rank:     parseInt(rankRaw) || null,
      name:     lines[0] || '',
      pts:      parseInt(ptsRaw) || 0,
      activeTournament: tournMatch ? tournMatch[1].trim() : null,
      activeRound:      tournMatch ? tournMatch[2]        : null
    };
  }).filter(r => r.rank !== null));
})()
```

## Step 2 — Scrape ATP Race (YTD pts)

Navigate to: `https://www.atptour.com/en/rankings/race`

```js
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells = Array.from(r.querySelectorAll('td'));
    const rank  = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name  = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ytd   = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { raceRank: parseInt(rank)||null, name, ytd: parseInt(ytd)||0 };
  }).filter(r => r.raceRank !== null));
})()
```

## Step 3 — Scrape WTA Rankings (rank + T12M pts)

Navigate to: `https://www.wtatennis.com/rankings/singles`

```js
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells  = Array.from(r.querySelectorAll('td'));
    const rankRaw = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name    = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ptsRaw  = (cells[2]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { rank: parseInt(rankRaw)||null, name, pts: parseInt(ptsRaw)||0 };
  }).filter(r => r.rank !== null));
})()
```

## Step 4 — Scrape WTA Race (YTD pts — column index 4)

Navigate to: `https://www.wtatennis.com/rankings/race-singles`

```js
(function() {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  return JSON.stringify(rows.slice(0, 150).map(r => {
    const cells = Array.from(r.querySelectorAll('td'));
    const rank  = (cells[0]?.innerText || '').trim().replace(/[^0-9]/g,'');
    const name  = (cells[1]?.innerText || '').trim().split('\n')[0].trim();
    const ytd   = (cells[4]?.innerText || '').trim().replace(/[^0-9]/g,'');
    return { raceRank: parseInt(rank)||null, name, ytd: parseInt(ytd)||0 };
  }).filter(r => r.raceRank !== null));
})()
```

## Step 5 — Save raw JSON files

After each extraction, write the result to the `scripts/raw/` directory:
- Step 1 result → `scripts/raw/atp_rankings.json`
- Step 2 result → `scripts/raw/atp_race.json`
- Step 3 result → `scripts/raw/wta_rankings.json`
- Step 4 result → `scripts/raw/wta_race.json`

## Step 6 — Run the processor

```bash
cd "/path/to/WTA Tennis Dashboardd"
python scripts/update_season.py
```

This reads the raw JSON files, matches player names to IDs, preserves
results{} history, updates rank/pts/ytd/rankMove, and rewrites
`data/season_atp.js` and `data/season_wta.js`.

## Step 7 — Verify

Check that the output files were updated:
```bash
head -5 data/season_atp.js
head -5 data/season_wta.js
```

Confirm the `lastUpdated` timestamp is today's date.

## Troubleshooting

**"WARN: X not found — skipping"** — raw file missing. Re-run the scrape step.

**Low match rate (< 80 matched)** — page structure may have changed.
Run `python scripts/update_season.py --print-js` to see current extraction JS.
Inspect the page DOM and update the selector if needed.

**activeTournaments not updating for WTA** — WTA rankings page doesn't
include inline tournament status. Update `data/season_wta.js` activeTournaments
manually after a tournament completes, or scrape the WTA draws page separately.
