import json
from pathlib import Path

from app.core.metadata import read_local
from app.core.models import Book, BookType
from app.readers.sidecar import read_book_info


def _book(tmp_path: Path, meta: dict, info: str | None = None) -> Book:
    d = tmp_path / "in" / "Some Folder" / "Book"
    d.mkdir(parents=True)
    f = d / "book.m4b"
    f.write_bytes(b"x")
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if info is not None:
        (d / "book info.txt").write_text(info, encoding="utf-8")
    book = Book(BookType.AUDIO, [f], tmp_path / "in", folder=d)
    read_local(book)
    return book


def test_book_info_formats(tmp_path):
    (tmp_path / "book info.txt").write_text("SAMPLE-ADB:\nhttps://x\nAuthor\tReia\nNarrated by: Suzie Yeung, Stephen Fu\n", encoding="utf-8")
    assert read_book_info(tmp_path) == (["Reia"], ["Suzie Yeung", "Stephen Fu"], False)
    (tmp_path / "book info.txt").write_text("Series: Perfect Blue (Light Novel)\nBy: Yoshikazu Takeuchi\n", encoding="utf-8")
    assert read_book_info(tmp_path) == (["Yoshikazu Takeuchi"], [], True)
    (tmp_path / "book info.txt").write_text("By: Hayaken, Mike Langwiser - translator\nNarrated by: Michelle H. Lee\n",
                                            encoding="utf-8")
    assert read_book_info(tmp_path) == (["Hayaken"], ["Michelle H. Lee"], False)
    (tmp_path / "book info.txt").write_text("By: Chugong, Hye Young Im\nNarrated by: Ki Hong Lee\nPublisher: Yen Audio\n",
                                            encoding="utf-8")
    assert read_book_info(tmp_path) == (["Chugong", "Hye Young Im"], ["Ki Hong Lee"], False)
    for clue in ("Genre\tLight Novels ,Shoujo", "Japanese Title\t囚われた王女", "©2021 Twoony and Tapas Media"):
        (tmp_path / "book info.txt").write_text(f"By: X\n{clue}\n", encoding="utf-8")
        assert read_book_info(tmp_path)[2], clue


def test_swapped_author_and_narrator_are_put_right(tmp_path):
    book = _book(tmp_path, {"title": "Perfect Blue, Vol. 01: Complete Metamorphosis", "authors": ["Brittany Pressley"],
                            "narrators": ["Yoshikazu Takeuchi"], "series": ["Perfect Blue #01"]},
                 "By: Yoshikazu Takeuchi\nNarrated by: Brittany Pressley\nSeries: Perfect Blue (Light Novel)\n")
    assert (book.meta.authors, book.meta.narrators) == (["Yoshikazu Takeuchi"], ["Brittany Pressley"])
    assert book.light_novel


def test_volume_only_in_the_subtitle_and_a_cjk_series(tmp_path):
    book = _book(tmp_path, {"title": "Peddler in Another World", "authors": ["Hiiro Shimotsuki"],
                            "subtitle": "I Can Go Back to My World Whenever I Want! Volume 2",
                            "series": ["I Can Go Back to My World Whenever I Want #2"]})
    assert book.meta.title == "Peddler in Another World, Vol. 2"


def test_own_title_gets_no_volume_from_the_series_label(tmp_path):
    book = _book(tmp_path, {"title": "Silvers", "authors": ["Brian J. Nordon"], "subtitle": "Quest Academy, Book 1",
                            "series": ["Quest Academy #1"]})
    assert book.meta.title == "Silvers"


def test_reader_as_author_is_fixed_by_book_info(tmp_path):
    book = _book(tmp_path, {"title": "Reborn as the Villain's Lackey, Vol. 01", "authors": ["Nancy Wu"],
                            "narrators": ["Sarah Roberts"], "series": ["Reborn as the Villain's Lackey #1"]},
                 info="By: Twoony Twoony\nNarrated by: Nancy Wu\n")
    assert (book.meta.authors, book.meta.narrators) == (["Twoony Twoony"], ["Nancy Wu"])


