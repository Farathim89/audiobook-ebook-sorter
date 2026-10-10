"""Audible catalog (public, no login): title, authors, narrators, series + number, cover, summary."""
from __future__ import annotations

import html
import re

from ..core.models import Metadata
from ..core.text import clean_title, fold, parse_index, parse_year
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

    def series_numbers(self, member_asin: str, series: str) -> dict[str, float]:
        """{folded title: number} of every book in a series, from the series' own list on Audible - also
        books that aren't out as audiobooks yet (search hides those: 'The Confession Strategy' = #8).
        member_asin: any book of the series."""
        product = (self.http.get_json(f"{self.api}/{member_asin}", {"response_groups": "series"}) or {}).get("product") or {}
        want = fold(series)
        sid = next((s.get("asin") for s in product.get("series") or [] if fold(s.get("title")) == want), None)
        if not sid:
            return {}
        rel = (self.http.get_json(f"{self.api}/{sid}", {"response_groups": "relationships"}) or {}).get("product") or {}
        seq = {r["asin"]: parse_index(r.get("sequence")) for r in rel.get("relationships") or []
               if r.get("asin") and r.get("relationship_type") == "series"}
        out: dict[str, float] = {}
        asins = [a for a, n in seq.items() if n is not None]
        for i in range(0, len(asins), 50):
            data = self.http.get_json(self.api, {"asins": ",".join(asins[i:i + 50]), "response_groups": "product_desc"})
            for p in (data or {}).get("products") or []:
                if p.get("title") and p.get("asin") in seq:
                    out[fold(clean_title(p["title"]))] = seq[p["asin"]]
                    if p.get("subtitle") and len(fold(p["subtitle"])) >= 12:
                        # 'Campfire Cooking...: Volume 7' + subtitle 'Red Meat Steak and the Creator God's Judgement'
                        out.setdefault(fold(p["subtitle"]), seq[p["asin"]])
        return out

    def by_asin(self, asin: str) -> list[Candidate]:
        data = self.http.get_json(f"{self.api}/{asin}", {"response_groups": GROUPS, "image_sizes": IMAGE_SIZES})
        product = data.get("product")
        return [self._candidate(product)] if product and product.get("title") else []

    def _candidate(self, p: dict) -> Candidate:
        # 'I Had That Same Dream Again' (no number) + 'The Yoru Sumino Collection #3': the numbered one;
        # 'Wizarding World #5' + 'Harry Potter #5': the one the title names
        listed = p.get("series") or [{}]
        numbered = [s for s in listed if parse_index(s.get("sequence")) is not None]
        title = fold(p.get("title"))
        series = next((s for s in numbered if fold(s.get("title")) and fold(s.get("title")) in title),
                      numbered[0] if numbered else listed[0])
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
