import zipfile
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TIT2

from app.config import Folders
from app.core.covers import extract_cover
from app.core.executor import Executor
from app.core.history import list_runs, undo
from app.core.models import Book, BookType, Metadata
from app.core.planner import Planner
from app.core.tagger import write_tags
from app.readers.audio import read_tags
from app.readers.ebook import read_epub

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 20


def _epub(path: Path, with_cover=True):
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Dune</dc:title>'
           '<dc:creator>Frank Herbert</dc:creator><meta name="cover" content="cov"/></metadata>'
           '<manifest><item id="cov" href="images/c.png" media-type="image/png"/></manifest></package>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                                             '</rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        if with_cover:
            z.writestr("OEBPS/images/c.png", PNG)


def _mp3(path: Path, cover: bytes | None = None):
    path.write_bytes(b"\0" * 64)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Chapter 1"))
    tags.add(TALB(encoding=3, text="old album"))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=cover))
    tags.save(path)


def test_epub_cover_and_series_tag(tmp_path):
    p = tmp_path / "Dune.epub"
    _epub(p)
    book = Book(BookType.EBOOK, [p], tmp_path)
    book.meta = Metadata(title="Dune", authors=["Frank Herbert"], series="Dune Chronicles", series_index=1)
    assert extract_cover(book) == (PNG, ".png")
    assert write_tags(p, book)
    with zipfile.ZipFile(p) as z:
        assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
    m = read_epub(p)
    assert (m.series, m.series_index, m.title) == ("Dune Chronicles", 1, "Dune")
    write_tags(p, book)  # running twice doesn't duplicate the meta
    with zipfile.ZipFile(p) as z:
        assert z.read("OEBPS/content.opf").decode().count("calibre:series\"") == 1


def test_comic_first_page_is_cover(tmp_path):
    p = tmp_path / "Saga 001.cbz"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("page10.jpg", PNG)
        z.writestr("page2.jpg", JPG)
    assert extract_cover(Book(BookType.COMIC, [p], tmp_path)) == (JPG, ".jpg")


def test_mp3_tags_keep_track_titles(tmp_path):
    files = [tmp_path / "01.mp3", tmp_path / "02.mp3"]
    for f in files:
        _mp3(f, JPG)
    book = Book(BookType.AUDIO, files, tmp_path)
    book.meta = Metadata(title="The Final Empire", authors=["Brandon Sanderson"], narrators=["Michael Kramer"],
                         series="Mistborn", series_index=1, asin="B002UZMLXM", year=2006)
    assert extract_cover(book) == (JPG, ".jpg")
    assert write_tags(files[0], book)
    read_tags.cache_clear()
    t = read_tags(files[0])
    assert t["title"] == "Chapter 1"  # per-track title untouched
    assert (t["album"], t["albumartist"], t["artist"]) == ("The Final Empire", "Brandon Sanderson", "Brandon Sanderson")
    assert (t["composer"], t["narrator"], t["series"], t["series_part"], t["asin"]) == (
        "Michael Kramer", "Michael Kramer", "Mistborn", "1", "B002UZMLXM")


def test_sort_writes_tags_and_cover_then_undo(tmp_path):
    src = tmp_path / "in" / "Frank Herbert"
    src.mkdir(parents=True)
    _epub(src / "Dune.epub")
    original = (src / "Dune.epub").read_bytes()
    folders = Folders(books_sorted=tmp_path / "out", review=tmp_path / "rev", state=tmp_path / "state", protected=[])
    from app.core.pipeline import load_books
    books = load_books([tmp_path / "in"])
    books[0].meta.series, books[0].meta.series_index = "Dune Chronicles", 1
    result = Executor(folders).run(Planner(folders).plan(books))
    assert result.count("done") == 1
    out = tmp_path / "out/EBooks/Frank Herbert/Dune Chronicles/1 - Dune"
    assert {p.name for p in out.iterdir()} == {"Dune.epub", "metadata.opf", "cover.png"}
    assert read_epub(out / "Dune.epub").series == "Dune Chronicles"
    assert (src / "Dune.epub").read_bytes() == original  # the original is never touched in copy mode

    res = undo(list_runs(folders.state / "history")[0])
    assert not res.skipped and not (tmp_path / "out").exists()


def test_cover_next_to_a_cbz_beats_a_japanese_mangadex_cover(tmp_path):
    from app.core.models import Book, BookType
    from app.core.sidecars import sidecar_files
    vol = tmp_path / "in/Daisuke Aizawa/Volume 01"
    vol.mkdir(parents=True)
    cbz = vol / "x v01.cbz"
    cbz.write_bytes(b"cbz")
    (vol / "cover.jpg").write_bytes(b"english cover")
    book = Book(BookType.COMIC, [cbz], tmp_path / "in")
    book.meta.title = "X, Vol. 1"
    book.meta.cover_url = "https://uploads.mangadex.org/covers/x/ja1.jpg"
    book.sources["cover_url"] = "mangadex-ja"
    out = sidecar_files(book, tmp_path / "out")
    assert out[tmp_path / "out/cover.jpg"] == b"english cover"


