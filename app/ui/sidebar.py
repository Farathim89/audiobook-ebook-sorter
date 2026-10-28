"""The tabs bar (Sorter / Collection / Authors) and the left sidebar: per tab its own part - the sorter's actions
and book filters, the Collection's filters and buttons, the Authors' recent lookups - and the pages (MORE)."""
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


class ToggleRow(QPushButton):
    """An on/off option as a row: its name on the left, a switch on the right - gold with the knob right
    when on, grey and empty with the knob left when off (greyed out when it doesn't apply)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("togglerow")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        super().paintEvent(event)
        from PySide6.QtCore import QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPainter, QPen  # noqa: PLC0415
        p = theme._current  # noqa: SLF001
        on, enabled = self.isChecked(), self.isEnabled()
        w, h = 34, 18
        track = QRectF(self.width() - w - 10, (self.height() - h) / 2, w, h)
        paint = QPainter(self)
        paint.setRenderHint(QPainter.Antialiasing)
        paint.setOpacity(1.0 if enabled else 0.35)
        paint.setPen(QPen(QColor(p.accent if on else p.border), 1.5))
        paint.setBrush(QColor(p.accent) if on else QColor(p.field))
        paint.drawRoundedRect(track, h / 2, h / 2)
        knob = h - 6
        x = track.right() - knob - 3 if on else track.left() + 3
        paint.setPen(Qt.NoPen)
        paint.setBrush(QColor(p.on_accent if on else p.muted))
        paint.drawEllipse(QRectF(x, track.top() + 3, knob, knob))
        paint.end()


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
        self._labels = list(labels)
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setObjectName("segfirst" if i == 0 else ("seglast" if i == len(labels) - 1 else "segmid"))
            self._group.addButton(b, i)
            row.addWidget(b, 1)
        self._group.idToggled.connect(lambda i, on: on and self._mark())
        self._group.buttons()[0].setChecked(True)
        self._group.idToggled.connect(lambda i, on: on and not self.signalsBlocked() and self.currentIndexChanged.emit(i))

    def _mark(self) -> None:
        """A check mark on the chosen one ('✓ Move'), so it is plain which is on."""
        for i, b in enumerate(self._group.buttons()):
            b.setText(f"✓  {self._labels[i]}" if b.isChecked() else self._labels[i])

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def setCurrentIndex(self, i: int) -> None:
        if (b := self._group.button(i)) is not None:
            b.setChecked(True)


APP_PAGES = ("books", "collection", "authors")  # the tabs at the top, in order
APP_TABS = [("📥   Sorter", "Sort what's in Incoming into your library (Ctrl+1)"),
            ("📚   Collection", "Your series: what you have, what's missing and what's coming (Ctrl+2)"),
            ("👤   Authors", "Everything an author wrote, next to what you have (Ctrl+3)")]


class AppTabs(QFrame):
    """The bar across the top of the window: one tab per part of the app, the shown one underlined."""
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("apptabbar")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, (text, tip) in enumerate(APP_TABS):
            b = QPushButton(text)
            b.setObjectName("apptab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            self._group.addButton(b, i)
            row.addWidget(b)
        row.addStretch(1)
        self._group.buttons()[0].setChecked(True)
        self._group.idToggled.connect(lambda i, on: on and not self.signalsBlocked() and self.currentIndexChanged.emit(i))

    def currentIndex(self) -> int:
        return self._group.checkedId()

    def setCurrentIndex(self, i: int) -> None:
        if (b := self._group.button(i)) is not None:
            self.blockSignals(True)
            b.setChecked(True)
            self.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt
        """Room for each label in bold (the shown tab is bold): it never gets cut off, at any screen scaling."""
        from PySide6.QtGui import QFontMetrics  # noqa: PLC0415
        for b in self._group.buttons():
            font = b.font()
            font.setBold(True)
            b.setMinimumWidth(QFontMetrics(font).horizontalAdvance(b.text()) + 44)
        super().showEvent(event)


class Sidebar(QFrame):
    filter_chosen = Signal(str)   # 'all', 'ready', 'check', ...
    page_chosen = Signal(str)     # 'books', 'collection', 'authors', 'tools', 'history', 'settings'
    collection_row = Signal(int)     # a Collection filter: its row in the Collection page's own list
    collection_action = Signal(str)  # 'scan' / 'check' / 'copy' / 'settings'
    author_chosen = Signal(str)      # a recent author: look them up again

    def __init__(self, filters: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(224)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        brand = QWidget()
        brand.setObjectName("brand")
        b = QVBoxLayout(brand)
        b.setContentsMargins(10, 9, 10, 6)
        b.setSpacing(0)
        title = QLabel("SHELFKEEPER")
        title.setObjectName("brandtitle")
        sub = QLabel("audiobooks · ebooks · manga")
        sub.setObjectName("brandsub")
        b.addWidget(title)
        sub.hide()  # room for everything on a 720p screen
        outer.addWidget(brand)
        self._app = "books"

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

        # the sorter's own part (actions + book filters): only on the Sorter tab
        main_v = self.v
        self.sorter_box = QWidget()
        self.v = QVBoxLayout(self.sorter_box)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(1)

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
        self.v.addSpacing(4)
        self.mode = SegmentedSwitch(["Copy", "Move"])
        self.mode.setToolTip("Copy keeps the originals in Incoming. Move takes them out "
                             "(their leftover cover/metadata goes to the Done folder). Remembered.")
        self.v.addWidget(self.mode)
        self.v.addSpacing(4)
        # on/off options, each a row with a switch: easy to see what is on
        self.cb_keep_done = ToggleRow("📦  Keep old copies")  # Move mode: old copies + leftovers to Done
        self.cb_m4b = ToggleRow("🎵  MP3 → M4B")
        self.cb_redo_m4b = ToggleRow("🔁  M4B → M4B")
        for row in (self.cb_keep_done, self.cb_m4b, self.cb_redo_m4b):
            self.v.addWidget(row)
            self.v.addSpacing(2)

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

        self.v = main_v
        self.v.addWidget(self.sorter_box)
        self._build_collection_part()
        self._build_authors_part()

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
        self.set_app("books")  # the Sorter's part shows first

    def _build_collection_part(self) -> None:
        """The Collection's filters and buttons (its page has no sidebar of its own here)."""
        from ..collection.ui.main_window import FILTERS, KIND_FILTERS  # noqa: PLC0415
        main_v = self.v
        self.collection_box = QWidget()
        self.v = QVBoxLayout(self.collection_box)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(1)
        self._group("ACTIONS")
        self.b_cscan = self._action_button("⟳  Scan library")
        self.b_cscan.setToolTip("Read your libraries again (keeps what was found online)")
        self.v.addSpacing(3)
        self.b_ccheck = QPushButton("🌐  Check online")
        self.b_ccheck.setObjectName("primary")
        self.b_ccheck.setFixedHeight(32)
        self.b_ccheck.setCursor(Qt.PointingHandCursor)
        self.b_ccheck.setToolTip("Look up the series shown for missing and upcoming books")
        self.v.addWidget(self.b_ccheck)
        self.v.addSpacing(3)
        self.b_ccopy = self._action_button("📋  Copy missing list")
        self.b_ccopy.setToolTip("Missing and upcoming books of the series shown, to the clipboard")
        self.v.addSpacing(3)
        self.b_csettings = self._action_button("⚙  Collection options")
        self.b_csettings.setToolTip("The Collection's options: library folders, which sources to ask, hidden series")
        for btn, what in ((self.b_cscan, "scan"), (self.b_ccheck, "check"), (self.b_ccopy, "copy"),
                          (self.b_csettings, "settings")):
            btn.clicked.connect(lambda _=False, w=what: self.collection_action.emit(w))
        self.collection_buttons: dict[int, NavButton] = {}
        self._group("SHOW")
        for row_no, (label, _key) in enumerate(FILTERS):
            self._collection_button(row_no, label)
        self._group("KIND")
        for i, (label, _key) in enumerate(KIND_FILTERS):
            self._collection_button(len(FILTERS) + 1 + i, label)  # (its list has a 'KIND' heading row)
        self.collection_totals = QLabel()
        self.collection_totals.setObjectName("muted")
        self.collection_totals.setWordWrap(True)
        self.v.addSpacing(6)
        self.v.addWidget(self.collection_totals)
        self.v = main_v
        self.v.addWidget(self.collection_box)
        self._collection_row_now = 0

    def _collection_button(self, row_no: int, label: str) -> None:
        btn = NavButton(label.strip())
        if label.startswith(" "):  # a sub-filter ('Missing audiobooks' under 'Missing books')
            btn.layout().setContentsMargins(26, 0, 10, 0)
        btn.clicked.connect(lambda _=False, r=row_no: self._pick_collection(r))
        self._filters.addButton(btn)
        self.collection_buttons[row_no] = btn
        self.v.addWidget(btn)

    def _build_authors_part(self) -> None:
        main_v = self.v
        self.authors_box = QWidget()
        self.v = QVBoxLayout(self.authors_box)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(1)
        self._group("RECENT")
        self.recent_list = QVBoxLayout()
        self.recent_list.setContentsMargins(0, 0, 0, 0)
        self.recent_list.setSpacing(1)
        self.v.addLayout(self.recent_list)
        self.recent_empty = QLabel("Authors you look up show here.")
        self.recent_empty.setObjectName("muted")
        self.recent_empty.setWordWrap(True)
        self.v.addWidget(self.recent_empty)
        self.v = main_v
        self.v.addWidget(self.authors_box)

    # -- building blocks --------------------------------------------------
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

    def _pick_collection(self, row_no: int) -> None:
        self._collection_row_now = row_no
        self.page_chosen.emit("collection")
        self.collection_row.emit(row_no)

    def app_page(self) -> str:
        """The tab shown: 'books' (Sorter), 'collection' or 'authors'."""
        return self._app

    def set_app(self, page: str) -> None:
        """The sidebar part of this tab; the entry of what is shown lit."""
        if page in APP_PAGES:
            self._app = page
        self.sorter_box.setVisible(self._app == "books")
        self.collection_box.setVisible(self._app == "collection")
        self.authors_box.setVisible(self._app == "authors")
        if self._app == "collection":
            self.select_collection_row(self._collection_row_now)
        elif self._app == "authors":  # its page is the whole tab: nothing in the menu is lit
            self._filters.setExclusive(False)
            for btn in self._filters.buttons():
                btn.setChecked(False)
            self._filters.setExclusive(True)

    def select_collection_row(self, row_no: int) -> None:
        self._collection_row_now = row_no
        if btn := self.collection_buttons.get(row_no):
            btn.setChecked(True)

    def set_collection_totals(self, text: str) -> None:
        self.collection_totals.setText(text)

    def set_recent(self, names: list[str]) -> None:
        """The authors looked up last (newest first) - a click looks them up again."""
        while self.recent_list.count():
            w = self.recent_list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for name in names[:12]:
            btn = NavButton(name)
            btn.setCheckable(False)
            btn.clicked.connect(lambda _=False, n=name: self.author_chosen.emit(n))
            self.recent_list.addWidget(btn)
        self.recent_empty.setVisible(not names)

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
