"""ABS series names with a comma, and Audible series lists that hold a sibling series' volumes."""
from app.collection.core.collect import split_series
from app.collection.core.lookup import _names_other_series, _title_names

SPIN = "Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!"
MAIN = "The World of Otome Games is Tough for Mobs"


def test_a_comma_inside_a_series_name():
    assert split_series(f"{SPIN} #1") == [f"{SPIN} #1"]
    assert split_series("Solo Leveling #2, Yen Audio #5") == ["Solo Leveling #2", "Yen Audio #5"]
    assert split_series("Overlord") == ["Overlord"]


def _p(title, *series):
    return {"title": title, "series": [{"asin": a, "title": t} for a, t in series]}


def test_a_main_series_volume_under_the_spin_off():
    vol7 = _p("Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs, Vol. 7", ("S", SPIN), ("M", MAIN))
    vol9 = _p("Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs, Vol. 9", ("S", SPIN))
    own = _p("Trapped in a Dating Sim: Otome Games Are Tough For Us, Too!, Vol. 2", ("S", SPIN))
    assert _names_other_series(vol7, "S", SPIN) and _names_other_series(vol9, "S", SPIN)
    assert not _names_other_series(own, "S", SPIN)
    assert _title_names(vol9, MAIN) and not _title_names(own, MAIN)


def test_a_typo_or_a_short_title_is_still_this_series():
    typo = _p("Mushoku Tensei: Jobless Reincarnatio, Vol. 18")
    assert not _names_other_series(typo, "X", "Mushoku Tensei: Jobless Reincarnation")
    year3 = _p("Classroom of the Elite: Year 3, Vol. 1")
    assert _names_other_series(year3, "X", "Classroom of the Elite: Year 2")
    assert not _names_other_series(_p("I Had That Same Dream Again"), "X", "The Yoru Sumino Collection")


def test_year_2_is_not_a_spelling_of_year_1():
    from app.collection.core.collect import group
    from app.collection.core.models import Owned
    def o(series, i):
        return Owned(title=f"{series} {i}", authors=["Syougo Kinugasa"], series=series, index=i, fmt="audio",
                     kind="light novel", source="ABS")
    series, _ = group([o("Classroom of the Elite", 1), o("Classroom of the Elite (Year 2)", 1),
                       o("I Got a Cheat Skill in Another World", 1), o("I Got a Cheat Skill in Another World, Too", 2)])
    assert len(series) == 3


def test_the_franchise_list_drops_other_years():
    assert _names_other_series(_p("Classroom of the Elite: Year 2, Vol. 11"), "X", "Classroom of the Elite")
    assert _names_other_series(_p("Classroom of the Elite: Year 3, Vol. 1"), "X", "Classroom of the Elite")
    assert not _names_other_series(_p("Classroom of the Elite, Vol. 11"), "X", "Classroom of the Elite")
    assert not _names_other_series(_p("Classroom of the Elite (Light Novel), Vol. 8"), "X", "Classroom of the Elite")


def test_anilist_count_with_side_volumes_adds_no_rows():
    from app.collection.core.models import Series, Volume
    s = Series("k", "Classroom of the Elite (Year 2)", "Syougo Kinugasa", "light novel")
    s.audible = {float(i): Volume(float(i), "v") for i in (*range(1, 11), 4.5, 9.5)}
    s.total_hint = 15
    known = s.known("ebook")
    assert max(known) == 10 and 13.0 not in known
    assert s.anilist_unlisted("ebook") == 3
    plain = Series("p", "Overlord", "Kugane Maruyama", "light novel")
    plain.audible = {1.0: Volume(1.0, "v")}
    plain.total_hint = 4
    assert sorted(plain.known("ebook")) == [1, 2, 3, 4]


