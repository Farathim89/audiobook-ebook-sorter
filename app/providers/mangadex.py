"""MangaDex (public API, no key): the official cover of each manga volume."""
from __future__ import annotations

from ..core.text import fold
from .http import Http

API = "https://api.mangadex.org"
UPLOADS = "https://uploads.mangadex.org/covers"
LOCALE_ORDER = ("en", "ja")  # English edition cover first, then the Japanese original


class MangaDex:
    name = "mangadex"

    def __init__(self, http: Http):
        self.http = http

    def find(self, series: str) -> str | None:
        """The manga's id when a title or alternative title equals the series name exactly."""
        data = self.http.get_json(f"{API}/manga", {"title": series, "limit": 10, "order[relevance]": "desc"})
        want = fold(series)
        for m in data.get("data") or []:
            a = m.get("attributes") or {}
            names = list((a.get("title") or {}).values())
            names += [v for alt in a.get("altTitles") or [] for v in alt.values()]
            if any(fold(n) == want for n in names):
                return m.get("id")
        return None

    def volume_cover(self, series: str, volume: float, english_only: bool = False) -> str | None:
        """The volume's cover - the English edition's first. english_only: None when MangaDex only has
        the Japanese original (the book's own English cover is better then)."""
        manga_id = self.find(series)
        if not manga_id:
            return None
        data = self.http.get_json(f"{API}/cover", {"manga[]": manga_id, "limit": 100, "order[volume]": "asc"})
        want = f"{volume:g}"
        covers = [c.get("attributes") or {} for c in data.get("data") or []]
        same = [c for c in covers if (c.get("volume") or "").lstrip("0") == want.lstrip("0") and c.get("fileName")]
        if english_only:
            same = [c for c in same if c.get("locale") == "en"]
        if not same:
            return None
        same.sort(key=lambda c: LOCALE_ORDER.index(c.get("locale")) if c.get("locale") in LOCALE_ORDER else 9)
        return f"{UPLOADS}/{manga_id}/{same[0]['fileName']}"
