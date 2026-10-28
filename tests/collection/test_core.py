from pathlib import Path

from app.collection.core.collect import _first_series, _owned, _people, group
from app.collection.core.models import AUDIO, EBOOK, Owned, Series, Volume, series_key


def test_series_name_from_abs():
    assert _first_series("Solo Leveling #2, Yen Audio #5") == ("Solo Leveling", 2.0)
    assert _first_series("Overlord") == ("Overlord", None)
    assert _people("Fuse, Mitz Vah - illustrator") == ["Fuse"]


def test_folder_layout(tmp_path):
    root = tmp_path / "Books"
    o = _owned(root, root / "Light Novels" / "Kugane Maruyama" / "Overlord" / "3 - Overlord, Vol. 3" / "x.epub", EBOOK, "Folder")
    assert (o.author, o.series, o.index, o.kind) == ("Kugane Maruyama", "Overlord", 3.0, "light novel")
    m = _owned(root, root / "Manga" / "Carlo Zen" / "The Saga of Tanya the Evil (Manga)" / "28 - Vol. 28.cbz", EBOOK, "Folder")
    assert (m.series, m.index, m.kind) == ("The Saga of Tanya the Evil", 28.0, "manga")


def test_audio_and_ebook_meet_manga_stays_apart():
    a = Owned("Overlord, Vol. 1", ["Kugane Maruyama"], "Overlord", 1, AUDIO, "light novel", "ABS")
    e = Owned("Overlord, Vol. 2", ["Kugane Maruyama"], "Overlord (Light Novel)", 2, EBOOK, "light novel", "ABS")
    m = Owned("Overlord, Vol. 1", ["Kugane Maruyama"], "Overlord", 1, EBOOK, "manga", "ABS")
    series, _ = group([a, e, m])
    assert set(series) == {series_key("Overlord"), series_key("Overlord", manga=True)}


def test_missing_only_in_formats_you_collect_and_not_upcoming():
    s = Series("k", "SAO", "Reki Kawahara", "light novel")
    s.owned = [Owned(f"v{i}", [], "SAO", i, AUDIO, "light novel", "ABS") for i in (1, 2, 4)]
    s.audible = {float(i): Volume(float(i), release="2020-01-01") for i in range(1, 5)}
    s.audible[5.0] = Volume(5.0, release="2999-01-01")
    assert [v.index for v in s.missing(AUDIO)] == [3.0]
    assert s.missing(EBOOK) == [] and [v.index for v in s.upcoming()] == [5.0]


def test_one_series_written_two_ways():
    a = Owned("v1", ["Miku"], "I Got a Cheat Skill in Another World and Became Unrivaled in the Real World", 1, EBOOK,
              "light novel", "ABS")
    b = Owned("v2", ["Miku"], "I Got a Cheat Skill in Another World and Became Unrivaled in The Real World, Too", 2,
              AUDIO, "light novel", "ABS")
    series, _ = group([a, b])
    assert len(series) == 1 and len(next(iter(series.values())).owned) == 2


def test_same_asin_is_the_same_volume_whatever_its_number():
    s = Series("k", "Witch and Mercenary", "Chohokiteki Kaeru", "light novel")
    s.owned = [Owned("Vol. 6", [], "Witch and Mercenary", 6.1, AUDIO, "light novel", "ABS", asin="B0HF9KN24S")]
    s.audible = {6.0: Volume(6.0, "Witch and Mercenary, Vol. 6: Part 1", "2026-08-14", "B0HF9KN24S")}
    assert s.have(AUDIO) == {6.0} and not s.missing(AUDIO)
