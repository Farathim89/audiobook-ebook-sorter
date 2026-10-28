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


def test_a_volume_number_only_in_the_title_still_counts():
    # Storytel: no series number, 'Volume 2' at the end of the title - not our volume 1
    book = _book(title="A Cave King's Road to Paradise: Climbing to the Top with My Almighty Mining Skills! Volume 1",
                 authors=["Hajime Naehara"], series_index=1)
    cand = _cand(title="A Cave King's Road to Paradise: Climbing to the Top with My Almighty Mining Skills! Volume 2",
                 authors=["Hajime Naehara"])
    assert not rank(book, [cand])[0].confident


def test_same_asin_is_confident_even_when_the_shop_title_is_shorter():
    book = _book(title="A Cave King's Road to Paradise: Climbing to the Top!, Vol. 1", authors=["Hajime Naehara"],
                 series_index=1, asin="B0F8KXZFVM")
    cand = _cand(title="A Cave King's Road to Paradise", authors=["Hajime Naehara"], asin="B0F8KXZFVM",
                 series="Cave King's Road to Paradise: Climbing to the Top!", series_index=1)
    assert rank(book, [cand])[0].confident
    apply_candidate(book, cand)
    assert book.meta.title == "A Cave King's Road to Paradise, Vol. 1"  # the shop title is just the series' start


def test_shop_tags_are_dropped_after_an_online_match():
    book = _book(title="A Summoner Awakens", authors=["Kerberos"])
    apply_candidate(book, _cand(title="A Summoner Awakens: Origins: (A Deck Building LitRPG)", authors=["Kerberos"],
                                series="A Summoner Awakens", series_index=1))
    assert book.meta.title == "A Summoner Awakens: Origins"
    saint = _book(title="A Tale of the Secret Saint, Vol. 1", authors=["Touya"], series_index=1)
    apply_candidate(saint, _cand(title="A Tale of the Secret Saint (Light Novel) Vol. 1", authors=["Touya"],
                                 series="A Tale of the Secret Saint (Light Novel)", series_index=1))
    assert (saint.meta.title, saint.meta.series, saint.light_novel) == \
        ("A Tale of the Secret Saint, Vol. 1", "A Tale of the Secret Saint", True)


def test_series_key_ignores_a_leading_article():
    from app.core.consistency import series_key
    assert series_key("A Cave King’s Road to Paradise") == series_key("Cave King's Road to Paradise")


def test_title_volume_beats_a_wrong_series_number_in_the_metadata_file(tmp_path):
    from app.core.metadata import _tidy
    f = tmp_path / "A Cave King's Road to Paradise, Vol. 01.m4b"
    book = Book(BookType.AUDIO, [f], tmp_path)
    book.meta = Metadata(title="A Cave King's Road to Paradise: Climbing to the Top! Volume 1",
                         series="A Cave King’s Road to Paradise: Climbing to the Top!", series_index=2,
                         authors=["Hajime Naehara"])
    book.sources.update(title="sidecar", series="sidecar", series_index="sidecar")
    _tidy(book)
    assert book.meta.series_index == 1


def test_series_gets_its_article_back_from_the_title():
    book = _book(title="A Cave King's Road to Paradise, Vol. 2", authors=["Hajime Naehara"], series_index=2)
    apply_candidate(book, _cand(title="A Cave King's Road to Paradise", authors=["Hajime Naehara"],
                                series="Cave King's Road to Paradise: Climbing to the Top!", series_index=2))
    assert book.meta.series == "A Cave King's Road to Paradise: Climbing to the Top!"
    other = _book(title="Ascension", authors=["Kerberos"], series_index=2)
    apply_candidate(other, _cand(title="Ascension", authors=["Kerberos"], series="Summoner Awakens", series_index=2))
    assert other.meta.series == "Summoner Awakens"  # nothing in the title to take it from


def test_series_spelling_tie_keeps_the_article():
    from collections import Counter
    from app.core.consistency import _pick
    assert _pick(Counter({"Cave King's Road": 1, "A Cave King's Road": 1})) == "A Cave King's Road"
    assert _pick(Counter({"Cave King's Road": 1000, "A Cave King's Road": 1})) == "Cave King's Road"  # library wins


