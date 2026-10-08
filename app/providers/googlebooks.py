"""Google Books: author, publisher, year, ISBN, description, cover.

Without an API key Google's shared quota is often used up (HTTP 429); then this source is skipped
for the rest of the run. A free key (Google Cloud console, "Books API") can be set in Settings.
"""
from __future__ import annotations

import urllib.error

from ..core.models import Metadata
from ..core.text import clean_title, parse_year
from . import Candidate
from .audible import _strip_html
from .http import Http

API = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooks:
    name = "googlebooks"

    def __init__(self, http: Http, api_key: str = ""):
        self.http, self.api_key = http, api_key
        self.blocked = False  # rate-limited: don't ask again this run

    def search(self, title: str, author: str | None = None) -> list[Candidate]:
        if self.blocked:
            return []
        q = f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else "")
        params = {"q": q, "maxResults": 10, "printType": "books"}
        if self.api_key:
            params["key"] = self.api_key
        try:
            data = self.http.get_json(API, params)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                self.blocked = True
                return []
            raise
        return [self._candidate(i) for i in data.get("items") or [] if (i.get("volumeInfo") or {}).get("title")]

    def _candidate(self, item: dict) -> Candidate:
        v = item.get("volumeInfo") or {}
        ids = {i.get("type"): i.get("identifier") for i in v.get("industryIdentifiers") or []}
        img = (v.get("imageLinks") or {}).get("thumbnail") or (v.get("imageLinks") or {}).get("smallThumbnail")
        meta = Metadata(
            title=clean_title(v.get("title")),
            authors=list(v.get("authors") or []),
            publisher=v.get("publisher") or None,
            year=parse_year(v.get("publishedDate")),
            isbn=ids.get("ISBN_13") or ids.get("ISBN_10"),
            language=v.get("language") or None,
            description=_strip_html(v.get("description")),
            cover_url=img.replace("http://", "https://").replace("&edge=curl", "") if img else None,
        )
        return Candidate(meta, self.name, v.get("infoLink") or "")
