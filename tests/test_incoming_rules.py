"""Rules from a real Incoming batch: narrator in {braces}, too-long file names, one folder style,
illustrators credited as authors, Japanese-only author names, the same book as audio and ebook."""
import json
from pathlib import Path

from app.config import Folders
from app.core.metadata import read_local
from app.core.models import Book, BookType, Metadata, Status
from app.core.online import _latin_author_names, _lone_extra_authors, _same_kind_per_series
from app.core.planner import Planner, folder_title


def _book(title, series=None, idx=None, authors=(), btype=BookType.AUDIO, ln=False):
    b = Book(btype, [Path(f"{title}.m4b")], Path("."))
    b.meta = Metadata(title=title, series=series, series_index=idx, authors=list(authors))
    b.light_novel = ln
    b.status = Status.READY
    return b


def test_narrator_in_braces_and_the_author_as_narrator(tmp_path):
    d = tmp_path / "in" / "Inori" / "Commoner" / "Volume 01 {Courtney Shaw}"
    d.mkdir(parents=True)
    f = d / "Vol. 01.m4b"
    f.write_bytes(b"x")
    (d / "metadata.json").write_text(json.dumps({"title": "Commoner, Vol. 01", "authors": ["Inori"],
                                                 "narrators": ["Inori"]}), encoding="utf-8")
    book = Book(BookType.AUDIO, [f], tmp_path / "in", folder=d)
    read_local(book)
    assert book.meta.narrators == ["Courtney Shaw"]


def test_one_folder_style_for_long_titles():
    parry = _book("I Parry Everything: What Do You Mean I'm the Strongest? I'm Not Even an Adventurer Yet!, Vol. 1",
                  "I Parry Everything: What Do You Mean I’m the Strongest? I’m Not Even an Adventurer Yet!", 1)
    assert folder_title(parry) == "I Parry Everything, Vol. 1"


def test_too_long_audio_file_gets_the_book_name(tmp_path):
    series = "I Got a Cheat Skill in Another World and Became Unrivaled in The Real World, Too"
    f = Path(f"{series}, Vol. 1 - Miku; Noboru Akimoto - translator.m4b")
    b = Book(BookType.AUDIO, [f], Path("."))
    b.meta = Metadata(title=f"{series}, Vol. 1", series=series, series_index=1, authors=["Miku"])
    b.light_novel, b.status = True, Status.READY
    sorted_root = tmp_path / ("S" * max(1, 46 - len(str(tmp_path))))  # as long as the real staging folder
    folders = Folders(audio_sorted=sorted_root, state=tmp_path / "s", protected=[])
    plan = Planner(folders).plan([b])
    assert not plan.problems.get(b)
    assert all(len(str(mv.dst)) <= 259 for mv in plan.moves)


def test_illustrator_as_author_in_one_volume():
    vols = [_book(f"Slimes, Vol. {n}", "Slimes", n, ["Kisetsu Morita"], ln=True) for n in (1, 2, 4)]
    odd = _book("Slimes, Vol. 3", "Slimes", 3, ["Kisetsu Morita", "Yusuke Shiba"], ln=True)
    only = _book("Slimes, Vol. 5", "Slimes", 5, ["Benio"], btype=BookType.EBOOK, ln=True)
    _lone_extra_authors([*vols, odd, only])
    assert odd.meta.authors == ["Kisetsu Morita"] and only.meta.authors == ["Kisetsu Morita"]


def test_japanese_only_name_gets_the_series_latin_name():
    v1 = _book("Evil Lord, Vol. 1", "Evil Lord", 1, ["Yomu Mishima"])
    v2 = _book("Evil Lord, Vol. 2", "Evil Lord", 2, ["三嶋与夢"])
    _latin_author_names([v1, v2])
    assert v2.meta.authors == ["Yomu Mishima"]


