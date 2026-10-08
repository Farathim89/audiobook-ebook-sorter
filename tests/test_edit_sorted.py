from pathlib import Path

from mutagen.id3 import ID3, TALB, TPE1, TPE2

from app.config import Folders
from app.core.history import list_runs, undo
from app.core.library import edit_sorted_book, load_sorted_book
from app.readers.audio import read_tags
from app.readers.opf import parse_opf

OPF = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><metadata '
       'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Earthsea Cycle Book 5</dc:title>'
       '<dc:creator>Ursula K. Le Guin</dc:creator><dc:description>Dragons.</dc:description>'
       '<meta name="calibre:series" content="Earthsea Cycle"/><meta name="calibre:series_index" content="5"/>'
       '</metadata></package>')


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def test_fix_author_of_a_sorted_audiobook_and_undo(tmp_path):
    f = Folders(audio_sorted=tmp_path / "audio", books_sorted=tmp_path / "books", state=tmp_path / "state",
                protected=[])
    old = f.audio_sorted / "Audiobooks/Sarah Skaer/Lonely Castle in the Mirror"
    old.mkdir(parents=True)
    mp3 = old / "Lonely Castle in the Mirror.mp3"
    mp3.write_bytes(b"\0" * 64)
    tags = ID3()
    tags.add(TALB(encoding=3, text="Lonely Castle in the Mirror"))
    tags.add(TPE1(encoding=3, text="Sarah Skaer"))
    tags.add(TPE2(encoding=3, text="Sarah Skaer"))
    tags.save(mp3)
    (old / "metadata.json").write_text("{}", encoding="utf-8")  # ABS's own copy
    before = _files(tmp_path / "audio")

    book = load_sorted_book(f, old)
    assert book.author == "Sarah Skaer"
    run_id, new = edit_sorted_book(f, old, {"authors": ["Mizuki Tsujimura"], "narrators": ["Sarah Skaer"]})
    assert new == f.audio_sorted / "Audiobooks/Mizuki Tsujimura/Lonely Castle in the Mirror"
    assert _files(tmp_path / "audio") == ["Audiobooks/Mizuki Tsujimura/Lonely Castle in the Mirror/Lonely Castle in the Mirror.mp3",
                                          "Audiobooks/Mizuki Tsujimura/Lonely Castle in the Mirror/reader.txt"]
    read_tags.cache_clear()
    t = read_tags(new / "Lonely Castle in the Mirror.mp3")
    assert (t["albumartist"], t["narrator"]) == ("Mizuki Tsujimura", "Sarah Skaer")

    undo(next(r for r in list_runs(f.state / "history") if r.run_id == run_id))
    assert _files(tmp_path / "audio") == before


def test_rename_and_renumber_a_sorted_ebook(tmp_path):
    f = Folders(books_sorted=tmp_path / "books", state=tmp_path / "state", protected=[])
    old = f.books_sorted / "EBooks/Ursula K. Le Guin/Earthsea Cycle/5 - Earthsea Cycle Book 5"
    old.mkdir(parents=True)
    (old / "Earthsea Cycle Book 5.epub").write_bytes(b"not a real epub")
    (old / "metadata.opf").write_text(OPF, encoding="utf-8")
    run_id, new = edit_sorted_book(f, old, {"title": "The Other Wind", "series": "The Earthsea Quartet",
                                            "series_index": 6.0})
    assert new == f.books_sorted / "EBooks/Ursula K. Le Guin/The Earthsea Quartet/6 - The Other Wind"
    m = parse_opf((new / "metadata.opf").read_bytes())
    assert (m.title, m.series, m.series_index, m.description) == ("The Other Wind", "The Earthsea Quartet", 6, "Dragons.")
    assert not (f.books_sorted / "EBooks/Ursula K. Le Guin/Earthsea Cycle").exists()  # empty series folder gone


def test_clearing_the_series_removes_series_tags_and_ignores_abs_json(tmp_path):
    from mutagen.id3 import MVNM, TXXX
    f = Folders(audio_sorted=tmp_path / "audio", state=tmp_path / "state", protected=[])
    old = f.audio_sorted / "Audiobooks/Mizuki Tsujimura/Sarah Skaer/Lonely Castle in the Mirror"
    old.mkdir(parents=True)
    mp3 = old / "Lonely Castle in the Mirror.mp3"
    mp3.write_bytes(b"\0" * 64)
    tags = ID3()
    tags.add(TALB(encoding=3, text="Lonely Castle in the Mirror"))
    tags.add(TPE2(encoding=3, text="Mizuki Tsujimura"))
    tags.add(MVNM(encoding=3, text="Sarah Skaer"))
    tags.add(TXXX(encoding=3, desc="SERIES", text="Sarah Skaer"))
    tags.save(mp3)
    # ABS guessed this from the kind folder: 'Audiobooks' as author, the author folder as series
    (old / "metadata.json").write_text('{"authors": ["Audiobooks"], "series": ["Mizuki Tsujimura"]}', encoding="utf-8")
    book = load_sorted_book(f, old)
    assert book.author == "Mizuki Tsujimura"
    _, new = edit_sorted_book(f, old, {"series": ""})
    assert new == f.audio_sorted / "Audiobooks/Mizuki Tsujimura/Lonely Castle in the Mirror"
    left = ID3(new / "Lonely Castle in the Mirror.mp3")
    assert not left.getall("MVNM") and not left.getall("TXXX:SERIES")
    assert not (f.audio_sorted / "Audiobooks/Mizuki Tsujimura/Sarah Skaer").exists()


def test_case_only_rename_of_a_sorted_light_novel_and_undo(tmp_path):
    from app.core.text import clean_title
    assert clean_title("Black Summoner, Vol. 1 (light Novel)") == "Black Summoner, Vol. 1 (Light Novel)"
    f = Folders(books_sorted=tmp_path / "books", state=tmp_path / "state", protected=[])
    old = f.books_sorted / "Light Novels/Doufu Mayoi/Black Summoner/1 - Black Summoner, Vol. 1 (light Novel)"
    old.mkdir(parents=True)
    (old / "Black Summoner, Vol. 1 (light Novel).epub").write_bytes(b"not a real epub")
    (old / "metadata.opf").write_text(OPF.replace("Earthsea Cycle Book 5", "Black Summoner, Vol. 1 (light Novel)")
                                      .replace("Earthsea Cycle", "Black Summoner").replace("Ursula K. Le Guin", "Doufu Mayoi")
                                      .replace('content="5"', 'content="1"'),
                                      encoding="utf-8")
    before = _files(tmp_path / "books")
    run_id, new = edit_sorted_book(f, old, {"title": "Black Summoner, Vol. 1 (Light Novel)"})
    assert _files(tmp_path / "books") == [
        "Light Novels/Doufu Mayoi/Black Summoner/1 - Black Summoner, Vol. 1 (Light Novel)/Black Summoner, Vol. 1 (Light Novel).epub",
        "Light Novels/Doufu Mayoi/Black Summoner/1 - Black Summoner, Vol. 1 (Light Novel)/metadata.opf"]
    undo(next(r for r in list_runs(f.state / "history") if r.run_id == run_id))
    assert _files(tmp_path / "books") == before
