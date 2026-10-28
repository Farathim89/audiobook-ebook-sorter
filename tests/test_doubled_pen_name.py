"""'All the Dust That Falls' by zaifyr: Audible lists the author as 'zaifyr zaifyr', the series titles as
'All the Dust That Falls Two' / 'All the Dust that Falls 3' / '... Four', and our tag joins title + subtitle."""
from pathlib import Path

from app.core.models import Book, BookType, Metadata
from app.core.online import _doubled_pen_names
from app.core.planner import folder_title
from app.providers import Candidate
from app.providers.match import rank

SERIES = "All the Dust That Falls"


def _book(title, idx=None, authors=("zaifyr",)):
    b = Book(BookType.AUDIO, [Path(f"{title}.m4b")], Path("."))
    b.meta = Metadata(title=title, series=SERIES if idx else None, series_index=idx, authors=list(authors))
    return b


def _audible(title, idx, asin):
    return Candidate(Metadata(title=title, subtitle="An Isekai LitRPG Adventure", authors=["zaifyr zaifyr"],
                              narrators=["Phil Thron"], series=SERIES, series_index=idx, asin=asin), "audible", "")


def test_a_doubled_one_word_pen_name_is_the_same_author():
    best = rank(_book(SERIES, 1), [_audible(SERIES, 1, "B0C5NYGFRT"), _audible("All the Dust that Falls 3", 3, "B3")])
    assert best[0].meta.asin == "B0C5NYGFRT" and best[0].confident


def test_our_title_with_the_subtitle_in_it_matches_the_shop_title_and_subtitle():
    ours = _book("All the Dust That Falls Two: An Isekai LitRPG Adventure", 2)
    assert rank(ours, [_audible("All the Dust That Falls Two", 2, "B2")])[0].confident


def test_a_number_word_after_the_series_name_is_the_volume():
    assert folder_title(_book("All the Dust That Falls Two", 2)) == f"{SERIES}, Vol. 2"
    assert folder_title(_book("All the Dust That Falls Four", 4)) == f"{SERIES}, Vol. 4"
    assert folder_title(_book("All the Dust That Falls Four", 3)) == "All the Dust That Falls Four"  # not #3
    assert folder_title(_book("All the Dust That Falls Two Worlds", 2)) == "All the Dust That Falls Two Worlds"


def test_the_shops_doubled_name_goes_when_the_books_own_files_said_it_once():
    books = [_book(SERIES, n, authors=["zaifyr zaifyr"]) for n in (1, 2)]  # all took Audible's spelling
    _doubled_pen_names(books, {"zaifyr"})
    assert [b.meta.authors for b in books] == [["zaifyr"], ["zaifyr"]]
    asato = _book("86--EIGHTY-SIX, Vol. 1", authors=["Asato Asato"])
    _doubled_pen_names([asato], set())  # nobody said 'Asato' alone: a real name, kept
    assert asato.meta.authors == ["Asato Asato"]
