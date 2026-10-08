from pathlib import Path

from app.config import Folders
from app.core import library
from app.core.history import list_runs, undo
from app.core.library import apply_manga_refresh, plan_manga_refresh

OPF = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
       'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>One Piece, Vol. 1</dc:title>'
       '<dc:creator>Eiichiro Oda</dc:creator><dc:description>Luffy.</dc:description>'
       '<meta name="calibre:series" content="One Piece (Manga)"/></metadata></package>')
PNG = b"\x89PNG\r\n\x1a\n" + b"new" * 10


class FakeLookup:
    class mangadex:  # noqa: N801
        @staticmethod
        def volume_cover(series, n, english_only=False):
            return "https://uploads.mangadex.org/covers/op/v1.png" if series == "One Piece" else None

    class fandom:  # noqa: N801
        @staticmethod
        def volume(wiki, n, pattern, series=""):
            return {"title": "Romance Dawn", "year": 2003, "isbn": "9781569319017"}


def _library(tmp_path: Path) -> tuple[Folders, Path]:
    f = Folders(books_sorted=tmp_path / "books", state=tmp_path / "state", protected=[],
                fandom_wikis="One Piece = onepiece.fandom.com")
    d = f.books_sorted / "Manga/Eiichiro Oda/One Piece (Manga)/1 - One Piece, Vol. 1"
    d.mkdir(parents=True)
    (d / "One Piece, Vol. 1.cbz").write_bytes(b"cbz")
    (d / "cover.jpg").write_bytes(b"first page")
    (d / "metadata.opf").write_text(OPF, encoding="utf-8")
    (d / "metadata.json").write_text("{}", encoding="utf-8")  # ABS's own copy
    return f, d


def test_refresh_sorted_manga_and_undo(tmp_path, monkeypatch):
    f, d = _library(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file() and "state" not in p.parts)
    monkeypatch.setattr("app.core.sidecars.download", lambda url, timeout=30: PNG)

    changes = plan_manga_refresh(f, FakeLookup)
    assert len(changes) == 1 and changes[0].new_title == "One Piece, Vol. 1: Romance Dawn"
    run_id, n = apply_manga_refresh(f, changes)
    assert n == 1

    new = d.parent / "1 - One Piece, Vol. 1 - Romance Dawn"
    assert sorted(p.name for p in new.iterdir()) == ["One Piece, Vol. 1.cbz", "cover.png", "metadata.opf"]
    assert (new / "cover.png").read_bytes() == PNG
    opf = (new / "metadata.opf").read_text(encoding="utf-8")
    assert "One Piece, Vol. 1: Romance Dawn" in opf and "Luffy." in opf and "9781569319017" in opf
    assert 'content="One Piece (Manga)"' in opf  # suffix kept once, not doubled

    # running it again finds nothing more to do (cover identical, title and data present)
    assert plan_manga_refresh(f, FakeLookup) == []

    undo(next(r for r in list_runs(f.state / "history") if r.run_id == run_id))
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file() and "state" not in p.parts)
    assert after == before
