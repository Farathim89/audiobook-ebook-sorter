"""Get a book's own cover: embedded audio artwork, the epub's cover image, or a comic's first page."""
from __future__ import annotations

import logging
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import mutagen

from ..readers.audio import load_tags
from ..readers.opf import local
from .models import Book, BookType
from .text import natural_key

log = logging.getLogger(__name__)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def extract_cover(book: Book) -> tuple[bytes, str] | None:
    """(image bytes, '.jpg' / '.png') or None."""
    try:
        if book.type is BookType.AUDIO:
            return _audio_cover(book.main_file)
        if book.type is BookType.COMIC:
            return _comic_cover(book.main_file)
        epub = next((f for f in book.files if f.suffix.lower() == ".epub"), None)
        return _epub_cover(epub) if epub else None
    except Exception as e:  # noqa: BLE001 - a missing cover is never an error
        log.debug("no cover from %s: %s", book.main_file, e)
        return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a JPEG / PNG / WebP from its header, or None."""
    import struct  # noqa: PLC0415
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if kind == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if kind == b"VP8L":
            b = int.from_bytes(data[21:25], "little")
            return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        return None
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # start of frame
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def _ext_for(data: bytes) -> str:
    return ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"


def _audio_cover(path: Path) -> tuple[bytes, str] | None:
    tags = load_tags(path)
    if tags is not None:
        if "covr" in tags and tags["covr"]:  # MP4
            data = bytes(tags["covr"][0])
            return data, _ext_for(data)
        apic = [v for k, v in tags.items() if k.startswith("APIC")] if hasattr(tags, "items") else []
        if apic:  # ID3: prefer the front cover
            pic = next((p for p in apic if getattr(p, "type", None) == 3), apic[0])
            return pic.data, _ext_for(pic.data)
    if path.suffix.lower() != ".flac":
        return None
    pictures = getattr(mutagen.File(path), "pictures", None)
    if pictures:
        return pictures[0].data, _ext_for(pictures[0].data)
    return None


def _epub_cover(path: Path) -> tuple[bytes, str] | None:
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(el.attrib["full-path"] for el in container.iter() if local(el.tag) == "rootfile")
        root = ET.fromstring(z.read(opf_path))
        cover_id = None
        items: dict[str, tuple[str, str, str]] = {}  # id -> (href, media-type, properties)
        for el in root.iter():
            tag = local(el.tag)
            if tag == "meta" and el.attrib.get("name") == "cover":
                cover_id = el.attrib.get("content")
            elif tag == "item":
                items[el.attrib.get("id", "")] = (el.attrib.get("href", ""), el.attrib.get("media-type", ""),
                                                  el.attrib.get("properties", ""))
        href = None
        if cover_id and cover_id in items:
            href = items[cover_id][0]
        if href is None:
            href = next((h for h, _, props in items.values() if "cover-image" in props.split()), None)
        if href is None:
            href = next((h for i, (h, mt, _) in items.items()
                         if mt.startswith("image/") and "cover" in (i + h).lower()), None)
        if href is None:
            return None
        name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
        data = z.read(name)
        return data, _ext_for(data)


def _comic_cover(path: Path) -> tuple[bytes, str] | None:
    try:
        with zipfile.ZipFile(path) as z:
            return _first_image(z.namelist(), z.read)
    except zipfile.BadZipFile:
        import rarfile
        with rarfile.RarFile(path) as r:
            return _first_image(r.namelist(), r.read)


def _first_image(names: list[str], read) -> tuple[bytes, str] | None:
    images = sorted((n for n in names if n.lower().endswith(IMAGE_EXTS) and "__macosx" not in n.lower()),
                    key=natural_key)
    if not images:
        return None
    data = read(images[0])
    return data, _ext_for(data)
