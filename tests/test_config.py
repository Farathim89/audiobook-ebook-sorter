from pathlib import Path

from app.config import Folders, load_folders, save_folders


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    f = Folders(audio_sorted=Path(r"X:\Audio"), audible_region="uk", extra_sources=[Path(r"X:\More")])
    save_folders(f, path)
    g = load_folders(path)
    assert (g.audio_sorted, g.audible_region, g.extra_sources) == (Path(r"X:\Audio"), "uk", [Path(r"X:\More")])
    assert g.protected == f.protected


def test_missing_or_broken_settings_give_defaults(tmp_path):
    assert load_folders(tmp_path / "nope.json") == Folders()
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_folders(tmp_path / "bad.json") == Folders()


def test_portable_folder_is_found_only_when_it_exists(tmp_path):
    from app.portable import DATA_FOLDER, portable_dir
    assert portable_dir(tmp_path) is None
    (tmp_path / DATA_FOLDER).mkdir()
    assert portable_dir(tmp_path) == tmp_path / DATA_FOLDER


def test_portable_paths_are_saved_relative_and_read_back(tmp_path, monkeypatch):
    import app.config as cfg
    data = tmp_path / "BookSorter-data"
    data.mkdir()
    monkeypatch.setattr(cfg, "PORTABLE", data)
    f = cfg.Folders(state=data / "state", audio_sorted=tmp_path / "Sorted", protected=[])
    cfg.save_folders(f, data / "settings.json")
    import json
    saved = json.loads((data / "settings.json").read_text(encoding="utf-8"))
    assert saved["state"] == "state" and saved["audio_sorted"] == str(tmp_path / "Sorted")
    assert cfg.load_folders(data / "settings.json").state == data / "state"


def test_version_is_set():
    import re
    from app import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_books_incoming_is_optional(tmp_path):
    import json
    from app.config import load_folders
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"audio_incoming": str(tmp_path / "in"), "books_incoming": ""}), encoding="utf-8")
    f = load_folders(p)
    assert f.books_incoming is None
    assert f.sources == [tmp_path / "in"]  # never '' (= the data folder in portable mode)


def test_one_sorted_folder_holds_audio_books_and_svenska(tmp_path):
    from app.config import Folders
    f = Folders(state=tmp_path, swedish_sorted=tmp_path / "old svenska")
    f.set_sorted_root(tmp_path / "-=Sorted=-")
    assert (f.audio_sorted.name, f.books_sorted.name, f.swedish_sorted.name) == ("AudioBooks", "Books", "Svenska")
    assert f.sorted_root == tmp_path / "-=Sorted=-"
    g = Folders(state=tmp_path, swedish_sorted=None)
    g.set_sorted_root(tmp_path / "S")
    assert g.swedish_sorted is None  # Swedish not apart: stays with the other audiobooks
