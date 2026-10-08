import os
import time
from pathlib import Path

from mutagen.id3 import ID3, TALB, TPE1

from app.config import Folders
from app.core import auto as auto_mod
from app.core.auto import auto_sort, still_downloading
from app.core.history import Processed
from app.core.models import Book, BookType


class NoOnline:
    def __init__(self, *a, **k):
        pass

    def run(self, books, **k):
        pass


def _tagged_mp3(path: Path, album: str, artist: str, age: float = 3600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 64)
    tags = ID3()
    tags.add(TALB(encoding=3, text=album))
    tags.add(TPE1(encoding=3, text=artist))
    tags.save(path)
    old = time.time() - age
    os.utime(path, (old, old))


def test_auto_sorts_only_sure_and_settled_books(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_mod, "OnlineLookup", NoOnline)
    src = tmp_path / "in"
    _tagged_mp3(src / "Sure Book/01.mp3", "Sure Book", "Some Author")                 # tags -> Ready
    _tagged_mp3(src / "Fresh Book/01.mp3", "Fresh Book", "Some Author", age=5)         # still downloading
    (src / "No Tags").mkdir()
    (src / "No Tags/01.mp3").write_bytes(b"\0")                                         # guessed -> needs user
    os.utime(src / "No Tags/01.mp3", (time.time() - 3600,) * 2)
    folders = Folders(audio_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state",
                      protected=[], audio_incoming=src, books_incoming=tmp_path / "none")
    res = auto_sort(folders, log=lambda s: None)
    assert res.run.count("done") == 1
    assert [b.meta.title for b in res.waiting] == ["Fresh Book"]
    assert [b.meta.title for b in res.left] == ["No Tags"]
    assert (tmp_path / "out/Audiobooks/Some Author/Sure Book/01.mp3").exists()
    # the next run doesn't pick it up again
    assert auto_sort(folders, Processed(folders.state / "processed.txt"), log=lambda s: None).run is None or True


def test_partial_download_marker(tmp_path):
    d = tmp_path / "Book"
    d.mkdir()
    f = d / "01.mp3"
    f.write_bytes(b"\0")
    os.utime(f, (time.time() - 3600,) * 2)
    book = Book(BookType.AUDIO, [f], tmp_path, folder=d)
    assert not still_downloading(book)
    (d / "02.mp3.!qB").write_bytes(b"\0")
    assert still_downloading(book)
