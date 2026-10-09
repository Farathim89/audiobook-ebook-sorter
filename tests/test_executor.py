from pathlib import Path

from app.config import Folders
from app.core.executor import Executor
from app.core.history import Processed, list_runs, undo
from app.core.pipeline import load_books
from app.core.planner import Planner


def _setup(tmp_path: Path):
    src = tmp_path / "in"
    files = {
        "Brandon Sanderson/Mistborn/01 - The Final Empire/CD1/01.mp3": b"a" * 10,
        "Brandon Sanderson/Mistborn/01 - The Final Empire/cover.jpg": b"img",
        "Frank Herbert/Dune.epub": b"epub",
        "Loose Release (2019).cbz": b"cbz",
    }
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    folders = Folders(audio_sorted=tmp_path / "out_audio", books_sorted=tmp_path / "out_books",
                      review=tmp_path / "review", state=tmp_path / "state", protected=[])
    books = load_books([src])
    return src, folders, books


def _all_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}


def test_copy_then_undo(tmp_path):
    src, folders, books = _setup(tmp_path)
    before = _all_files(src)
    plan = Planner(folders).plan(books)
    result = Executor(folders).run(plan)
    assert (result.count("done"), result.count("review"), result.count("failed")) == (2, 1, 0)

    assert _all_files(src) == before  # copy mode: originals untouched
    assert _all_files(tmp_path / "out_audio") == {
        "Audiobooks/Brandon Sanderson/Mistborn/1 - The Final Empire/CD1/01.mp3",
        "Audiobooks/Brandon Sanderson/Mistborn/1 - The Final Empire/cover.jpg"}
    assert _all_files(tmp_path / "out_books") == {"EBooks/Frank Herbert/Dune/Dune.epub", "EBooks/Frank Herbert/Dune/metadata.opf"}
    opf = (tmp_path / "out_books/EBooks/Frank Herbert/Dune/metadata.opf").read_text(encoding="utf-8")
    assert '<dc:creator opf:role="aut">Frank Herbert</dc:creator>' in opf
    assert _all_files(tmp_path / "review") == {"Loose Release (2019).cbz", "Loose Release (2019).cbz.reason.txt"}
    assert not list(tmp_path.rglob("*.partial"))

    processed = Processed(folders.state / "processed.txt")
    assert all(processed.contains(b) for b in books)

    # a second run skips everything: targets exist now
    again = Executor(folders).run(Planner(folders).plan(books))
    assert again.count("done") == 0

    run = next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id)
    res = undo(run, processed)
    assert res.restored == 6 and not res.skipped  # mp3, cover, epub, opf, cbz, reason.txt
    assert not (tmp_path / "out_audio").exists() and not (tmp_path / "out_books").exists()
    assert not (tmp_path / "review").exists()
    assert not any(processed.contains(b) for b in books)
    assert _all_files(src) == before


def test_move_then_undo(tmp_path):
    src, folders, books = _setup(tmp_path)
    before = _all_files(src)
    result = Executor(folders, mode="move").run(Planner(folders).plan(books))
    assert result.count("failed") == 0
    assert "Frank Herbert/Dune.epub" not in _all_files(src)
    run = next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id)
    undo(run)
    assert _all_files(src) == before


def test_existing_target_is_never_overwritten(tmp_path):
    src, folders, books = _setup(tmp_path)
    target = tmp_path / "out_books/EBooks/Frank Herbert/Dune/Dune.epub"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"my own different file")
    plan = Planner(folders).plan(books)
    result = Executor(folders).run(plan)
    dune = next(r for r in result.results if r.book.meta.title == "Dune")
    assert dune.status == "skipped" and "already exists" in dune.detail
    assert target.read_bytes() == b"my own different file"


def test_xhe_original_goes_to_done_in_move_mode(tmp_path, monkeypatch):
    from app.core import m4b
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    original = src_dir / "Some Book.m4b"
    original.write_bytes(b"xhe audio")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=True)
    monkeypatch.setattr(m4b, "is_xhe_aac", lambda p: p.suffix == ".m4b" and b"xhe" in p.read_bytes())
    monkeypatch.setattr(m4b, "reencode_aac", lambda src, out, cancelled=None: out.write_bytes(b"plain aac"))
    books = load_books([tmp_path / "in"])
    result = Executor(folders, mode="move").run(Planner(folders).plan(books))
    assert result.count("done") == 1, result.results[0].detail
    assert not original.exists()                            # Move mode: the original left Incoming...
    kept = next((tmp_path / "done").rglob("Some Book.m4b"))
    assert kept.read_bytes() == b"xhe audio"                # ...and is kept in Done (nothing is deleted)
    sorted_file = next((tmp_path / "out").rglob("*.m4b"))
    assert sorted_file.read_bytes() == b"plain aac"         # the library gets the converted copy


