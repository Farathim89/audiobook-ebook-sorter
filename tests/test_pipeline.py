from pathlib import Path

from app.config import Folders
from app.core.models import BookType, Status
from app.core.pipeline import load_books
from app.core.planner import Planner, clean_name


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\0")


def _folders(tmp_path: Path) -> Folders:
    return Folders(audio_sorted=tmp_path / "out_audio", books_sorted=tmp_path / "out_books",
                   review=tmp_path / "review", protected=[tmp_path / "real"])


def test_end_to_end(tmp_path):
    src = tmp_path / "in"
    _touch(src,
           "Brandon Sanderson/Mistborn/01 - The Final Empire/CD1/01.mp3",
           "Brandon Sanderson/Mistborn/01 - The Final Empire/CD2/01.mp3",
           "Brandon Sanderson/Mistborn/01 - The Final Empire/cover.jpg",
           "Brandon Sanderson/Mistborn/01 - The Final Empire/booklet.pdf",
           "Andy Weir/Project Hail Mary.m4b",
           "Andy Weir/The Martian.m4b",
           "Frank Herbert/Dune.epub",
           "Frank Herbert/Dune.pdf",
           "Made in Abyss v01 (2018) (Digital) (LuCaZ).cbz",
           "Akihito Tsukushi/Made in Abyss v02 (2018) (Digital).cbz")
    books = load_books([src])
    by_title = {b.meta.title: b for b in books}
    assert sorted(by_title) == ["Dune", "Made in Abyss, Vol. 1", "Made in Abyss, Vol. 2",
                                "Project Hail Mary", "The Final Empire", "The Martian"]

    fe = by_title["The Final Empire"]
    assert fe.type is BookType.AUDIO and len(fe.files) == 2
    assert [p.name for p in fe.extras] == ["booklet.pdf", "cover.jpg"]
    assert (fe.author, fe.meta.series, fe.meta.series_index) == ("Brandon Sanderson", "Mistborn", 1)
    assert fe.status is Status.CHECK  # all guessed from folder names

    dune = by_title["Dune"]
    assert [p.suffix for p in dune.files] == [".epub", ".pdf"] and dune.author == "Frank Herbert"

    plan = Planner(_folders(tmp_path)).plan(books)
    out_a, out_b = tmp_path / "out_audio" / "Audiobooks", tmp_path / "out_books"
    assert plan.targets[fe] == out_a / "Brandon Sanderson" / "Mistborn" / "1 - The Final Empire"
    assert {mv.dst for mv in plan.moves if mv.book is fe} >= {
        out_a / "Brandon Sanderson/Mistborn/1 - The Final Empire/CD1/01.mp3",
        out_a / "Brandon Sanderson/Mistborn/1 - The Final Empire/cover.jpg"}
    assert {mv.dst for mv in plan.moves if mv.book is dune} == {
        out_b / "EBooks/Frank Herbert/Dune/Dune.epub", out_b / "EBooks/Frank Herbert/Dune/Dune.pdf"}
    assert plan.targets[by_title["Project Hail Mary"]] == out_a / "Andy Weir" / "Project Hail Mary"

    # manga: no author folder -> Review; author folder -> "(Comic)" series until AniList says manga
    v1, v2 = by_title["Made in Abyss, Vol. 1"], by_title["Made in Abyss, Vol. 2"]
    assert v1 in plan.review and plan.targets[v1] == tmp_path / "review"
    assert plan.targets[v2] == out_b / "Comics" / "Akihito Tsukushi" / "Made in Abyss (Comic)" / "2 - Made in Abyss, Vol. 2"
    assert not plan.problems


def test_conflicts_and_protected(tmp_path):
    src = tmp_path / "in"
    _touch(src, "Frank Herbert/Dune.epub", "other/Frank Herbert - Dune.epub")
    folders = _folders(tmp_path)
    plan = Planner(folders).plan(load_books([src]))
    assert all(p[0].startswith("duplicate of") for p in plan.problems.values()) and len(plan.problems) == 2

    folders.books_sorted = tmp_path / "real" / "Books"
    plan = Planner(folders).plan(load_books([src]))
    assert any("Audiobookshelf library folder" in p for ps in plan.problems.values() for p in ps)


