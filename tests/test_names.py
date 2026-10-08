import pytest

from app.core.models import BookType
from app.core.text import parse_index, split_people
from app.readers.names import (context_from_dirs, parse_name, parse_volume,
                               split_series_from_title, strip_release)


@pytest.mark.parametrize("name, author, series, idx, title", [
    ("Brandon Sanderson - Mistborn 01 - The Final Empire", "Brandon Sanderson", "Mistborn", 1, "The Final Empire"),
    ("Brandon Sanderson - [Stormlight Archive 2] - Words of Radiance", "Brandon Sanderson", "Stormlight Archive", 2, "Words of Radiance"),
    ("The Way of Kings (Stormlight Archive #1) - Brandon Sanderson", "Brandon Sanderson", "Stormlight Archive", 1, "The Way of Kings"),
    ("Andy Weir - Project Hail Mary (2021) (Unabridged)", "Andy Weir", None, None, "Project Hail Mary"),
    ("03 - The Hero of Ages", None, None, 3, "The Hero of Ages"),
    ("Dune", None, None, None, "Dune"),
    ("andy_weir_-_the_martian", "andy weir", None, None, "the martian"),
    ("The Martian by Andy Weir", "Andy Weir", None, None, "The Martian"),
    ("Jean-Paul Sartre - Nausea", "Jean-Paul Sartre", None, None, "Nausea"),
    ("2001 A Space Odyssey", None, None, None, "2001 A Space Odyssey"),
])
def test_parse_book_names(name, author, series, idx, title):
    m = parse_name(name, BookType.EBOOK)
    assert (m.authors[0] if m.authors else None) == author
    assert m.series == series
    assert m.series_index == idx
    assert m.title == title


def test_narrator_and_year_are_pulled_out():
    m = parse_name("Andy Weir - The Martian (2014) [Read by R.C. Bray]", BookType.AUDIO)
    assert m.year == 2014
    assert m.narrators == ["R.C. Bray"]
    assert m.title == "The Martian"


@pytest.mark.parametrize("name, series, idx, year", [
    ("Made in Abyss v01 (2018) (Digital) (LuCaZ) (f)", "Made in Abyss", 1, 2018),
    ("Saga 001 (2012) (Digital) (Empire)", "Saga", 1, 2012),
    ("One Piece, Vol. 3 [Viz]", "One Piece", 3, None),
    ("Spy x Family Volume 05", "Spy x Family", 5, None),
])
def test_parse_comic_names(name, series, idx, year):
    m = parse_name(name, BookType.COMIC)
    assert (m.series, m.series_index, m.year) == (series, idx, year)


def test_strip_release():
    assert strip_release("Made in Abyss v01 (2018) (Digital) (LuCaZ) [f]") == "Made in Abyss v01"


def test_volume_and_title_series_split():
    assert parse_volume("Sword Art Online: Volume 01") == ("Sword Art Online", 1)
    assert parse_volume("Catch 22") is None
    assert parse_volume("Catch 22", trailing_number=True) == ("Catch", 22)
    assert split_series_from_title("The Way of Kings: The Stormlight Archive, Book 1") == (
        "The Way of Kings", "The Stormlight Archive", 1)
    assert split_series_from_title("The Final Empire (Mistborn #1)") == ("The Final Empire", "Mistborn", 1)


def test_people_and_index():
    assert split_people("Sanderson, Brandon") == ["Brandon Sanderson"]
    assert split_people("Alan Moore, Dave Gibbons") == ["Alan Moore", "Dave Gibbons"]
    assert split_people("Terry Pratchett & Neil Gaiman") == ["Terry Pratchett", "Neil Gaiman"]
    assert parse_index("3/10") == 3
    assert parse_index("Book 2.5") == 2.5


def test_folder_context():
    from app.core.models import Metadata
    m = context_from_dirs(["Brandon Sanderson", "Mistborn"], BookType.AUDIO, Metadata(title="The Final Empire"))
    assert (m.authors, m.series) == (["Brandon Sanderson"], "Mistborn")
    # release folders and generic folders never become the author
    m = context_from_dirs(["Downloads", "The Martian (2014) [MP3]"], BookType.AUDIO, Metadata(title="x"))
    assert m.authors == []
    # comics: only a user-made top folder counts
    m = context_from_dirs(["Akihito Tsukushi", "Made in Abyss (Digital)"], BookType.COMIC, Metadata(series="Made in Abyss"))
    assert m.authors == ["Akihito Tsukushi"]
    m = context_from_dirs(["Made in Abyss"], BookType.COMIC, Metadata(series="Made in Abyss"))
    assert m.authors == []


def test_catalogue_code_is_dropped_from_titles():
    from app.core.text import clean_title
    assert clean_title("Lotta på Bråkmakargatan C41663") == "Lotta på Bråkmakargatan"
    assert clean_title("Apollo 13") == "Apollo 13" and clean_title("Room 101") == "Room 101"


def test_translator_credit_is_not_an_author(tmp_path):
    from app.core.metadata import read_local
    from app.core.models import Book, BookType, Metadata
    from mutagen.mp4 import MP4
    import subprocess, pytest
    from app.core import m4b
    if not m4b.ffmpeg():
        pytest.skip("ffmpeg not installed")
    d = tmp_path / "in/J.K. Rowling/HP1"
    d.mkdir(parents=True)
    f = d / "HP1.m4b"
    subprocess.run([m4b.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "1", "-c:a", "aac",
                    "-f", "mp4", str(f)], check=True)
    t = MP4(f); t["\xa9alb"] = ["Harry Potter och De Vises Sten"]; t["\xa9ART"] = ["J.K. Rowling, Lena Fries-Gedin - translator"]; t.save()
    book = Book(BookType.AUDIO, [f], tmp_path / "in", folder=d)
    read_local(book)
    assert book.meta.authors == ["J.K. Rowling"]


def test_swedish_reader_in_folder_name():
    from app.core.models import BookType
    from app.readers.names import parse_name
    m = parse_name("Volume 01 - (1997) Harry Potter och de vises sten (Björn Kjellman läser)", BookType.AUDIO)
    assert m.narrators == ["Björn Kjellman"] and "läser" not in (m.title or "")
    assert parse_name("Bröderna Lejonhjärta (uppläsare: Tomas Schweder)", BookType.AUDIO).narrators == ["Tomas Schweder"]