def test_same_book_as_audio_and_ebook_gets_one_kind():
    audio = _book("I Want to Eat Your Pancreas", "The Yoru Sumino Collection", 1, ["Yoru Sumino"], ln=True)
    ebook = _book("I Want to Eat Your Pancreas", None, None, ["Yoru Sumino"], btype=BookType.EBOOK)
    _same_kind_per_series([audio, ebook])
    assert ebook.light_novel


def test_series_range_in_metadata_json(tmp_path):
    from app.readers.sidecar import read_abs_json
    p = tmp_path / "metadata.json"
    p.write_text(json.dumps({"title": "Musashi", "series": ["Musashi: An Epic Novel of the Samurai Era #01–07"]}),
                 encoding="utf-8")
    m = read_abs_json(p)
    assert (m.series, m.series_index) == ("Musashi: An Epic Novel of the Samurai Era", None)


def test_series_dash_volume_dash_title_file_name():
    from app.readers.names import parse_name
    m = parse_name("Sword Art Online - Volume 17 - Alicization Awakening [Yen Press][Kobo]", BookType.EBOOK)
    assert (m.series, m.series_index, m.title, m.authors) == ("Sword Art Online", 17, "Alicization Awakening", [])


def test_roles_follow_the_rest_of_the_series():
    from app.core.online import _roles_by_series
    ok = []
    for n in (1, 2, 3, 4):
        b = _book(f"Mushoku Tensei, Vol. {n}", "Mushoku Tensei", n, ["Rifujin na Magonote"])
        b.meta.narrators = ["Cliff Kurt"]
        ok.append(b)
    swapped = _book("Mushoku Tensei, Vol. 13", "Mushoku Tensei", 13, ["Cliff Kurt"])
    swapped.meta.narrators = ["Rifujin na Magonote"]
    _roles_by_series([*ok, swapped])
    assert (swapped.meta.authors, swapped.meta.narrators) == (["Rifujin na Magonote"], ["Cliff Kurt"])


def test_ebook_without_metadata_takes_the_audiobook_next_to_it(tmp_path):
    from app.core.online import _from_folder_sibling
    folder = tmp_path / "Lily Clairet" / "Volume 02"
    audio = Book(BookType.AUDIO, [folder / "Lily Clairet, Vol. 02.m4b"], tmp_path)
    audio.meta = Metadata(title="Lily Clairet, Vol. 2", series="Lily Clairet", series_index=2, authors=["Kaye Ng"])
    audio.sources.update(authors="audible", series="audible", series_index="audible")
    audio.light_novel = True
    ebook = Book(BookType.EBOOK, [folder / "Lily Clairet, Vol. 02.epub"], tmp_path)
    ebook.meta = Metadata(title="Lily Clairet, Vol. 2", series="Lily Clairet", series_index=2, authors=["Lily Clairet"])
    ebook.sources.update(authors="folder", series="filename", series_index="filename", title="filename")
    _from_folder_sibling([audio, ebook])
    assert ebook.meta.authors == ["Kaye Ng"] and ebook.light_novel and ebook.status is Status.READY


def test_title_with_a_typo_in_the_series_gets_the_style_and_keeps_its_subtitle():
    from app.core.online import apply_candidate
    from app.providers import Candidate
    b = _book("Legend of Galactic Heroes, Vol. 03: Endurance", "Legend of the Galactic Heroes", 3, ["Yoshiki Tanaka"])
    b.meta.subtitle = "Endurance"
    apply_candidate(b, Candidate(Metadata(title="Legend of the Galactic Heroes, Vol. 3", subtitle="Dawn",
                                          series="Legend of the Galactic Heroes", series_index=3,
                                          authors=["Yoshiki Tanaka"]), "audible"))
    assert (b.meta.title, b.meta.subtitle) == ("Legend of the Galactic Heroes, Vol. 3", "Endurance")


