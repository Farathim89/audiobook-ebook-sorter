"""The Author page's works: Audible / AniList / Google Books answers merged per series, next to what you have."""
from app.authors.works import Works, add_anilist, add_audible, add_books, add_yours, same_author
from app.core.abs_index import AbsBook

ME = "Reki Kawahara"


def _audible(title, n, series="Sword Art Online", language="english", author=ME):
    return {"asin": f"B{n}", "title": title, "language": language, "authors": [{"name": author}],
            "series": [{"title": series, "sequence": str(n), "asin": "SAO"}], "release_date": "2021-08-10"}


def _staff(*media):
    return {"staffMedia": {"edges": [{"staffRole": role, "node": {"title": {"english": t}, "format": f, "volumes": v,
                                                                   "status": "RELEASING", "siteUrl": f"x/{t}"}}
                                     for t, f, v, role in media]}}


def _abs(title, series, n, library="AudioBooks", fmt="audio"):
    return AbsBook(title, library, f"x/{title}", title, [ME], [], [(series, n)], "", fmt, False)


def test_audible_and_anilist_join_on_the_series_name():
    works = Works()
    add_audible(works, [_audible("Sword Art Online 1", 1), _audible("Sword Art Online 2", 2),
                        _audible("Sword art online 1", 1, "Sword art online (Italian Edition)", "italian")], ME)
    add_anilist(works, _staff(("Sword Art Online", "NOVEL", None, "Story"), ("Accel World", "MANGA", 8, "Original Story"),
                              ("Sword Art Online", "TV", None, "Original Creator")))
    sao = works.by_key[next(k for k in works.by_key if "swordart" in k)]
    assert sao.online("audio") == 2 and sao.kinds == {"audio", "ln"}
    assert set(sao.links) == {"Audible", "AniList"}
    accel = next(w for w in works.sorted() if w.name == "Accel World")
    assert accel.online("manga") == 8
    assert len(works.by_key) == 2  # no Italian edition, no anime


def test_what_you_have_and_what_is_missing():
    works = Works()
    add_audible(works, [_audible(f"Sword Art Online {n}", n) for n in (1, 2, 3)], ME)
    add_yours(works, [_abs("Sword Art Online 1", "Sword Art Online", 1.0),
                      _abs("Accel World 1", "Accel World", 1.0, "Light Novels", "ebook")], ME)
    sao = next(w for w in works.sorted() if w.name == "Sword Art Online")
    assert (sao.have("audio"), sao.missing("audio")) == (1, 2)
    accel = next(w for w in works.sorted() if w.name == "Accel World")  # only in your library
    assert accel.have("ln") == 1 and accel.online("ln") == 0


def test_ebook_volumes_count_as_light_novels_of_a_known_light_novel():
    works = Works()
    add_anilist(works, _staff(("Accel World", "NOVEL", None, "Story")))
    add_books(works, [("Accel World, Vol. 1", [ME], "2014", ""), ("Accel World, Vol. 2", [ME], "2015", ""),
                      ("Accel World, Vol. 2", [ME], "2016", ""),  # another edition of 2
                      ("Sword Art Online刀劍神域 (1)", [ME], "2010", ""),  # another language
                      ("Accel World, Vol. 3", ["Someone Else"], "2015", "")], ME, "google")
    accel = next(w for w in works.sorted() if w.name == "Accel World")
    assert accel.online("ln") == 2 and accel.online("ebook") == 0
    assert len(works.by_key) == 1


def test_romanizations_and_credits_are_the_same_author():
    assert same_author("Syougo Kinugasa", ["Shogo Kinugasa"])
    assert same_author(ME, ["Reki Kawahara - author", "abec"])
    assert not same_author(ME, ["abec"])


def test_volumes_between_listed_ones_count_but_one_far_off_number_doesnt():
    works = Works()
    add_anilist(works, _staff(("Accel World", "NOVEL", None, "Story")))
    add_books(works, [(f"Accel World, Vol. {n} (light Novel)", [ME], "2017", "") for n in (12, 14, 26)], ME, "openlibrary")
    accel = next(w for w in works.sorted() if w.name == "Accel World")
    assert accel.online("ln") == 26 and accel.missing("ln") == 26
    assert all("(light" not in v.title for v in accel.volumes)
    works = Works()
    add_books(works, [(f"Tiny, Vol. {n}", [ME], "", "") for n in (1, 2, 3, 250)], ME, "google")
    assert next(iter(works.by_key.values())).online("ebook") == 3


def test_related_works_fold_under_their_main_series():
    from app.authors.works import Work, parents
    names = ["Sword Art Online", "Sword Art Online: Aincrad", "Sword Art Online Progressive",
             "Sword Art Online Progressive Barcarolle of Froth", "Sword Art Onlines", "Accel World",
             "Accel World / Dural: Magisa Garden", "The Isolator", "The Isolator, realization of absolute solitude"]
    up = parents([Work(n) for n in names])
    assert [names[up[i]] if up[i] is not None else None for i in range(len(names))] == [
        None, "Sword Art Online", "Sword Art Online", "Sword Art Online Progressive", None, None, "Accel World",
        None, "The Isolator"]


def test_one_series_under_its_other_names_and_a_volume_audible_lists_without_series():
    works = Works()
    add_audible(works, [{"asin": "B5", "title": "Heroine? Saint? No, I'm an All-Works Maid (and Proud of It)!, Vol. 5",
                         "language": "english", "authors": [{"name": ME}], "series": []},
                        _audible("Heroine? Saint? No, I'm an All-Works Maid (And Proud of It)!, Vol. 1", 1,
                                 "Heroine? Saint? No, I'm an All-Works Maid (And Proud of It)!")], ME)
    assert len(works.by_key) == 1 and next(iter(works.by_key.values())).online("audio") == 5
    works = Works()
    add_anilist(works, {"staffMedia": {"edges": [{"staffRole": "Story", "node": {
        "title": {"english": None, "romaji": "Saikyou no Shokugyou wa Yuusha demo Kenja demo Naku Kanteishi"},
        "synonyms": ["The strongest job is apparently not a hero or a sage, but an appraiser"],
        "format": "NOVEL", "volumes": None}}]}})
    add_books(works, [("The strongest job is apparently not a hero or a sage, but an appraiser, Vol. 1", [ME], "", "")],
              ME, "openlibrary")
    assert [w.name for w in works.sorted()] == ["The strongest job is apparently not a hero or a sage, but an appraiser"]


def test_spin_offs_that_contain_the_series_name_or_its_other_name_fold_under_it():
    from app.authors.works import Work, parents
    slime = Work("That Time I Got Reincarnated as a Slime", other_names=["Tensei Shitara Slime Datta Ken"])
    names = ["The Slime Diaries: That Time I Got Reincarnated as a Slime", "Tensei Shitara Slime Datta Ken: Bangai-hen",
             "Tenchura! That Time I Got Reincarnated as a Slime", "That Time I Got Reincarnated (Again!) as a Workaholic Slime"]
    works = [slime] + [Work(n) for n in names]
    up = parents(works)
    assert [up[i] for i in range(1, 5)] == [0, 0, 0, None]


def test_a_count_alone_lists_its_volumes():
    from app.authors.works import Work
    w = Work("Scarlet Bond", counts={"manga": 2}, kinds={"manga"})
    assert w.numbers("manga") == {1.0, 2.0} and w.online("manga") == 2
