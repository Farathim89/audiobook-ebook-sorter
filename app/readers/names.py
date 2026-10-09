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
    bare = re.sub(r"\s*\[[^\]]*\]", "", s).strip()  # release tags: '[J-Novel Club][Premium]'
    if m := _SERIES_DASH_VOLUME_RE.match(words_to_numbers(bare)):
        # '<Series> - Volume 12': the part before the dash is the series, not an author
        meta.series = clean_title(m.group("series"))
        meta.series_index = parse_index(m.group("idx"))
        meta.title = clean_title(m.group("title")) or f"{meta.series}, Vol. {m.group('idx').lstrip('0') or '0'}"
        return meta

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


_SERIES_DASH_VOLUME_RE = re.compile(
    r"(?i)^(?P<series>.+?)\s+[-–—]\s+(?:vol(?:ume)?\.?|book|tome)\s*(?P<idx>\d+(?:\.\d+)?)"
    r"(?:\s+[-–—]\s+(?P<title>.+))?$")


def strip_release(text: str) -> str:
    """'Made in Abyss v01 (2018) (Digital) (LuCaZ) [f]' -> 'Made in Abyss v01'."""
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", "", text.replace("_", " "))
    while (new := _RELEASE_TAIL_RE.sub("", s).strip()) != s:
        s = new
    return s.strip(" -_.")


_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_WORD_VOLUME_RE = re.compile(r"(?i)\b(vol(?:ume)?\.?|book|part|tome)\s+(" + "|".join(_NUMBER_WORDS) + r")\b")


def single_name(name: str) -> str:
    """'Twoony Twoony' -> 'Twoony' (a shop puts a one-word pen name in both name fields)."""
    m = re.fullmatch(r"\s*(\S+)\s+(\S+)\s*", name or "")
    return m.group(1) if m and m.group(1).casefold() == m.group(2).casefold() else name


def words_to_numbers(title: str) -> str:
    """'Constellations Volume One' -> 'Constellations Volume 1'."""
    return _WORD_VOLUME_RE.sub(lambda m: f"{m.group(1)} {_NUMBER_WORDS[m.group(2).lower()]}", title)


def parse_volume(title: str, trailing_number: bool = False) -> tuple[str, float] | None:
    title = words_to_numbers(title)
    patterns = _VOLUME_PATTERNS + ([_TRAILING_NUMBER_RE] if trailing_number else [])
    for pattern in patterns:
        if m := pattern.match(title):
            series = m.group("s").strip(" :,-")
            if series:
                return series, float(m.group("n"))
    return None


def split_series_from_title(title: str) -> tuple[str, str, float] | None:
    """('The Way of Kings', 'The Stormlight Archive', 1.0) or None."""
    title = words_to_numbers(title)
    for pattern in _TITLE_SERIES_RES:
        if m := pattern.match(title):
            if _PART_OF_NAME_RE.match(m.group("series").strip()):
                return None  # 'Classroom of the Elite: Year 2 Vol. 6': 'Year 2' belongs to the series name
            return m.group("title").strip(), m.group("series").strip(), float(m.group("idx"))
    return None


# 'Year 2', 'Part 3', 'Season 1': a part of a series' name, not a series of its own
_PART_OF_NAME_RE = re.compile(r"(?i)^(?:year|part|season|arc|phase|stage|act|chapter|cour)\s*\d+$")


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
