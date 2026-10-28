"""Portable mode: a 'Shelfkeeper-data' folder next to Shelfkeeper.exe (or shelfkeeper.pyw) keeps everything -
settings, history/undo, cache, logs, ffmpeg and the window layout - in there instead of the user profile
and the registry. Without that folder nothing changes.

The app was called Book Sorter: on the first start its 'BookSorter-data' folder (and, without one, the
%APPDATA% / %LOCALAPPDATA% 'BookSorter' folders) are renamed to the new names, and the paths saved inside
them (undo history, settings) are rewritten, so undo of older runs keeps working."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

DATA_FOLDER = "Shelfkeeper-data"
OLD_DATA_FOLDER = "BookSorter-data"
APP_NAME = "Shelfkeeper"
OLD_APP_NAME = "BookSorter"
_TEXT_FILES = (".json", ".jsonl", ".txt", ".ini")
log = logging.getLogger(__name__)
_MOVE_IN_TESTS = False  # tests of the move itself switch this on (with temp folders)


def app_dir() -> Path:
    """The folder of Shelfkeeper.exe (frozen) or of the project (run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def rewrite_paths(folder: Path, old: Path, new: Path) -> int:
    """Saved paths inside `folder` that point into `old` now point into `new` (the history's parked files,
    settings). Returns how many files changed."""
    pairs = []
    for o, n in ((str(old), str(new)), (str(old).replace("\\", "/"), str(new).replace("\\", "/"))):
        pairs += [(o, n), (o.replace("\\", "\\\\"), n.replace("\\", "\\\\"))]  # also as JSON writes it
    changed = 0
    for p in folder.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _TEXT_FILES or p.stat().st_size > 50_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out = text
        for o, n in pairs:
            out = out.replace(o, n)
        if out != text:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(out, encoding="utf-8")
            tmp.replace(p)
            changed += 1
    return changed


def move_old_folder(old: Path, new: Path, rewrite_in: list[Path] | None = None) -> Path:
    """Rename `old` to `new` when only the old one exists; then fix saved paths. Returns the folder to use
    (`old` when it can't be renamed - e.g. the old app still runs and holds a file open)."""
    if new.exists() or not old.is_dir():
        return new if new.exists() or not old.exists() else old
    if "pytest" in sys.modules and not _MOVE_IN_TESTS:
        return old  # the test suite never renames the real profile folders
    try:
        old.rename(new)
    except OSError as e:
        log.warning("could not rename %s to %s (%s) - using the old folder", old, new, e)
        return old
    for folder in [new, *(rewrite_in or [])]:
        try:
            rewrite_paths(folder, old, new)
        except OSError as e:
            log.warning("could not update paths in %s: %s", folder, e)
    return new


def portable_dir(base: Path | None = None) -> Path | None:
    """The portable data folder when it exists (an old BookSorter-data one is renamed first), else None."""
    base = base or app_dir()
    d = move_old_folder(base / OLD_DATA_FOLDER, base / DATA_FOLDER)
    return d if d.is_dir() else None


def profile_dir(env: str) -> Path:
    """%APPDATA%\\Shelfkeeper / %LOCALAPPDATA%\\Shelfkeeper (an old ...\\BookSorter one is renamed first)."""
    root = Path(os.environ.get(env) or Path.home())
    others = [Path(os.environ[e]) / APP_NAME for e in ("APPDATA", "LOCALAPPDATA") if os.environ.get(e) and e != env]
    return move_old_folder(root / OLD_APP_NAME, root / APP_NAME, rewrite_in=[p for p in others if p.is_dir()])


PORTABLE = portable_dir()


def ui_settings():
    """Window size, column widths, filters: an .ini in the portable folder, else Qt's default (registry)."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    if PORTABLE is not None:
        return QSettings(str(PORTABLE / "window.ini"), QSettings.IniFormat)
    s = QSettings(APP_NAME, APP_NAME)
    if not s.allKeys():  # first start after the rename: the old window layout
        old = QSettings(OLD_APP_NAME, OLD_APP_NAME)
        for key in old.allKeys():
            s.setValue(key, old.value(key))
    return s
