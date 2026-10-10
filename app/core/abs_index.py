"""What your Audiobookshelf already has - read once per scan / lookup (it takes a fraction of a second):

    find_same(book)       the copy you already have: same ASIN, else same author + title, else same series number
    series_name(...)      the series as ABS spells it ('Is It Wrong to Try to Pick Up Girls in a Dungeon?' - with
                          the '?' a folder name can't hold)
    narrators_for(...)    who reads the other volumes of a series
    owned_numbers(...)    which volumes of a series you have (for 'fills a gap' / 'next volume')

Only reads. Without an ABS address and key it is simply empty."""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field

from .models import Book, BookType
from .text import fold

_CACHE: dict[str, tuple[float, "AbsIndex"]] = {}
MAX_AGE = 120  # seconds: one sort reuses it, the next scan reads ABS again


def split_series(value: str) -> list[str]:
    """ABS's seriesName: 'A #1, B #5' -> ['A #1', 'B #5']. A comma counts only after a '#number', so
    'Trapped in a Dating Sim: Otome Games Are Tough for Us, Too! #1' stays one series."""
    out, cur = [], ""
    for piece in (value or "").split(", "):
        cur = f"{cur}, {piece}" if cur else piece
        if re.search(r"#\s*[\d.]+$", cur):
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def _skey(name: str) -> str:
    from .consistency import series_key  # noqa: PLC0415
    return series_key(re.sub(r"\s*\((?:Manga|Comic)\)$", "", name or ""))


@dataclass
class AbsBook:
    id: str
    library: str
    path: str
    title: str
    authors: list[str]
    narrators: list[str]
    series: list[tuple[str, float | None]]
    asin: str
    fmt: str            # 'audio' / 'ebook'
    missing: bool
    language: str = ""  # 'swedish' / 'english' / ''

    @property
    def numbers(self) -> set[float]:
        """The volumes this item holds: its series number, or all of a box set ('Hell's Rejects, Books 1-4' /
        folder '1-4 - ...' -> 1, 2, 3, 4)."""
        out = {i for _, i in self.series if i is not None}
        m = re.search(r"(?i)\bbooks?\s+(\d+)\s*-\s*(\d+)\b", self.title) \
            or re.match(r"^(\d+)-(\d+) - ", re.split(r"[\\/]", self.path.rstrip("\\/"))[-1])
        if m and int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + 30:
            out |= {float(n) for n in range(int(m.group(1)), int(m.group(2)) + 1)}
        return out

    @property
    def swedish(self) -> bool:
        """The Swedish translation of a series is not the English book with the same number."""
        return "svensk" in self.library.lower() or self.language == "swedish"

    @property
    def comic(self) -> bool:
        """Manga / comics - never the same book as the light novel of the same series."""
        return bool(re.search(r"(?i)manga|comic", self.library)) or any(
            re.search(r"(?i)\((?:manga|comic)\)$", n) for n, _ in self.series)


def _comic(book: Book) -> bool:
    return book.type is BookType.COMIC


def _swedish(book: Book) -> bool:
    from ..providers.match import norm_language  # noqa: PLC0415
    return norm_language(book.meta.language) == "swedish"