def test_long_series_full_name_folder_short_book_folder_and_file(tmp_path):
    from app.core.models import Book, BookType, Metadata
    full = "Hell Mode: The Hardcore Gamer Dominates Another World with Garbage Balancing - Summoner of the Beginning"
    book = Book(BookType.COMIC, [tmp_path / "in/x.cbz"], tmp_path / "in",
                meta=Metadata(title=f"{full}, Vol. 1", series=full, series_index=1, authors=["Hamuo"]))
    book.manga = book.kind_known = True
    folders = Folders(books_sorted=tmp_path / "out", state=tmp_path / "state", protected=[])
    target = Planner(folders).plan([book]).targets[book]
    assert target.name == "1 - Hell Mode, Vol. 1"  # book folder (and file) short
    assert target.parent.name.startswith("Hell Mode - The Hardcore Gamer Dominates")  # series folder: full
    assert target.parent.name.endswith(" (Manga)")  # kept even when the name has to be shortened


def test_rechaptered_m4b_original_goes_to_done_in_move_mode(tmp_path, monkeypatch):
    from app.core import m4b
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    original = src_dir / "Some Book.m4b"
    original.write_bytes(b"old chapters")
    (src_dir / "cover.jpg").write_bytes(b"img")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=False, write_tags=False)
    monkeypatch.setattr(m4b, "duration", lambda p: 100.0)
    monkeypatch.setattr(m4b, "chapters_from_audible", lambda http, asin, total: [("Chapter 1", 0.0)])
    monkeypatch.setattr(m4b, "rechapter", lambda src, out, *a, **kw: out.write_bytes(b"new chapters"))
    monkeypatch.setattr(m4b, "add_cover", lambda out, image: None)
    books = load_books([tmp_path / "in"])
    books[0].meta.asin = "B000TEST01"
    result = Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    assert result.count("done") == 1, result.results[0].detail
    assert not (tmp_path / "in" / "Some Author").exists()   # Incoming is empty
    assert next((tmp_path / "out").rglob("*.m4b")).read_bytes() == b"new chapters"
    assert next((tmp_path / "done").rglob("Some Book.m4b")).read_bytes() == b"old chapters"


def test_progress_says_what_is_happening(tmp_path):
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    (src_dir / "Some Book.m4b").write_bytes(b"audio")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=False, write_tags=False)
    steps: list[str] = []
    books = load_books([tmp_path / "in"])
    Executor(folders, mode="move", on_detail=steps.append).run(Planner(folders).plan(books))
    assert any(s.startswith("Some Book - moving Some Book.m4b (") for s in steps), steps
    assert "Some Book - moving the originals' leftovers to Done" in steps


def _one_m4b_book(tmp_path, monkeypatch, codec="copy"):
    from app.core import m4b
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    (src_dir / "Some Book.m4b").write_bytes(b"original")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=False, write_tags=False, m4b_codec=codec)
    calls: list[str] = []
    monkeypatch.setattr(m4b, "duration", lambda p: 100.0)
    monkeypatch.setattr(m4b, "is_xhe_aac", lambda p: False)
    monkeypatch.setattr(m4b, "chapters_from_audible", lambda http, asin, total: [("Chapter 1", 0.0)])
    monkeypatch.setattr(m4b, "rechapter", lambda src, out, *a, **kw: (calls.append("copy"), out.write_bytes(b"new")))
    monkeypatch.setattr(m4b, "encode_m4b", lambda src, out, *a, **kw: (calls.append("aac"), out.write_bytes(b"aac")))
    monkeypatch.setattr(m4b, "add_cover", lambda out, image: None)
    books = load_books([tmp_path / "in"])
    books[0].meta.asin = "B000TEST01"
    return folders, books, calls