def test_one_series_folder_for_monogatari_and_min_maxing():
    from app.core.online import _keep_shared_series, _series_head_vote
    vols = [_book(f"Monogatari {n}", "Monogatari", n, ["NISIOISIN"]) for n in (2, 3, 4, 8)]
    first = _book("Bakemonogatari, Vol. 1", "Bakemonogatari", 1, ["NISIOISIN"])   # what Audible said
    own = {id(b): "Monogatari" for b in [*vols, first]}
    _keep_shared_series([*vols, first], own)
    assert first.meta.series == "Monogatari"
    audio = [_book(f"Min-Maxing, Vol. {n}", "Min-Maxing My TRPG Build in Another World", n, ["Schuld"]) for n in (1, 2, 3)]
    ebook = _book("x", "Min-Maxing My TRPG Build in Another World: Preach the Good Word of Mr. Henderson", 3, ["Schuld"],
                  btype=BookType.EBOOK)
    _series_head_vote([*audio, ebook])
    assert ebook.meta.series == "Min-Maxing My TRPG Build in Another World"


def test_two_titles_in_one_and_series_number_titles(tmp_path):
    d = tmp_path / "in" / "Monogatari" / "Part 03"
    d.mkdir(parents=True)
    f = d / "x.m4b"
    f.write_bytes(b"x")
    (d / "metadata.json").write_text(json.dumps({
        "title": "BAKEMONOGATARI, Part 3; BAKEMONOGATARI: Monster Tale, Part 03", "authors": ["NISIOISIN"],
        "series": ["Monogatari #3"]}), encoding="utf-8")
    book = Book(BookType.AUDIO, [f], tmp_path / "in", folder=d)
    read_local(book)
    assert book.meta.title == "BAKEMONOGATARI: Monster Tale, Part 03"
    stars = _book("Nowhere Stars 2", "Nowhere Stars", 2, ["Anemone"])
    from app.core.metadata import strip_tags
    strip_tags(stars)
    assert stars.meta.title == "Nowhere Stars, Vol. 2"


def test_shortened_book_folder_keeps_its_volume(tmp_path):
    long = "My Daughter Left the Nest and Returned an S-Rank Adventurer"
    b = _book(f"{long}, Vol. 10", long, 10, ["Mojikakiya"], btype=BookType.EBOOK, ln=True)
    name = Planner(Folders(books_sorted=tmp_path, state=tmp_path / "s", protected=[])).book_dir(b, limit=60).name
    assert name.endswith(", Vol. 10") and len(name) <= 60


def test_ebooks_of_a_light_novel_series_are_light_novels_too():
    audio = [_book(f"Min-Maxing, Vol. {n}", "Min-Maxing", n, ["Schuld"], ln=True) for n in (1, 2)]
    ebook = _book("Min-Maxing: Preach the Good Word, Vol. 3", "Min-Maxing", 3, ["Schuld"], btype=BookType.EBOOK)
    other = _book("Other, Vol. 3", "Min-Maxing", 3, ["Somebody Else"], btype=BookType.EBOOK)
    _same_kind_per_series([*audio, ebook, other])
    assert ebook.light_novel and not other.light_novel


def test_spin_off_keeps_its_own_series_and_the_main_series_its_whole_name():
    from app.core.online import _keep_shared_series
    main = [_book(f"Trapped in a Dating Sim, Vol. {n}", "Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs",
                  n, ["Yomu Mishima"]) for n in (1, 2, 3)]
    spin = _book("Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!, Vol. 1",
                 "Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!", 1, ["Yomu Mishima"])
    own = {id(b): "The World of Otome Games is Tough for Mobs" for b in [*main, spin]}
    _keep_shared_series([*main, spin], own)
    assert {b.meta.series for b in main} == {"Trapped in a Dating Sim: The World of Otome Games is Tough for Mobs"}
    assert spin.meta.series == "Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!"


