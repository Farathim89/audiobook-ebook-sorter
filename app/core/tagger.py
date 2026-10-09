"""Write the corrected metadata into the SORTED copies (never into originals in copy mode).

Audiobooks (mp3 / m4b / m4a / flac / ogg / opus): album = book title, album artist + artist = author,
composer + NARRATOR = narrator (narrator never goes into artist - ABS would make it an author),
SERIES / SERIES-PART (+ MVNM/MVIN), ASIN, year. Per-track titles are kept, except in a one-file book.
Epub: calibre:series + series_index in the OPF (what the PowerShell sorter did with ebook-meta).
"""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import mutagen
from mutagen.id3 import ID3, MVIN, MVNM, TALB, TCOM, TDRC, TIT2, TPE1, TPE2, TXXX, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4FreeForm

from ..readers.opf import local
from .metadata import format_index, series_number
from .models import AUDIO_EXTS, Book, BookType

log = logging.getLogger(__name__)


def write_tags(path: Path, book: Book, whole_book: bool = False) -> bool:
    """Tag one sorted file; True when the file was changed. whole_book: the file is the entire book."""
    ext = path.suffix.lower()
    try:
        if book.type is BookType.AUDIO and ext in AUDIO_EXTS:
            return _audio(path, book, whole_book or len(book.files) == 1)
        if ext == ".epub" and book.meta.series:
            return _epub_series(path, book.meta.series, book.meta.series_index)
    except Exception as e:  # noqa: BLE001 - a file we can't tag is still sorted
        log.warning("could not write tags to %s: %s", path, e)
    return False


def _values(book: Book, whole_book: bool) -> dict[str, str]:
    m = book.meta
    v = {"album": m.title, "albumartist": book.author, "artist": ", ".join(m.authors),
         "narrator": ", ".join(m.narrators), "series": m.series,
         "series_part": series_number(book) if m.series else None,  # '1-4' for a box set
         "asin": m.asin, "year": str(m.year) if m.year else None}
    if whole_book:
        v["title"] = m.title
    return {k: x for k, x in v.items() if x}


def _audio(path: Path, book: Book, whole_book: bool) -> bool:
    v = _values(book, whole_book)
    if not v:
        return False
    ext = path.suffix.lower()
    if ext in (".aac", ".wma"):
        return False  # raw AAC has no tags; WMA isn't worth the risk
    if ext in (".m4b", ".m4a"):
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        tags = f.tags
        simple = {"title": "©nam", "album": "©alb", "albumartist": "aART", "artist": "©ART",
                  "narrator": "©wrt", "year": "©day", "series": "©mvn"}
        for k, atom in simple.items():
            if k in v:
                tags[atom] = [v[k]]
        if "series_part" in v and v["series_part"].isdigit():
            tags["©mvi"] = [int(v["series_part"])]
        for k, name in (("series", "SERIES"), ("series_part", "SERIES-PART"), ("asin", "ASIN"), ("narrator", "NARRATOR")):
            if k in v:
                tags[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(v[k].encode("utf-8"))]
        if "series" not in v:  # no series (any more): drop old series tags, ABS would still group it
            for atom in ("©mvn", "©mvi", "----:com.apple.iTunes:SERIES", "----:com.apple.iTunes:SERIES-PART"):
                tags.pop(atom, None)
        f.save()
        return True
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        frames = {"title": TIT2, "album": TALB, "albumartist": TPE2, "artist": TPE1, "narrator": TCOM,
                  "year": TDRC, "series": MVNM}
        for k, frame in frames.items():
            if k in v:
                tags.setall(frame.__name__, [frame(encoding=3, text=v[k])])
        if "series_part" in v:
            tags.setall("MVIN", [MVIN(encoding=3, text=v["series_part"])])
        for k, desc in (("series", "SERIES"), ("series_part", "SERIES-PART"), ("asin", "ASIN"), ("narrator", "NARRATOR")):
            if k in v:
                tags.setall(f"TXXX:{desc}", [TXXX(encoding=3, desc=desc, text=v[k])])
        if "series" not in v:
            for frame in ("MVNM", "MVIN", "TXXX:SERIES", "TXXX:SERIES-PART"):
                tags.delall(frame)
        tags.save(path)
        return True
    f = mutagen.File(path)  # flac / ogg / opus: Vorbis comments
    if f is None:
        return False
    if f.tags is None:
        f.add_tags()
    names = {"title": "title", "album": "album", "albumartist": "albumartist", "artist": "artist",
             "narrator": "composer", "year": "date", "series": "series", "series_part": "series-part", "asin": "asin"}
    for k, name in names.items():
        if k in v:
            f.tags[name] = [v[k]]
    if "narrator" in v:
        f.tags["narrator"] = [v["narrator"]]
    if "series" not in v:
        for name in ("series", "series-part"):
            f.tags.pop(name, None)
    f.save()
    return True


