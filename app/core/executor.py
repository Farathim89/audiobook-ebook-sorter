"""Carry out a Plan: copy (default) or move files, write sidecars, journal everything for undo.

Safety rules:
- never overwrite: an existing target fails that book, nothing else
- copy to '<name>.partial', check the size, then rename into place
- books with problems (duplicate, target exists, protected library...) are skipped
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Folders
from ..providers.http import Cache, Http
from . import m4b
from .covers import extract_cover
from .history import Journal, Processed
from .leftovers import done_dir, drop_empty_parents, leftovers
from .library import remove_empty_dirs
from .models import Book, BookType, Status
from .planner import Move, Plan, clean_name
from .sidecars import download, sidecar_files
from .tagger import write_tags

log = logging.getLogger(__name__)


@dataclass
class BookResult:
    book: Book
    status: str  # "done", "review", "skipped", "failed"
    detail: str = ""


@dataclass
class RunResult:
    run_id: str
    results: list[BookResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(r.status == status for r in self.results)


class Executor:
    def __init__(self, folders: Folders, mode: str = "copy", processed: Processed | None = None,
                 to_m4b: bool = False, on_detail: Callable[[str], None] | None = None):
        """to_m4b: multi-file audiobooks become one chaptered .m4b (originals are always kept),
        single m4b files get Audible's chapter names when they match."""
        if mode not in ("copy", "move"):
            raise ValueError(mode)
        self.folders, self.mode, self.to_m4b = folders, mode, to_m4b
        self.processed = processed or Processed(folders.state / "processed.txt")
        self.on_detail = on_detail or (lambda s: None)
        self._cancelled: Callable[[], bool] = lambda: False
        self._http: Http | None = None

    def run(self, plan: Plan, books: list[Book] | None = None,
            on_progress: Callable[[int, int, Book], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> RunResult:
        books = list(plan.targets) if books is None else books
        self._cancelled = cancelled
        moves: dict[Book, list[Move]] = {}
        for mv in plan.moves:
            moves.setdefault(mv.book, []).append(mv)

        journal = Journal(self.folders.state / "history", self.mode)
        result = RunResult(journal.run_id)
        try:
            for i, book in enumerate(books, 1):
                if cancelled():
                    break
                result.results.append(self._book(book, plan, moves.get(book, []), journal))
                if on_progress:
                    on_progress(i, len(books), book)
        finally:
            journal.close()
            self.processed.save()
        return result

    def _book(self, book: Book, plan: Plan, moves: list[Move], journal: Journal) -> BookResult:
        if problems := plan.problems.get(book):
            return BookResult(book, "skipped", "; ".join(problems))
        if book.status is Status.UNKNOWN:
            return BookResult(book, "skipped", "unknown book")
        try:
            target = plan.targets[book]
            # read the covers etc. BEFORE moving: in Move mode the book's own files are gone afterwards
            sidecars = sidecar_files(book, target) if book not in plan.review else {}
            m4b_file, chapters_from = None, ""
            fix_xhe = self.folders.fix_xhe_aac and book.type is BookType.AUDIO
            if self.to_m4b and book.type is BookType.AUDIO and book not in plan.review:
                m4b_file, chapters_from, handled = self._make_m4b(book, target, moves, journal)
                moves = [mv for mv in moves if mv.src not in handled]
                if m4b_file and fix_xhe and m4b.is_xhe_aac(m4b_file):  # a re-chaptered xHE m4b: our own file
                    self.on_detail(f"{book.meta.title}: xHE-AAC -> AAC")
                    fixed = m4b_file.with_name(m4b_file.stem + ".aac-lc" + m4b_file.suffix)
                    m4b.reencode_aac(m4b_file, fixed, self._cancelled)
                    os.replace(fixed, m4b_file)
                    journal.write(op="update", dst=str(m4b_file), size=m4b_file.stat().st_size)
            for mv in moves:
                if mv.in_place:
                    continue
                if fix_xhe and m4b.is_xhe_aac(mv.src):
                    # converted straight from the original, which stays where it is (also in Move mode)
                    self.on_detail(f"{book.meta.title}: xHE-AAC -> AAC")
                    self._ensure_dir(mv.dst.parent, journal)
                    m4b.reencode_aac(mv.src, mv.dst, self._cancelled)
                    journal.write(op="create", dst=str(mv.dst), size=mv.dst.stat().st_size)
                else:
                    self._transfer(mv.src, mv.dst, journal)
            if book in plan.review:
                name = f"{(book.folder or book.main_file).name}.reason.txt"
                self._create(self.folders.review / name, plan.review[book].encode("utf-8"), journal)
                status, detail = "review", plan.review[book]
            else:
                status, detail = "done", str(target)
                if m4b_file:
                    detail += f" (m4b, chapters from {chapters_from})"
                if self.folders.write_tags:
                    for mv in moves:
                        if not mv.in_place and write_tags(mv.dst, book):
                            journal.write(op="update", dst=str(mv.dst), size=mv.dst.stat().st_size)
                    if m4b_file and write_tags(m4b_file, book, whole_book=True):
                        journal.write(op="update", dst=str(m4b_file), size=m4b_file.stat().st_size)
                for path, content in sidecars.items():
                    if isinstance(content, str):
                        try:
                            content = download(content)
                        except OSError as e:
                            log.warning("cover download failed for %s: %s", book.meta.title, e)
                            continue
                    self._create(path, content, journal)
                    if path.stem == "cover" and book.type is BookType.AUDIO:
                        self._embed_cover(book, moves, m4b_file, content, journal)
                if self.mode == "move":
                    self._tidy_source(book, journal)
            self.processed.add(book)
            return BookResult(book, status, detail)
        except OSError as e:
            log.error("failed on %s: %s", book.main_file, e)
            return BookResult(book, "failed", str(e))

    @property
    def http(self) -> Http:
        if self._http is None:
            self._http = Http(Cache(self.folders.state / "cache.sqlite"))
        return self._http

    def _make_m4b(self, book: Book, target: Path, moves: list[Move], journal: Journal):
        """(m4b path or None, where its chapters came from, source files it replaces)."""
        files = m4b.audio_files_in_order(book)
        title = book.meta.title
        if len(files) == 1 and files[0].suffix.lower() == ".m4b":
            if not book.meta.asin:
                return None, "", set()
            total = m4b.duration(files[0])
            chapters = m4b.chapters_from_audible(self.http, book.meta.asin, total)
            if not chapters:
                return None, "", set()
            out = next((mv.dst for mv in moves if mv.src == files[0]), target / files[0].name)
            self._ensure_dir(out.parent, journal)
            self.on_detail(f"{title}: new chapters")
            m4b.rechapter(files[0], out, chapters, title, total, cancelled=self._cancelled)
            source = "audible"
        else:
            chapters, source, total = m4b.plan_chapters(book, files, self.http)
            out = target / f"{clean_name(title or files[0].stem)}.m4b"
            self._ensure_dir(out.parent, journal)
            m4b.convert(files, out, chapters, title, total, cancelled=self._cancelled,
                        on_progress=lambda f: self.on_detail(f"{title}: m4b {f:.0%}"))
        journal.write(op="create", dst=str(out), size=out.stat().st_size)
        cover = extract_cover(book)
        image = cover[0] if cover else None
        if image is None and book.meta.cover_url:
            try:
                image = download(book.meta.cover_url)
            except OSError:
                image = None
        if image:
            m4b.add_cover(out, image)
            journal.write(op="update", dst=str(out), size=out.stat().st_size)
        return out, source, set(files)

    def _transfer(self, src: Path, dst: Path, journal: Journal) -> None:
        if dst.exists():
            raise FileExistsError(f"already exists: {dst}")
        size = src.stat().st_size
        self._ensure_dir(dst.parent, journal)
        if self.mode == "move" and _same_drive(src, dst):
            os.rename(src, dst)  # refuses to replace an existing file on Windows
            journal.write(op="move", src=str(src), dst=str(dst), size=size)
            return
        tmp = dst.with_name(dst.name + ".partial")
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != size:
            tmp.unlink()
            raise OSError(f"copy is incomplete: {src}")
        os.rename(tmp, dst)
        if self.mode == "move":
            os.remove(src)
            journal.write(op="move", src=str(src), dst=str(dst), size=size)
        else:
            journal.write(op="copy", src=str(src), dst=str(dst), size=size)

    def _embed_cover(self, book: Book, moves: list[Move], m4b_file: Path | None, image: bytes,
                     journal: Journal) -> None:
        """The sorted .m4b/.m4a files get the cover inside too, when it beats their own art."""
        files = [m4b_file] if m4b_file else [mv.dst for mv in moves if not mv.in_place]
        files = [f for f in files if f and f.suffix.lower() in (".m4b", ".m4a")]
        if len(files) > 5:
            return  # many small files: cover.jpg is enough
        for f in files:
            try:
                if m4b.embed_cover_if_bigger(f, image):
                    journal.write(op="update", dst=str(f), size=f.stat().st_size)
            except OSError as e:
                log.warning("could not put the cover into %s: %s", f, e)

    def _tidy_source(self, book: Book, journal: Journal) -> None:
        """Move mode: what the book left in Incoming (cover.jpg, ABS's metadata.json...) goes to the
        Done folder, and its emptied folders are removed - nothing is deleted."""
        folder = book.folder or book.main_file.parent
        dest = done_dir(self.folders.done) / book.source_root.name
        for f in leftovers(folder, book.source_root):
            self._transfer(f, dest / f.relative_to(book.source_root), journal)
        if folder != book.source_root and folder.is_dir():
            remove_empty_dirs(folder)
        drop_empty_parents(folder.parent, book.source_root)

    def _create(self, path: Path, content: bytes, journal: Journal) -> None:
        if path.exists():
            return  # sidecars never replace what is there
        self._ensure_dir(path.parent, journal)
        with open(path, "xb") as fh:
            fh.write(content)
        journal.write(op="create", dst=str(path), size=len(content))

    @staticmethod
    def _ensure_dir(d: Path, journal: Journal) -> None:
        missing = []
        while not d.exists():
            missing.append(d)
            d = d.parent
        for p in reversed(missing):
            p.mkdir()
            journal.write(op="mkdir", path=str(p))


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a))[0].lower() == os.path.splitdrive(str(b))[0].lower()