def test_alter_is_another_series():
    from app.collection.core.collect import group
    from app.collection.core.models import Owned
    def o(series, i):
        return Owned(title=f"{series} {i}", authors=["Asato Asato"], series=series, index=i, fmt="audio",
                     kind="light novel", source="ABS")
    series, _ = group([o("86--EIGHTY-SIX", 1), o("86--EIGHTY-SIX Alter", 1)])
    assert len(series) == 2


def test_an_odd_far_number_makes_no_gap_rows():
    from app.collection.core.models import Series, Volume
    s = Series("k", "Adachi and Shimamura", "Hitoma Iruma", "light novel")
    s.audible = {float(i): Volume(float(i), "v") for i in (*range(1, 13), 99.9)}
    known = s.known("ebook")
    assert 99.9 in known and 50.0 not in known and max(i for i in known if i < 99) == 12


def test_a_volume_number_right_after_the_series_name():
    from app.collection.core.lookup import _volume_after_name
    assert _volume_after_name("Goblin Slayer, Vol. 3", "Goblin Slayer") == 3
    assert _volume_after_name("Goblin Slayer, Vol. 14 (light novel)", "Goblin Slayer") == 14
    assert _volume_after_name("86--EIGHTY-SIX, Vol. 4 (light novel)", "86--EIGHTY-SIX") == 4
    assert _volume_after_name("Alya Sometimes Hides Her Feelings in Russian, Vol. 4.5", "Alya Sometimes Hides Her Feelings in Russian") == 4.5
    assert _volume_after_name("Goblin Slayer Side Story: Year One, Chapter 49", "Goblin Slayer") is None
    assert _volume_after_name("Goblin Slayer Side Story: Year One, Vol. 2", "Goblin Slayer") is None
    assert _volume_after_name("The Saga of Tanya the Evil, Vol. 3", "Saga of Tanya the Evil") == 3


def test_openlibrary_one_book_one_volume():
    from app.collection.core.lookup import Lookup
    from app.collection.core.models import Series, Volume
    docs = [{"key": "/w/7", "title": "Lily Clairet, Vol. 7"}, {"key": "/w/1", "title": "Lily Clairet, Vol. 1"},
            {"key": "/w/k", "title": "Kuma Kuma Kuma Bear (Light Novel) Vol. 11. 5"},
            {"key": "/w/t", "title": "Too-Perfect Saint"}, {"key": "/w/c", "title": "Carl's Doomsday Scenario"}]

    class FakeHttp:
        def get_json(self, url, params):
            return {"docs": docs}
    lk = Lookup.__new__(Lookup)
    lk.openlib = FakeHttp()
    lily = Series("l", "Lily Clairet", "Kaye Ng", "light novel")
    lily.audible = {float(i): Volume(float(i), f"Lily Clairet, Vol. {i}") for i in range(1, 8)}
    lk.openlibrary_books(lily)
    assert sorted(lily.others) == [1.0, 7.0]
    saint = Series("s", "The Silver Leaf Seductions", "x", "light novel")
    saint.audible = {float(i): Volume(float(i), "Too-Perfect Saint") for i in range(1, 4)}  # one title, three books
    lk.openlibrary_books(saint)
    assert saint.others == {}
    carl = Series("c", "Dungeon Crawler Carl", "Matt Dinniman", "audiobook")
    carl.audible = {2.0: Volume(2.0, "Carl's Doomsday Scenario"), 3.0: Volume(3.0, "The Dungeon Anarchist's Cookbook")}
    lk.openlibrary_books(carl)
    assert sorted(carl.others) == [2.0]


def test_side_stories_are_another_series():
    assert _names_other_series(_p("Adachi and Shimamura: Short Stories"), "X", "Adachi and Shimamura")
    assert not _names_other_series(_p("Adachi and Shimamura, Vol. 12"), "X", "Adachi and Shimamura")
    assert not _names_other_series(_p("Adachi and Shimamura: Short Stories 2"), "X", "Adachi and Shimamura: Short Stories")


