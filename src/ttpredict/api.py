"""Minimal client for Chess.com's Published Data API with an on-disk cache.

The API is read-only and public. Chess.com recommends serial (not parallel)
requests and may answer 429 when rate limiting, so requests are sequential and
retried with exponential backoff. Every successful response is cached under
data/raw/ so the pipeline can be re-run offline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .config import API_BASE, RAW_DIR, USER_AGENT

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def _cache_path(url: str) -> Path:
    rel = url.removeprefix(API_BASE).strip("/")
    return RAW_DIR / f"{rel}.json"


def get_json(url: str, *, max_retries: int = 5, use_cache: bool = True) -> dict:
    """GET `url` and return the decoded JSON body, using the disk cache if possible."""
    path = _cache_path(url)
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    delay = 1.0
    for attempt in range(max_retries):
        resp = _session.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
            return data
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
    raise RuntimeError(f"unreachable: {url}")


def tournament_url(slug: str) -> str:
    return f"{API_BASE}/tournament/{slug}"
