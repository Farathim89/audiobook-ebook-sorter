from pathlib import Path

from app.config import Folders
from app.core.duplicates import plan_duplicate_moves
from app.core.history import list_runs, undo
from app.core.library import move_duplicates
from app.core.pipeline import load_books
from app.core.planner import Planner
from app.readers.opf import parse_opf

LIB_OPF = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
           'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>The Great Cleric: Volume 01</dc:title>'
           '<dc:creator>Broccoli Lion</dc:creator><meta name="calibre:series" content="The Great Cleric"/>'
           '<meta name="calibre:series_index" content="1"/></metadata></package>')


def _put(p: Path, data: bytes | str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def test_move_extra_copies_and_library_duplicates(tmp_path):
    src = tmp_path / "Incoming"
    f = Folders(books_sorted=tmp_path / "Sorted", duplicates=tmp_path / "Duplicates", state=tmp_path / "state",
                protected=[], audio_incoming=tmp_path / "none", books_incoming=src)
    # two copies of Dune in Incoming: the bigger one wins
    _put(src / "Frank Herbert/Dune.epub", b"x" * 500)
    _put(src / "old/Frank Herbert - Dune.epub", b"x" * 100)
    # Great Cleric 1 is already in the library (no ISBN there) - the incoming copy has one
    _put(src / "Broccoli Lion/The Great Cleric/The Great Cleric - Volume 01/The Great Cleric - Volume 01.epub", b"e" * 300)
    lib = f.books_sorted / "Light Novels/Broccoli Lion/The Great Cleric/1 - The Great Cleric - Volume 01"
    _put(lib / "The Great Cleric - Volume 01.epub", b"e" * 290)
    _put(lib / "metadata.opf", LIB_OPF)
    # a much better copy than the library's: stays
    _put(src / "Andy Weir/Artemis.epub", b"a" * 900)
    _put(f.books_sorted / "EBooks/Andy Weir/Artemis/Artemis.epub", b"a" * 100)

    books = load_books([src])
    for b in books:
        if "Cleric" in b.main_file.name:
            b.light_novel = True
            b.meta.series, b.meta.series_index, b.meta.isbn = "The Great Cleric", 1, "9781718362024"
            b.meta.title, b.meta.authors = "The Great Cleric: Volume 1", ["Broccoli Lion"]
    plan = Planner(f).plan(books)
    moves, better = plan_duplicate_moves(books, plan)

    moved = sorted(m.book.main_file.name for m in moves)
    assert moved == ["Frank Herbert - Dune.epub", "The Great Cleric - Volume 01.epub"]
    assert [b.main_file.name for b, _ in better] == ["Artemis.epub"]

    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file() and "state" not in p.parts)
    run_id, n = move_duplicates(f, moves)
    assert n == 2
    assert (f.duplicates / "Incoming/old/Frank Herbert - Dune.epub").exists()
    assert (f.duplicates / "Incoming/Broccoli Lion/The Great Cleric/The Great Cleric - Volume 01").is_dir()
    assert not (src / "old").exists() and not (src / "Broccoli Lion").exists()  # empty folders cleaned up
    assert (src / "Frank Herbert/Dune.epub").exists()                           # the best copy stays
    assert parse_opf((lib / "metadata.opf").read_bytes()).isbn == "9781718362024"  # gap filled

    undo(next(r for r in list_runs(f.state / "history") if r.run_id == run_id))
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file() and "state" not in p.parts)
    assert after == before


def test_a_protected_library_copy_is_never_written(tmp_path):
    from app.core.duplicates import DupMove
    src, lib_root = tmp_path / "Incoming", tmp_path / "Library"
    f = Folders(books_sorted=tmp_path / "Sorted", duplicates=tmp_path / "Duplicates", state=tmp_path / "state",
                protected=[lib_root], audio_incoming=tmp_path / "none", books_incoming=src)
    _put(src / "Fujino Oomori/DanMachi 1.epub", b"e" * 300)
    lib = lib_root / "Manga/Fujino Oomori/DanMachi/1 - DanMachi"
    _put(lib / "DanMachi 1.epub", b"e" * 290)
    book = load_books([src])[0]
    book.meta.isbn, book.meta.description = "9780316339155", "a description the library copy lacks"
    run_id, n = move_duplicates(f, [DupMove(book, "already in the library", lib)])
    assert n == 1 and (f.duplicates / "Incoming/Fujino Oomori/DanMachi 1.epub").exists()
    assert sorted(p.name for p in lib.iterdir()) == ["DanMachi 1.epub"]  # nothing written into the library
