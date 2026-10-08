"""Compare folders with your libraries: which books do you already have? Sort them into
<dest>\\-=Have=-\\... and <dest>\\-=Do Not Have=-\\... in the sorter's own layout:
  AudioBooks\\Audiobooks|Light Novels\\<author>\\<series>\\<N - title>
  Books\\Manga|Light Novels|EBooks|Comics\\<author>\\<series>\\<N - title>
Your libraries (the protected folders) are only read; every move is journaled (undo from History)."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from ..config import Folders
from .consistency import series_key, unify_names
from .library import LibraryEditor
from .models import Book, BookType
from .pipeline import load_books
from .planner import Planner
from .text import fold, name_key

HAVE, HAVE_NOT = "-=Have=-", "-=Do Not Have=-"
_VOL_RE = re.compile(r"(?i)[\s,:\-]*(?:vol(?:ume)?\.?|book|part)\s*\d+(?:\.\d+)?\b.*$")


def _kind(b: Book) -> str:
    return "comic" if b.type is BookType.COMIC else ("audio" if b.type is BookType.AUDIO else "text")


def book_keys(b: Book) -> tuple[set, set]:
    """Ways to recognise a book: series + number, title (+ main title before ':') + author, ASIN.
    Manga and novels never match each other; a guessed author (from a folder name) isn't used."""
    m, k = b.meta, _kind(b)
    guessed = b.sources.get("authors") in ("filename", "folder")
    authors = ({name_key(a) for a in m.authors} if not guessed else set()) or {""}
    idx = round(m.series_index, 2) if m.series_index is not None else None
    out: set = set()
    if m.series and idx is not None:
        out.add(("s", k, series_key(m.series), idx))
    title = m.title or ""
    for name in {title, re.split(r"\s*:\s*", title)[0]}:
        t = fold(_VOL_RE.sub("", name)) or fold(name)
        if t:
            out |= {("t", k, t, a, idx) for a in authors}
            out |= {("t0", k, t, a) for a in authors}  # matches a copy without volume number
    if m.asin:
        out.add(("asin", m.asin))
    return out, authors


@dataclass
class Comparison:
    have: list[tuple[Book, Book]] = field(default_factory=list)  # (book here, the library's copy)
    have_not: list[Book] = field(default_factory=list)
    library_books: int = 0


def compare(books: list[Book], library: list[Book]) -> Comparison:
    index: dict = defaultdict(list)
    for lb in library:
        ks, la = book_keys(lb)
        for k in ks:
            index[k].append((lb, la))
    result = Comparison(library_books=len(library))
    for b in books:
        ks, authors = book_keys(b)
        if b.meta.series_index is not None:
            ks = {k for k in ks if k[0] != "t0"}  # numbered: the number has to agree
        hit = None
        for k in ks:
            for lb, la in index.get(k, []):
                if k[0] == "s" and not (authors & la or "" in authors or "" in la):
                    continue  # same series + number, another author
                hit = lb
                break
            if hit:
                break
        if hit:
            result.have.append((b, hit))
        else:
            result.have_not.append(b)
    return result


def libraries_of(folders: Folders) -> list[Path]:
    """Your libraries = the protected folders that exist (they are only read)."""
    return [p for p in folders.protected if p.is_dir()]


def plan_compare(folders: Folders, sources: list[Path],
                 on_progress: Callable[[int, int, str], None] | None = None) -> Comparison:
    for s in sources:
        if folders.is_protected(s):
            raise PermissionError(f"that's one of your libraries, not a folder to sort: {s}")
    library = [b for r in libraries_of(folders) for b in load_books([r])]
    progress = (lambda i, n, b: on_progress(i, n, b.main_file.name)) if on_progress else None
    books = [b for s in sources for b in load_books([s], on_progress=progress)]
    from .online import OnlineLookup  # noqa: PLC0415
    OnlineLookup(folders).run(books, on_progress=progress)  # light novel / manga / names, like sorting
    unify_names(books, [])
    return compare(books, library)


def apply_compare(folders: Folders, result: Comparison, dest: Path) -> tuple[str, int]:
    """Move every book into dest\\-=Have=- or dest\\-=Do Not Have=-, in the sorter's layout."""
    moved = 0
    sources: set[Path] = set()
    with LibraryEditor(folders, "compare") as ed:
        for books, name in (([b for b, _ in result.have], HAVE), (result.have_not, HAVE_NOT)):
            root = dest / name
            planner = Planner(replace(folders, audio_sorted=root / "AudioBooks", books_sorted=root / "Books",
                                      swedish_sorted=None))
            for b in _one_per_split_book(books):
                target = planner.book_dir(b)
                here = b.folder or b.main_file.parent
                if here == target:
                    continue  # already in its place (the tool ran here before)
                base_name, n = target.name, 1
                while target.exists() and any(target.iterdir()):  # the same book more than once
                    n += 1
                    target = target.with_name(f"{base_name} (copy {n})")
                sources.add(b.source_root)
                for f in _book_files(b):
                    rel = f.relative_to(here) if here in f.parents else Path(f.name)
                    ed.move_file(f, target / rel)
                    moved += 1
        for src in sources:  # drop the emptied folders (never the Have / Do Not Have roots)
            for d in sorted((p for p in src.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                if d.name not in (HAVE, HAVE_NOT):
                    try:
                        d.rmdir()
                    except OSError:
                        pass
        return ed.run_id, moved


def _one_per_split_book(books: list[Book]) -> list[Book]:
    """A book split into files that were each read as a book ('Dominion of Blades (01).m4b' ...
    '(45).m4b' with the same title, in one folder) moves as ONE book: the first stands for all."""
    groups: dict = defaultdict(list)
    for b in books:
        title = fold(_VOL_RE.sub("", b.meta.title or "")) or fold(b.main_file.stem)
        groups[(b.main_file.parent, title, b.meta.series_index)].append(b)
    out = []
    for group in groups.values():
        first = group[0]
        if len(group) > 1:
            first.files = sorted({f for b in group for f in b.files})
        out.append(first)
    return out


def _book_files(b: Book) -> list[Path]:
    """The book's files plus what lives in its own folder (cover, metadata, desc.txt...)."""
    files = list(dict.fromkeys([*b.files, *b.extras]))
    from ..readers.sidecar import _own_folder  # noqa: PLC0415
    folder = b.folder or (_own_folder(b) if b.type is not BookType.AUDIO else None)  # a .cbz alone in its folder
    if folder and folder.is_dir():
        files += [p for p in folder.rglob("*") if p.is_file() and p not in files]
    return [f for f in files if f.exists()]
