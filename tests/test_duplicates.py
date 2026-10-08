from pathlib import Path

from app.config import Folders
from app.core.duplicates import best_copy, describe
from app.core.pipeline import load_books
from app.core.planner import Planner


def test_duplicate_group_and_best_copy(tmp_path):
    src = tmp_path / "in"
    for rel, size in (("Frank Herbert/Dune.epub", 10), ("other/Frank Herbert - Dune.epub", 500),
                      ("third/Frank Herbert - Dune.epub", 50)):
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    books = load_books([src])
    plan = Planner(Folders(books_sorted=tmp_path / "out", protected=[])).plan(books)
    group = plan.duplicate_group(books[0])
    assert len(group) == 3
    best = best_copy([describe(b) for b in group])
    assert best.size == 500 and "epub" in best.summary

    # keeping one and skipping the rest clears the clash
    rest = [b for b in books if b is not best.book]
    plan = Planner(Folders(books_sorted=tmp_path / "out", protected=[])).plan([best.book])
    assert not plan.problems and rest
