"""Minimal client for Chess.com's Published Data API with an on-disk cache.

The API is read-only and public. Chess.com may answer 429 when rate limiting,
so requests are retried with exponential backoff. Every successful response
is cached under data/raw/ so the pipeline can be re-run offline.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import requests

from .config import API_BASE, RAW_DIR, USER_AGENT

_local = threading.local()


def _session() -> requests.Session:
    # requests.Session is not guaranteed to be thread-safe: one per thread.
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _local.session


def fetch(url: str, *, max_retries: int = 6, missing_ok: bool = False) -> dict | None:
    """GET `url` and decode JSON. Returns None for 404/410 when `missing_ok`."""
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = _session().get(url, timeout=30)
        except requests.RequestException:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code == 200:
            return resp.json()
        if missing_ok and resp.status_code in (404, 410):
            return None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
    raise RuntimeError(f"giving up on {url}")


def _cache_path(url: str) -> Path:
    rel = url.removeprefix(API_BASE).strip("/")
    return RAW_DIR / f"{rel}.json"


def get_json(url: str, *, use_cache: bool = True) -> dict:
    """Cached GET for small, immutable resources (tournaments, rounds, groups)."""
    path = _cache_path(url)
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def tournament_url(slug: str) -> str:
    return f"{API_BASE}/tournament/{slug}"
