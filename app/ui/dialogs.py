"""Settings and History (undo) dialogs."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QGridLayout, QToolButton,
                               QListWidget, QListWidgetItem, QScrollArea, QStackedWidget,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ..config import Folders, save_folders
from ..core.duplicates import best_copy, describe
from ..core.history import Processed, list_runs, undo
from ..providers.audible import TLDS
from . import theme

INCOMING_FIELDS = [
    ("audio_incoming", "Audiobooks incoming"),
    ("books_incoming", "Books incoming (ebooks, manga)"),
]
OUTPUT_FIELDS = [
    ("audio_sorted", "Sorted audiobooks"),
    ("books_sorted", "Sorted books"),
    ("review", "Review (no author found)"),
    ("duplicates", "Duplicates (extra copies)"),
    ("done", "Done (originals after sorting)"),
]
WELCOME = ("Welcome! Choose where your new books arrive (Incoming) and where the sorted copies should go.\n"
           "The app copies from Incoming (or moves, if you choose) and never writes into the protected folders - put your "
           "Audiobookshelf / library folders there and move finished books in yourself.")


def two_columns(form: QFormLayout, left_rows: list[int], titles: tuple[str, str], stacked: bool = False) -> QWidget:
    """Lay a settings form out as two side-by-side groups (UnitFramesPro style): the rows in
    left_rows go left (in that order), the rest right. stacked: one above the other instead (long paths)."""
    rows = []
    while form.rowCount():
        taken = form.takeRow(0)
        label = taken.labelItem.widget() if taken.labelItem else None
        field = taken.fieldItem.widget() if taken.fieldItem else None
        rows.append((label, field))
    page = QWidget()
    cols = QVBoxLayout(page) if stacked else QHBoxLayout(page)
    cols.setContentsMargins(0, 0, 0, 0)
    cols.setSpacing(12)
    left = [rows[i] for i in left_rows]
    right = [r for i, r in enumerate(rows) if i not in left_rows]
    for title, part in zip(titles, (left, right)):
        group = QGroupBox(title)
        f = QFormLayout(group)
        for label, field in part:
            if field is None:
                continue
            if label is None and isinstance(field, QLabel):
                f.addRow(field)  # an explanation across the whole group
            elif label is None:
                f.addRow(QLabel(""), field)  # keep it under the field above (a second checkbox...)
            else:
                f.addRow(label, field)
        if stacked:
            cols.addWidget(group)
            continue
        col = QVBoxLayout()
        col.addWidget(group)
        col.addStretch(1)
        cols.addLayout(col, 1)
    if stacked:
        cols.addStretch(1)
    return page


class ThemePicker(QWidget):
    """Every theme as a clickable preview tile (no dropdown). currentText() = the chosen theme."""
    picked = Signal(str)

    def __init__(self, current: str, columns: int = 4, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(10)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._current = current
        for i, name in enumerate(theme.THEMES):
            tile = QToolButton()
            tile.setObjectName("themetile")
            tile.setCheckable(True)
            tile.setChecked(name == current)
            tile.setText(name)
            tile.setIcon(QIcon(theme.swatch(name)))
            tile.setIconSize(QSize(150, 84))
            tile.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            tile.setCursor(Qt.PointingHandCursor)
            tile.clicked.connect(lambda _=False, n=name: self._pick(n))
            self._group.addButton(tile)
            grid.addWidget(tile, i // columns, i % columns)

    def _pick(self, name: str) -> None:
        self._current = name
        self.picked.emit(name)

    def currentText(self) -> str:
        return self._current


class SideTabs(QWidget):
    """Settings sections as a grouped menu on the left (like UnitFramesPro) with the page on the right.
    Same addTab() as QTabWidget. groups: {first tab of a group: group title}."""

    def __init__(self, groups: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.groups = groups or {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.menu = QListWidget()
        self.menu.setObjectName("sidemenu")
        self.menu.setFixedWidth(180)
        self.stack = QStackedWidget()
        row.addWidget(self.menu)
        row.addWidget(self.stack, 1)
        self.menu.currentRowChanged.connect(self._show)

    def addTab(self, widget: QWidget, title: str) -> None:
        if title in self.groups:
            head = QListWidgetItem(self.groups[title])
            head.setFlags(Qt.NoItemFlags)  # a group title, not clickable
            head.setData(Qt.UserRole, -1)
            font = head.font()
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() * 0.8)
            head.setFont(font)
            self.menu.addItem(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(widget)
        index = self.stack.addWidget(scroll)
        item = QListWidgetItem("   " + title)
        item.setData(Qt.UserRole, index)
        self.menu.addItem(item)
        if self.menu.currentRow() < 0 or self.menu.currentItem().data(Qt.UserRole) == -1:
            self.menu.setCurrentItem(item)

    def _show(self, row: int) -> None:
        item = self.menu.item(row)
        if item is not None and (index := item.data(Qt.UserRole)) is not None and index >= 0:
            self.stack.setCurrentIndex(index)


class SettingsDialog(QDialog):
    def __init__(self, folders: Folders, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.folders = folders
        self.setWindowTitle("Welcome - set up your folders" if first_run else "Settings")
        self.resize(780, 520)
        layout = QVBoxLayout(self)
        if first_run:
            hello = QLabel(WELCOME)
            hello.setWordWrap(True)
            hello.setObjectName("heading")
            layout.addWidget(hello)
        self.edits: dict[str, QLineEdit] = {}
        tabs = SideTabs({"Folders": "LIBRARY", "Sorting": "BEHAVIOUR", "Look": "APPEARANCE"})

        box = QWidget()
        form = QFormLayout(box)
        for name, label in INCOMING_FIELDS:
            form.addRow(label, self._path_row(name, getattr(folders, name)))
        self.extra = QPlainTextEdit("\n".join(map(str, folders.extra_sources)))
        self.extra.setMinimumHeight(56)
        self.extra.setMaximumHeight(56)
        self.extra.setPlaceholderText("more folders to scan, one per line (optional)")
        form.addRow("Also scan", self.extra)
        one = QPushButton("Put all sorted output in one folder…")
        one.setToolTip("Pick one folder; Sorted audiobooks, Sorted books and Review go inside it")
        one.clicked.connect(self._one_output_folder)
        one.setMinimumHeight(30)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 6, 0, 0)
        hl.addWidget(one)
        hl.addStretch()
        form.addRow("", holder)
        for name, label in OUTPUT_FIELDS:
            form.addRow(label, self._path_row(name, getattr(folders, name)))
        self.swedish = self._path_row("swedish_sorted", folders.swedish_sorted or "",
                                      placeholder="empty = together with the other audiobooks")
        form.addRow("Sorted Swedish audiobooks", self.swedish)
        form.addRow("App data (cache, history)", self._path_row("state", folders.state))
        self.create = QCheckBox("Create missing folders when saving")
        self.create.setChecked(first_run)
        form.addRow("", self.create)
        tabs.addTab(two_columns(form, [0, 1, 2, 10, 11], ("Incoming && app data", "Sorted output"), stacked=True), "Folders")

        box = QWidget()
        v = QVBoxLayout(box)
        v.addWidget(QLabel("The app never writes into these folders - put your Audiobookshelf / library folders here."))
        self.protected = QPlainTextEdit("\n".join(map(str, folders.protected)))
        self.protected.setFixedHeight(84)
        self.protected.setPlaceholderText("your Audiobookshelf / library folders, one per line")
        v.addWidget(self.protected)
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_protected)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch()
        v.addLayout(row)
        tabs.addTab(box, "Safety")

        box = QWidget()
        form = QFormLayout(box)
        self.split_audio = QCheckBox("Audiobooks: Light Novels / Audiobooks")
        self.split_audio.setChecked(folders.split_audio)
        self.split_books = QCheckBox("Books: Manga / Light Novels / EBooks / Comics")
        self.split_books.setChecked(folders.split_books)
        self.write_tags = QCheckBox("Write corrected tags into sorted files")
        self.write_tags.setToolTip("Author, series, narrator ... go into the tags of the sorted copies")
        self.write_tags.setChecked(folders.write_tags)
        self.fix_xhe = QCheckBox("Re-encode xHE-AAC to plain AAC")
        self.fix_xhe.setToolTip("Audiobookshelf can't play xHE-AAC audiobooks")
        self.fix_xhe.setChecked(folders.fix_xhe_aac)
        form.addRow("Folders", self.split_audio)
        form.addRow("", self.split_books)
        form.addRow("Files", self.write_tags)
        form.addRow("", self.fix_xhe)
        from .sidebar import SegmentedSwitch  # noqa: PLC0415
        self.m4b_codec = SegmentedSwitch(["Keep audio", "Re-encode AAC"])
        self.m4b_codec.setCurrentIndex(1 if folders.m4b_codec == "aac" else 0)
        self.m4b_codec.setToolTip("What the M4B → M4B switch does to books that already are one .m4b:\n"
                                  "Keep audio - only Audible's chapter names, the sound is copied as it is (fast)\n"
                                  "Re-encode AAC - new AAC audio at the same bitrate (slow), plus Audible's chapters")
        form.addRow("M4B → M4B", self.m4b_codec)
        self.play_test = SegmentedSwitch(["Off", "Quick", "Full"])
        self.play_test.setCurrentIndex({"off": 0, "quick": 1}.get(folders.play_test, 2))
        self.play_test.setToolTip("Every audio file the app makes or changes (M4B → M4B, MP3 → M4B, xHE fix, tags,\n"
                                  "cover) is played through with ffmpeg before the original leaves Incoming.\n"
                                  "Quick: 20 s at the start, middle and end. Full: the whole file (~20 s per book).\n"
                                  "A new file that fails is removed and the original is sorted as it is.")
        form.addRow("Play test", self.play_test)
        self.move_originals = QCheckBox("Move originals out of Incoming")
        self.move_originals.setChecked(folders.move_originals)
        self.move_originals.setToolTip("Else copy and keep them. Leftover cover/metadata files go to the Done "
                                       "folder. Also used by auto-sort. When MP3 → M4B, M4B → M4B or the "
                                       "xHE-AAC fix makes a new file, the original goes to the Done folder.")
        form.addRow("Originals", self.move_originals)
        self.keep_done = QCheckBox("Keep old copies in Done")
        self.keep_done.setChecked(folders.keep_done)
        self.keep_done.setToolTip("Move mode: the old copy of a book that got a new file (M4B → M4B, MP3 → M4B, "
                                  "xHE fix)\nand its leftover cover/metadata files go to the Done folder - a safety "
                                  "net you empty yourself.\nOff: they go to the Recycle Bin, or are DELETED FOR GOOD "
                                  "when the drive has no Recycle Bin.\nAn old audio copy only once its new file "
                                  "passed a FULL play test - else it stays in Done.\n"
                                  "The sorted book is always complete first.")
        form.addRow("", self.keep_done)
        self.done_days = QSpinBox()
        self.done_days.setRange(0, 3650)
        self.done_days.setSuffix(" days")
        self.done_days.setSpecialValueText("never")
        self.done_days.setValue(folders.done_days)
        self.done_days.setToolTip("The Done folder keeps one sub-folder per day (Done\\2026-10-08\\...).\n"
                                  "Day folders older than this go to the Windows Recycle Bin (restorable)\n"
                                  "when the app scans or auto-sorts.")
        form.addRow("Empty Done after", self.done_days)
        self.bin_warning = QLabel()
        self.bin_warning.setWordWrap(True)
        self.bin_warning.setStyleSheet("color: #e0a030; font-weight: bold;")
        form.addRow("", self.bin_warning)
        self.done_days.valueChanged.connect(self._check_recycle_bin)
        self.keep_done.toggled.connect(self._check_recycle_bin)
        self._check_recycle_bin()
        self.auto = QSpinBox()
        self.auto.setRange(0, 1440)
        self.auto.setSuffix(" min")
        self.auto.setSpecialValueText("off")
        self.auto.setValue(folders.auto_minutes)
        self.auto.setToolTip("While the window is open: scan Incoming and sort every book that is Ready "
                             "(sure match, no problems) into the sorted folders. Everything else waits for you.")
        form.addRow("Auto-sort every", self.auto)
        self.auto_dupes = QCheckBox("Auto-sort also moves duplicates")
        self.auto_dupes.setToolTip("Extra copies and books your library already has go to the Duplicates folder")
        self.auto_dupes.setChecked(folders.auto_move_duplicates)
        form.addRow("", self.auto_dupes)
        tabs.addTab(two_columns(form, [0, 1, 2, 3], ("Folders && files", "Originals && auto-sort")), "Sorting")

        box = QWidget()
        form = QFormLayout(box)
        intro = QLabel("Audible (audiobooks) and AniList (manga, light novels) are always used. The others "
                       "only fill gaps - description, cover, year, ISBN - and only on an exact title + author match.")
        intro.setWordWrap(True)
        intro.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        form.addRow(intro)
        self.region = QComboBox()
        self.region.addItems(list(TLDS))
        self.region.setCurrentText(folders.audible_region)
        form.addRow("Audible region", self.region)
        self.use_storytel = QCheckBox("Storytel (Swedish / Nordic)")
        self.use_storytel.setChecked(folders.use_storytel)
        self.use_itunes = QCheckBox("Apple Books (iTunes)")
        self.use_itunes.setChecked(folders.use_itunes)
        self.use_google = QCheckBox("Google Books")
        self.use_google.setChecked(folders.use_google_books)
        self.use_openlib = QCheckBox("Open Library")
        self.use_openlib.setChecked(folders.use_openlibrary)
        form.addRow("Sources", self.use_storytel)
        form.addRow("", self.use_itunes)
        form.addRow("", self.use_google)
        form.addRow("", self.use_openlib)
        self.google_key = QLineEdit(folders.google_books_key)
        self.google_key.setPlaceholderText("optional")
        self.google_key.setToolTip("Without a key Google often answers 'too many requests'")
        self.google_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        form.addRow("Google key", self.google_key)
        self.use_mangadex = QCheckBox("MangaDex volume covers")
        self.use_mangadex.setChecked(folders.use_mangadex)
        form.addRow("Manga", self.use_mangadex)
        self.fandom = QPlainTextEdit(folders.fandom_wikis)
        self.fandom.setFixedHeight(70)
        self.fandom.setPlaceholderText("Volume titles from Fandom wikis, one series per line:\n"
                                       "One Piece = onepiece.fandom.com\n"
                                       "Made in Abyss = madeinabyss.fandom.com | Volume {n}")
        form.addRow("Fandom wikis", self.fandom)
        self.fandom_auto = QCheckBox("Also guess <series>.fandom.com")
        self.fandom_auto.setChecked(folders.fandom_auto)
        form.addRow("", self.fandom_auto)
        self.abs_url = QLineEdit(folders.abs_url)
        self.abs_url.setPlaceholderText("e.g. http://localhost:13378/audiobookshelf")
        form.addRow("ABS address", self.abs_url)
        self.abs_key = QLineEdit(folders.abs_api_key)
        self.abs_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.abs_key.setPlaceholderText("ABS: Settings > API Keys")
        self.abs_key.setToolTip("Only for Tools > Sort collections")
        form.addRow("ABS API key", self.abs_key)
        tabs.addTab(two_columns(form, [0, 1, 2, 3, 4, 5, 6], ("Sources", "Manga && Audiobookshelf")), "Online")

        box = QWidget()
        form = QFormLayout(box)
        self.theme = ThemePicker(folders.theme if folders.theme in theme.THEMES else theme.DEFAULT_THEME)
        self.theme.picked.connect(theme.apply)  # live preview; Cancel puts the old one back
        form.addRow(QLabel("Colour theme - click one to try it, Save to keep it"))
        form.addRow(self.theme)
        tabs.addTab(box, "Look")

        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- helpers --------------------------------------------------------------
    def _path_row(self, name: str, value, placeholder: str = "") -> QWidget:
        edit = QLineEdit(str(value))
        if placeholder:
            edit.setPlaceholderText(placeholder)
        browse = QPushButton("…")
        browse.setFixedWidth(40)
        browse.setStyleSheet("padding: 2px;")
        browse.setToolTip("Choose folder")
        browse.clicked.connect(lambda _=False, e=edit: self._browse(e))
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        h.addWidget(browse)
        self.edits[name] = edit
        return row

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
        if path:
            edit.setText(str(Path(path)))

    def _one_output_folder(self) -> None:
        start = str(Path(self.edits["audio_sorted"].text()).parent)
        path = QFileDialog.getExistingDirectory(self, "Folder for all sorted output", start)
        if path:
            base = Path(path)
            self.edits["audio_sorted"].setText(str(base / "AudioBooks"))
            self.edits["books_sorted"].setText(str(base / "Books"))
            self.edits["review"].setText(str(base / "Review"))
            self.edits["duplicates"].setText(str(base / "Duplicates"))
            self.edits["done"].setText(str(base / "Done"))

    def _add_protected(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "A folder the app must never write to")
        if path:
            text = self.protected.toPlainText().rstrip()
            self.protected.setPlainText((text + "\n" if text else "") + str(Path(path)))

    def _check_recycle_bin(self) -> None:
        """Warn when the Done folder's drive deletes at once: emptying Done would then do nothing."""
        from ..core.leftovers import recycle_bin_on  # noqa: PLC0415
        done = Path(self.edits["done"].text().strip() or str(self.folders.done))
        incoming = self.folders.audio_incoming
        try:
            off = self.done_days.value() > 0 and not recycle_bin_on(done)
            gone = not self.keep_done.isChecked() and not recycle_bin_on(incoming)
        except OSError:
            off = gone = False
        texts = []
        if gone:
            texts.append(f"No Recycle Bin on drive {incoming.drive or incoming}: with 'Keep old copies in Done' "
                         "off, old copies are DELETED FOR GOOD after sorting (undo can't bring them back).")
        if off:
            texts.append(f"The Recycle Bin is switched off for drive {done.drive or done} - emptying Done is "
                         "refused (Windows would delete for good). Turn it on: Recycle Bin > Properties.")
        self.bin_warning.setText("\n".join(texts))
        self.bin_warning.setVisible(bool(texts))

    def reject(self) -> None:
        theme.apply(self.folders.theme)  # undo the live preview
        super().reject()

    def _save(self) -> None:
        lines = lambda w: [Path(x.strip()) for x in w.toPlainText().splitlines() if x.strip()]  # noqa: E731
        paths = {name: edit.text().strip() for name, edit in self.edits.items()}
        missing = [label for name, label in INCOMING_FIELDS + OUTPUT_FIELDS if not paths[name]]
        if missing:
            QMessageBox.warning(self, "Settings", "Please choose: " + ", ".join(missing))
            return
        check = Folders(protected=lines(self.protected))
        clash = [paths[n] for n, _ in OUTPUT_FIELDS if check.is_protected(Path(paths[n]))]
        if clash:
            QMessageBox.warning(self, "Settings", "These sorted folders are inside a protected folder, so the app "
                                "could never write there:\n\n" + "\n".join(clash))
            return
        for name, value in paths.items():
            if name == "swedish_sorted":
                self.folders.swedish_sorted = Path(value) if value else None
            else:
                setattr(self.folders, name, Path(value))
        self.folders.extra_sources = lines(self.extra)
        self.folders.protected = lines(self.protected)
        self.folders.audible_region = self.region.currentText()
        self.folders.use_storytel = self.use_storytel.isChecked()
        self.folders.use_itunes = self.use_itunes.isChecked()
        self.folders.use_google_books = self.use_google.isChecked()
        self.folders.use_openlibrary = self.use_openlib.isChecked()
        self.folders.google_books_key = self.google_key.text().strip()
        self.folders.use_mangadex = self.use_mangadex.isChecked()
        self.folders.fandom_wikis = self.fandom.toPlainText().strip()
        self.folders.fandom_auto = self.fandom_auto.isChecked()
        self.folders.abs_url = self.abs_url.text().strip().rstrip("/")
        self.folders.abs_api_key = self.abs_key.text().strip()
        self.folders.theme = self.theme.currentText()
        self.folders.split_audio = self.split_audio.isChecked()
        self.folders.split_books = self.split_books.isChecked()
        self.folders.write_tags = self.write_tags.isChecked()
        self.folders.fix_xhe_aac = self.fix_xhe.isChecked()
        self.folders.m4b_codec = "aac" if self.m4b_codec.currentIndex() == 1 else "copy"
        self.folders.play_test = ("off", "quick", "full")[self.play_test.currentIndex()]
        self.folders.auto_minutes = self.auto.value()
        self.folders.auto_move_duplicates = self.auto_dupes.isChecked()
        self.folders.move_originals = self.move_originals.isChecked()
        self.folders.done_days = self.done_days.value()
        self.folders.keep_done = self.keep_done.isChecked()
        if self.create.isChecked():
            for name in ("audio_incoming", "books_incoming", "audio_sorted", "books_sorted", "review", "duplicates",
                         "done", "state"):
                try:
                    getattr(self.folders, name).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    QMessageBox.warning(self, "Settings", f"Could not create {getattr(self.folders, name)}:\n{e}")
                    return
        save_folders(self.folders)
        self.accept()



