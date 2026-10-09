from pathlib import Path

from app.core.models import Book, BookType, Metadata, Status
from app.core.online import apply_candidate
from app.core.planner import Planner
from app.config import Folders
from app.providers.anilist import AniList


class FakeHttp:
    def __init__(self, media):
        self.media = media

    def post_json(self, url, payload):
        return {"data": {"Page": {"media": self.media}}}


def _media(fmt, english, romaji, staff, synonyms=()):
    return {"format": fmt, "siteUrl": "https://anilist.co/manga/1", "description": "A <i>deep</i> hole.<br>More.",
            "title": {"english": english, "romaji": romaji}, "synonyms": list(synonyms),
            "coverImage": {"extraLarge": "http://img/c.jpg"},
            "staff": {"edges": [{"role": r, "node": {"name": {"full": n}}} for r, n in staff]}}


def test_original_author_beats_artist_and_novel_flag():
    media = [
        _media("NOVEL", "Spy Classroom", "Spy Kyoushitsu", [("Story", "Takemachi")]),
        _media("MANGA", "Spy Classroom", "Spy Kyoushitsu",
               [("Art", "Kaname Seu"), ("Story & Art", "Somebody"), ("Original Story", "Takemachi")]),
    ]
    a = AniList(FakeHttp(media))
    manga = a.series("Spy Classroom", novel=False)
    assert manga.meta.authors == ["Takemachi"] and manga.meta.description == "A deep hole.\nMore."
    novel = a.series("spy kyoushitsu", novel=True)
    assert novel.meta.authors == ["Takemachi"]


def test_shortened_long_name_matches_only_when_allowed():
    a = AniList(FakeHttp([_media("NOVEL", "7th Time Loop: The Villainess Enjoys a Carefree Life Married to Her Worst Enemy!",
                                 "x", [("Story", "Touko Amekawa")])]))
    short = "7th Time Loop - The Villainess Enjoys a Carefree Life"
    assert a.series(short, novel=True) is None
    assert a.series(short, novel=True, prefix_ok=True) is not None
    # "7th Time Loop" is the main title (before the colon) of the only match: that counts
    assert a.series("7th Time Loop", novel=True, prefix_ok=True) is not None
    assert a.series("7th Time", novel=True, prefix_ok=True) is None  # too short to trust a prefix


def test_no_exact_title_no_match():
    a = AniList(FakeHttp([_media("MANGA", "Made in Abyss Official Anthology", "x", [("Story & Art", "X")])]))
    assert a.series("Made in Abyss", novel=False) is None


def test_manga_gets_manga_suffix_and_author(tmp_path):
    book = Book(BookType.COMIC, [Path("Made in Abyss v01.cbz")], Path("."))
    book.meta = Metadata(title="Made in Abyss, Vol. 1", series="Made in Abyss", series_index=1)
    book.sources = {"title": "filename", "series": "filename"}
    cand = AniList(FakeHttp([_media("MANGA", "Made in Abyss", "Made in Abyss", [("Story & Art", "Akihito Tsukushi")])])
                   ).series("Made in Abyss", novel=False)
    apply_candidate(book, cand)
    assert book.manga and book.author == "Akihito Tsukushi" and book.meta.cover_url
    plan = Planner(Folders(books_sorted=tmp_path, protected=[])).plan([book])
    assert plan.targets[book] == tmp_path / "Manga" / "Akihito Tsukushi" / "Made in Abyss (Manga)" / "1 - Made in Abyss, Vol. 1"
    assert book.status is Status.CHECK  # series still only from the file name


def test_anilist_match_confirms_a_guessed_manga_series(tmp_path):
    from app.core.metadata import read_local
    src = tmp_path / "in"
    cbz = src / "Daisuke Aizawa/The Eminence in Shadow (manga)/Volume 01/The Eminence in Shadow v01 (2021).cbz"
    cbz.parent.mkdir(parents=True)
    cbz.write_bytes(b"cbz")
    book = Book(BookType.COMIC, [cbz], src)
    read_local(book)
    assert book.status is Status.CHECK  # series guessed from the file name
    cand = AniList(FakeHttp([_media("MANGA", "The Eminence in Shadow", "Kage no Jitsuryokusha",
                                    [("Original Story", "Daisuke Aizawa")])])).series("The Eminence in Shadow", novel=False)
    apply_candidate(book, cand)
    assert book.status is Status.READY and book.meta.authors == ["Daisuke Aizawa"]


