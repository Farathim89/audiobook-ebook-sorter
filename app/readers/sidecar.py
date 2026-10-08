"""Metadata files next to a book: Audiobookshelf metadata.json, Calibre/ABS metadata.opf, *.opf."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from ..core.models import Book, Metadata
from ..core.text import clean_title, parse_index, parse_year, split_people
from . import safe
from .opf import parse_opf

_SERIES_RE = re.compile(r"^(?P<name>.+?)\s*#\s*(?P<idx>[\d.]+)$")


def read_sidecars(book: Book, abs_json: bool = True) -> Metadata:
    """abs_json=False skips Audiobookshelf's metadata.json - in a sorted library ABS may have guessed it
    from the folder layout (e.g. a kind folder taken as the author)."""
    candidates: list[Path] = []
    folder = book.folder or _own_folder(book)
    if folder:
        candidates += ([folder / "metadata.json"] if abs_json else []) + [folder / "metadata.opf"]
        candidates += sorted(folder.glob("*.opf"))
        candidates += [folder / "ncc.html"]  # DAISY 2.02 (Swedish library audiobooks): narrator, language
    candidates += [f.with_suffix(".opf") for f in book.files]

    meta, seen = Metadata(), set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        reader = {".json": read_abs_json, ".html": read_daisy_ncc}.get(path.suffix.lower(), read_opf_file)
        meta.fill_from(reader(path))
    return meta


def _own_folder(book: Book) -> Path | None:
    """A .cbz (or loose ebook) alone in its folder - 'Volume 01\\x.cbz' + metadata.json: that folder."""
    from ..core.models import BOOK_EXTS  # noqa: PLC0415
    parent = book.main_file.parent
    if parent == book.source_root or book.source_root not in parent.parents:
        return None
    try:
        others = [p for p in parent.iterdir() if p.suffix.lower() in BOOK_EXTS and p not in book.files]
    except OSError:
        return None
    return None if others else parent


@safe
def read_opf_file(path: Path) -> Metadata:
    return parse_opf(path.read_bytes())


@safe
def read_daisy_ncc(path: Path) -> Metadata:
    """A DAISY 2.02 talking book's ncc.html: <meta name="dc:creator" ...>, ncc:narrator, dc:language..."""
    text = path.read_text(encoding="utf-8", errors="replace")
    metas: dict[str, list[str]] = {}
    for tag in re.findall(r"<meta\b[^>]*>", text, re.I):
        name = re.search(r'name="([^"]+)"', tag)
        content = re.search(r'content="([^"]*)"', tag)
        if name and content:
            metas.setdefault(name.group(1).lower(), []).append(html.unescape(content.group(1)).strip())
    first = lambda k: (metas.get(k) or [None])[0]  # noqa: E731
    return Metadata(
        title=clean_title(first("dc:title")),
        authors=[a for v in metas.get("dc:creator", []) for a in split_people(v)],
        narrators=[n for v in metas.get("ncc:narrator", []) for n in split_people(v)],
        year=parse_year(first("dc:date")),
        publisher=first("dc:publisher") or None,
        language=first("dc:language") or None,
        description=first("dc:description") or None,
    )


@safe
def read_abs_json(path: Path) -> Metadata:
    """Audiobookshelf's 'Store metadata with item' file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = Metadata(
        title=clean_title(data.get("title")),
        authors=[a for name in data.get("authors") or [] for a in split_people(name)],
        narrators=[n for name in data.get("narrators") or [] for n in split_people(name)],
        year=parse_year(data.get("publishedYear")),
        isbn=data.get("isbn") or None,
        asin=data.get("asin") or None,
        publisher=data.get("publisher") or None,
        language=data.get("language") or None,
        description=(data.get("description") or "").strip() or None,
    )
    series = (data.get("series") or [None])[0]
    if series:
        m = _SERIES_RE.match(series)
        meta.series = m.group("name") if m else series
        meta.series_index = parse_index(m.group("idx")) if m else None
    return meta
