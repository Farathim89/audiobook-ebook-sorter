"""What your Audiobookshelf has: the same book under another folder name, its series spelling, the narrator
of the other volumes, and where a new book fits ('fills gap #2')."""
from pathlib import Path

from app.core.abs_index import AbsBook, AbsIndex, library_status
from app.core.models import Book, BookType, Metadata, Status
from app.core.online import OnlineLookup


def _book(title, series=None, idx=None, authors=("Fujino Omori",), asin="", btype=BookType.AUDIO, folder=None):
    b = Book(btype, [Path(f"{title}.m4b")], Path("."), folder=Path(folder) if folder else None)
    b.meta = Metadata(title=title, series=series, series_index=idx, authors=list(authors), asin=asin)
    b.status = Status.READY
    return b


def _abs(title, series, n, narr=("Robbie Daymond",), asin="", path=None, fmt="audio", missing=False):
    return AbsBook(f"id-{title}", "Light Novels", path or f"H:/LN/{series}/{n:g} - {title}", title,
                   ["Fujino Omori"], list(narr), [(series, n)], asin, fmt, missing)


DANMACHI = "Is It Wrong to Try to Pick Up Girls in a Dungeon?"
INDEX = AbsIndex([_abs("Vol. 1", DANMACHI, 1, asin="B01ABC"), _abs("Vol. 3", DANMACHI, 3),
                  _abs("Vol. 4", DANMACHI, 4, missing=True),
                  _abs("Vol. 2", DANMACHI, 2, path="H:/LN/x/2 - Vol. 2 (Dramatized)", narr=("Full Cast",))])


def test_the_same_book_by_asin_title_or_number():
    assert INDEX.find_same(_book("Something else", asin="b01abc")).title == "Vol. 1"
    assert INDEX.find_same(_book("Vol. 3")).title == "Vol. 3"
    assert INDEX.find_same(_book("Other name", "Is It Wrong to Try to Pick Up Girls in a Dungeon", 3)).title == "Vol. 3"
    assert INDEX.find_same(_book("Vol. 4", DANMACHI, 4)) is None  # gone from disk
    assert INDEX.find_same(_book("Vol. 2", DANMACHI, 2)) is None  # only the dramatized edition is there
    assert INDEX.find_same(_book("Vol. 3", btype=BookType.EBOOK)) is None  # other format
    assert INDEX.find_same(_book("Vol. 3", authors=("Someone Else",))) is None


def test_series_spelling_narrators_and_status():
    assert INDEX.series_name("Fujino Omori", "Is It Wrong to Try to Pick Up Girls in a Dungeon") == DANMACHI
    assert INDEX.narrators_for("Fujino Omori", DANMACHI) == ["Robbie Daymond"]
    assert library_status(_book("Vol. 3"), INDEX) == "have it"
    assert library_status(_book("Vol. 2", DANMACHI, 2), INDEX) == "fills gap #2"
    assert library_status(_book("Vol. 4", DANMACHI, 4), INDEX) == "next volume"
    assert library_status(_book("Vol. 9", DANMACHI, 9), INDEX) == "new (you have up to #3)"
    assert library_status(_book("Vol. 1", DANMACHI, 1, btype=BookType.EBOOK), INDEX) == "new format"
    assert library_status(_book("X", "Other", 1), INDEX) == "new series"
    assert library_status(_book("X", "Other", 1), AbsIndex()) == ""


def test_lookup_uses_the_library_spelling_and_narrator(tmp_path):
    look = OnlineLookup.__new__(OnlineLookup)
    look.abs_index = INDEX
    b = _book("Vol. 7", "Is It Wrong to Try to Pick Up Girls in a Dungeon", 7)
    look._series_as_in_abs([b])
    assert b.meta.series == DANMACHI
    manual = _book("Vol. 7", "Is it wrong to try to pick up girls in a dungeon", 7)
    manual.sources["series"] = "manual"
    look._series_as_in_abs([manual])
    assert manual.meta.series != DANMACHI
    look.fill_narrators(b)
    assert b.meta.narrators == ["Robbie Daymond"] and b.sources["narrators"] == "library"


def test_without_abs_settings_it_is_empty():
    class F:
        abs_url = ""
        abs_api_key = ""
    assert AbsIndex.load(F()).books == []


def test_manga_is_never_the_light_novel():
    ix = AbsIndex([AbsBook("m3", "Manga", "H:/M/x/3", "Vol. 3", ["Fujino Omori"], [], [(DANMACHI + " (Manga)", 3)],
                           "", "ebook", False)])
    ln = _book("Other", DANMACHI, 3, btype=BookType.EBOOK)
    assert ix.find_same(ln) is None and library_status(ln, ix) == "new series"
    assert ix.find_same(_book("Other", DANMACHI, 3, btype=BookType.COMIC)).id == "m3"
    assert ix.series_name("Fujino Omori", DANMACHI) is None


