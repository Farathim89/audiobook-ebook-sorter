"""Productions no shop sells, recognised by their name: Soundbooth Theater's 'Dungeon Crawler Carl Audio
Immersion Tunnel' (a full-cast season per book, soundbooth.app) is not on Audible - a search there finds the
plain reading of book N, and the AI guessed an unrelated 'Season 1'. A download whose folder, file or title
says 'Audio Immersion Tunnel' gets its series, season number, author and publisher from here instead, and the
shops and the AI leave it alone.

    'Dungeon Crawler Carl - Season 2 - Audio Immersion Tunnel'
        -> 'Dungeon Crawler Carl: Audio Immersion Tunnel, Season 2'   series #2, by Matt Dinniman
           subtitle 'Carl's Doomsday Scenario' (book 2, when your Audiobookshelf has it)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .metadata import update_status
from .models import Book, BookType, Metadata
from .text import fold

SOURCE = "known series"


@dataclass(frozen=True)
class KnownSeries:
    name: re.Pattern            # what a folder / file / title says
    series: str
    author: str
    publisher: str
    narrators: tuple[str, ...]  # only when the files name nobody
    book_series: str            # the books the seasons follow (the subtitle: book N's title)


KNOWN = [
    KnownSeries(re.compile(r"(?i)audio[\s_-]*immersion[\s_-]*tunnel"), "Dungeon Crawler Carl: Audio Immersion Tunnel",
                "Matt Dinniman", "Soundbooth Theater",
                ("Jeff Hays", "Andrea Parsneau", "Travis Baldree", "Heath Miller", "Johnathan McClain"),
                "Dungeon Crawler Carl"),
]
_SEASON_RE = re.compile(r"(?i)\bseason[\s_.-]*0*(\d+)\b|\bS0*(\d+)E\d+\b")


def _names(book: Book) -> list[str]:
    m = book.meta
    names = [m.title or "", m.subtitle or "", m.series or ""]
    if book.folder is not None:
        names += [book.folder.name, book.folder.parent.name]
    return names + [f.stem for f in book.files[:3]]


def known_series(book: Book) -> KnownSeries | None:
    if book.type is not BookType.AUDIO:
        return None
    names = _names(book)
    return next((k for k in KNOWN if any(k.name.search(n) for n in names)), None)


def season(book: Book, known: KnownSeries) -> int | None:
    """'Season 2' / 'S02E01' in a name, else the number its own series already has."""
    for n in _names(book):
        if m := _SEASON_RE.search(n):
            return int(m.group(1) or m.group(2))
    if book.meta.series_index is not None and known.name.search(book.meta.series or ""):
        return int(book.meta.series_index)
    return None


def apply_known_series(book: Book, abs_index=None) -> bool:
    """Set a recognised production's details; True when the book is one (then no shop / AI lookup)."""
    known = known_series(book)
    if known is None:
        return False
    n = season(book, known)
    meta = Metadata(series=known.series, authors=[known.author], publisher=known.publisher,
                    narrators=[] if book.meta.narrators else list(known.narrators))
    if n is not None:
        meta.series_index = float(n)
        meta.title = f"{known.series}, Season {n}"
        meta.subtitle = _book_title(abs_index, known, n)
    else:
        meta.title = known.series
        note = f"{known.series}: no season number in its names - check it"
        if note not in book.notes:
            book.notes.append(note)
    book.override(meta, SOURCE)
    sub = book.meta.subtitle or ""
    if not meta.subtitle and (known.name.search(sub) or _SEASON_RE.search(sub)):
        book.meta.subtitle = None  # 'Season 1 - Audio Immersion Tunnel' only repeats the title
    book.meta.asin = None  # a shop's ASIN would be the plain reading of the book
    book.doubt = ""
    book.looked_up = True
    update_status(book)
    return True


def _book_title(abs_index, known: KnownSeries, n: int) -> str | None:
    """Book N of the series the seasons follow, as your Audiobookshelf has it ('Carl's Doomsday Scenario')."""
    want = fold(known.book_series)
    for b in getattr(abs_index, "books", None) or []:
        if not b.missing and any(fold(s) == want and i == n for s, i in b.series):
            return b.title
    return None
