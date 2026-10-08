from pathlib import Path

from app.config import Folders
from app.core.compare import HAVE, HAVE_NOT, apply_compare, compare, plan_compare
from app.core.history import list_runs, undo
from app.core.models import Book, BookType, Metadata

OPF = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
       'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{t}</dc:title><dc:creator>{a}</dc:creator>'
       '<meta name="calibre:series" content="{s}"/><meta name="calibre:series_index" content="{n}"/></metadata></package>')


def _book(root: Path, rel: str, title: str, author: str, series: str, n: int):
    d = root / rel
    d.mkdir(parents=True)
    (d / f"{title}.epub").write_bytes(b"x")
    (d / "metadata.opf").write_text(OPF.format(t=title, a=author, s=series, n=n), encoding="utf-8")
    (d / "cover.jpg").write_bytes(b"img")


def test_matching_rules(tmp_path):
    lib = Book(BookType.EBOOK, [tmp_path / "l.epub"], tmp_path,
               meta=Metadata(title="Dungeon Crawler Carl", series="Dungeon Crawler Carl", series_index=1, authors=["Matt Dinniman"]))
    unnumbered = Book(BookType.EBOOK, [tmp_path / "a.epub"], tmp_path, meta=Metadata(title="Dungeon Crawler Carl", authors=["Matt Dinniman"]))
    other_vol = Book(BookType.EBOOK, [tmp_path / "b.epub"], tmp_path,
                     meta=Metadata(title="Dungeon Crawler Carl", series="Dungeon Crawler Carl", series_index=2, authors=["Matt Dinniman"]))
    manga = Book(BookType.COMIC, [tmp_path / "c.cbz"], tmp_path,
                 meta=Metadata(title="Dungeon Crawler Carl, Vol. 1", series="Dungeon Crawler Carl", series_index=1, authors=["Matt Dinniman"]))
    r = compare([unnumbered, other_vol, manga], [lib])
    assert [b for b, _ in r.have] == [unnumbered] and r.have_not == [other_vol, manga]


def test_compare_sorts_into_have_and_do_not_have_and_undo(tmp_path, monkeypatch):
    lib, src = tmp_path / "Library", tmp_path / "Check"
    _book(lib, "Broccoli Lion/The Great Cleric/1", "The Great Cleric, Vol. 1", "Broccoli Lion", "The Great Cleric", 1)
    _book(src, "junk/a", "The Great Cleric, Vol. 1", "Broccoli Lion", "The Great Cleric", 1)
    _book(src, "junk/b", "The Great Cleric, Vol. 2", "Broccoli Lion", "The Great Cleric", 2)
    folders = Folders(state=tmp_path / "state", protected=[lib], use_storytel=False, use_itunes=False,
                      use_google_books=False, use_openlibrary=False, use_mangadex=False)
    import app.core.online as online
    monkeypatch.setattr(online.OnlineLookup, "run", lambda self, books, **kw: None)  # offline test
    result = plan_compare(folders, [src])
    run_id, n = apply_compare(folders, result, src)
    files = sorted(str(p.relative_to(src)).replace("\\", "/") for p in src.rglob("*") if p.is_file())
    assert files == [
        f"{HAVE_NOT}/Books/EBooks/Broccoli Lion/The Great Cleric/2 - The Great Cleric, Vol. 2/The Great Cleric, Vol. 2.epub",
        f"{HAVE_NOT}/Books/EBooks/Broccoli Lion/The Great Cleric/2 - The Great Cleric, Vol. 2/cover.jpg",
        f"{HAVE_NOT}/Books/EBooks/Broccoli Lion/The Great Cleric/2 - The Great Cleric, Vol. 2/metadata.opf",
        f"{HAVE}/Books/EBooks/Broccoli Lion/The Great Cleric/1 - The Great Cleric, Vol. 1/The Great Cleric, Vol. 1.epub",
        f"{HAVE}/Books/EBooks/Broccoli Lion/The Great Cleric/1 - The Great Cleric, Vol. 1/cover.jpg",
        f"{HAVE}/Books/EBooks/Broccoli Lion/The Great Cleric/1 - The Great Cleric, Vol. 1/metadata.opf",
    ]
    assert sorted(p.name for p in lib.rglob("*") if p.is_file()) == ["The Great Cleric, Vol. 1.epub", "cover.jpg", "metadata.opf"]
    undo(next(r for r in list_runs(folders.state / "history") if r.run_id == run_id))
    assert (src / "junk/a/The Great Cleric, Vol. 1.epub").exists()
