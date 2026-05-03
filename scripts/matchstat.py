#!/usr/bin/env python3
"""
matchstat.py — Matchstat Tennis API client (via RapidAPI).

PHASE 1 RENAME: This file replaces `api_client.py`. The JSON-file cache from
the old client is intentionally gone — the SQLite DB is now the durable
cache. The client just makes well-behaved HTTP calls and returns parsed JSON
plus a metadata dict the caller can use to log to `api_fetch_log`.

Keeps the rate-limit throttle and 429 retry that landed during today's
Madrid fix (Pro plan caps ~5 req/s).

USAGE
-----
    from matchstat import MatchstatClient

    client = MatchstatClient()
    data, meta = client.past_matches('wta', 47742, year=2026)
    # `meta` includes endpoint, http_status, ms_elapsed, etc. — feed it to
    # `api_fetch_log` after you know how many rows you ended up inserting.

ENV / SECRETS
-------------
    MATCHSTAT_API_KEY   (required) — read from env or .env in repo root
    MATCHSTAT_API_HOST  (optional) — defaults to RapidAPI host
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT       = Path(__file__).parent.parent
ENV_FILE        = REPO_ROOT / ".env"
DEFAULT_TIMEOUT = 20  # seconds


def load_env(path: Path = ENV_FILE) -> dict:
    """Tiny .env parser — KEY=VALUE per line, # comments, blank lines ignored."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class MatchstatClient:
    BASE_PATH = "/tennis/v2"

    def __init__(self, api_key: str | None = None, host: str | None = None,
                 *, verbose: bool = True):
        env = load_env()
        self.api_key = (
            api_key
            or os.environ.get("MATCHSTAT_API_KEY")
            or env.get("MATCHSTAT_API_KEY")
        )
        self.host = (
            host
            or os.environ.get("MATCHSTAT_API_HOST")
            or env.get("MATCHSTAT_API_HOST", "tennis-api-atp-wta-itf.p.rapidapi.com")
        )
        if not self.api_key:
            raise RuntimeError(
                "MATCHSTAT_API_KEY not set. Add it to .env or export it before calling."
            )
        self.verbose = verbose
        # Rate-limit throttle (Pro plan ≈ 5 req/s; 250 ms gives margin).
        self._last_request_at = 0.0
        self._min_interval    = 0.25

    # ─── Core HTTP ─────────────────────────────────────────────────────────

    def get(self, tour: str, endpoint: str, params: dict | None = None
            ) -> tuple[dict | list, dict]:
        """Fetch `tour/endpoint` and return (parsed_json, fetch_meta).

        fetch_meta keys: fetched_at, endpoint, params, http_status,
        ms_elapsed, error, rows_returned (best-effort if response is a list
        or {data: [...]}).
        """
        if tour not in ("atp", "wta"):
            raise ValueError(f"tour must be 'atp' or 'wta', got {tour!r}")
        params = params or {}
        full_endpoint = f"{tour}/{endpoint}"

        url = f"https://{self.host}{self.BASE_PATH}/{full_endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "X-RapidAPI-Key":  self.api_key,
                "X-RapidAPI-Host": self.host,
                "Accept":          "application/json",
                # Cloudflare 1010 blocks default urllib UA. Browser-like UA passes.
                "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/124.0.0.0 Safari/537.36",
            },
        )
        if self.verbose:
            print(f"  [net]   {full_endpoint}", file=sys.stderr)

        # Throttle.
        elapsed = time.time() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        # Fetch with 429 retry (Retry-After honored; otherwise 2/4/8s backoff).
        max_attempts = 4
        meta: dict = {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "endpoint":      full_endpoint,
            "params":        json.dumps(params, separators=(",", ":")) if params else None,
            "http_status":   None,
            "ms_elapsed":    None,
            "rows_returned": None,
            "error":         None,
        }
        data: dict | list | None = None
        t_start = time.time()

        for attempt in range(1, max_attempts + 1):
            self._last_request_at = time.time()
            try:
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    meta["http_status"] = resp.status
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_attempts:
                    retry_after = (
                        e.headers.get("Retry-After")
                        if hasattr(e, "headers") and e.headers else None
                    )
                    try:
                        wait = float(retry_after) if retry_after else 2.0 * (2 ** (attempt - 1))
                    except (TypeError, ValueError):
                        wait = 2.0 * (2 ** (attempt - 1))
                    if self.verbose:
                        print(
                            f"  [429]   {full_endpoint} — retry {attempt}/{max_attempts-1} after {wait:.1f}s",
                            file=sys.stderr,
                        )
                    time.sleep(wait)
                    continue
                err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
                meta["http_status"] = e.code
                meta["error"]       = err_body[:300]
                meta["ms_elapsed"]  = int((time.time() - t_start) * 1000)
                raise RuntimeError(
                    f"HTTP {e.code} on {full_endpoint}: {err_body[:300]}"
                ) from e
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
                meta["error"]      = str(e)[:300]
                meta["ms_elapsed"] = int((time.time() - t_start) * 1000)
                raise RuntimeError(f"network/parse error on {full_endpoint}: {e}") from e

        meta["ms_elapsed"] = int((time.time() - t_start) * 1000)

        # Best-effort row count for the log.
        if isinstance(data, list):
            meta["rows_returned"] = len(data)
        elif isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                meta["rows_returned"] = len(inner)

        return data, meta

    # ─── Convenience wrappers ──────────────────────────────────────────────

    def past_matches(self, tour: str, mid: int, *, year: int | None = None,
                     include: str = "stat,round,tournament",
                     page_size: int = 50, page_no: int = 1
                     ) -> tuple[dict | list, dict]:
        params: dict = {"include": include, "pageSize": page_size, "pageNo": page_no}
        if year:
            params["filter"] = f"GameYear:{year}"
        return self.get(tour, f"player/past-matches/{mid}", params)

    def rankings(self, tour: str, *, race: bool = False,
                 page_size: int = 200, page_no: int = 1,
                 include: str | None = None
                 ) -> tuple[dict | list, dict]:
        # Race uses the same endpoint with `race=true` — there's no
        # separate `/ranking/race` route on the Matchstat API.
        params: dict = {"pageSize": page_size, "pageNo": page_no}
        if include:
            params["include"] = include
        if race:
            params["race"] = "true"
        return self.get(tour, "ranking/singles", params)

    def profile(self, tour: str, mid: int, *, include: str | None = None
                ) -> tuple[dict | list, dict]:
        params: dict = {"include": include} if include else {}
        return self.get(tour, f"player/profile/{mid}", params)


# ─── CLI smoke test ─────────────────────────────────────────────────────────

def _smoke() -> int:
    client = MatchstatClient()
    print(f"Matchstat API @ {client.host}")
    data, meta = client.past_matches("wta", 47742, year=2026, page_size=5)
    rows = (data or {}).get("data") or data or []
    print(f"  wta/47742 (Kostyuk): {meta['rows_returned']} rows, "
          f"http={meta['http_status']}, ms={meta['ms_elapsed']}")
    for m in rows[:3]:
        rd = m.get("round")
        rd_s = (rd.get("shortName") or rd.get("name")) if isinstance(rd, dict) else (rd or "")
        opp = (m.get("player2") or {}).get("name") or "?"
        print(f"    {(m.get('date') or '')[:10]}  {rd_s:<8}  vs {opp}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        sys.exit(_smoke())
    print(__doc__)
