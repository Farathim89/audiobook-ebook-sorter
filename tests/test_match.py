from pathlib import Path

from app.core.metadata import read_local
from app.core.models import Book, BookType, Metadata, Status
from app.core.online import apply_candidate
from app.providers import Candidate
from app.providers.audible import Audible
from app.providers.match import rank
from app.readers.names import split_series_from_title


def _book(**meta) -> Book:
    b = Book(BookType.AUDIO, [Path("x.m4b")], Path("."))
    b.meta = Metadata(**meta)
    return b


def _cand(**meta) -> Candidate:
    return Candidate(Metadata(**meta), "audible")


def test_exact_title_and_author_is_confident_and_beats_relevance_order():
    book = _book(title="Dungeon Crawler Carl", authors=["Matt Dinniman"])
    ranked = rank(book, [
        _cand(title="The Butcher's Masquerade", authors=["Matt Dinniman"], series="Dungeon Crawler Carl", series_index=5),
        _cand(title="Dungeon Crawler Carl", authors=["Matt Dinniman"], series="Dungeon Crawler Carl", series_index=1),
    ])
    assert ranked[0].meta.series_index == 1 and ranked[0].confident
    assert not ranked[1].confident


def test_wrong_volume_is_never_confident():
    book = _book(title="By the Grace of the Gods, Vol. 02", authors=["Roy"], series_index=2)
    ranked = rank(book, [_cand(title="By the Grace of the Gods: Volume 3", authors=["Roy"], series_index=3)])
    assert not ranked[0].confident


def test_narrator_tagged_as_author_still_matches():
    # rip: albumartist = narrator, composer = author
    book = _book(title="The Tatami Time Machine Blues", authors=["Kurt Kanazawa"], narrators=["Tomihiko Morimi"])
    cand = _cand(title="The Tatami Time Machine Blues", authors=["Tomihiko Morimi"], narrators=["Kurt Kanazawa"])
    assert rank(book, [cand])[0].confident
    apply_candidate(book, cand)
    assert (book.meta.authors, book.meta.narrators) == (["Tomihiko Morimi"], ["Kurt Kanazawa"])
    assert book.sources["authors"] == "audible" and book.status is Status.READY


def test_light_novel_keeps_its_volume_in_the_title():
    book = _book(title="My Daughter Left the Nest, Vol. 03", authors=["Derek Shoales"], series_index=3)
    apply_candidate(book, _cand(title="My Daughter Left the Nest", authors=["Mojikakiya"],
                                series="My Daughter Left the Nest", series_index=3))
    assert book.meta.title == "My Daughter Left the Nest, Vol. 3"
    # a book whose title simply is the series name stays as it is
    dcc = _book(title="Dungeon Crawler Carl", authors=["Matt Dinniman"])
    apply_candidate(dcc, _cand(title="Dungeon Crawler Carl", authors=["Matt Dinniman"],
                               series="Dungeon Crawler Carl", series_index=1))
    assert dcc.meta.title == "Dungeon Crawler Carl"


def test_translation_is_not_confident():
    book = _book(title="Carl's Doomsday Scenario", authors=["Matt Dinniman"])
    ranked = rank(book, [
        _cand(title="Carl’s Doomsday Scenario", authors=["Matt Dinniman"], language="German"),
        _cand(title="Carl's Doomsday Scenario", authors=["Matt Dinniman"], language="English"),
    ])
    assert ranked[0].meta.language == "English" and ranked[0].confident
    assert not ranked[1].confident


def test_language_codes_match_names():
    book = _book(title="Tehanu", authors=["Ursula K. Le Guin"], language="en")
    assert rank(book, [_cand(title="Tehanu", authors=["Ursula K. Le Guin"], language="English")])[0].confident
    book.meta.language = "Unknown"
    assert rank(book, [_cand(title="Tehanu", authors=["Ursula K. Le Guin"], language="English")])[0].confident


def test_different_author_is_not_confident():
    book = _book(title="The Raven", authors=["Edgar Allan Poe"])
    assert not rank(book, [_cand(title="The Raven", authors=["Somebody Else"])])[0].confident


def test_subtitle_after_colon():
    book = _book(title="7th Time Loop", authors=["Touko Amekawa"])
    cand = _cand(title="7th Time Loop: The Villainess Enjoys a Carefree Life, Vol. 1", authors=["Touko Amekawa"])
    assert rank(book, [cand])[0].confident


def test_audible_drops_translators():
    product = {"title": "By the Grace of the Gods: Volume 2", "asin": "B0C9RS445X",
               "authors": [{"name": "Roy"}, {"name": "Mana Z - translator"}],
               "narrators": [{"name": "Ray Greenley"}],
               "series": [{"title": "By the Grace of the Gods Series", "sequence": "2"}],
               "release_date": "2023-07-11", "product_images": {"500": "http://img/500.jpg"},
               "publisher_summary": "<p>Ryoma <b>returns</b>.</p><p>More.</p>"}
    c = Audible(http=None)._candidate(product)
    assert c.meta.authors == ["Roy"]
    assert (c.meta.series_index, c.meta.year, c.meta.cover_url) == (2, 2023, "http://img/500.jpg")
    assert c.meta.description == "Ryoma returns.\nMore."


def test_series_split_out_of_titles():
    assert split_series_from_title("Earthsea Cycle Book 4 - Tehanu") == ("Tehanu", "Earthsea Cycle", 4)
    assert split_series_from_title("Tehanu (The Earthsea Cycle, Book 4)") == ("Tehanu", "The Earthsea Cycle", 4)


def test_known_series_is_stripped_from_title(tmp_path):
    f = tmp_path / "Tehanu (The Earthsea Cycle, Book 4).m4b"
    f.write_bytes(b"\0")
    book = Book(BookType.AUDIO, [f], tmp_path)
    read_local(book)
    assert (book.meta.title, book.meta.series, book.meta.series_index) == ("Tehanu", "The Earthsea Cycle", 4)
