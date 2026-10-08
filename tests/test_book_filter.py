from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.config import Folders
from app.core.models import Book, BookType, Metadata
from app.core.planner import Plan
from app.ui.book_model import BookFilter, BookModel

app = QApplication.instance() or QApplication([])


def _shown(proxy: BookFilter) -> list[str]:
    model = proxy.sourceModel()
    return [model.books[r].meta.title for r in range(len(model.books)) if proxy.filterAcceptsRow(r, None)]


def test_sorted_books_only_show_under_sorted(tmp_path):
    new = Book(BookType.EBOOK, [tmp_path / "a.epub"], tmp_path, meta=Metadata(title="New", authors=["A"]))
    done = Book(BookType.EBOOK, [tmp_path / "b.epub"], tmp_path, meta=Metadata(title="Done", authors=["A"]))
    model = BookModel(Folders(state=tmp_path / "state"))
    model.set_books([new, done], Plan(), {done})
    proxy = BookFilter(model)
    assert _shown(proxy) == ["New"]                      # All: no sorted books
    proxy.set_filter(state="sorted")
    assert _shown(proxy) == ["Done"]
    proxy.set_filter(state="all")
    assert _shown(proxy) == ["New"]                      # back to All: hidden again
