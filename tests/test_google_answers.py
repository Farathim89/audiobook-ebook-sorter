"""Google Books: a 503 now and then is asked again, plain searches (intitle: / inauthor: find nothing now)."""
import io
import json
import urllib.error
import urllib.request

import pytest

from app.collection.config import Settings
from app.collection.core.lookup import Lookup
from app.collection.core.models import EBOOK, Owned, Series, Volume
from app.providers.googlebooks import GoogleBooks
from app.providers import http as http_mod
from app.providers.http import Cache, Http


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_503_is_asked_again_and_a_wrong_ask_is_not(monkeypatch, tmp_path):
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)
    answers = [503, 503, {"items": [1]}]

    def urlopen(req, timeout):
        a = answers.pop(0)
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "Service Unavailable", None, None)
        return _Resp(json.dumps(a).encode())
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    h = Http(Cache(tmp_path / "c.sqlite"), min_interval=0)
    assert h.get_json("https://www.googleapis.com/books/v1/volumes", {"q": "x"}) == {"items": [1]}
    answers[:] = [400, {"items": [2]}]
    with pytest.raises(urllib.error.HTTPError):
        h.get_json("https://www.googleapis.com/books/v1/volumes", {"q": "y"})
    answers[:] = [503, 503, 503]
    with pytest.raises(OSError):
        h.get_json("https://www.googleapis.com/books/v1/volumes", {"q": "z"})


def test_the_sorter_asks_google_in_plain_words():
    asked = []

    class Fake:
        def get_json(self, url, params=None):
            asked.append(params["q"])
            return {}
    GoogleBooks(Fake()).search("Accel World", "Reki Kawahara")
    assert asked == ["Accel World Reki Kawahara"]


def test_google_not_answering_keeps_last_times_volumes(tmp_path):
    settings = Settings()
    settings.use_audible = settings.use_anilist = False
    lk = Lookup(settings, tmp_path)
    s = Series("b", "Black Summoner", "Doufu Mayoi", "light novel")
    s.owned = [Owned("Black Summoner, Vol. 1", ["Doufu Mayoi"], "Black Summoner", 1.0, EBOOK, "light novel", "Folder")]
    s.others = {2.0: Volume(2.0, "Black Summoner: Volume 2", source="google")}

    def down(_s):
        raise OSError("HTTP Error 503: Service Unavailable")
    lk.google_volumes = down
    lk.check(s)
    assert list(s.others) == [2.0] and "Google" in s.links.get("errors", "")


def test_the_collection_also_asks_with_the_kind_and_one_failed_search_is_not_all(tmp_path):
    asked = []

    class Fake:
        def get_json(self, url, params):
            asked.append(params["q"])
            if len(asked) == 1:
                raise OSError("HTTP Error 503: Service Unavailable")
            return {"items": [{"volumeInfo": {"title": "Goblin Slayer! Light Novel 14"}},
                              {"volumeInfo": {"title": "Goblin Slayer, Vol. 2 (light novel)"}},
                              {"volumeInfo": {"title": "Goblin Slayer, Chapter 8 (manga)"}}]}
    lk = Lookup(Settings(), tmp_path)
    lk.google = Fake()
    s = Series("g", "Goblin Slayer", "Kumo Kagyu", "light novel")
    lk.google_volumes(s)
    assert asked == ["Goblin Slayer Kumo Kagyu", "Goblin Slayer light novel"] and sorted(s.others) == [2.0, 14.0]


def test_the_authors_page_keeps_english_editions_only():
    from app.authors.sources import AuthorLookup
    from app.authors.works import Works

    class Fake:
        def get_json(self, url, params):
            return {"items": [{"id": "1", "volumeInfo": {"title": "One Piece, Vol. 7", "authors": ["Eiichiro Oda"],
                                                          "language": "en"}},
                              {"id": "2", "volumeInfo": {"title": "One Piece - Édition originale", "authors":
                                                          ["Eiichiro Oda"], "language": "fr"}}]}
    lk = AuthorLookup.__new__(AuthorLookup)
    lk.books_http, lk.google_key = Fake(), ""
    works = Works()
    lk._google(works, "Eiichiro Oda")
    assert [w.name for w in works.sorted()] == ["One Piece"]


def test_the_collection_skips_other_languages_and_bundles(tmp_path):
    class Fake:
        def get_json(self, url, params):
            return {"items": [{"volumeInfo": {"title": "86--EIGHTY-SIX, Vol. 2 (light novel)", "language": "en"}},
                              {"volumeInfo": {"title": "86-EIGHTY-SIX Light Novel 12-Book Set Vol.1-12"}},
                              {"volumeInfo": {"title": "86 - Eighty Six, Vol. 3", "language": "fr"}}]}
    lk = Lookup(Settings(), tmp_path)
    lk.google = Fake()
    s = Series("e", "86--EIGHTY-SIX", "Asato Asato", "light novel")
    lk.google_volumes(s)
    assert sorted(s.others) == [2.0]


def test_a_save_during_a_check_sees_the_old_answers_and_a_partial_google_keeps_them(tmp_path):
    settings = Settings()
    settings.use_audible = settings.use_anilist = False
    lk = Lookup(settings, tmp_path)
    s = Series("b", "Black Summoner", "Doufu Mayoi", "light novel")
    s.owned = [Owned("Black Summoner, Vol. 1", ["Doufu Mayoi"], "Black Summoner", 1.0, EBOOK, "light novel", "Folder")]
    s.others = {2.0: Volume(2.0, "Black Summoner: Volume 2", source="google")}
    seen = []

    class Fake:
        def get_json(self, url, params):
            seen.append(sorted(s.others))  # what a save right now would write
            if len(seen) == 1:
                raise OSError("HTTP Error 503: Service Unavailable")
            return {"items": [{"volumeInfo": {"title": "Black Summoner, Vol. 5"}}]}
    lk.google = Fake()
    lk.check(s)
    assert seen == [[2.0], [2.0]] and sorted(s.others) == [2.0, 5.0]

    class Busy:
        def get_json(self, url, params):
            raise OSError("HTTP Error 429: Too Many Requests")
    lk.google = Busy()
    lk.check(s)
    assert lk.google_busy and sorted(s.others) == [2.0, 5.0]
