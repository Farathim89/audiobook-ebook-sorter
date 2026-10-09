import copy
import json
from pathlib import Path

from app.core.ai import AiHelper, check_answer
from app.core.metadata import edit_book, update_status
from app.core.models import Book, BookType, Metadata, Status
from app.core.rules import Rules, book_kind


def _book(tmp_path: Path, name: str = "b", btype: BookType = BookType.AUDIO, **meta) -> Book:
    d = tmp_path / "in" / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / ("book.m4b" if btype is BookType.AUDIO else "book.epub")
    f.write_bytes(b"x")
    book = Book(btype, [f], tmp_path / "in", folder=d, meta=Metadata(**meta))
    for k in meta:
        book.sources[k] = "sidecar"
    update_status(book)
    return book


def _edit(rules: Rules, book: Book, run_id: str = "", **values) -> list:
    before, kind = copy.deepcopy(book.meta), book_kind(book)
    light_novel = values.pop("light_novel", None)
    edit_book(book, **values)
    if light_novel is not None:
        book.light_novel = light_novel
    return rules.learn(before, kind, book, run_id=run_id)


def test_an_edit_becomes_a_rule_for_the_next_sort(tmp_path):
    rules = Rules.load(tmp_path / "state")
    edited = _book(tmp_path, "v3", title="Loner Life, Vol. 3", authors=["Goji Shoji"],
                   series="Loner Life in Another World", series_index=3)
    learned = _edit(rules, edited, authors=["Shoji Goji"], light_novel=True)
    assert [r.kind for r in learned] == ["author", "kind"]
    again = Rules.load(tmp_path / "state")  # saved
    new = _book(tmp_path, "v4", title="Loner Life, Vol. 4", authors=["Goji Shoji"],
                series="Loner Life in Another World", series_index=4)
    assert again.apply([new]) == 1
    assert new.meta.authors == ["Shoji Goji"] and new.light_novel and new.sources["authors"] == "rule"
    assert any(n.startswith("rule from your edits") for n in new.notes)


def test_rules_never_override_your_own_edit_and_undo_forgets_them(tmp_path):
    rules = Rules.load(tmp_path / "state")
    _edit(rules, _book(tmp_path, "a", title="X", authors=["Twoony Twoony"]), run_id="run-1", authors=["Twoony"])
    mine = _book(tmp_path, "b", title="Y", authors=["Twoony Twoony"])
    mine.sources["authors"] = "manual"  # set by hand
    assert rules.apply([mine]) == 0
    assert rules.forget_run("run-1") == 1 and not Rules.load(tmp_path / "state").items


def test_series_narrator_fills_only_an_empty_one(tmp_path):
    rules = Rules.load(tmp_path / "state")
    _edit(rules, _book(tmp_path, "a", title="ORV, Vol. 1", authors=["singNsong"], series="ORV", series_index=1),
          narrators=["Sam Slade"])
    empty = _book(tmp_path, "b", title="ORV, Vol. 2", authors=["singNsong"], series="ORV", series_index=2)
    other = _book(tmp_path, "c", title="ORV, Vol. 3", authors=["singNsong"], series="ORV", series_index=3,
                  narrators=["Someone"])
    rules.apply([empty, other])
    assert empty.meta.narrators == ["Sam Slade"] and other.meta.narrators == ["Someone"]


class FakeClient:
    name, model = "Fake", "fake"

    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def ask(self, system, user):
        self.calls += 1
        assert "Nancy Wu" in user  # the evidence is sent
        return dict(self.answer), 0.0


def _unsure(tmp_path) -> Book:
    book = _book(tmp_path, "lackey", title="Reborn as the Villain's Lackey, Vol. 1", authors=["Nancy Wu"],
                 narrators=["Sarah Roberts"], series="Reborn as the Villain's Lackey", series_index=1)
    (book.folder / "book info.txt").write_text("By: Twoony\nNarrated by: Nancy Wu\n", encoding="utf-8")
    book.doubt = "Audible lists this title by Twoony - check the author"
    update_status(book)
    assert book.status is Status.CHECK
    return book


ANSWER = {"authors": ["Twoony"], "narrators": ["Nancy Wu"], "series": "Reborn as the Villain's Lackey",
          "series_index": 1, "title": "Reborn as the Villain's Lackey, Vol. 1", "kind": "audiobook",
          "confidence": 0.95, "reason": "book info.txt names Twoony as the writer"}


