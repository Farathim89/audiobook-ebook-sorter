"""The book table: one row per book, with a tick box for 'sort this one'."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from ..config import Folders
from ..core.models import Book, BookType
from ..core.planner import Plan
from ..core.text import natural_key
from .theme import state_color, state_icon

SORT_ROLE = Qt.UserRole + 1
COLUMNS = ["", "Status", "Type", "Author", "Series", "#", "Title", "Narrator", "Year", "Goes to"]
COL_CHECK, COL_STATUS, COL_TYPE, COL_AUTHOR, COL_SERIES, COL_INDEX, COL_TITLE, COL_NARR, COL_YEAR, COL_TARGET = range(10)
TYPE_LABELS = {BookType.AUDIO: "🎧 Audio", BookType.EBOOK: "📖 Ebook", BookType.COMIC: "💬 Comic"}


def type_label(book: Book) -> str:
    if book.type is BookType.COMIC and book.manga:
        return "💬 Manga"
    if book.type is BookType.COMIC and not book.kind_known:
        return "❔ Manga / comic?"  # Look up online (AniList) decides
    if book.light_novel:
        return "🎧 Light novel" if book.type is BookType.AUDIO else "📘 Light novel"
    return TYPE_LABELS[book.type]


def state_of(book: Book, plan: Plan | None, done: set[Book], skipped: set[Book] = frozenset()) -> str:
    """ready / check / unknown / review / problem / sorted / skipped - what the row shows."""
    if book in done:
        return "sorted"
    if book in skipped:
        return "skipped"
    if plan is not None:
        if plan.problems.get(book):
            return "problem"
        if book in plan.review:
            return "review"
    return book.status.value


class BookModel(QAbstractTableModel):
    def __init__(self, folders: Folders):
        super().__init__()
        self.folders = folders
        self.books: list[Book] = []
        self.plan: Plan | None = None
        self.checked: set[Book] = set()
        self.done: set[Book] = set()  # sorted in this or an earlier run
        self.skipped: set[Book] = set()  # left out on purpose (e.g. the extra copy of a duplicate)

    def state(self, book: Book) -> str:
        return state_of(book, self.plan, self.done, self.skipped)

    # -- loading -----------------------------------------------------------
    def set_books(self, books: list[Book], plan: Plan, done: set[Book]) -> None:
        self.beginResetModel()
        self.books, self.plan, self.done = books, plan, done
        self.skipped = set()
        self.checked = {b for b in books if self.default_checked(b)}
        self.endResetModel()

    def set_plan(self, plan: Plan) -> None:
        self.plan = plan
        self.checked = {b for b in self.checked if not plan.problems.get(b)}
        self.refresh()

    def refresh(self, book: Book | None = None) -> None:
        if not self.books:
            return
        if book is None:
            top, bottom = 0, len(self.books) - 1
        else:
            top = bottom = self.books.index(book)
        self.dataChanged.emit(self.index(top, 0), self.index(bottom, len(COLUMNS) - 1))

    def default_checked(self, book: Book) -> bool:
        return self.state(book) in ("ready", "review")

    def book_at(self, row: int) -> Book:
        return self.books[row]

    def set_checked(self, books: list[Book], on: bool) -> None:
        for b in books:
            if on and self.state(b) not in ("problem", "sorted", "unknown", "skipped"):
                self.checked.add(b)
            elif not on:
                self.checked.discard(b)
        self.refresh()

    # -- Qt model ----------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.books)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHECK:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        book, col = self.books[index.row()], index.column()
        if role == Qt.CheckStateRole and col == COL_CHECK:
            return Qt.Checked if book in self.checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            return self._text(book, col)
        if role == Qt.DecorationRole and col == COL_STATUS:
            return state_icon(self.state(book))
        if role == SORT_ROLE:
            return self._sort_value(book, col)
        if role == Qt.ForegroundRole:
            state = self.state(book)
            if col == COL_STATUS or (col == COL_TARGET and state in ("problem", "review")):
                return state_color(state)
            if state == "sorted":
                return state_color("sorted")
        if role == Qt.ToolTipRole:
            lines = list(book.notes) + (self.plan.problems.get(book, []) if self.plan else [])
            if col == COL_TARGET and self.plan:
                lines.insert(0, str(self.plan.targets.get(book, "")))
            return "\n".join(lines) or None
        if role == Qt.TextAlignmentRole and col == COL_YEAR:
            return int(Qt.AlignCenter)
        if role == Qt.TextAlignmentRole and col == COL_INDEX:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role == Qt.CheckStateRole and index.column() == COL_CHECK:
            book = self.books[index.row()]
            self.set_checked([book], Qt.CheckState(value) == Qt.Checked)
            return True
        return False

    def _text(self, book: Book, col: int) -> str:
        m = book.meta
        if col == COL_STATUS:
            return self.state(book).capitalize()
        if col == COL_TYPE:
            return type_label(book)
        if col == COL_AUTHOR:
            return ", ".join(m.authors)
        if col == COL_SERIES:
            return m.series or ""
        if col == COL_INDEX:
            return f"{m.series_index:g}" if m.series_index is not None else ""
        if col == COL_TITLE:
            return m.title or ""
        if col == COL_NARR:
            return ", ".join(m.narrators)
        if col == COL_YEAR:
            return str(m.year or "")
        if col == COL_TARGET:
            return self._target_text(book)
        return ""

    def _target_text(self, book: Book) -> str:
        if not self.plan or book not in self.plan.targets:
            return ""
        if problems := self.plan.problems.get(book):
            return problems[0]
        target = self.plan.targets[book]
        for label, root in (("Review", self.folders.review), ("", self.folders.audio_sorted),
                            ("", self.folders.books_sorted)):
            try:
                rel = target.relative_to(root)
            except ValueError:
                continue
            return os.path.join(label, str(rel)) if label else str(rel)
        return str(target)

    def _sort_value(self, book: Book, col: int):
        if col == COL_CHECK:
            return int(book in self.checked)
        if col == COL_INDEX:
            return book.meta.series_index if book.meta.series_index is not None else -1.0
        if col == COL_YEAR:
            return book.meta.year or 0
        return natural_key(self._text(book, col))


class BookFilter(QSortFilterProxyModel):
    def __init__(self, model: BookModel):
        super().__init__()
        self.setSourceModel(model)
        self.setSortRole(SORT_ROLE)
        self.text = ""
        self.state = "all"

    def set_filter(self, text: str | None = None, state: str | None = None) -> None:
        if text is not None:
            self.text = text.casefold()
        if state is not None:
            self.state = state
        self.invalidateFilter()

    def lessThan(self, left, right) -> bool:
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def filterAcceptsRow(self, row: int, parent) -> bool:
        model: BookModel = self.sourceModel()
        book = model.books[row]
        state = model.state(book)
        if state == "sorted" and self.state != "sorted":
            return False  # sorted books only show under Sorted
        if self.state != "all" and state != self.state:
            return False
        if self.text:
            hay = " ".join(model._text(book, c) for c in range(1, len(COLUMNS)))
            hay += " " + str(book.folder or book.main_file)
            return all(word in hay.casefold() for word in self.text.split())
        return True
