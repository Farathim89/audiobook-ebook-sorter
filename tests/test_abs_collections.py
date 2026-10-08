from app.core.abs_collections import apply_collection_sort, plan_collection_sort, reading_order


def _book(id_, title, series=None, seq=None):
    meta = {"title": title, "series": [{"name": series, "sequence": seq}] if series else []}
    return {"id": id_, "media": {"metadata": meta}}


COLLECTION = {"id": "c1", "name": "One Piece", "books": [
    _book("w", "Wanted! Eiichiro Oda Before One Piece"),
    _book("a2", "Ace 2", "One Piece - Ace's Story - The Manga (Manga)", "2"),
    _book("v10", "One Piece, Vol. 10", "One Piece (Manga)", "10"),
    _book("v2", "One Piece, Vol. 2", "One Piece (Manga)", "2"),
    _book("a1", "Ace 1", "One Piece - Ace's Story - The Manga (Manga)", "1"),
    _book("v1", "One Piece, Vol. 1", "One Piece (Manga)", "1"),
]}


class FakeClient:
    def __init__(self):
        self.patched = []

    def collections(self):
        return [COLLECTION]

    def set_collection_order(self, cid, ids):
        self.patched.append((cid, ids))


def test_reading_order_main_series_then_spin_offs_then_stand_alone():
    assert reading_order(COLLECTION) == ["v1", "v2", "v10", "a1", "a2", "w"]  # 10 after 2: numbers, not text


def test_plan_and_apply_only_reorder():
    client = FakeClient()
    changes = plan_collection_sort(client)
    assert [c.name for c in changes] == ["One Piece"] and changes[0].first == ["One Piece, Vol. 1", "One Piece, Vol. 2", "One Piece, Vol. 10"]
    assert apply_collection_sort(client, changes) == 1
    assert sorted(client.patched[0][1]) == sorted(b["id"] for b in COLLECTION["books"])  # same books
