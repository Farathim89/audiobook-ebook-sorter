"""Productions no shop sells: Soundbooth's 'Dungeon Crawler Carl Audio Immersion Tunnel' seasons."""
from pathlib import Path

from app.core.abs_index import AbsBook, AbsIndex
from app.core.known_series import apply_known_series
from app.core.m4b import chapters_from_files
from app.core.models import Book, BookType, Metadata, Status

SERIES = "Dungeon Crawler Carl: Audio Immersion Tunnel"


def _book(folder, meta=None, files=None, btype=BookType.AUDIO):
    d = Path("in") / "Matt Dinniman" / folder
    b = Book(btype, [d / f for f in (files or [f"{folder}.m4b"])], Path("in"), folder=d)
    b.meta = meta or Metadata()
    return b


def _abs_with_book(n, title):
    return AbsIndex([AbsBook("1", "AudioBooks", "x", title, ["Matt Dinniman"], ["Jeff Hays"],
                             [("Dungeon Crawler Carl", float(n))], "B0", "audio", False)])


def test_a_season_by_its_folder_name_only():
    b = _book("Dungeon Crawler Carl - Season 4 - Audio Immersion Tunnel")
    assert apply_known_series(b, _abs_with_book(4, "The Gate of the Feral Gods"))
    m = b.meta
    assert (m.title, m.series, m.series_index) == (f"{SERIES}, Season 4", SERIES, 4.0)
    assert m.subtitle == "The Gate of the Feral Gods"  # book 4, as Audiobookshelf has it
    assert m.authors == ["Matt Dinniman"] and m.publisher == "Soundbooth Theater"
    assert m.narrators[0] == "Jeff Hays"
    assert b.looked_up and b.status is Status.READY


def test_its_own_metadata_is_tidied_and_the_shop_asin_dropped():
    # how Season 1 first arrived: no number, the series spelled with ' - ', and a shop's ASIN
    meta = Metadata(title="Dungeon Crawler Carl Audio Immersion Tunnel", subtitle="Season 1 - Audio Immersion Tunnel",
                    series="Dungeon Crawler Carl - Audio Immersion Tunnel", authors=["Matt Dinniman"],
                    narrators=["Jeff Hays", "Andrea Parsneau"], asin="B08V8B2CGV")
    b = _book("Dungeon Crawler Carl - Season 1 - Audio Immersion Tunnel", meta)
    assert apply_known_series(b)
    assert (b.meta.title, b.meta.series_index, b.meta.asin) == (f"{SERIES}, Season 1", 1.0, None)
    assert b.meta.subtitle is None  # 'Season 1 - Audio Immersion Tunnel' only repeated the title
    assert b.meta.narrators == ["Jeff Hays", "Andrea Parsneau"]  # the files' own cast stays


def test_the_season_from_episode_file_names():
    b = _book("Dungeon Crawler Carl - Audio Immersion Tunnel",
              files=["DDC S02E01 - 000.mp3", "DDC S02E01 - 001.mp3", "DDC S02E02 - 001.mp3"])
    assert apply_known_series(b) and b.meta.series_index == 2.0


def test_no_season_number_is_left_for_you_to_check():
    b = _book("Audio Immersion Tunnel")
    assert apply_known_series(b) and b.meta.series_index is None
    apply_known_series(b)  # run twice (before and after the votes): one note
    assert sum("no season number" in n for n in b.notes) == 1


def test_other_books_and_ebooks_are_left_alone():
    assert not apply_known_series(_book("1 - Dungeon Crawler Carl", Metadata(title="Dungeon Crawler Carl")))
    assert not apply_known_series(_book("Audio Immersion Tunnel - Season 1", btype=BookType.EBOOK))


def test_a_season_in_pieces_gets_one_chapter_per_episode():
    files = [Path(n) for n in ("DDC S02E01 - 000.mp3", "DDC S02E01 - 000Opening Credits.mp3", "DDC S02E01 - 001.mp3",
                               "DDC S02E02 - 000Opening Credits.mp3", "DDC S02E02 - 001.mp3", "DDC S02E03 - 01.mp3")]
    chapters = chapters_from_files(files, [10, 5, 100, 5, 200, 50])
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0, 115, "Episode 1"), (115, 320, "Episode 2"), (320, 370, "Episode 3")]
