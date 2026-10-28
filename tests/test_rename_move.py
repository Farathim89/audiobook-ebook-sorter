"""Book Sorter -> Shelfkeeper: the old data folder is renamed on first start and the paths saved in it follow."""
import json

import app.portable as portable


def test_old_data_folder_is_renamed_and_its_saved_paths_follow(tmp_path, monkeypatch):
    monkeypatch.setattr(portable, "_MOVE_IN_TESTS", True)
    old = tmp_path / "BookSorter-data"
    (old / "state" / "history").mkdir(parents=True)
    parked = old / "state" / "replaced" / "r1" / "contents" / "00001.json"
    entry = {"op": "content", "dst": r"H:\Lib\Book\metadata.json", "saved": str(parked)}
    (old / "state" / "history" / "r1.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    (old / "settings.json").write_text(json.dumps({"state": "state", "abs_url": "x"}), encoding="utf-8")

    found = portable.portable_dir(tmp_path)

    new = tmp_path / "Shelfkeeper-data"
    assert found == new and new.is_dir() and not old.exists()
    saved = json.loads((new / "state" / "history" / "r1.jsonl").read_text(encoding="utf-8"))["saved"]
    assert saved == str(new / "state" / "replaced" / "r1" / "contents" / "00001.json")
    assert json.loads((new / "settings.json").read_text(encoding="utf-8"))["state"] == "state"


def test_nothing_moves_when_the_new_folder_is_already_there(tmp_path, monkeypatch):
    monkeypatch.setattr(portable, "_MOVE_IN_TESTS", True)
    (tmp_path / "BookSorter-data").mkdir()
    (tmp_path / "Shelfkeeper-data").mkdir()
    assert portable.portable_dir(tmp_path) == tmp_path / "Shelfkeeper-data"
    assert (tmp_path / "BookSorter-data").is_dir()  # left as it is


def test_tests_never_move_real_folders(tmp_path):
    (tmp_path / "BookSorter-data").mkdir()
    assert portable.portable_dir(tmp_path) == tmp_path / "BookSorter-data"  # _MOVE_IN_TESTS off
