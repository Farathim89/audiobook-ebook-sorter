import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TIT2, TPE1
from mutagen.mp4 import MP4

from app.config import Folders
from app.core import m4b
from app.core.executor import Executor
from app.core.history import list_runs, undo
from app.core.pipeline import load_books
from app.core.planner import Planner

needs_ffmpeg = pytest.mark.skipif(not m4b.ffmpeg(), reason="ffmpeg not installed")


@pytest.mark.parametrize("names, expected", [
    (["Carl's Doomsday Scenario (01)", "Carl's Doomsday Scenario (02)"], ["Chapter 1", "Chapter 2"]),
    (["01 - Opening Credits", "02 - The Beginning", "03 - 03"], ["Opening Credits", "The Beginning", "Chapter 3"]),
    (["Track 01", "Track 02"], ["Chapter 1", "Chapter 2"]),
    (["Dune - Book One - Prologue", "Dune - Book One - Chapter 1"], ["Prologue", "Chapter 1"]),
    (["Intro", "Intro"], ["Intro", "Intro"]),  # never cut a whole name away
])
def test_tidy_chapter_names(names, expected):
    assert m4b.tidy_chapter_names(names) == expected


def test_ffmetadata_escapes():
    text = m4b.ffmetadata([m4b.Chapter(0, 1.5, "A=B; #1")], "T")
    assert "title=A\\=B\\; \\#1" in text and "END=1500" in text


def _tone(path: Path, seconds: float, title: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([m4b.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-b:a", "64k", str(path)], check=True)
    tags = ID3(path)
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TALB(encoding=3, text="Tone Book"))
    tags.add(TPE1(encoding=3, text="Test Author"))
    tags.save(path)


@needs_ffmpeg
def test_mp3s_become_one_chaptered_m4b_and_undo(tmp_path):
    src = tmp_path / "in"
    _tone(src / "Tone Book/01.mp3", 2, "Tone Book (01)")
    _tone(src / "Tone Book/02.mp3", 3, "Tone Book (02)")
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state", protected=[])
    books = load_books([src])
    result = Executor(folders, to_m4b=True).run(Planner(folders).plan(books))
    assert result.count("done") == 1, result.results[0].detail
    out = tmp_path / "out/Audiobooks/Test Author/Tone Book/Tone Book.m4b"
    assert {p.name for p in out.parent.iterdir()} == {"Tone Book.m4b"}  # no mp3s copied
    assert m4b.read_chapters(out) == ["Chapter 1", "Chapter 2"]
    f = MP4(out)
    assert round(f.info.length) == 5
    assert f.tags["©nam"] == ["Tone Book"] and f.tags["aART"] == ["Test Author"]
    assert (src / "Tone Book/01.mp3").exists()  # originals kept

    res = undo(list_runs(folders.state / "history")[0])
    assert not res.skipped and not (tmp_path / "out").exists()
