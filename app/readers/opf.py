"""OPF package metadata: used inside epubs and as metadata.opf sidecar files (Calibre/ABS)."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from ..core.models import Metadata
from ..core.text import parse_index, parse_year, split_people

_ISBN_RE = re.compile(r"(?:urn:isbn:|isbn:?)?\s*([\d\- ]{9,17}[\dXx])", re.I)


def local(tag: str) -> str:
    """'{http://purl.org/dc/elements/1.1/}title' -> 'title'."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_opf(data: bytes) -> Metadata:
    root = ET.fromstring(data)
    md = next((el for el in root.iter() if local(el.tag) == "metadata"), None)
    meta = Metadata()
    if md is None:
        return meta

    creators: list[tuple[str | None, str, str | None]] = []  # (id, name, role)
    roles: dict[str, str] = {}
    collections: dict[str | None, str] = {}
    positions: dict[str, str] = {}
    for el in md.iter():
        tag = local(el.tag)
        text = (el.text or "").strip()
        attrs = {local(k): v.strip() for k, v in el.attrib.items()}
        if tag == "title":
            meta.title = meta.title or text or None
        elif tag == "creator" and text:
            creators.append((attrs.get("id"), text, attrs.get("role")))
        elif tag == "date":
            meta.year = meta.year or parse_year(text)
        elif tag == "identifier":
            _identifier(meta, text, attrs.get("scheme", ""))
        elif tag == "description" and text:
            plain = html.unescape(re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I))
            meta.description = meta.description or re.sub(r"<[^>]+>", "", plain).strip() or None
        elif tag == "publisher":
            meta.publisher = meta.publisher or text or None
        elif tag == "language":
            meta.language = meta.language or text or None
        elif tag == "meta":
            name, content = attrs.get("name"), attrs.get("content")
            prop, refines = attrs.get("property"), attrs.get("refines", "").lstrip("#")
            if name == "calibre:series":
                meta.series = content or None
            elif name == "calibre:series_index":
                meta.series_index = parse_index(content)
            elif prop == "belongs-to-collection":
                collections[attrs.get("id")] = text
            elif prop == "group-position" and refines:
                positions[refines] = text
            elif prop == "role" and refines:
                roles[refines] = text

    for cid, name, role in creators:
        role = role or roles.get(cid or "") or "aut"
        if role == "aut":
            meta.authors += split_people(name)
        elif role == "nrt":
            meta.narrators += split_people(name)
    if not meta.series and collections:
        cid, name = next(iter(collections.items()))
        meta.series = name or None
        meta.series_index = parse_index(positions.get(cid or ""))
    return meta


def _identifier(meta: Metadata, text: str, scheme: str) -> None:
    hint = f"{scheme} {text}".lower()
    if "asin" in hint or "amazon" in hint:
        meta.asin = meta.asin or text.split(":")[-1].strip() or None
        return
    m = _ISBN_RE.fullmatch(text.strip())
    if m and ("isbn" in hint or m.group(1).replace("-", "").replace(" ", "").isdigit()):
        digits = re.sub(r"[^\dXx]", "", m.group(1)).upper()
        if len(digits) in (10, 13):
            meta.isbn = meta.isbn or digits
