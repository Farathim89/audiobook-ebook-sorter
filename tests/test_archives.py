"""Book zips in Incoming are unpacked next to themselves; the zip goes to Done; nothing is overwritten."""
import zipfile

from app.core.archives import unpack_archives
from app.core.pipeline import load_books

LATER = 10**10  # 'now' far after the files were written: nothing is still downloading


def _zip(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def test_a_book_zip_is_unpacked_and_scanned(tmp_path):
    inc, done = tmp_path / "in", tmp_path / "done"
    z = inc / "Yoru" / "At Night, I Become a Monster by Yoru Sumino.zip"
    _zip(z, {"At Night, I Become a Monster.epub": b"x" * 10, "At Night, I Become a Monster.pdf": b"y"})
    lines = unpack_archives([inc], done, now=LATER)
    folder = inc / "Yoru" / "At Night, I Become a Monster by Yoru Sumino"
    assert (folder / "At Night, I Become a Monster.epub").read_bytes() == b"x" * 10
    assert not z.exists() and (done / "Unpacked zips" / z.name).exists() and "unpacked" in lines[0]
    assert any(b.main_file.suffix == ".epub" for b in load_books([inc]))


def test_other_zips_unsafe_paths_and_taken_names(tmp_path):
    inc, done = tmp_path / "in", tmp_path / "done"
    _zip(inc / "photos.zip", {"a.jpg": b"1"})                                  # no book: left alone
    _zip(inc / "evil.zip", {"../outside.epub": b"1", "ok.epub": b"2"})         # never outside its folder
    _zip(inc / "taken.zip", {"t.epub": b"1"})
    (inc / "taken").mkdir()
    _zip(inc / "fresh.zip", {"f.epub": b"1"})
    unpack_archives([inc], done, now=None)                                      # just written: maybe downloading
    assert (inc / "fresh.zip").exists() and not (inc / "fresh").exists()
    unpack_archives([inc], done, now=LATER)
    assert (inc / "fresh" / "f.epub").exists()
    assert (inc / "photos.zip").exists() and not (inc / "photos").exists()
    assert not (tmp_path / "outside.epub").exists() and (inc / "evil" / "ok.epub").exists()
    assert (inc / "taken.zip").exists() and not any((inc / "taken").iterdir())
