"""Scan -> group -> read: turn source folders into a list of books."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from .grouper import group
from .metadata import read_local
from .models import Book
from .scanner import scan

Progress = Callable[[int, int, Book], None]


def load_books(sources: Iterable[Path], on_progress: Progress | None = None) -> list[Book]:
    books: list[Book] = []
    for src in map(Path, sources):
        books.extend(group(scan([src]), src))
    for i, book in enumerate(books, 1):
        read_local(book)
        if on_progress:
            on_progress(i, len(books), book)
    return books
