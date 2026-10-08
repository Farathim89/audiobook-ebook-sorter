from pathlib import Path

from app.config import Folders
from app.core.executor import Executor
from app.core.history import Processed, list_runs, undo
from app.core.pipeline import load_books
from app.core.planner import Planner


def _setup(tmp_path: Path):
    src = tmp_path / "in"
    files = {
        "Brandon Sanderson/Mistborn/01 - The Final Empire/CD1/01.mp3": b"a" * 10,
        "Brandon Sanderson/Mistborn/01 - The Final Empire/cover.jpg": b"img",
        "Frank Herbert/Dune.epub": b"epub",
        "Loose Release (2019).cbz": b"cbz",
    }
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    folders = Folders(audio_sorted=tmp_path / "out_audio", books_sorted=tmp_path / "out_books",
                      review=tmp_path / "review", state=tmp_path / "state", protected=[])
    books = load_books([src])
    return src, folders, books


def _all_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}


def test_copy_then_undo(tmp_path):
    src, folders, books = _setup(tmp_path)
    before = _all_files(src)
    plan = Planner(folders).plan(books)
    result = Executor(folders).run(plan)
    assert (result.count("done"), result.count("review"), result.count("failed")) == (2, 1, 0)

    assert _all_files(src) == before  # copy mode: originals untouched
    assert _all_files(tmp_path / "out_audio") == {
        "Audiobooks/Brandon Sanderson/Mistborn/1 - The Final Empire/CD1/01.mp3",
        "Audiobooks/Brandon Sanderson/Mistborn/1 - The Final Empire/cover.jpg"}
    assert _all_files(tmp_path / "out_books") == {"EBooks/Frank Herbert/Dune/Dune.epub", "EBooks/Frank Herbert/Dune/metadata.opf"}
    opf = (tmp_path / "out_books/EBooks/Frank Herbert/Dune/metadata.opf").read_text(encoding="utf-8")
    assert '<dc:creator opf:role="aut">Frank Herbert</dc:creator>' in opf
    assert _all_files(tmp_path / "review") == {"Loose Release (2019).cbz", "Loose Release (2019).cbz.reason.txt"}
    assert not list(tmp_path.rglob("*.partial"))

    processed = Processed(folders.state / "processed.txt")
    assert all(processed.contains(b) for b in books)

    # a second run skips everything: targets exist now
    again = Executor(folders).run(Planner(folders).plan(books))
    assert again.count("done") == 0

    run = next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id)
    res = undo(run, processed)
    assert res.restored == 6 and not res.skipped  # mp3, cover, epub, opf, cbz, reason.txt
    assert not (tmp_path / "out_audio").exists() and not (tmp_path / "out_books").exists()
    assert not (tmp_path / "review").exists()
    assert not any(processed.contains(b) for b in books)
    assert _all_files(src) == before


def test_move_then_undo(tmp_path):
    src, folders, books = _setup(tmp_path)
    before = _all_files(src)
    result = Executor(folders, mode="move").run(Planner(folders).plan(books))
    assert result.count("failed") == 0
    assert "Frank Herbert/Dune.epub" not in _all_files(src)
    run = next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id)
    undo(run)
    assert _all_files(src) == before


def test_existing_target_is_never_overwritten(tmp_path):
    src, folders, books = _setup(tmp_path)
    target = tmp_path / "out_books/EBooks/Frank Herbert/Dune/Dune.epub"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my own different file")
    plan = Planner(folders).plan(books)
    result = Executor(folders).run(plan)
    dune = next(r for r in result.results if r.book.meta.title == "Dune")
    assert dune.status == "skipped" and "already exists" in dune.detail
    assert target.read_bytes() == b"my own different file"


def test_xhe_original_stays_even_in_move_mode(tmp_path, monkeypatch):
    from app.core import m4b
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    original = src_dir / "Some Book.m4b"
    original.write_bytes(b"xhe audio")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      protected=[], fix_xhe_aac=True)
    monkeypatch.setattr(m4b, "is_xhe_aac", lambda p: p.suffix == ".m4b" and b"xhe" in p.read_bytes())
    monkeypatch.setattr(m4b, "reencode_aac", lambda src, out, cancelled=None: out.write_bytes(b"plain aac"))
    books = load_books([tmp_path / "in"])
    result = Executor(folders, mode="move").run(Planner(folders).plan(books))
    assert result.count("done") == 1, result.results[0].detail
    assert original.read_bytes() == b"xhe audio"            # the original is still in Incoming
    sorted_file = next((tmp_path / "out").rglob("*.m4b"))
    assert sorted_file.read_bytes() == b"plain aac"         # the library gets the converted copy


def test_long_series_full_name_folder_short_book_folder_and_file(tmp_path):
    from app.core.models import Book, BookType, Metadata
    full = "Hell Mode: The Hardcore Gamer Dominates Another World with Garbage Balancing - Summoner of the Beginning"
    book = Book(BookType.COMIC, [tmp_path / "in/x.cbz"], tmp_path / "in",
                meta=Metadata(title=f"{full}, Vol. 1", series=full, series_index=1, authors=["Hamuo"]))
    book.manga = book.kind_known = True
    folders = Folders(books_sorted=tmp_path / "out", state=tmp_path / "state", protected=[])
    target = Planner(folders).plan([book]).targets[book]
    assert target.name == "1 - Hell Mode, Vol. 1"  # book folder (and file) short
    assert target.parent.name.startswith("Hell Mode - The Hardcore Gamer Dominates")  # series folder: full
    assert target.parent.name.endswith(" (Manga)")  # kept even when the name has to be shortened
