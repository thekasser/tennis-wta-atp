#!/usr/bin/env python3
"""
api_client.py — Matchstat Tennis API client (via RapidAPI).

Reads MATCHSTAT_API_KEY from ./.env (do NOT commit that file).
Caches all responses to scripts/cache/api/{endpoint}/{key}.json so we don't
burn the 10k req/mo budget on dev work.

USAGE
-----
    from scripts.api_client import MatchstatClient
    c = MatchstatClient()
    sinner = c.get('atp', 'player/profile/106421')
    matches = c.past_matches('atp', 106421, year=2025, include='stat')

CLI smoke test:
    python3 scripts/api_client.py smoke
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT  = Path(__file__).parent.parent
ENV_FILE   = REPO_ROOT / ".env"
CACHE_DIR  = REPO_ROOT / "scripts" / "cache" / "api"
DEFAULT_TIMEOUT = 20  # seconds

# Cache TTL per endpoint pattern (seconds).
# Tune these — burning cache costs API calls.
CACHE_TTL = {
    "player":              7 * 24 * 3600,   # bio rarely changes
    "player/profile":      7 * 24 * 3600,
    "player/titles":      30 * 24 * 3600,
    "player/match-stats":  3 * 24 * 3600,   # career aggregates change slowly
    "player/past-matches": 6 * 3600,        # ~daily during tournaments
    "player/surface-summary": 7 * 24 * 3600,
    "player/perf-breakdown":  7 * 24 * 3600,
    "default":             24 * 3600,
}


def load_env(path: Path = ENV_FILE) -> dict:
    """Tiny .env parser — KEY=VALUE per line, # comments, ignores blank."""
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class MatchstatClient:
    BASE_PATH = "/tennis/v2"

    def __init__(self, api_key: str | None = None, host: str | None = None,
                 cache: bool = True, verbose: bool = True):
        env = load_env()
        self.api_key = api_key or os.environ.get("MATCHSTAT_API_KEY") or env.get("MATCHSTAT_API_KEY")
        self.host    = host or os.environ.get("MATCHSTAT_API_HOST") or env.get("MATCHSTAT_API_HOST", "tennis-api-atp-wta-itf.p.rapidapi.com")
        if not self.api_key:
            raise RuntimeError(
                "MATCHSTAT_API_KEY not set. Add it to .env or export it before calling."
            )
        self.cache = cache
        self.verbose = verbose
        # Rate-limit throttle: Matchstat Pro plan caps ~5 req/s. Spacing
        # consecutive network requests ≥ 250ms apart keeps us safely under
        # without serializing too aggressively.
        self._last_request_at = 0.0
        self._min_interval    = 0.25
        if cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ─── Core HTTP ─────────────────────────────────────────────────────────
    def _ttl(self, endpoint: str) -> int:
        for prefix, ttl in CACHE_TTL.items():
            if endpoint.startswith(prefix):
                return ttl
        return CACHE_TTL["default"]

    def _cache_path(self, endpoint: str, params: dict) -> Path:
        # Stable filename per query
        param_str = urllib.parse.urlencode(sorted(params.items())) if params else ""
        safe = endpoint.replace("/", "_") + ("__" + param_str.replace("&", "_").replace("=", "-")
                                              if param_str else "")
        return CACHE_DIR / (safe + ".json")

    def get(self, tour: str, endpoint: str, params: dict | None = None,
            force_refresh: bool = False) -> dict | list:
        """
        tour: 'atp' or 'wta'
        endpoint: e.g. 'player/profile/106421' or 'player/past-matches/106421'
        params: query string params (dict)
        """
        if tour not in ("atp", "wta"):
            raise ValueError(f"tour must be 'atp' or 'wta', got {tour}")
        params = params or {}
        full_endpoint = f"{tour}/{endpoint}"
        path = self._cache_path(full_endpoint, params)

        # Cache hit
        if self.cache and not force_refresh and path.exists():
            age = time.time() - path.stat().st_mtime
            if age < self._ttl(endpoint):
                if self.verbose:
                    print(f"  [cache] {full_endpoint} ({int(age/60)}m old)", file=sys.stderr)
                return json.loads(path.read_text())

        # Network fetch
        url = f"https://{self.host}{self.BASE_PATH}/{full_endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "X-RapidAPI-Key":  self.api_key,
                "X-RapidAPI-Host": self.host,
                "Accept":          "application/json",
                # Cloudflare 1010 fires on default urllib UA. Browser-like UA bypasses it.
                "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36",
            },
        )
        if self.verbose:
            print(f"  [net]   {full_endpoint}", file=sys.stderr)

        # Throttle: ensure ≥ self._min_interval since the last network request.
        elapsed = time.time() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        # Retry loop: on 429, honor Retry-After (or back off 2/4/8s).
        max_attempts = 4  # 1 initial + 3 retries
        data = None
        for attempt in range(1, max_attempts + 1):
            self._last_request_at = time.time()
            try:
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_attempts:
                    retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") and e.headers else None
                    try:
                        wait = float(retry_after) if retry_after else 2.0 * (2 ** (attempt - 1))
                    except (TypeError, ValueError):
                        wait = 2.0 * (2 ** (attempt - 1))
                    if self.verbose:
                        print(f"  [429]   {full_endpoint} — retry {attempt}/{max_attempts-1} after {wait:.1f}s",
                              file=sys.stderr)
                    time.sleep(wait)
                    continue
                err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
                raise RuntimeError(f"HTTP {e.code} on {full_endpoint}: {err_body[:300]}")

        if self.cache:
            path.write_text(json.dumps(data, indent=2))
        return data

    # ─── Convenience wrappers (typed-ish) ──────────────────────────────────
    def players(self, tour: str, page_size: int = 200, page_no: int = 1,
                include: str | None = None, filter_: str | None = None) -> list:
        params = {"pageSize": page_size, "pageNo": page_no}
        if include: params["include"] = include
        if filter_: params["filter"]  = filter_
        return self.get(tour, "player", params)

    def profile(self, tour: str, player_id: int, include: str | None = None) -> dict:
        params = {"include": include} if include else {}
        return self.get(tour, f"player/profile/{player_id}", params)

    def career_stats(self, tour: str, player_id: int) -> dict:
        return self.get(tour, f"player/match-stats/{player_id}")

    def past_matches(self, tour: str, player_id: int, year: int | str | None = None,
                     include: str = "stat,round,tournament", page_size: int = 50,
                     page_no: int = 1) -> dict:
        params = {"include": include, "pageSize": page_size, "pageNo": page_no}
        if year: params["filter"] = f"GameYear:{year}"
        return self.get(tour, f"player/past-matches/{player_id}", params)


