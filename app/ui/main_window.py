"""Main window: scan -> review/edit -> sort, with history/undo."""
from __future__ import annotations

import logging
import os
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSettings, QThreadPool, QTime, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QScrollArea, QStackedWidget,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QTableView, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from ..config import SETTINGS_FILE, load_folders, save_folders
from ..core import m4b
from ..core.models import BookType
from ..core.auto import auto_sort, find_duplicates
from ..core.duplicates import DupMove
from ..core.consistency import unify_names
from ..core.library import (LibraryEditor, apply_manga_refresh, edit_sorted_book, find_xhe_aac, load_sorted_book,
                            apply_cover_upgrades, empty_done, find_sorted_originals, move_duplicates,
                            plan_cover_upgrades, move_sorted_originals,
                            plan_manga_refresh,
                            reorganize_audio, reorganize_books)
from ..core.tools import TOOLS_DIR, download_ffmpeg
from ..core.executor import Executor
from ..core.history import Processed
from ..core.models import Book
from ..core.online import OnlineLookup
from ..core.pipeline import load_books
from ..core.planner import Planner
from .book_model import (COL_AUTHOR, COL_CHECK, COL_SERIES, COL_STATUS, COL_TARGET, COL_TITLE, COL_TYPE,
                         BookFilter, BookModel)
from .detail_panel import DetailPanel
from ..core.abs_collections import apply_collection_sort, plan_collection_sort
from ..core.compare import HAVE, HAVE_NOT, apply_compare, libraries_of, plan_compare
from ..providers.abs import AbsClient
from .dialogs import DuplicateDialog, EditSortedDialog, HistoryDialog, SettingsDialog
from .help import HelpDialog
from ..portable import ui_settings
from .sidebar import Sidebar, tool_card
from . import theme
from .workers import Worker

log = logging.getLogger(__name__)

