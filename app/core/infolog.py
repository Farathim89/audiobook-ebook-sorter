"""The info log: for every book a sort or an edit touches, what the app READ (tags, metadata files,
'book info.txt', file names) and what it DECIDED (each value and where it came from, the online match,
the kind, the target folder). One JSON line per book in <state>\\history\\info\\<run id>.jsonl - so
"why did this book go there?" can be looked up instead of dug out of the files again."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from .models import Book, Metadata

log = logging.getLogger(__name__)


def info_dir(state: Path) -> Path:
    return state / "history" / "info"


def _meta(m: Metadata | None) -> dict:
    """The non-empty fields (no description: it is long and says nothing about the sorting)."""
    if m is None:
        return {}
    return {k: v for k, v in asdict(m).items() if v not in (None, "", []) and k != "description"}


def what_was_read(book: Book) -> dict:
    """Read the book's own sources again, each on its own (the merged result hides who said what)."""
    from ..readers.sidecar import _own_folder, read_book_info, read_sidecars  # noqa: PLC0415
    from .metadata import _embedded, _name_guess  # noqa: PLC0415
    out: dict = {}
    for name, read in (("metadata files", lambda: read_sidecars(book)), ("tags", lambda: _embedded(book)),
                       ("file/folder names", lambda: _name_guess(book)[0])):
        try:
            if found := _meta(read()):
                out[name] = found
        except Exception as e:  # noqa: BLE001 - a log must never stop a sort
            out[name] = f"could not read: {e}"
    try:
        authors, narrators, light_novel = read_book_info(book.folder or _own_folder(book))
        if authors or narrators or light_novel:
            out["book info.txt"] = {"authors": authors, "narrators": narrators, "says light novel": light_novel}
    except Exception as e:  # noqa: BLE001
        out["book info.txt"] = f"could not read: {e}"
    return out


def what_was_decided(book: Book, target: Path | None = None) -> dict:
    match = next((c for c in book.candidates if c.provider in set(book.sources.values())), None)
    out = {
        "values": _meta(book.meta),
        "from": dict(book.sources),
        "kind": book.type.name.lower() + (" (light novel)" if book.light_novel else "")
                + (" (manga)" if getattr(book, "manga", False) else ""),
        "status": book.status.name.lower(),
    }
    if match is not None:
        out["online match"] = {"provider": match.provider, "title": match.meta.title, "asin": match.meta.asin,
                               "score": match.score, "sure": match.confident}
    if book.notes:
        out["notes"] = list(book.notes)
    if book.doubt:
        out["doubt"] = book.doubt
    if target is not None:
        out["target"] = str(target)
    return out


class InfoLog:
    """Appends one line per book to <state>\\history\\info\\<run id>.jsonl."""

    def __init__(self, state: Path, run_id: str):
        self.path = info_dir(state) / f"{run_id}.jsonl"

    _lock = __import__("threading").Lock()  # several books are sorted at once

    def write(self, **entry) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"time": time.strftime("%H:%M:%S"), **entry}, ensure_ascii=False,
                                    default=str) + "\n")
        except OSError as e:
            log.warning("info log not written: %s", e)

    def book(self, book: Book, target: Path | None, read: dict, result: str = "", detail: str = "") -> None:
        self.write(book=str(book.folder or book.main_file), files=[str(f) for f in book.files], read=read,
                   decided=what_was_decided(book, target), result=result, detail=detail)


def read_info(state: Path, run_id: str) -> list[dict]:
    path = info_dir(state) / f"{run_id}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
