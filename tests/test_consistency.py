from pathlib import Path

from app.core.consistency import series_key, unify_names
from app.core.models import Book, BookType, Metadata


def _book(author, series=None):
    b = Book(BookType.AUDIO, [Path("x.m4b")], Path("."))
    b.meta = Metadata(title="t", authors=[author], series=series)
    return b


def test_series_key():
    assert series_key("By the Grace of the Gods Series") == series_key("By the Grace of the Gods")
    assert series_key("A Tale of the Secret Saint (Light Novel)") == series_key("A Tale of the Secret Saint")


def test_most_common_spelling_wins():
    books = [_book("Roy", "By the Grace of the Gods Series"), _book("Roy", "By the Grace of the Gods Series"),
             _book("Roy", "By the Grace of the Gods"), _book("Ursula K. LeGuin"), _book("Ursula K. Le Guin"),
             _book("Ursula K. Le Guin")]
    unify_names(books, [])
    assert {b.meta.series for b in books[:3]} == {"By the Grace of the Gods Series"}
    assert {b.author for b in books[3:]} == {"Ursula K. Le Guin"}


def test_near_identical_author_joins_existing_folder(tmp_path):
    (tmp_path / "Eiichiro Oda").mkdir()
    books = [_book("Eiichirou Oda", "One Piece")]
    unify_names(books, [tmp_path])
    assert books[0].author == "Eiichiro Oda"
    other = [_book("Anne Rice")]  # short/different names are left alone
    unify_names(other, [tmp_path])
    assert other[0].author == "Anne Rice"


def test_existing_library_folders_win(tmp_path):
    (tmp_path / "Mojikakiya" / "My Daughter Left the Nest").mkdir(parents=True)
    books = [_book("MOJIKAKIYA", "My Daughter Left the Nest (Light Novel)"),
             _book("MOJIKAKIYA", "My Daughter Left the Nest (Light Novel)")]
    unify_names(books, [tmp_path])
    assert {(b.author, b.meta.series) for b in books} == {("Mojikakiya", "My Daughter Left the Nest")}


def test_one_author_per_series_and_title_from_series(tmp_path):
    from app.core.consistency import unify_names
    from app.core.models import Book, BookType, Metadata
    def ln(series, n, authors):
        b = Book(BookType.EBOOK, [tmp_path / f"{series}{n}.epub"], tmp_path,
                 meta=Metadata(title="One Piece", series=series, series_index=n, authors=authors))
        b.light_novel = True
        return b
    h1, h2 = ln("One Piece - Heroines", 1, ["Eiichiro Oda", "Jun Esaka"]), ln("One Piece - Heroines", 2, ["Jun Esaka", "Eiichiro Oda"])
    a1, a2 = ln("One Piece - Ace's Story", 1, ["Sho Hinata"]), ln("One Piece - Ace's Story", 2, ["Tatsuya Hamazaki"])
    unify_names([h1, h2, a1, a2], [])
    assert h1.author == h2.author == "Eiichiro Oda"
    assert a1.author == a2.author == "Sho Hinata" and "Tatsuya Hamazaki" in a2.meta.authors


def test_title_that_is_only_the_start_of_the_series(tmp_path):
    from app.core.metadata import read_local
    from app.core.models import Book, BookType
    d = tmp_path / "in/One Piece - Heroines Volume 01"
    d.mkdir(parents=True)
    epub = d / "One Piece - Heroines Volume 01.epub"
    epub.write_bytes(b"x")
    (d / "metadata.opf").write_text('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
                                    'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>One Piece</dc:title>'
                                    '<dc:creator>Jun Esaka</dc:creator><meta name="calibre:series" content="One Piece - Heroines"/>'
                                    '</metadata></package>', encoding="utf-8")
    book = Book(BookType.EBOOK, [epub], tmp_path / "in", folder=d)
    read_local(book)
    assert (book.meta.title, book.meta.series_index) == ("One Piece - Heroines, Vol. 1", 1)


def _epub_with_opf(tmp_path, rel, title, series=None, index=None):
    from app.core.models import Book, BookType
    d = tmp_path / "in" / rel
    d.mkdir(parents=True)
    epub = d / f"{d.name}.epub"
    epub.write_bytes(b"x")
    extra = (f'<meta name="calibre:series" content="{series}"/>' if series else "") + \
            (f'<meta name="calibre:series_index" content="{index}"/>' if index is not None else "")
    (d / "metadata.opf").write_text('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
                                    f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title>'
                                    f'<dc:creator>Miya Kazuki</dc:creator>{extra}</metadata></package>', encoding="utf-8")
    return Book(BookType.EBOOK, [epub], tmp_path / "in", folder=d)


def test_bookworm_parts_swapped_halves_and_stand_alone_spin_off(tmp_path):
    from app.core.metadata import read_local
    part = _epub_with_opf(tmp_path, "Miya Kazuki/Ascendance of a Bookworm/P5/Part 5 Volume 10",
                          "Ascendance of a Bookworm: Part 5 Volume 10", "Ascendance of a Bookworm", "5.10")
    swapped = _epub_with_opf(tmp_path, "Miya Kazuki/Ascendance of a Bookworm/Ascendance of a Bookworm - Short Story Collection Volume 02",
                             "Ascendance of a Bookworm", "Short Story Collection", 2)
    alone = _epub_with_opf(tmp_path, "Miya Kazuki/Ascendance of a Bookworm/Royal Academy Stories",
                           "Ascendance of a Bookworm: Royal Academy Stories - First Year", "Ascendance of a Bookworm")
    for b in (part, swapped, alone):
        read_local(b)
    assert (part.meta.series, part.meta.series_index, part.meta.title) == (
        "Ascendance of a Bookworm: Part 5", 10, "Ascendance of a Bookworm: Part 5, Vol. 10")
    assert (swapped.meta.series, swapped.meta.title) == (
        "Ascendance of a Bookworm: Short Story Collection", "Ascendance of a Bookworm: Short Story Collection, Vol. 2")
    assert alone.meta.series is None


def test_folder_names_use_a_short_form_of_long_series():
    from app.core.planner import folder_series
    assert folder_series("Ascendance of a Bookworm: Part 5") == "Ascendance of a Bookworm: Part 5"
    assert folder_series("Ascendance of a Bookworm: Hannelore's Fifth Year at the Royal Academy") == \
        "Hannelore's Fifth Year at the Royal Academy"
    # the subtitle only tells an author's series apart; else the main name ('Reborn as a Space Mercenary')
    assert folder_series("Reborn as a Space Mercenary: I Woke Up Piloting the Strongest Starship!",
                         shared_head=False) == "Reborn as a Space Mercenary"
    assert folder_series("Hell Mode: The Hardcore Gamer Dominates Another World with Garbage Balancing - "
                         "Summoner of the Beginning") == "Hell Mode"
