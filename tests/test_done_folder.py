import os
import time
from pathlib import Path

from app.config import Folders
from app.core.executor import Executor
from app.core.history import Processed, list_runs, undo
from app.core.library import find_sorted_originals, move_sorted_originals
from app.core.pipeline import load_books
from app.core.planner import Planner
from app.core.library import empty_done
from datetime import date, timedelta

DAY = date.today().isoformat()

FILES = {
    "Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/Black Summoner Volume 01.epub": b"epub",
    "Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/cover.jpg": b"img",
    "Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/metadata.json": b"{}",
    "Eminence/Volume 01/The Eminence in Shadow v01 (2021).cbz": b"cbz",
    "Eminence/Volume 01/cover.jpg": b"img",
    "Eminence/Volume 01/metadata.json": b"{}",
}


def _setup(tmp_path: Path):
    src = tmp_path / "Books Incoming"
    old = time.time() - 3600  # not 'still downloading'
    for rel, data in FILES.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.utime(p, (old, old))
    folders = Folders(books_incoming=src, audio_incoming=tmp_path / "none", books_sorted=tmp_path / "sorted",
                      review=tmp_path / "review", done=tmp_path / "done", state=tmp_path / "state", protected=[])
    return src, folders


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()} if root.exists() else set()


def test_move_mode_sends_leftovers_to_done_and_empties_incoming(tmp_path):
    src, folders = _setup(tmp_path)
    books = [b for b in load_books([src]) if b.main_file.suffix == ".epub"]
    result = Executor(folders, mode="move").run(Planner(folders).plan(books), books)
    assert result.count("done") == 1
    assert _files(tmp_path / "done") == {
        f"{DAY}/Books Incoming/Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/cover.jpg",
        f"{DAY}/Books Incoming/Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/metadata.json"}
    assert not (src / "Doufu Mayoi").exists()  # emptied folders are gone
    assert (src / "Eminence/Volume 01/cover.jpg").exists()  # another book's folder is left alone

    undo(next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id))
    assert _files(src) == set(FILES)


def test_copy_mode_leaves_incoming_alone(tmp_path):
    src, folders = _setup(tmp_path)
    Executor(folders).run(Planner(folders).plan(load_books([src])))
    assert _files(src) == set(FILES) and not (tmp_path / "done").exists()


def test_clean_sorted_originals_and_leftover_folders(tmp_path):
    src, folders = _setup(tmp_path)
    (src / "Doufu Mayoi/Black Summoner/Black Summoner Volume 01 - The Bound Demon/Black Summoner Volume 01.epub").unlink()
    processed = Processed(folders.state / "processed.txt")
    for b in load_books([src]):
        processed.add(b)  # the manga was sorted (copied) before
    processed.save()

    books, orphans = find_sorted_originals(folders)
    assert [b.main_file.name for b in books] == ["The Eminence in Shadow v01 (2021).cbz"]
    assert [d.name for d, _ in orphans] == ["Doufu Mayoi"]
    run_id, n = move_sorted_originals(folders, books, orphans)
    assert n == 2 and _files(src) == set() and not list(src.iterdir())
    assert f"{DAY}/Books Incoming/Eminence/Volume 01/metadata.json" in _files(tmp_path / "done")

    undo(next(r for r in list_runs(folders.state / "history") if r.run_id == run_id))
    assert len(_files(src)) == 5


def test_empty_done_sends_only_old_day_folders_to_the_recycle_bin(tmp_path):
    folders = Folders(done=tmp_path / "done", state=tmp_path / "state", protected=[], done_days=30)
    old = (date.today() - timedelta(days=31)).isoformat()
    for day in (old, DAY, "not-a-day"):
        (folders.done / day).mkdir(parents=True)
    gone = []
    assert empty_done(folders, recycle=gone.append) == [folders.done / old] and gone == [folders.done / old]
    folders.done_days = 0  # off
    assert empty_done(folders, recycle=gone.append) == []


def test_move_mode_still_takes_the_cover_from_inside_the_book(tmp_path, monkeypatch):
    import app.core.sidecars as sc
    src, folders = _setup(tmp_path)
    seen = []
    def fake_extract(book):
        seen.append(book.main_file.exists())  # the file must still be there when the cover is read
        return (b"\xff\xd8\xff inner cover", ".jpg")
    monkeypatch.setattr(sc, "extract_cover", fake_extract)
    books = [b for b in load_books([src]) if b.main_file.suffix == ".epub"]
    books[0].meta.cover_url, books[0].sources["cover_url"] = "http://anilist/series.jpg", "anilist"
    Executor(folders, mode="move").run(Planner(folders).plan(books), books)
    cover = next((tmp_path / "sorted").rglob("cover.jpg"))
    assert seen and all(seen) and cover.read_bytes() == b"\xff\xd8\xff inner cover"


def test_daisy_player_files_stay_behind_and_go_to_done(tmp_path):
    src = tmp_path / "AudioBooks Incoming"
    d = src / "Astrid Lindgren/Ronja"
    d.mkdir(parents=True)
    old = time.time() - 3600
    for name, data in {"Ronja.m4b": b"a" * 10, "ncc.html": b'<meta content="sv" name="dc:language" />',
                       "default.css": b"x", "a1.smil": b"x", "cover.jpg": b"img"}.items():
        (d / name).write_bytes(data)
        os.utime(d / name, (old, old))
    folders = Folders(audio_incoming=src, books_incoming=tmp_path / "none", audio_sorted=tmp_path / "sorted",
                      done=tmp_path / "done", state=tmp_path / "state", protected=[])
    books = load_books([src])
    Executor(folders, mode="move").run(Planner(folders).plan(books), books)
    sorted_files = sorted(p.name for p in (tmp_path / "sorted").rglob("*") if p.is_file())
    assert "a1.smil" not in sorted_files and "default.css" not in sorted_files and "Ronja.m4b" in sorted_files
    assert {"a1.smil", "default.css", "ncc.html"} <= {p.name for p in (tmp_path / "done").rglob("*")}
