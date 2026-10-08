"""Folders and options. The defaults are neutral (shareable); the real folders are chosen in
Settings (asked on first run) and saved to %APPDATA%\\BookSorter\\settings.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

SETTINGS_FILE = Path(os.environ.get("APPDATA") or Path.home()) / "BookSorter" / "settings.json"
DEFAULT_BASE = Path.home() / "Book Sorter"


@dataclass
class Folders:
    # Neutral defaults for a fresh install (the first-run Settings dialog asks for real folders).
    # Sorted output goes to a staging folder OUTSIDE every library; the user moves books in by hand.
    audio_incoming: Path = DEFAULT_BASE / "Incoming" / "AudioBooks"
    books_incoming: Path = DEFAULT_BASE / "Incoming" / "Books"
    audio_sorted: Path = DEFAULT_BASE / "Sorted" / "AudioBooks"
    books_sorted: Path = DEFAULT_BASE / "Sorted" / "Books"
    review: Path = DEFAULT_BASE / "Sorted" / "Review"
    duplicates: Path = DEFAULT_BASE / "Sorted" / "Duplicates"  # extra copies moved out of Incoming
    auto_move_duplicates: bool = False  # auto-sort also moves extra copies / books already in the library
    done: Path = DEFAULT_BASE / "Sorted" / "Done"  # originals of sorted books + their leftover cover/metadata
    move_originals: bool = False  # sorting MOVES the originals out of Incoming (else copies, keeps them)
    done_days: int = 0  # Done\<day> folders older than this go to the Recycle Bin (0 = keep forever)
    # Online-lookup cache, run history (undo) and the processed list.
    state: Path = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BookSorter"
    audible_region: str = "us"
    storytel_locale: str = "se"   # Storytel is asked for Swedish-looking books and when Audible finds nothing
    # Extra online sources (like Audiobookshelf's): they only fill gaps - description, cover, year, ISBN.
    use_storytel: bool = True
    use_itunes: bool = True
    use_google_books: bool = True
    use_openlibrary: bool = True
    google_books_key: str = ""    # optional; without it Google often answers "too many requests"
    use_mangadex: bool = True     # official volume covers for manga
    abs_url: str = ""             # your Audiobookshelf, e.g. http://localhost:13378/audiobookshelf (Tools > Sort collections)
    abs_api_key: str = ""         # ABS: Settings > API Keys (or Users > your user > API token)
    fandom_wikis: str = ""        # "One Piece = onepiece.fandom.com" lines: volume titles from Fandom wikis
    fandom_auto: bool = False     # also try <series>.fandom.com for series not in the list
    swedish_sorted: Path | None = None  # Swedish audiobooks go here when set (else with the others)
    write_tags: bool = True  # write the corrected metadata into the sorted files
    theme: str = "Gold (dark)"  # colour theme of the window
    to_m4b: bool = False     # sorting turns multi-file audiobooks into one chaptered .m4b
    # xHE-AAC -> plain AAC (ABS can't play xHE). Decoded by Windows' own xHE decoder - ffmpeg loses ~24%
    # of the packets (dropouts) - so on other systems the conversion is refused instead.
    fix_xhe_aac: bool = True
    auto_minutes: int = 0    # watch mode: scan + sort sure books every N minutes (0 = off)
    split_audio: bool = True        # AudioBooks Sorted\Light Novels, \Audiobooks
    audio_ln_dir: str = "Light Novels"
    audio_dir: str = "Audiobooks"
    split_books: bool = True        # Books Sorted\Manga, \Light Novels, \EBooks, \Comics
    manga_dir: str = "Manga"
    light_novel_dir: str = "Light Novels"
    ebook_dir: str = "EBooks"
    comic_dir: str = "Comics"
    extra_sources: list[Path] = field(default_factory=list)   # scanned besides the two Incoming folders
    # Library folders (e.g. Audiobookshelf's): the app never writes there - sorting, tools, nothing.
    protected: list[Path] = field(default_factory=list)

    def is_protected(self, path: Path) -> bool:
        p = os.path.normcase(str(path))
        for folder in self.protected:
            f = os.path.normcase(str(folder)).rstrip("\\/")
            if p == f or p.startswith(f + os.sep):
                return True
        return False

    @property
    def sources(self) -> list[Path]:
        return [self.audio_incoming, self.books_incoming, *self.extra_sources]

    @property
    def kind_dirs(self) -> list[str]:
        return [self.manga_dir, self.light_novel_dir, self.ebook_dir, self.comic_dir]

    def library_roots(self) -> list[Path]:
        """Every folder whose sub-folders are author folders (for one spelling per author/series)."""
        roots = [self.audio_sorted, *([self.swedish_sorted] if self.swedish_sorted else [])]
        if self.split_audio:
            roots += [self.audio_sorted / self.audio_ln_dir, self.audio_sorted / self.audio_dir]
        if self.split_books:
            roots += [self.books_sorted / d for d in self.kind_dirs]
        return roots + [self.books_sorted]


def load_folders(path: Path = SETTINGS_FILE) -> Folders:
    folders = Folders()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return folders
    for f in fields(Folders):
        if f.name not in data:
            continue
        value, default = data[f.name], getattr(folders, f.name)
        if f.name == "swedish_sorted":
            value = Path(value) if value else None
        elif isinstance(default, list):
            value = [Path(v) for v in value]
        elif isinstance(default, Path):
            value = Path(value)
        elif isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            value = int(value)
        setattr(folders, f.name, value)
    if "done" not in data:  # older settings: next to the Duplicates folder
        folders.done = folders.duplicates.parent / "Done"
    return folders


def save_folders(folders: Folders, path: Path = SETTINGS_FILE) -> None:
    def plain(v):
        if isinstance(v, list):
            return [str(x) for x in v]
        return str(v) if isinstance(v, Path) else v
    data = {k: plain(v) for k, v in asdict(folders).items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