# ─── CLI smoke test ─────────────────────────────────────────────────────────

def smoke():
    """
    Validate end-to-end: pull Sinner's profile, career stats, last 10 matches
    (with stat detail), and check whether per-match service-game count is
    actually returned (the one field the docs didn't promise).
    """
    c = MatchstatClient()
    print(f"\n→ Connecting to {c.host}")
    print(f"→ API key: {c.api_key[:8]}…{c.api_key[-4:]}\n")

    # Sinner's real player ID is 47275 (confirmed via /atp/ranking/singles).
    # The docs example uses 106421 but that's stale data.
    SINNER = 47275

    print("─── 1. Player profile ──────────────────────────────────")
    prof = c.profile("atp", SINNER, include="form,ranking,country")
    print(f"  Name:       {prof.get('name')}")
    print(f"  Country:    {prof.get('countryAcr')}")
    print(f"  Rank:       {prof.get('currentRank')} ({prof.get('points'):,} pts)")
    print(f"  Career hi:  {prof.get('ch')}")
    print(f"  Surface pts: H{prof.get('hardPoints')} / C{prof.get('clayPoints')} / G{prof.get('grassPoints')}")

    print("\n─── 2. Career match stats (aggregated) ─────────────────")
    cs = c.career_stats("atp", SINNER)
    serv = (cs.get("data") or {}).get("serviceStats") or {}
    bp_serve = (cs.get("data") or {}).get("breakPointsServeStats") or {}
    print(f"  Aces (career):    {serv.get('acesGm'):,}" if serv.get('acesGm') else "  Aces: missing")
    print(f"  Double faults:    {serv.get('doubleFaultsGm'):,}" if serv.get('doubleFaultsGm') else "")
    if serv.get("firstServeOfGm"):
        pct = round(100 * serv['firstServeGm'] / serv['firstServeOfGm'], 1)
        print(f"  1st-serve %:      {pct}% ({serv['firstServeGm']:,}/{serv['firstServeOfGm']:,})")
    print(f"  BPs faced:        {bp_serve.get('breakPointFacedGm'):,}" if bp_serve.get('breakPointFacedGm') else "")
    print(f"  BPs saved:        {bp_serve.get('breakPointSavedGm'):,}" if bp_serve.get('breakPointSavedGm') else "")

    print("\n─── 3. Last 10 matches (with include=stat) ─────────────")
    pm = c.past_matches("atp", SINNER, year=2025, include="stat,round,tournament", page_size=10)
    rows = pm.get("data") or []
    if not rows:
        print("  (no rows returned — check year filter or auth)")
    else:
        for i, m in enumerate(rows[:10], 1):
            tn = (m.get("tournament") or {}).get("name", "?")
            score = m.get("result", "?")
            opp = m.get("player2", {}).get("name") if m.get("player1Id") == SINNER else m.get("player1", {}).get("name")
            won = "def." if m.get("player1Id") == SINNER else "lost to"
            date = (m.get("date") or "")[:10]
            print(f"  {i:2d}. {date} · {tn:<30} · {won} {opp:<25} · {score}")

        print("\n─── 4. CRITICAL: do per-match stats include service games? ──")
        m1 = rows[0]
        # The 'stat' include should attach a stat object/array — schema unknown
        stat_keys = [k for k in m1.keys() if 'stat' in k.lower()]
        print(f"  Top-level keys in match record:  {sorted(m1.keys())}")
        print(f"  Stat-related keys present:       {stat_keys}")
        # Look for service game indicators
        all_keys = json.dumps(m1)
        gm_indicators = [k for k in ['svGms', 'serviceGames', 'sv_games', 'gameCount'] if k in all_keys]
        print(f"  Service-game count indicators:   {gm_indicators or 'NONE FOUND in top-level'}")
        if stat_keys:
            print(f"\n  Full match[0] payload:")
            print(json.dumps(m1, indent=2)[:2000])

    print("\n✓ Smoke test complete.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        smoke()
    else:
        print(__doc__)
