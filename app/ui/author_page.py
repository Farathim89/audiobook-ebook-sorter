"""The Author page: type an author, see every series and book Audible, AniList and Google Books / OpenLibrary know -
per kind (🎧 audiobook, 📖 light novel, 🗯 manga, 📘 ebook) what you have and what's missing.

    left:  the author's works (filter: all / the ones you have some of / the ones new to you)
    right: the selected work - a tab per kind it exists in, with its volumes, what you have and release dates"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCompleter, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QSplitter, QTabBar, QTableWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..authors.works import KIND_LABELS, KINDS, Work, parents
from ..collection.ui.main_window import _GroupHeader, _Item, make_copyable
from ..portable import ui_settings
from . import theme
from .columns import resizable_columns
from .sidebar import SegmentedSwitch
from .workers import Worker

COLS = ["Series / book", "have", "✗", "have", "✗", "have", "✗", "have", "✗"]
GROUPS = {1: "🎧 A", 3: "📖 LN", 5: "🗯 M", 7: "📘 E"}
VOLUME_COLS = ["#", "Title", "Have", "Release"]
SHOW = ["All", "You have some", "New to you"]  # which works the list shows


class _TreeItem(QTreeWidgetItem):
    """Sorts by a cell's number (UserRole + 1) when it has one, else by its text."""

    def __lt__(self, other) -> bool:  # noqa: D105
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None or b is not None:
            return (a or 0) < (b or 0)
        return self.text(col).casefold() < other.text(col).casefold()


