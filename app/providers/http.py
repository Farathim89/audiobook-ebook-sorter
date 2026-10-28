"""Tiny cached JSON-over-HTTP client (stdlib only)."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Shelfkeeper/0.4 (+personal library tool)"
CACHE_DAYS = 30
RETRY_CODES = (500, 502, 503, 504)  # the server's hiccup, not a wrong ask
RETRIES, RETRY_WAIT = 2, 1.5


class Cache:
    """URL -> JSON response, kept for `days` (CACHE_DAYS). Failed requests are never cached."""

    def __init__(self, path: Path | None, days: float = CACHE_DAYS):
        self.days = days  # the Collection page: 7 (release dates change)
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
        if row and time.time() - row[1] < self.days * 86400:
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
        key = url
        if params:
            # the cache never holds an API key ('key' = Google Books): it would sit in cache.sqlite on disk
            key = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if k != 'key'})}"
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._fetch(key, url, None)

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
            try:
                for attempt in range(RETRIES + 1):
                    req = urllib.request.Request(url, data=body, headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                            data = json.loads(resp.read().decode("utf-8"))
                        break
                    except urllib.error.HTTPError as e:
                        # 'service unavailable' now and then (Google Books often): the same ask works a moment later
                        if e.code not in RETRY_CODES or attempt == RETRIES:
                            raise
                        time.sleep(RETRY_WAIT * (attempt + 1))
            except (ValueError, http.client.HTTPException) as e:
                # not JSON / cut off mid-answer: the same as no answer for every caller (they catch OSError)
                raise OSError(f"bad answer from {urllib.parse.urlsplit(url).netloc}: {type(e).__name__}") from e
            finally:
                self._last = time.monotonic()
        self.cache.put(key, data)
        return data