def test_audible_titles_get_one_style():
    bs = _book(title="Black Summoner, Vol. 01", authors=["Doufu Mayoi"], series_index=1)
    apply_candidate(bs, _cand(title="Black Summoner: Volume 1", authors=["Doufu Mayoi"],
                              series="Black Summoner", series_index=1))
    assert bs.meta.title == "Black Summoner, Vol. 1"
    boc = _book(title="Beware of Chicken 2", authors=["Casualfarmer"], series_index=2)
    apply_candidate(boc, _cand(title="Beware of Chicken 2: A Xianxia Cultivation Novel", authors=["Casualfarmer"],
                               series="Beware of Chicken", series_index=2))
    assert boc.meta.title == "Beware of Chicken, Vol. 2"  # one style with Vol. 1 (was: the shop title)
    one = _book(title="Beware of Chicken, Vol. 1", authors=["Casualfarmer"], series_index=1)
    apply_candidate(one, _cand(title="Beware of Chicken: A Xianxia Cultivation Novel", authors=["Casualfarmer"],
                               series="Beware of Chicken", series_index=1))
    assert one.meta.title == "Beware of Chicken, Vol. 1"
    keep = _book(title="The Cultivation of Plants", authors=["X"])               # a real title stays
    apply_candidate(keep, _cand(title="The Cultivation of Plants: A Guide", authors=["X"]))
    assert keep.meta.title == "The Cultivation of Plants: A Guide"


def test_novel_subtitle_means_light_novel():
    b = _book(title="Breathless Time Traveler", authors=["Yuyuko Takemiya"])
    apply_candidate(b, _cand(title="Breathless Time Traveler", subtitle="Novel", authors=["Yuyuko Takemiya"]))
    assert b.light_novel
    w = _book(title="Dungeon Crawler Carl", authors=["Matt Dinniman"])
    apply_candidate(w, _cand(title="Dungeon Crawler Carl", subtitle="A LitRPG Adventure", authors=["Matt Dinniman"]))
    assert not w.light_novel


def test_audible_series_word_is_dropped():
    b = _book(title="By the Grace of the Gods, Vol. 10", authors=["Roy"], series_index=10)
    apply_candidate(b, _cand(title="By the Grace of the Gods: Volume 10", authors=["Roy"],
                             series="By the Grace of the Gods Series", series_index=10))
    assert (b.meta.series, b.meta.title) == ("By the Grace of the Gods", "By the Grace of the Gods, Vol. 10")


def test_a_credited_illustrator_is_no_author_in_any_volume():
    from app.core.online import _drop_credited
    v1 = _book(title="Bungo Stray Dogs, Vol. 1", authors=["Kafka Asagiri", "Sango Harukawa"])
    v2 = _book(title="Bungo Stray Dogs, Vol. 2", authors=["Kafka Asagiri", "Sango Harukawa"])
    v2.credits = ["Sango Harukawa"]
    _drop_credited([v1, v2])
    assert v1.meta.authors == v2.meta.authors == ["Kafka Asagiri"]


def test_box_set_gets_its_range():
    from app.config import Folders
    from app.core.metadata import series_number
    from app.core.planner import Planner
    box = _book(title="Hell's Rejects, Books 1-4", series="Chaos of the Covenant", series_index=1, authors=["M.R. Forbes"])
    one = _book(title="Hell's Rejects", series="Chaos of the Covenant", series_index=1, authors=["M.R. Forbes"])
    assert series_number(box) == "1-4" and series_number(one) == "1"
    assert Planner(None).book_name(box) == "1-4 - Hell's Rejects, Books 1-4"
    assert series_number(_book(title="Volume 4.5", series="X", series_index=4.5)) == "4.5"
    assert series_number(_book(title="Best of 2010-2020", series="X", series_index=1)) == "1"   # not a range of books
    f = Folders(audio_sorted=Path("C:/out"), state=Path("C:/state"), protected=[])
    for b in (box, one):
        b.status = Status.READY
    plan = Planner(f).plan([box, one])
    assert not plan.problems.get(box) and not plan.problems.get(one)   # a box set is no duplicate of book 1


def test_number_words_and_a_guessed_folder_series():
    from app.readers.names import parse_volume, words_to_numbers
    assert words_to_numbers("Constellations Volume One") == "Constellations Volume 1"
    assert parse_volume("Constellations Book Twelve") == ("Constellations", 12.0)
    assert words_to_numbers("One Piece Volume 3") == "One Piece Volume 3"           # only after vol/book/part
    b = _book(title="Constellations, Vol. 1", series="Constellations", series_index=1, authors=["Leela Townsley"])
    b.sources.update(series="folder", series_index="folder", title="sidecar", authors="sidecar")
    apply_candidate(b, _cand(title="Constellations Volume One", authors=["Leela Townsley"]))
    assert b.meta.title == "Constellations, Vol. 1" and b.status is Status.READY


def test_title_style_keeps_the_series_name():
    b = _book(title="x", series="Even Dogs Go to Other Worlds", series_index=2, authors=["Ryuuou"])
    apply_candidate(b, _cand(title="Even Dogs Go to Other Worlds: Life in Another World with My Beloved Hound Vol. 2",
                             authors=["Ryuuou"], series="Even Dogs Go to Other Worlds", series_index=2))
    assert b.meta.series == "Even Dogs Go to Other Worlds"
    assert b.meta.title == "Even Dogs Go to Other Worlds: Life in Another World with My Beloved Hound, Vol. 2"


