"""Zips in Incoming that hold books ('At Night, I Become a Monster by Yoru Sumino.zip' - an epub and a pdf):
unpacked into a folder of the same name next to the zip, so the scan sees the books. The zip itself goes to
Done\\Unpacked zips (nothing is deleted, nothing is overwritten)."""
from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from .models import BOOK_EXTS
from .scanner import walk_files

SETTLE_SECONDS = 60  # a zip changed in the last minute may still be downloading


def _safe_parts(name: str) -> tuple[str, ...] | None:
    """The member's path inside the target folder, or None for '../x', 'C:/x', '/x' (never outside it)."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or any(part in ("", "..") or ":" in part for part in p.parts):
        return None
    return p.parts


def _free(path: Path) -> Path:
    n = 2
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return out


def unpack_archives(sources: Iterable[Path], done: Path, now: float | None = None) -> list[str]:
    """Unpack every book zip under the sources. Returns one line per zip that was unpacked or skipped."""
    now = now or time.time()
    lines = []
    for src in sources:
        if not Path(src).is_dir():
            continue
        for z in [p for p in walk_files(Path(src)) if p.suffix.lower() == ".zip"]:
            try:
                if now - z.stat().st_mtime < SETTLE_SECONDS or not zipfile.is_zipfile(z):
                    continue
                with zipfile.ZipFile(z) as zf:
                    members = [(i, _safe_parts(i.filename)) for i in zf.infolist() if not i.is_dir()]
                    if not any(parts and Path(parts[-1]).suffix.lower() in BOOK_EXTS for _, parts in members):
                        continue  # no book inside: not ours
                    target = z.parent / z.stem
                    if target.exists():
                        lines.append(f"{z.name}: not unpacked, '{target.name}' already exists")
                        continue
                    work = z.parent / f"{z.stem}.unpacking"  # the scan ignores it until it is complete
                    if work.exists():
                        shutil.rmtree(work)  # left over from an unpack that was cut off - ours alone
                    for info, parts in members:
                        if parts is None:
                            continue
                        out = work.joinpath(*parts)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as fin, open(out, "wb") as fout:
                            shutil.copyfileobj(fin, fout)
                work.rename(target)
                keep = _free(done / "Unpacked zips" / z.name)
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(z), str(keep))
                lines.append(f"{z.name}: unpacked to '{target.name}', the zip is in {keep.parent}")
            except (OSError, zipfile.BadZipFile, RuntimeError) as err:  # damaged / password: left alone
                lines.append(f"{z.name}: not unpacked ({err})")
    return lines
