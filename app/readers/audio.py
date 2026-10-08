"""Audiobook tags (mp3/ID3, m4b/m4a/MP4, flac/ogg/opus Vorbis) via mutagen."""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import mutagen
from mutagen.id3 import ID3

from ..core.models import Metadata
from ..core.text import clean_title, parse_index, parse_year, same_text, split_people

log = logging.getLogger(__name__)

# The same field under its ID3, MP4 and Vorbis names (Audible/beets/ABS conventions included).
KEYS = {
    "title": ["TIT2", "©nam", "title"],
    "album": ["TALB", "©alb", "album"],
    "artist": ["TPE1", "©ART", "artist"],
    "albumartist": ["TPE2", "aART", "albumartist", "album artist"],
    "composer": ["TCOM", "©wrt", "composer"],
    "narrator": ["TXXX:NARRATOR", "TXXX:narrator", "----:com.apple.iTunes:NARRATOR",
                 "----:com.apple.iTunes:narrator", "©nrt", "narrator"],
    "date": ["TDRC", "TYER", "TDRL", "©day", "date", "year"],
    "series": ["TXXX:SERIES", "TXXX:series", "MVNM", "----:com.apple.iTunes:SERIES",
               "----:com.apple.iTunes:series", "©mvn", "series"],
    "series_part": ["TXXX:SERIES-PART", "TXXX:series-part", "MVIN",
                    "----:com.apple.iTunes:SERIES-PART", "----:com.apple.iTunes:series-part",
                    "©mvi", "series-part", "seriespart"],
    "asin": ["TXXX:ASIN", "TXXX:AUDIBLE_ASIN", "----:com.apple.iTunes:ASIN",
             "----:com.apple.iTunes:AUDIBLE_ASIN", "asin", "audible_asin"],
}

# Per-track titles that say nothing about the book.
_TRACK_TITLE_RE = re.compile(r"^(?:chapter|track|part|disc|cd|kapitel)?\s*\d+\b", re.I)


@lru_cache(maxsize=8192)
def read_tags(path: Path) -> dict[str, str]:
    tags = load_tags(path)
    out: dict[str, str] = {}
    if tags is None:
        return out
    for name, keys in KEYS.items():
        for key in keys:
            try:
                value = _text(tags.get(key))
            except (KeyError, ValueError, TypeError):
                value = None
            if value:
                out[name] = value
                break
    return out


def album_of(path: Path) -> str | None:
    return read_tags(path).get("album")


def read_audiobook(files: list[Path]) -> Metadata:
    """Book-level metadata from the first tagged file of an audiobook."""
    tags = next((t for t in map(read_tags, files) if t), {})
    if not tags:
        return Metadata()
    multi = len(files) > 1
    title = tags.get("title")
    if multi or not title or _TRACK_TITLE_RE.match(title):
        title = tags.get("album") or title
    authors = split_people(tags.get("albumartist") or tags.get("artist"))
    narrators = split_people(tags.get("narrator"))
    composer = tags.get("composer")
    if not narrators and composer and not any(same_text(composer, a) for a in authors):
        narrators = split_people(composer)  # Audible/ABS put the narrator in composer
    return Metadata(
        title=clean_title(title),
        authors=authors,
        narrators=narrators,
        series=clean_title(tags.get("series")),
        series_index=parse_index(tags.get("series_part")),
        year=parse_year(tags.get("date")),
        asin=tags.get("asin"),
    )


def load_tags(path: Path):
    """The tag object of an audio file (ID3 / MP4 / Vorbis), or None."""
    try:
        f = mutagen.File(path)
        if f is not None and f.tags is not None:
            return f.tags
    except Exception as e:  # noqa: BLE001
        log.debug("mutagen failed on %s: %s", path, e)
    if path.suffix.lower() == ".mp3":  # tags on a file mutagen can't parse as audio
        try:
            return ID3(path)
        except Exception:  # noqa: BLE001
            return None
    return None


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if hasattr(value, "text"):  # ID3 frame
        value = value.text[0] if value.text else None
    if isinstance(value, bytes):  # MP4 freeform atom
        value = value.decode("utf-8", "replace")
    if isinstance(value, tuple):
        value = value[0]
    text = str(value).strip() if value is not None else ""
    return text or None
