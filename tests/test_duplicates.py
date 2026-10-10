from pathlib import Path

from app.config import Folders
from app.core.duplicates import best_copy, describe
from app.core.pipeline import load_books
from app.core.planner import Planner


def test_duplicate_group_and_best_copy(tmp_path):
    src = tmp_path / "in"
    for rel, size in (("Frank Herbert/Dune.epub", 10), ("other/Frank Herbert - Dune.epub", 500),
                      ("third/Frank Herbert - Dune.epub", 50)):
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    books = load_books([src])
    plan = Planner(Folders(books_sorted=tmp_path / "out", protected=[])).plan(books)
    group = plan.duplicate_group(books[0])
    assert len(group) == 3
    best = best_copy([describe(b) for b in group])
    assert best.size == 500 and "epub" in best.summary

    # keeping one and skipping the rest clears the clash
    rest = [b for b in books if b is not best.book]
    plan = Planner(Folders(books_sorted=tmp_path / "out", protected=[])).plan([best.book])
    assert not plan.problems and rest


def test_chapters_beat_a_little_extra_length():
    from pathlib import Path
    from app.core.duplicates import CopyInfo, best_copy, clearly_better
    from app.core.models import Book, BookType
    def info(d, ch):
        return CopyInfo(Book(BookType.AUDIO, [Path("x.m4b")], Path(".")), 1, 300_000_000, "m4b", d, 125, ch)
    plain, chaptered = info(20300, 0), info(19800, 11)
    assert not clearly_better(plain, chaptered) and best_copy([plain, chaptered]) is chaptered
    assert clearly_better(info(25000, 0), chaptered)  # much longer: the other one is missing a part


def test_a_fresh_download_you_scanned_counts_as_finished(tmp_path):
    import os
    import time
    from app.core.auto import still_downloading, unchanged_since
    from app.core.models import Book, BookType
    f = tmp_path / "86 v08.epub"
    f.write_bytes(b"x")
    b = Book(BookType.EBOOK, [f], tmp_path)
    assert still_downloading(b)                    # written just now
    assert unchanged_since(b, time.time() + 1)     # ... but not touched since the scan
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert not unchanged_since(b, time.time() + 1)  # changed after the scan: maybe still downloading
    (tmp_path / "next.epub.part").write_bytes(b"")
    b.folder = tmp_path
    assert not unchanged_since(b, time.time() + 10)
