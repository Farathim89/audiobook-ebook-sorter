"""Audiobookshelf's metadata.json - downloads often come with one (in ABS's own format), and ABS reads it
BEFORE the audio tags. So a sorted book's metadata.json must say what the sorter decided, not what the
uploader wrote ('Veronica Taylor' as the author, an old series name...). Chapters, genres, tags and the
description of the original stay as they are."""
from __future__ import annotations

import json
from pathlib import Path

from .metadata import format_index
from .models import Metadata

ABS_KEYS = {"title", "authors", "narrators", "series", "chapters"}


def read_abs_json(path: Path) -> dict | None:
    """The file as a dict when it is ABS's format, else None (another tool's metadata.json is left alone)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and len(ABS_KEYS & set(data)) >= 2 else None


def corrected(original: dict, m: Metadata) -> dict:
    out = dict(original)
    if m.title:
        out["title"] = m.title
    if m.subtitle is not None:
        out["subtitle"] = m.subtitle or None
    if m.authors:
        out["authors"] = list(m.authors)
    if m.narrators or "narrators" in out:
        out["narrators"] = list(m.narrators)
    out["series"] = [f"{m.series} #{format_index(m.series_index)}" if m.series_index is not None else m.series] \
        if m.series else []
    if m.year:
        out["publishedYear"] = str(m.year)
    for key in ("publisher", "isbn", "asin", "language"):
        if getattr(m, key, None):
            out[key] = getattr(m, key)
    return out


def dumps(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
