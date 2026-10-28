"""Everything you own: Audiobookshelf's libraries (with listening progress), library folders on disk and the
sorter's staging folders - read only, nothing is ever written there."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from ..config import Settings
from ...core.abs_index import split_series  # the sorter's (it knows '#01-07' ranges)
from ...providers.abs import AbsClient  # the sorter's
from .models import AUDIO, EBOOK, Owned, Series, series_key
from ...core.text import fold  # the sorter's

AUDIO_EXTS = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma"}
BOOK_EXTS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".cbz", ".cbr"}
COMIC_EXTS = {".cbz", ".cbr"}
_SPELLING_EXTRAS = {"too", "the", "series", "novel", "novels", "lightnovel", "s"}  # folded
_NUM_TITLE_RE = re.compile(r"^(\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?)?\s+-\s+(.+)$")   # '3 - Title' / '1-4 - Box'
_SERIES_RE = re.compile(r"^(.*?)\s+#\s*(\d+(?:\.\d+)?)")
Progress = Callable[[str], None]


def kind_of(name: str, fmt: str, comic: bool = False) -> str:
    """The kind from a library / folder name ('Audiobooks - Light Novels', 'Manga', 'EBooks')."""
    n = name.lower()
    if comic or "manga" in n:
        return "manga" if "comic" not in n else "comic"
    if "light novel" in n:
        return "light novel"
    return "audiobook" if fmt == AUDIO else "ebook"


# -- Audiobookshelf ------------------------------------------------------------------------------
def from_abs(settings: Settings, log: Progress = lambda _: None) -> list[Owned]:
    if not (settings.use_abs and settings.abs_url and settings.abs_api_key):
        return []
    abs_ = AbsClient(settings.abs_url, settings.abs_api_key)
    progress = {p.get("libraryItemId"): p for p in (abs_.me().get("mediaProgress") or [])}
    out: list[Owned] = []
    for lib in abs_.libraries():
        if lib.get("mediaType") != "book":
            continue
        log(f"Audiobookshelf: {lib.get('name')}")
        for it in abs_.library_items(lib["id"]):
            media = it.get("media") or {}
            md = media.get("metadata") or {}
            fmt = AUDIO if (media.get("numAudioFiles") or media.get("numTracks")) else EBOOK
            path = it.get("path") or ""
            comic = Path(path).suffix.lower() in COMIC_EXTS or (media.get("ebookFormat") or "") in ("cbz", "cbr")
            series, index = _first_series(md.get("seriesName") or "")
            p = progress.get(it.get("id")) or {}
            out.append(Owned(
                title=md.get("title") or Path(path).name, authors=_people(md.get("authorName")),
                series=series, index=index, fmt=fmt, kind=kind_of(lib.get("name") or "", fmt, comic),
                source=f"ABS: {lib.get('name')}", path=path, asin=md.get("asin") or "",
                finished=bool(p.get("isFinished")), progress=float(p.get("progress") or 0),
                item_id=it.get("id") or ""))
    return out


def _first_series(value: str) -> tuple[str | None, float | None]:
    """'Solo Leveling #2, Yen Audio #5' -> ('Solo Leveling', 2.0)."""
    first = split_series(value)[0] if value else ""
    if not first:
        return None, None
    m = _SERIES_RE.match(first)
    return (m.group(1).strip(), float(m.group(2))) if m else (first, None)


_ROLE_RE = re.compile(r"(?i)[\s(\-–]+(?:illustrator|translator|translated by|editor|cover art|foreword)\)?\s*$")


def _people(value: str | None) -> list[str]:
    """'Fuse, Mitz Vah - illustrator' -> ['Fuse'] - the illustrator / translator is no author."""
    names = [p.strip() for p in (value or "").split(",") if p.strip()]
    return [n for n in names if not _ROLE_RE.search(n)] or names


# -- folders on disk ---------------------------------------------------------------------------
_KIND_DIRS = ("audiobooks", "light novels", "ebooks", "manga", "comics", "books", "audiobooks - light novels")


def from_folders(roots: list[str], label: str, log: Progress = lambda _: None,
                 skip: set[str] | None = None) -> list[Owned]:
    """Shelfkeeper / Audiobookshelf layout: [<kind>\\]<Author>\\[<Series>\\]<N - Title>\\files. A folder with
    audio files is one audiobook; every ebook / comic file is one book."""
    skip = skip or set()
    out: list[Owned] = []
    for root in map(Path, roots):
        if not root.is_dir():
            continue
        log(f"{label}: {root}")
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if _skipped(d, skip):
                dirnames[:] = []
                continue
            audio = [f for f in filenames if Path(f).suffix.lower() in AUDIO_EXTS]
            if audio:
                if (o := _owned(root, d, AUDIO, label)) is not None:
                    out.append(o)
                dirnames[:] = []  # disc folders inside belong to this book
                continue
            for f in filenames:
                if Path(f).suffix.lower() in BOOK_EXTS and not _skipped(d / f, skip):
                    if (o := _owned(root, d / f, EBOOK, label)) is not None:
                        out.append(o)
    return out


def _skipped(p: Path, skip: set[str]) -> bool:
    s = os.path.normcase(str(p))
    return any(s == k or s.startswith(k + os.sep) for k in skip)


def _owned(root: Path, item: Path, fmt: str, label: str) -> Owned | None:
    parts = list(item.relative_to(root).parts)
    if fmt == EBOOK:
        stem = Path(parts[-1]).stem
        # 'Author\Series\3 - Title\Title.epub' -> the book folder names it; 'Author\Series\Title.epub' -> the file
        if len(parts) >= 2 and _NUM_TITLE_RE.match(parts[-2]):
            parts = parts[:-1]
        else:
            parts[-1] = stem
    kind_dir = ""
    if parts and parts[0].lower() in _KIND_DIRS:
        kind_dir = parts.pop(0)
    if len(parts) < 2:
        return None  # a loose file at the top: no author folder
    comic = item.suffix.lower() in COMIC_EXTS
    author, rest = parts[0], parts[1:]
    series, index, title = None, None, rest[-1]
    if len(rest) >= 2:
        series = re.sub(r"\s*\((?:Manga|Comic)\)$", "", rest[0])
    if m := _NUM_TITLE_RE.match(title):
        index, title = float(m.group(1)), m.group(2)
    if series is None and index is not None:
        series = None  # '3 - Title' without a series folder: just a title
    kind = kind_of(kind_dir or str(root), fmt, comic or "(manga)" in (rest[0].lower() if rest else ""))
    return Owned(title=title, authors=[author], series=series, index=index, fmt=fmt, kind=kind,
                 source=label, path=str(item))


# -- all of it ---------------------------------------------------------------------------------
def collect(settings: Settings, log: Progress = lambda _: None) -> list[Owned]:
    owned = from_abs(settings, log)
    seen = {os.path.normcase(o.path.replace("/", os.sep)) for o in owned if o.path}
    owned += from_folders(settings.library_folders, "Folder", log, seen)
    owned += from_folders(settings.staging_folders, "Staging", log)
    return owned


def group(owned: list[Owned], hidden: set[str] = frozenset()) -> tuple[dict[str, Series], list[Owned]]:
    """Series by key (audio + ebook of one novel series together, its manga apart); and the standalones."""
    series: dict[str, Series] = {}
    alone: list[Owned] = []
    for o in owned:
        if not o.series:
            alone.append(o)
            continue
        key = series_key(o.series, o.kind in ("manga", "comic"))
        s = series.get(key)
        if s is None:
            s = series[key] = Series(key, o.series, o.author, o.kind)
        s.owned.append(o)
        if o.kind == "light novel" and s.kind != "light novel":
            s.kind = "light novel"  # an audiobook series whose ebooks are light novels
    # one series written two ways: '...in the Real World' (ebooks) / '...in the Real World, Too' (audio)
    for short in sorted(series, key=len):
        s = series.get(short)
        if s is None:
            continue
        long_ = next((k for k, o in series.items() if k != short and k.endswith("|manga") == short.endswith("|manga")
                      and k.split("|")[0].startswith(short.split("|")[0])
                      # only a filler word is a spelling ('..., Too'); '86--EIGHTY-SIX Alter' or '... (Year 2)' is
                      # another series
                      and k.split("|")[0][len(short.split("|")[0]):] in _SPELLING_EXTRAS
                      and fold(o.author) == fold(s.author)), None)
        if long_ is not None:
            series[long_].owned += s.owned
            del series[short]
    # hidden: after the spelling merge, so hiding either spelling hides the one series
    for k in [k for k, s in series.items()
              if k in hidden or any(series_key(o.series, o.kind in ("manga", "comic")) in hidden for o in s.owned)]:
        del series[k]
    for s in series.values():  # the name / author most volumes use
        s.name = _most(o.series for o in s.owned) or s.name
        s.author = _most(o.author for o in s.owned) or s.author
    return series, alone


def _most(values) -> str:
    from collections import Counter  # noqa: PLC0415
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else ""


def same_name(a: str, b: str) -> bool:
    return series_key(a) == series_key(b) or fold(a) == fold(b)
