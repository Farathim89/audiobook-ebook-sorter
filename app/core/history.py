"""Run journals (for undo) and the processed list (to skip books already sorted).

Every file operation is appended to <state>/history/<run id>.jsonl the moment it
happens, so even a crashed or cancelled run can be undone.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import Book


class Journal:
    def __init__(self, history_dir: Path, mode: str):
        history_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.path = history_dir / f"{self.run_id}.jsonl"
        n = 1
        # an undone run is renamed '<id>.undone.jsonl': its id stays taken (info log, saved tags)
        while self.path.exists() or self.path.with_name(f"{self.path.stem}.undone.jsonl").exists():
            n += 1
            self.path = history_dir / f"{self.run_id}-{n}.jsonl"
        self.run_id = self.path.stem
        self._fh = open(self.path, "a", encoding="utf-8")
        self._lock = threading.Lock()  # several books are sorted at once
        self._book = threading.local()  # the steps of the book this thread is sorting (begin_book)
        self.write(op="run", mode=mode, time=time.strftime("%Y-%m-%d %H:%M:%S"))

    def write(self, **entry) -> None:
        with self._lock:
            self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._fh.flush()
        steps = getattr(self._book, "steps", None)
        if steps is not None:
            steps.append(dict(entry))

    def begin_book(self) -> None:
        """From now on this thread's steps are one book's (so a failure can put just that book back)."""
        self._book.steps = []

    def end_book(self) -> list[dict]:
        steps, self._book.steps = getattr(self._book, "steps", None) or [], None
        return steps

    def rollback_book(self) -> list[str]:
        """The book failed halfway: reverse its steps (moved files back to Incoming, its new files and
        folders removed) and journal that, so the book is whole again - not half in Sorted. Returns what
        could not be put back."""
        steps = self.end_book()
        result = UndoResult()
        _reverse(steps, result)
        for e in steps:
            self.write(op="rolled-back", step=e.get("op"), src=e.get("src"), dst=e.get("dst") or e.get("path"))
        return result.skipped

    def close(self) -> None:
        self._fh.close()


@dataclass
class RunInfo:
    run_id: str
    path: Path
    time: str
    mode: str
    files: int
    undone: bool


def list_runs(history_dir: Path) -> list[RunInfo]:
    runs = []
    for path in sorted(history_dir.glob("*.jsonl"), reverse=True):
        entries = _read(path)
        head = entries[0] if entries else {}
        runs.append(RunInfo(
            run_id=path.stem.replace(".undone", ""), path=path, time=head.get("time", ""),
            mode=head.get("mode", ""), files=sum(e.get("op") in ("copy", "move") for e in entries),
            undone=path.stem.endswith(".undone")))
    return runs


@dataclass
class UndoResult:
    restored: int = 0
    skipped: list[str] = field(default_factory=list)


def undo(run: RunInfo, processed: Processed | None = None) -> UndoResult:
    """Reverse a run: delete the copies/created files, move moved files back, remove empty new folders."""
    result = UndoResult()
    if run.undone:
        result.skipped.append("this run was already undone")
        return result
    entries = _read(run.path)
    # a book that failed was put back during the sort ('rolled-back'): its steps are done already
    rolled = {(e.get("step"), e.get("src"), e.get("dst")) for e in entries if e.get("op") == "rolled-back"}
    entries = [e for e in entries if e.get("op") == "rolled-back"
               or (e.get("op"), e.get("src"), e.get("dst") or e.get("path")) not in rolled]
    failed = _reverse(entries, result, processed)
    if failed:  # e.g. a file open in a player: the run stays undoable - Undo again does the rest
        result.skipped.append(f"{failed} step(s) failed - close what uses those files and Undo this run again")
    else:
        run.path.rename(run.path.with_name(f"{run.path.stem}.undone.jsonl"))
    if processed is not None:
        processed.save()
    try:  # an undone Edit: the rule it taught goes too (history\<run>.jsonl -> state\rules.json)
        from .rules import Rules  # noqa: PLC0415
        Rules.load(run.path.parent.parent).forget_run(run.run_id)
    except (OSError, ValueError):
        pass
    return result


def _reverse(entries: list[dict], result: UndoResult, processed: Processed | None = None) -> int:
    """Undo these journal steps, newest first. Returns how many failed (OSError)."""
    # files we re-tagged after copying: their expected size is the size after tagging
    final_size = {e["dst"]: e.get("size") for e in entries if e.get("op") == "update"}
    # an original BOOK file was deleted ('Keep old copies in Done' off): the new book files made from it are
    # the only copies left and are kept - a deleted cover.jpg / .nfo doesn't count, nor are sidecars kept
    from .models import BOOK_EXTS  # noqa: PLC0415
    deleted_originals = any(e.get("op") == "delete" and Path(e.get("src", "")).suffix.lower() in BOOK_EXTS
                            for e in entries)
    failed = 0
    for e in reversed(entries):
        op = e.get("op")
        dst = Path(e["dst"]) if "dst" in e else None
        try:
            if op == "tags":
                # the app re-tagged this file (an original - Move mode or Edit): put the old tags back
                # before it moves back
                from .tagger import restore_tags  # noqa: PLC0415
                saved = Path(e["saved"])
                if dst is None or not dst.exists() or not saved.exists():
                    result.skipped.append(f"old tags not put back (file or saved tags gone): {dst}")
                    continue
                restore_tags(dst, saved.read_bytes())
                continue
            if op == "content":
                # a file the app rewrote (a metadata.json): its old contents back, before it moves back
                saved = Path(e["saved"])
                if dst is None or not dst.exists() or not saved.exists():
                    result.skipped.append(f"old contents not put back (file or saved copy gone): {dst}")
                    continue
                dst.write_bytes(saved.read_bytes())
                result.restored += 1
                continue
            if op == "delete":
                where = "in the Recycle Bin" if e.get("how") == "recycle bin" else "deleted for good"
                result.skipped.append(f"{where}, can't bring back: {e.get('src')}")
                continue
            if op in ("copy", "create"):
                if not dst.exists():
                    continue
                if deleted_originals and op == "create" and dst.suffix.lower() in BOOK_EXTS:
                    result.skipped.append(f"kept - its original was deleted when sorting: {dst}")
                    continue
                size = final_size.get(e["dst"], e.get("size"))
                if size is not None and dst.stat().st_size not in (size, e.get("size")):  # rewritten + put back
                    result.skipped.append(f"changed since, kept: {dst}")
                    continue
                os.remove(dst)
                result.restored += 1
            elif op == "move":
                src = Path(e["src"])
                if src.exists():
                    result.skipped.append(f"original place is taken again, kept: {dst}")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                result.restored += 1
            elif op == "rename":
                src = Path(e["src"])
                if not dst.exists() or (src.exists() and not same_place(src, dst)):
                    result.skipped.append(f"can't rename back: {dst}")
                    continue
                rename_in_two_steps(dst, src)
                result.restored += 1
            elif op == "mkdir":
                d = Path(e["path"])
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            if op in ("copy", "move") and processed is not None:
                processed.discard(Path(e["src"]), e.get("size"))
        except OSError as err:
            result.skipped.append(f"{dst or e.get('path')}: {err}")
            failed += 1
    return failed


def same_place(a: Path, b: Path) -> bool:
    """The same file/folder for Windows ('light Novel' == 'Light Novel')."""
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def rename_in_two_steps(src: Path, dst: Path) -> None:
    """Rename in place; via a temporary name, so a case-only change works on Windows too."""
    if dst.exists() and not same_place(src, dst):
        raise FileExistsError(f"already exists: {dst}")
    tmp = src.with_name(src.name + ".renaming")
    os.rename(src, tmp)
    os.rename(tmp, dst)


def _read(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a half-written last line from a crash
    return entries


class Processed:
    """'<path>|<size>' of every source file already sorted (same idea as the PowerShell sorter)."""

    def __init__(self, path: Path):
        self.path = path
        self.keys: set[str] = set()
        self._removed: set[str] = set()  # forgotten here: not taken back from the file on save
        self._lock = threading.Lock()    # sorting threads add while the window may save
        if path.is_file():
            self.keys = self._read()

    def _read(self) -> set[str]:
        try:
            return {line for line in self.path.read_text(encoding="utf-8").splitlines() if line}
        except OSError:
            return set()

    @staticmethod
    def key(path: Path, size: int | None = None) -> str:
        if size is None:
            size = path.stat().st_size
        return f"{os.path.normcase(str(path))}|{size}"

    def contains(self, book: Book) -> bool:
        try:
            return all(self.key(f) in self.keys for f in book.files)
        except OSError:
            return False

    def add(self, book: Book) -> None:
        for f in book.files:
            try:
                k = self.key(f)
            except OSError:
                continue
            with self._lock:
                self.keys.add(k)
                self._removed.discard(k)

    def remove(self, book: Book) -> None:
        """Forget that a book was sorted (it shows up again on the next scan)."""
        for f in book.files:
            try:
                self.keys.discard(self.key(f))
                self._removed.add(self.key(f))
            except OSError:
                pass

    def discard(self, path: Path, size: int | None) -> None:
        if size is not None:
            self.keys.discard(self.key(path, size))
            self._removed.add(self.key(path, size))

    def save(self) -> None:
        """Merged with the file as it is now: the window and a scheduled '--auto' run never undo each other's
        entries. Written to a temp file first (never half a list)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.keys |= self._read() - self._removed
            text = "".join(f"{k}\n" for k in sorted(self.keys))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)