def test_mp3_to_m4b_leaves_a_single_m4b_alone(tmp_path, monkeypatch):
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    Executor(folders, to_m4b=True, redo_m4b=False).run(Planner(folders).plan(books))
    assert calls == [] and next((tmp_path / "out").rglob("*.m4b")).read_bytes() == b"original"


def test_m4b_to_m4b_with_aac_codec_reencodes(tmp_path, monkeypatch):
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch, codec="aac")
    Executor(folders, redo_m4b=True).run(Planner(folders).plan(books))
    assert calls == ["aac"] and next((tmp_path / "out").rglob("*.m4b")).read_bytes() == b"aac"


def test_keep_done_off_deletes_the_old_copy_and_undo_keeps_the_new_one(tmp_path, monkeypatch):
    import app.core.executor as ex
    from app.core.history import list_runs, undo
    monkeypatch.setattr(ex, "recycle_bin_on", lambda p: False)  # like drive H: - deleted for good
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    folders.keep_done = False
    original = books[0].main_file
    result = Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    assert result.count("done") == 1
    assert not original.exists() and not (tmp_path / "done").exists()      # nothing kept in Done
    sorted_file = next((tmp_path / "out").rglob("*.m4b"))
    res = undo(next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id))
    assert sorted_file.exists()                                            # the only copy left is kept
    assert any("deleted for good" in s for s in res.skipped)


def test_keep_done_off_uses_the_recycle_bin_when_there_is_one(tmp_path, monkeypatch):
    import app.core.executor as ex
    binned: list = []
    monkeypatch.setattr(ex, "recycle_bin_on", lambda p: True)
    monkeypatch.setattr(ex, "to_recycle_bin", lambda p: (binned.append(p.name), p.unlink()))
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    folders.keep_done = False
    Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    assert binned == ["Some Book.m4b"]


def test_chapters_are_good():
    from app.core.m4b import Chapter, chapters_are_good
    def ch(*titles, step=600.0):
        return [Chapter(i * step, (i + 1) * step, t) for i, t in enumerate(titles)]
    assert chapters_are_good(ch("Opening Credits", "Prologue", "The Bound Demon", "Black Summoner", "Epilogue"))
    assert not chapters_are_good(ch("Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"))
    assert not chapters_are_good(ch("Track 01", "Track 02", "03"))
    assert not chapters_are_good(ch("Whole book"))                        # one chapter is no chapters
    audible = ch("Chapter 1", "Chapter 2", "Chapter 3")
    same = [Chapter(c.start + 2, c.end + 2, c.title) for c in audible]
    assert chapters_are_good(same, audible)                               # already Audible's: nothing to gain


def test_m4b_to_m4b_leaves_good_chapters_alone(tmp_path, monkeypatch):
    from app.core import m4b
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    monkeypatch.setattr(m4b, "chapters_in", lambda p: [m4b.Chapter(0, 50, "Prologue"), m4b.Chapter(50, 100, "The Gate")])
    Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    assert calls == [] and next((tmp_path / "out").rglob("*.m4b")).read_bytes() == b"original"
    assert not (tmp_path / "done").exists()                               # just moved: no old copy


def test_a_new_m4b_that_fails_the_play_test_is_dropped_and_the_original_sorted(tmp_path, monkeypatch):
    from app.core import m4b
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    monkeypatch.setattr(m4b, "chapters_in", lambda p: [])
    monkeypatch.setattr(m4b, "play_test", lambda path, *a, **kw: "decode error" if path.read_bytes() == b"new" else "")
    result = Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    r = result.results[0]
    assert r.status == "done" and "failed the play test" in r.detail
    assert next((tmp_path / "out").rglob("*.m4b")).read_bytes() == b"original"   # the original, as it is
    assert not (tmp_path / "done").exists()                                       # nothing replaced


