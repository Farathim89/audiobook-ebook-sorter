"""Fixes from the audit: file safety (library as a source, saved-tag numbering, undo retry, copies across drives)."""
import json
import threading
from pathlib import Path

from app.config import Folders
from app.core.history import Journal, _save_new, list_runs, undo
from app.core.models import Book, BookType, Metadata, Status
from app.core.planner import Planner


def test_a_book_inside_a_protected_library_is_never_sorted_from_there(tmp_path):
    lib = tmp_path / "Library"
    f = lib / "Author" / "Book" / "Book.m4b"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    b = Book(BookType.AUDIO, [f], lib, folder=f.parent)
    b.meta = Metadata(title="Book", authors=["Author"])
    b.status = Status.READY
    folders = Folders(audio_sorted=tmp_path / "Sorted", state=tmp_path / "s", protected=[lib])
    plan = Planner(folders).plan([b])
    assert any("protected" in p for p in plan.problems.get(b, []))


def test_saved_snapshots_never_share_a_number_across_threads(tmp_path):
    out = []
    threads = [threading.Thread(target=lambda i=i: [out.append(_save_new(tmp_path, ".tags", bytes([i])))
                                                      for _ in range(40)]) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 120 == len(list(tmp_path.iterdir()))


def test_an_undo_with_a_failed_step_stays_undoable(tmp_path, monkeypatch):
    import shutil
    hist = tmp_path / "history"
    j = Journal(hist, "sort")
    src, dst = tmp_path / "a.m4b", tmp_path / "b.m4b"
    dst.write_bytes(b"x")
    j.write(op="move", src=str(src), dst=str(dst), size=1)
    j.close()

    def locked(*a, **k):
        raise PermissionError("in use by a player")
    monkeypatch.setattr(shutil, "move", locked)
    res = undo(list_runs(hist)[0])
    assert any("Undo this run again" in s for s in res.skipped)
    assert not list_runs(hist)[0].undone  # still undoable
    monkeypatch.undo()
    undo(list_runs(hist)[0])  # the player is closed: the rest goes back
    assert src.exists() and list_runs(hist)[0].undone