def test_copies_with_different_file_names_are_duplicates(tmp_path):
    src = tmp_path / "in"
    _touch(src, "Matt Dinniman/Dominion of Blades/1 - Dominion of Blades/a01.mp3",
           "Dominion of Blades/Matt Dinniman - Dominion of Blades/Part 01.mp3")
    books = load_books([src])
    for b in books:  # same metadata, as after an Audible match
        b.meta.authors, b.meta.title, b.meta.series, b.meta.series_index = (
            ["Matt Dinniman"], "Dominion of Blades", "Dominion of Blades", 1)
    plan = Planner(_folders(tmp_path)).plan(books)
    assert len(plan.duplicate_group(books[0])) == 2


def test_same_series_number_is_the_same_book(tmp_path):
    src = tmp_path / "in"
    _touch(src, "Broccoli Lion/The Great Cleric - Volume 03.epub", "x/Broccoli Lion - The Great Cleric, Volume 3.epub")
    folders = _folders(tmp_path)
    books = load_books([src])
    for b in books:
        b.meta.authors, b.meta.series, b.meta.series_index = ["Broccoli Lion"], "The Great Cleric", 3
    books[0].meta.title, books[1].meta.title = "The Great Cleric: Volume 03", "The Great Cleric: Volume 3"
    plan = Planner(folders).plan(books)
    assert len(plan.duplicate_group(books[0])) == 2
    # and against the library: volume 3 sorted earlier under another title spelling
    (folders.books_sorted / "EBooks/Broccoli Lion/The Great Cleric/3 - The Great Cleric - Volume 03").mkdir(parents=True)
    plan = Planner(folders).plan(books[1:])
    assert any("already in the library: 3 - The Great Cleric - Volume 03" in p for p in plan.problems[books[1]])


def test_book_already_in_library_with_other_file_names(tmp_path):
    src = tmp_path / "in"
    _touch(src, "Matt Dinniman/Dungeon Crawler Carl/01 - Dungeon Crawler Carl/track01.mp3")
    folders = _folders(tmp_path)
    existing = folders.audio_sorted / "Audiobooks/Matt Dinniman/Dungeon Crawler Carl/1 - Dungeon Crawler Carl"
    existing.mkdir(parents=True)
    (existing / "01 - Opening Credits.mp3").write_bytes(b"x")  # beets renamed its tracks
    plan = Planner(folders).plan(load_books([src]))
    assert any("already in the library" in p for ps in plan.problems.values() for p in ps)


def test_long_names_are_shortened_to_fit(tmp_path):
    src = tmp_path / "in"
    long_title = "7th Time Loop - The Villainess Enjoys a Carefree Life Married to Her Worst Enemy! " * 2
    _touch(src, f"Touko Amekawa/{long_title.strip()}/{'chapter ' * 6}01.mp3")
    folders = _folders(tmp_path)
    plan = Planner(folders).plan(load_books([src]))
    assert not plan.problems
    assert all(len(str(mv.dst)) <= 259 for mv in plan.moves)


def test_clean_name():
    assert clean_name("Title: Sub?") == "Title - Sub"
    assert clean_name("CON") == "CON_"
    assert clean_name("Dots...") == "Dots"


def test_identical_copy_already_in_library(tmp_path):
    src = tmp_path / "in"
    (src / "Eiichiro Oda").mkdir(parents=True)
    (src / "Eiichiro Oda/One Piece v01.cbz").write_bytes(b"x" * 100)
    folders = _folders(tmp_path)
    books = load_books([src])
    plan = Planner(folders).plan(books)
    target = plan.targets[books[0]]
    target.mkdir(parents=True)
    (target / "One Piece, Vol. 1.cbz").write_bytes(b"x" * 100)  # sorted before, same file
    plan = Planner(folders).plan(books)
    assert books[0] in plan.identical
    assert plan.problems[books[0]] == ["identical copy already in the library"]
    (target / "One Piece, Vol. 1.cbz").write_bytes(b"x" * 90)   # a different file: a real problem
    assert books[0] not in Planner(folders).plan(books).identical