def test_a_sure_ai_answer_is_used_and_cached(tmp_path):
    book = _unsure(tmp_path)
    client = FakeClient(ANSWER)
    ai = AiHelper(client, tmp_path / "state")
    assert ai.run([book]) == 1
    assert (book.meta.authors, book.meta.narrators) == (["Twoony"], ["Nancy Wu"])
    assert book.sources["authors"] == "ai" and book.status is Status.READY and not book.doubt
    again = _unsure(tmp_path)
    AiHelper(client, tmp_path / "state").run([again])
    assert client.calls == 1  # the second time from the cache
    assert json.loads((tmp_path / "state" / "ai_usage.json").read_text())["calls"] == 1


def test_an_unsure_ai_answer_waits_and_an_invented_author_is_refused(tmp_path):
    book = _unsure(tmp_path)
    AiHelper(FakeClient({**ANSWER, "confidence": 0.5}), tmp_path / "s1").run([book])
    assert book.meta.authors == ["Nancy Wu"] and book.ai["authors"] == ["Twoony"]
    assert any(n.startswith("🤖 AI suggests") for n in book.notes)
    book = _unsure(tmp_path)
    AiHelper(FakeClient({**ANSWER, "authors": ["Made Up Person"]}), tmp_path / "s2").run([book])
    assert book.meta.authors == ["Nancy Wu"] and "no source" in book.ai["rejected"]


def test_ai_kind_must_suit_the_file(tmp_path):
    book = _book(tmp_path, "x", title="T", authors=["A"])
    assert check_answer(book, {**ANSWER, "authors": ["A"], "kind": "manga"}, "A")["kind"] == "audiobook"
    assert check_answer(book, {**ANSWER, "authors": ["A"], "kind": "light novel"}, "A")["kind"] == "light novel"


def test_ready_and_hand_edited_books_are_not_sent(tmp_path):
    ready = _book(tmp_path, "r", title="T", authors=["A"])
    assert ready.status is Status.READY and not AiHelper.wanted(ready)
    mine = _unsure(tmp_path)
    mine.sources["authors"] = "manual"
    assert not AiHelper.wanted(mine)


def test_publisher_list_decides_the_kind(tmp_path):
    from app.core.publishers import Publisher, Publishers
    pubs = Publishers.load(tmp_path / "state")
    assert pubs.match("Seven Seas Entertainment, Seven Seas Siren").kind == "light novel"
    audio = _book(tmp_path, "a", title="Solo Leveling, Vol. 1", authors=["Chugong"])
    (audio.folder / "book info.txt").write_text("By: Chugong\nPublisher: Yen Audio\n", encoding="utf-8")
    other = _book(tmp_path, "b", title="Treasure Island", authors=["Robert Louis Stevenson"], publisher="Recorded Books")
    assert pubs.apply([audio, other]) == 1
    assert audio.light_novel and not other.light_novel
    pubs.items.append(Publisher("Recorded Books", "normal"))
    pubs.save()
    other.light_novel = True
    assert Publishers.load(tmp_path / "state").apply([other]) == 1 and not other.light_novel


def test_doubled_pen_name_only_with_the_single_form_nearby(tmp_path):
    from app.core.online import _doubled_pen_names
    v2 = _book(tmp_path, "a", title="Lackey, Vol. 2", authors=["Twoony Twoony"])
    v1 = _book(tmp_path, "b", title="Lackey, Vol. 1", authors=["Twoony"])
    eighty_six = _book(tmp_path, "c", title="86, Vol. 1", authors=["Asato Asato"])
    _doubled_pen_names([v1, v2, eighty_six])
    assert v2.meta.authors == ["Twoony"] and eighty_six.meta.authors == ["Asato Asato"]


def test_ai_never_moves_a_book_out_of_light_novels(tmp_path):
    from app.core.ai import use_answer
    book = _book(tmp_path, "sao", BookType.EBOOK, title="Sword Art Online, Vol. 11", authors=["Reki Kawahara"])
    book.light_novel = True
    use_answer(book, {**ANSWER, "authors": ["Reki Kawahara"], "title": book.meta.title, "kind": "ebook"})
    assert book.light_novel


def test_ai_does_not_see_the_readers_of_another_recording():
    from types import SimpleNamespace
    from app.core.ai import _same_recording
    inglis_file = 19.2 * 3600
    assert not _same_recording(SimpleNamespace(length=22.63 * 3600), inglis_file)  # Andy Serkis' Fellowship
    assert _same_recording(SimpleNamespace(length=19.1 * 3600), inglis_file)
    assert _same_recording(SimpleNamespace(length=None), inglis_file)