class AuthorPage(QWidget):
    recent_changed = Signal(list)  # the authors looked up last, newest first (the sidebar's RECENT)

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = folders
        self.works: list[Work] = []
        self.worker: Worker | None = None
        self._pending: str | None = None  # 'More by Y' while X is looked up: Y next
        self._shown: Work | None = None
        self._kinds: list[str] = []  # the kind of each tab on the right
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 8)
        row = QHBoxLayout()
        self.name = QLineEdit(str(ui_settings().value("authors/last", "") or ""))
        self.name.setObjectName("search")
        self.name.setPlaceholderText("An author - e.g. Reki Kawahara")
        self.name.returnPressed.connect(self.look_up)
        row.addWidget(self.name, 1)
        self.b_look = QPushButton("🔍  Look up")
        self.b_look.setObjectName("primary")
        self.b_look.clicked.connect(self.look_up)
        row.addWidget(self.b_look)
        v.addLayout(row)
        self.status = QLabel("Every series and book of an author, next to what you have.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(220)
        self.bar.setVisible(False)
        srow = QHBoxLayout()
        srow.addWidget(self.status, 1)
        srow.addWidget(self.bar)
        v.addLayout(srow)

        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        frow = QHBoxLayout()
        self.show_filter = SegmentedSwitch(SHOW)
        self.show_filter.setMaximumWidth(420)
        self.show_filter.setToolTip("All of the author's works / only the ones you have some of / only the ones "
                                    "you have none of yet")
        try:
            self.show_filter.setCurrentIndex(int(ui_settings().value("authors/show", 0)))
        except (TypeError, ValueError):
            pass
        self.show_filter.currentIndexChanged.connect(lambda i: (ui_settings().setValue("authors/show", i), self.fill()))
        frow.addWidget(self.show_filter)
        self.count = QLabel()
        self.count.setObjectName("muted")
        frow.addWidget(self.count, 1, Qt.AlignRight)
        lv.addLayout(frow)
        # a tree: a series' spin-offs, manga adaptations and side stories fold under it
        t = self.table = QTreeWidget()
        t.setColumnCount(len(COLS))
        t.setHeader(_GroupHeader(t, GROUPS))
        t.setHeaderLabels(COLS)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setUniformRowHeights(True)
        t.setWordWrap(False)
        t.setIndentation(18)
        # every column can be dragged wider / narrower (remembered); the name starts wide
        self._own_widths = resizable_columns(
            t, "authors/widths", {0: 430, **{c: 52 if c % 2 else 46 for c in range(1, len(COLS))}}, ui_settings)
        t.currentItemChanged.connect(lambda *_: self.show_work())
        lv.addWidget(t, 1)
        split.addWidget(left)

        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(10, 0, 0, 0)
        self.title = QLabel("Select a series")
        self.title.setObjectName("pagetitle")
        self.title.setWordWrap(True)
        r.addWidget(self.title)
        self.links = QLabel()
        self.links.setOpenExternalLinks(True)
        self.links.setWordWrap(True)
        r.addWidget(self.links)
        self.kind_tabs = QTabBar()
        self.kind_tabs.setExpanding(False)
        self.kind_tabs.setDrawBase(False)
        self.kind_tabs.currentChanged.connect(lambda _: self._show_kind())
        r.addWidget(self.kind_tabs)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        r.addWidget(self.summary)
        vt = self.volumes = QTableWidget(0, len(VOLUME_COLS))
        vt.setHorizontalHeaderLabels(VOLUME_COLS)
        vt.verticalHeader().setVisible(False)
        vt.setEditTriggers(QAbstractItemView.NoEditTriggers)
        vt.setSelectionBehavior(QAbstractItemView.SelectRows)
        vt.setWordWrap(False)
        self._own_volume_widths = resizable_columns(vt, "authors/volume_widths", {0: 46, 1: 360, 2: 58, 3: 104},
                                                    ui_settings, stretch_last=True)
        self._fitted = False
        r.addWidget(vt, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([820, 560])
        v.addWidget(split, 1)
        make_copyable(self.volumes)
        self._complete_from_library()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt
        super().showEvent(event)
        if not self._fitted:  # first time shown: names / titles take the room, every column visible
            self._fitted = True
            from PySide6.QtCore import QTimer  # noqa: PLC0415
            QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        from .columns import fill_column  # noqa: PLC0415
        if not self._own_widths:
            fill_column(self.table, 0)
        if not self._own_volume_widths:
            fill_column(self.volumes, 1, least=140)

    # -- looking up -----------------------------------------------------------------------------------
    @staticmethod
    def recent() -> list[str]:
        value = ui_settings().value("authors/recent", []) or []
        return [value] if isinstance(value, str) else [str(v) for v in value]

    def _complete_from_library(self) -> None:
        """Suggest the authors your Audiobookshelf has - read in the background (it asks Audiobookshelf)."""
        folders = self.folders

        def names(_w) -> list[str]:
            from ..core.abs_index import AbsIndex  # noqa: PLC0415
            return sorted({a for b in AbsIndex.load(folders).books for a in b.authors}, key=str.casefold)

        def done(found: list[str]) -> None:
            box = QCompleter(found, self)
            box.setCaseSensitivity(Qt.CaseInsensitive)
            box.setFilterMode(Qt.MatchContains)
            self.name.setCompleter(box)

        self._names_worker = Worker(names)  # kept: the pool must not outlive its signals
        self._names_worker.signals.finished.connect(done)
        QThreadPool.globalInstance().start(self._names_worker)

    def look_up(self) -> None:
        author = self.name.text().strip()
        if not author:
            return
        if self.worker is not None:  # one at a time: this one right after
            self._pending = author
            self.worker.cancel()
            self.status.setText(f"{author} next…")
            return
        ui_settings().setValue("authors/last", author)
        recent = [author] + [n for n in self.recent() if n.casefold() != author.casefold()]
        ui_settings().setValue("authors/recent", recent[:12])
        self.recent_changed.emit(recent[:12])
        from ..authors.sources import AuthorLookup  # noqa: PLC0415
        lookup = AuthorLookup(self.folders, _google_key(self.folders))
        self.worker = Worker(lambda w: lookup.run(author, w.report, w.cancelled))
        self.worker.signals.progress.connect(self._progress)
        self.worker.signals.finished.connect(lambda result: self._done(author, *result))
        self.worker.signals.failed.connect(self._failed)
        self.b_look.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText(f"Looking up {author}…")
        QThreadPool.globalInstance().start(self.worker)

    def _progress(self, i: int, n: int, text: str) -> None:
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i)
        self.status.setText(text)

    def _failed(self, text: str) -> None:
        self.worker = None
        self.b_look.setEnabled(True)
        self.bar.setVisible(False)
        self.status.setText(f"The lookup failed: {text}")
        self._next()

    def _next(self) -> bool:
        """A lookup asked for while another ran: start it now."""
        author, self._pending = self._pending, None
        if author:
            self.name.setText(author)
            self.look_up()
        return bool(author)

    def _done(self, author: str, works, notes: list[str]) -> None:
        self.worker = None
        self.b_look.setEnabled(True)
        self.bar.setVisible(False)
        if self._next():
            return  # cancelled for the next author: these results are the old one's
        self.works = works.sorted()
        mine = sum(1 for w in self.works if _collecting(w))
        missing = sum(w.missing(k) for w in self.works if _collecting(w) for k in KINDS)
        text = f"{author}: {len(self.works)} series / books - you have {mine} of them"
        if missing:
            text += f", {missing} volumes missing in those"
        self.status.setText(text + ("" if not notes else "  ·  " + "; ".join(notes)))
        self.fill()

    # -- the list (left) ---------------------------------------------------------------------------------
    def _matches(self, w: Work) -> bool:
        show = self.show_filter.currentIndex()
        return show == 0 or (show == 1) == _collecting(w)

    def fill(self) -> None:
        p = theme._current  # noqa: SLF001 - the theme's colours
        keep = self._shown
        up = parents(self.works)
        kids: dict[int, list[int]] = {}
        for i, j in up.items():
            if j is not None:
                kids.setdefault(j, []).append(i)

        def family(i: int) -> list[int]:
            return [i] + [k for c in kids.get(i, []) for k in family(c)]

        # a work shows when it - or something folded under it - fits the filter
        shown = {i for i in range(len(self.works)) if any(self._matches(self.works[k]) for k in family(i))}
        t = self.table
        t.setSortingEnabled(False)
        t.clear()
        items: dict[int, _TreeItem] = {}
        for i in sorted(shown, key=lambda i: _depth(i, up)):  # a main series before what folds under it
            w = self.works[i]
            item = _TreeItem()
            items[i] = item
            parent = up[i] if up[i] in shown else None
            (items[parent].addChild(item) if parent is not None and parent in items else t.addTopLevelItem(item))
            self._fill_item(item, w, i, len(family(i)) - 1, p)
        t.setSortingEnabled(True)
        t.sortByColumn(0, Qt.AscendingOrder)  # A-Z
        tops = sum(1 for i in shown if up[i] not in shown)
        self.count.setText(f"{tops} series · {len(shown)} works" if self.works else "")
        target = next((it for i, it in items.items() if self.works[i] is keep), None)
        if target is None and t.topLevelItemCount():
            target = t.topLevelItem(0)
        if target is not None:
            t.setCurrentItem(target)
        else:
            self._clear_right()

    def _fill_item(self, item: QTreeWidgetItem, w: Work, index: int, related: int, p) -> None:
        item.setText(0, f"{w.name}   +{related}" if related else w.name)
        item.setData(0, Qt.UserRole, index)
        kinds = ", ".join(KIND_LABELS[k] for k in KINDS if k in w.kinds or w.have(k))
        where = sorted({v.source.capitalize() for v in w.volumes} | ({"Anilist"} if "AniList" in w.links else set()))
        found = ", ".join(where).replace("Openlibrary", "Open Library").replace("Anilist", "AniList") \
            or ("only in your library" if _collecting(w) else "")
        tip = f"{w.name}\n{kinds}\nfound on: {found}"
        if related:
            tip += f"\n+{related} related (spin-offs, manga, side stories): click the arrow"
        item.setToolTip(0, tip)
        for i, kind in enumerate(KINDS):
            have, online, miss = w.have(kind), w.online(kind), w.missing(kind)
            hc, mc = 1 + 2 * i, 2 + 2 * i
            item.setText(hc, str(have) if have or online else "")
            item.setData(hc, Qt.UserRole + 1, have)
            if not have and online:
                item.setForeground(hc, QColor(p.muted))
            if miss:
                item.setText(mc, f"✗{miss}")
                item.setForeground(mc, QColor(p.problem if have else p.check))
                item.setToolTip(mc, f"{miss} missing" if have else f"{online} exist - you have none of them")
            elif have and online:
                item.setText(mc, "✓")
                item.setForeground(mc, QColor(p.ready))
            elif kind in w.kinds and not have and not online:  # it exists in this kind - how many is unknown
                item.setText(mc, "?")
                item.setForeground(mc, QColor(p.muted))
                item.setToolTip(mc, f"exists as {KIND_LABELS[kind].split(' ', 1)[1].lower()} - "
                                    "no source lists its volumes yet")
            item.setData(mc, Qt.UserRole + 1, miss)
            item.setTextAlignment(hc, Qt.AlignCenter)
            item.setTextAlignment(mc, Qt.AlignCenter)

    # -- the selected work (right) ------------------------------------------------------------------------
    def _clear_right(self) -> None:
        self._shown = None
        self.title.setText("Select a series")
        self.links.clear()
        self.summary.clear()
        while self.kind_tabs.count():
            self.kind_tabs.removeTab(0)
        self.volumes.setRowCount(0)

    def show_work(self) -> None:
        item = self.table.currentItem()
        if item is None:
            return
        w = self.works[item.data(0, Qt.UserRole)]
        before = self._kinds[self.kind_tabs.currentIndex()] if self._shown is w and self._kinds else None
        self._shown = w
        self.title.setText(w.name)
        accent = theme._current.accent  # noqa: SLF001
        bits = [f'<a href="{url}" style="color:{accent}">{name}</a>' for name, url in w.links.items()]
        if w.status:
            bits.append({"RELEASING": "still coming out", "FINISHED": "finished"}.get(w.status, w.status.lower()))
        self.links.setText("  ·  ".join(bits))
        self.kind_tabs.blockSignals(True)
        while self.kind_tabs.count():
            self.kind_tabs.removeTab(0)
        self._kinds = [k for k in KINDS if k in w.kinds or w.have(k) or w.online(k)]
        for k in self._kinds:
            n = max(w.online(k), w.have(k))
            self.kind_tabs.addTab(f"{KIND_LABELS[k]}  {n}" if n else KIND_LABELS[k])
        # the tab of the kind you collect it in first (else the first kind it exists in)
        first = before if before in self._kinds else next((k for k in self._kinds if w.have(k)), None)
        self.kind_tabs.setCurrentIndex(self._kinds.index(first) if first in self._kinds else 0)
        self.kind_tabs.blockSignals(False)
        self._show_kind()

    def _show_kind(self) -> None:
        w = self._shown
        if w is None or not self._kinds:
            self.volumes.setRowCount(0)
            self.summary.clear()
            return
        kind = self._kinds[max(0, self.kind_tabs.currentIndex())]
        p = theme._current  # noqa: SLF001
        mine = w.mine.get(kind, set())
        rows_out, seen, sources = [], set(), set()
        shown = [v for v in w.volumes if v.kind == kind and v.number is not None] + w.loose(kind)
        for v in sorted(shown, key=lambda v: (v.number is None, v.number or 0)):
            if v.number is not None and v.number in seen:
                continue
            seen.add(v.number)
            sources.add(v.source)
            rows_out.append((v.number, v.title, v.number in mine if v.number is not None else None, v.date,
                             v.source, v.url))
        known = {n for n in seen if n is not None}
        for n in sorted(w.numbers(kind) - known):  # between listed volumes (or yours): no source names them
            rows_out.append((n, f"{w.name}, Vol. {n:g}", n in mine, "", "yours" if n in mine else "between", ""))
        rows_out.sort(key=lambda r: (r[0] is None, r[0] or 0))
        known |= w.numbers(kind)
        if w.counts.get(kind) and not known:
            rows_out.append((None, f"{w.counts[kind]} volumes (AniList)", None, "", "anilist", ""))
            sources.add("anilist")
        for n in sorted(mine - known):
            rows_out.append((n if n else None, "in your library", True, "", "", ""))
        have, miss = w.have(kind), w.missing(kind)
        names = {"audible": "Audible", "anilist": "AniList", "google": "Google Books", "openlibrary": "Open Library",
                 "between": "no source names this one - its number lies within the series' known volumes",
                 "yours": "in your library - no source lists it (yet)"}
        sources.discard("between")
        text = f"You have {have}" + (f" · {miss} missing" if miss else (" · all of them" if have else ""))
        if sources:
            text += "  ·  from " + ", ".join(names.get(s, s) for s in sorted(sources))
        self.summary.setText(text)
        vt = self.volumes
        vt.clearSpans()
        if not rows_out:  # the work exists (AniList knows it) but no source lists its volumes
            known_by = " and ".join(n for n in ("AniList", "Audible") if n in w.links) or "a source"
            note = (f"No source lists its volumes yet - {known_by} knows it as "
                    f"{KIND_LABELS[kind].split(' ', 1)[1].lower()}"
                    + (", still coming out" if w.status == "RELEASING" else "") + ". Look up again later.")
            vt.setRowCount(1)
            it = _Item(note)
            it.setForeground(QColor(p.muted))
            it.setToolTip(note)
            vt.setItem(0, 0, it)
            vt.setSpan(0, 0, 1, vt.columnCount())
            self.summary.setText(f"You have {have}  ·  volumes unknown")
            return
        vt.setRowCount(len(rows_out))
        for r, (n, title, owned, date, source, url) in enumerate(rows_out):
            cells = ["" if n is None else f"{n:g}", _short(title, w.name), "✓" if owned else ("✗" if owned is False else ""),
                     date]
            for c, cell in enumerate(cells):
                it = _Item(cell)
                if c == 1:
                    it.setToolTip(f"{title}\n{names.get(source, source)}" + (f"\n{url}" if url else ""))
                if c in (0, 2):
                    it.setTextAlignment(Qt.AlignCenter)
                if c == 2:
                    it.setForeground(QColor(p.ready if owned else p.problem))
                elif source == "between" and c in (1, 3):
                    it.setForeground(QColor(p.muted))
                vt.setItem(r, c, it)

    def refresh_theme(self) -> None:
        self.fill()


def _short(title: str, series: str) -> str:
    """The title without the series name the heading already shows: 'Heroine? Saint? No, I'm an All-Works Maid
    (and Proud of It)!, Vol. 5' -> 'Vol. 5', 'Sword Art Online 3: Fairy Dance' -> '3: Fairy Dance'."""
    import re  # noqa: PLC0415
    title = re.sub(r"(?i)\s*[\(\[](?:light novel|novel)[\)\]]", "", title).strip()  # '(Light Novel), Vol. 4'
    if series and title.casefold().startswith(series.casefold()):
        rest = title[len(series):].lstrip(" ,:;-–—")
        if rest:
            return rest
    return title


def _depth(i: int, up: dict[int, int | None]) -> int:
    d = 0
    while up.get(i) is not None:
        i, d = up[i], d + 1
    return d


def _collecting(w: Work) -> bool:
    """You have some of it."""
    return any(w.have(k) for k in KINDS)


def _google_key(folders) -> str:
    """The Google Books key: the sorter's, else the Collection's (it came from the old Checker)."""
    if getattr(folders, "google_books_key", ""):
        return folders.google_books_key
    try:
        from ..collection.config import load_settings  # noqa: PLC0415
        return load_settings().google_books_key
    except (OSError, ValueError):
        return ""