def test_a_thumbnail_cover_jpg_loses_to_the_full_size_first_page(tmp_path, monkeypatch):
    import app.core.sidecars as sc
    from app.core.models import Book, BookType
    vol = tmp_path / "in/A/Volume 01"
    vol.mkdir(parents=True)
    cbz = vol / "x v01.cbz"
    cbz.write_bytes(b"cbz")
    (vol / "cover.jpg").write_bytes(b"t" * 100)  # a 128x192 thumbnail
    monkeypatch.setattr(sc, "extract_cover", lambda book: (b"\xff\xd8\xff" + b"p" * 5000, ".jpg"))
    book = Book(BookType.COMIC, [cbz], tmp_path / "in")
    book.meta.title = "X, Vol. 1"
    assert len(sc.sidecar_files(book, tmp_path / "out")[tmp_path / "out/cover.jpg"]) == 5003


def _jpeg(w, h):
    import struct
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"JF" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, h, w) + b"\x00" * 8


def test_image_size_reads_jpeg_and_png_headers():
    import struct
    from app.core.covers import image_size
    assert image_size(_jpeg(900, 1350)) == (900, 1350)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1200, 1800)
    assert image_size(png) == (1200, 1800)
    assert image_size(b"not an image") is None


def test_small_cover_gets_a_bigger_one_by_isbn_and_a_note(tmp_path, monkeypatch):
    import app.core.sidecars as sc
    from app.config import Folders
    from app.core.models import Book, BookType, Metadata
    from app.core.online import OnlineLookup
    lk = OnlineLookup(Folders(state=tmp_path / "state", protected=[]))
    monkeypatch.setattr(sc, "best_local_cover", lambda book: _jpeg(128, 192))
    sizes = {"https://covers.openlibrary.org/b/isbn/9781975325183-L.jpg?default=false": _jpeg(333, 500)}
    monkeypatch.setattr(sc, "download", lambda url: sizes[url])
    monkeypatch.setattr(lk.covers_http, "get_json", lambda url, params=None: {})
    book = Book(BookType.COMIC, [tmp_path / "x.cbz"], tmp_path, meta=Metadata(title="X, Vol. 1", isbn="978-1-9753-2518-3"))
    lk.check_cover(book)
    assert book.sources["cover_url"] == "bigger-cover" and book.notes == ["low-res cover (333x500)"]
    big = Book(BookType.COMIC, [tmp_path / "y.cbz"], tmp_path, meta=Metadata(title="Y, Vol. 1"))
    monkeypatch.setattr(sc, "best_local_cover", lambda book: _jpeg(900, 1350))
    lk.check_cover(big)
    assert not big.notes and "cover_url" not in big.sources


def test_audible_2400_beats_small_embedded_art(tmp_path, monkeypatch):
    import app.core.sidecars as sc
    from app.core.models import Book, BookType
    book = Book(BookType.AUDIO, [tmp_path / "a.m4b"], tmp_path)
    book.meta.cover_url, book.sources["cover_url"] = "https://m.media-amazon.com/x._SL2400_.jpg", "audible"
    monkeypatch.setattr(sc, "extract_cover", lambda b: (_jpeg(500, 500), ".jpg"))
    assert sc.choose_cover(book).url.endswith("_SL2400_.jpg")
    monkeypatch.setattr(sc, "extract_cover", lambda b: (_jpeg(1400, 1400), ".jpg"))
    assert sc.choose_cover(book).source == "inside the book"


def test_improve_covers_of_sorted_books_and_undo(tmp_path, monkeypatch):
    import app.core.library as lib
    from app.config import Folders
    from app.core.history import list_runs, undo
    f = Folders(books_sorted=tmp_path / "books", state=tmp_path / "state", protected=[])
    d = f.books_sorted / "Light Novels/Doufu Mayoi/Black Summoner/1 - Black Summoner, Vol. 1"
    d.mkdir(parents=True)
    (d / "Black Summoner, Vol. 1.epub").write_bytes(b"epub")
    (d / "cover.jpg").write_bytes(_jpeg(400, 564))
    monkeypatch.setattr("app.core.covers.extract_cover", lambda book: (_jpeg(1400, 1990), ".jpg"))
    ups = lib.plan_cover_upgrades(f, None)
    assert [(u.before, u.why) for u in ups] == [("400x564", "1400x1990 from inside the book")]
    run_id, n = lib.apply_cover_upgrades(f, ups)
    assert n == 1 and (d / "cover.jpg").read_bytes() == _jpeg(1400, 1990)
    undo(next(r for r in list_runs(f.state / "history") if r.run_id == run_id))
    assert (d / "cover.jpg").read_bytes() == _jpeg(400, 564)


def test_embed_cover_only_when_bigger(tmp_path):
    import shutil
    from mutagen.mp4 import MP4
    from app.core import m4b
    import subprocess
    import pytest
    from app.core.tools import find_tool
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    f = tmp_path / "a.m4b"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "1", "-c:a", "aac",
                    "-f", "mp4", str(f)], check=True)
    m4b.add_cover(f, _jpeg(500, 500))
    assert m4b.embed_cover_if_bigger(f, _jpeg(2400, 2400))
    assert not m4b.embed_cover_if_bigger(f, _jpeg(1000, 1000))
    assert bytes(MP4(f).tags["covr"][0]) == _jpeg(2400, 2400)