def test_a_box_set_counts_for_its_volumes():
    from app.collection.core.models import Owned, Series, Volume
    s = Series("c", "Chaos of the Covenant", "M.R. Forbes", "audiobook")
    s.audible = {float(i): Volume(float(i), f"v{i}") for i in range(1, 8)}
    s.track = ["audio"]
    s.owned = [Owned("Hell's Rejects, Books 1-4", ["M.R. Forbes"], "Chaos of the Covenant", 1.0, "audio", "audiobook",
                     "ABS", path=r"H:\A\M.R. Forbes\Chaos of the Covenant\1-4 - Hell's Rejects, Books 1-4"),
               Owned("Damned If You Don't", ["M.R. Forbes"], "Chaos of the Covenant", 5.0, "audio", "audiobook", "ABS")]
    assert s.have("audio") == {1.0, 2.0, 3.0, 4.0, 5.0}
    assert [v.index for v in s.missing("audio")] == [6.0, 7.0]


def test_a_wrong_asin_in_your_library_does_not_move_a_volume():
    from app.collection.core.models import Owned, Series, Volume
    s = Series("m", "Mushoku Tensei", "Rifujin na Magonote", "light novel")
    s.audible = {1.0: Volume(1.0, "v1", asin="A1"), 2.0: Volume(2.0, "v2", asin="A2"), 6.0: Volume(6.0, "v6", asin="A6")}
    o1 = Owned("Vol. 1", ["R"], "Mushoku Tensei", 1.0, "audio", "light novel", "ABS", asin="A1")
    o2 = Owned("Vol. 2", ["R"], "Mushoku Tensei", 2.0, "audio", "light novel", "ABS", asin="A1")   # Vol. 1's ASIN
    part = Owned("Vol. 6 Part One", ["R"], "Mushoku Tensei", 6.1, "audio", "light novel", "ABS", asin="A6")
    s.owned = [o1, o2, part]
    assert (s.index_of(o1), s.index_of(o2), s.index_of(part)) == (1.0, 2.0, 6.0)


def test_a_trilogy_counts_for_three():
    from app.collection.core.models import Owned, Series, Volume
    s = Series("p", "Pangea Online", "S.L. Rowland", "audiobook")
    s.audible = {float(i): Volume(float(i), f"v{i}") for i in (1, 2, 3)}
    s.owned = [Owned("Pangea Online: The Complete Trilogy", ["S.L. Rowland"], "Pangea Online", 1.0, "audio",
                     "audiobook", "ABS", path=r"H:\A\S.L. Rowland\Pangea Online\1 - Pangea Online - The Complete Trilogy")]
    assert s.have("audio") == {1.0, 2.0, 3.0} and s.missing("audio") == []


def test_the_hobbit_on_its_own_counts_for_lotr():
    from app.collection.core.models import Owned, Series, Volume, link_other_series
    lotr = Series("lotr", "The Lord of the Rings", "J. R. R. Tolkien", "audiobook")
    lotr.audible = {0.5: Volume(0.5, "The Hobbit"), 1.0: Volume(1.0, "The Fellowship of the Ring")}
    lotr.owned = [Owned("The Fellowship of the Ring", ["J. R. R. Tolkien"], "The Lord of the Rings", 1.0, "audio",
                        "audiobook", "ABS")]
    hobbit = Owned("The Hobbit", ["J. R. R. Tolkien"], None, None, "audio", "audiobook", "ABS")
    villain = Series("v", "I'm in Love with the Villainess", "Inori", "light novel")
    villain.audible = {2.0: Volume(2.0, "I'm in Love with the Villainess")}   # a title that is only the name
    other = Series("v2", "Villainess Side", "Inori", "light novel")
    other.owned = [Owned("I'm in Love with the Villainess", ["Inori"], "Villainess Side", 1.0, "audio",
                         "light novel", "ABS")]
    link_other_series({"lotr": lotr, "v": villain, "v2": other}, [hobbit])
    assert lotr.have("audio") == {0.5, 1.0}
    assert villain.elsewhere == {}
