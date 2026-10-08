"""Collect a book's metadata from every local source, best source first."""
from __future__ import annotations

import re
from pathlib import Path

from ..readers.audio import read_audiobook
from ..readers.comic import read_comic
from ..readers.ebook import read_ebook
from ..readers.names import (context_from_dirs, parse_name, parse_volume,
                             split_series_from_title)
from ..readers.sidecar import read_sidecars
from .models import Book, BookType, Metadata, Status
from .text import clean_title, dedupe, fold, same_text

GUESSED = ("filename", "folder")
_LN_HINT_RE = re.compile(r"(?i)\blight novel\b|\(novel\)")
_PART_VOLUME_RE = re.compile(r"(?i)^(?P<s>.+?)[\s:,\-]+Part\s+(?P<p>\d+)[\s:,\-]+(?:Volume|Vol\.?)\s*(?P<v>\d+(?:\.\d+)?)\s*$")
_ROLE_RE = re.compile(r"(?i)[\s(\-–,]+(?:translator|translated by|översättare|översättning|illustrator|editor|redaktör)\)?\s*$")
_KIND_SUFFIX_RE = re.compile(r"\s*\((Manga|Comic)\)\s*$", re.I)
_KIND_FOLDERS = {"audiobooks", "lightnovels", "manga", "ebooks", "comics", "books", "audiobooksorted", "bookssorted"}
_MANGA_HINT_RE =re.compile(r"(?i)\(manga\)|\bmanga\b")


def read_local(book: Book, abs_json: bool = True) -> None:
    """Fill book.meta from sidecars > embedded tags > file/folder name > parent folders."""
    book.absorb(read_sidecars(book, abs_json), "sidecar")
    book.absorb(_embedded(book), "tags")
    guess, dirs = _name_guess(book)
    book.absorb(guess, "filename")
    book.absorb(context_from_dirs(dirs, book.type, book.meta), "folder")
    _tidy(book)
    update_status(book)


def update_status(book: Book) -> None:
    book.status = _status(book)


def edit_book(book: Book, **values) -> None:
    """Manual edits from the GUI: set exactly these fields (empty clears) and mark them 'manual'."""
    for name, value in values.items():
        if name == "manga":
            book.manga = bool(value)
            book.kind_known = True
            continue
        if value == "":
            value = None
        if getattr(book.meta, name) != value:
            setattr(book.meta, name, value)
            book.sources[name] = "manual"
    book.doubt = ""  # the user has looked at it
    update_status(book)


def format_index(index: float) -> str:
    """1.0 -> '1', 1.5 -> '1.5' (Audiobookshelf reads the number at the start of the folder)."""
    return str(int(index)) if index == int(index) else f"{index:g}"


def _embedded(book: Book) -> Metadata:
    if book.type is BookType.AUDIO:
        return read_audiobook(book.files)
    if book.type is BookType.COMIC:
        return read_comic(book.main_file)
    return read_ebook(book.files)


def _name_guess(book: Book) -> tuple[Metadata, list[str]]:
    """Parse the file name, or the book folder's name for multi-file audiobooks.

    Also returns the folders above the name that was used, for the author/series context.
    """
    stem = parse_name(book.main_file.stem, book.type)
    stem_dirs = _rel_dirs(book.main_file.parent, book.source_root)
    if book.type is not BookType.AUDIO or book.folder is None:
        return _swap_if_reversed(stem, stem_dirs), stem_dirs
    folder = parse_name(book.folder.name, book.type)
    folder_dirs = _rel_dirs(book.folder.parent, book.source_root)
    if len(book.files) > 1 or _score(folder) > _score(stem):
        return _swap_if_reversed(folder, folder_dirs), folder_dirs
    return _swap_if_reversed(stem, stem_dirs), stem_dirs


