"""Audible catalog (public, no login): title, authors, narrators, series + number, cover, summary."""
from __future__ import annotations

import html
import re

from ..core.models import Metadata
from ..core.text import clean_title, parse_index, parse_year
from . import Candidate
from .http import Http

TLDS = {"us": "com", "uk": "co.uk", "ca": "ca", "au": "com.au", "de": "de", "fr": "fr",
        "it": "it", "es": "es", "jp": "co.jp", "in": "in"}
LANGUAGES = {"de": "German", "fr": "French", "it": "Italian", "es": "Spanish", "jp": "Japanese"}
GROUPS = "contributors,series,product_attrs,product_desc,media"
IMAGE_SIZES = "500,1024,2400"  # without this Audible only returns the 500 px cover
# Audible lists translators/illustrators as authors: "Mana Z - translator", "X - Translated by".
_ROLE_RE = re.compile(r"\s-\s.+$")


def _name(name: str) -> str:
    """'MOJIKAKIYA' -> 'Mojikakiya', so one author doesn't become two in Audiobookshelf."""
    name = name.strip()
    return name.title() if name.isupper() and len(name) > 3 else name


class Audible:
    name = "audible"

    def __init__(self, http: Http, region: str = "us"):
        self.http = http
        self.region = region
        self.language = LANGUAGES.get(region, "English")  # what an untagged book is assumed to be in
        self.api = f"https://api.audible.{TLDS.get(region, 'com')}/1.0/catalog/products"

    def search(self, title: str | None = None, author: str | None = None,
               narrator: str | None = None, keywords: str | None = None) -> list[Candidate]:
        params = {"num_results": 25, "products_sort_by": "Relevance", "response_groups": GROUPS,
                  "image_sizes": IMAGE_SIZES}
        for key, value in (("title", title), ("author", author), ("narrator", narrator), ("keywords", keywords)):
            if value:
                params[key] = value
        data = self.http.get_json(self.api, params)
        return [self._candidate(p) for p in data.get("products") or [] if p.get("title")]

    def by_asin(self, asin: str) -> list[Candidate]:
        data = self.http.get_json(f"{self.api}/{asin}", {"response_groups": GROUPS, "image_sizes": IMAGE_SIZES})
        product = data.get("product")
        return [self._candidate(product)] if product and product.get("title") else []

    def _candidate(self, p: dict) -> Candidate:
        series = (p.get("series") or [{}])[0]
        images = p.get("product_images") or {}
        meta = Metadata(
            title=clean_title(p.get("title")),
            subtitle=(p.get("subtitle") or "").strip() or None,
            authors=[_name(a["name"]) for a in p.get("authors") or [] if a.get("name") and not _ROLE_RE.search(a["name"])],
            narrators=[_name(n["name"]) for n in p.get("narrators") or [] if n.get("name")
                       and not _ROLE_RE.search(n["name"])],
            series=series.get("title") or None,
            series_index=parse_index(series.get("sequence")),
            year=parse_year(p.get("release_date") or p.get("issue_date")),
            asin=p.get("asin"),
            publisher=p.get("publisher_name") or None,
            language=(p.get("language") or "").capitalize() or None,
            description=_strip_html(p.get("publisher_summary") or p.get("merchandising_summary")),
            cover_url=images.get("2400") or images.get("1024") or images.get("500") or next(iter(images.values()), None),
        )
        if not meta.series:
            meta.series_index = None
        url = f"https://www.audible.{TLDS.get(self.region, 'com')}/pd/{p.get('asin')}"
        return Candidate(meta, self.name, url, length=float(p.get("runtime_length_min") or 0) * 60)


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None