@dataclass
class AbsIndex:
    books: list[AbsBook] = field(default_factory=list)

    @classmethod
    def load(cls, folders, fresh: bool = False) -> "AbsIndex":
        if not (getattr(folders, "abs_url", "") and getattr(folders, "abs_api_key", "")):
            return cls()
        key = folders.abs_url
        hit = _CACHE.get(key)
        if hit and not fresh and time.time() - hit[0] < MAX_AGE:
            return hit[1]
        from ..providers.abs import AbsClient  # noqa: PLC0415
        from ..providers.match import norm_language  # noqa: PLC0415
        index = cls()
        try:
            client = AbsClient(folders.abs_url, folders.abs_api_key, timeout=15)
            for lib in client.libraries():
                if lib.get("mediaType") != "book":
                    continue
                for it in client.library_items(lib["id"]):
                    media = it.get("media") or {}
                    md = media.get("metadata") or {}
                    series = []
                    for part in split_series(md.get("seriesName") or ""):
                        m = re.match(r"^(.*?)\s*#\s*([\d.]+)$", part)
                        series.append((m.group(1), float(m.group(2))) if m else (part, None))
                    index.books.append(AbsBook(
                        it["id"], lib.get("name") or "", it.get("path") or "", md.get("title") or "",
                        [a.strip() for a in (md.get("authorName") or "").split(",") if a.strip()],
                        [n.strip() for n in (md.get("narratorName") or "").split(",") if n.strip()],
                        series, (md.get("asin") or "").upper(),
                        "audio" if media.get("numAudioFiles") else "ebook", bool(it.get("isMissing")),
                        norm_language(md.get("language"))))
        except (OSError, ValueError):
            return cls()  # ABS not reachable: the sorter works as before
        _CACHE[key] = (time.time(), index)
        return index

    # -- lookups ------------------------------------------------------------------------------------------
    def _live(self, fmt: str, comic: bool = False, swedish: bool = False):
        return (b for b in self.books if not b.missing and b.fmt == fmt and b.comic == comic and b.swedish == swedish)

    def find_same(self, book: Book) -> AbsBook | None:
        """Your library's copy of this book (same format), or None."""
        fmt = "audio" if book.type is BookType.AUDIO else "ebook"
        m = book.meta
        from .planner import edition  # noqa: PLC0415
        dramatized = bool(edition(book))  # a full-cast edition next to the reading is another book
        live = [b for b in self._live(fmt, _comic(book), _swedish(book)) if ("dramatized" in b.path.lower()) == dramatized]
        asin = (m.asin or "").upper()
        if asin:
            hit = next((b for b in live if b.asin == asin), None)
            if hit:
                return hit
        authors = {fold(a) for a in m.authors}
        if not authors:
            return None
        mine = [b for b in live if authors & {fold(a) for a in b.authors}]
        if m.title:
            hit = next((b for b in mine if fold(b.title) == fold(m.title)), None)
            if hit:
                return hit
        if m.series and m.series_index is not None:
            key = _skey(m.series)
            return next((b for b in mine if any(_skey(n) == key for n, _ in b.series)
                         and m.series_index in b.numbers), None)
        return None

    def series_name(self, author: str | None, series: str, comic: bool = False, swedish: bool = False) -> str | None:
        """How ABS spells this series (by the same author, same kind and language), if it has it."""
        key = _skey(series)
        names = Counter(n for b in self.books if not b.missing and b.comic == comic and b.swedish == swedish and (not author or fold(author) in {fold(a) for a in b.authors})
                        for n, _ in b.series if _skey(n) == key)
        return names.most_common(1)[0][0] if names else None

    def narrators_for(self, author: str | None, series: str, swedish: bool = False) -> list[str]:
        key = _skey(series)
        votes = Counter(tuple(b.narrators) for b in self._live("audio", swedish=swedish) if b.narrators
                        and (not author or fold(author) in {fold(a) for a in b.authors})
                        and any(_skey(n) == key for n, _ in b.series))
        return list(votes.most_common(1)[0][0]) if votes else []

    def owned_numbers(self, author: str | None, series: str, fmt: str, comic: bool = False,
                      swedish: bool = False) -> set[float]:
        key = _skey(series)
        return {i for b in self._live(fmt, comic, swedish) if (not author or fold(author) in {fold(a) for a in b.authors})
                for n, _ in b.series if _skey(n) == key for i in b.numbers}


def library_status(book: Book, index: AbsIndex) -> str:
    """For the book list: 'have it' / 'fills gap #5' / 'next volume' / 'new volume' / 'new series' / ''."""
    if not index.books or not book.meta.authors:
        return ""
    if index.find_same(book):
        return "have it"
    m = book.meta
    if not m.series or m.series_index is None:
        return ""
    fmt = "audio" if book.type is BookType.AUDIO else "ebook"
    comic, swedish = _comic(book), _swedish(book)
    have = index.owned_numbers(book.author, m.series, fmt, comic, swedish)
    if not have:
        other = "ebook" if fmt == "audio" else "audio"
        return "new format" if not comic and index.owned_numbers(book.author, m.series, other, swedish=swedish) \
            else "new series"
    n = m.series_index
    if n < max(have):
        return f"fills gap #{n:g}"
    if n <= int(max(have)) + 1:
        return "next volume"
    return f"new (you have up to #{max(have):g})"
