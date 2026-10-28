"""What a book leaves behind in Incoming once its files are gone: cover.jpg, ABS's metadata.json,
an .opf or .nfo... Those go to the Done folder with the originals, so Incoming ends up empty."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .models import BOOK_EXTS

LEFTOVER_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".json", ".opf", ".nfo", ".txt",
                           ".xml", ".cue", ".sfv", ".url", ".ini", ".db",
                           ".smil", ".html", ".htm", ".css", ".js"})  # + DAISY player files


def leftovers(folder: Path, source_root: Path) -> list[Path]:
    """The files left in a book's own folder, when nothing but leftovers is there (else [])."""
    if folder == source_root or source_root not in folder.parents or not folder.is_dir():
        return []
    files = [p for p in folder.rglob("*") if p.is_file()]
    if any(p.suffix.lower() not in LEFTOVER_EXTS or p.suffix.lower() in BOOK_EXTS for p in files):
        return []  # another book (or something we don't know) still lives here
    return sorted(files)


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def done_dir(done: Path, today: date | None = None) -> Path:
    """Done\\<yyyy-mm-dd>: the day a book's originals left Incoming (for emptying Done after N days)."""
    return done / (today or date.today()).isoformat()


def old_done_days(done: Path, days: int, today: date | None = None) -> list[Path]:
    """The Done\\<yyyy-mm-dd> folders older than `days` days (none when days is 0)."""
    if days <= 0 or not done.is_dir():
        return []
    today = today or date.today()
    old = []
    for d in sorted(done.iterdir()):
        if d.is_dir() and _DAY_RE.match(d.name):
            try:
                if (today - date.fromisoformat(d.name)).days > days:
                    old.append(d)
            except ValueError:
                continue
    return old


def recycle_bin_on(path: Path) -> bool:
    """False when Windows is set to delete files on this drive at once ('NukeOnDelete') - then
    'to the Recycle Bin' would really mean 'gone for good'."""
    import ctypes  # noqa: PLC0415
    import winreg  # noqa: PLC0415
    root = str(path.resolve().anchor)  # 'H:\\'
    DRIVE_FIXED = 3
    if ctypes.windll.kernel32.GetDriveTypeW(root) != DRIVE_FIXED:
        return False  # network shares, USB sticks: no Recycle Bin - 'to the Recycle Bin' would delete for good
    buf = ctypes.create_unicode_buffer(64)
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"
    if ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(root, buf, len(buf)):
        guid = re.search(r"\{[0-9a-fA-F-]+\}", buf.value)
        if guid:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{key}\Volume\{guid.group(0)}") as k:
                    return int(winreg.QueryValueEx(k, "NukeOnDelete")[0]) == 0
            except OSError:
                pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return int(winreg.QueryValueEx(k, "NukeOnDelete")[0]) == 0
    except OSError:
        return True  # Windows' default: the Recycle Bin is on


def to_recycle_bin(path: Path) -> None:
    """Send a file or folder to the Windows Recycle Bin (it can be restored from there)."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    if not recycle_bin_on(path):
        raise OSError(f"the Recycle Bin is switched off for {path.drive or path} - nothing deleted "
                      "(Windows would delete it for good)")
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path.resolve()) + "\0", None,
                         FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or op.fAnyOperationsAborted:
        raise OSError(f"could not move to the Recycle Bin: {path}")


def drop_empty_parents(folder: Path, stop: Path) -> None:
    """Remove folder and its parents up to (not including) stop, as long as they are empty."""
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()  # only when empty
        except OSError:
            return
        folder = folder.parent


def folder_size(folder: Path) -> tuple[int, int]:
    """(bytes, files) under folder - (0, 0) when it doesn't exist."""
    total = count = 0
    if folder.is_dir():
        for f in folder.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
                    count += 1
            except OSError:
                pass
    return total, count


def human_size(n: int) -> str:
    for unit, size in (("TB", 2**40), ("GB", 2**30), ("MB", 2**20)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n // 1024} KB"
