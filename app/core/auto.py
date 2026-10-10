"""Watch mode: sort only the books we are SURE about, leave the rest for the GUI.

Used by the GUI timer and by `python -m app.cli --auto` (e.g. from Task Scheduler).
Copies, or moves when Settings say so; never touches books that are still downloading.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..config import Folders
from .consistency import unify_names
from .duplicates import best_copy, describe, plan_duplicate_moves
from .executor import Executor, RunResult
from .history import Processed
from .models import Book, Status
from .online import OnlineLookup
from .pipeline import load_books
from .planner import Planner

SETTLE_SECONDS = 120
DOWNLOADING_EXTS = {".part", ".!qb", ".crdownload", ".tmp", ".partial", ".!ut", ".aria2"}


@dataclass
class AutoResult:
    found: int = 0
    waiting: list[Book] = field(default_factory=list)   # still downloading
    left: list[Book] = field(default_factory=list)      # need the user (check / review / problems)
    run: RunResult | None = None

    @property
    def summary(self) -> str:
        sorted_ = self.run.count("done") if self.run else 0
        return (f"{sorted_} sorted, {len(self.left)} need you, {len(self.waiting)} still downloading"
                f" ({self.found} new)")


def find_duplicates(folders: Folders, lookup: OnlineLookup | None = None,
                    on_progress: Callable[[int, int, Book], None] | None = None,
                    cancelled: Callable[[], bool] = lambda: False, scanned_at: float | None = None):
    """Every book in Incoming (also the ones marked sorted): which copies can leave for the
    Duplicates folder, and which stay because they beat the library's copy.
    scanned_at: after a sort you started - a file unchanged since that scan is finished (you saw it in the
    list), however new it is; only one changed since then may still be downloading."""
    books = [b for b in load_books([p for p in folders.sources if p.is_dir()])
             if not still_downloading(b) or (scanned_at and unchanged_since(b, scanned_at))]
    (lookup or OnlineLookup(folders)).run(books, on_progress=on_progress, cancelled=cancelled)
    unify_names(books, folders.library_roots())
    processed = Processed(folders.state / "processed.txt")
    sorted_here = {b for b in books if processed.contains(b)}  # originals this app sorted: they stay
    return plan_duplicate_moves(books, Planner(folders).plan(books), stay=sorted_here)


def unchanged_since(book: Book, when: float) -> bool:
    """No file of the book changed after `when` and none is a part file ('.part', '.!qB')."""
    try:
        if any(f.stat().st_mtime > when for f in [*book.files, *book.extras]):
            return False
        if book.folder and any(p.suffix.lower() in DOWNLOADING_EXTS for p in book.folder.rglob("*")):
            return False
    except OSError:
        return False
    return True


def still_downloading(book: Book, now: float | None = None) -> bool:
    now = now or time.time()
    files = [*book.files, *book.extras]
    try:
        if any(now - f.stat().st_mtime < SETTLE_SECONDS for f in files):
            return True
    except OSError:
        return True
    if book.folder:
        try:
            return any(p.suffix.lower() in DOWNLOADING_EXTS for p in book.folder.rglob("*"))
        except OSError:
            return True
    return False


def auto_sort(folders: Folders, processed: Processed | None = None,
              log: Callable[[str], None] = print, cancelled: Callable[[], bool] = lambda: False,
              sources: list | None = None) -> AutoResult:
    from .library import empty_done  # noqa: PLC0415
    for d in empty_done(folders):
        log(f"Done folder: {d.name} went to the Recycle Bin")
    processed = processed or Processed(folders.state / "processed.txt")
    sources = [p for p in (sources or folders.sources) if p.is_dir()]
    from .archives import unpack_archives  # noqa: PLC0415
    for line in unpack_archives(sources, folders.done):
        log(line)
    books = [b for b in load_books(sources) if not processed.contains(b)]
    result = AutoResult(found=len(books))
    if not books:
        return result
    result.waiting = [b for b in books if still_downloading(b)]
    books = [b for b in books if b not in result.waiting]
    log(f"{len(books)} new book(s), {len(result.waiting)} still downloading")
    OnlineLookup(folders).run(books, cancelled=cancelled)
    unify_names(books, folders.library_roots())
    plan = Planner(folders).plan(books)
    # copies of the same book: keep the most complete one (the others just stay in Incoming)
    extra_copies: set[Book] = set()
    for book in books:
        if book in plan.duplicates and book not in extra_copies:
            group = plan.duplicate_group(book)
            if all(b.status is Status.READY for b in group):
                keep = best_copy([describe(b) for b in group]).book
                extra_copies |= {b for b in group if b is not keep}
    if extra_copies:
        log(f"{len(extra_copies)} extra cop{'y' if len(extra_copies) == 1 else 'ies'} of books left in Incoming")
        books = [b for b in books if b not in extra_copies]
        plan = Planner(folders).plan(books)
    if plan.identical:  # already in the library with the same files: nothing to do
        for b in plan.identical:
            processed.add(b)
        processed.save()
        log(f"{len(plan.identical)} book(s) already in the library (identical) - marked as sorted")
        books = [b for b in books if b not in plan.identical]
        plan = Planner(folders).plan(books)
    sure = [b for b in books if b.status is Status.READY and not plan.problems.get(b) and b not in plan.review]
    result.left = [b for b in books if b not in sure]
    if sure and not cancelled():
        result.run = Executor(folders, "move" if folders.move_originals else "copy", processed, to_m4b=folders.to_m4b,
                              redo_m4b=folders.redo_m4b).run(plan, sure, cancelled=cancelled)
    if folders.auto_move_duplicates and not cancelled():
        from .library import move_duplicates  # noqa: PLC0415
        moves, _ = find_duplicates(folders, cancelled=cancelled)
        if moves:
            run_id, n = move_duplicates(folders, moves, cancelled)
            log(f"{n} duplicate(s) moved to {folders.duplicates} (undo: {run_id})")
    log(result.summary)
    return result
