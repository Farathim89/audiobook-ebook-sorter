"""New volumes join the series folder your library already has - but never a cut-off one an old sort made."""
from pathlib import Path

from app.config import Folders
from app.core.abs_index import AbsBook, AbsIndex
from app.core.models import Book, BookType, Metadata
from app.core.planner import Planner

AUTHOR = "Fujino Oomori"
ORATORIA = "Is It Wrong to Try to Pick Up Girls in a Dungeon On the Side - Sword Oratoria (Manga)"


def _abs(folder, n, series):
    return AbsBook(str(n), "Manga", rf"H:\-=Books=-\Manga\{AUTHOR}\{folder}\{n} - Vol. {n}", f"Vol. {n}", [AUTHOR], [],
                   [(series, float(n))], "", "ebook", False)


def _planner(tmp_path, items):
    p = Planner(Folders(books_sorted=tmp_path, state=tmp_path / "s", protected=[]))
    p._abs = AbsIndex(items)
    return p


def _manga(series, n):
    b = Book(BookType.COMIC, [Path(f"v{n}.cbz")], Path("."))
    b.meta, b.manga = Metadata(title=f"{series}, Vol. {n}", series=series, series_index=n, authors=[AUTHOR]), True
    return b


def test_a_cut_off_library_folder_is_not_reused(tmp_path):
    cut = [_abs("Is It Wrong to Try to Pick Up Girls in a", n, ORATORIA) for n in range(10, 29)]
    assert _planner(tmp_path, cut)._library_series_folder(_manga(ORATORIA, 29)) is None


def test_another_spelling_of_the_same_series_is_reused(tmp_path):
    items = [_abs("Classroom of the Elite - Year 2", n, "Classroom of the Elite (Year 2)") for n in (1, 2)]
    book = _manga("Classroom of the Elite (Year 2)", 3)
    assert _planner(tmp_path, items)._library_series_folder(book) == "Classroom of the Elite - Year 2"


def test_a_series_range_from_audiobookshelf_is_read():
    from app.core.abs_index import parse_series_part, split_series
    assert split_series("Musashi: An Epic Novel of the Samurai Era #01–07") == ["Musashi: An Epic Novel of the Samurai Era #01–07"]
    assert parse_series_part("Musashi: An Epic Novel of the Samurai Era #01–07") == ("Musashi: An Epic Novel of the Samurai Era", 1.0)
    assert parse_series_part("Solo Leveling #2") == ("Solo Leveling", 2.0)


def _audio(title, series, n, author="Matt Dinniman"):
    b = Book(BookType.AUDIO, [Path(f"{title}.m4b")], Path("."))
    b.meta = Metadata(title=title, series=series, series_index=n, authors=[author])
    return b


def test_a_title_that_is_the_series_name_keeps_it_unless_the_volumes_are_vol_n(tmp_path):
    p = _planner(tmp_path, [])
    dcc = _audio("Dungeon Crawler Carl", "Dungeon Crawler Carl", 1)
    p._batch = [dcc, _audio("Carl's Doomsday Scenario", "Dungeon Crawler Carl", 2)]
    assert p.book_name(dcc) == "1 - Dungeon Crawler Carl"
    dust = _audio("All the Dust That Falls", "All the Dust That Falls", 1, "zaifyr")
    p._batch = [dust, _audio("All the Dust That Falls Two", "All the Dust That Falls", 2, "zaifyr")]
    assert p.book_name(dust) == "1 - All the Dust That Falls, Vol. 1"
    one = _audio("Your Name.", "Your Name.", 1, "Makoto Shinkai")
    p._batch = [one]
    assert p.book_name(one) == "1 - Your Name."  # the folder name drops the end dot
