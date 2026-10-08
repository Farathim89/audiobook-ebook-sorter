"""Guess metadata from file and folder names (the fallback when there are no tags)."""
from __future__ import annotations

import re

from ..core.models import BookType, Metadata
from ..core.text import clean_title, fold, parse_index, same_text, split_people

_SEP = r"\s+[-–—]\s+"
_IDX = r"(?P<idx>\d{1,3}(?:\.\d+)?)"
_PREFIX = r"(?:#|book\s+|bk\.?\s*|vol\.?\s*|volume\s+|no\.?\s*)"

_BOOK_PATTERNS = [re.compile(p, re.I) for p in (
    # Author - [Series 3] - Title   /   Author - (Series #3) - Title
    rf"^(?P<author>.+?){_SEP}[\[\(](?P<series>[^\]\)]+?),?\s*{_PREFIX}?{_IDX}[\]\)]{_SEP}(?P<title>.+)$",
    # Author - Series 03 - Title   /   Author - Series, Book 3 - Title
    rf"^(?P<author>.+?){_SEP}(?P<series>.+?),?\s+{_PREFIX}?{_IDX}{_SEP}(?P<title>.+)$",
    # Title (Series #3) - Author   /   Title (Series, Book 3)
    rf"^(?P<title>.+?)\s*[\[\(](?P<series>[^\]\)]+?),?\s+{_PREFIX}{_IDX}[\]\)](?:{_SEP}(?P<author>.+))?$",
    # Series 03 - Title
    rf"^(?P<series>.+?)\s+{_PREFIX}?{_IDX}{_SEP}(?P<title>.+)$",
    # 03 - Title   /   03. Title   /   Book 3 Title
    rf"^{_PREFIX}?{_IDX}(?:{_SEP}|\.\s+|\s+)(?P<title>\D.*)$",
    # Author - Title
    rf"^(?P<author>.+?){_SEP}(?P<title>.+)$",
    # Title by Author Name  (only reached when there is no " - ")
    r"^(?P<title>.+?)\s+by\s+(?P<author>\S+(?:\s+\S+)+)$",
)]

_YEAR_IN_BRACKETS_RE = re.compile(r"[\(\[]\s*((?:1[5-9]|20)\d\d)\s*[\)\]]")
# Swedish / Nordic: '(Björn Kjellman läser)', '(uppläsare: X)', '(läst av X)', '(indlæst af X)', '(lest av X)'
_READS_RE = re.compile(r"[\(\[]\s*(?:(?:uppläsare|läst av|inläst av|indlæst af|lest av|oppleser)[:\s]+([^\)\]]+?)"
                       r"|([^\)\]]+?)\s+(?:läser|læser|leser))\s*[\)\]]", re.I)
_NARRATOR_RE = re.compile(r"[\(\[]?\s*(?:read\s+by|narrated\s+by|narr\.)\s+([^\)\]]+?)\s*(?:[\)\]]|$)", re.I)
_RELEASE_TAIL_RE = re.compile(r"\s*\([^()]*\)\s*$")

# From the PowerShell sorter: "Series: Volume 01", "Series, Vol. 2", "Series - Book 3", "Series v05"...
_VOLUME_PATTERNS = [re.compile(p, re.I) for p in (
    r"^(?P<s>.+?)[\s:,\-]+(?:vol(?:ume)?\.?|book|part|tome)\s*(?P<n>\d+(?:\.\d+)?)\s*(?:[:\-]\s*.+)?$",
    r"^(?P<s>.+?)\s+v(?P<n>\d{1,3}(?:\.\d+)?)\b.*$",
)]
_TRAILING_NUMBER_RE = re.compile(r"^(?P<s>.*\D)\s+#?(?P<n>\d{1,3}(?:\.\d+)?)$")

# "The Way of Kings: The Stormlight Archive, Book 1" / "Tehanu (The Earthsea Cycle, Book 4)"
# / "Earthsea Cycle Book 4 - Tehanu"
_TITLE_SERIES_RES = [re.compile(p, re.I) for p in (
    r"^(?P<title>.+?)\s*(?::|\s[-–—])\s*(?P<series>[^:]+?),?\s+(?:book|volume|vol\.?|#)\s*(?P<idx>\d+(?:\.\d+)?)$",
    r"^(?P<title>.+?)\s*\((?P<series>[^()]+?),?\s+(?:book|volume|vol\.?|#)\s*(?P<idx>\d+(?:\.\d+)?)\)$",
    r"^(?P<series>.+?),?\s+(?:book|volume|vol\.?|#)\s*(?P<idx>\d+(?:\.\d+)?)\s*(?::|\s[-–—])\s*(?P<title>.+)$",
)]