def test_one_latin_name_for_a_cjk_author():
    from app.core.online import _latin_author_names
    v1 = _book(title="Disciple of the Lich, Vol. 1", series="Disciple of the Lich", series_index=1, authors=["猫子"])
    v2 = _book(title="Disciple of the Lich, Vol. 2", series="Disciple of the Lich", series_index=2, authors=["猫子", "Necoco"])
    _latin_author_names([v1, v2])
    assert v1.meta.authors == v2.meta.authors == ["Necoco"]


def test_long_series_keeps_its_folder_and_shortens_at_the_exclamation_mark():
    from app.config import Folders
    from app.core.planner import Planner, folder_series
    s = "Enough with This Slow Life! I Was Reincarnated as a High Elf and Now I'm Bored"
    assert folder_series(s) == "Enough with This Slow Life!"
    root = Path("C:/" + "x" * 90)
    f = Folders(audio_sorted=root, state=Path("C:/state"), protected=[])
    targets = []
    for n, title in ((1, f"{s}, Vol. 1"), (2, "Enough with This Slow Life!, Vol. 2")):
        b = _book(title=title, series=s, series_index=n, authors=["Rarutori"])
        b.status = Status.READY
        targets.append(Planner(f).plan([b]).targets[b])
    assert targets[0].parent == targets[1].parent                    # one series folder for both
    assert targets[0].name == "1 - Enough with This Slow Life!, Vol. 1"


def test_audible_series_is_only_the_second_half_of_the_name():
    from app.core.online import _full_series_names
    from app.core.planner import folder_title
    half = "What Do You Mean I'm the Strongest? I'm Not Even an Adventurer Yet!"
    v1 = _book(title="x", series_index=1, authors=["Nabeshiki"])
    apply_candidate(v1, _cand(title=f"I Parry Everything: {half}, Volume 1", authors=["Nabeshiki"], series=half, series_index=1))
    v2 = _book(title="I Parry Everything, Vol. 2", series_index=2, authors=["Nabeshiki"])
    apply_candidate(v2, _cand(title="I Parry Everything", subtitle=f"{half} Volume 2", authors=["Nabeshiki"],
                              series=half, series_index=2))
    assert v1.meta.series == f"I Parry Everything: {half}"
    _full_series_names([v1, v2])
    assert v2.meta.series == v1.meta.series
    assert folder_title(v1) == "I Parry Everything, Vol. 1" and folder_title(v2) == "I Parry Everything, Vol. 2"


def test_missing_narrator_comes_from_another_audible_store(monkeypatch, tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    import app.core.online as online
    lk = OnlineLookup(Folders(state=tmp_path, use_storytel=False))
    asked = []

    class FakeAudible:
        def __init__(self, http, region):
            self.region = region

        def by_asin(self, asin):
            asked.append(self.region)
            return [_cand(title="X", narrators=["Bryson Baugus"] if self.region == "ca" else [])]
    monkeypatch.setattr(online, "Audible", FakeAudible)
    book = _book(title="DanMachi, Vol. 9", authors=["Fujino Omori"], asin="B0FPN9YJQY")
    lk.fill_narrators(book)
    assert book.meta.narrators == ["Bryson Baugus"] and book.sources["narrators"] == "audible.ca"
    assert asked == ["uk", "ca"]
    none = _book(title="Y", authors=["Z"], asin="B0")
    monkeypatch.setattr(online, "Audible", lambda http, region: type("A", (), {"by_asin": lambda s, a: []})())
    lk.fill_narrators(none)
    assert none.meta.narrators == [] and any("narrator not listed" in n for n in none.notes)


def test_another_edition_is_not_applied(monkeypatch, tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    import app.core.online as online
    lk = OnlineLookup(Folders(state=tmp_path, use_storytel=False))
    fry = _cand(title="Harry Potter and the Prisoner of Azkaban", authors=["J.K. Rowling"], narrators=["Stephen Fry"],
                asin="B017WGK7Q6")
    fry.length = 12.05 * 3600
    monkeypatch.setattr(lk.audible, "by_asin", lambda asin: [fry])
    monkeypatch.setattr(lk.audible, "search", lambda **q: [fry])
    monkeypatch.setattr(online, "book_length", lambda b: 11.80 * 3600)
    book = _book(title="Harry Potter and the Prisoner of Azkaban", authors=["J.K. Rowling"], narrators=["Jim Dale"],
                 asin="B017WGK7Q6")
    book.sources["narrators"] = "label"
    assert not any(c.confident for c in lk._audible(book))
    assert book.meta.asin is None and any("another edition" in n for n in book.notes)


def test_a_title_before_the_colon_is_only_sure_for_a_numbered_volume():
    book = _book(title="Mistborn", authors=["Brandon Sanderson"])
    assert not rank(book, [_cand(title="Mistborn: Secret History", authors=["Brandon Sanderson"])])[0].confident