_SAVE_LOCK = threading.Lock()


def _save_new(folder: Path, suffix: str, data: bytes) -> Path:
    """data in the next free numbered file of folder (00001.json, 00002.json ...) - never one another book's
    thread took at the same moment (up to 3 books are sorted at once)."""
    folder.mkdir(parents=True, exist_ok=True)
    with _SAVE_LOCK:
        n = len(list(folder.iterdir())) + 1
        while True:
            saved = folder / f"{n:05d}{suffix}"
            try:
                with open(saved, "xb") as fh:  # exclusive: fails if the name is taken
                    fh.write(data)
                return saved
            except FileExistsError:
                n += 1


def keep_old_contents(journal: Journal, parked: Path, path: Path) -> None:
    """Save a file before the app rewrites it (a metadata.json) and journal it - undo writes it back."""
    saved = _save_new(parked / "contents", path.suffix, path.read_bytes())
    journal.write(op="content", dst=str(path), saved=str(saved))


def keep_old_tags(journal: Journal, parked: Path, path: Path, kept: set | None = None) -> None:
    """Save a file's tags before the app changes them (once per run and file) and journal it, so
    undo can write them back. parked = <state>\\replaced\\<run id>."""
    from .tagger import tag_snapshot  # noqa: PLC0415
    key = os.path.normcase(str(path))
    if kept is not None:
        if key in kept:
            return
        kept.add(key)
    snapshot = tag_snapshot(path)
    if snapshot is None:
        return
    saved = _save_new(parked / "tags", ".tags", snapshot)
    journal.write(op="tags", dst=str(path), saved=str(saved))
