"""The left sidebar: actions (Scan / Look up / Sort), the book filters with counts, and pages."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from . import theme


class NavButton(QPushButton):
    """A flat, left-aligned sidebar entry with an optional count badge on the right."""

    def __init__(self, text: str, icon_state: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("nav")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 10, 0)
        row.setSpacing(8)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("navicon")
        self.icon_label.setFixedWidth(16)
        self._state = icon_state
        if icon_state is None and text != "All":
            self.icon_label.hide()  # the text has its own symbol (Tools)
        row.addWidget(self.icon_label)
        self.text_label = QLabel(text)
        self.text_label.setObjectName("navtext")
        row.addWidget(self.text_label, 1)
        self.badge = QLabel()
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.hide()
        row.addWidget(self.badge)
        self.setFixedHeight(28)
        self.toggled.connect(self._mark)
        self.refresh_icon()

    def _mark(self, on: bool) -> None:
        """Stylesheets can't follow the button's 'checked' into its labels: mark them ourselves."""
        for w in (self.text_label, self.badge):
            w.setProperty("on", on)
            w.style().unpolish(w)
            w.style().polish(w)

    def refresh_icon(self) -> None:
        if self._state:
            self.icon_label.setPixmap(theme.state_icon(self._state, 14).pixmap(14, 14))

    def set_count(self, n: int | None) -> None:
        self.badge.setVisible(bool(n))
        self.badge.setText(str(n) if n else "")


class SegmentedSwitch(QWidget):
    """Two (or more) side-by-side buttons, one lit - a click switches (no dropdown).
    Same API as the combo box it replaces: currentIndex / setCurrentIndex / currentIndexChanged."""
    currentIndexChanged = Signal(int)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setObjectName("segfirst" if i == 0 else ("seglast" if i == len(labels) - 1 else "segmid"))
            self._group.addButton(b, i)
            row.addWidget(b, 1)
        self._group.buttons()[0].setChecked(True)
        self._group.idToggled.connect(lambda i, on: on and not self.signalsBlocked() and self.currentIndexChanged.emit(i))

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def setCurrentIndex(self, i: int) -> None:
        if (b := self._group.button(i)) is not None:
            b.setChecked(True)


class Sidebar(QFrame):
    filter_chosen = Signal(str)   # 'all', 'ready', 'check', ...
    page_chosen = Signal(str)     # 'books', 'collection', 'tools', 'history', 'settings'

    def __init__(self, filters: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(212)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        brand = QWidget()
        brand.setObjectName("brand")
        b = QVBoxLayout(brand)
        b.setContentsMargins(16, 9, 16, 6)
        b.setSpacing(0)
        title = QLabel("SHELFKEEPER")
        title.setObjectName("brandtitle")
        sub = QLabel("audiobooks · ebooks · manga")
        sub.setObjectName("brandsub")
        b.addWidget(title)
        sub.hide()  # room for everything on a 720p screen
        outer.addWidget(brand)

        scroll = QScrollArea()
        scroll.setObjectName("sidescroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("sidebody")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(8, 2, 8, 8)
        self.v.setSpacing(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # -- actions --
        self._group("ACTIONS")
        self.b_scan = self._action_button("⟳  Scan Incoming")
        self.v.addSpacing(3)
        self.b_lookup = self._action_button("🌐  Look up online")
        self.v.addSpacing(3)
        self.b_sort = QPushButton("Sort ticked books")
        self.b_sort.setObjectName("primary")
        self.b_sort.setFixedHeight(32)
        self.b_sort.setCursor(Qt.PointingHandCursor)
        self.v.addWidget(self.b_sort)
        opts = QHBoxLayout()
        opts.setContentsMargins(0, 2, 0, 0)
        self.mode = SegmentedSwitch(["Copy", "Move"])
        self.mode.setToolTip("Copy keeps the originals in Incoming. Move takes them out "
                             "(their leftover cover/metadata goes to the Done folder). Remembered.")
        opts.setSpacing(6)
        opts.addWidget(self.mode, 3)
        self.cb_keep_done = self._pill("📦 Done")  # Move mode: keep old copies in the Done folder
        opts.addWidget(self.cb_keep_done, 2)
        self.v.addLayout(opts)
        audio = QHBoxLayout()
        audio.setContentsMargins(0, 4, 0, 0)
        audio.setSpacing(6)
        self.cb_m4b = self._pill("MP3 → M4B")       # on/off switches (checkable): lit when on
        self.cb_redo_m4b = self._pill("M4B → M4B")
        audio.addWidget(self.cb_m4b, 1)
        audio.addWidget(self.cb_redo_m4b, 1)
        self.v.addLayout(audio)

        # -- book filters --
        self._group("BOOKS")
        self.filter_buttons: dict[str, NavButton] = {}
        self._filters = QButtonGroup(self)
        self._filters.setExclusive(True)
        for label, key in filters:
            btn = NavButton(label, None if key == "all" else key)
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._filters.addButton(btn)
            self.filter_buttons[key] = btn
            self.v.addWidget(btn)

        # -- your library: what you have and what's missing (the former Book Collection Checker) --
        self._group("LIBRARY")
        self.b_collection = NavButton("📚   Collection")
        self.b_collection.setToolTip("Your series: what you have, what's missing and what's coming")
        self.b_collection.clicked.connect(lambda: self._pick_page("collection"))
        self._filters.addButton(self.b_collection)
        self.v.addWidget(self.b_collection)
        self.b_authors = NavButton("👤   Authors")
        self.b_authors.setToolTip("Everything an author wrote - audiobooks, light novels, manga, ebooks - "
                                  "next to what you have")
        self.b_authors.clicked.connect(lambda: self._pick_page("authors"))
        self._filters.addButton(self.b_authors)
        self.v.addWidget(self.b_authors)

        # -- pages / more --
        self._group("MORE")
        self.b_tools = NavButton("🧰   Tools")
        self.b_tools.clicked.connect(lambda: self._pick_page("tools"))
        self._filters.addButton(self.b_tools)
        self.v.addWidget(self.b_tools)
        self.b_history = NavButton("🕘   History / Undo")
        self.b_history.clicked.connect(lambda: self._pick_page("history"))
        self.b_settings = NavButton("⚙   Settings")
        self.b_settings.clicked.connect(lambda: self._pick_page("settings"))
        for b in (self.b_history, self.b_settings):
            self._filters.addButton(b)
            self.v.addWidget(b)
        self.b_help = self._plain("❔   How to use")
        self.v.addStretch(1)

    # -- building blocks --------------------------------------------------
    @staticmethod
    def _pill(text: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("pilltoggle")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(28)
        return b

    def _group(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("navgroup")
        self.v.addSpacing(6)
        self.v.addWidget(label)

    def _action_button(self, text: str) -> QPushButton:
        """A real button (Scan, Look up): filled and framed, like the Sort button but not gold."""
        btn = QPushButton(text)
        btn.setObjectName("sideaction")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(32)
        self.v.addWidget(btn)
        return btn

    def _plain(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("navplain")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(28)
        self.v.addWidget(btn)
        return btn

    # -- behaviour -----------------------------------------------------------
    def _pick(self, key: str) -> None:
        self.page_chosen.emit("books")
        self.filter_chosen.emit(key)

    def _pick_page(self, page: str) -> None:
        self.page_chosen.emit(page)

    def select_filter(self, key: str) -> None:
        if btn := self.filter_buttons.get(key):
            btn.setChecked(True)

    def set_counts(self, counts: dict[str, int], total: int) -> None:
        for key, btn in self.filter_buttons.items():
            btn.set_count(total if key == "all" else counts.get(key, 0))

    def set_sort_count(self, n: int) -> None:
        self.b_sort.setText(f"Sort {n} ticked book{'s' if n != 1 else ''}" if n else "Sort ticked books")

    def refresh_theme(self) -> None:
        for btn in self.filter_buttons.values():
            btn.refresh_icon()


def tool_card(title: str, text: str, button: str, slot: Callable, parent=None) -> QFrame:
    """One tool on the Tools page: a title, a line of explanation and a button."""
    card = QFrame(parent)
    card.setObjectName("card")
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(6)
    t = QLabel(title)
    t.setObjectName("cardtitle")
    t.setWordWrap(True)
    d = QLabel(text)
    d.setObjectName("muted")
    d.setWordWrap(True)
    v.addWidget(t)
    v.addWidget(d, 1)
    row = QHBoxLayout()
    row.addStretch(1)
    b = QPushButton(button)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    row.addWidget(b)
    v.addLayout(row)
    card.setMinimumHeight(132)
    return card