_SERIES_META_RE = re.compile(r'\s*<meta\b[^>]*\bname="calibre:series(?:_index)?"[^>]*/>', re.I)
_METADATA_END_RE = re.compile(r"</(?:[\w-]+:)?metadata>")


def _epub_series(path: Path, series: str, index: float | None) -> bool:
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_name = next(el.attrib["full-path"] for el in container.iter() if local(el.tag) == "rootfile")
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    opf = next(data for info, data in entries if info.filename == opf_name).decode("utf-8")
    lines = f'<meta name="calibre:series" content="{escape(series, {chr(34): "&quot;"})}"/>'
    if index is not None:
        lines += f'\n    <meta name="calibre:series_index" content="{format_index(index)}"/>'
    new_opf, n = _METADATA_END_RE.subn(lambda m: f"    {lines}\n  {m.group(0)}", _SERIES_META_RE.sub("", opf), count=1)
    if not n or new_opf == opf:
        return False
    tmp = path.with_name(path.name + ".tagging")
    with zipfile.ZipFile(tmp, "w") as out:
        for info, data in entries:
            if info.filename == opf_name:
                data = new_opf.encode("utf-8")
            # the mimetype entry must stay first and uncompressed
            out.writestr(info, data, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type)
    os.replace(tmp, path)
    return True


# -- old tags, for undo ---------------------------------------------------------
def _epub_opf_name(z: zipfile.ZipFile) -> str:
    container = ET.fromstring(z.read("META-INF/container.xml"))
    return next(el.attrib["full-path"] for el in container.iter() if local(el.tag) == "rootfile")


def tag_snapshot(path: Path) -> bytes | None:
    """Everything write_tags / the cover embedding may change in this file, to put back on undo:
    all its tags (with the cover inside) - or, for an epub, its .opf. None when there is nothing to keep."""
    import pickle  # noqa: PLC0415
    try:
        if path.suffix.lower() == ".epub":
            with zipfile.ZipFile(path) as z:
                name = _epub_opf_name(z)
                return pickle.dumps(("epub-opf", name, z.read(name)))
        if path.suffix.lower() == ".mp3":  # ID3 straight: mutagen.File gives up on odd mp3 frames
            from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: PLC0415
            try:
                return pickle.dumps(("id3", ID3(path)))
            except ID3NoHeaderError:
                return pickle.dumps(("id3", None))
        f = mutagen.File(path)
        if f is None:
            return None
        return pickle.dumps(("tags", f.tags))
    except Exception as e:  # noqa: BLE001
        log.warning("could not keep the old tags of %s: %s", path, e)
        return None


def restore_tags(path: Path, snapshot: bytes) -> None:
    """Put the tags from tag_snapshot back (raises OSError when it can't)."""
    import pickle  # noqa: PLC0415
    kind, *rest = pickle.loads(snapshot)  # noqa: S301 - our own file from this app's state folder
    try:
        if kind == "epub-opf":
            name, data = rest
            with zipfile.ZipFile(path) as z:
                entries = [(info, z.read(info.filename)) for info in z.infolist()]
            tmp = path.with_name(path.name + ".untagging")
            with zipfile.ZipFile(tmp, "w") as out:
                for info, old in entries:
                    out.writestr(info, data if info.filename == name else old,
                                 compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else info.compress_type)
            os.replace(tmp, path)
            return
        (tags,) = rest
        if kind == "id3":
            from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: PLC0415
            if tags is None:
                try:
                    ID3(path).delete(path)
                except ID3NoHeaderError:
                    pass
                return
            tags.save(path)  # the whole old ID3 block, as it was
            return
        f = mutagen.File(path)
        if f is None:
            raise OSError(f"not a tagged audio file any more: {path}")
        if tags is None:
            if f.tags is not None:
                f.delete()
            return
        if f.tags is None:
            f.add_tags()
        f.tags.clear()
        for key in tags.keys():
            f.tags[key] = tags[key]
        f.save()
    except OSError:
        raise
    except Exception as e:  # noqa: BLE001
        raise OSError(f"could not put the old tags back into {path}: {e}") from e
