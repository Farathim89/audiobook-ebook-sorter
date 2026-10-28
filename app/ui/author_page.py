"""The Author page: type an author, see every series and book Audible, AniList and Google Books / OpenLibrary know -
per kind (🎧 audiobook, 📖 light novel, 🗯 manga, 📘 ebook) what you have and what's missing."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCompleter, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QSplitter, QTableWidget, QVBoxLayout, QWidget)

from ..authors.works import KIND_LABELS, KINDS, Work
from ..collection.ui.main_window import _GroupHeader, _Item, make_copyable
from ..portable import ui_settings
from . import theme
from .workers import Worker

COLS = ["Series / book", "have", "✗", "have", "✗", "have", "✗", "have", "✗", "Found on"]
GROUPS = {1: "🎧 A", 3: "📖 LN", 5: "🗯 M", 7: "📘 E"}
VOLUME_COLS = ["", "#", "Title", "Have", "Release", "Source"]  # the kind as its icon (tooltip: the word)


class AuthorPage(QWidget):
    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = folders
        self.works: list[Work] = []
        self.worker: Worker | None = None
        self._pending: str | None = None  # 'More by Y' while X is looked up: Y next
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
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(220)
        self.bar.setVisible(False)
        srow = QHBoxLayout()
        srow.addWidget(self.status, 1)
        srow.addWidget(self.bar)
        v.addLayout(srow)

        split = QSplitter()
        t = self.table = QTableWidget(0, len(COLS))
        t.setHorizontalHeader(_GroupHeader(t, GROUPS))
        t.setHorizontalHeaderLabels(COLS)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for c, width in enumerate((380, 48, 44, 48, 44, 48, 44, 48, 44, 170)):
            t.setColumnWidth(c, width)
        t.itemSelectionChanged.connect(self.show_work)
        split.addWidget(t)
        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(8, 0, 0, 0)
        self.title = QLabel("Select a series")
        self.title.setObjectName("pagetitle")
        self.title.setWordWrap(True)
        r.addWidget(self.title)
        self.links = QLabel()
        self.links.setOpenExternalLinks(True)
        self.links.setWordWrap(True)
        r.addWidget(self.links)
        vt = self.volumes = QTableWidget(0, len(VOLUME_COLS))
        vt.setHorizontalHeaderLabels(VOLUME_COLS)
        vt.verticalHeader().setVisible(False)
        vt.setEditTriggers(QAbstractItemView.NoEditTriggers)
        vt.setSelectionBehavior(QAbstractItemView.SelectRows)
        vt.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for c, width in ((0, 34), (1, 40), (3, 46), (4, 84), (5, 84)):
            vt.setColumnWidth(c, width)
        r.addWidget(vt, 1)
        split.addWidget(right)
        split.setSizes([900, 520])
        v.addWidget(split, 1)
        for table in (self.table, self.volumes):
            make_copyable(table)
        self._complete_from_library()

    # -- looking up -----------------------------------------------------------------------------------
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
        mine = sum(1 for w in self.works if any(w.have(k) for k in KINDS))
        missing = sum(w.missing(k) for w in self.works for k in KINDS if any(w.have(x) for x in KINDS))
        text = f"{author}: {len(self.works)} series / books found - you have {mine} of them"
        if missing:
            text += f", {missing} volumes missing in those"
        self.status.setText(text + ("" if not notes else "  ·  " + "; ".join(notes)))
        self.fill()

    # -- showing ---------------------------------------------------------------------------------------
    def fill(self) -> None:
        p = theme._current  # noqa: SLF001 - the theme's colours
        t = self.table
        t.setSortingEnabled(False)
        t.setRowCount(len(self.works))
        for r, w in enumerate(self.works):
            name = _Item(w.name)
            name.setData(Qt.UserRole, r)
            name.setToolTip(", ".join(KIND_LABELS[k] for k in KINDS if k in w.kinds or w.have(k)))
            t.setItem(r, 0, name)
            collecting = any(w.have(k) for k in KINDS)
            for i, kind in enumerate(KINDS):
                have, online, miss = w.have(kind), w.online(kind), w.missing(kind)
                hv = _Item(str(have) if have else "")
                hv.setData(Qt.UserRole + 1, have)
                if miss:
                    ms = _Item(f"✗{miss}" if have else str(online))
                    ms.setForeground(QColor(p.problem if have else p.check))
                    ms.setToolTip(f"{miss} missing" if have else f"{online} exist - you have none")
                else:
                    ms = _Item("✓" if have and online else "")
                    ms.setForeground(QColor(p.ready))
                ms.setData(Qt.UserRole + 1, miss)
                for it in (hv, ms):
                    it.setTextAlignment(Qt.AlignCenter)
                t.setItem(r, 1 + 2 * i, hv)
                t.setItem(r, 2 + 2 * i, ms)
            where = sorted({v.source.capitalize() for v in w.volumes} | ({"AniList"} if "AniList" in w.links else set()))
            if not where and collecting:
                where = ["only in your library"]
            t.setItem(r, 9, _Item(", ".join(where).replace("Openlibrary", "OpenLibrary")))
        t.setSortingEnabled(True)
        t.sortByColumn(0, Qt.AscendingOrder)  # A-Z: a franchise's series next to each other
        if self.works:
            t.selectRow(0)

    def show_work(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        w = self.works[self.table.item(rows[0].row(), 0).data(Qt.UserRole)]
        self.title.setText(w.name)
        bits = [f'<a href="{url}" style="color:{theme._current.accent}">{name}</a>'  # noqa: SLF001
                for name, url in w.links.items()]
        if w.status:
            bits.append({"RELEASING": "still coming out", "FINISHED": "finished"}.get(w.status, w.status.lower()))
        self.links.setText("  ·  ".join(bits))
        p = theme._current  # noqa: SLF001
        rows_out = []
        for kind in KINDS:
            mine = w.mine.get(kind, set())
            seen = set()
            for v in sorted((v for v in w.volumes if v.kind == kind), key=lambda v: (v.number is None, v.number or 0)):
                if v.number is not None and v.number in seen:
                    continue
                seen.add(v.number)
                rows_out.append((kind, v.number, v.title, v.number in mine if v.number is not None else None,
                                 v.date, v.source))
            known = {n for n in seen if n is not None}
            if w.counts.get(kind) and not known:
                rows_out.append((kind, None, f"{w.counts[kind]} volumes (AniList)", None, "", "anilist"))
            for n in sorted(mine - known):
                rows_out.append((kind, n if n else None, "in your library", True, "", ""))
        vt = self.volumes
        vt.setRowCount(len(rows_out))
        for r, (kind, n, title, have, date, source) in enumerate(rows_out):
            cells = [KIND_LABELS[kind].split()[0], "" if n is None else f"{n:g}", title,
                     "✓" if have else ("✗" if have is False else ""), date, source.capitalize()]
            for c, text in enumerate(cells):
                it = _Item(text)
                if c == 0:
                    it.setToolTip(KIND_LABELS[kind])
                elif c == 2:
                    it.setToolTip(text)
                if c == 3:
                    it.setTextAlignment(Qt.AlignCenter)
                    it.setForeground(QColor(p.ready if have else p.problem))
                vt.setItem(r, c, it)

    def refresh_theme(self) -> None:
        self.fill()


def _google_key(folders) -> str:
    """The Google Books key: the sorter's, else the Collection's (it came from the old Checker)."""
    if getattr(folders, "google_books_key", ""):
        return folders.google_books_key
    try:
        from ..collection.config import load_settings  # noqa: PLC0415
        return load_settings().google_books_key
    except (OSError, ValueError):
        return ""
