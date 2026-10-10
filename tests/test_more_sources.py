from pathlib import Path

from app.core.models import Book, BookType, Metadata, Status
from app.core.online import apply_fill
from app.providers.googlebooks import GoogleBooks
from app.providers.itunes import ITunes
from app.providers.match import rank
from app.providers.openlibrary import OpenLibrary


class FakeHttp:
    def __init__(self, data):
        self.data = data

    def get_json(self, url, params=None):
        return self.data(url) if callable(self.data) else self.data


def test_itunes_candidate():
    c = ITunes(FakeHttp({"results": [{
        "collectionName": "Project Hail Mary (Unabridged)", "artistName": "Andy Weir",
        "releaseDate": "2021-05-04T07:00:00Z", "description": "<p>Ryland <b>Grace</b></p>",
        "artworkUrl100": "https://x/100x100bb.jpg", "collectionViewUrl": "https://books.apple.com/1"}]})).search("x")[0]
    assert (c.meta.title, c.meta.authors, c.meta.year) == ("Project Hail Mary", ["Andy Weir"], 2021)
    assert c.meta.cover_url == "https://x/1000x1000bb.jpg" and c.meta.description == "Ryland Grace"


def test_openlibrary_candidate_and_description():
    def data(url):
        if url.endswith(".json") and "/works/" in url:
            return {"description": {"value": "Arrakis."}}
        return {"docs": [{"key": "/works/OL1W", "title": "Dune", "author_name": ["Frank Herbert"],
                          "first_publish_year": 1965, "isbn": ["0441013597", "9780441013593"], "cover_i": 42,
                          "language": ["eng"]}]}
    ol = OpenLibrary(FakeHttp(data))
    c = ol.search("Dune", "Frank Herbert")[0]
    assert (c.meta.isbn, c.meta.year, c.meta.language) == ("9780441013593", 1965, "eng")
    assert c.meta.cover_url == "https://covers.openlibrary.org/b/id/42-L.jpg"
    ol.add_description(c)
    assert c.meta.description == "Arrakis."


def test_google_books_rate_limit_is_skipped_quietly():
    import urllib.error

    class Limited:
        calls = 0

        def get_json(self, url, params=None):
            Limited.calls += 1
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    g = GoogleBooks(Limited())
    assert g.search("Dune") == [] and g.search("Dune") == [] and Limited.calls == 1


def test_gap_filler_fills_only_empty_fields_and_confirms_guesses():
    book = Book(BookType.EBOOK, [Path("Frank Herbert - Dune.epub")], Path("."))
    book.meta = Metadata(title="Dune", authors=["Frank Herbert"], year=1990)
    book.sources = {"title": "filename", "authors": "filename", "year": "tags"}
    cand = OpenLibrary(FakeHttp({"docs": [{"key": "/works/OL1W", "title": "Dune", "author_name": ["Frank Herbert"],
                                           "first_publish_year": 1965, "cover_i": 1}]})).search("Dune")[0]
    assert rank(book, [cand], language="")[0].confident
    apply_fill(book, cand)
    assert book.meta.year == 1990                        # kept ours
    assert book.meta.cover_url and book.sources["title"] == "openlibrary"  # filled + guess confirmed
    assert book.status is Status.READY


def test_audible_takes_the_numbered_series():
    from app.providers.audible import Audible
    a = Audible.__new__(Audible)
    a.region = "us"
    c = a._candidate({"asin": "B0D3G9F94G", "title": "I Had That Same Dream Again",
                      "authors": [{"name": "Yoru Sumino"}],
                      "series": [{"title": "I Had That Same Dream Again", "sequence": ""},
                                 {"title": "The Yoru Sumino Collection", "sequence": "3"}]})
    assert (c.meta.series, c.meta.series_index) == ("The Yoru Sumino Collection", 3)


def test_audible_takes_the_series_the_title_names():
    from app.providers.audible import Audible
    a = Audible.__new__(Audible)
    a.region = "us"
    c = a._candidate({"asin": "B1", "title": "Harry Potter and the Order of the Phoenix", "authors": [],
                      "series": [{"title": "Wizarding World", "sequence": "5"},
                                 {"title": "Harry Potter", "sequence": "5"}]})
    assert (c.meta.series, c.meta.series_index) == ("Harry Potter", 5)
