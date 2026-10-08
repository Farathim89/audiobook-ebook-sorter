"""ComicInfo.xml inside cbz (zip) / cbr (rar). Most scene releases don't have one."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..core.models import Metadata
from ..core.text import clean_title, parse_index, parse_year, split_people
from . import safe
from .opf import local


@safe
def read_comic(path: Path) -> Metadata:
    xml = _comicinfo(path)
    if not xml:
        return Metadata()
    root = ET.fromstring(xml)
    v = {local(el.tag).lower(): (el.text or "").strip() for el in root}
    number = v.get("number") or v.get("volume")
    return Metadata(
        title=clean_title(v.get("title")),
        series=clean_title(v.get("series")),
        series_index=parse_index(number),
        year=parse_year(v.get("year")),
        authors=split_people(v.get("writer")),
        publisher=v.get("publisher") or None,
        language=v.get("languageiso") or None,
    )


def _comicinfo(path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as z:
            return _pick(z.namelist(), z.read)
    except zipfile.BadZipFile:
        pass  # a .cbz that is really a rar, or a .cbr
    try:
        import rarfile  # needs UnRAR on PATH; without it cbr files go by name only
        with rarfile.RarFile(path) as r:
            return _pick(r.namelist(), r.read)
    except Exception:  # noqa: BLE001
        return None


def _pick(names: list[str], read) -> bytes | None:
    name = next((n for n in names if n.lower().rsplit("/", 1)[-1] == "comicinfo.xml"), None)
    return read(name) if name else None