def test_manga_volumes_follow_their_folder_and_neighbours(tmp_path):
    from app.core.online import _comics_like_their_folder
    eng = tmp_path / "Tanya (Digital)"
    jp = tmp_path / "The Saga of Tanya the Evil"
    rag = tmp_path / "Ragnarok"
    books = []
    for n in range(1, 4):
        b = Book(BookType.COMIC, [eng / f"v{n}.cbz"], tmp_path)
        b.meta = Metadata(title=f"The Saga of Tanya the Evil, Vol. {n}", series="The Saga of Tanya the Evil",
                          series_index=n, authors=["Carlo Zen"])
        books.append(b)
    senki = Book(BookType.COMIC, [jp / "Youjo Senki Vol. 29.cbz"], tmp_path)
    senki.meta = Metadata(title="Youjo Senki, Vol. 29", series="Youjo Senki", series_index=29, authors=["Carlo Zen"])
    mate = Book(BookType.COMIC, [rag / "Solo Leveling Ragnarok 046.cbz"], tmp_path, manga=True, kind_known=True)
    mate.meta = Metadata(title="Solo Leveling Ragnarok, Vol. 46", series="Solo Leveling Ragnarok", series_index=46,
                         authors=["Daul"])
    special = Book(BookType.COMIC, [rag / "Solo Leveling Ragnarok 047 - Season 1 End.cbz"], tmp_path)
    special.meta = Metadata(title="Solo Leveling Ragnarok 047 - Season 1 End")
    special.sources["title"] = "filename"
    _comics_like_their_folder([*books, senki, mate, special])
    assert senki.meta.series == "The Saga of Tanya the Evil"
    assert (special.meta.series, special.meta.series_index, special.meta.authors, special.manga) == (
        "Solo Leveling Ragnarok", 47.0, ["Daul"], True)


def test_a_damaged_sorted_copy_makes_room_for_a_clean_new_one(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.config import Folders
    from app.core import m4b
    from app.core.library import replace_damaged_copies
    f = Folders(audio_sorted=tmp_path / "sorted", done=tmp_path / "done", state=tmp_path / "state")
    old = f.audio_sorted / "Light Novels" / "Chugong" / "Solo Leveling" / "2 - Solo Leveling, Vol. 2"
    old.mkdir(parents=True)
    (old / "Solo Leveling, Vol. 02.m4b").write_bytes(b"old")
    new = tmp_path / "in" / "Solo Leveling, Vol. 02.m4b"
    new.parent.mkdir()
    new.write_bytes(b"new")
    book = Book(BookType.AUDIO, [new], tmp_path / "in")
    monkeypatch.setattr(m4b, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(m4b, "play_test", lambda p, **kw: "56 damaged audio packets" if "Light Novels" in str(p) else "")
    assert replace_damaged_copies(f, SimpleNamespace(targets={book: old}), [book]) == [book]
    assert not old.exists()
    assert (f.done / "Replaced (damaged)" / old.relative_to(f.audio_sorted) / "Solo Leveling, Vol. 02.m4b").exists()


def test_a_less_damaged_new_copy_replaces_only_when_clearly_better(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.config import Folders
    from app.core import m4b
    from app.core.library import replace_damaged_copies
    f = Folders(audio_sorted=tmp_path / "sorted", done=tmp_path / "done", state=tmp_path / "state")
    monkeypatch.setattr(m4b, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(m4b, "play_test", lambda p, **kw: "9 damaged audio packets at 1:00:00")
    for new_count, replaced in ((19, True), (56, False)):
        old = f.audio_sorted / "Light Novels" / "A" / f"Book {new_count}"
        old.mkdir(parents=True)
        (old / "b.m4b").write_bytes(b"old")
        new = tmp_path / "in" / str(new_count) / "b.m4b"
        new.parent.mkdir(parents=True)
        new.write_bytes(b"new")
        book = Book(BookType.AUDIO, [new], tmp_path / "in")
        monkeypatch.setattr(m4b, "damaged_packets", lambda p, c=None, n=new_count: 56 if "sorted" in p.parts else n)
        got = replace_damaged_copies(f, SimpleNamespace(targets={book: old}), [book])
        assert (got == [book]) is replaced
