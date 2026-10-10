"""Compare copies of the same book so the user can keep the best one."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mutagen

from .metadata import format_index
from .models import AUDIO_EXTS, BOOK_EXTS, Book, BookType
from .text import natural_key


@dataclass
class CopyInfo:
    book: Book
    files: int
    size: int            # bytes
    formats: str         # "mp3", "epub + pdf"
    duration: float = 0  # seconds, audiobooks only
    bitrate: int = 0     # kbit/s of the first file, audiobooks only
    chapters: int = 0    # chapters in a single-file audiobook (several files: one per file)

    @property
    def summary(self) -> str:
        parts = [f"{self.files} file(s)", f"{self.size / 1_048_576:,.0f} MB", self.formats]
        if self.duration:
            h, rest = divmod(int(self.duration), 3600)
            parts.append(f"{h}h {rest // 60:02d}m")
        if self.bitrate:
            parts.append(f"{self.bitrate} kbit/s")
        if self.book.type is BookType.AUDIO and self.files == 1:
            parts.append(f"{self.chapters} chapters" if self.chapters else "no chapters")
        return " · ".join(parts)


def describe(book: Book) -> CopyInfo:
    size = sum(f.stat().st_size for f in book.files if f.exists())
    formats = " + ".join(dict.fromkeys(f.suffix.lower().lstrip(".") for f in book.files))
    info = CopyInfo(book, len(book.files), size, formats)
    if book.type is BookType.AUDIO:
        for i, f in enumerate(book.files):
            try:
                a = mutagen.File(f)
            except Exception:  # noqa: BLE001
                continue
            if a is None or a.info is None:
                continue
            info.duration += getattr(a.info, "length", 0) or 0
            if i == 0:
                info.bitrate = int((getattr(a.info, "bitrate", 0) or 0) / 1000)
            info.chapters += len(getattr(a, "chapters", None) or []) if len(book.files) == 1 else 1
    return info


def _whole(c: CopyInfo, longest: float) -> bool:
    """Close enough to the longest copy to count as complete (a few minutes of padding or silence)."""
    return not longest or c.duration >= longest * 0.95


def best_copy(infos: list[CopyInfo]) -> CopyInfo:
    """Most complete first (longest / biggest), then better quality, then m4b / epub."""
    longest = max((c.duration for c in infos), default=0)

    def key(c: CopyInfo):
        preferred = "m4b" in c.formats or "epub" in c.formats
        # 'Baccano! 6': 5.6 h without chapters vs 5.5 h with all 11 - the chaptered one is the better copy
        return (_whole(c, longest), c.chapters >= 3, round(c.duration / 60), c.bitrate, preferred, c.size)
    return max(infos, key=key)


def clearly_better(a: CopyInfo, b: CopyInfo) -> bool:
    """Is copy a clearly better than copy b (longer, higher bitrate, or much bigger)?"""
    if a.duration and b.duration:
        if b.chapters >= 3 > a.chapters and a.duration <= b.duration * 1.05:
            return False  # a little longer but no chapters: not better than a copy with them
        if a.duration > b.duration * 1.02:
            return True
        return a.bitrate > b.bitrate * 1.15 and a.duration >= b.duration * 0.98
    return a.size > b.size * 1.15


@dataclass
class DupMove:
    book: Book
    reason: str
    library: Path | None = None  # the copy already in the library (gets missing metadata from this one)


def library_folder(book: Book, plan) -> Path | None:
    """The folder in the library that already holds this book (same target, or same series number)."""
    target = plan.targets.get(book)
    if target is None or book in plan.review:
        return None
    try:
        if target.is_dir() and any(target.iterdir()):
            return target
        m = book.meta
        if m.series and m.series_index is not None and target.parent.is_dir():
            prefix = f"{format_index(m.series_index)} - "
            hit = next((d for d in sorted(target.parent.iterdir()) if d.is_dir() and d.name.startswith(prefix)), None)
            if hit is not None:
                return hit
        index = getattr(plan, "abs_index", None)
        if index is not None and (same := index.find_same(book)) is not None:
            folder = Path(same.path)  # your Audiobookshelf's copy, whatever its folder is called (only read)
            folder = folder if folder.is_dir() else folder.parent
            return folder if folder.is_dir() else None
    except OSError:
        return None
    return None


def library_copy(book: Book, folder: Path) -> Book:
    exts = AUDIO_EXTS if book.type is BookType.AUDIO else BOOK_EXTS - AUDIO_EXTS
    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts), key=natural_key)
    return Book(book.type, files, folder.parent, folder=folder)


def plan_duplicate_moves(books: list[Book], plan, stay: set[Book] = frozenset()
                         ) -> tuple[list[DupMove], list[tuple[Book, str]]]:
    """(copies to move out of Incoming, books kept because they are better than the library's copy).

    Inside Incoming, only the best copy of a book stays. A book the library already has (same or
    better quality) moves too - its metadata can still fill gaps in the library copy.
    `stay`: books that never move (the originals this app sorted - copy mode keeps them on purpose)."""
    moves, better = _plan_moves(books, plan)
    return [m for m in moves if m.book not in stay], [(b, w) for b, w in better if b not in stay]


def _plan_moves(books: list[Book], plan) -> tuple[list[DupMove], list[tuple[Book, str]]]:
    from .m4b import is_xhe_aac  # noqa: PLC0415 - ffprobe helper
    moves: list[DupMove] = []
    better: list[tuple[Book, str]] = []
    leaving: set[Book] = set()
    infos: dict[Book, CopyInfo] = {}

    def info(b: Book) -> CopyInfo:
        if b not in infos:
            infos[b] = describe(b)
        return infos[b]

    for book in books:
        if book in leaving or book not in plan.duplicates:
            continue
        group = plan.duplicate_group(book)
        keep = best_copy([info(b) for b in group]).book
        for b in group:
            if b is not keep and b not in leaving:
                leaving.add(b)
                moves.append(DupMove(b, f"extra copy - kept {_where(keep)} ({info(keep).summary})"))
    for book in books:
        if book in leaving:
            continue
        folder = library_folder(book, plan)
        if folder is None:
            continue
        lib = library_copy(book, folder)
        if not lib.files:
            continue
        mine, theirs = info(book), describe(lib)
        # an xHE-AAC original only "wins" on paper: Audiobookshelf can't play it
        if clearly_better(mine, theirs) and not (book.type is BookType.AUDIO and is_xhe_aac(book.main_file)):
            better.append((book, f"better than the library copy ({mine.summary} vs {theirs.summary})"))
            continue
        leaving.add(book)
        moves.append(DupMove(book, f"already in the library: {folder.name} ({theirs.summary})", folder))
    return moves, better


def _where(book: Book) -> str:
    path = book.folder or book.main_file
    try:
        return str(path.relative_to(book.source_root))
    except ValueError:
        return path.name
