import json

from app.config import Folders
from app.core import abs_progress
from app.core.abs_progress import apply_restore, backup_progress, plan_restore


class FakeAbs:
    url = "http://abs"

    def __init__(self):
        self.libs = [{"id": "L1", "name": "AudioBooks"}]
        self.items = {"L1": [
            {"id": "old", "path": "H:/a", "media": {"duration": 100, "metadata": {"title": "Overlord, Vol. 1",
                                                                                  "asin": "B1", "authorName": "Kugane Maruyama"}}}]}
        self.progress = [{"libraryItemId": "old", "isFinished": True, "progress": 1, "currentTime": 100, "lastUpdate": 5}]
        self.written = {}

    def me(self):
        return {"id": "u", "username": "farathim", "mediaProgress": self.progress, "bookmarks": []}

    def libraries(self):
        return self.libs

    def library_items(self, lib_id):
        return self.items[lib_id]

    def set_progress(self, item_id, body):
        self.written[item_id] = body


def test_backup_then_restore_into_a_new_library(tmp_path, monkeypatch):
    monkeypatch.setattr(abs_progress, "PORTABLE", None)
    folders = Folders(state=tmp_path)
    abs_ = FakeAbs()
    backup, n = backup_progress(folders, abs_)
    assert n == 1 and json.loads(backup.read_text(encoding="utf-8"))["progress"][0]["item"]["asin"] == "B1"
    # the library is split: the book is a new item, the old progress is gone
    abs_.libs = [{"id": "L2", "name": "Audiobooks - Light Novels"}]
    abs_.items = {"L2": [
        {"id": "new", "media": {"duration": 100, "metadata": {"title": "Overlord, Vol. 1", "asin": "B1",
                                                             "authors": [{"name": "Kugane Maruyama"}]}}},
        {"id": "slime4", "media": {"duration": 50, "metadata": {"title": "Slime, Vol. 4", "authorName": "Fuse"}}}]}
    abs_.progress = []
    steps = plan_restore(abs_, backup)
    assert [(s.action, s.how, s.item_id) for s in steps] == [("set", "ASIN", "new")]
    before, done = apply_restore(folders, abs_, steps)
    assert done == 1 and abs_.written["new"]["isFinished"] is True and before.name.startswith("abs-progress-before-restore-")
    abs_.progress = [{"libraryItemId": "new", "isFinished": True, "currentTime": 100, "lastUpdate": 9}]
    assert [s.action for s in plan_restore(abs_, backup)] == ["keep"]       # never twice / never over newer