def _swap_if_reversed(meta: Metadata, dirs: list[str]) -> Metadata:
    """Calibre names files 'Title - Author': if the 'title' is a folder above, it's the author."""
    if meta.title and len(meta.authors) == 1 and any(same_text(meta.title, d) for d in dirs):
        meta.title, meta.authors = meta.authors[0], [meta.title]
    return meta


_GENERIC_NAME_RE = re.compile(r"^\W*(?:chapter|track|part|disc|cd|kapitel|audiobook|book)?\W*\d*\W*$", re.I)


def _score(meta: Metadata) -> int:
    if not meta.title or _GENERIC_NAME_RE.match(meta.title):
        return 0  # "01", "Track 03", "audiobook" say nothing
    return sum(bool(x) for x in (meta.title, meta.authors, meta.series, meta.series_index is not None))


def _rel_dirs(path: Path, root: Path) -> list[str]:
    try:
        return list(path.relative_to(root).parts)
    except ValueError:
        return []


def _tidy(book: Book) -> None:
    m = book.meta
    if book.type is BookType.COMIC and not book.manga:
        names = [m.title, m.series, book.main_file.stem, *book.main_file.parent.relative_to(book.source_root).parts] \
            if book.source_root in book.main_file.parents else [m.title, m.series, book.main_file.stem]
        if _MANGA_HINT_RE.search(" ".join(filter(None, names))):  # 'The Eminence in Shadow (manga)'
            book.manga = book.kind_known = True
    m.title = clean_title(m.title)
    m.series = clean_title(m.series)
    if m.series and (kind := _KIND_SUFFIX_RE.search(m.series)):
        # 'One Piece (Manga)' from an already-sorted folder: the series is 'One Piece'
        m.series = m.series[:kind.start()].strip()
        if book.type is BookType.COMIC:
            book.manga, book.kind_known = kind.group(1).lower() == "manga", True
    if m.authors and book.sources.get("authors") in GUESSED and any(
            fold(a) in _KIND_FOLDERS or a.startswith(("-=", "=")) for a in m.authors):
        m.authors = []  # 'Audiobooks' / '-=Books Review=-' is a folder, not a person
        book.sources.pop("authors", None)
    if m.series and book.sources.get("series") in GUESSED and (
            fold(m.series) in {fold(a) for a in m.authors} or fold(m.series) in _KIND_FOLDERS
            or m.series.startswith("-=") or m.series.startswith("=")):
        # a folder name taken for the series: the author's own folder, a kind folder, a staging folder
        m.series, m.series_index = None, None if book.sources.get("series_index") in GUESSED else m.series_index
        book.sources.pop("series", None)
    if m.series and m.series_index is None and (vol := parse_volume(book.main_file.stem)) \
            and _same_series(vol[0], m.series):
        m.series_index = vol[1]  # 'One Piece - Heroines Volume 01.epub' with series but no number
        book.sources["series_index"] = "filename"
    if m.title and book.type is not BookType.COMIC:
        split = split_series_from_title(m.title)
        if split and m.series and _same_series(split[1], m.series):
            m.title = split[0]  # the series is known already; just drop it from the title
        elif split and not m.series:
            m.title, m.series, m.series_index = split
            book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
        elif not m.series and (vol := parse_volume(m.title)):
            m.series, m.series_index = vol
            book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
    if m.title and m.series and (part := _PART_VOLUME_RE.match(m.title)) \
            and fold(part.group("s")) == fold(m.series):
        # 'Ascendance of a Bookworm: Part 5 Volume 10' (sidecar number 5.10): each part is a series
        m.series = f"{part.group('s').strip()}: Part {int(part.group('p'))}"
        m.series_index = float(part.group("v"))
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if m.series and m.series_index is None and m.title and re.match(re.escape(m.series) + r"\s*[:\-]\s*\S", m.title):
        # 'Ascendance of a Bookworm: Royal Academy Stories - First Year' with series 'Ascendance of a
        # Bookworm' but no number: a stand-alone spin-off, not a volume of that series
        m.series = None
        book.sources.pop("series", None)
    if m.title and m.series and fold(m.title) != fold(m.series) and _swapped_halves(book):
        # title 'Ascendance of a Bookworm' + series 'Short Story Collection', filed under
        # '...\Ascendance of a Bookworm\Ascendance of a Bookworm - Short Story Collection Volume 02'
        m.series = f"{m.title}: {m.series}"
        m.title = m.series + (f", Vol. {format_index(m.series_index)}" if m.series_index is not None else "")
    if m.title and m.series and book.type is not BookType.COMIC and fold(m.title) != fold(m.series) \
            and fold(m.series).startswith(fold(m.title)):
        # the title is just the start of the series ('One Piece' in 'One Piece - Ace's Story'): say which book
        m.title = m.series + (f", Vol. {format_index(m.series_index)}" if m.series_index is not None else "")
    if m.title and m.series and m.series_index is not None and book.type is not BookType.COMIC \
            and (vol := parse_volume(m.title)) and fold(vol[0]) == fold(m.series) and vol[1] == m.series_index:
        # one style for 'series + number' titles: 'Hell Mode: Volume 1' -> 'Hell Mode, Vol. 1' (like the manga)
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if book.type is BookType.COMIC and m.series and m.series_index is not None:
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if book.type is not BookType.COMIC and _LN_HINT_RE.search(" ".join(filter(None, (m.title, m.series)))):
        book.light_novel = True
    # 'Lena Fries-Gedin - translator' is a credit, not an author (Audiobookshelf would list her as one)
    m.authors = [a for a in m.authors if not _ROLE_RE.search(a)] or m.authors
    m.authors = dedupe(m.authors)
    if book.sources.get("narrators") == "sidecar":  # a metadata file says so: authors read their own books too
        m.narrators = dedupe(m.narrators)
    else:  # in tags the author is often repeated as composer/narrator by mistake
        m.narrators = [n for n in dedupe(m.narrators) if fold(n) not in {fold(a) for a in m.authors}]


