import json
import struct
import zipfile

from mutagen.id3 import ID3, TALB, TCOM, TDRC, TPE1, TXXX

from app.core.models import Book, BookType
from app.readers.audio import read_audiobook
from app.readers.comic import read_comic
from app.readers.ebook import read_epub, read_mobi
from app.readers.sidecar import read_sidecars

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>The Way of Kings</dc:title>
  <dc:creator id="c1">Brandon Sanderson</dc:creator>
  <meta refines="#c1" property="role">aut</meta>
  <dc:creator id="c2">Some Illustrator</dc:creator>
  <meta refines="#c2" property="role">ill</meta>
  <dc:identifier>urn:isbn:978-0-7653-2635-5</dc:identifier>
  <dc:date>2010-08-31</dc:date>
  <dc:description>&lt;p&gt;Roshar is a world &lt;b&gt;of stone&lt;/b&gt;.&lt;/p&gt;</dc:description>
  <meta property="belongs-to-collection" id="s1">The Stormlight Archive</meta>
  <meta refines="#s1" property="group-position">1</meta>
 </metadata>
</package>"""


def test_epub(tmp_path):
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", OPF)
    m = read_epub(p)
    assert m.title == "The Way of Kings"
    assert m.authors == ["Brandon Sanderson"]  # the illustrator is not an author
    assert (m.series, m.series_index, m.year, m.isbn) == ("The Stormlight Archive", 1, 2010, "9780765326355")
    assert m.description == "Roshar is a world of stone."


def test_broken_epub_returns_empty(tmp_path):
    p = tmp_path / "bad.epub"
    p.write_bytes(b"not a zip")
    assert read_epub(p).is_empty()


def _make_mobi(path, title, author):
    records = [(100, author.encode()), (104, b"9780765326355"), (106, b"2010-08-31")]
    body = b"".join(struct.pack(">II", t, len(v) + 8) + v for t, v in records)
    exth = b"EXTH" + struct.pack(">II", 12 + len(body), len(records)) + body
    mobi_len = 232
    name = title.encode()
    mobi = bytearray(mobi_len)
    mobi[0:4] = b"MOBI"
    struct.pack_into(">I", mobi, 4, mobi_len)
    struct.pack_into(">I", mobi, 12, 65001)
    struct.pack_into(">II", mobi, 68, 16 + mobi_len + len(exth), len(name))
    struct.pack_into(">I", mobi, 112, 0x40)
    rec0 = bytes(16) + bytes(mobi) + exth + name
    header = bytearray(78)
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, 1)
    path.write_bytes(bytes(header) + struct.pack(">II", 78 + 8 + 2, 0) + b"\0\0" + rec0)


def test_mobi(tmp_path):
    p = tmp_path / "book.azw3"
    _make_mobi(p, "Mistborn", "Brandon Sanderson")
    m = read_mobi(p)
    assert (m.title, m.authors, m.isbn, m.year) == ("Mistborn", ["Brandon Sanderson"], "9780765326355", 2010)


def test_comicinfo(tmp_path):
    p = tmp_path / "saga.cbz"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ComicInfo.xml", "<ComicInfo><Series>Saga</Series><Number>3</Number>"
                                    "<Writer>Brian K. Vaughan</Writer><Year>2012</Year></ComicInfo>")
        z.writestr("001.jpg", b"")
    m = read_comic(p)
    assert (m.series, m.series_index, m.authors, m.year) == ("Saga", 3, ["Brian K. Vaughan"], 2012)


def test_audio_id3(tmp_path):
    p = tmp_path / "01.mp3"
    p.write_bytes(b"\0" * 128)
    tags = ID3()
    tags.add(TALB(encoding=3, text="The Final Empire (Unabridged)"))
    tags.add(TPE1(encoding=3, text="Brandon Sanderson"))
    tags.add(TCOM(encoding=3, text="Michael Kramer"))
    tags.add(TXXX(encoding=3, desc="SERIES", text="Mistborn"))
    tags.add(TXXX(encoding=3, desc="SERIES-PART", text="1"))
    tags.add(TDRC(encoding=3, text="2006"))
    tags.save(p)
    p2 = tmp_path / "02.mp3"
    p2.write_bytes(p.read_bytes())
    m = read_audiobook([p, p2])
    assert m.title == "The Final Empire"
    assert m.authors == ["Brandon Sanderson"]
    assert m.narrators == ["Michael Kramer"]
    assert (m.series, m.series_index, m.year) == ("Mistborn", 1, 2006)


def test_abs_metadata_json(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({
        "title": "Dungeon Crawler Carl", "authors": ["Matt Dinniman"], "narrators": ["Jeff Hays"],
        "series": ["Dungeon Crawler Carl #1"], "publishedYear": "2020", "asin": "B08BX54DDS"}), encoding="utf-8")
    book = Book(BookType.AUDIO, [tmp_path / "a.m4b"], tmp_path.parent, folder=tmp_path)
    m = read_sidecars(book)
    assert (m.title, m.authors, m.narrators) == ("Dungeon Crawler Carl", ["Matt Dinniman"], ["Jeff Hays"])
    assert (m.series, m.series_index, m.asin) == ("Dungeon Crawler Carl", 1, "B08BX54DDS")


def test_daisy_ncc_gives_narrator_and_language(tmp_path):
    from app.readers.sidecar import read_daisy_ncc
    ncc = tmp_path / "ncc.html"
    ncc.write_text('<html><head><meta content="Ronja r&#246;vardotter" name="dc:title" />'
                   '<meta content="sv" name="dc:language" /><meta content="Astrid Lindgren" name="dc:creator" />'
                   '<meta content="Inga H&#229;kansson" name="ncc:narrator" /><meta content="2000-02-04" name="dc:date" />'
                   '</head></html>', encoding="utf-8")
    m = read_daisy_ncc(ncc)
    assert (m.title, m.authors, m.narrators, m.language, m.year) == (
        "Ronja rövardotter", ["Astrid Lindgren"], ["Inga Håkansson"], "sv", 2000)
