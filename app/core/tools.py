"""Finding (and, when asked, downloading) ffmpeg / ffprobe.

Looked for, in order: a ffmpeg\\ folder next to BookSorter.exe (or the project), the app's own
download folder (%LOCALAPPDATA%\\BookSorter\\ffmpeg), then the PATH.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
TOOLS_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BookSorter" / "ffmpeg"
_HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def find_tool(name: str) -> str | None:
    """Full path of ffmpeg / ffprobe, or None."""
    for folder in (_HERE / "ffmpeg", _HERE, TOOLS_DIR):
        exe = folder / f"{name}.exe"
        if exe.is_file():
            return str(exe)
    return shutil.which(name)


def download_ffmpeg(on_progress: Callable[[int, int], None] | None = None,
                    cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download the ffmpeg 'essentials' build (gyan.dev), check its SHA-256, keep ffmpeg.exe,
    ffprobe.exe and the licence in TOOLS_DIR. Returns TOOLS_DIR."""
    headers = {"User-Agent": "BookSorter/0.1"}
    with urllib.request.urlopen(urllib.request.Request(FFMPEG_ZIP + ".sha256", headers=headers), timeout=30) as r:
        expected = r.read().decode("ascii", "replace").split()[0].strip().lower()
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TOOLS_DIR.parent) as td:
        zpath = Path(td) / "ffmpeg.zip"
        digest = hashlib.sha256()
        with urllib.request.urlopen(urllib.request.Request(FFMPEG_ZIP, headers=headers), timeout=60) as r, \
                open(zpath, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                if cancelled():
                    raise OSError("cancelled")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        if digest.hexdigest().lower() != expected:
            raise OSError("the downloaded ffmpeg does not match its published checksum - not installed")
        with zipfile.ZipFile(zpath) as z:
            wanted = {"ffmpeg.exe", "ffprobe.exe", "LICENSE"}
            for info in z.infolist():
                name = info.filename.rsplit("/", 1)[-1]
                if name in wanted and ("/bin/" in info.filename or name == "LICENSE"):
                    target = TOOLS_DIR / name
                    with z.open(info) as src, open(target.with_name(name + ".partial"), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(target.with_name(name + ".partial"), target)
    if not (TOOLS_DIR / "ffmpeg.exe").is_file():
        raise OSError("ffmpeg.exe was not found in the download")
    return TOOLS_DIR
