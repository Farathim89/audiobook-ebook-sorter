"""Fixes from the audit: lookups (cache without keys, bad answers, rules from a reorder, Author page counts)."""
import io
import json
import urllib.request

import pytest

from app.authors.works import Works, add_anilist, add_audible, add_yours, same_author
from app.core.abs_index import AbsBook
from app.core.models import Book, BookType, Metadata
from app.core.rules import Rules
from app.providers.http import Cache, Http


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_cache_never_holds_an_api_key_and_a_bad_answer_is_no_answer(monkeypatch, tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    http = Http(cache, min_interval=0)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(json.dumps({"ok": 1}).encode()))
    http.get_json("https://www.googleapis.com/books/v1/volumes", {"q": "x", "key": "SECRET"})
    rows = [r[0] for r in cache._db.execute("SELECT key FROM cache")]
    assert rows and not any("SECRET" in r for r in rows)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Resp(b"<html>busy</html>"))
    with pytest.raises(OSError):
        http.get_json("https://api.audible.com/1.0/catalog/products", {"title": "y"})


def test_reordering_co_authors_makes_no_rule(tmp_path):
    rules = Rules(tmp_path / "rules.json", [])
    b = Book(BookType.EBOOK, [tmp_path / "x.epub"], tmp_path)
    b.meta = Metadata(title="Slimes", authors=["Kisetsu Morita", "Yusuke Shiba"])
    assert rules.learn(Metadata(title="Slimes", authors=["Yusuke Shiba", "Kisetsu Morita"]), "", b) == []


def test_author_page_doubled_names_art_credits_and_two_series():
    assert same_author("zaifyr", ["zaifyr zaifyr"])
    works = Works()
    add_anilist(works, {"staffMedia": {"edges": [
        {"staffRole": "Art", "node": {"title": {"english": "Drawn Only"}, "format": "MANGA", "volumes": 3}},
        {"staffRole": "Original Story", "node": {"title": {"english": "Written"}, "format": "MANGA", "volumes": 2}}]}})
    assert [w.name for w in works.sorted()] == ["Written"]
    works = Works()
    add_audible(works, [{"asin": f"B{i}", "title": "Project Hail Mary", "language": "english",
                         "authors": [{"name": "Andy Weir"}]} for i in (1, 2)], "Andy Weir")
    add_yours(works, [AbsBook("1", "AudioBooks", "p", "Project Hail Mary", ["Andy Weir"], [], [], "", "audio", False)],
              "Andy Weir")
    phm = works.sorted()[0]
    assert (phm.online("audio"), phm.missing("audio")) == (1, 0)  # listed twice, one book
    works = Works()
    add_yours(works, [AbsBook("2", "AudioBooks", "p", "The Final Empire", ["Brandon Sanderson"], [],
                              [("Mistborn", 1.0), ("The Cosmere", 12.0)], "", "audio", False)], "Brandon Sanderson")
    mistborn = next(w for w in works.sorted() if w.name == "Mistborn")
    assert mistborn.mine["audio"] == {1.0}  # not Cosmere's 12 too
