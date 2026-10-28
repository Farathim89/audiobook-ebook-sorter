"""Score online candidates against what we know about a book.

Strict on purpose (lesson from Chaptarr/Calibre): only an exact title with a
matching author is applied automatically; everything else waits for the user.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..core.models import Book
from ..core.text import fold
from . import Candidate

AUTO_TITLE = 0.97
AUTO_AUTHOR = 0.9

_LANG_CODES = {
    "en": "english", "eng": "english", "de": "german", "ger": "german", "deu": "german",
    "sv": "swedish", "swe": "swedish", "fr": "french", "fre": "french", "fra": "french",
    "es": "spanish", "spa": "spanish", "it": "italian", "ita": "italian", "ja": "japanese",
    "jpn": "japanese", "nl": "dutch", "nld": "dutch", "dut": "dutch", "no": "norwegian",
    "nb": "norwegian", "nor": "norwegian", "da": "danish", "dan": "danish", "fi": "finnish", "fin": "finnish",
}


def norm_language(lang: str | None) -> str:
    """'en', 'eng', 'en-US', 'English' -> 'english'."""
    s = (lang or "").strip().casefold().replace("_", "-").split("-")[0]
    if s in ("unknown", "und", "zxx", "mul", "none"):
        return ""
    return _LANG_CODES.get(s, s)


def norm_title(text: str | None) -> str:
    """Ignore volume words and leading zeros: 'Vol. 01' == 'Volume 1' == '1'."""
    s = re.sub(r"(?i)\b(?:volume|vol|book|part|tome)\b\.?", " ", text or "")
    s = re.sub(r"\b0+(\d)", r"\1", s)
    return fold(s)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def title_score(want: str | None, cand: Candidate) -> float:
    w, title = norm_title(want), cand.meta.title or ""
    scores = [_ratio(w, norm_title(title))]
    if subtitle := getattr(cand.meta, "subtitle", None):
        # our tag 'All the Dust That Falls Two: An Isekai LitRPG Adventure' = the shop's title + subtitle
        scores.append(_ratio(w, norm_title(f"{title}: {subtitle}")))
    if ":" in title:  # "7th Time Loop: The Villainess Enjoys..., Vol. 1" is "7th Time Loop"
        # sure only for a numbered volume of a long series name - 'Mistborn' is not 'Mistborn: Secret History'
        cap = AUTO_TITLE if _TITLE_VOLUME_RE.search(title) else AUTO_TITLE - 0.02
        scores.append(min(_ratio(w, norm_title(title.split(":", 1)[0])), cap))
    if cand.meta.series:
        scores.append(_ratio(w, norm_title(f"{cand.meta.series} {title}")) * 0.95)
    return max(scores)


def author_score(authors: list[str], narrators: list[str], cand: Candidate) -> float:
    """Best name match. Rips often swap author and narrator tags, so a crossed match counts too.
    A shop's 'zaifyr zaifyr' (a one-word pen name in both name fields) is our 'zaifyr'."""
    from ..readers.names import single_name  # noqa: PLC0415
    if not authors and not narrators:
        return 0.5  # unknown: neither helps nor rules out
    shop_authors = [n for a in cand.meta.authors for n in {a, single_name(a)}]
    pairs = [(a, b) for a in authors for b in shop_authors]
    pairs += [(a, b) for a in authors for b in cand.meta.narrators]
    pairs += [(a, b) for a in narrators for b in shop_authors]
    return max((_ratio(fold(a), fold(b)) for a, b in pairs), default=0.0)


_TITLE_VOLUME_RE = re.compile(r"(?i)\b(?:volume|vol\.?|book|part)\s*0*(\d+(?:\.\d+)?)\s*$")


def _volume_in(cand: Candidate) -> float | None:
    """The candidate's volume: its series number, else 'Volume 2' at the end of its title/subtitle."""
    if cand.meta.series_index is not None:
        return cand.meta.series_index
    for text in (cand.meta.title, getattr(cand.meta, "subtitle", None)):
        if text and (hit := _TITLE_VOLUME_RE.search(text)):
            return float(hit.group(1))
    return None


def rank(book: Book, candidates: list[Candidate], language: str = "English") -> list[Candidate]:
    """Best first. A translation (German/French edition...) is never applied automatically
    unless the book itself is known to be in that language."""
    m = book.meta
    want_lang = norm_language(m.language) or norm_language(language)
    for c in candidates:
        t, a = title_score(m.title, c), author_score(m.authors, m.narrators, c)
        lang_ok = not c.meta.language or not want_lang or norm_language(c.meta.language) == want_lang
        c.score = round(0.7 * t + 0.3 * a - (0 if lang_ok else 0.1), 3)
        # 'Volume 2' vs our volume 1 scores 99% on the title alone: the number must agree
        vol = _volume_in(c)
        same_number = m.series_index is None or vol is None or m.series_index == vol
        same_asin = bool(m.asin and c.meta.asin and m.asin.upper() == c.meta.asin.upper())
        c.confident = (t >= AUTO_TITLE or same_asin) and a >= AUTO_AUTHOR and same_number and lang_ok
    return sorted(candidates, key=lambda c: (c.confident, c.score), reverse=True)
