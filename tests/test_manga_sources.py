from pathlib import Path

from app.core.models import Book, BookType, Metadata
from app.core.sidecars import sidecar_files
from app.providers.fandom import Fandom, parse_wiki_list
from app.providers.mangadex import MangaDex

WIKITEXT = """{{Volume Box
|title       = ROMANCE DAWN - The Dawn of the Adventure
|chapters    = 1 - 8
|ename       = ''Romance Dawn''
|erelease    = June 30, 2003
|eisbn       = 978-1-56931-901-8
}}
'''Volume 1''' is titled ..."""


class FakeHttp:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.responder(url, params or {})


def test_fandom_reads_the_english_name_from_the_info_box():
    f = Fandom(FakeHttp(lambda url, p: {"parse": {"wikitext": WIKITEXT}}))
    info = f.volume("onepiece.fandom.com", 1)
    assert info == {"title": "Romance Dawn", "year": 2003, "isbn": "9781569319018"}
    assert f.http.calls[0][1]["page"] == "Volume 1"
    assert Fandom(FakeHttp(lambda u, p: {"error": {"code": "missingtitle"}})).volume("x.fandom.com", 9) is None


RENT = """{{Volume infobox
|title= Volume 1
|engname= Rent a Girlfriend (1)
|release= October 17, 2017 (JP)
June 2, 2020 (ENG)
|isbn= 978-4-06-510221-3 (JP)
978-1-63-236997-0 (ENG)
}}"""

SAO = """{{Warning}}
{{Volume Infobox
|Name = Sword Art Online the Movie -Ordinal Scale- 1
|Release Date = March 10, 2020<ref name = x>[https://x y]</ref>
|ISBN13 = ISBN 978-4-04-913106-2 (paperback)
|Release Date (NA) = June 18, 2024<ref name = y/>
|ISBN13 (NA) = ISBN 978-1-9753-6574-5 (paperback)<br />ISBN 978-1-9753-6575-2 (digital)
|Next = [[Volume 02|Volume 2]]}}"""


def test_fandom_takes_only_english_edition_data_and_no_fake_titles():
    rent = Fandom(FakeHttp(lambda u, p: {"parse": {"wikitext": RENT}})).volume("w", 1, series="Rent-A-Girlfriend")
    assert rent == {"title": None, "year": 2020, "isbn": "9781632369970"}
    sao = Fandom(FakeHttp(lambda u, p: {"parse": {"wikitext": SAO}})).volume(
        "w", 1, "X Volume {nn} (manga)", series="Sword Art Online - Ordinal Scale")
    assert sao == {"title": None, "year": 2024, "isbn": "9781975365745"}


def test_editions_on_one_line_split_by_br():
    text = ("{{Volume infobox\n|title= Volume 10\n|engname= Rent a Girlfriend (10)\n"
            "|release= May 17, 2019 (JP) <br> December 28, 2021 (EN)\n"
            "|isbn= 978-4-06-515139-6 (JP) <br> 978-1-64-651094-8 (EN)\n}}")
    info = Fandom(FakeHttp(lambda u, p: {"parse": {"wikitext": text}})).volume("w", 10, series="Rent-A-Girlfriend")
    assert info == {"title": None, "year": 2021, "isbn": "9781646510948"}
    jp_only = text.replace(" <br> December 28, 2021 (EN)", "").replace(" <br> 978-1-64-651094-8 (EN)", "")
    info = Fandom(FakeHttp(lambda u, p: {"parse": {"wikitext": jp_only}})).volume("w", 10, series="Rent-A-Girlfriend")
    assert info is None or info["isbn"] is None  # never the Japanese ISBN


def test_page_name_padding():
    from app.providers.fandom import page_name
    assert page_name("Made in Abyss Volume {nn}", 3) == "Made in Abyss Volume 03"
    assert page_name("Volume {n}", 12) == "Volume 12"


def test_wiki_list_parsing():
    wikis = parse_wiki_list("One Piece = https://onepiece.fandom.com/wiki/Main\nMade in Abyss = madeinabyss.fandom.com | Volume {n}\njunk")
    assert wikis["onepiece"] == ("onepiece.fandom.com", "Volume {n}")
    assert wikis["madeinabyss"] == ("madeinabyss.fandom.com", "Volume {n}")
    assert len(wikis) == 2


def test_mangadex_exact_title_and_volume_cover():
    def respond(url, p):
        if url.endswith("/manga"):
            return {"data": [{"id": "academy", "attributes": {"title": {"en": "One Piece Academy"}, "altTitles": []}},
                             {"id": "op", "attributes": {"title": {"ja-ro": "One Piece"}, "altTitles": [{"ja": "ワンピース"}]}}]}
        return {"data": [{"attributes": {"volume": "1", "locale": "ja", "fileName": "ja1.png"}},
                         {"attributes": {"volume": "1", "locale": "en", "fileName": "en1.jpg"}},
                         {"attributes": {"volume": "2", "locale": "ja", "fileName": "ja2.png"}}]}
    md = MangaDex(FakeHttp(respond))
    assert md.volume_cover("One Piece", 1) == "https://uploads.mangadex.org/covers/op/en1.jpg"  # English first
    assert md.volume_cover("One Piece", 2).endswith("/op/ja2.png")
    assert md.volume_cover("One Piece", 3) is None
    assert md.volume_cover("One Piece", 2, english_only=True) is None  # only the Japanese cover there


def test_mangadex_cover_beats_the_first_page(tmp_path):
    book = Book(BookType.COMIC, [tmp_path / "v01.cbz"], tmp_path, manga=True)
    book.meta = Metadata(title="One Piece, Vol. 1", cover_url="https://uploads.mangadex.org/covers/op/ja1.png")
    book.sources = {"cover_url": "mangadex"}
    files = sidecar_files(book, tmp_path / "out")
    assert files[tmp_path / "out" / "cover.png"] == "https://uploads.mangadex.org/covers/op/ja1.png"
