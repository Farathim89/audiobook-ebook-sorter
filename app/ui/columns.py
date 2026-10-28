"""Tables and lists: every column can be dragged wider or narrower (the widths are remembered), and scrolling
moves smoothly by pixels - not a whole column / row at a time."""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QWidget


def smooth_scrolling(root: QWidget) -> None:
    """Every table / list / tree inside root scrolls by pixels (left-right too: no jumps of a whole column)."""
    views = [root] if isinstance(root, QAbstractItemView) else []
    for view in views + root.findChildren(QAbstractItemView):
        view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)


def resizable_columns(view: QAbstractItemView, key: str, widths: dict[int, int], settings,
                      stretch_last: bool = False) -> bool:
    """All columns draggable; start at `widths` (column -> px), then the widths you set last time (saved under
    `key` in settings(), a QSettings factory). Returns True when saved widths were used."""
    head: QHeaderView = view.horizontalHeader() if hasattr(view, "horizontalHeader") else view.header()
    head.setSectionResizeMode(QHeaderView.Interactive)
    head.setStretchLastSection(stretch_last)
    head.setMinimumSectionSize(28)
    for col, width in widths.items():
        view.setColumnWidth(col, width)
    saved = settings().value(key)
    try:
        saved = [int(w) for w in saved] if saved else []
    except (TypeError, ValueError):
        saved = []
    if len(saved) == head.count():
        for col, width in enumerate(saved):
            if width > 0:
                view.setColumnWidth(col, width)
    head.sectionResized.connect(lambda *_: view.property("fitting") or settings().setValue(
        key, [head.sectionSize(c) for c in range(head.count())]))  # your own widths only, not the page's fit
    smooth_scrolling(view)
    return len(saved) == head.count()


def fill_column(view: QAbstractItemView, col: int, least: int = 180) -> None:
    """col takes the room the other columns leave (all columns visible, no sideways scrolling) - for a table
    whose widths you haven't set yourself yet."""
    head: QHeaderView = view.horizontalHeader() if hasattr(view, "horizontalHeader") else view.header()
    others = sum(head.sectionSize(c) for c in range(head.count()) if c != col)
    room = view.viewport().width() - others - 4
    if room > least:
        view.setProperty("fitting", True)  # not saved: it is the page's own start, not your choice
        view.setColumnWidth(col, room)
        view.setProperty("fitting", False)
