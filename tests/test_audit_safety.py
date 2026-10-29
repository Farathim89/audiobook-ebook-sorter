"""Second audit (data safety): Undo keeps a copy whose original is gone, read-only originals, re-encodes that
can't swap in, leftovers of a book without its own folder, a book folder inside another, 'Title - Author'."""
import os
import stat
from pathlib import Path

import pytest

from app.config import Folders
from app.core import m4b
from app.core.executor import Executor
from app.core.history import list_runs, undo
from app.core.pipeline import load_books
from app.core.planner import Planner


@pytest.fixture(autouse=True)
def _no_play_test(monkeypatch):
    monkeypatch.setattr(m4b, "play_test", lambda *a, **kw: "")


def _folders(tmp_path, **kw):
    return Folders(audio_sorted=tmp_path / "out_audio", books_sorted=tmp_path / "out_books",
                   review=tmp_path / "review", state=tmp_path / "state", done=tmp_path / "done",
                   protected=[], **kw)


def _write(root: Path, rel: str, data: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _run(folders):
    return next(r for r in list_runs(folders.state / "history"))


# 1. Copy mode: the original is gone afterwards (user cleaned Incoming / torrent removed) - Undo deletes the
#    library copy, which is now the only copy.
def test_undo_of_copy_run_keeps_the_only_copy(tmp_path):
    src = tmp_path / "in"
    orig = _write(src, "Frank Herbert/Dune.epub", b"epub bytes")
    folders = _folders(tmp_path)
    books = load_books([src])
    res = Executor(folders).run(Planner(folders).plan(books))
    assert res.count("done") == 1
    sorted_copy = next((tmp_path / "out_books").rglob("Dune.epub"))
    orig.unlink()                       # Incoming cleaned up after the sort
    undo(_run(folders))
    assert sorted_copy.exists() or orig.exists(), "Undo deleted the last copy of the book"


# 2. Read-only download: the copy keeps the read-only flag; a failure later in the book (metadata.json fix)
#    rolls back, but the rollback can't delete read-only copies; they are marked 'rolled-back' anyway, so no
#    Undo will ever take them out of the library.
def test_read_only_book_failure_leaves_no_orphans(tmp_path):
    src = tmp_path / "in"
    d = "Some Author/Some Book"
    a = _write(src, f"{d}/Some Book.m4b", b"audio")
    meta = _write(src, f"{d}/metadata.json",
                  b'{"title": "Wrong", "authors": ["Some Author"]}')
    for p in (a, meta):
        os.chmod(p, stat.S_IREAD)
    folders = _folders(tmp_path, write_tags=False, fix_xhe_aac=False)
    books = load_books([src])
    books[0].meta.title = "Some Book"
    try:
        res = Executor(folders).run(Planner(folders).plan(books))
        print([(r.status, r.detail) for r in res.results])
        undo(_run(folders))
        left = [p for p in (tmp_path / "out_audio").rglob("*") if p.is_file()] if (tmp_path / "out_audio").exists() else []
        assert not left, f"files left in the library after fail + undo: {left}"
    finally:
        for p in [*src.rglob("*"), *tmp_path.rglob("*")]:
            if p.is_file():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


# 2b. Plain copy-mode undo of a read-only book fails for good ('Access is denied'), run never undoable.
def test_undo_of_read_only_copy(tmp_path):
    src = tmp_path / "in"
    orig = _write(src, "Frank Herbert/Dune.epub", b"epub bytes")
    os.chmod(orig, stat.S_IREAD)
    folders = _folders(tmp_path)
    try:
        Executor(folders).run(Planner(folders).plan(load_books([src])))
        r = undo(_run(folders))
        print(r.skipped)
        assert not list((tmp_path / "out_books").rglob("Dune.epub")), r.skipped
    finally:
        for p in tmp_path.rglob("*"):
            if p.is_file():
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


# 3. Tools > fix xHE-AAC: the original is in use (a player / ABS stream) - parking it fails, and the new
#    '<name>.aac-lc.m4b' stays in the book folder, never journaled (Undo can't remove it; ABS sees 2 files).
def test_fix_xhe_leaves_no_unjournaled_file_when_park_fails(tmp_path, monkeypatch):
    from app.core.library import LibraryEditor
    book = tmp_path / "lib" / "Author" / "Book"
    f = _write(book, "Book.m4b", b"xhe")
    monkeypatch.setattr(m4b, "reencode_aac", lambda s, o, c=None: o.write_bytes(b"aac"))
    monkeypatch.setattr(m4b, "duration", lambda p: 1.0)
    folders = _folders(tmp_path, play_test="off")
    ed = LibraryEditor(folders, "tools")
    with open(f, "rb"):                 # open elsewhere -> rename refused on Windows
        with pytest.raises(OSError):
            ed.fix_xhe_aac(f)
    ed.close()
    extra = [p.name for p in book.iterdir() if p.name != "Book.m4b"]
    assert not extra, f"un-journaled files left next to the book: {extra}"


# 4. Move mode with 'Keep old copies in Done' off on a drive without Recycle Bin: an ebook directly in an
#    author folder - its 'leftovers' are everything under the author folder, also the user's own sub-folders.
def test_keep_done_off_does_not_delete_unrelated_files(tmp_path, monkeypatch):
    import app.core.executor as ex
    monkeypatch.setattr(ex, "recycle_bin_on", lambda p: False)
    src = tmp_path / "in"
    _write(src, "Frank Herbert/Dune.epub", b"epub")
    notes = _write(src, "Frank Herbert/My reading notes/notes.txt", b"my own notes")
    folders = _folders(tmp_path)
    folders.keep_done = False
    res = Executor(folders, mode="move").run(Planner(folders).plan(load_books([src])))
    assert res.count("done") == 1, res.results
    assert notes.exists() or list((tmp_path / "done").rglob("notes.txt")), \
        "the user's notes.txt (not part of the book) was deleted for good"


# 5. Planning: a stand-alone 'Dune' and 'Dune Messiah' (series 'Dune' #2) by the same author: the first
#    book's folder IS the second one's series folder - one book nested inside another.
def test_standalone_book_folder_is_not_another_books_series_folder(tmp_path):
    from app.core.models import Book, BookType, Metadata
    folders = _folders(tmp_path)
    a = Book(BookType.EBOOK, [tmp_path / "in/a/Dune.epub"], tmp_path / "in",
             meta=Metadata(title="Dune", authors=["Frank Herbert"]))
    b = Book(BookType.EBOOK, [tmp_path / "in/b/Dune Messiah.epub"], tmp_path / "in",
             meta=Metadata(title="Dune Messiah", authors=["Frank Herbert"], series="Dune", series_index=2))
    plan = Planner(folders).plan([a, b])
    ta, tb = plan.targets[a], plan.targets[b]
    print(ta, tb)
    nested = ta in tb.parents or tb in ta.parents
    assert not nested or plan.problems.get(a) or plan.problems.get(b), f"{tb} is inside {ta}"


def test_fahrenheit_in_author_folder(tmp_path):
    src = tmp_path / "in"
    f = src / "Ray Bradbury" / "Fahrenheit 451 - Ray Bradbury.epub"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"epub")
    folders = Folders(books_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state", protected=[])
    books = load_books([src])
    m = books[0].meta
    plan = Planner(folders).plan(books)
    print(m.title, m.authors, m.series, m.series_index, plan.targets[books[0]], plan.review)
    assert m.series is None and m.title == "Fahrenheit 451", (m.title, m.authors, m.series, m.series_index)


def test_a_fixed_xhe_file_and_its_undo(tmp_path, monkeypatch):
    from app.core.library import LibraryEditor
    book = tmp_path / "lib" / "Author" / "Book"
    f = _write(book, "Book.m4b", b"xhe")
    monkeypatch.setattr(m4b, "reencode_aac", lambda s, o, c=None: o.write_bytes(b"aac-lc"))
    monkeypatch.setattr(m4b, "duration", lambda p: 1.0)
    folders = _folders(tmp_path, play_test="off")
    ed = LibraryEditor(folders, "tools")
    ed.fix_xhe_aac(f)
    ed.close()
    assert f.read_bytes() == b"aac-lc" and [p.name for p in book.iterdir() if p.is_file()] == ["Book.m4b"]
    result = undo(_run(folders))
    assert f.read_bytes() == b"xhe" and sorted(p.name for p in book.iterdir()) == ["Book.m4b"], result.skipped


def test_undo_keeps_only_the_new_files_whose_own_original_was_deleted(tmp_path):
    from app.core.history import UndoResult, _reverse
    a, b = _write(tmp_path, "lib/A/A.m4b", b"a"), _write(tmp_path, "lib/B/B.m4b", b"b")
    b_done = _write(tmp_path, "done/b.mp3", b"mp3")  # B's original went to Done, A's was deleted
    entries = [{"op": "create", "dst": str(a), "size": 1, "made_from": [str(tmp_path / "in/a.mp3")]},
               {"op": "delete", "src": str(tmp_path / "in/a.mp3"), "size": 3, "how": "deleted for good"},
               {"op": "create", "dst": str(b), "size": 1, "made_from": [str(tmp_path / "in/b.mp3")]},
               {"op": "move", "src": str(tmp_path / "in/b.mp3"), "dst": str(b_done), "size": 3}]
    _reverse(entries, UndoResult())
    assert a.exists() and not b.exists() and (tmp_path / "in/b.mp3").exists()  # no duplicate of B left


def test_two_runs_in_the_same_second_get_their_own_journal(tmp_path):
    from app.core.history import Journal
    a, b = Journal(tmp_path, "copy"), Journal(tmp_path, "move")
    assert a.path != b.path
    a.close()
    b.close()
