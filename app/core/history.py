"""Run journals (for undo) and the processed list (to skip books already sorted).

Every file operation is appended to <state>/history/<run id>.jsonl the moment it
happens, so even a crashed or cancelled run can be undone.
"""
from __future__ import annotations

import json
import os
import shutil
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
        while self.path.exists():
            n += 1
            self.path = history_dir / f"{self.run_id}-{n}.jsonl"
        self.run_id = self.path.stem
        self._fh = open(self.path, "a", encoding="utf-8")
        self.write(op="run", mode=mode, time=time.strftime("%Y-%m-%d %H:%M:%S"))

    def write(self, **entry) -> None:
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()

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
    # files we re-tagged after copying: their expected size is the size after tagging
    final_size = {e["dst"]: e.get("size") for e in entries if e.get("op") == "update"}
    for e in reversed(entries):
        op = e.get("op")
        dst = Path(e["dst"]) if "dst" in e else None
        try:
            if op in ("copy", "create"):
                if not dst.exists():
                    continue
                size = final_size.get(e["dst"], e.get("size"))
                if size is not None and dst.stat().st_size != size:
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
    run.path.rename(run.path.with_name(f"{run.path.stem}.undone.jsonl"))
    if processed is not None:
        processed.save()
    return result


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
        if path.is_file():
            self.keys = {line for line in path.read_text(encoding="utf-8").splitlines() if line}

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
                self.keys.add(self.key(f))
            except OSError:
                pass

    def remove(self, book: Book) -> None:
        """Forget that a book was sorted (it shows up again on the next scan)."""
        for f in book.files:
            try:
                self.keys.discard(self.key(f))
            except OSError:
                pass

    def discard(self, path: Path, size: int | None) -> None:
        if size is not None:
            self.keys.discard(self.key(path, size))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(f"{k}\n" for k in sorted(self.keys)), encoding="utf-8")