def test_metadata_json_next_to_a_cbz_is_read(tmp_path):
    from app.core.metadata import read_local
    src = tmp_path / "in"
    vol = src / "Some Author/Volume 01"
    vol.mkdir(parents=True)
    cbz = vol / "x v01.cbz"
    cbz.write_bytes(b"cbz")
    (vol / "metadata.json").write_text('{"title": "The Eminence in Shadow, Vol. 1 (manga)", "authors": ["Daisuke Aizawa"],'
                                       ' "series": ["The Eminence in Shadow #1"], "publisher": "Yen Press"}', encoding="utf-8")
    book = Book(BookType.COMIC, [cbz], src)
    read_local(book)
    m = book.meta
    assert (m.title, m.authors, m.series, m.series_index, m.publisher) == (
        "The Eminence in Shadow, Vol. 1", ["Daisuke Aizawa"], "The Eminence in Shadow", 1, "Yen Press")
    assert book.status is Status.READY


def test_comic_without_a_manga_hint_waits_for_anilist(tmp_path):
    from app.core.metadata import read_local
    src = tmp_path / "in"
    cbz = src / "Brian K. Vaughan/Saga v01.cbz"
    cbz.parent.mkdir(parents=True)
    cbz.write_bytes(b"cbz")
    (cbz.parent / "metadata.json").write_text('{"title": "Saga, Vol. 1", "authors": ["Brian K. Vaughan"],'
                                              ' "series": ["Saga #1"]}', encoding="utf-8")
    book = Book(BookType.COMIC, [cbz], src)
    read_local(book)
    assert book.status is Status.CHECK and not book.manga  # manga or comic is not known yet
    hinted = src / "X/Some Series (manga)/Volume 01/s v01.cbz"
    hinted.parent.mkdir(parents=True)
    hinted.write_bytes(b"cbz")
    book = Book(BookType.COMIC, [hinted], src)
    read_local(book)
    assert book.manga


