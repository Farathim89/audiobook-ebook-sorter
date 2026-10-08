"""AniList (port of the PowerShell sorter's lookup): manga / light-novel author, description, cover.

Accepted only when an AniList title or synonym equals the series name exactly.
Author = the ORIGINAL author: 'Original Story' > 'Original Creator' > 'Story & Art' > 'Story'
(otherwise adaptations would get the manga artist).
"""
from __future__ import annotations

import re

from ..core.models import Metadata
from ..core.text import fold, name_key
from . import Candidate
from .audible import _strip_html
from .http import Http

API = "https://graphql.anilist.co"
QUERY = ("query($s:String){Page(perPage:8){media(search:$s,type:MANGA){id format siteUrl "
         "description(asHtml:false) title{romaji english} synonyms coverImage{extraLarge} "
         "staff(perPage:15){edges{role node{name{full}}}}}}}")
AUTHOR_ROLES = ("Original Story", "Original Creator", "Story & Art", "Story")


class AniList:
    name = "anilist"

    def __init__(self, http: Http):
        self.http = http

    def series(self, series: str, novel: bool, prefix_ok: bool = False,
               authors: list[str] | None = None) -> Candidate | None:
        """The series' exact match, or None. novel=True looks for the light novel, else the manga.

        prefix_ok: a long name (20+ letters) may also be the start of the AniList title - for a
        shortened folder name like "7th Time Loop - The Villainess Enjoys a Carefree Life".
        The main title before a colon also counts ('Hell Mode' = 'Hell Mode: The Hardcore Gamer...'),
        but only when one of `authors` is the AniList author too - or, with no authors known, when
        AniList has just that one match."""
        data = self.http.post_json(API, {"query": QUERY, "variables": {"s": series}})
        want = fold(series)

        def same(name: str) -> bool:
            got = fold(name)
            return got == want or (prefix_ok and len(want) >= 20 and got.startswith(want))

        main_matches = []  # (media, author) whose main title (before ':') is the series
        for media in (data.get("data") or {}).get("Page", {}).get("media") or []:
            if novel != (media.get("format") == "NOVEL"):
                continue
            t = media.get("title") or {}
            names = [t.get("english"), t.get("romaji"), *(media.get("synonyms") or [])]
            exact = any(same(n) for n in names if n)
            if not exact and not any(fold(re.split(r"\s*[:：]\s*", n)[0]) == want for n in names if n):
                continue
            author = _original_author(media)
            if not author:
                continue
            if exact:
                return self._candidate(media, author, series, exact=True)
            main_matches.append((media, author))
        if authors:  # only the main title matches: the author must be the same
            for media, author in main_matches:
                if _same_person(author, authors):
                    return self._candidate(media, author, series)
            return None
        if len(main_matches) == 1:  # no author to compare: only when AniList has just this one
            return self._candidate(*main_matches[0], series)
        return None

    def _candidate(self, media: dict, author: str, series: str, exact: bool = False) -> Candidate:
        t = media.get("title") or {}
        meta = Metadata(
            authors=[author],
            series=t.get("english") or t.get("romaji") or series,
            description=_strip_html(media.get("description")),
            cover_url=(media.get("coverImage") or {}).get("extraLarge"),
        )
        return Candidate(meta, self.name, media.get("siteUrl") or "", score=1.0, confident=True, exact=exact)


def _same_person(author: str, authors: list[str]) -> bool:
    from difflib import SequenceMatcher  # noqa: PLC0415
    a = name_key(author)
    return any(name_key(x) == a or SequenceMatcher(None, name_key(x), a).ratio() >= 0.9 for x in authors)


def _original_author(media: dict) -> str | None:
    edges = ((media.get("staff") or {}).get("edges")) or []
    for role in AUTHOR_ROLES:
        for e in edges:
            if re.match(re.escape(role) + r"\b", e.get("role") or ""):
                return ((e.get("node") or {}).get("name") or {}).get("full")
    return None
