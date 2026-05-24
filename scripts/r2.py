#!/usr/bin/env python3
"""
r2.py — Thin wrapper around Cloudflare R2 (S3-compatible) for the DB snapshot.

R2 is the canonical store for `data/tennis.db.gz` since 2026-05-24. The
compressed snapshot contains raw Matchstat API payloads (matches.raw,
matches.stat_p1, matches.stat_p2) that the RapidAPI/Matchstat ToS doesn't
permit us to redistribute publicly — so the file lives in a private R2
bucket instead of the public GitHub repo.

ENV VARS
--------
    R2_ACCOUNT_ID         Cloudflare account ID (32-hex string)
    R2_BUCKET             Bucket name (default: tennis-snapshots)
    R2_ACCESS_KEY_ID      R2 API token's access key
    R2_SECRET_ACCESS_KEY  R2 API token's secret key

If R2_ACCOUNT_ID and the credentials are unset, `is_configured()` returns
False and callers are expected to skip the upload/download gracefully.
This lets local-dev work without R2 setup when the local DB already exists.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

DEFAULT_BUCKET = "tennis-snapshots"
DEFAULT_KEY    = "tennis.db.gz"


def is_configured() -> bool:
    """True iff every R2 env var needed for an upload/download is set."""
    return all(os.environ.get(k) for k in
               ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"))


def _client():
    """Lazy boto3 import so the rest of the pipeline works without it
    installed (local dev where R2 isn't needed)."""
    try:
        import boto3
    except ImportError:
        print("[r2] boto3 not installed. Install with: pip install boto3",
              file=sys.stderr)
        raise
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload(local_path: Path, *, bucket: str | None = None,
           key: str | None = None) -> None:
    bucket = bucket or os.environ.get("R2_BUCKET", DEFAULT_BUCKET)
    key    = key    or DEFAULT_KEY
    size = local_path.stat().st_size
    print(f"[r2] uploading {local_path.name} → s3://{bucket}/{key} "
          f"({size:,} bytes)")
    _client().upload_file(str(local_path), bucket, key)
    print(f"[r2] upload OK")


def download(local_path: Path, *, bucket: str | None = None,
             key: str | None = None) -> None:
    bucket = bucket or os.environ.get("R2_BUCKET", DEFAULT_BUCKET)
    key    = key    or DEFAULT_KEY
    local_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[r2] downloading s3://{bucket}/{key} → {local_path.name}")
    _client().download_file(bucket, key, str(local_path))
    size = local_path.stat().st_size
    print(f"[r2] download OK ({size:,} bytes)")
