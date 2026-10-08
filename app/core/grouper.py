"""Decide which files belong to the same book."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from ..readers.audio import album_of
from .models import (AUDIO_EXTS, BOOK_EXTS, COMIC_EXTS, EBOOK_EXTS, EBOOK_PREFERENCE, Book,
                     BookType)
from .scanner import walk_files
from .text import fold, natural_key

DISC_DIR_RE = re.compile(r"^(?:cd|disc|disk|part|pt)[\s._-]*\d{1,3}$", re.I)
# A PDF inside an audiobook folder is almost always the companion booklet.
EXTRA_EXCLUDED_EXTS = BOOK_EXTS - {".pdf"}
# DAISY 2.02 player files (Swedish library audiobooks): useless next to the audio, ncc.html is read first
DAISY_EXTS = frozenset({".smil", ".html", ".htm", ".css", ".js"})


def group(files: list[Path], source_root: Path) -> list[Book]:
    source_root = Path(source_root)
    audio = [f for f in files if f.suffix.lower() in AUDIO_EXTS]
    books = _group_audio(audio, source_root)
    audio_dirs = [b.folder for b in books if b.folder]

    ebooks = []
    for f in files:
        ext = f.suffix.lower()
        if ext in EBOOK_EXTS and not (ext == ".pdf" and _inside(f, audio_dirs)):
            ebooks.append(f)
        elif ext in COMIC_EXTS:
            books.append(Book(BookType.COMIC, [f], source_root))
    books.extend(_group_ebooks(ebooks, files, source_root))

    claimed = {f for book in books for f in book.files}
    for book in books:
        if book.type is BookType.AUDIO and book.folder:
            daisy = (book.folder / "ncc.html").is_file()  # a DAISY talking book: its player files stay behind
            book.extras = sorted(
                (p for p in walk_files(book.folder)
                 if p not in claimed and p.suffix.lower() not in EXTRA_EXCLUDED_EXTS
                 and not (daisy and p.suffix.lower() in DAISY_EXTS)),
                key=natural_key,
            )
    books.sort(key=lambda b: natural_key(b.main_file))
    return books


def _group_audio(audio: list[Path], source_root: Path) -> list[Book]:
    by_dir: dict[Path, list[Path]] = defaultdict(list)
    for f in audio:
        d = f.parent
        if d != source_root and DISC_DIR_RE.match(d.name):
            d = d.parent  # CD1 / CD2 / Disc 1 ... belong to the book folder above
        by_dir[d].append(f)

    books = []
    for d, fs in by_dir.items():
        fs.sort(key=natural_key)
        chunks = _split_audio(fs)
        # A folder with audio in its subfolders too (e.g. an author folder) isn't one book.
        nested = any(other != d and d in other.parents for other in by_dir)
        dedicated = len(chunks) == 1 and d != source_root and not nested
        for chunk in chunks:
            books.append(Book(BookType.AUDIO, chunk, source_root, folder=d if dedicated else None))
    return books


def _split_audio(files: list[Path]) -> list[list[Path]]:
    if len(files) == 1:
        return [files]
    if all(f.suffix.lower() == ".m4b" for f in files):
        return [[f] for f in files]  # each m4b is a complete book
    groups: dict[str, list[Path]] = {}
    for f in files:
        groups.setdefault(fold(album_of(f)), []).append(f)
    if len([k for k in groups if k]) <= 1:
        return [files]
    return list(groups.values())


def _group_ebooks(ebooks: list[Path], all_files: list[Path], source_root: Path) -> list[Book]:
    by_key: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for f in ebooks:
        by_key[(f.parent, fold(f.stem))].append(f)

    files_per_dir: dict[Path, int] = defaultdict(int)
    for f in all_files:
        files_per_dir[f.parent] += 1

    books = []
    for (d, _), fs in by_key.items():
        fs.sort(key=lambda f: EBOOK_PREFERENCE.index(f.suffix.lower()))
        dedicated = d != source_root and files_per_dir[d] == len(fs)
        books.append(Book(BookType.EBOOK, fs, source_root, folder=d if dedicated else None))
    return books


def _inside(path: Path, dirs: list[Path]) -> bool:
    return any(d == path.parent or d in path.parents for d in dirs)
