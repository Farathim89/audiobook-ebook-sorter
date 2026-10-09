"""Right-hand panel: cover, editable metadata, online matches and the files of one book."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThreadPool, QUrl, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QFrame, QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from ..core.metadata import edit_book
from ..core.models import Book, BookType
from ..core.online import apply_candidate
from ..core.text import parse_index, parse_year
from ..providers import Candidate
from ..core.online import MIN_COVER_WIDTH
from ..core.sidecars import choose_cover
from .workers import Worker

COVER_SIZE = 180
SEP = "; "

SearchFn = Callable[[Book, str], list[Candidate]]


class DetailPanel(QScrollArea):
    edited = Signal(object)  # the Book whose metadata changed
    ask_ai = Signal(object)  # the Book to ask the AI about now
    resolve_duplicates = Signal(object)

    def __init__(self, search: SearchFn):
        super().__init__()
        self.search = search
        self.book: Book | None = None
        self._worker: Worker | None = None
        self._net = QNetworkAccessManager(self)
        self._covers: dict[str, QPixmap] = {}
        self._cover_source = ""
        self._pending_cover = ""
        self.setWidgetResizable(True)
        self.setObjectName("details")
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(380)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)

        self.heading = QLabel("Select a book")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        top = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setObjectName("cover")
        cover_box = QVBoxLayout()
        cover_box.setSpacing(2)
        cover_box.addWidget(self.cover)
        self.cover_info = QLabel()  # size + where the cover comes from (what sorting will write)
        self.cover_info.setObjectName("muted")
        self.cover_info.setFixedWidth(COVER_SIZE)
        self.cover_info.setWordWrap(True)
        self.cover_info.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        cover_box.addWidget(self.cover_info)
        cover_box.addStretch()
        top.addLayout(cover_box)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignTop)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.info, 1)
        layout.addLayout(top)

        # -- editable fields --
        box = QGroupBox("Details")
        form = QFormLayout(box)
        self.f_title, self.f_authors, self.f_series = QLineEdit(), QLineEdit(), QLineEdit()
        self.f_index, self.f_narrators, self.f_year, self.f_asin = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.f_authors.setPlaceholderText("Main author first; separate with ;")
        self.f_narrators.setPlaceholderText("separate with ;")
        self.f_index.setPlaceholderText("e.g. 1 or 2.5")
        self.f_manga = QCheckBox("Manga")
        self.f_manga.setToolTip('Manga goes to the Manga folder and its series gets "(Manga)" instead of "(Comic)"')
        for label, w in (("Title", self.f_title), ("Author(s)", self.f_authors), ("Series", self.f_series),
                         ("Number", self.f_index), ("Narrator(s)", self.f_narrators), ("Year", self.f_year),
                         ("ASIN", self.f_asin)):
            form.addRow(label, w)
            w.returnPressed.connect(self._save)
        form.addRow("", self.f_manga)
        buttons = QHBoxLayout()
        self.b_save = QPushButton("Save changes")
        self.b_save.setObjectName("primary")
        self.b_save.clicked.connect(self._save)
        self.b_reset = QPushButton("Reset")
        self.b_reset.clicked.connect(lambda: self.show_book(self.book, *self._shown_with))
        buttons.addWidget(self.b_save)
        buttons.addWidget(self.b_reset)
        buttons.addStretch()
        form.addRow(buttons)
        layout.addWidget(box)

        # -- the AI's answer (Settings > AI) --
        box = self.ai_box = QGroupBox("AI")
        v = QVBoxLayout(box)
        self.ai_text = QLabel()
        self.ai_text.setWordWrap(True)
        self.ai_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.ai_text)
        row = QHBoxLayout()
        self.b_ai_accept = QPushButton("Accept")
        self.b_ai_accept.setObjectName("primary")
        self.b_ai_accept.setToolTip("Use the AI's answer - like Save changes, and the app learns it as a rule")
        self.b_ai_accept.clicked.connect(self._accept_ai)
        self.b_ai_ask = QPushButton("Ask AI")
        self.b_ai_ask.setToolTip("Ask the AI about this book now (Settings > AI)")
        self.b_ai_ask.clicked.connect(lambda: self.book and self.ask_ai.emit(self.book))
        row.addWidget(self.b_ai_accept)
        row.addWidget(self.b_ai_ask)
        row.addStretch()
        v.addLayout(row)
        layout.addWidget(box)

        # -- online matches --
        box = QGroupBox("Online matches")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("title [author]")
        self.q.returnPressed.connect(self._search)
        self.b_search = QPushButton("Search")
        self.b_search.clicked.connect(self._search)
        row.addWidget(self.q, 1)
        row.addWidget(self.b_search)
        v.addLayout(row)
        self.matches = QListWidget()
        self.matches.setWordWrap(True)
        self.matches.setMinimumHeight(120)
        self.matches.itemDoubleClicked.connect(self._use_match)
        v.addWidget(self.matches)
        self.b_use = QPushButton("Use selected match")
        self.b_use.clicked.connect(lambda: self._use_match(self.matches.currentItem()))
        v.addWidget(self.b_use)
        layout.addWidget(box)

        # -- files and notes --
        box = QGroupBox("Files")
        v = QVBoxLayout(box)
        self.files = QListWidget()
        self.files.setMinimumHeight(100)
        v.addWidget(self.files)
        layout.addWidget(box)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setObjectName("notes")
        layout.addWidget(self.notes)
        self.b_dupes = QPushButton("Choose which copy to keep…")
        self.b_dupes.clicked.connect(lambda: self.book and self.resolve_duplicates.emit(self.book))
        layout.addWidget(self.b_dupes)
        layout.addStretch()

        self.setWidget(body)
        self.show_book(None)

    # ---------------------------------------------------------------------
    def show_book(self, book: Book | None, problems: list[str] | None = None, has_duplicates: bool = False) -> None:
        self.book = book
        self._shown_with = (problems, has_duplicates)  # for Reset
        self.b_dupes.setVisible(has_duplicates)
        self._enable_editing()
        self.matches.clear()
        self.files.clear()
        if book is None:
            self.heading.setText("Select a book")
            self.info.clear()
            self.notes.clear()
            self.cover.clear()
            self.cover_info.clear()
            for f in (self.f_title, self.f_authors, self.f_series, self.f_index, self.f_narrators, self.f_year, self.f_asin):
                f.clear()
            return
        m = book.meta
        self.heading.setText(m.title or book.main_file.name)
        where = book.folder or book.main_file
        sources = ", ".join(f"{k}: {v}" for k, v in sorted(book.sources.items()) if k in ("title", "authors", "series"))
        self.info.setText(f"{book.type.value} · {len(book.files)} file(s)"
                          + (f" + {len(book.extras)} extra" if book.extras else "")
                          + f"\n{where}\n\n{sources}")
        self.f_title.setText(m.title or "")
        self.f_authors.setText(SEP.join(m.authors))
        self.f_series.setText(m.series or "")
        self.f_index.setText(f"{m.series_index:g}" if m.series_index is not None else "")
        self.f_narrators.setText(SEP.join(m.narrators))
        self.f_year.setText(str(m.year or ""))
        self.f_asin.setText(m.asin or "")
        self.f_manga.setVisible(book.type is BookType.COMIC)
        self.f_manga.setChecked(book.manga)
        self.q.setText(" ".join(x for x in (m.title, book.author) if x))
        self._show_candidates(book.candidates)
        base = book.folder or book.main_file.parent
        for f in [*book.files, *book.extras]:
            try:
                self.files.addItem(str(f.relative_to(base)))
            except ValueError:
                self.files.addItem(str(f))
        self.notes.setText("\n".join(book.notes + (problems or [])))
        self._show_ai(book)
        self._load_cover(book)

    def _show_ai(self, book: Book) -> None:
        from ..core.ai import summary  # noqa: PLC0415
        a = book.ai or {}
        used = bool(a) and not a.get("rejected") and "ai" in book.sources.values()
        if not a:
            self.ai_text.setText("Not asked. The AI is asked about unsure books when it is on (Settings > AI).")
        elif a.get("rejected"):
            self.ai_text.setText(f"Answer not used: {a['rejected']}")
        else:
            self.ai_text.setText(("✔ Used: " if used else "Suggests: ") + summary(a)
                                 + f"\n{a.get('confidence', 0):.0%} sure - {a.get('reason', '')}")
        self.b_ai_accept.setVisible(bool(a) and not a.get("rejected") and not used)

    def _accept_ai(self) -> None:
        if not self.book or not self.book.ai:
            return
        import copy  # noqa: PLC0415
        from ..core.ai import use_answer  # noqa: PLC0415
        from ..core.rules import book_kind  # noqa: PLC0415
        self.before_edit = (copy.deepcopy(self.book.meta), book_kind(self.book))
        use_answer(self.book, self.book.ai)
        self.edited.emit(self.book)

    def _show_candidates(self, candidates: list[Candidate]) -> None:
        self.matches.clear()
        for c in candidates:
            item = QListWidgetItem(f"{'✔ ' if c.confident else ''}{c.score:.2f}  [{c.provider}]  {c.label}")
            item.setData(Qt.UserRole, c)
            item.setToolTip(c.url)
            self.matches.addItem(item)
        if not candidates:
            self.matches.addItem("No matches yet - use Look up online or Search.")

    def _save(self) -> None:
        if not self.book:
            return
        split = lambda s: [x.strip() for x in s.split(";") if x.strip()]  # noqa: E731
        import copy  # noqa: PLC0415
        from ..core.rules import book_kind  # noqa: PLC0415
        self.before_edit = (copy.deepcopy(self.book.meta), book_kind(self.book))
        edit_book(self.book,
                  title=self.f_title.text().strip(),
                  authors=split(self.f_authors.text()),
                  series=self.f_series.text().strip(),
                  series_index=parse_index(self.f_index.text()) if self.f_index.text().strip() else None,
                  narrators=split(self.f_narrators.text()),
                  year=parse_year(self.f_year.text()),
                  asin=self.f_asin.text().strip(),
                  manga=self.f_manga.isChecked())
        self.edited.emit(self.book)

    def _search(self) -> None:
        if not self.book or not self.q.text().strip():
            return
        book, query = self.book, self.q.text().strip()
        self.b_search.setEnabled(False)
        self.matches.clear()
        self.matches.addItem("Searching…")
        self._worker = Worker(lambda w: self.search(book, query))
        self._worker.signals.finished.connect(lambda found, b=book: self._search_done(b, found))
        self._worker.signals.failed.connect(lambda err: (self.matches.clear(), self.matches.addItem(err),
                                                         self.b_search.setEnabled(True)))
        QThreadPool.globalInstance().start(self._worker)

    def _search_done(self, book: Book, found: list[Candidate]) -> None:
        book.candidates = found
        self.b_search.setEnabled(True)
        if book is self.book:
            self._show_candidates(found)

    def _use_match(self, item: QListWidgetItem | None) -> None:
        cand = item.data(Qt.UserRole) if item else None
        if self.book and isinstance(cand, Candidate):
            apply_candidate(self.book, cand)
            self.edited.emit(self.book)

    def set_busy(self, busy: bool) -> None:
        """While the app works in the background: no edits, but scrolling and reading still work."""
        self._busy = busy
        self._enable_editing()

    def _enable_editing(self) -> None:
        enabled = self.book is not None and not getattr(self, "_busy", False)
        for w in (self.f_title, self.f_authors, self.f_series, self.f_index, self.f_narrators, self.f_year,
                  self.f_asin, self.f_manga, self.b_save, self.b_reset, self.q, self.b_search, self.b_use,
                  self.b_dupes, self.matches):
            w.setEnabled(enabled)

    def _load_cover(self, book: Book) -> None:
        """Show the cover sorting will write (choose_cover), with its size and source."""
        self.cover.setText("no cover")
        self._pending_cover = ""
        try:
            choice = choose_cover(book)
        except OSError:
            choice = None
        if choice is None:
            self.cover_info.setText("no cover found")
            return
        self._cover_source = choice.source
        if choice.url and choice.data is None and choice.path is None:
            self._pending_cover = choice.url
            self.cover_info.setText(f"loading…\n{choice.source}")
            if choice.url in self._covers:
                self._set_cover(self._covers[choice.url])
                return
            reply = self._net.get(QNetworkRequest(QUrl(choice.url)))
            reply.finished.connect(lambda r=reply, u=choice.url: self._cover_reply(r, u))
            return
        pix = QPixmap(str(choice.path)) if choice.data is None else QPixmap()
        if choice.data is not None:
            pix.loadFromData(choice.data)
        self._set_cover(pix)

    def _cover_reply(self, reply: QNetworkReply, url: str) -> None:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            return
        pix = QPixmap()
        pix.loadFromData(reply.readAll())
        self._covers[url] = pix
        if url == self._pending_cover:
            self._set_cover(pix)

    def _set_cover(self, pix: QPixmap) -> None:
        if pix.isNull():
            self.cover_info.setText(f"unreadable image\n{self._cover_source}")
            return
        small = pix.width() < MIN_COVER_WIDTH
        size = f"{pix.width()}×{pix.height()}" + (" – low-res" if small else "")
        self.cover_info.setText(f"{size}\n{self._cover_source}")
        self.cover_info.setStyleSheet("color: #e0a030; font-weight: bold;" if small else "")
        if not pix.isNull():
            self.cover.setPixmap(pix.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
