"""Work out where every file goes (Audiobookshelf layout) and spot problems before anything moves.

    <Author>\\<Series>\\<N> - <Title>\\<files>
    <Author>\\<Title>\\<files>                    (no series)
    Manga/comics: series gets a "(Manga)" / "(Comic)" suffix, title "<Series>, Vol. <N>"
    No author: <Review>\\<original name>
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Folders
from ..providers.match import norm_language
from ..readers.names import parse_volume
from .metadata import format_index, series_number
from .text import fold
from .models import Book, BookType

MAX_PATH = 259
_INVALID_RE = re.compile(r'[<>"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def clean_name(name: str, max_len: int = 120) -> str:
    """A safe Windows file/folder name: 'Title: Sub?' -> 'Title - Sub'."""
    s = re.sub(r"\s*:\s*", " - ", name)
    s = _INVALID_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        cut = s[:max_len]
        space = cut.rfind(" ")
        s = cut[:space] if space > max_len * 0.6 else cut  # prefer cutting between words
    s = s.strip().rstrip(".,;- ")
    if not s:
        return "_"
    if s.split(".")[0].lower() in _RESERVED:
        s += "_"
    return s


SHORT_SERIES = 40   # series names up to this length stay whole in book folder / file names
SHORT_SUBTITLE = 45  # else the subtitle is used when it's at most this long, else the main title
# (the subtitle only when the author has more series of that name: 'Ascendance of a Bookworm: ...')


def folder_series(series: str, shared_head: bool = True) -> str:
    """The series' short form for book folder / file names (the series folder keeps the full name;
    Windows allows 260 characters per path):
    'Ascendance of a Bookworm: Hannelore's Fifth Year at the Royal Academy' -> 'Hannelore's Fifth Year ...',
    'Hell Mode: The Hardcore Gamer Dominates Another World ... Summoner of the Beginning' -> 'Hell Mode'."""
    if len(series) <= SHORT_SERIES:
        return series
    parts = re.split(r"\s*[:：]\s*", series, maxsplit=1)
    if len(parts) < 2 or not parts[0].strip():
        # no colon: 'Enough with This Slow Life! I Was Reincarnated as a High Elf...' -> up to the '!'
        head = re.split(r"(?<=[!?])\s+", series, maxsplit=1)[0]
        return head if 3 <= len(head) < len(series) else series
    main, sub = parts[0].strip(), parts[1].strip()
    return sub if shared_head and sub and len(sub) <= SHORT_SUBTITLE else main


_folder_series = folder_series


def series_head(series: str) -> str:
    """'Ascendance of a Bookworm: Part 5' / '... - Part 5' (a folder) -> 'ascendanceofabookworm'."""
    return fold(re.split(r"\s*[:：]\s*|\s+-\s+", series or "", maxsplit=1)[0])


_DRAMATIZED_RE = re.compile(r"(?i)\bdramati[sz](?:ed|ation)\b|\bfull[- ]cast\b")


def edition(book: Book) -> str:
    """'Dramatized' for a full-cast recording ('Volume 01 - Vampire Hunter D (Dramatized) {full cast}'):
    it gets its own book folder next to the plain reading of the same volume, not 'duplicate of'."""
    names = [book.main_file.stem, book.meta.title or "", book.meta.subtitle or ""]
    if book.folder is not None:
        names.append(book.folder.name)
    return "Dramatized" if any(_DRAMATIZED_RE.search(n) for n in names) else ""


def with_edition(title: str, book: Book) -> str:
    ed = edition(book)
    return f"{title} ({ed})" if ed and not _DRAMATIZED_RE.search(title) else title


def folder_title(book: Book, shared_head: bool = True) -> str:
    """The title for book folder / file names: a long series name at its start gets its short form."""
    return with_edition(_folder_title(book, shared_head), book)


def _folder_title(book: Book, shared_head: bool = True) -> str:
    folder_series = lambda s: _folder_series(s, shared_head)  # noqa: E731
    m = book.meta
    title = m.title or book.main_file.stem
    if m.series and m.series_index is not None and (vol := parse_volume(title)) and vol[1] == m.series_index \
            and (fold(vol[0]).startswith(fold(m.series))
                 or (len(fold(vol[0])) >= 6 and series_head(m.series) == fold(vol[0]) and ":" in m.series)):
        # (also 'Trapped in a Dating Sim, Vol. 1' - only the head of 'Trapped in a Dating Sim: The World of...')
        # 'I Parry Everything: What Do You Mean...Yet!, Vol. 1' with series 'I Parry Everything: ...':
        # the folder says 'I Parry Everything, Vol. 1' - like every other volume
        return f"{folder_series(m.series)}, Vol. {format_index(m.series_index)}"
    if m.series and title.startswith(m.series):
        title = folder_series(m.series) + title[len(m.series):]
    return title


@dataclass(eq=False)
class Move:
    src: Path
    dst: Path
    book: Book

    @property
    def in_place(self) -> bool:
        return str(self.src) == str(self.dst)


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    targets: dict[Book, Path] = field(default_factory=dict)  # book -> its folder
    review: dict[Book, str] = field(default_factory=dict)    # book -> why it goes to Review
    problems: dict[Book, list[str]] = field(default_factory=dict)
    duplicates: dict[Book, set[Book]] = field(default_factory=dict)  # book -> other copies of it
    identical: set[Book] = field(default_factory=set)  # every file is already in the library, same size

    def duplicate_group(self, book: Book) -> list[Book]:
        """The book and every copy that clashes with it (transitively)."""
        group, todo = [], [book]
        while todo:
            b = todo.pop()
            if b not in group:
                group.append(b)
                todo.extend(self.duplicates.get(b, ()))
        return group

    def add_problem(self, book: Book, text: str) -> None:
        items = self.problems.setdefault(book, [])
        if text not in items:
            items.append(text)


class Planner:
    def __init__(self, folders: Folders, allow_protected: bool = False):
        self.folders = folders
        self.allow_protected = allow_protected
        self._batch_series: dict[str, set[str]] = {}  # author -> the series names in this sort
        self._on_disk: dict[str, set[str]] = {}

    def shared_head(self, book: Book) -> bool:
        """Does the author have another series starting with this one's main name? ('Ascendance of a
        Bookworm: Part 5' next to ': Hannelore's Fifth Year...' - then the subtitle tells them apart)"""
        series = book.meta.series or ""
        if not re.search(r"[:：]", series) or not book.author:
            return False
        names = set(self._batch_series.get(fold(book.author), ()))
        author_dir = self.library_root(book) / clean_name(book.author)
        key = os.path.normcase(str(author_dir))
        if key not in self._on_disk:
            try:
                self._on_disk[key] = {p.name for p in author_dir.iterdir() if p.is_dir()}
            except OSError:
                self._on_disk[key] = set()
        names |= self._on_disk[key]
        mine = fold(clean_name(series))
        head = series_head(series)
        return any(series_head(n) == head and fold(clean_name(n)) != mine for n in names)

    def plan(self, books: list[Book]) -> Plan:
        plan = Plan()
        self._batch_series = {}
        for book in books:
            if book.meta.series and book.author:
                self._batch_series.setdefault(fold(book.author), set()).add(book.meta.series)
        for book in books:
            review = not book.author
            if review:
                plan.review[book] = "no author - put it in an <Author> folder inside Incoming"
                target = self._review_target(book)
                moves = self._moves(book, target, review)
            else:
                target, moves = self._fit(book)
            plan.targets[book] = target
            plan.moves.extend(moves)
        self._check(plan)
        self._find_identical(plan)  # last: it replaces the other reasons
        return plan

    def _fit(self, book: Book) -> tuple[Path, list[Move]]:
        """Shorten long series/title names until every path fits in 260 characters."""
        # first the book folder/file names get shorter; the series folder only when that is not enough
        # (else volume 1 and volume 2 of a long series end up in two series folders)
        for series_first in (False, True):
            limit = 120
            while True:
                target = self.book_dir(book, limit, series_limit=limit if series_first else 120)
                moves = self._moves(book, target, False, limit)
                longest = max((len(str(mv.dst)) for mv in moves), default=0)
                if longest <= MAX_PATH:
                    return target, moves
                if limit <= 40:
                    break
                limit = max(40, limit - (longest - MAX_PATH))
        return target, moves

    def book_dir(self, book: Book, limit: int = 120, series_limit: int | None = None) -> Path:
        m = book.meta
        d = self.library_root(book) / clean_name(book.author or "Unknown Author")
        if m.series:
            d /= self.series_folder(book, series_limit or limit)
        name = self.book_name(book)
        cut = clean_name(name, limit)
        if cut != clean_name(name, 10_000) and (tail := re.search(r"(?i),?\s*vol(?:ume)?\.?\s*[\d.]+$", name)):
            # '10 - My Daughter Left the Nest and Returned an S-Rank' lost its volume: cut the middle instead
            cut = clean_name(name[:tail.start()], max(10, limit - len(tail.group(0)))).rstrip(" .,-") + tail.group(0)
        return d / cut

    def library_root(self, book: Book) -> Path:
        if book.type is not BookType.AUDIO:
            f = self.folders
            if not f.split_books:
                return f.books_sorted
            if book.type is BookType.COMIC:
                return f.books_sorted / (f.manga_dir if book.manga else f.comic_dir)
            return f.books_sorted / (f.light_novel_dir if book.light_novel else f.ebook_dir)
        if self.folders.swedish_sorted and norm_language(book.meta.language) == "swedish":
            return self.folders.swedish_sorted
        f = self.folders
        if not f.split_audio:
            return f.audio_sorted
        return f.audio_sorted / (f.audio_ln_dir if book.light_novel else f.audio_dir)

    @staticmethod
    def series_name(book: Book) -> str:
        series = book.meta.series or ""  # the full name: the series folder is the one place for it
        if book.type is BookType.COMIC:
            return f"{series} ({'Manga' if book.manga else 'Comic'})"
        return series

    @staticmethod
    def series_folder(book: Book, limit: int = 120) -> str:
        """The series folder name; a long name is shortened BEFORE the ' (Manga)' suffix, so it stays."""
        series = book.meta.series or ""
        if book.type is not BookType.COMIC:
            return clean_name(series, limit)
        suffix = f" ({'Manga' if book.manga else 'Comic'})"
        return clean_name(series, max(20, limit - len(suffix))) + suffix

    def book_name(self, book: Book) -> str:
        m = book.meta
        title = folder_title(book, self.shared_head(book))
        if m.series and m.series_index is not None:
            return f"{series_number(book)} - {title}"  # a box set: '1-4 - Hell's Rejects, Books 1-4'
        return title

    def _review_target(self, book: Book) -> Path:
        if book.type is BookType.AUDIO and book.folder:
            return self.folders.review / book.folder.name
        return self.folders.review

    def _moves(self, book: Book, target: Path, review: bool, limit: int = 120) -> list[Move]:
        if book.type is BookType.AUDIO:
            files = [*book.files, *book.extras]
            if book.folder:
                moves = [Move(f, target / f.relative_to(book.folder), book) for f in files]
            else:
                moves = [Move(f, target / f.name, book) for f in files]
            return self._short_audio_names(book, moves, limit)
        if review:
            return [Move(f, target / f.name, book) for f in book.files]
        name = clean_name(folder_title(book, self.shared_head(book)), limit)
        return [Move(f, target / f"{name}{f.suffix.lower()}", book) for f in book.files]

    def _short_audio_names(self, book: Book, moves: list[Move], limit: int) -> list[Move]:
        """A file name that alone breaks the 260 characters ('...Too, Vol. 1 - Miku; Noboru Akimoto -
        translator.m4b'): the book's one audio file gets the book's short name, other files are cut."""
        out, taken = [], set()
        single = len(book.files) == 1
        for mv in moves:
            dst = mv.dst
            if len(str(dst)) > MAX_PATH:
                room = max(12, MAX_PATH - len(str(dst.parent)) - 1 - len(dst.suffix))
                stem = clean_name(folder_title(book, self.shared_head(book)), min(limit, room)) \
                    if single and mv.src in book.files \
                    else clean_name(dst.stem, room)
                name = stem + dst.suffix
                n = 2
                while name.lower() in taken:
                    name = f"{stem[:room - 4]} ({n}){dst.suffix}"
                    n += 1
                dst = dst.with_name(name)
            taken.add(dst.name.lower())
            out.append(Move(mv.src, dst, mv.book) if dst != mv.dst else mv)
        return out

    def _check(self, plan: Plan) -> None:
        # two books aiming at the same book folder - or the same number in the same series - are
        # copies of each other, whatever their file names or title spelling ("Volume 01" / "Volume 1")
        by_target: dict[str, Book] = {}
        for book, target in plan.targets.items():
            if book in plan.review:
                continue
            m = book.meta
            if m.series and m.series_index is not None:
                key = os.path.normcase(f"{target.parent}|{series_number(book)}|{edition(book)}")
            else:
                key = os.path.normcase(str(target))
            if (other := by_target.get(key)) is not None:
                plan.add_problem(book, f"duplicate of {_where(other)}")
                plan.add_problem(other, f"duplicate of {_where(book)}")
                plan.duplicates.setdefault(book, set()).add(other)
                plan.duplicates.setdefault(other, set()).add(book)
            else:
                by_target[key] = book
        sources = {os.path.normcase(str(mv.src)) for mv in plan.moves}
        for book, target in plan.targets.items():
            if book in plan.review:
                continue
            try:
                present = [p for p in target.iterdir() if os.path.normcase(str(p)) not in sources]
            except OSError:
                continue
            if present:  # e.g. sorted earlier by beets/the PowerShell sorter with other file names
                plan.add_problem(book, f"already in the library: {target.name}")
        for book, target in plan.targets.items():  # same series number already sorted under another title
            m = book.meta
            if book in plan.review or not m.series or m.series_index is None:
                continue
            prefix = f"{series_number(book)} - "
            try:
                twins = [d.name for d in target.parent.iterdir()
                         if d.is_dir() and d.name.startswith(prefix) and d.name != target.name
                         and bool(_DRAMATIZED_RE.search(d.name)) == bool(edition(book))]  # the other edition
            except OSError:
                continue
            if twins:
                plan.add_problem(book, f"already in the library: {twins[0]}")
        seen: dict[str, Move] = {}
        for mv in plan.moves:
            if mv.in_place:
                continue
            key = os.path.normcase(str(mv.dst))
            if (other := seen.get(key)) is not None:
                if other.book is mv.book:
                    plan.add_problem(mv.book, f"two files would both become {mv.dst.name}")
                else:  # the same book twice (another copy or format): keep only one
                    plan.add_problem(mv.book, f"duplicate of {_where(other.book)}")
                    plan.add_problem(other.book, f"duplicate of {_where(mv.book)}")
                    plan.duplicates.setdefault(mv.book, set()).add(other.book)
                    plan.duplicates.setdefault(other.book, set()).add(mv.book)
                continue
            seen[key] = mv
            if os.path.normcase(str(mv.src)) != key and mv.dst.exists():
                plan.add_problem(mv.book, f"already exists: {mv.dst.name}")
            if len(str(mv.dst)) > MAX_PATH:
                plan.add_problem(mv.book, "path longer than 260 characters")
            if not self.allow_protected and any(_is_within(mv.dst, p) for p in self.folders.protected):
                plan.add_problem(mv.book, "target is inside an Audiobookshelf library folder (protected)")


    @staticmethod
    def _find_identical(plan: Plan) -> None:
        """Books whose every file already sits in the library with the same size (e.g. sorted before
        by another tool): nothing to do, the caller can mark them as sorted."""
        for book, target in plan.targets.items():
            if book in plan.review:
                continue
            m = book.meta
            dirs = [target]
            if m.series and m.series_index is not None and target.parent.is_dir():
                prefix = f"{series_number(book)} - "
                dirs += [d for d in target.parent.iterdir() if d.is_dir() and d.name.startswith(prefix)]
            sizes: set[int] = set()
            for d in dirs:
                if d.is_dir():
                    sizes |= {f.stat().st_size for f in d.rglob("*") if f.is_file()}
            try:
                if sizes and all(f.stat().st_size in sizes for f in book.files):
                    plan.identical.add(book)
                    plan.problems[book] = ["identical copy already in the library"]
            except OSError:
                continue


def _where(book: Book) -> str:
    path = book.folder or book.main_file
    try:
        return str(path.relative_to(book.source_root))
    except ValueError:
        return str(path)


def _is_within(path: Path, folder: Path) -> bool:
    p, f = os.path.normcase(str(path)), os.path.normcase(str(folder)).rstrip("\\/")
    return p == f or p.startswith(f + os.sep)
