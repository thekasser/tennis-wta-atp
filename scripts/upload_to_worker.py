#!/usr/bin/env python3
"""
upload_to_worker.py — Push materialized JSON blobs to D1 via the Worker's
POST /api/admin/sync endpoint (instead of `wrangler d1 execute --file=`).

WHY THIS EXISTS
---------------
The cron pipeline (GH Actions) runs `materialize.py` to produce data/*.js,
then needs to push those JSON blobs to D1. Two ways to do that:
  1. Install wrangler in CI + use `wrangler d1 execute --file=...`
  2. HTTP POST to the Worker with a bearer token   ← this script

Option 2 keeps CI light (no Node/wrangler), gives us proper auth via a
Workers Secret, and is the same path any future automated client could use.

USAGE
-----
    export ADMIN_SYNC_TOKEN="<the same value set via wrangler secret>"
    python3 scripts/upload_to_worker.py

    # Or specify a different worker URL:
    python3 scripts/upload_to_worker.py \\
        --url https://tennis-wta-atp.kasserconnor.workers.dev/api/admin/sync

ENV / FLAGS
-----------
    ADMIN_SYNC_TOKEN    (required) — bearer token; matches the worker secret
    --url               (optional) — endpoint URL; defaults to production
    --dry-run           (optional) — build + log payload sizes, don't POST
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Reuse the JS-literal → JSON extraction helpers from push_to_d1.py so we
# don't duplicate parsing logic.
from push_to_d1 import (
    SOURCES, DATA_DIR, _extract, _extract_trapezoid,
)


DEFAULT_URL = "https://tennis-wta-atp.kasserconnor.workers.dev/api/admin/sync"


def build_payload() -> dict[str, str]:
    """Read every data/*.js, extract its JSON, return {name: json_string}."""
    blobs: dict[str, str] = {}
    for name, fname, marker, open_ch, close_ch in SOURCES:
        path = DATA_DIR / fname
        if not path.exists():
            print(f"  skip {name}: {path} not found", file=sys.stderr)
            continue
        try:
            blobs[name] = _extract(path, marker, open_ch, close_ch)
        except Exception as e:
            print(f"  ERROR {name}: {e}", file=sys.stderr)
            raise

    trap_path = DATA_DIR / "trapezoid_data.js"
    if trap_path.exists():
        blobs["trapezoid"] = _extract_trapezoid(trap_path)

    return blobs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--dry-run", action="store_true",
                   help="build payload + report sizes without sending")
    args = p.parse_args()

    token = os.environ.get("ADMIN_SYNC_TOKEN")
    if not token and not args.dry_run:
        print("ERROR: ADMIN_SYNC_TOKEN env var not set.\n"
              "  Set it to the same value you used for `wrangler secret put ADMIN_SYNC_TOKEN`.",
              file=sys.stderr)
        return 1

    print("[upload] building payload from data/*.js …")
    blobs = build_payload()
    body = json.dumps({"blobs": blobs}).encode("utf-8")

    print(f"[upload] {len(blobs)} blobs, {len(body):,} bytes total:")
    for name, payload in sorted(blobs.items(), key=lambda kv: -len(kv[1])):
        print(f"  {name:<22} {len(payload):>10,} bytes")

    if args.dry_run:
        print("\n[upload] --dry-run set, not sending.")
        return 0

    print(f"\n[upload] POST {args.url} …")
    req = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type":  "application/json",
            # Cloudflare 1010 fires on default urllib UA. Browser-like UA bypasses it
            # (same workaround used in scripts/matchstat.py).
            "user-agent":    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_body = resp.read().decode("utf-8")
            print(f"[upload] HTTP {resp.status}")
            try:
                parsed = json.loads(response_body)
                print(json.dumps(parsed, indent=2))
            except json.JSONDecodeError:
                print(response_body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        print(f"[upload] HTTP {e.code}: {err_body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"[upload] network error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
