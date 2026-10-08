"""Tiny cached JSON-over-HTTP client (stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "BookSorter/0.1 (+personal library tool)"
CACHE_DAYS = 30


class Cache:
    """URL -> JSON response, kept for CACHE_DAYS. Failed requests are never cached."""

    def __init__(self, path: Path | None):
        self._lock = threading.Lock()
        if path is None:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, created REAL)")

    def get(self, key: str):
        with self._lock:
            row = self._db.execute("SELECT value, created FROM cache WHERE key = ?", (key,)).fetchone()
        if row and time.time() - row[1] < CACHE_DAYS * 86400:
            return json.loads(row[0])
        return None

    def put(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(value), time.time()))
            self._db.commit()


class Http:
    def __init__(self, cache: Cache, min_interval: float = 0.3, timeout: float = 20):
        self.cache, self.min_interval, self.timeout = cache, min_interval, timeout
        self._last = 0.0
        self._lock = threading.Lock()

    def get_json(self, url: str, params: dict | None = None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._fetch(url, url, None)

    def post_json(self, url: str, payload: dict):
        body = json.dumps(payload, sort_keys=True)
        return self._fetch(f"{url}|{body}", url, body.encode("utf-8"))

    def _fetch(self, key: str, url: str, body: bytes | None):
        if (hit := self.cache.get(key)) is not None:
            return hit
        with self._lock:  # one request at a time per service, spaced by min_interval
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json; charset=utf-8"
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            finally:
                self._last = time.monotonic()
        self.cache.put(key, data)
        return data
