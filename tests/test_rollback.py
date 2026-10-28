"""A book that fails halfway is put back as it was - not left half in Sorted, half in Incoming."""
from pathlib import Path

from app.core.history import Journal, list_runs, undo


def test_a_failed_book_is_put_back_and_a_later_undo_leaves_it_alone(tmp_path):
    inc, out = tmp_path / "Incoming" / "Book", tmp_path / "Sorted" / "Author" / "Book"
    inc.mkdir(parents=True)
    a, b = inc / "1.mp3", inc / "2.mp3"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    j = Journal(tmp_path / "history", "sort")
    j.begin_book()
    out.mkdir(parents=True)
    j.write(op="mkdir", path=str(out.parent))
    j.write(op="mkdir", path=str(out))
    a.rename(out / "1.mp3")
    j.write(op="move", src=str(a), dst=str(out / "1.mp3"), size=3)
    (out / "cover.jpg").write_bytes(b"c")
    j.write(op="create", dst=str(out / "cover.jpg"), size=1)
    # ... 2.mp3 is locked: the book fails here
    assert j.rollback_book() == []
    j.close()
    assert a.read_bytes() == b"one" and b.exists()  # back in Incoming, whole
    assert not out.exists() and not out.parent.exists()  # nothing half-sorted left
    res = undo(list_runs(tmp_path / "history")[0])  # undoing the whole run later: nothing to redo, no noise
    assert res.skipped == [] and a.exists()


def test_a_book_that_succeeds_keeps_its_steps(tmp_path):
    j = Journal(tmp_path / "history", "sort")
    j.begin_book()
    j.write(op="mkdir", path=str(tmp_path / "x"))
    assert len(j.end_book()) == 1
    j.write(op="mkdir", path=str(tmp_path / "y"))  # not tracked any more
    assert j.end_book() == []
    j.close()
