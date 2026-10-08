"""Fandom wikis (MediaWiki API): a manga volume's own title, English release year and ISBN.

Every wiki uses its own info box, so this reads the common keys: the English name first
("ename", "engname", ...), then "title" - but "Volume 1" or "Series (1)" is no title. ISBNs are
only taken when they are clearly the English edition's. Opt-in per series (Settings > Online):
    One Piece = onepiece.fandom.com
    Made in Abyss = madeinabyss.fandom.com | Volume {n}
    Sword Art Online - Kiss and Fly = swordartonline.fandom.com | Sword Art Online - Kiss and Fly Volume {nn} (manga)
{n} is the volume number, {nn} the same with a leading zero (01, 02...).
"""
from __future__ import annotations

import re

from ..core.text import fold, parse_year
from .http import Http

TITLE_KEYS = ("ename", "engname", "english", "engtitle", "eng_title", "entitle", "title_en", "etitle",
              "english_title", "title", "title1", "name")
EN_DATE_KEYS = ("erelease", "publicationeng", "english_release", "release_en", "releaseeng", "engrelease")
DATE_KEYS = EN_DATE_KEYS + ("release", "releasedate", "release_date", "publication", "date")
EN_ISBN_KEYS = ("eisbn", "english_isbn", "isbneng", "isbn_en", "engisbn", "publicationeng", "erelease")
_PARAM_RE = re.compile(r"^\s*\|\s*([\w ()]+?)\s*=\s*(.*)$")
_EN_SUFFIX_RE = re.compile(r"_(?:na|en|eng|english|us)$")  # "Release Date (NA)" -> release_date_na
_ISBN_RE = re.compile(r"97[89][\d\- ]{10,16}\d")
_ENGLISH_MARK = re.compile(r"\((?:eng?|english|na|us)\)|\ben=", re.I)


def parse_wiki_list(text: str) -> dict[str, tuple[str, str]]:
    """'One Piece = onepiece.fandom.com | Volume {n}' lines -> {fold(series): (wiki host, page pattern)}."""
    out = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        series, rest = line.split("=", 1)
        wiki, _, pattern = rest.partition("|")
        wiki = re.sub(r"^https?://", "", wiki.strip()).split("/")[0]
        if series.strip() and wiki:
            out[fold(series)] = (wiki, pattern.strip() or "Volume {n}")
    return out


def page_name(pattern: str, volume: float) -> str:
    n = f"{volume:g}"
    return pattern.replace("{nn}", n.zfill(2) if n.isdigit() else n).replace("{n}", n)


def _clean(value: str) -> str:
    v = re.sub(r"<[^>]+>|\{\{[^{}]*\}\}|'{2,}", " ", value)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)  # [[link|text]] -> text
    return re.sub(r"\s+", " ", v).strip(" -—")


def _is_generic(title: str, series: str) -> bool:
    """'Volume 1', 'Vol. 01', 'Rent a Girlfriend (1)', '#3' say nothing about the volume."""
    rest = re.sub(r"(?i)\b(?:volume|vol|tome|book)\b\.?|[\d()#.,:\-]", " ", title)
    if not fold(rest) or (series and fold(rest) in fold(series)):
        return True
    # "Sword Art Online the Movie -Ordinal Scale- 1": the series' words + a number, at most 2 others
    series_words = set(re.findall(r"[^\W\d_]+", series.lower()))
    extra = [w for w in re.findall(r"[^\W\d_]+", rest.lower()) if w not in series_words]
    return bool(re.search(r"\d+\)?\s*$", title)) and len(extra) <= 2


def _segments(value: str) -> list[str]:
    """'May 17, 2019 (JP) <br> December 28, 2021 (EN)' -> one part per edition."""
    return [s for s in re.split(r"\n|<br\s*/?>", value, flags=re.I) if s.strip()]


def _english_keys(params: dict[str, str], kind: str) -> list[str]:
    """Keys of the English edition: known names, or any 'isbn...'/'release...' key ending in NA/EN/ENG."""
    known = EN_ISBN_KEYS if kind == "isbn" else EN_DATE_KEYS
    return [k for k in params if k in known or (kind in k and _EN_SUFFIX_RE.search(k))]


def _english_isbn(params: dict[str, str]) -> str | None:
    for key in _english_keys(params, "isbn"):
        if (m := _ISBN_RE.search(params.get(key, ""))):
            return re.sub(r"[^\d]", "", m.group())
    for line in _segments(params.get("isbn", "")):  # "978-... (ENG)" next to "978-... (JP)"
        if _ENGLISH_MARK.search(line) and (m := _ISBN_RE.search(line)):
            return re.sub(r"[^\d]", "", m.group())
    return None


class Fandom:
    name = "fandom"

    def __init__(self, http: Http):
        self.http = http

    def volume(self, wiki: str, volume: float, pattern: str = "Volume {n}", series: str = "") -> dict | None:
        """{'title': str|None, 'year': int|None, 'isbn': str|None} from the volume page's info box."""
        data = self.http.get_json(f"https://{wiki}/api.php", {"action": "parse", "page": page_name(pattern, volume),
                                                             "prop": "wikitext", "format": "json",
                                                             "formatversion": 2, "redirects": 1})
        text = (data.get("parse") or {}).get("wikitext") or ""
        params = self._infobox(text)
        if not params:
            return None
        title = next((_clean(params[k]) for k in TITLE_KEYS if _clean(params.get(k, ""))), None)
        if title and _is_generic(title, series):
            title = None
        year = next((parse_year(_clean(params[k])) for k in _english_keys(params, "release")
                     if parse_year(_clean(params[k]))), None)
        if year is None:  # "October 17, 2017 (JP)\nJune 2, 2020 (ENG)" in one field
            for k in DATE_KEYS:
                lines = _segments(params.get(k, ""))
                english = [ln for ln in lines if _ENGLISH_MARK.search(ln)]
                if (y := parse_year(" ".join(english or lines[:1]))):
                    year = y
                    break
        info = {"title": title, "year": year, "isbn": _english_isbn(params)}
        return info if any(info.values()) else None

    @staticmethod
    def _infobox(text: str) -> dict[str, str]:
        """key -> raw value of the first {{...}} block that has parameters (values may span lines)."""
        params: dict[str, str] = {}
        key = None
        depth = 0
        started = False
        for line in text.splitlines():
            if not started:
                if "{{" not in line or line.strip().startswith("{{DISPLAYTITLE"):
                    continue
                started = True
            depth += line.count("{{") - line.count("}}")
            if m := _PARAM_RE.match(line):
                key = re.sub(r"[()]", "", m.group(1)).strip().lower().replace(" ", "_")
                params[key] = m.group(2)
            elif key and depth > 0:
                params[key] += "\n" + line
            if depth <= 0 and started and params:
                break
        return params

    @staticmethod
    def guess_wiki(series: str) -> str:
        return re.sub(r"[^a-z0-9]", "", series.lower())[:60] + ".fandom.com"  # a web address part is max 63
