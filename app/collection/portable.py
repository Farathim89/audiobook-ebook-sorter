"""The Collection page (the former Book Collection Checker) keeps its settings, lookup cache, saved collection and
table layout in a 'collection' folder next to the sorter's own data: <Shelfkeeper-data>\\collection when portable,
else %LOCALAPPDATA%\\Shelfkeeper\\collection. On first use it takes over an old Checker's data folder."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..portable import PORTABLE as SORTER_PORTABLE, app_dir, profile_dir

OLD_FOLDER = "CollectionChecker-data"
OLD_FILES = ("settings.json", "collection.json", "cache.sqlite", "window.ini")


def data_dir() -> Path:
    base = SORTER_PORTABLE if SORTER_PORTABLE is not None else profile_dir("LOCALAPPDATA")
    return base / "collection"


PORTABLE = data_dir()


def old_checker_dirs() -> list[Path]:
    """Where a Book Collection Checker kept its data: next to this exe, the Checker project next door
    (D:\\Ai - Programs\\Book Collection Checker\\dist), or its profile folder."""
    here = app_dir()
    return [here / OLD_FOLDER,
            here.parent / "Book Collection Checker" / "dist" / OLD_FOLDER,
            here.parent.parent / "Book Collection Checker" / "dist" / OLD_FOLDER,
            Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BookCollectionChecker"]


def take_over_old_checker(target: Path = PORTABLE) -> Path | None:
    """First use: copy an old Checker's settings, saved collection, lookup cache and layout (it stays as it was)."""
    if (target / "settings.json").exists():
        return None
    for old in old_checker_dirs():
        if (old / "settings.json").is_file():
            target.mkdir(parents=True, exist_ok=True)
            for name in OLD_FILES[1:] + OLD_FILES[:1]:  # settings.json last: it marks the takeover as done
                if (old / name).is_file():
                    shutil.copy2(old / name, target / name)
            return old
    return None


def ui_settings():
    """Column widths, sort order: collection\\window.ini (never the sorter's own window.ini)."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    PORTABLE.mkdir(parents=True, exist_ok=True)
    return QSettings(str(PORTABLE / "window.ini"), QSettings.IniFormat)
