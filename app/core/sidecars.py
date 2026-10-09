"""Files Audiobookshelf reads next to a book.

Audiobooks: desc.txt (description), reader.txt (narrators), cover.jpg
Ebooks/comics: metadata.opf (only the MAIN author - epubs often list the illustrator 2nd)

Never write a NEW metadata.json (ABS's own file, it overrides everything) - but one that came with the
download is corrected (executor._fix_abs_metadata), else ABS shows the uploader's mistakes.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .covers import extract_cover
from .metadata import format_index
from .models import Book, BookType

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIBLE_BEATS_EMBEDDED_BELOW = 1000  # px: a smaller embedded audiobook cover loses to Audible's 2400 px one


def sidecar_files(book: Book, target: Path) -> dict[Path, bytes | str]:
    """{path: content}; a str value is a URL to download (the cover).

    Cover: see choose_cover().
    """
    m = book.meta
    out: dict[Path, bytes | str] = {}
    if book.type is BookType.AUDIO:
        if m.description:
            out[target / "desc.txt"] = m.description.encode("utf-8")
        if m.narrators:
            out[target / "reader.txt"] = ", ".join(m.narrators).encode("utf-8")
    else:
        out[target / "metadata.opf"] = opf_xml(book).encode("utf-8")
    cover = choose_cover(book)
    if cover and cover.source != "with the book":  # 'with the book' is copied with the audio files
        out[target / f"cover{cover.ext}"] = cover.data if cover.data is not None else cover.url
    return out


@dataclass
class CoverChoice:
    source: str              # where it comes from, for the details panel
    data: bytes | None = None
    url: str | None = None   # downloaded when sorting
    ext: str = ".jpg"
    path: Path | None = None  # a local file (to show without reading it twice)


def choose_cover(book: Book) -> CoverChoice | None:
    """The cover sorting will write (sidecar_files uses this; the details panel shows it):
    an image next to the book > MangaDex's English volume cover > the book's own embedded cover /
    first page > any online one (e.g. a Japanese-only MangaDex cover). A bigger English cover found
    by ISBN (OnlineLookup.check_cover) beats a small local one; a thumbnail cover.jpg loses to a
    much bigger cover inside the book."""
    m = book.meta
    src = book.sources.get("cover_url")
    extra = next((p for p in book.extras if p.suffix.lower() in IMAGE_EXTS and p.stem.lower() in ("cover", "folder")), None)
    if extra is not None:
        return CoverChoice("with the book", path=extra, ext=extra.suffix.lower())
    if m.cover_url and src == "bigger-cover":
        return CoverChoice("bigger online cover (same ISBN)", url=m.cover_url)
    image = _image_next_to(book) if book.type is not BookType.AUDIO else None
    inner = extract_cover(book) if image else None
    if image and inner and len(inner[0]) > image.stat().st_size * 2:
        return CoverChoice("inside the book", data=inner[0], ext=inner[1])  # the cover.jpg is a thumbnail
    if image:
        return CoverChoice("cover file next to the book", data=image.read_bytes(), ext=image.suffix.lower(), path=image)
    if m.cover_url and src == "mangadex":
        ext = Path(m.cover_url.split("?")[0]).suffix.lower()
        return CoverChoice("MangaDex (English edition)", url=m.cover_url, ext=ext if ext in IMAGE_EXTS else ".jpg")
    if own := extract_cover(book):
        if book.type is BookType.AUDIO and m.cover_url and src == "audible":
            from .covers import image_size  # noqa: PLC0415
            if ((image_size(own[0]) or (0, 0))[0]) < AUDIBLE_BEATS_EMBEDDED_BELOW:
                return CoverChoice("Audible (2400 px)", url=m.cover_url)  # embedded art is often 300-500 px
        return CoverChoice("inside the book", data=own[0], ext=own[1])
    if m.cover_url:
        return CoverChoice(f"online ({src or 'unknown'})", url=m.cover_url)
    return None


def best_local_cover(book: Book) -> bytes | None:
    """The cover the book would get without going online (same choice as sidecar_files)."""
    image = _image_next_to(book) if book.type is not BookType.AUDIO else None
    inner = extract_cover(book)
    if image and not (inner and len(inner[0]) > image.stat().st_size * 2):
        return image.read_bytes()
    return inner[0] if inner else None


def _image_next_to(book: Book) -> Path | None:
    """cover.jpg / folder.jpg in the folder that holds only this book (a .cbz or ebook folder)."""
    from ..readers.sidecar import _own_folder  # noqa: PLC0415
    folder = book.folder or _own_folder(book)
    if folder is None:
        return None
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return None
    for p in entries:
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and p.stem.lower() in ("cover", "folder"):
            return p
    return None


def opf_xml(book: Book) -> str:
    m = book.meta
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">',
             '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">',
             f"    <dc:title>{escape(m.title or '')}</dc:title>"]
    if book.author:
        lines.append(f'    <dc:creator opf:role="aut">{escape(book.author)}</dc:creator>')
    if m.description:
        lines.append(f"    <dc:description>{escape(m.description)}</dc:description>")
    if m.year:
        lines.append(f"    <dc:date>{m.year}</dc:date>")
    if m.language:
        lines.append(f"    <dc:language>{escape(m.language)}</dc:language>")
    if m.publisher:
        lines.append(f"    <dc:publisher>{escape(m.publisher)}</dc:publisher>")
    if m.isbn:
        lines.append(f'    <dc:identifier opf:scheme="ISBN">{escape(m.isbn)}</dc:identifier>')
    if m.asin:
        lines.append(f'    <dc:identifier opf:scheme="ASIN">{escape(m.asin)}</dc:identifier>')
    if m.series:
        series = f"{m.series} ({'Manga' if book.manga else 'Comic'})" if book.type is BookType.COMIC else m.series
        lines.append(f'    <meta name="calibre:series" content="{escape(series, {chr(34): "&quot;"})}"/>')
        if m.series_index is not None:
            lines.append(f'    <meta name="calibre:series_index" content="{format_index(m.series_index)}"/>')
    lines += ["  </metadata>", "</package>", ""]
    return "\n".join(lines)


def download(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "BookSorter/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()