def test_play_test_finds_a_cut_off_file(tmp_path):
    import pytest as _pytest
    from app.core import m4b
    if not m4b.ffmpeg() or not m4b.find_tool("ffprobe"):
        _pytest.skip("no ffmpeg")
    import subprocess
    good = tmp_path / "good.m4b"
    subprocess.run([m4b.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=120",
                    "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(good)], check=True)
    assert m4b.play_test(good, 120) == "" and m4b.play_test(good, full=True) == ""
    assert "long instead of" in m4b.play_test(good, 300)
    cut = tmp_path / "cut.m4b"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    assert m4b.play_test(cut) and m4b.play_test(cut, full=True)


test_play_test_finds_a_cut_off_file = __import__("pytest").mark.real_play_test(test_play_test_finds_a_cut_off_file)


def test_play_test_library_lists_the_broken_files(tmp_path, monkeypatch):
    from app.core import m4b
    from app.core.library import audio_files_under, play_test_library
    for rel in ("A/1/a.m4b", "A/2/b.mp3", "A/2/cover.jpg", "B/c.m4b"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    files = audio_files_under([tmp_path / "A", tmp_path / "B", tmp_path / "missing"])
    assert [f.name for f in files] == ["a.m4b", "b.mp3", "c.m4b"]
    monkeypatch.setattr(m4b, "play_test", lambda f, **kw: "audio stops early" if f.name == "b.mp3" else "")
    tested, bad = play_test_library(files)
    assert tested == 3 and [(f.name, why) for f, why in bad] == [("b.mp3", "audio stops early")]


def test_old_copy_is_only_removed_after_a_full_play_test(tmp_path, monkeypatch):
    import app.core.executor as ex
    removed: list = []
    monkeypatch.setattr(ex, "recycle_bin_on", lambda p: True)
    monkeypatch.setattr(ex, "to_recycle_bin", lambda p: (removed.append(p.name), p.unlink()))
    folders, books, calls = _one_m4b_book(tmp_path, monkeypatch)
    folders.keep_done, folders.play_test = False, "quick"
    result = Executor(folders, mode="move", redo_m4b=True).run(Planner(folders).plan(books))
    assert removed == []                                                     # quick only: not trusted enough
    assert next((tmp_path / "done").rglob("Some Book.m4b")).read_bytes() == b"original"
    assert "old copy kept in Done" in result.results[0].detail


def test_info_log_says_what_was_read_and_decided(tmp_path):
    import json
    from app.core.infolog import read_info
    src_dir = tmp_path / "in" / "Some Author" / "Some Book"
    src_dir.mkdir(parents=True)
    (src_dir / "Some Book.m4b").write_bytes(b"audio")
    (src_dir / "metadata.json").write_text(json.dumps({"title": "Some Book", "authors": ["Some Author"]}),
                                           encoding="utf-8")
    (src_dir / "book info.txt").write_text("By: Some Author\nNarrated by: A Reader\n", encoding="utf-8")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=False, write_tags=False)
    books = load_books([tmp_path / "in"])
    result = Executor(folders, mode="move").run(Planner(folders).plan(books))
    (entry,) = read_info(folders.state, result.run_id)
    assert entry["read"]["metadata files"]["authors"] == ["Some Author"]
    assert entry["read"]["book info.txt"]["narrators"] == ["A Reader"]
    assert entry["decided"]["values"]["title"] == "Some Book" and entry["decided"]["from"]["title"] == "sidecar"
    assert Path(entry["decided"]["target"]).parts[-2:] == ("Some Author", "Some Book") and entry["result"] == "done"


def test_play_test_ignores_a_broken_cover_picture(monkeypatch):
    import subprocess
    from app.core import m4b
    monkeypatch.setattr(m4b, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(m4b, "find_tool", lambda name: name)

    def run(cmd, **kw):
        if "ffprobe" in cmd[0]:
            return subprocess.CompletedProcess(cmd, 0, '{"streams": [{"codec_name": "aac"}], "format": {"duration": "100"}}', "")
        return subprocess.CompletedProcess(cmd, 0, "out_time_us=20000000\n",
                                           "[mjpeg @ 0000019e] dqt: invalid precision\n[mjpeg @ 0000019e] bits 198 is invalid\n")
    monkeypatch.setattr(m4b.subprocess, "run", run)
    assert m4b.play_test(Path("x.m4b"), 100) == ""


test_play_test_ignores_a_broken_cover_picture = __import__("pytest").mark.real_play_test(
    test_play_test_ignores_a_broken_cover_picture)


def test_several_books_at_once_keep_order_history_and_undo(tmp_path):
    from app.core.history import list_runs, undo
    from app.core.models import Status
    src = tmp_path / "in"
    for n in range(1, 13):
        d = src / "Some Author" / "Long Series" / f"Long Series, Vol. {n:02d}"
        d.mkdir(parents=True)
        (d / f"Long Series, Vol. {n:02d}.m4b").write_bytes(b"audio" * n)
        (d / "cover.jpg").write_bytes(b"img")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      done=tmp_path / "done", protected=[], fix_xhe_aac=False, write_tags=False, workers=6)
    books = load_books([src])
    for b in books:
        b.status = Status.READY
    ex = Executor(folders, mode="move")
    assert ex.workers == 6
    result = ex.run(Planner(folders).plan(books), books)
    assert result.count("done") == 12 and [r.book for r in result.results] == books   # the order given
    assert len(list((tmp_path / "out").rglob("*.m4b"))) == 12 and not any(p.is_file() for p in src.rglob("*"))
    undo(next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id))
    assert len(list(src.rglob("*.m4b"))) == 12 and not any((tmp_path / "out").rglob("*.m4b"))


def test_audiobook_and_ebook_in_one_folder_never_lose_files(tmp_path, monkeypatch):
    """Mushoku Tensei Vol. 14: the m4b and the epub side by side, sorted at once with Done off - the
    ebook must not take the audiobook's cover/metadata for its own leftovers."""
    import app.core.executor as ex
    monkeypatch.setattr(ex, "recycle_bin_on", lambda p: False)
    from app.core.models import Status
    src = tmp_path / "in"
    for n in range(1, 7):
        d = src / "Series" / f"Volume {n:02d}"
        d.mkdir(parents=True)
        (d / f"Series, Vol. {n:02d}.m4b").write_bytes(b"audio" * n)
        (d / f"Series - Volume {n:02d}.epub").write_bytes(b"epub" * n)
        for extra in ("book info.txt", "metadata.json", "cover-adb.jpg", "cover-ln.jpg"):
            (d / extra).write_bytes(extra.encode())
    folders = Folders(audio_sorted=tmp_path / "out", books_sorted=tmp_path / "books", review=tmp_path / "rev",
                      state=tmp_path / "state", done=tmp_path / "done", protected=[], fix_xhe_aac=False,
                      write_tags=False, workers=8, keep_done=False)
    books = load_books([src])
    for b in books:
        b.meta.authors, b.status = ["Someone"], Status.READY
    result = Executor(folders, mode="move").run(Planner(folders).plan(books), books)
    assert result.count("done") == len(books), [r.detail for r in result.results if r.status != "done"]
    for name in ("book info.txt", "cover-adb.jpg"):                       # the audiobook kept its files
        assert len(list((tmp_path / "out").rglob(name))) == 6


def test_downloaded_metadata_json_is_corrected_and_undo_puts_it_back(tmp_path):
    import json
    for mode in ("copy", "move"):
        base = tmp_path / mode
        src = base / "in"
        d = src / "Saint" / "Volume 01"
        d.mkdir(parents=True)
        (d / "book.mp3").write_bytes(b"a" * 10)
        wrong = {"title": "The Saint's Magic Power is Omnipotent, Vol. 01", "authors": ["Veronica Taylor"],
                 "narrators": ["Yuka Tachibana"], "series": ["The Saint's Magic Power is Omnipotent #01"],
                 "chapters": [{"id": 0, "start": 0, "end": 5, "title": "Prologue"}]}
        original = json.dumps(wrong).encode("utf-8")
        (d / "metadata.json").write_bytes(original)
        (d / "book info.txt").write_text("By: Yuka Tachibana\nNarrated by: Veronica Taylor\n", encoding="utf-8")
        folders = Folders(audio_sorted=base / "out", books_sorted=base / "outb", review=base / "rev",
                          state=base / "state", protected=[])
        books = load_books([src])
        result = Executor(folders, mode=mode).run(Planner(folders).plan(books))
        assert result.count("done") == 1, result.results
        target = next((base / "out").rglob("metadata.json"))
        fixed = json.loads(target.read_text(encoding="utf-8"))
        assert fixed["authors"] == ["Yuka Tachibana"] and fixed["narrators"] == ["Veronica Taylor"]
        assert fixed["series"] == ["The Saint's Magic Power is Omnipotent #1"] and fixed["chapters"] == wrong["chapters"]
        run = next(r for r in list_runs(folders.state / "history") if r.run_id == result.run_id)
        res = undo(run)
        assert not res.skipped, res.skipped
        assert (d / "metadata.json").read_bytes() == original  # exactly the uploader's file again
        assert not target.exists()