def test_the_swedish_translation_is_another_book():
    ix = AbsIndex([AbsBook("en1", "AudioBooks", "H:/A/HP/1 - Stone", "Philosopher's Stone", ["J.K. Rowling"],
                           ["Stephen Fry"], [("Harry Potter", 1)], "", "audio", False)])
    sv = _book("De Vises Sten", "Harry Potter", 1, authors=("J.K. Rowling",))
    sv.meta.language = "sv"
    assert ix.find_same(sv) is None and ix.narrators_for("J.K. Rowling", "Harry Potter", swedish=True) == []
    assert ix.find_same(_book("Other", "Harry Potter", 1, authors=("J.K. Rowling",))).id == "en1"


def test_number_from_the_series_list_on_audible():
    class FakeAudible:
        def series_numbers(self, asin, series):
            assert asin == "B0D9C7HDVL"
            return {"iambblueinpainandfragile": 4.0, "iamblueinpainandfragile": 5.0, "confessionstrategy": 8.0}
    look = OnlineLookup.__new__(OnlineLookup)
    look.audible = FakeAudible()
    look.abs_index = AbsIndex([_abs("I Want to Eat Your Pancreas", "The Yoru Sumino Collection", 1, asin="B0D9C7HDVL")])
    blue = _book("I am Blue, in Pain, and Fragile", "The Yoru Sumino Collection", btype=BookType.EBOOK)
    conf = _book("The Confession Strategy", "The Yoru Sumino Collection", btype=BookType.EBOOK)
    other = _book("Something", "The Yoru Sumino Collection", btype=BookType.EBOOK)
    look._numbers_from_series_lists([blue, conf, other])
    assert (blue.meta.series_index, conf.meta.series_index, other.meta.series_index) == (5, 8, None)
    assert blue.sources["series_index"] == "audible series list"


def test_no_series_at_all_finds_it_in_the_authors_series_lists():
    class FakeAudible:
        def series_numbers(self, asin, series):
            return {"iamblueinpainandfragile": 5.0} if series == "The Yoru Sumino Collection" else {}
    look = OnlineLookup.__new__(OnlineLookup)
    look.audible = FakeAudible()
    look.abs_index = AbsIndex([AbsBook("p1", "Audiobooks - Light Novels", "H:/x", "Pancreas", ["Yoru Sumino"], [],
                                       [("The Yoru Sumino Collection", 1)], "B0D9C7HDVL", "audio", False)])
    blue = _book("I am Blue, in Pain, and Fragile", authors=("Yoru Sumino",), btype=BookType.EBOOK)
    stranger = _book("I am Blue, in Pain, and Fragile", authors=("Someone Else",), btype=BookType.EBOOK)
    look._numbers_from_series_lists([blue, stranger])
    assert (blue.meta.series, blue.meta.series_index) == ("The Yoru Sumino Collection", 5)
    assert stranger.meta.series is None


def test_a_comma_inside_a_series_name():
    from app.core.abs_index import split_series
    spin = "Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!"
    assert split_series(f"{spin} #1") == [f"{spin} #1"]
    assert split_series("Solo Leveling #2, Yen Audio #5") == ["Solo Leveling #2", "Yen Audio #5"]


def test_shared_head_counts_the_spin_off_in_your_library(tmp_path):
    from app.config import Folders
    from app.core.planner import Planner, folder_title
    p = Planner(Folders(state=tmp_path, audio_sorted=tmp_path / "a", books_sorted=tmp_path / "b"))
    p._abs = AbsIndex([AbsBook("s1", "Audiobooks - Light Novels", "H:/x", "Too 1", ["Yomu Mishima"], [],
                               [("Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!", 1)], "", "audio", False)])
    main = _book("Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs, Vol. 8",
                 "Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs", 8, authors=("Yomu Mishima",))
    assert p.shared_head(main)
    assert folder_title(main, p.shared_head(main)) == "The World of Otome Games is Tough for Mobs, Vol. 8"
    manga = _book("x", "Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs", 8,
                  authors=("Yomu Mishima",), btype=BookType.COMIC)
    assert not p.shared_head(manga)  # the spin-off is no manga


def test_manga_takes_the_light_novel_spelling():
    look = OnlineLookup.__new__(OnlineLookup)
    ln = "Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs"
    look.abs_index = AbsIndex([AbsBook("l1", "Audiobooks - Light Novels", "H:/x", "Vol 1", ["Yomu Mishima"], [],
                                       [(ln, 1)], "", "audio", False)])
    m = _book("Vol 10", "Trapped in a Dating Sim - The World of Otome Games is Tough for Mobs (Manga)", 10,
              authors=("Yomu Mishima",), btype=BookType.COMIC)
    look._series_as_in_abs([m])
    assert m.meta.series == ln + " (Manga)"


