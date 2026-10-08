from pathlib import Path

from app.config import Folders
from app.core.history import list_runs, undo
from app.core.library import reorganize_audio, reorganize_books
from app.core.models import Book, BookType, Metadata
from app.core.planner import Planner

OPF = '<package><metadata><meta name="calibre:series" content="{}"/></metadata></package>'


def _put(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _folders(tmp_path: Path) -> Folders:
    return Folders(books_sorted=tmp_path / "books", state=tmp_path / "state", protected=[])


def test_new_books_go_into_kind_folders(tmp_path):
    f = _folders(tmp_path)
    manga = Book(BookType.COMIC, [Path("a.cbz")], Path("."), manga=True)
    manga.meta = Metadata(title="One Piece, Vol. 1", authors=["Eiichiro Oda"], series="One Piece", series_index=1)
    ln = Book(BookType.EBOOK, [Path("b.epub")], Path("."), light_novel=True)
    ln.meta = Metadata(title="Solo Leveling, Vol. 1", authors=["Chugong"], series="Solo Leveling", series_index=1)
    novel = Book(BookType.EBOOK, [Path("c.epub")], Path("."))
    novel.meta = Metadata(title="Dune", authors=["Frank Herbert"])
    plan = Planner(f).plan([manga, ln, novel])
    books = tmp_path / "books"
    assert plan.targets[manga] == books / "Manga/Eiichiro Oda/One Piece (Manga)/1 - One Piece, Vol. 1"
    assert plan.targets[ln] == books / "Light Novels/Chugong/Solo Leveling/1 - Solo Leveling, Vol. 1"
    assert plan.targets[novel] == books / "EBooks/Frank Herbert/Dune"


def test_audio_light_novels_get_their_own_folder(tmp_path):
    f = Folders(audio_sorted=tmp_path / "audio", state=tmp_path / "state", protected=[])
    ln = Book(BookType.AUDIO, [Path("a.m4b")], Path("."), light_novel=True)
    ln.meta = Metadata(title="By the Grace of the Gods: Volume 1", authors=["Roy"], series="By the Grace of the Gods",
                       series_index=1)
    other = Book(BookType.AUDIO, [Path("b.m4b")], Path("."))
    other.meta = Metadata(title="Tehanu", authors=["Ursula K. Le Guin"])
    plan = Planner(f).plan([ln, other])
    assert plan.targets[ln].parts[-4:-2] == ("Light Novels", "Roy")
    assert plan.targets[other] == tmp_path / "audio/Audiobooks/Ursula K. Le Guin/Tehanu"

    audio = f.audio_sorted
    _put(audio, "Roy/By the Grace of the Gods Series/1 - Vol 1/a.m4b")
    _put(audio, "Touya/A Tale of the Secret Saint (Light Novel)/2 - Vol 2/b.m4b")
    _put(audio, "Ursula K. Le Guin/The Earthsea Quartet/1 - A Wizard of Earthsea/c.mp3")
    _put(audio, "Audiobooks/Ursula K. Le Guin/The Earthsea Quartet/2 - The Tombs of Atuan/d.m4b")  # already split
    res = reorganize_audio(f, lambda name: name.startswith("By the Grace"))
    files = {str(p.relative_to(audio)).replace("\\", "/") for p in audio.rglob("*") if p.is_file()}
    assert files == {
        "Light Novels/Roy/By the Grace of the Gods Series/1 - Vol 1/a.m4b",
        "Light Novels/Touya/A Tale of the Secret Saint (Light Novel)/2 - Vol 2/b.m4b",
        "Audiobooks/Ursula K. Le Guin/The Earthsea Quartet/1 - A Wizard of Earthsea/c.mp3",
        "Audiobooks/Ursula K. Le Guin/The Earthsea Quartet/2 - The Tombs of Atuan/d.m4b",
    }
    assert len(res.moved) == 3 and not res.skipped


def test_reorganize_existing_library_and_undo(tmp_path):
    f = _folders(tmp_path)
    books = f.books_sorted
    _put(books, "Eiichiro Oda/One Piece (Manga)/1 - One Piece, Vol. 1/One Piece, Vol. 1.cbz")
    _put(books, "Chugong/Solo Leveling/1 - Solo Leveling, Vol. 1/Solo Leveling, Vol. 1.epub")
    _put(books, "Kotoko/Hinted/1 - Hinted, Vol. 1 (novel)/Hinted.epub")  # AniList doesn't know it
    _put(books, "Frank Herbert/Dune/Dune.epub")
    _put(books, "Miku/Cheat Skill (Manga)/6 - Cheat Skill, Vol. 6/v6.cbz")
    _put(books, "Miku/Cheat Skill/7 - Cheat Skill, Vol. 7/v7.cbz")
    _put(books, "Miku/Cheat Skill/7 - Cheat Skill, Vol. 7/metadata.opf", OPF.format("Cheat Skill"))
    _put(books, "Miku/Cheat Skill/7 - Cheat Skill, Vol. 7/metadata.json", "{}")  # ABS's own file
    before = sorted(str(p.relative_to(books)) for p in books.rglob("*") if p.is_file())

    preview = reorganize_books(f, lambda s: s == "Solo Leveling", dry_run=True)
    assert len(preview.moved) == 6 and not any(books.glob("Manga"))  # nothing moved yet
    assert "Kotoko\\Hinted -> Light Novels" in preview.moved

    res = reorganize_books(f, lambda s: s == "Solo Leveling")
    files = {str(p.relative_to(books)).replace("\\", "/") for p in books.rglob("*") if p.is_file()}
    assert files == {
        "Manga/Eiichiro Oda/One Piece (Manga)/1 - One Piece, Vol. 1/One Piece, Vol. 1.cbz",
        "Light Novels/Chugong/Solo Leveling/1 - Solo Leveling, Vol. 1/Solo Leveling, Vol. 1.epub",
        "EBooks/Frank Herbert/Dune/Dune.epub",
        "Light Novels/Kotoko/Hinted/1 - Hinted, Vol. 1 (novel)/Hinted.epub",
        "Manga/Miku/Cheat Skill (Manga)/6 - Cheat Skill, Vol. 6/v6.cbz",
        "Manga/Miku/Cheat Skill (Manga)/7 - Cheat Skill, Vol. 7/v7.cbz",
        "Manga/Miku/Cheat Skill (Manga)/7 - Cheat Skill, Vol. 7/metadata.opf",
    }  # ABS's stale metadata.json of the merged volume is parked, old author folders are gone
    opf = (books / "Manga/Miku/Cheat Skill (Manga)/7 - Cheat Skill, Vol. 7/metadata.opf").read_text(encoding="utf-8")
    assert 'content="Cheat Skill (Manga)"' in opf
    assert res.merged == ["Miku\\Cheat Skill -> Cheat Skill (Manga)"]

    u = undo(next(r for r in list_runs(f.state / "history") if r.run_id == res.run_id))
    after = sorted(str(p.relative_to(books)) for p in books.rglob("*") if p.is_file())
    assert after == before
    assert u.skipped == []


def test_library_tools_never_touch_protected_folders(tmp_path):
    import pytest
    from app.core.library import LibraryEditor
    abs_lib = tmp_path / "abs"
    _put(abs_lib, "Author/Book/a.m4b")
    f = Folders(state=tmp_path / "state", protected=[abs_lib])
    with LibraryEditor(f, "test") as ed:
        with pytest.raises(PermissionError):
            ed.move_file(abs_lib / "Author/Book/a.m4b", tmp_path / "elsewhere/a.m4b")
        with pytest.raises(PermissionError):
            ed.ensure_dir(abs_lib / "New")
    assert (abs_lib / "Author/Book/a.m4b").exists() and not (abs_lib / "New").exists()


def test_defaults_are_neutral_and_protection_works():
    f = Folders()
    assert "-=" not in str(f.audio_sorted) and f.protected == []  # nothing personal in a shared copy
    f.protected = [Path(r"H:\-=Books=-")]
    assert f.is_protected(Path(r"H:\-=Books=-\Author\Book")) and f.is_protected(Path(r"H:\-=Books=-"))
    assert not f.is_protected(Path(r"H:\-=Books=- other")) and not f.is_protected(Path(r"H:\-=Books Sorter=-"))
