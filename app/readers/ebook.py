"""Embedded ebook metadata: epub (OPF) and mobi/azw/azw3 (EXTH header).

PDFs are deliberately not read: their Title/Author fields are mostly junk
("Microsoft Word - doc1.docx", "Administrator"), so PDFs go by file name.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..core.models import Metadata
from ..core.text import clean_title, parse_year, split_people
from . import safe
from .opf import local, parse_opf


@safe
def read_epub(path: Path) -> Metadata:
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(el.attrib["full-path"] for el in container.iter() if local(el.tag) == "rootfile")
        meta = parse_opf(z.read(opf_path))
    meta.title = clean_title(meta.title)
    return meta


# EXTH record types
_EXTH_AUTHOR, _EXTH_PUBLISHER, _EXTH_ISBN, _EXTH_DATE = 100, 101, 104, 106
_EXTH_ASIN, _EXTH_ASIN2, _EXTH_TITLE, _EXTH_LANGUAGE = 113, 504, 503, 524


@safe
def read_mobi(path: Path) -> Metadata:
    with open(path, "rb") as fh:
        data = fh.read(1 << 20)  # record 0 (with the EXTH header) sits near the start
    if data[60:68] not in (b"BOOKMOBI", b"TEXtREAd"):
        return Metadata()
    rec0 = struct.unpack_from(">I", data, 78)[0]
    if data[rec0 + 16:rec0 + 20] != b"MOBI":
        return Metadata()
    header_len, = struct.unpack_from(">I", data, rec0 + 20)
    encoding = "utf-8" if struct.unpack_from(">I", data, rec0 + 28)[0] == 65001 else "cp1252"
    name_off, name_len = struct.unpack_from(">II", data, rec0 + 84)
    meta = Metadata(title=data[rec0 + name_off:rec0 + name_off + name_len].decode(encoding, "replace") or None)

    if struct.unpack_from(">I", data, rec0 + 128)[0] & 0x40:
        pos = rec0 + 16 + header_len
        if data[pos:pos + 4] == b"EXTH":
            count, = struct.unpack_from(">I", data, pos + 8)
            p = pos + 12
            for _ in range(count):
                rtype, rlen = struct.unpack_from(">II", data, p)
                value = data[p + 8:p + rlen].decode(encoding, "replace").strip()
                p += rlen
                if not value:
                    continue
                if rtype == _EXTH_AUTHOR:
                    meta.authors += split_people(value)
                elif rtype == _EXTH_TITLE:
                    meta.title = value
                elif rtype == _EXTH_ISBN:
                    meta.isbn = meta.isbn or value.replace("-", "")
                elif rtype == _EXTH_DATE:
                    meta.year = meta.year or parse_year(value)
                elif rtype in (_EXTH_ASIN, _EXTH_ASIN2):
                    meta.asin = meta.asin or value
                elif rtype == _EXTH_PUBLISHER:
                    meta.publisher = value
                elif rtype == _EXTH_LANGUAGE:
                    meta.language = value
    meta.title = clean_title(meta.title)
    return meta


READERS = {".epub": read_epub, ".mobi": read_mobi, ".azw3": read_mobi, ".azw": read_mobi}


def read_ebook(files: list[Path]) -> Metadata:
    """Merge the embedded metadata of every format of one book (best format first)."""
    meta = Metadata()
    for f in files:
        reader = READERS.get(f.suffix.lower())
        if reader:
            meta.fill_from(reader(f))
    return meta
