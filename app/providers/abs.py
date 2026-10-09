"""Audiobookshelf's own API (your server, your API key): collections, library items, listening progress."""
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
            raw = resp.read()
        try:
            return json.loads(raw or b"null")
        except ValueError:
            return raw.decode("utf-8", "replace")  # some calls answer just 'OK'

    def me(self) -> dict:
        return self._call("GET", "/me") or {}

    def libraries(self) -> list[dict]:
        return (self._call("GET", "/libraries") or {}).get("libraries") or []

    def library_items(self, library_id: str) -> list[dict]:
        out, page = [], 0
        while True:
            res = self._call("GET", f"/libraries/{library_id}/items?limit=500&page={page}&minified=0") or {}
            out += res.get("results") or []
            page += 1
            if page * 500 >= (res.get("total") or 0):
                return out

    def set_progress(self, item_id: str, body: dict) -> None:
        self._call("PATCH", f"/me/progress/{item_id}", body)

    def collections(self) -> list[dict]:
        return (self._call("GET", "/collections") or {}).get("collections") or []

    def set_collection_order(self, collection_id: str, item_ids: list[str]) -> dict:
        return self._call("PATCH", f"/collections/{collection_id}", {"books": item_ids})
