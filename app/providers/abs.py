"""Audiobookshelf's own API (your server, your API key): read and reorder collections."""
from __future__ import annotations

import json
import urllib.request


class AbsClient:
    def __init__(self, url: str, api_key: str, timeout: float = 30):
        self.url = url.rstrip("/")  # e.g. http://localhost:13378/audiobookshelf
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.url}/api{path}", data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
            "User-Agent": "BookSorter/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read() or b"null")

    def collections(self) -> list[dict]:
        return (self._call("GET", "/collections") or {}).get("collections") or []

    def set_collection_order(self, collection_id: str, item_ids: list[str]) -> dict:
        return self._call("PATCH", f"/collections/{collection_id}", {"books": item_ids})
