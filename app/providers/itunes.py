"""Apple Books / iTunes audiobooks (public search API, no key): title, author, year, description, cover."""
from __future__ import annotations

from ..core.models import Metadata
from ..core.text import clean_title, parse_year, split_people
from . import Candidate
from .audible import _strip_html
from .http import Http

API = "https://itunes.apple.com/search"
COUNTRIES = {"us": "us", "uk": "gb", "ca": "ca", "au": "au", "de": "de", "fr": "fr", "it": "it", "es": "es",
             "jp": "jp", "in": "in"}


class ITunes:
    name = "itunes"

    def __init__(self, http: Http, region: str = "us"):
        self.http, self.country = http, COUNTRIES.get(region, "us")

    def search(self, title: str, author: str | None = None) -> list[Candidate]:
        term = f"{title} {author or ''}".strip()
        data = self.http.get_json(API, {"term": term, "media": "audiobook", "entity": "audiobook",
                                        "limit": 15, "country": self.country})
        return [self._candidate(r) for r in data.get("results") or [] if r.get("collectionName")]

    def _candidate(self, r: dict) -> Candidate:
        art = r.get("artworkUrl100") or ""
        meta = Metadata(
            title=clean_title(r.get("collectionName")),
            authors=split_people(r.get("artistName")),
            year=parse_year(r.get("releaseDate")),
            description=_strip_html(r.get("description")),
            cover_url=art.replace("100x100bb", "1000x1000bb") or None,
        )
        return Candidate(meta, self.name, r.get("collectionViewUrl") or "")
