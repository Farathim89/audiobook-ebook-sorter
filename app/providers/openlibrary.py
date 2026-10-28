"""Open Library (public, no key): author, first year, ISBN, cover; the description comes from the work."""
from __future__ import annotations

from ..core.models import Metadata
from ..core.text import clean_title, split_people
from . import Candidate
from .http import Http

SITE = "https://openlibrary.org"
FIELDS = "key,title,subtitle,author_name,first_publish_year,isbn,cover_i,language,publisher"


class OpenLibrary:
    name = "openlibrary"

    def __init__(self, http: Http):
        self.http = http

    def search(self, title: str, author: str | None = None) -> list[Candidate]:
        params = {"title": title, "limit": 10, "fields": FIELDS}
        if author:
            params["author"] = author
        data = self.http.get_json(f"{SITE}/search.json", params)
        return [self._candidate(d) for d in data.get("docs") or [] if d.get("title")]

    def _candidate(self, d: dict) -> Candidate:
        isbns = d.get("isbn") or []
        langs = d.get("language") or []
        # a work lists the ISBNs of all its editions (translations too): only one edition's (its ISBN-10 /
        # ISBN-13 pair) is surely this book's - else none, so no foreign ISBN / cover is taken from it
        isbn = next((i for i in isbns if len(i) == 13), isbns[0] if isbns else None) \
            if len(isbns) <= 2 and len(langs) <= 1 else None
        meta = Metadata(
            title=clean_title(d.get("title")),
            authors=[a for name in (d.get("author_name") or [])[:3] for a in split_people(name)],
            year=d.get("first_publish_year"),
            isbn=isbn,
            language=langs[0] if len(langs) == 1 else None,  # many languages = a work with translations
            publisher=(d.get("publisher") or [None])[0],
            cover_url=f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get("cover_i") else None,
        )
        return Candidate(meta, self.name, f"{SITE}{d.get('key', '')}")

    def add_description(self, cand: Candidate) -> None:
        """One more request, only for the match we use: the work's description."""
        if cand.meta.description or "/works/" not in cand.url:
            return
        data = self.http.get_json(f"{cand.url}.json")
        desc = data.get("description")
        if isinstance(desc, dict):
            desc = desc.get("value")
        cand.meta.description = (desc or "").strip() or None