def test_comic_without_series_is_manga_when_the_author_has_manga(tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    oda = Book(BookType.COMIC, [tmp_path / "op1.cbz"], tmp_path, meta=Metadata(title="One Piece, Vol. 1", authors=["Eiichiro Oda"]))
    oda.manga = oda.kind_known = True
    wanted = Book(BookType.COMIC, [tmp_path / "w.cbz"], tmp_path, meta=Metadata(title="Wanted!", authors=["Eiichiro Oda"]))
    other = Book(BookType.COMIC, [tmp_path / "s.cbz"], tmp_path, meta=Metadata(title="Saga", authors=["Brian K. Vaughan"]))
    OnlineLookup._kind_from_author([oda, wanted, other])
    assert wanted.manga and wanted.kind_known and not other.kind_known


def test_comic_is_manga_when_the_author_is_already_in_the_manga_library(tmp_path):
    from app.core.online import OnlineLookup
    wanted = Book(BookType.COMIC, [tmp_path / "w.cbz"], tmp_path, meta=Metadata(title="Wanted!", authors=["Eiichirou Oda"]))
    OnlineLookup._kind_from_author([wanted], {"eiichirouoda"})
    other = Book(BookType.COMIC, [tmp_path / "o.cbz"], tmp_path, meta=Metadata(title="W", authors=["Eiichiro Oda"]))
    OnlineLookup._kind_from_author([other], {"eiichirouoda"})
    assert other.manga
    assert wanted.manga


def test_stand_alone_manga_is_found_by_title(tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    lk = OnlineLookup(Folders(state=tmp_path / "state", protected=[]))
    lk.anilist = AniList(FakeHttp([_media("MANGA", "Wanted! Eiichiro Oda Before One Piece", "WANTED! Oda Eiichiro Tanpenshuu",
                                          [("Story & Art", "Eiichirou Oda")])]))
    lk.manga_root = None
    book = Book(BookType.COMIC, [tmp_path / "w.cbz"], tmp_path,
                meta=Metadata(title="Wanted! Eiichiro Oda Before One Piece", authors=["Eiichiro Oda"]))
    lk.lookup_anilist(book)
    assert book.manga and book.kind_known and book.meta.authors == ["Eiichirou Oda"]


def test_stand_alone_light_novel_by_title_needs_the_same_author(tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    lk = OnlineLookup(Folders(state=tmp_path / "state", protected=[]))
    lk.anilist = AniList(FakeHttp([_media("NOVEL", "Lonely Castle in the Mirror", "Kagami no Kojou", [("Story", "Mizuki Tsujimura")])]))
    same = Book(BookType.EBOOK, [tmp_path / "a.epub"], tmp_path, meta=Metadata(title="Lonely Castle in the Mirror", authors=["Mizuki Tsujimura"]))
    other = Book(BookType.EBOOK, [tmp_path / "b.epub"], tmp_path, meta=Metadata(title="Lonely Castle in the Mirror", authors=["Somebody Else"]))
    lk.lookup_anilist(same)
    lk.lookup_anilist(other)
    assert same.light_novel and not other.light_novel


def test_stand_alone_audiobook_is_a_light_novel_only_with_the_same_author(tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    lk = OnlineLookup(Folders(state=tmp_path / "state", protected=[]))
    lk.anilist = AniList(FakeHttp([_media("NOVEL", "Mushoku Tensei: Jobless Reincarnation", "x", [("Story", "Rifujin na Magonote")])]))
    same = Book(BookType.AUDIO, [tmp_path / "a.m4b"], tmp_path,
                meta=Metadata(title="Mushoku Tensei: Jobless Reincarnation", authors=["Rifujin na Magonote"]))
    other = Book(BookType.AUDIO, [tmp_path / "b.m4b"], tmp_path,
                 meta=Metadata(title="Mushoku Tensei: Jobless Reincarnation", authors=["Somebody Else"]))
    prefix = Book(BookType.AUDIO, [tmp_path / "c.m4b"], tmp_path, meta=Metadata(title="Mushoku", authors=["Rifujin na Magonote"]))
    for b in (same, other, prefix):
        lk.mark_light_novel(b)
    assert same.light_novel and not other.light_novel and not prefix.light_novel


def test_main_title_before_the_colon_matches_with_the_same_author():
    a = AniList(FakeHttp([_media("NOVEL", "Hell Mode: The Hardcore Gamer Dominates in Another World with Garbage Balancing",
                                 "Hell Mode: Yarikomi-zuki no Gamer", [("Original Story", "Hamuo")])]))
    assert a.series("Hell Mode", novel=True, authors=["Hamuo"]).meta.authors == ["Hamuo"]
    assert a.series("Hell Mode", novel=True, authors=["Someone Else"]) is None
    assert a.series("Hell Mode", novel=True) is not None  # no author known: the only match counts
    two = AniList(FakeHttp([_media("MANGA", "Hell Mode: Summoner of the Beginning", "x", [("Original Story", "Hamuo")]),
                            _media("MANGA", "Hell Mode: Another One", "y", [("Story & Art", "Someone")])]))
    assert two.series("Hell Mode", novel=False) is None  # two could be meant, no author to choose
    assert two.series("Hell Mode", novel=False, authors=["Hamuo"]).meta.authors == ["Hamuo"]


def test_mixed_kinds_in_one_series_get_a_note(tmp_path):
    from app.core.online import _note_mixed_kinds
    epubs = [Book(BookType.EBOOK, [tmp_path / f"{n}.epub"], tmp_path, meta=Metadata(title=f"V{n}", series="Hell Mode", series_index=n))
             for n in (1, 2, 3)]
    cbz = Book(BookType.COMIC, [tmp_path / "6.cbz"], tmp_path, meta=Metadata(title="V6", series="Hell Mode", series_index=6))
    _note_mixed_kinds(epubs + [cbz])
    _note_mixed_kinds(epubs + [cbz])  # no duplicate notes
    assert cbz.notes == ["a comic among this series' ebooks - the manga adaptation?"] and not epubs[0].notes


def test_series_and_number_titles_get_one_style(tmp_path):
    from app.core.metadata import read_local
    d = tmp_path / "in/Hamuo/Hell Mode/Hell Mode - Volume 01"
    d.mkdir(parents=True)
    epub = d / "Hell Mode - Volume 01.epub"
    epub.write_bytes(b"x")
    (d / "metadata.opf").write_text('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
                                    'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Hell Mode: Volume 1</dc:title>'
                                    '<dc:creator>Hamuo</dc:creator><meta name="calibre:series" content="Hell Mode"/>'
                                    '<meta name="calibre:series_index" content="1"/></metadata></package>', encoding="utf-8")
    book = Book(BookType.EBOOK, [epub], tmp_path / "in", folder=d)
    read_local(book)
    assert book.meta.title == "Hell Mode, Vol. 1"


def test_short_series_name_becomes_the_full_real_title(tmp_path):
    from app.core.online import apply_candidate
    a = AniList(FakeHttp([_media("NOVEL", "Hell Mode: The Hardcore Gamer Dominates in Another World with Garbage Balancing",
                                 "x", [("Original Story", "Hamuo")])]))
    book = Book(BookType.EBOOK, [tmp_path / "1.epub"], tmp_path,
                meta=Metadata(title="Hell Mode, Vol. 1", series="Hell Mode", series_index=1, authors=["Hamuo"]))
    apply_candidate(book, a.series("Hell Mode", novel=True, authors=["Hamuo"]))
    full = "Hell Mode: The Hardcore Gamer Dominates in Another World with Garbage Balancing"
    assert (book.meta.series, book.meta.title) == (full, full + ", Vol. 1")


def test_romanizations_are_one_person_and_one_author_folder(tmp_path):
    from app.core.consistency import unify_names
    from app.core.text import name_key
    assert name_key("Shōgo Kinugasa") == name_key("Syougo Kinugasa") == name_key("Shougo Kinugasa")
    assert name_key("Eiichirou Oda") == name_key("Eiichiro Oda")
    a = Book(BookType.COMIC, [tmp_path / "a.cbz"], tmp_path, meta=Metadata(title="A", authors=["Shōgo Kinugasa"]))
    b = Book(BookType.EBOOK, [tmp_path / "b.epub"], tmp_path, meta=Metadata(title="B", authors=["Syougo Kinugasa"]))
    c = Book(BookType.EBOOK, [tmp_path / "c.epub"], tmp_path, meta=Metadata(title="C", authors=["Syougo Kinugasa"]))
    unify_names([a, b, c], [])
    assert a.author == b.author == c.author == "Syougo Kinugasa"


def test_unknown_comic_stays_undecided_and_ln_authors_comics_are_manga(tmp_path):
    from app.config import Folders
    from app.core.online import OnlineLookup
    lk = OnlineLookup(Folders(state=tmp_path / "state", protected=[]))
    lk.anilist = AniList(FakeHttp([]))
    lk.manga_root = None
    comic = Book(BookType.COMIC, [tmp_path / "1.cbz"], tmp_path,
                 meta=Metadata(title="Classroom of the Elite (Year 1), Vol. 1", series="Classroom of the Elite (Year 1)",
                               series_index=1, authors=["Shōgo Kinugasa"]))
    lk.lookup_anilist(comic)
    assert not comic.kind_known and comic.status.value == "check"  # not found is NOT 'western comic'
    ln = Book(BookType.EBOOK, [tmp_path / "2.epub"], tmp_path, meta=Metadata(title="Y2", authors=["Syougo Kinugasa"]))
    ln.light_novel = True
    OnlineLookup._kind_from_author([comic, ln])
    assert comic.manga


def test_several_novels_of_the_author_fit_keep_our_own_series():
    media = [_media("NOVEL", "Bungo Stray Dogs: BEAST", "Bungou Stray Dogs BEAST", [("Story", "Kafka Asagiri")]),
             _media("NOVEL", "Bungo Stray Dogs: Osamu Dazai's Entrance Exam", "x", [("Story", "Kafka Asagiri")])]
    cand = AniList(FakeHttp(media)).series("Bungo Stray Dogs", novel=True, authors=["Kafka Asagiri"])
    assert cand is not None and cand.exact                      # a light novel, but not one picked by name
    assert cand.meta.series == "Bungo Stray Dogs" and cand.meta.cover_url is None
    book = Book(BookType.EBOOK, [Path("v1.epub")], Path("."))
    book.meta = Metadata(title="Bungo Stray Dogs, Vol. 1", series="Bungo Stray Dogs", series_index=1,
                         authors=["Kafka Asagiri"])
    apply_candidate(book, cand)
    assert book.meta.series == "Bungo Stray Dogs" and book.light_novel


def test_one_novel_of_the_author_still_gives_the_full_name():
    media = [_media("NOVEL", "Hell Mode: The Hardcore Gamer Dominates", "x", [("Story", "Hamuo")])]
    cand = AniList(FakeHttp(media)).series("Hell Mode", novel=True, authors=["Hamuo"])
    assert cand is not None and not cand.exact and cand.meta.series.startswith("Hell Mode: The Hardcore")
