"""Data model shared by the scanner, readers, planner and (later) the GUI."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path


class BookType(str, Enum):
    AUDIO = "audiobook"
    EBOOK = "ebook"
    COMIC = "comic"


class Status(str, Enum):
    READY = "ready"      # metadata came from tags or sidecar files
    CHECK = "check"      # some of it was guessed from file/folder names
    UNKNOWN = "unknown"  # no usable title


AUDIO_EXTS = frozenset({".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma"})
EBOOK_EXTS = frozenset({".epub", ".azw3", ".mobi", ".azw", ".pdf"})
COMIC_EXTS = frozenset({".cbz", ".cbr"})
BOOK_EXTS = AUDIO_EXTS | EBOOK_EXTS | COMIC_EXTS

# When one ebook exists in several formats, the first one listed is read first.
EBOOK_PREFERENCE = (".epub", ".azw3", ".mobi", ".azw", ".pdf")


def book_type_for(path: Path) -> BookType | None:
    ext = path.suffix.lower()
    if ext in AUDIO_EXTS:
        return BookType.AUDIO
    if ext in EBOOK_EXTS:
        return BookType.EBOOK
    if ext in COMIC_EXTS:
        return BookType.COMIC
    return None


def _empty(value) -> bool:
    return value is None or value == "" or value == []


@dataclass
class Metadata:
    title: str | None = None
    subtitle: str | None = None  # 'I Can Go Back to My World Whenever I Want! Volume 2' (the volume may be only here)
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: float | None = None  # also the volume number of manga/comics
    year: int | None = None
    isbn: str | None = None
    asin: str | None = None
    publisher: str | None = None
    language: str | None = None
    description: str | None = None
    cover_url: str | None = None

    def is_empty(self) -> bool:
        return all(_empty(getattr(self, f.name)) for f in fields(self))

    def fill_from(self, other: Metadata) -> list[str]:
        """Copy fields that are empty here but set in `other`; return the names filled."""
        filled = []
        for f in fields(self):
            value = getattr(other, f.name)
            if _empty(getattr(self, f.name)) and not _empty(value):
                setattr(self, f.name, list(value) if isinstance(value, list) else value)
                filled.append(f.name)
        return filled


@dataclass(eq=False)
class Book:
    type: BookType
    files: list[Path]
    source_root: Path
    folder: Path | None = None  # a folder that holds only this book
    extras: list[Path] = field(default_factory=list)  # covers, cue sheets, booklets...
    meta: Metadata = field(default_factory=Metadata)
    sources: dict[str, str] = field(default_factory=dict)  # field name -> where it came from
    status: Status = Status.UNKNOWN
    notes: list[str] = field(default_factory=list)
    manga: bool = False  # set when AniList confirms it; picks the "(Manga)" series suffix
    looked_up: bool = False  # the online lookup has run for this book (Sort does it first otherwise)
    kind_known: bool = False  # comics: manga-or-comic was decided (AniList asked, a name hint, or by hand)
    light_novel: bool = False  # AniList knows it as a NOVEL, or the title says so
    candidates: list = field(default_factory=list)  # online matches (providers.Candidate), best first
    credits: list[str] = field(default_factory=list)  # names marked illustrator/translator in its files
    doubt: str = ""  # an online source disagrees with our metadata (keeps the book on "check")
    ai: dict = field(default_factory=dict)  # the AI's last answer for this book (see core.ai)

    @property
    def main_file(self) -> Path:
        return self.files[0]

    @property
    def author(self) -> str | None:
        return self.meta.authors[0] if self.meta.authors else None

    def absorb(self, other: Metadata, source: str) -> None:
        """Fill empty fields from `other`, remembering the source of each one."""
        for name in self.meta.fill_from(other):
            self.sources[name] = source

    def override(self, other: Metadata, source: str) -> None:
        """Replace every field that `other` has (a confirmed online match, or a manual edit)."""
        for f in fields(Metadata):
            value = getattr(other, f.name)
            if not _empty(value):
                setattr(self.meta, f.name, list(value) if isinstance(value, list) else value)
                self.sources[f.name] = source
