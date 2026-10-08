"""Storytel (public search, no login): Swedish/Nordic audiobooks - author, narrator, series, length."""
from __future__ import annotations

from ..core.models import Metadata
from ..core.text import clean_title, parse_index, parse_year
from . import Candidate
from .audible import _name, _strip_html
from .http import Http

SITE = "https://www.storytel.com"
LANGUAGES = {"sv": "Swedish", "no": "Norwegian", "nb": "Norwegian", "da": "Danish", "fi": "Finnish",
             "en": "English", "de": "German", "pl": "Polish", "nl": "Dutch", "es": "Spanish", "is": "Icelandic"}


class Storytel:
    name = "storytel"

    def __init__(self, http: Http, locale: str = "se"):
        self.http, self.locale = http, locale

    def search(self, query: str) -> list[Candidate]:
        data = self.http.get_json(f"{SITE}/api/search.action", {"request_locale": self.locale, "q": query})
        return [c for item in data.get("books") or [] if (c := self._candidate(item))]

    def _candidate(self, item: dict) -> Candidate | None:
        book, abook = item.get("book") or {}, item.get("abook")
        if not abook or not book.get("name"):
            return None  # e-book only
        series = (book.get("series") or [{}])[0] or {}
        iso = ((book.get("language") or {}).get("isoValue") or "").lower()
        cover = book.get("largeCover") or book.get("cover")
        meta = Metadata(
            title=clean_title(book.get("name")),
            authors=[_name(a["name"]) for a in book.get("authors") or [] if a.get("name")],
            narrators=[_name(n["name"]) for n in abook.get("narrators") or [] if n.get("name")],
            series=series.get("name") or None,
            series_index=parse_index(book.get("seriesOrder")) if series.get("name") else None,
            year=parse_year(abook.get("releaseDateFormat")),
            isbn=abook.get("isbn") or None,
            publisher=(abook.get("publisher") or {}).get("name") or None,
            language=LANGUAGES.get(iso, iso or None),
            description=_strip_html(abook.get("description")),
            cover_url=f"{SITE}{cover}" if cover and cover.startswith("/") else cover,
        )
        if not meta.series_index:  # Storytel uses 0 for "no number"
            meta.series_index = None
        cand = Candidate(meta, self.name, f"{SITE}{book.get('href', '')}")
        cand.length = (abook.get("length") or 0) / 1000  # seconds, for the duration check
        return cand