class EditSortedDialog(QDialog):
    """Correct a book that is already in the sorted library."""

    def __init__(self, book, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit a sorted book")
        self.resize(560, 0)
        m = book.meta
        layout = QVBoxLayout(self)
        where = QLabel(str(book.folder))
        where.setObjectName("muted")
        where.setWordWrap(True)
        layout.addWidget(where)
        form = QFormLayout()
        self.f_title = QLineEdit(m.title or "")
        self.f_authors = QLineEdit("; ".join(m.authors))
        self.f_series = QLineEdit(m.series or "")
        self.f_index = QLineEdit(f"{m.series_index:g}" if m.series_index is not None else "")
        self.f_narrators = QLineEdit("; ".join(m.narrators))
        self.f_year = QLineEdit(str(m.year or ""))
        self.f_authors.setPlaceholderText("Main author first; separate with ;")
        for label, w in (("Title", self.f_title), ("Author(s)", self.f_authors), ("Series", self.f_series),
                         ("Number", self.f_index), ("Narrator(s)", self.f_narrators), ("Year", self.f_year)):
            form.addRow(label, w)
        layout.addLayout(form)
        note = QLabel("The folder moves to match (e.g. to the right author), the files are re-tagged and "
                      "Audiobookshelf's metadata.json is parked so it reads the new data. Undo from History.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.values: dict = {}

    def _save(self) -> None:
        split = lambda s: [x.strip() for x in s.split(";") if x.strip()]  # noqa: E731
        if not self.f_title.text().strip() or not split(self.f_authors.text()):
            QMessageBox.warning(self, "Edit", "Title and author are needed.")
            return
        try:
            index = float(self.f_index.text().replace(",", ".")) if self.f_index.text().strip() else None
            year = int(self.f_year.text()) if self.f_year.text().strip() else None
        except ValueError:
            QMessageBox.warning(self, "Edit", "Number and year must be numbers.")
            return
        self.values = {"title": self.f_title.text().strip(), "authors": split(self.f_authors.text()),
                       "series": self.f_series.text().strip(), "series_index": index,
                       "narrators": split(self.f_narrators.text()), "year": year}
        self.accept()


class DuplicateDialog(QDialog):
    """Pick the one copy to sort; the others are skipped (left where they are)."""

    def __init__(self, books, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose which copy to keep")
        self.resize(820, 300)
        self.keep = None
        self.move_others = False
        self.infos = [describe(b) for b in books]
        best = best_copy(self.infos)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"These {len(books)} copies would end up in the same place. The one you keep is "
                                "sorted; the others are skipped and stay where they are."))
        self.table = QTableWidget(len(self.infos), 3)
        self.table.setHorizontalHeaderLabels(["Copy", "Details", ""])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        for i, info in enumerate(self.infos):
            b = info.book
            self.table.setItem(i, 0, QTableWidgetItem(str(b.folder or b.main_file)))
            self.table.setItem(i, 1, QTableWidgetItem(info.summary))
            self.table.setItem(i, 2, QTableWidgetItem("recommended" if info is best else ""))
        self.table.selectRow(self.infos.index(best))
        self.table.doubleClicked.connect(self._accept)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Keep selected, skip the others")
        buttons.accepted.connect(self._accept)
        move = buttons.addButton("Keep selected, move the others to Duplicates", QDialogButtonBox.ActionRole)
        move.clicked.connect(self._accept_and_move)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_and_move(self) -> None:
        self.move_others = True
        self._accept()

    def _accept(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.keep = self.infos[row].book
            self.accept()


class HistoryDialog(QDialog):
    def __init__(self, folders: Folders, processed: Processed, parent=None):
        super().__init__(parent)
        self.folders, self.processed = folders, processed
        self.undid_something = False
        self.setWindowTitle("History")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Every run is logged. Undo deletes the copies it made "
                                "(or moves files back) and removes the folders it created."))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Run", "When", "Mode", "Files"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.b_undo = QPushButton("Undo selected run")
        self.b_undo.setObjectName("primary")
        self.b_undo.clicked.connect(self._undo)
        row.addWidget(self.b_undo)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.runs = list_runs(self.folders.state / "history")
        self.table.setRowCount(len(self.runs))
        for i, r in enumerate(self.runs):
            for j, text in enumerate((r.run_id, r.time, r.mode + (" (undone)" if r.undone else ""), str(r.files))):
                self.table.setItem(i, j, QTableWidgetItem(text))

    def _undo(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        run = self.runs[row]
        if run.undone:
            QMessageBox.information(self, "Undo", "This run was already undone.")
            return
        verb = "move the files back" if run.mode == "move" else "delete the copies"
        if QMessageBox.question(self, "Undo", f"Undo run {run.run_id}?\nThis will {verb} "
                                f"({run.files} files) and remove the folders it created.") != QMessageBox.Yes:
            return
        res = undo(run, self.processed)
        self.undid_something = True
        msg = f"{res.restored} files undone."
        if res.skipped:
            msg += "\n\nKept:\n" + "\n".join(res.skipped[:20])
        QMessageBox.information(self, "Undo", msg)
        self._fill()
