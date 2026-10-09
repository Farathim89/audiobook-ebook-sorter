"""Carry out a Plan: copy (default) or move files, write sidecars, journal everything for undo.

Safety rules:
- never overwrite: an existing target fails that book, nothing else
- copy to '<name>.partial', check the size, then rename into place
- books with problems (duplicate, target exists, protected library...) are skipped
"""
from __future__ import annotations

import logging
import os
import threading
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Folders
from ..providers.http import Cache, Http
from . import m4b
from .covers import extract_cover
from .history import Journal, Processed, keep_old_tags
from .infolog import InfoLog, what_was_read
from .leftovers import done_dir, drop_empty_parents, leftovers, recycle_bin_on, to_recycle_bin
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
    duplicates: tuple[str, int] | None = None  # (run id, books) moved out of Incoming after the sort

    def count(self, status: str) -> int:
        return sum(r.status == status for r in self.results)


class Executor:
    def __init__(self, folders: Folders, mode: str = "copy", processed: Processed | None = None,
                 to_m4b: bool = False, on_detail: Callable[[str], None] | None = None,
                 redo_m4b: bool | None = None, on_step: Callable[[str, str], None] | None = None):
        """to_m4b: multi-file audiobooks become one chaptered .m4b. redo_m4b (default: the setting):
        books that already are one .m4b get Audible's chapter names, and with m4b_codec "aac" are
        re-encoded too. In Move mode the originals of a new file go to the Done folder."""
        if mode not in ("copy", "move"):
            raise ValueError(mode)
        self.folders, self.mode, self.to_m4b = folders, mode, to_m4b
        self.redo_m4b = folders.redo_m4b if redo_m4b is None else redo_m4b
        self._tls = threading.local()  # the book each worker thread is busy with
        self.workers = folders.workers or auto_workers()
        self.processed = processed or Processed(folders.state / "processed.txt")
        self.on_detail = on_detail or (lambda s: None)
        self.on_step = on_step  # (book title, what is happening) - the window shows them apart
        self._cancelled: Callable[[], bool] = lambda: False
        self._http: Http | None = None

    def run(self, plan: Plan, books: list[Book] | None = None,
            on_progress: Callable[[int, int, Book], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> RunResult:
        books = list(plan.targets) if books is None else books
        self._cancelled = cancelled
        self._title = ""
        self._kept_tags: set[str] = set()  # files whose old tags are saved in this run
        moves: dict[Book, list[Move]] = {}
        for mv in plan.moves:
            moves.setdefault(mv.book, []).append(mv)

        # the full play test plays parts at the same time: share the cores between the books running at once
        m4b.PLAY_PARTS = max(2, min(8, ((os.cpu_count() or 4) // 2) // max(1, self.workers)))
        self._owned: dict[str, Book] = {}  # source file -> the book of this run that moves it
        for b in books:
            for f in [*b.files, *b.extras, *(mv.src for mv in moves.get(b, []))]:
                self._owned.setdefault(os.path.normcase(str(f)), b)
        journal = Journal(self.folders.state / "history", self.mode)
        result = RunResult(journal.run_id)
        info = InfoLog(self.folders.state, journal.run_id)  # what each book's files said + what was decided
        def one(book: Book) -> BookResult | None:
            if cancelled():
                return None
            read = what_was_read(book)  # before anything moves
            r = self._book(book, plan, moves.get(book, []), journal)
            info.book(book, plan.targets.get(book), read, r.status, r.detail)
            return r

        try:
            if self.workers <= 1 or len(books) <= 1:
                for i, book in enumerate(books, 1):
                    if (r := one(book)) is None:
                        break
                    result.results.append(r)
                    if on_progress:
                        on_progress(i, len(books), book)
            else:
                # several books at once: encoding and play tests use one CPU core each
                from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415
                done: dict[Book, BookResult] = {}
                # books in the same folder (an audiobook and its ebook side by side) one after another,
                # never at the same time - they share cover.jpg, metadata.json...
                groups: dict[str, list[Book]] = {}
                for b in books:
                    groups.setdefault(os.path.normcase(str(b.folder or b.main_file.parent)), []).append(b)

                def group(bs: list[Book]) -> list[tuple[Book, BookResult | None]]:
                    return [(b, one(b)) for b in bs]

                count = 0
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    for fut in as_completed([pool.submit(group, g) for g in groups.values()]):
                        for b, r in fut.result():
                            count += 1
                            if r is not None:
                                done[b] = r
                            if on_progress:
                                on_progress(count, len(books), b)
                result.results = [done[b] for b in books if b in done]  # in the order they were given
        finally:
            journal.close()
            self.processed.save()
        return result

    # per book - and several books run at once, so per thread
    _title = property(lambda s: getattr(s._tls, "title", ""), lambda s, v: setattr(s._tls, "title", v))
    _notes = property(lambda s: getattr(s._tls, "notes", []), lambda s, v: setattr(s._tls, "notes", v))
    _full_ok = property(lambda s: getattr(s._tls, "full_ok", None), lambda s, v: setattr(s._tls, "full_ok", v))

    def _step(self, text: str) -> None:
        """Tell the window what is happening right now: '<title> - adding Audible chapters'."""
        self.on_detail(f"{self._title} - {text}" if self._title else text)
        if self.on_step:
            self.on_step(self._title, text)

    def _book(self, book: Book, plan: Plan, moves: list[Move], journal: Journal) -> BookResult:
        self._title = book.meta.title or book.main_file.stem
        self._notes: list[str] = []  # play-test problems etc., shown with the result
        self._full_ok: bool | None = None  # True: every new file of this book passed a FULL play test
        if problems := plan.problems.get(book):
            return BookResult(book, "skipped", "; ".join(problems))
        if book.status is Status.UNKNOWN:
            return BookResult(book, "skipped", "unknown book")
        try:
            target = plan.targets[book]
            self._step("reading covers and metadata")
            # read the covers etc. BEFORE moving: in Move mode the book's own files are gone afterwards
            sidecars = sidecar_files(book, target) if book not in plan.review else {}
            m4b_file, chapters_from = None, ""
            replaced: set[Path] = set()  # originals a new file was made from (m4b, xHE fix): Done in Move mode
            fix_xhe = self.folders.fix_xhe_aac and book.type is BookType.AUDIO
            if (self.to_m4b or self.redo_m4b) and book.type is BookType.AUDIO and book not in plan.review:
                m4b_file, chapters_from, handled = self._make_m4b(book, target, moves, journal)
                moves = [mv for mv in moves if mv.src not in handled]
                replaced |= handled
                if m4b_file and fix_xhe and m4b.is_xhe_aac(m4b_file):  # a re-chaptered xHE m4b: our own file
                    self._step("re-encoding xHE-AAC -> AAC (slow)")
                    fixed = m4b_file.with_name(m4b_file.stem + ".aac-lc" + m4b_file.suffix)
                    m4b.reencode_aac(m4b_file, fixed, self._cancelled)
                    if problem := self._play_test(fixed, m4b.duration(m4b_file)):
                        os.remove(fixed)
                        self._notes.append(f"the xHE-AAC fix failed the play test ({problem}) - kept as xHE-AAC")
                    else:
                        os.replace(fixed, m4b_file)
                        journal.write(op="update", dst=str(m4b_file), size=m4b_file.stat().st_size)
            for mv in moves:
                if mv.in_place:
                    continue
                if fix_xhe and m4b.is_xhe_aac(mv.src):
                    # converted straight from the original, which stays where it is (also in Move mode)
                    self._step(f"re-encoding xHE-AAC -> AAC: {mv.src.name} (slow)")
                    self._ensure_dir(mv.dst.parent, journal)
                    m4b.reencode_aac(mv.src, mv.dst, self._cancelled)
                    journal.write(op="create", dst=str(mv.dst), size=mv.dst.stat().st_size)
                    if problem := self._play_test(mv.dst, m4b.duration(mv.src)):
                        os.remove(mv.dst)
                        self._notes.append(f"the xHE-AAC fix of {mv.src.name} failed the play test ({problem}) "
                                           "- sorted the original as it is")
                        self._transfer(mv.src, mv.dst, journal)
                    else:
                        replaced.add(mv.src)
                else:
                    self._step(f"{self._verb(mv.src, mv.dst)} {mv.src.name} ({_size(mv.src)})")
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
                    self._step("writing tags")
                    for mv in moves:
                        if not mv.in_place and self.mode == "move":
                            self._keep_tags(mv.dst, journal)  # your original: undo puts its tags back
                        if not mv.in_place and write_tags(mv.dst, book):
                            journal.write(op="update", dst=str(mv.dst), size=mv.dst.stat().st_size)
                    if m4b_file and write_tags(m4b_file, book, whole_book=True):
                        journal.write(op="update", dst=str(m4b_file), size=m4b_file.stat().st_size)
                for path, content in sidecars.items():
                    if isinstance(content, str):
                        self._step(f"downloading {path.name}")
                        try:
                            content = download(content)
                        except OSError as e:
                            log.warning("cover download failed for %s: %s", book.meta.title, e)
                            continue
                    self._step(f"writing {path.name}")
                    self._create(path, content, journal)
                    if path.stem == "cover" and book.type is BookType.AUDIO:
                        self._step("putting the cover inside the audio file")
                        if self.mode == "move" and not m4b_file:
                            for mv in moves:
                                if not mv.in_place:
                                    self._keep_tags(mv.dst, journal)
                        self._embed_cover(book, moves, m4b_file, content, journal)
                self._fix_abs_metadata(book, target, moves, journal)
                edited = [m4b_file] if m4b_file else [mv.dst for mv in moves if not mv.in_place]
                edited = [f for f in edited if f and f.suffix.lower() in (".m4b", ".m4a", ".mp3", ".flac", ".ogg")]
                if len(edited) <= 5 and (self.folders.write_tags or m4b_file):
                    for f in edited:
                        if problem := self._play_test(f):
                            if f == m4b_file:  # our own file: the originals are still in Incoming - keep them
                                os.remove(f)
                                raise OSError(f"the new m4b failed the play test after tags/cover ({problem}) - "
                                              "removed it; the original is still in Incoming")
                            self._notes.append(f"{f.name} failed the play test after tags/cover: {problem}")
                if self.mode == "move":
                    self._step("moving the originals' leftovers to Done")
                    self._tidy_source(book, journal, replaced)
            self.processed.add(book)
            if self._notes:
                detail += " - " + "; ".join(self._notes)
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
            # already one m4b: only with the 'M4B -> M4B' switch (MP3 -> M4B leaves it alone)
            if not self.redo_m4b:
                return None, "", set()
            total = m4b.duration(files[0])
            chapters = m4b.chapters_from_audible(self.http, book.meta.asin, total) if book.meta.asin else []
            if chapters and m4b.chapters_are_good(m4b.chapters_in(files[0]), chapters):
                chapters = []  # its own chapters are fine: no new file just for the same chapters
            # xHE-AAC is never re-encoded by ffmpeg (dropouts): the xHE fix (Windows' decoder) does it
            encode = self.folders.m4b_codec == "aac" and not m4b.is_xhe_aac(files[0])
            if not chapters and not encode:
                return None, "", set()
            out = next((mv.dst for mv in moves if mv.src == files[0]), target / files[0].name)
            self._ensure_dir(out.parent, journal)
            if encode and chapters:
                self._step("re-encoding to AAC with Audible's chapters")
                m4b.encode_m4b(files[0], out, chapters, title, total, cancelled=self._cancelled,
                               on_progress=lambda f: self._step(f"re-encoding to AAC with Audible's chapters {f:.0%}"))
            elif encode:
                self._step("re-encoding to AAC (keeps its own chapters)")
                m4b.reencode_aac(files[0], out, self._cancelled)
            else:
                self._step("making a new m4b with Audible's chapters (audio kept as it is)")
                m4b.rechapter(files[0], out, chapters, title, total, cancelled=self._cancelled)
            source = "audible" if chapters else "the book"
        elif not self.to_m4b:
            return None, "", set()
        else:
            chapters, source, total = m4b.plan_chapters(book, files, self.http)
            out = target / f"{clean_name(title or files[0].stem)}.m4b"
            self._ensure_dir(out.parent, journal)
            m4b.convert(files, out, chapters, title, total, cancelled=self._cancelled,
                        on_progress=lambda f: self._step(f"joining {len(files)} files into one m4b {f:.0%}"))
        journal.write(op="create", dst=str(out), size=out.stat().st_size)
        cover = extract_cover(book)
        image = cover[0] if cover else None
        if image is None and book.meta.cover_url:
            try:
                image = download(book.meta.cover_url)
            except OSError:
                image = None
        if image:
            self._step("adding the cover to the new m4b")
            m4b.add_cover(out, image)
            journal.write(op="update", dst=str(out), size=out.stat().st_size)
        if problem := self._play_test(out, total):
            # never trade a good original for a broken copy: drop ours, the original is sorted as it is
            os.remove(out)
            self._notes.append(f"the new m4b failed the play test ({problem}) - sorted the original as it is")
            log.warning("%s: new m4b failed the play test: %s", title, problem)
            return None, "", set()
        return out, source, set(files)

    def _keep_tags(self, path: Path, journal: Journal) -> None:
        keep_old_tags(journal, self.folders.state / "replaced" / journal.run_id, path, self._kept_tags)

    def _play_test(self, path: Path, expected: float | None = None) -> str:
        """'' when the file plays (or testing is off), else what is wrong with it."""
        how = self.folders.play_test
        if how == "off" or not path.is_file():
            return ""
        self._step(f"play-testing {path.name} ({'whole file' if how == 'full' else 'start, middle, end'})")
        problem = m4b.play_test(path, expected or None, full=how == "full", cancelled=self._cancelled)
        if how == "full" and not self._cancelled():
            self._full_ok = not problem and self._full_ok is not False
        return problem

    def _verb(self, src: Path, dst: Path) -> str:
        if self.mode != "move":
            return "copying"
        return "moving" if _same_drive(src, dst) else "copying (other drive)"

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

    def _tidy_source(self, book: Book, journal: Journal, replaced: set[Path] = frozenset()) -> None:
        """Move mode: the originals a new file was made from (re-chaptered / converted m4b) and what
        the book left in Incoming (cover.jpg, ABS's metadata.json...) go to the Done folder, and its
        emptied folders are removed - nothing is deleted."""
        folder = book.folder or book.main_file.parent
        dest = done_dir(self.folders.done) / book.source_root.name
        # an old copy only goes for good when its new file passed a FULL play test - else it stays in Done
        may_remove = self._full_ok is True
        if replaced and not self.folders.keep_done and not may_remove:
            self._notes.append("old copy kept in Done - the new file had no full play test"
                               if self.folders.play_test != "full" else "old copy kept in Done")
        for f in sorted(replaced):
            if f.is_file():
                rel = f.relative_to(book.source_root) if book.source_root in f.parents else Path(f.name)
                self._to_done(f, dest / rel, journal, may_remove)
        rest = leftovers(folder, book.source_root)
        owned = getattr(self, "_owned", {})
        if any(owned.get(os.path.normcase(str(f)), book) is not book for f in rest):
            return  # another book of this run still lives here (its cover.jpg, book info.txt...): not ours
        for f in rest:
            self._to_done(f, dest / f.relative_to(book.source_root), journal)
        if folder != book.source_root and folder.is_dir():
            remove_empty_dirs(folder)
        drop_empty_parents(folder.parent, book.source_root)

    def _to_done(self, src: Path, dst: Path, journal: Journal, may_remove: bool = True) -> None:
        """Into the Done folder - or, with 'Keep old copies in Done' off, away: the Recycle Bin when the
        drive has one, else deleted for good. (Only called after the sorted book is complete; an old
        audio copy only with may_remove = its new file passed a full play test.)"""
        if self.folders.keep_done or not may_remove:
            self._transfer(src, dst, journal)
            return
        size = src.stat().st_size
        if recycle_bin_on(src):
            to_recycle_bin(src)
            journal.write(op="delete", src=str(src), size=size, how="recycle bin")
        else:
            os.remove(src)
            journal.write(op="delete", src=str(src), size=size, how="deleted for good")

    def _fix_abs_metadata(self, book: Book, target: Path, moves, journal: Journal) -> None:
        """The download's metadata.json came along - ABS reads it before the tags: it gets what was
        decided (the original is saved; undo writes it back)."""
        from .absmeta import corrected, dumps, read_abs_json  # noqa: PLC0415
        from .history import keep_old_contents  # noqa: PLC0415
        path = target / "metadata.json"
        if not any(not mv.in_place and os.path.normcase(str(mv.dst)) == os.path.normcase(str(path)) for mv in moves):
            return  # not one we brought here
        original = read_abs_json(path)
        if original is None:
            return
        new = corrected(original, book.meta)
        if new == original:
            return
        self._step("writing metadata.json (what ABS reads first)")
        keep_old_contents(journal, self.folders.state / "replaced" / journal.run_id, path)
        data = dumps(new)
        path.write_bytes(data)
        journal.write(op="update", dst=str(path), size=len(data))

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
            try:
                p.mkdir()
            except FileExistsError:
                continue  # another book (sorted at the same time) made it a moment ago
            journal.write(op="mkdir", path=str(p))


def auto_workers() -> int:
    """Books sorted at once: 3 at most (encoding and play tests use a core each, and they share one
    disk - more makes the PC sluggish), fewer on a small PC."""
    return max(1, min(3, (os.cpu_count() or 4) // 4))


def _size(path: Path) -> str:
    try:
        n = path.stat().st_size
    except OSError:
        return "?"
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{max(n / 2**20, 0.1):.1f} MB" if n >= 2**20 else f"{n // 1024} KB"


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a))[0].lower() == os.path.splitdrive(str(b))[0].lower()