# Folder names that never are an author or series.
GENERIC_DIRS = {
    "audiobooks", "audiobook", "audio books", "books", "ebooks", "ebook", "e-books", "comics",
    "manga", "downloads", "download", "torrents", "complete", "completed", "incoming", "new folder",
    "unsorted", "misc", "library", "media", "mp3", "m4b", "epub", "pdf", "done",
}


def parse_name(text: str, book_type: BookType) -> Metadata:
    if book_type is BookType.COMIC:
        return _parse_comic(text)
    s = text.replace("_", " ")
    if " " not in s and s.count(".") >= 2:
        s = s.replace(".", " ")  # Some.Release.Name

    meta = Metadata()
    if m := _NARRATOR_RE.search(s) or _READS_RE.search(s):
        meta.narrators = split_people(next(g for g in m.groups() if g))
        s = s[:m.start()] + s[m.end():]
    if m := _YEAR_IN_BRACKETS_RE.search(s):
        meta.year = int(m.group(1))
        s = s[:m.start()] + s[m.end():]
    s = clean_title(s) or ""

    for pattern in _BOOK_PATTERNS:
        if m := pattern.match(s):
            g = m.groupdict()
            meta.title = clean_title(g.get("title"))
            meta.authors = split_people(g.get("author"))
            meta.series = clean_title(g.get("series"))
            meta.series_index = parse_index(g.get("idx"))
            return meta
    meta.title = clean_title(s)
    return meta


def strip_release(text: str) -> str:
    """'Made in Abyss v01 (2018) (Digital) (LuCaZ) [f]' -> 'Made in Abyss v01'."""
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", "", text.replace("_", " "))
    while (new := _RELEASE_TAIL_RE.sub("", s).strip()) != s:
        s = new
    return s.strip(" -_.")


def parse_volume(title: str, trailing_number: bool = False) -> tuple[str, float] | None:
    patterns = _VOLUME_PATTERNS + ([_TRAILING_NUMBER_RE] if trailing_number else [])
    for pattern in patterns:
        if m := pattern.match(title):
            series = m.group("s").strip(" :,-")
            if series:
                return series, float(m.group("n"))
    return None


def split_series_from_title(title: str) -> tuple[str, str, float] | None:
    """('The Way of Kings', 'The Stormlight Archive', 1.0) or None."""
    for pattern in _TITLE_SERIES_RES:
        if m := pattern.match(title):
            return m.group("title").strip(), m.group("series").strip(), float(m.group("idx"))
    return None


def _parse_comic(text: str) -> Metadata:
    meta = Metadata()
    if m := _YEAR_IN_BRACKETS_RE.search(text):
        meta.year = int(m.group(1))
    s = strip_release(text)
    if vol := parse_volume(s, trailing_number=True):
        meta.series, meta.series_index = vol
    else:
        meta.title = s or None
    return meta


def context_from_dirs(dirs: list[str], book_type: BookType, meta: Metadata) -> Metadata:
    """Author (and series) from the folders above a book: Incoming\\<Author>\\<Series>\\book.

    Release-looking folders ("Title (2019) [MP3]") and folders named after the
    book or series itself are skipped, so they never become the author.
    """
    names = []
    for d in dirs:
        name = re.sub(r"\s*\(\d+\)$", "", d).strip()  # Calibre's "Title (123)"
        if name and fold(name) not in {fold(g) for g in GENERIC_DIRS}:
            names.append(name)
    names = [n for n in names if not (same_text(n, meta.title) or same_text(n, meta.series))]
    out = Metadata()
    if not names:
        return out

    if book_type is BookType.COMIC:
        # Only a folder the user made at the top of Incoming counts, as the author.
        top = names[0]
        if not _looks_like_release(top) and not (meta.series and fold(meta.series) in fold(top)):
            out.authors = [top]
        return out

    candidates = [n for n in names[-2:] if not _looks_like_release(n)]
    if len(candidates) == 2:
        out.authors = split_people(candidates[0])
        out.series = clean_title(candidates[1])
    elif candidates:
        out.authors = split_people(candidates[0])
    return out


def _looks_like_release(name: str) -> bool:
    return bool(re.search(r"[\(\[]", name)) or bool(re.search(_SEP, name))