def test_series_in_another_script_is_dropped(tmp_path):
    book = _book(tmp_path, {"title": "The Tatami Time Machine Blues", "authors": ["Tomihiko Morimi"],
                            "series": ["四畳半シリーズ #2"]})
    assert (book.meta.series, book.meta.series_index) == (None, None)


def _epub_book(tmp_path: Path, folder: str, title: str) -> Book:
    import zipfile
    d = tmp_path / "in" / "Classroom of the Elite" / folder
    d.mkdir(parents=True)
    f = d / "book.epub"
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
           f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title>'
           '<dc:creator>Syougo Kinugasa</dc:creator></metadata></package>')
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
        z.writestr("c.opf", opf)
    book = Book(BookType.EBOOK, [f], tmp_path / "in")
    read_local(book)
    return book


def test_volume_folder_is_no_series_and_year_2_stays(tmp_path):
    b = _epub_book(tmp_path, "Volume 07.5", "Classroom of the Elite Vol. 7.5")
    assert (b.meta.series, b.meta.series_index, b.meta.title) == \
        ("Classroom of the Elite", 7.5, "Classroom of the Elite, Vol. 7.5")
    y = _epub_book(tmp_path / "y", "Classroom of the Elite Year 2, Vol. 6", "Classroom of the Elite: Year 2 Vol. 6")
    assert (y.meta.series, y.meta.series_index, y.meta.title) == \
        ("Classroom of the Elite: Year 2", 6, "Classroom of the Elite: Year 2, Vol. 6")


def test_book_info_fixes_a_swap_with_extra_credits_and_title_as_author(tmp_path):
    book = _book(tmp_path / "a", {"title": "How Do You Live?", "authors": ["Brian Nishii"], "narrators": ["Genzaburo Yoshino"]},
                 "By: Genzaburo Yoshino, Bruno Navasky, Neil Gaiman\nNarrated by: Brian Nishii\n")
    assert (book.meta.authors, book.meta.narrators) == (["Genzaburo Yoshino"], ["Brian Nishii"])
    hh = _book(tmp_path / "b", {"title": "Hybrid Heart", "authors": ["Hybrid Heart"], "narrators": ["Sarah Skaer"]},
               "By: Iori Kusano\nNarrated by: Sarah Skaer\n")
    assert hh.meta.authors == ["Iori Kusano"]


def test_series_dash_volume_file_name():
    from app.readers.names import parse_name
    m = parse_name("How a Realist Hero Rebuilt the Kingdom - Volume 12 [J-Novel Club][Premium]", BookType.EBOOK)
    assert (m.series, m.series_index, m.title, m.authors) == \
        ("How a Realist Hero Rebuilt the Kingdom", 12, "How a Realist Hero Rebuilt the Kingdom, Vol. 12", [])


def test_narrated_by_and_written_by_in_the_author_list(tmp_path):
    book = _book(tmp_path / "hp", {"title": "Harry Potter And The Prisoner Of Azkaban",
                                   "authors": ["Narrated By Jim Dale", "Written By J. K. Rowling"],
                                   "narrators": ["Stephen Fry"], "series": ["Harry Potter #3"]})
    assert (book.meta.authors, book.meta.narrators) == (["J. K. Rowling"], ["Jim Dale"])
    assert book.sources["narrators"] == "label"


def test_book_info_wins_when_metadata_names_the_writer_as_narrator(tmp_path):
    kiki = _book(tmp_path / "k", {"title": "Kiki's Delivery Service", "authors": ["Emily Balistrieri"],
                                  "narrators": ["Eiko Kadono"]},
                 "By: Eiko Kadono\nNarrated by: Kim Mai Guest\n")
    assert (kiki.meta.authors, kiki.meta.narrators) == (["Eiko Kadono"], ["Kim Mai Guest"])
