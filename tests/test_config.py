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
