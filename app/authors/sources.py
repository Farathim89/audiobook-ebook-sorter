"""Fetch an author's works: Audible (every audiobook), AniList (light novels and manga they wrote), Google Books
and OpenLibrary (ebooks / print, also the ones with no audiobook). Answers are cached like the sorter's lookups."""
from __future__ import annotations

import logging
from typing import Callable

from ..core.abs_index import AbsIndex
from ..providers.audible import TLDS
from ..providers.http import Cache, Http
from .works import Works, add_anilist, add_audible, add_books, add_yours, same_author

log = logging.getLogger(__name__)
ANILIST = "https://graphql.anilist.co"
STAFF_QUERY = """query($s:String){Page(perPage:5){staff(search:$s){name{full native alternative} siteUrl
  staffMedia(perPage:100,sort:START_DATE){edges{staffRole node{title{english romaji} format volumes status siteUrl}}}}}}"""


class AuthorLookup:
    def __init__(self, folders, google_key: str = ""):
        cache = Cache(folders.state / "cache.sqlite")
        self.folders = folders
        self.audible_http = Http(cache, min_interval=0.3)
        self.anilist_http = Http(cache, min_interval=0.8)  # AniList allows ~90 requests/minute
        self.books_http = Http(cache, min_interval=0.4)
        self.audible_api = f"https://api.audible.{TLDS.get(folders.audible_region, 'com')}/1.0/catalog/products"
        self.google_key = google_key or getattr(folders, "google_books_key", "")
        self.use_google = getattr(folders, "use_google_books", True)
        self.use_openlibrary = getattr(folders, "use_openlibrary", True)

    def run(self, author: str, progress: Callable[[int, int, str], None] = lambda *_: None,
            cancelled: Callable[[], bool] = lambda: False) -> tuple[Works, list[str]]:
        """(the works, notes about sources that failed)."""
        works, notes = Works(), []
        steps = [("Audible", self._audible), ("AniList", self._anilist)]
        if self.use_google:
            steps.append(("Google Books", self._google))
        if self.use_openlibrary:
            steps.append(("OpenLibrary", self._openlibrary))
        for i, (name, step) in enumerate(steps):
            if cancelled():
                break
            progress(i, len(steps) + 1, f"{name}…")
            try:
                step(works, author)
            except OSError as e:
                notes.append(f"{name} didn't answer ({e})")
                log.warning("%s author lookup failed: %s", name, e)
        progress(len(steps), len(steps) + 1, "Your library…")
        add_yours(works, AbsIndex.load(self.folders).books, author)
        return works, notes

    def _audible(self, works: Works, author: str) -> None:
        products = []
        for page in range(8):  # 50 a page, up to 400 audiobooks
            data = self.audible_http.get_json(self.audible_api, {
                "author": author, "num_results": 50, "page": page, "products_sort_by": "-ReleaseDate",
                "response_groups": "contributors,series,product_attrs,product_desc"}) or {}
            got = data.get("products") or []
            products += got
            if len(got) < 50:
                break
        add_audible(works, products, author)

    def _anilist(self, works: Works, author: str) -> None:
        data = self.anilist_http.post_json(ANILIST, {"query": STAFF_QUERY, "variables": {"s": author}}) or {}
        people = (((data.get("data") or {}).get("Page") or {}).get("staff")) or []
        for person in people:  # the one with this name (search also finds look-alikes)
            n = person.get("name") or {}
            if same_author(author, [n.get("full") or ""] + list(n.get("alternative") or [])):
                add_anilist(works, person)
                return

    def _google(self, works: Works, author: str) -> None:
        books = []
        for start in (0, 40, 80):
            params = {"q": f'inauthor:"{author}"', "maxResults": 40, "startIndex": start, "printType": "books",
                      "langRestrict": "en"} | ({"key": self.google_key} if self.google_key else {})
            data = self.books_http.get_json("https://www.googleapis.com/books/v1/volumes", params) or {}
            items = data.get("items") or []
            for it in items:
                v = it.get("volumeInfo") or {}
                title = v.get("title") or ""
                if v.get("subtitle") and not title.endswith(v["subtitle"]):
                    title = f"{title}: {v['subtitle']}" if "vol" not in v["subtitle"].lower() else f"{title}, {v['subtitle']}"
                books.append((title, v.get("authors") or [], v.get("publishedDate") or "", v.get("infoLink") or ""))
            if len(items) < 40:
                break
        add_books(works, books, author, "google")

    def _openlibrary(self, works: Works, author: str) -> None:
        data = self.books_http.get_json("https://openlibrary.org/search.json", {
            "author": author, "limit": 200, "language": "eng",
            "fields": "title,author_name,first_publish_year,key"}) or {}
        books = [(d.get("title") or "", d.get("author_name") or [], str(d.get("first_publish_year") or ""),
                  f"https://openlibrary.org{d.get('key')}" if d.get("key") else "") for d in data.get("docs") or []]
        add_books(works, books, author, "openlibrary")