STATE_FILTERS = [("All", "all"), ("Ready", "ready"), ("Check", "check"), ("Review", "review"),
                 ("Problems", "problem"), ("Unknown", "unknown"), ("Skipped", "skipped"), ("Sorted", "sorted")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AudioBooks & EBook Sorter")
        self.folders = load_folders()
        self.processed = Processed(self.folders.state / "processed.txt")
        self._lookup: OnlineLookup | None = None
        self._worker: Worker | None = None

        self.model = BookModel(self.folders)
        self.proxy = BookFilter(self.model)
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self.a_details.toggled.connect(self.panel.setVisible)
        self._fit_to_screen()
        self._restore_filters()
        self._set_busy(False)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.run_auto)
        if self.folders.auto_minutes:
            self.a_auto.setChecked(True)

    # -- layout ------------------------------------------------------------
    def _build_toolbar(self) -> None:
        """Menu bar for everything, a slim toolbar for the everyday actions."""
        A = lambda text, slot=None, key=None, tip=None, checkable=False: self._action(text, slot, key, tip, checkable)  # noqa: E731
        self.a_scan = A("&Scan", self.scan, "F5", "Read the Incoming folders (nothing is changed)")
        self.a_lookup = A("&Look up online", self.lookup_online, "Ctrl+L",
                          "Audible / Storytel for audiobooks, AniList for manga and light novels")
        self.a_sort = A("S&ort ticked books", self.sort_books, "Ctrl+Return")
        self.a_settings = A("Se&ttings…", self.show_settings, "Ctrl+,")
        self.a_history = A("&History / Undo…", self.show_history, "Ctrl+H")
        self.a_auto = A("&Auto-sort (sure books only)", None, None,
                        "Every few minutes: scan Incoming and sort the books that are Ready "
                        "(sure match, no problems). Everything else waits for you.", checkable=True)
        self.a_auto.toggled.connect(self._toggle_auto)
        self.a_m4b = A("MP3 → M4B while sorting", None, None, None, checkable=True)

        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.a_scan, self.a_lookup, self.a_sort])
        m.addSeparator()
        m.addAction(A("Open &Incoming folders", lambda: self._open(self.folders.audio_incoming, self.folders.books_incoming)))
        m.addAction(A("Open s&orted folders", lambda: self._open(self.folders.audio_sorted, self.folders.books_sorted)))
        m.addSeparator()
        m.addAction(self.a_settings)
        m.addSeparator()
        m.addAction(A("E&xit", self.close, "Ctrl+Q"))

        m = mb.addMenu("&Edit")
        m.addAction(A("&Find…", lambda: (self.search.setFocus(), self.search.selectAll()), "Ctrl+F"))
        m.addSeparator()
        m.addAction(A("Tick &ready books", lambda: self._tick_visible(None), "Ctrl+R"))
        m.addAction(A("Tick &all shown", lambda: self._tick_visible(True), "Ctrl+Shift+A"))
        m.addAction(A("&Untick all shown", lambda: self._tick_visible(False), "Ctrl+Shift+U"))
        m.addAction(A("&Skip / unskip selected", lambda: self._set_skipped(
            self._selected_books(), not all(b in self.model.skipped for b in self._selected_books())), "Delete"))
        m.addAction(A("&Mark selected as already sorted", lambda: self.mark_sorted(self._selected_books(), True), "Ctrl+M"))
        m.addAction(A("U&nmark selected (not sorted yet)", lambda: self.mark_sorted(self._selected_books(), False)))
        m.addSeparator()
        m.addAction(self.a_history)

        m = mb.addMenu("&View")
        self.a_show_sorted = A("Show &sorted books", None, None, None, checkable=True)
        self.a_show_sorted.toggled.connect(lambda on: self.proxy.set_filter(show_sorted=on))
        m.addAction(self.a_show_sorted)
        self.a_details = A("&Details panel", None, "Ctrl+D", None, checkable=True)
        self.a_details.setChecked(True)
        m.addAction(self.a_details)
        self.a_toolbar = A("&Toolbar", None, None, None, checkable=True)
        self.a_toolbar.setChecked(True)
        m.addAction(self.a_toolbar)
        m.addSeparator()
        themes = m.addMenu("&Colour theme")
        group = QActionGroup(self)
        for name in theme.THEMES:
            a = A(name, lambda _=False, n=name: self.set_theme(n), None, None, checkable=True)
            a.setChecked(name == self.folders.theme)
            group.addAction(a)
            themes.addAction(a)

        m = mb.addMenu("&Tools")
        m.addAction(self.a_m4b)
        m.addAction(self.a_auto)
        m.addSeparator()
        m.addAction(A("Convert selected audiobooks to M4B (no sorting)…", lambda: self.convert_only(self._selected_books())))
        m.addAction(A("Convert a sorted audiobook to M4B…", self.m4b_in_library))
        m.addAction(A("Fix xHE-AAC files Audiobookshelf can't play…", self.fix_xhe))
        m.addAction(A("Update sorted manga (official covers, volume titles)…", self.refresh_manga))
        m.addAction(A("Improve covers of sorted books (missing / low-res)…", self.improve_covers))
        m.addAction(A("Sort Audiobookshelf collections (reading order)…", self.sort_collections))
        m.addAction(A("Compare a folder with my libraries (Have / Do Not Have)…", self.compare_folder))
        m.addAction(A("Move duplicates out of Incoming (keep the best copy)…", self.move_duplicates))
        m.addAction(A("Clear sorted originals out of Incoming (to Done)…", self.clean_originals))
        m.addAction(A("&Edit a sorted book (author, title, series…)…", self.edit_sorted, "Ctrl+E"))
        m.addSeparator()
        m.addAction(A("Split Books library into Manga / Light Novels / EBooks…", self.reorganize_books))
        m.addAction(A("Split AudioBooks library into Light Novels / Audiobooks…",
                      lambda: self.reorganize_books(audio=True)))

        m = mb.addMenu("&Help")
        m.addAction(A("&How to use", lambda: HelpDialog(self).exec(), "F1"))
        m.addSeparator()
        m.addAction(A("Open &app data folder (history, cache, logs)", lambda: self._open(self.folders.state)))
        m.addAction(A("&Download ffmpeg (for M4B and the xHE-AAC fix)…", lambda: self.download_ffmpeg(ask=True)))
        m.addSeparator()
        m.addAction(A("&About", self._about))

        sb = self.sidebar = Sidebar(STATE_FILTERS)
        self.a_toolbar.setText("&Sidebar")
        self.a_toolbar.toggled.connect(sb.setVisible)
        sb.b_scan.clicked.connect(self.scan)
        sb.b_lookup.clicked.connect(lambda: self.lookup_online())
        sb.b_help.clicked.connect(lambda: HelpDialog(self).exec())
        self.mode = sb.mode
        self.mode.setCurrentIndex(1 if self.folders.move_originals else 0)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.cb_m4b = sb.cb_m4b
        self.cb_m4b.setToolTip("While sorting: audiobooks made of several files become one .m4b with chapters\n"
                               "(Audible chapter names when they match, else cleaned-up file names).\n"
                               "Single .m4b files get Audible's chapter names. Originals are always kept.")
        self.cb_m4b.setChecked(self.folders.to_m4b)
        self.a_m4b.setChecked(self.folders.to_m4b)
        self.cb_m4b.toggled.connect(self._m4b_toggled)
        self.cb_m4b.toggled.connect(self.a_m4b.setChecked)
        self.a_m4b.toggled.connect(self.cb_m4b.setChecked)
        self.b_sort = sb.b_sort
        self.b_sort.clicked.connect(self.sort_books)

    def _action(self, text, slot=None, key=None, tip=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if slot is not None:
            a.triggered.connect(slot)
        if key:
            a.setShortcut(QKeySequence(key))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        return a

    def _open(self, *folders: Path) -> None:
        for f in folders:
            if Path(f).is_dir():
                os.startfile(str(f))

    def _about(self) -> None:
        f = self.folders
        QMessageBox.about(self, "About", "<b>AudioBooks &amp; EBook Sorter</b><br>by Farathim<br><br>"
                          "Sorts audiobooks, ebooks, manga and light novels into an Audiobookshelf-friendly "
                          "layout. Incoming is only copied from; protected folders are never written to.<br><br>"
                          f"Settings: {SETTINGS_FILE}<br>App data: {f.state}")

    def _restore_filters(self) -> None:
        """The search text, state filter and View > Show sorted books from last time."""
        saved = ui_settings()
        self.search.setText(str(saved.value("view/search", "") or ""))
        state = str(saved.value("view/state", "all") or "all")
        keys = [k for _, k in STATE_FILTERS]
        if state in keys:
            self.state_filter.setCurrentIndex(keys.index(state))
        self.a_show_sorted.setChecked(str(saved.value("view/show_sorted", "false")).lower() == "true")

    def _fit_to_screen(self) -> None:
        """Restore the last window size, or fit the screen (the old fixed 1500x860 was too tall on scaled displays)."""
        geo = ui_settings().value("window/geometry")
        if geo is not None and self.restoreGeometry(geo):
            return
        avail = self.screen().availableGeometry()
        # fits a 720p screen (1280x720) - bigger screens just get room around it
        w, h = min(1280, int(avail.width() * 0.95)), min(720, int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)

    def reorganize_books(self, audio: bool = False) -> None:
        lookup, folders = self.lookup, self.folders
        split = reorganize_audio if audio else reorganize_books
        root = folders.audio_sorted if audio else folders.books_sorted

        def preview(w: Worker):
            return split(folders, lookup.is_light_novel, dry_run=True)

        def confirm(res):
            if not res.moved:
                QMessageBox.information(self, "Split Books library", "Nothing to move - it is already split.")
                return
            text = "\n".join(res.moved[:40]) + ("\n…" if len(res.moved) > 40 else "")
            if QMessageBox.question(self, "Split Books library",
                                    f"Move these folders inside {root}?\n\n{text}\n\n"
                                    "Nothing is overwritten and it can be undone from History.") != QMessageBox.Yes:
                return
            self._run(lambda w: split(folders, lookup.is_light_novel), done, "Moving folders…")

        def done(res):
            msg = f"{len(res.moved)} folders moved, {len(res.merged)} merged. Run id: {res.run_id}"
            if res.skipped:
                msg += "\n\nSkipped:\n" + "\n".join(res.skipped[:15])
            msg += ("\n\nIn Audiobookshelf, add the new sub-folders (Manga, Light Novels, Audiobooks…) as the "
                    "library's folders, or make one library per kind, then Scan.")
            QMessageBox.information(self, "Split Books library", msg)

        self._run(preview, confirm, "Checking the Books library…")

    def edit_sorted(self) -> None:
        start = str(self.folders.audio_sorted if self.folders.audio_sorted.is_dir() else self.folders.books_sorted)
        folder = QFileDialog.getExistingDirectory(self, "Choose the book's folder in the sorted library", start)
        if not folder:
            return
        try:
            book = load_sorted_book(self.folders, Path(folder))
        except ValueError as e:
            QMessageBox.warning(self, "Edit", str(e))
            return
        if book is None:
            QMessageBox.information(self, "Edit", "No book found in that folder - choose the book's own folder.")
            return
        dlg = EditSortedDialog(book, self)
        if not dlg.exec():
            return
        folders = self.folders
        self._run(lambda w: edit_sorted_book(folders, Path(folder), dlg.values),
                  lambda r: QMessageBox.information(self, "Edit", f"Saved.\n{r[1]}\n\nUndo from History (run {r[0]})."),
                  "Updating the book…")

    def compare_folder(self) -> None:
        f = self.folders
        if not libraries_of(f):
            QMessageBox.information(self, "Compare", "Add your library folders first: Settings > Safety.")
            return
        start = str(f.audio_incoming.parent if f.audio_incoming.parent.is_dir() else Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Folder to compare with your libraries", start)
        if not folder:
            return
        source = Path(folder)
        if f.is_protected(source):
            QMessageBox.warning(self, "Compare", "That is one of your libraries - pick a folder to check against them.")
            return

        def confirm(result):
            if not result.have and not result.have_not:
                QMessageBox.information(self, "Compare", "No books found in that folder.")
                return
            have = "\n".join(f"• {b.meta.title}" for b, _ in result.have[:12]) + ("\n…" if len(result.have) > 12 else "")
            text = (f"Compared with {result.library_books} books in your libraries:\n\n"
                    f"Have already: {len(result.have)}\nDo not have: {len(result.have_not)}\n\n"
                    f"Already have, e.g.:\n{have}\n\nMove them into\n{source / HAVE}\n{source / HAVE_NOT}\n"
                    "sorted as AudioBooks / Books \\ kind \\ author \\ series? Undo from History.")
            if QMessageBox.question(self, "Compare", text) != QMessageBox.Yes:
                return
            self._run(lambda w: apply_compare(f, result, source),
                      lambda r: QMessageBox.information(self, "Compare", f"{r[1]} files moved.\nUndo from History (run {r[0]})."),
                      "Sorting into Have / Do Not Have…")

        self._run(lambda w: plan_compare(f, [source], lambda i, n, t: w.report(i, n, t)), confirm,
                  "Comparing with your libraries…")

    def sort_collections(self) -> None:
        f = self.folders
        if not f.abs_url or not f.abs_api_key:
            QMessageBox.information(self, "Collections", "Set the Audiobookshelf address and API key first:\n"
                                    "Settings > Online. (In Audiobookshelf: Settings > API Keys.)")
            return
        client = AbsClient(f.abs_url, f.abs_api_key)

        def confirm(changes):
            if not changes:
                QMessageBox.information(self, "Collections", "All collections are already in reading order.")
                return
            text = "\n\n".join(f"• {c.name} ({c.books} books):\n   " + "\n   ".join(c.first) + "\n   …"
                                 for c in changes[:10])
            if QMessageBox.question(self, "Collections", f"Put {len(changes)} collection(s) in reading order?\n"
                                    "Their own series by number, then other series, then stand-alone books. "
                                    f"Nothing is added or removed.\n\n{text}") != QMessageBox.Yes:
                return
            self._run(lambda w: apply_collection_sort(client, changes),
                      lambda n: QMessageBox.information(self, "Collections", f"{n} collection(s) sorted."),
                      "Sorting collections…")

        self._run(lambda w: plan_collection_sort(client), confirm, "Reading Audiobookshelf collections…")

    def improve_covers(self) -> None:
        folders, lookup = self.folders, self.lookup

        def confirm(ups):
            if not ups:
                QMessageBox.information(self, "Covers", "No sorted book needs a better cover (or none was found).")
                return
            text = "\n".join(f"• {u.folder.name}: {u.before} → {u.why}" for u in ups[:25])
            text += "\n…" if len(ups) > 25 else ""
            if QMessageBox.question(self, "Covers", f"Replace {len(ups)} covers?\n\n{text}\n\n"
                                    "The old covers are kept (undo from History).") != QMessageBox.Yes:
                return
            self._run(lambda w: apply_cover_upgrades(folders, ups),
                      lambda r: QMessageBox.information(self, "Covers", f"{r[1]} covers replaced.\n"
                                                        f"Undo from History (run {r[0]})."),
                      "Writing covers…")

        self._run(lambda w: plan_cover_upgrades(folders, lookup, w.cancelled, lambda i, n, t: w.report(i, n, t)),
                  confirm, "Checking covers…")

    def clean_originals(self) -> None:
        folders = self.folders

        def confirm(found):
            books, orphans = found
            if not books and not orphans:
                QMessageBox.information(self, "Done folder", "Incoming has no sorted originals or leftovers.")
                return
            lines = [f"• {b.meta.title or b.main_file.name}" for b in books]
            lines += [f"• {d.name}  (only cover / metadata left)" for d, _ in orphans]
            text = "\n".join(lines[:25]) + ("\n…" if len(lines) > 25 else "")
            if QMessageBox.question(self, "Done folder", f"Move {len(books)} sorted books and {len(orphans)} leftover "
                                    f"folders to\n{folders.done}?\n\n{text}\n\nNothing is deleted - undo from History."
                                    ) != QMessageBox.Yes:
                return
            self._run(lambda w: move_sorted_originals(folders, books, orphans, w.cancelled,
                                                      lambda i, n, t: w.report(i, n, t)),
                      lambda r: (QMessageBox.information(self, "Done folder", f"{r[1]} moved to {folders.done}.\n"
                                                         f"Undo from History (run {r[0]})."), self.scan()),
                      "Moving sorted originals…")

        self._run(lambda w: find_sorted_originals(folders), confirm, "Looking for sorted originals…")

    def move_duplicates(self, moves: list | None = None) -> None:
        folders, lookup = self.folders, self.lookup

        def find(w: Worker):
            return find_duplicates(folders, lookup, lambda i, n, b: w.report(i, n, b.meta.title or ""), w.cancelled)

        def confirm(found):
            moves, better = found
            if not moves:
                msg = "No duplicates to move."
                if better:
                    msg += "\n\nBetter than the library copy (kept):\n" + "\n".join(b.meta.title or "" for b, _ in better)
                QMessageBox.information(self, "Duplicates", msg)
                return
            text = "\n".join(f"• {m.book.meta.title}: {m.reason}" for m in moves[:25])
            if len(moves) > 25:
                text += "\n…"
            if better:
                text += "\n\nKept, better than the library copy:\n" + "\n".join(f"• {b.meta.title}" for b, _ in better)
            if QMessageBox.question(self, "Duplicates", f"Move {len(moves)} copies to\n{folders.duplicates}?\n\n{text}\n\n"
                                    "Nothing is deleted - undo from History.") != QMessageBox.Yes:
                return
            go(moves)

        def go(moves):
            self._run(lambda w: move_duplicates(folders, moves, w.cancelled, lambda i, n, t: w.report(i, n, t)),
                      lambda r: (QMessageBox.information(self, "Duplicates", f"{r[1]} copies moved to {folders.duplicates}.\n"
                                                         f"Undo from History (run {r[0]})."), self.scan()),
                      "Moving duplicates…")

        if moves is not None:
            go(moves)
        else:
            self._run(find, confirm, "Looking for duplicates…")

    def refresh_manga(self) -> None:
        folders, lookup = self.folders, self.lookup

        def find(w: Worker):
            return plan_manga_refresh(folders, lookup, w.cancelled, lambda i, n, t: w.report(i, n, t))

        def confirm(changes):
            if not changes:
                QMessageBox.information(self, "Manga", "Nothing to update - covers and titles are up to date.")
                return
            text = "\n".join(c.summary for c in changes[:30]) + ("\n…" if len(changes) > 30 else "")
            if QMessageBox.question(self, "Manga", f"Update {len(changes)} volumes?\n\n{text}\n\n"
                                    "Old covers and metadata are parked - undo from History.") != QMessageBox.Yes:
                return
            self._run(lambda w: apply_manga_refresh(folders, changes, w.cancelled,
                                                    lambda i, n, t: w.report(i, n, t)),
                      lambda r: QMessageBox.information(self, "Manga", f"{r[1]} volumes updated. Scan the library in Audiobookshelf.\nUndo from History (run {r[0]})."), "Updating manga…")

        self._run(find, confirm, "Checking MangaDex and the wikis…")

    def fix_xhe(self) -> None:
        if not self._have_ffmpeg():
            return
        folders = self.folders

        def find(w: Worker):
            return find_xhe_aac(folders.audio_sorted)

        def confirm(files):
            if not files:
                QMessageBox.information(self, "xHE-AAC", "No xHE-AAC files in the audiobook library.")
                return
            names = "\n".join(f.name for f in files[:30])
            if QMessageBox.question(self, "xHE-AAC", f"These {len(files)} files use xHE-AAC, which Audiobookshelf's ffmpeg can't play:\n\n{names}\n\nRe-encode them to plain AAC? The originals are parked and it can be undone from History.") != QMessageBox.Yes:
                return

            def work(w: Worker):
                with LibraryEditor(folders, "fix-xhe") as ed:
                    for i, f in enumerate(files, 1):
                        w.report(i, len(files), f.name)
                        ed.fix_xhe_aac(f, w.cancelled)
                    return ed.run_id

            self._run(work, lambda run_id: QMessageBox.information(
                self, "xHE-AAC", f"{len(files)} files fixed. Scan the library in Audiobookshelf.\nUndo from History (run {run_id})."), "Re-encoding…")

        self._run(find, confirm, "Looking for xHE-AAC files…")

    def _have_ffmpeg(self) -> bool:
        """True when ffmpeg is there; otherwise offer to download it (try the action again afterwards)."""
        if m4b.ffmpeg():
            return True
        self.download_ffmpeg(ask=True)
        return False

    def download_ffmpeg(self, ask: bool = False) -> None:
        if ask and QMessageBox.question(
                self, "ffmpeg", "This needs ffmpeg (free audio tool), which isn't installed.\n\n"
                "Download it now? About 100 MB from gyan.dev (the official Windows builds), checked against "
                f"its published checksum, saved in\n{TOOLS_DIR}") != QMessageBox.Yes:
            return

        def work(w: Worker):
            return download_ffmpeg(lambda done, total: w.report(done // 1_048_576, max(total // 1_048_576, 1),
                                                                "MB"), w.cancelled)

        self._run(work, lambda folder: QMessageBox.information(
            self, "ffmpeg", f"ffmpeg is ready ({folder}).\nYou can use MP3 → M4B and the xHE-AAC fix now."),
            "Downloading ffmpeg…")

    def m4b_in_library(self) -> None:
        if not self._have_ffmpeg():
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a sorted audiobook folder", str(self.folders.audio_sorted))
        if not folder:
            return
        folder = Path(folder)
        book = next((b for b in load_books([folder.parent]) if b.folder == folder and b.type is BookType.AUDIO), None)
        if book is None or all(f.suffix.lower() == ".m4b" for f in book.files):
            QMessageBox.information(self, "M4B", "That folder has no multi-file audiobook.")
            return
        folders, http = self.folders, self.lookup.audible.http

        def work(w: Worker):
            with LibraryEditor(folders, "m4b") as ed:
                out = ed.convert_to_m4b(book, http, on_progress=lambda f: w.report(int(f * 100), 100, book.meta.title or ""))
                return out, ed.run_id

        self._run(work, lambda r: QMessageBox.information(
            self, "M4B", f"Made {r[0].name}.\nThe old audio files and ABS's metadata.json were parked.\n"
                         f"Undo from History (run {r[1]})."), "Converting to m4b…")

    def set_theme(self, name: str) -> None:
        theme.apply(name)
        self.folders.theme = name
        save_folders(self.folders)
        self.model.refresh()  # status colours come from the theme
        self.sidebar.refresh_theme()

    def _build_body(self) -> None:
        left = QWidget()
        v = QVBoxLayout(left)
        v.setContentsMargins(8, 8, 4, 8)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Search author, title, series, path…")
        self.search.setObjectName("search")
        self.search.textChanged.connect(lambda t: self.proxy.set_filter(text=t))
        self.state_filter = QComboBox()
        for label, _ in STATE_FILTERS:
            self.state_filter.addItem(label)
        self.state_filter.currentIndexChanged.connect(self._state_filter_changed)
        bar.addWidget(self.search, 1)
        self.state_filter.hide()  # the sidebar's Books list replaces it
        for label, on in (("Tick ready", None), ("Tick all", True), ("Untick all", False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, on=on: self._tick_visible(on))
            bar.addWidget(b)
        v.addLayout(bar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_AUTHOR, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        for col, width in ((COL_CHECK, 28), (COL_STATUS, 96), (COL_TYPE, 120), (COL_AUTHOR, 160), (COL_SERIES, 190), (COL_TITLE, 260)):
            self.table.setColumnWidth(col, width)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._show_current())
        v.addWidget(self.table)

        self.panel = DetailPanel(self._search_online)
        self.panel.edited.connect(self._book_edited)
        self.panel.resolve_duplicates.connect(self.resolve_duplicates)

        split = self.split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([760, 330])
        self.pages = QStackedWidget()
        self.pages.addWidget(split)
        self.pages.addWidget(self._build_tools_page())
        self.page_host = QWidget()  # Settings / History live here as pages
        self.page_host.setObjectName("toolspage")
        QVBoxLayout(self.page_host).setContentsMargins(24, 20, 24, 16)
        self.pages.addWidget(self.page_host)
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        row.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.sidebar.filter_chosen.connect(lambda k: self.state_filter.setCurrentIndex(
            [key for _, key in STATE_FILTERS].index(k)))
        self.sidebar.page_chosen.connect(self._page_chosen)
        self.state_filter.currentIndexChanged.connect(
            lambda i: self.sidebar.select_filter(STATE_FILTERS[i][1]))
        self.sidebar.select_filter(STATE_FILTERS[self.state_filter.currentIndex()][1])
        # the user's column widths / sort column and the details panel width from last time
        saved = ui_settings()
        if (state := saved.value("table/header")) is not None:
            header.restoreState(state)
        if (state := saved.value("window/splitter")) is not None:
            split.restoreState(state)

        space = QAction(self, shortcut=QKeySequence(Qt.Key_Space), triggered=self._toggle_selected)
        self.table.addAction(space)

    def _build_tools_page(self) -> QWidget:
        """Every tool as a card, in groups."""
        groups = [
            ("Sorting", [
                ("Convert selected audiobooks to M4B", "Make one .m4b with chapters from the selected books, "
                 "without sorting them.", "Convert…", lambda: self.convert_only(self._selected_books())),
                ("Move duplicates out of Incoming", "Keep the best copy of each book; the extra copies and books "
                 "your library already has go to the Duplicates folder.", "Find duplicates…", self.move_duplicates),
                ("Clear sorted originals", "Move the originals of books you already sorted out of Incoming, "
                 "into the Done folder.", "Clear…", self.clean_originals),
                ("Compare a folder with my libraries", "Sort any folder into Have / Do Not Have, compared with "
                 "your Audiobookshelf libraries.", "Compare…", self.compare_folder),
            ]),
            ("Sorted library", [
                ("Edit a sorted book", "Fix the author, title, series, number or narrator of a book that is "
                 "already sorted - the folder moves to match.", "Edit…", self.edit_sorted),
                ("Improve covers", "Find bigger covers for sorted books with none or a low-res one.",
                 "Check covers…", self.improve_covers),
                ("Update sorted manga", "Official English volume covers and volume titles (MangaDex, Fandom).",
                 "Update…", self.refresh_manga),
                ("Convert a sorted audiobook to M4B", "One chaptered .m4b from a sorted multi-file audiobook.",
                 "Convert…", self.m4b_in_library),
                ("Split the Books library", "Sort a Books folder into Manga / Light Novels / EBooks / Comics.",
                 "Split…", self.reorganize_books),
                ("Split the AudioBooks library", "Sort an AudioBooks folder into Light Novels / Audiobooks.",
                 "Split…", lambda: self.reorganize_books(audio=True)),
            ]),
            ("Audiobookshelf", [
                ("Fix xHE-AAC files", "Re-encode audiobooks Audiobookshelf can't play (xHE-AAC) to plain AAC.",
                 "Find files…", self.fix_xhe),
                ("Sort collections", "Put your Audiobookshelf collections in reading order (needs the API key "
                 "in Settings).", "Sort…", self.sort_collections),
            ]),
            ("Maintenance", [
                ("Download ffmpeg", "Needed for MP3 → M4B and the xHE-AAC fix.", "Download…",
                 lambda: self.download_ffmpeg(ask=True)),
                ("App data folder", "History (undo), cache and logs.", "Open",
                 lambda: self._open(self.folders.state)),
            ]),
        ]
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("toolspage")
        v = QVBoxLayout(body)
        v.setContentsMargins(24, 20, 24, 24)
        v.setSpacing(8)
        head = QLabel("Tools")
        head.setObjectName("pagetitle")
        v.addWidget(head)
        for name, tools in groups:
            label = QLabel(name.upper())
            label.setObjectName("navgroup")
            v.addSpacing(10)
            v.addWidget(label)
            grid = QGridLayout()
            grid.setSpacing(12)
            for i, (title, text, button, slot) in enumerate(tools):
                grid.addWidget(tool_card(title, text, button, slot), i // 3, i % 3)
            for c in range(3):
                grid.setColumnStretch(c, 1)
            v.addLayout(grid)
        v.addStretch(1)
        page.setWidget(body)
        return page

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.counts = QLabel()
        sb.addWidget(self.counts, 1)
        # the text sits next to the bar (on the bar it is unreadable on half of it in some themes)
        self.progress_text = QLabel()
        sb.addPermanentWidget(self.progress_text)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(220, 10)
        sb.addPermanentWidget(self.progress)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.clicked.connect(lambda: self._worker and self._worker.cancel())
        sb.addPermanentWidget(self.b_cancel)

    # -- helpers -----------------------------------------------------------
    @property
    def lookup(self) -> OnlineLookup:
        if self._lookup is None:
            self._lookup = OnlineLookup(self.folders)
        return self._lookup

    def _run(self, fn, done, label: str) -> None:
        self._set_busy(True, label)
        self._worker = Worker(fn)
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(lambda result: (done(result), self._set_busy(False)))
        self._worker.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(self._worker)

    def _on_progress(self, i: int, n: int, text: str) -> None:
        self.progress.setMaximum(max(n, 1))
        self.progress.setValue(i)
        self.progress_text.setText(f"{i}/{n}  {text[:60]}")

    def _on_failed(self, err: str) -> None:
        self._set_busy(False)
        QMessageBox.critical(self, "Error", err)

    def _set_busy(self, busy: bool, label: str = "") -> None:
        for a in (self.a_scan, self.a_lookup, self.a_history, self.a_settings):
            a.setEnabled(not busy)
        self.b_sort.setEnabled(not busy and bool(self.model.books))
        self.a_sort.setEnabled(not busy and bool(self.model.books))
        self.a_lookup.setEnabled(not busy and bool(self.model.books))
        self.panel.set_busy(busy)  # edits locked; scrolling still works
        self.sidebar.b_scan.setEnabled(not busy)
        self.sidebar.b_lookup.setEnabled(not busy and bool(self.model.books))
        self.sidebar.b_settings.setEnabled(not busy)
        self.sidebar.b_history.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.progress_text.setVisible(busy)
        self.b_cancel.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.progress_text.setText("")
            self.counts.setText(label)
        else:
            self._update_counts()

    def _update_counts(self) -> None:
        books = self.model.books
        if not books:
            self.counts.setText("No books loaded - press Scan (F5).")
            self.sidebar.set_counts({}, 0)
            self.sidebar.set_sort_count(0)
            return
        states = Counter(self.model.state(b) for b in books)
        parts = [f"{len(books)} books"] + [f"{states[k]} {k}" for _, k in STATE_FILTERS[1:] if states[k]]
        self.counts.setText("  ·  ".join(parts) + f"  ·  {len(self.model.checked)} ticked")
        self.sidebar.set_counts(states, len(books))
        self.sidebar.set_sort_count(len(self.model.checked))

    def _selected_books(self) -> list[Book]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.book_at(r) for r in sorted(rows)]

    def _visible_books(self) -> list[Book]:
        return [self.model.book_at(self.proxy.mapToSource(self.proxy.index(r, 0)).row())
                for r in range(self.proxy.rowCount())]

    def _show_current(self) -> None:
        idx = self.table.selectionModel().currentIndex()
        if not idx.isValid():
            self.panel.show_book(None)
            return
        book = self.model.book_at(self.proxy.mapToSource(idx).row())
        plan = self.model.plan
        self.panel.show_book(book, plan.problems.get(book, []) if plan else [],
                             has_duplicates=bool(plan and plan.duplicates.get(book)))

    def _replan(self) -> None:
        for _ in range(2):  # a second pass after books turned out to be in the library already
            todo = [b for b in self.model.books if b not in self.model.done and b not in self.model.skipped]
            self.model.set_plan(Planner(self.folders).plan(todo))
            if not self._mark_identical():
                break
        self.proxy.invalidateFilter()
        self._update_counts()

    def _state_filter_changed(self, i: int) -> None:
        state = STATE_FILTERS[i][1]
        self.proxy.set_filter(state=state, show_sorted=True if state == "sorted" else None)

    def _tick_visible(self, on: bool | None) -> None:
        books = self._visible_books()
        if on is None:  # "Tick ready": ready + review, untick the rest
            self.model.set_checked(books, False)
            books = [b for b in books if self.model.state(b) in ("ready", "review")]
            on = True
        self.model.set_checked(books, on)
        self._update_counts()

    def _toggle_selected(self) -> None:
        books = self._selected_books()
        if books:
            self.model.set_checked(books, not all(b in self.model.checked for b in books))
            self._update_counts()

    def _context_menu(self, pos) -> None:
        books = self._selected_books()
        if not books:
            return
        menu = QMenu(self)
        menu.addAction("Tick", lambda: (self.model.set_checked(books, True), self._update_counts()))
        menu.addAction("Untick", lambda: (self.model.set_checked(books, False), self._update_counts()))
        menu.addSeparator()
        menu.addAction(f"Look up {len(books)} online", lambda: self.lookup_online(books))
        if any(b.type is BookType.AUDIO for b in books):
            menu.addAction("Convert to M4B only (no sorting)…", lambda: self.convert_only(books))
        if self.model.plan and self.model.plan.duplicates.get(books[0]):
            menu.addAction("Choose which copy to keep…", lambda: self.resolve_duplicates(books[0]))
        if any(b not in self.model.done for b in books):
            menu.addAction("Mark as already sorted (hide)", lambda: self.mark_sorted(books, True))
        if any(b in self.model.done for b in books):
            menu.addAction("Unmark - not sorted yet", lambda: self.mark_sorted(books, False))
        if any(b in self.model.skipped for b in books):
            menu.addAction("Don't skip", lambda: self._set_skipped(books, False))
        else:
            menu.addAction("Skip (leave out of sorting)", lambda: self._set_skipped(books, True))
        menu.addAction("Open source folder", lambda: os.startfile(str(books[0].folder or books[0].main_file.parent)))
        target = self.model.plan.targets.get(books[0]) if self.model.plan else None
        if target and target.exists():
            menu.addAction("Open target folder", lambda: os.startfile(str(target)))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _m4b_toggled(self, on: bool) -> None:
        if on and not self._have_ffmpeg():
            self.cb_m4b.setChecked(False)
            return
        self.folders.to_m4b = on
        save_folders(self.folders)

    def convert_only(self, books: list[Book]) -> None:
        books = [b for b in books if b.type is BookType.AUDIO]
        if not books:
            return
        if not self._have_ffmpeg():
            return
        start = str((books[0].folder or books[0].main_file.parent).parent)
        out_dir = QFileDialog.getExistingDirectory(self, "Save the .m4b file(s) in", start)
        if not out_dir:
            return
        http = self.lookup.audible.http

        def work(w: Worker):
            made = []
            for i, b in enumerate(books, 1):
                if w.cancelled():
                    break
                path, source = m4b.make_m4b(b, Path(out_dir), http, cancelled=w.cancelled,
                                            on_progress=lambda f, i=i, b=b: w.report(i, len(books), f"{b.meta.title} {f:.0%}"))
                made.append(f"{path.name}  (chapters from {source})")
            return made

        self._run(work, lambda made: QMessageBox.information(self, "Convert", "Made:\n" + "\n".join(made)),
                  "Converting to m4b…")

    def _mark_identical(self) -> bool:
        """Books whose files are already in the library (same size) need nothing: mark them sorted."""
        plan = self.model.plan
        new = [b for b in (plan.identical if plan else ()) if b not in self.model.done]
        if not new:
            return False
        for b in new:
            self.processed.add(b)
        self.processed.save()
        self.model.done |= set(new)
        self.model.checked -= set(new)
        self.statusBar().showMessage(f"{len(new)} book(s) are already in the library (identical files) - "
                                     "marked as sorted. View > Show sorted books to see them.", 20000)
        return True

    def mark_sorted(self, books: list[Book], on: bool) -> None:
        """Manually say a book is (not) sorted already - e.g. another tool put it in the library."""
        if not books:
            return
        for b in books:
            (self.processed.add if on else self.processed.remove)(b)
        self.processed.save()
        if on:
            self.model.done |= set(books)
            self.model.checked -= set(books)
        else:
            self.model.done -= set(books)
        self._replan()
        self._show_current()

    def _set_skipped(self, books: list[Book], on: bool) -> None:
        if on:
            self.model.skipped |= set(books)
            self.model.checked -= set(books)
        else:
            self.model.skipped -= set(books)
        self._replan()
        self._show_current()

    def resolve_duplicates(self, book: Book) -> None:
        plan = self.model.plan
        if not plan or not plan.duplicates.get(book):
            return
        dlg = DuplicateDialog(plan.duplicate_group(book), self)
        if dlg.exec() and dlg.keep is not None:
            group = plan.duplicate_group(book)
            if dlg.move_others:
                others = [b for b in group if b is not dlg.keep]
                self.move_duplicates([DupMove(b, "extra copy") for b in others])
                return
            self._set_skipped([b for b in group if b is not dlg.keep], True)
            self._set_skipped([dlg.keep], False)
            if self.model.state(dlg.keep) in ("ready", "review"):
                self.model.set_checked([dlg.keep], True)
            self._update_counts()

    def _search_online(self, book: Book, query: str):
        return self.lookup.search(book, query)

    def _book_edited(self, book: Book) -> None:
        self._replan()
        if self.model.state(book) in ("ready", "review"):
            self.model.set_checked([book], True)
        self.model.refresh(book)
        self._update_counts()
        self._show_current()

    # -- actions -----------------------------------------------------------
    def scan(self) -> None:
        empty_done(self.folders)  # Done\<day> folders past the Settings limit -> Recycle Bin
        sources = [p for p in self.folders.sources if p.is_dir()]
        if not sources:
            QMessageBox.warning(self, "Scan", "None of the source folders exist. Check Settings.")
            return
        folders = self.folders

        def work(w: Worker):
            books = load_books(sources, on_progress=lambda i, n, b: w.report(i, n, b.main_file.name))
            unify_names(books, folders.library_roots())
            done = {b for b in books if self.processed.contains(b)}
            return books, done

        def finished(result):
            books, done = result
            todo = [b for b in books if b not in done]
            self.model.set_books(books, Planner(self.folders).plan(todo), done)
            self.model.checked = {b for b in books if self.model.default_checked(b)}
            if self._mark_identical():
                self._replan()
            self.proxy.invalidate()
            if self.proxy.rowCount():
                self.table.selectionModel().setCurrentIndex(
                    self.proxy.index(0, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)

        self._run(work, finished, "Scanning…")

    def lookup_online(self, books: list[Book] | None = None, then=None) -> None:
        books = [b for b in (books or self.model.books) if b not in self.model.done]
        lookup, folders = self.lookup, self.folders

        def work(w: Worker):
            lookup.run(books, on_progress=lambda i, n, b: w.report(i, n, b.meta.title or ""), cancelled=w.cancelled)
            unify_names(self.model.books, folders.library_roots())

        def finished(_):
            self._replan()
            for b in books:  # newly sure books get ticked
                if self.model.state(b) in ("ready", "review"):
                    self.model.checked.add(b)
            self.model.refresh()
            self._update_counts()
            self._show_current()
            if then:
                then()

        self._run(work, finished, "Looking up online…")

    def _mode_changed(self, index: int) -> None:
        self.folders.move_originals = index == 1
        save_folders(self.folders)

    def _sort_after_lookup(self, fresh: list[Book], before: dict) -> None:
        """Books that were sure before the lookup but aren't any more are unticked for a look."""
        changed = [b for b in fresh if before[b] in ("ready", "review")
                   and self.model.state(b) not in ("ready", "review")]
        if any(not b.looked_up for b in fresh):  # cancelled
            return
        if changed:
            self.model.set_checked(changed, False)
            self.model.refresh()
            self._update_counts()
            names = "\n".join(f"• {b.meta.title or b.main_file.name}: {self.model.state(b)}" for b in changed[:15])
            if QMessageBox.question(self, "Sort", f"Looked them up online first. {len(changed)} are not sure any more "
                                    f"and were unticked - have a look at them:\n\n{names}\n\nSort the rest now?"
                                    ) != QMessageBox.Yes:
                return
        self.sort_books()

    def sort_books(self) -> None:
        plan = self.model.plan
        chosen = [b for b in self.model.books if b in self.model.checked
                  and self.model.state(b) not in ("problem", "sorted", "unknown", "skipped")]
        if not chosen or plan is None:
            QMessageBox.information(self, "Sort", "Tick the books to sort first.")
            return
        fresh = [b for b in chosen if not b.looked_up]
        if fresh:  # never looked up online: do that first - it can change where a book goes
            before = {b: self.model.state(b) for b in fresh}
            self.lookup_online(fresh, then=lambda: self._sort_after_lookup(fresh, before))
            return
        mode = "move" if self.mode.currentIndex() == 1 else "copy"
        n_review = sum(b in plan.review for b in chosen)
        text = (f"{mode.capitalize()} {len(chosen) - n_review} books into the sorted libraries"
                + (f" and {n_review} to Review" if n_review else "") + "?\n\n"
                + f"Audiobooks → {self.folders.audio_sorted}\nBooks → {self.folders.books_sorted}\n\n"
                + ("Originals stay where they are." if mode == "copy"
                   else f"Originals are MOVED; their leftover cover/metadata files go to {self.folders.done}.")
                + " Existing files are never overwritten, and the run can be undone from History.")
        if QMessageBox.question(self, "Sort", text) != QMessageBox.Yes:
            return
        to_m4b = self.cb_m4b.isChecked()

        def work(w: Worker):
            pos = [0, len(chosen)]

            def progress(i, n, b):
                pos[0] = i
                w.report(i, n, b.meta.title or "")

            executor = Executor(self.folders, mode, self.processed, to_m4b=to_m4b,
                                on_detail=lambda s: w.report(pos[0] + 1, pos[1], s))
            return executor.run(plan, chosen, on_progress=progress, cancelled=w.cancelled)

        def finished(result):
            self.model.done |= {r.book for r in result.results if r.status in ("done", "review")}
            self.model.checked -= self.model.done
            self._replan()
            failed = [r for r in result.results if r.status in ("failed", "skipped")]
            msg = (f"{result.count('done')} sorted, {result.count('review')} to Review, "
                   f"{result.count('skipped')} skipped, {result.count('failed')} failed.\n\nRun id: {result.run_id}")
            if failed:
                msg += "\n\n" + "\n".join(f"• {r.book.meta.title}: {r.detail}" for r in failed[:15])
            QMessageBox.information(self, "Sort finished", msg)

        self._run(work, finished, "Moving…" if mode == "move" else "Copying…")

    def _toggle_auto(self, on: bool) -> None:
        if on:
            minutes = self.folders.auto_minutes or 10
            self.auto_timer.start(minutes * 60_000)
            self.a_auto.setText(f"Auto-sort: every {minutes} min")
            QTimer.singleShot(2000, self.run_auto)
        else:
            self.auto_timer.stop()
            self.a_auto.setText("Auto-sort")

    def run_auto(self) -> None:
        if self.progress.isVisible():  # busy with something else - try again next time
            return
        folders, processed = self.folders, self.processed

        def work(w: Worker):
            return auto_sort(folders, processed, log=lambda s: w.report(0, 0, s), cancelled=w.cancelled)

        def finished(result):
            self.a_auto.setToolTip(f"Last auto-sort {QTime.currentTime().toString('HH:mm')}: {result.summary}")
            self.statusBar().showMessage(f"Auto-sort {QTime.currentTime().toString('HH:mm')}: {result.summary}", 60_000)
            if result.run and result.run.count("done"):
                QTimer.singleShot(0, self.scan)

        self._run(work, finished, "Auto-sort…")

    def _page_chosen(self, page: str) -> None:
        if page == "history":
            self.show_history()
        elif page == "settings":
            self.show_settings()
        else:
            self.pages.setCurrentIndex(1 if page == "tools" else 0)

    def _show_in_page(self, title: str, dlg) -> None:
        """Show a dialog as a page of the main area (Settings, History)."""
        layout = self.page_host.layout()
        while layout.count():
            w = layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        head = QLabel(title)
        head.setObjectName("pagetitle")
        layout.addWidget(head)
        dlg.setWindowFlags(Qt.Widget)
        layout.addWidget(dlg, 1)
        dlg.show()
        self.pages.setCurrentIndex(2)
        self.sidebar.b_settings.setChecked(title == "Settings")
        self.sidebar.b_history.setChecked(title == "History")

    def _back_to_books(self) -> None:
        self.pages.setCurrentIndex(0)
        self.sidebar.select_filter(STATE_FILTERS[self.state_filter.currentIndex()][1])

    def show_history(self) -> None:
        dlg = HistoryDialog(self.folders, self.processed, self)

        def done(_):
            self._back_to_books()
            if dlg.undid_something and self.model.books:
                self.scan()
        dlg.finished.connect(done)
        self._show_in_page("History", dlg)

    def show_settings(self) -> None:
        dlg = SettingsDialog(self.folders, self)
        dlg.finished.connect(lambda result: (self._back_to_books(), self._settings_done(bool(result))))
        self._show_in_page("Settings", dlg)

    def _settings_done(self, accepted: bool) -> None:
        self.model.refresh()  # the theme may have changed (or been previewed and reverted)
        self.sidebar.refresh_theme()
        if accepted:
            self.processed = Processed(self.folders.state / "processed.txt")
            self._lookup = None
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(1 if self.folders.move_originals else 0)
            self.mode.blockSignals(False)
            if self.folders.auto_minutes and not self.a_auto.isChecked():
                self.a_auto.setChecked(True)
            elif self.a_auto.isChecked():
                self._toggle_auto(True)
            if self.model.books:
                self._replan()

    def closeEvent(self, event) -> None:
        saved = ui_settings()
        saved.setValue("window/geometry", self.saveGeometry())
        saved.setValue("table/header", self.table.horizontalHeader().saveState())
        saved.setValue("window/splitter", self.split.saveState())
        saved.setValue("view/search", self.search.text())
        saved.setValue("view/state", STATE_FILTERS[self.state_filter.currentIndex()][1])
        saved.setValue("view/show_sorted", self.a_show_sorted.isChecked())
        if self._worker:
            self._worker.cancel()
        QThreadPool.globalInstance().waitForDone(5000)
        super().closeEvent(event)


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("AudioBooks & EBook Sorter")
    app.setStyle("Fusion")
    theme.apply(load_folders().theme, app)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))  # inside the .exe, or the project
    app.setWindowIcon(QIcon(str(base / "assets" / "icon.png")))
    first_run = not SETTINGS_FILE.exists()
    win = MainWindow()
    win.show()
    if first_run:  # e.g. the app was shared with someone: ask for their folders before anything else
        if SettingsDialog(win.folders, win, first_run=True).exec():
            win.processed = Processed(win.folders.state / "processed.txt")
            win.scan()
    else:
        win.scan()
    return app.exec()
