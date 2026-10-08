"""Find book files under the source folders."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Iterator

from .models import BOOK_EXTS
from .text import natural_key

IGNORED_DIRS = {"$recycle.bin", "system volume information", "@eadir", "#recycle"}
IGNORED_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}


def walk_files(root: Path) -> Iterator[Path]:
    """Every regular file under `root`, skipping hidden and system folders."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in IGNORED_DIRS]
        for name in filenames:
            if name.startswith("._") or name.lower() in IGNORED_FILES:
                continue
            yield Path(dirpath) / name


def scan(sources: Iterable[Path]) -> list[Path]:
    found: set[Path] = set()
    for src in map(Path, sources):
        if src.is_file():
            candidates: Iterable[Path] = [src]
        else:
            candidates = walk_files(src)
        found.update(p for p in candidates if p.suffix.lower() in BOOK_EXTS)
    return sorted(found, key=natural_key)
