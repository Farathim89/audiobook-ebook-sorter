from pathlib import Path

from app.config import Folders
from app.core import online
from app.core.models import Book, BookType, Metadata
from app.core.online import OnlineLookup, looks_swedish
from app.core.planner import Planner
from app.providers.http import Cache
from app.providers.storytel import Storytel

ITEM = {
    "book": {"name": "Män som hatar kvinnor", "href": "/se/books/1356", "seriesOrder": 1,
             "authors": [{"name": "Stieg Larsson"}], "series": [{"name": "Millennium"}],
             "language": {"isoValue": "sv"}, "largeCover": "/images/320x320/0000001356.jpg"},
    "abook": {"narrators": [{"name": "Reine Brynolfsson"}], "length": 68189000, "isbn": "9789173133586",
              "releaseDateFormat": "2007-08-21", "description": "Folk har alltid hemligheter.",
              "publisher": {"name": "Norstedts"}},
}


class FakeHttp:
    def __init__(self, data):
        self.data = data

    def get_json(self, url, params=None):
        return self.data


def test_storytel_candidate():
    c = Storytel(FakeHttp({"books": [ITEM, {"book": {"name": "E-book only"}}]})).search("x")
    assert len(c) == 1
    m = c[0].meta
    assert (m.title, m.authors, m.narrators, m.series, m.series_index) == (
        "Män som hatar kvinnor", ["Stieg Larsson"], ["Reine Brynolfsson"], "Millennium", 1)
    assert (m.language, m.year, round(c[0].length)) == ("Swedish", 2007, 68189)
    assert m.cover_url == "https://www.storytel.com/images/320x320/0000001356.jpg"


def _book(title, author, files=()):
    b = Book(BookType.AUDIO, [Path(f) for f in files] or [Path("x.mp3")], Path("."))
    b.meta = Metadata(title=title, authors=[author])
    return b


def test_looks_swedish():
    assert looks_swedish(_book("Män som hatar kvinnor", "Stieg Larsson"))
    assert not looks_swedish(_book("Dune", "Frank Herbert"))


def test_storytel_match_needs_the_same_length(monkeypatch, tmp_path):
    look = OnlineLookup(Folders(state=tmp_path), Cache(None))
    look.storytel = Storytel(FakeHttp({"books": [ITEM]}))
    look.audible.search = lambda **k: []
    look.itunes = look.google = look.openlib = None  # offline test
    book = _book("Män som hatar kvinnor", "Stieg Larsson")

    monkeypatch.setattr(online, "book_length", lambda b: 68189 * 1.10)  # 10 % longer: another recording
    look.lookup(book)
    assert not book.meta.narrators and book.candidates and not book.candidates[0].confident

    book = _book("Män som hatar kvinnor", "Stieg Larsson")
    monkeypatch.setattr(online, "book_length", lambda b: 68189 + 30)
    look.lookup(book)
    assert book.meta.narrators == ["Reine Brynolfsson"] and book.sources["narrators"] == "storytel"
    assert book.meta.language == "Swedish"

    # with a Swedish library set, it goes there
    plan = Planner(Folders(swedish_sorted=tmp_path / "sv", audio_sorted=tmp_path / "a", protected=[])).plan([book])
    assert plan.targets[book] == tmp_path / "sv" / "Stieg Larsson" / "Millennium" / "1 - Män som hatar kvinnor"