def test_a_box_set_covers_its_books():
    box = AbsBook("b", "AudioBooks", "H:/A/M.R. Forbes/Chaos of the Covenant/1-4 - Hell's Rejects, Books 1-4",
                  "Hell's Rejects, Books 1-4", ["M.R. Forbes"], [], [("Chaos of the Covenant", 1)], "", "audio", False)
    ix = AbsIndex([box])
    for n in (2, 3, 4):
        assert ix.find_same(_book("x", "Chaos of the Covenant", n, authors=("M.R. Forbes",))) is box
    assert ix.find_same(_book("x", "Chaos of the Covenant", 5, authors=("M.R. Forbes",))) is None
    assert library_status(_book("x", "Chaos of the Covenant", 5, authors=("M.R. Forbes",)), ix) == "next volume"


def test_own_side_series_kept_when_the_shop_has_no_number():
    from app.core.online import _own_series_when_unnumbered, _series_head_vote
    b = _book("Adachi and Shimamura: Short Stories 2", "Adachi and Shimamura", 1, authors=("Hitoma Iruma",))
    b.sources.update(series="audible", series_index="sidecar")
    mains = [_book(f"Adachi and Shimamura, Vol. {n}", "Adachi and Shimamura", n, authors=("Hitoma Iruma",))
             for n in (8, 9, 10)]
    _own_series_when_unnumbered([b], {id(b): "Adachi and Shimamura: Short Stories"},
                                {id(b): "Adachi and Shimamura: Short Stories"})
    _series_head_vote([b, *mains])
    assert (b.meta.series, b.meta.title) == ("Adachi and Shimamura: Short Stories", "Adachi and Shimamura: Short Stories")


def test_a_store_as_author_takes_the_folder_series(tmp_path):
    from app.core.online import _odd_one_in_folder
    d = tmp_path / "Alya"
    books = []
    for n in (4, 5, 6):
        b = _book(f"Alya Sometimes Hides Her Feelings in Russian, Vol. {n}", "Alya Sometimes Hides Her Feelings in Russian",
                  n, authors=("Sunsunsun",), btype=BookType.EBOOK)
        b.files = [d / f"Alya v0{n}.epub"]
        books.append(b)
    odd = _book("Alya Sometimes Hides Her Feelings in Russian, Vol. 4.5 Pinup Art Collection", None,
                authors=("BOOK☆WALKER Global",), btype=BookType.EBOOK)
    odd.files = [d / "Alya Sometimes Hides Her Feelings in Russian v04.6 - Pinup Art Collection.epub"]
    _odd_one_in_folder([*books, odd])
    assert (odd.meta.authors, odd.meta.series, odd.meta.series_index) == (
        ["Sunsunsun"], "Alya Sometimes Hides Her Feelings in Russian", 4.6)


def test_a_new_year_is_written_like_the_library():
    look = OnlineLookup.__new__(OnlineLookup)
    look.abs_index = AbsIndex([AbsBook("y2", "Light Novels", "H:/x", "Y2 1", ["Syougo Kinugasa"], [],
                                       [("Classroom of the Elite (Year 2)", 1)], "", "ebook", False)])
    b = _book("Classroom of the Elite: Year 3, Vol. 1", "Classroom of the Elite: Year 3", 1,
              authors=("Syougo Kinugasa",), btype=BookType.EBOOK)
    look._series_as_in_abs([b])
    assert b.meta.series == "Classroom of the Elite (Year 3)"


def test_new_volumes_join_the_librarys_series_folder(tmp_path):
    from app.config import Folders
    from app.core.planner import Planner
    p = Planner(Folders(state=tmp_path, audio_sorted=tmp_path / "a", books_sorted=tmp_path / "b"))
    p._abs = AbsIndex([AbsBook("y", "Audiobooks - Light Novels",
                               "H:/A/Syougo Kinugasa/Classroom of the Elite - Year 2/11 - Classroom of the Elite - Year 2, Vol. 11",
                               "Y2 11", ["Syougo Kinugasa"], [], [("Classroom of the Elite (Year 2)", 11)], "", "audio", False)])
    b = _book("Classroom of the Elite (Year 2), Vol. 12", "Classroom of the Elite (Year 2)", 12, authors=("Syougo Kinugasa",))
    assert p.book_dir(b).parent.name == "Classroom of the Elite - Year 2"
    e = _book("Classroom of the Elite (Year 2), Vol. 12", "Classroom of the Elite (Year 2)", 12,
              authors=("Syougo Kinugasa",), btype=BookType.EBOOK)
    assert p.book_dir(e).parent.name == "Classroom of the Elite (Year 2)"  # no ebook of it in the library: its own name