def _swapped_halves(book: Book) -> bool:
    """The title is the franchise and the series its sub-series: a parent folder is named like the
    title, and the file/folder names say '<title> - <series>'. ('Tehanu - The Earthsea Cycle' isn't:
    no parent folder is called 'Tehanu'.)"""
    m = book.meta
    try:
        parents = list(book.main_file.parent.relative_to(book.source_root).parts)
    except ValueError:
        parents = []
    if fold(m.title) not in {fold(p) for p in parents}:
        return False
    both = fold(m.title) + fold(m.series)
    return any(both in fold(n) for n in [book.main_file.stem, *parents])


def _same_series(a: str, b: str) -> bool:
    """'The Earthsea Cycle' ~ 'Earthsea Cycle' ~ 'Earthsea Cycle (Light Novel)'."""
    fa, fb = fold(re.sub(r"(?i)^the\s+", "", a)), fold(re.sub(r"(?i)^the\s+", "", b))
    return bool(fa and fb) and (fa in fb or fb in fa)


def _status(book: Book) -> Status:
    m = book.meta
    book.notes = [n for n in book.notes if not n.startswith(("no ", "guessed", "manga or comic")) and n != book.doubt]
    if not m.title:
        book.notes.append("no title")
        return Status.UNKNOWN
    status = Status.READY
    if not m.authors:
        book.notes.append("no author - goes to Review")
        status = Status.CHECK
    guessed = [f for f in ("title", "authors", "series") if book.sources.get(f) in GUESSED]
    if guessed:
        book.notes.append("guessed from names: " + ", ".join(guessed))
        status = Status.CHECK
    if book.doubt:
        book.notes.append(book.doubt)
        status = Status.CHECK
    if book.type is BookType.COMIC and not book.kind_known:
        book.notes.append("manga or comic? - Look up online (AniList) decides")
        status = Status.CHECK
    return status
