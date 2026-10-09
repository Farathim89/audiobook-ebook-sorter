"""Changes to books that are ALREADY in a library: move, re-tag, convert to m4b, reorganize.

Audiobookshelf ("Store metadata with item") writes its own metadata.json into every item
folder, and that file beats tags and metadata.opf. So whenever we change what a sorted book
IS (title, series, chapters, audio files) we park that file - ABS writes a fresh one on the
next scan. A plain move keeps it (the metadata is still right).
Parked files go to <state>\\replaced\\<run id>\\..., and every step is journaled (undoable).
"""
from __future__ import annotations

import logging
import re
import shutil
from xml.sax.saxutils import escape as xml_escape
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Folders
from . import m4b
from .history import Journal, keep_old_tags, rename_in_two_steps, same_place
from .leftovers import done_dir, drop_empty_parents, leftovers, old_done_days, to_recycle_bin
from .models import Book
from .text import fold

log = logging.getLogger(__name__)
ABS_METADATA = "metadata.json"


class LibraryEditor:
    def __init__(self, folders: Folders, mode: str):
        self.folders = folders
        self.journal = Journal(folders.state / "history", mode)
        self.parked = folders.state / "replaced" / self.journal.run_id

    @property
    def run_id(self) -> str:
        return self.journal.run_id

    def close(self) -> None:
        self.journal.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- building blocks ----------------------------------------------------
    def move_file(self, src: Path, dst: Path) -> None:
        for p in (src, dst):
            if self.folders.is_protected(p):
                raise PermissionError(f"protected Audiobookshelf folder, not touched: {p}")
        if dst.exists():
            raise FileExistsError(f"already exists: {dst}")
        self.ensure_dir(dst.parent)
        size = src.stat().st_size
        shutil.move(str(src), str(dst))  # a rename on the same drive
        self.journal.write(op="move", src=str(src), dst=str(dst), size=size)

    def keep_tags(self, path: Path) -> None:
        """Before the first change to a file's tags in this run: save them, so undo puts them back."""
        keep_old_tags(self.journal, self.parked, path)

    def rename(self, src: Path, dst: Path) -> None:
        """Rename a file or folder in place - also when only the case changes ('light' -> 'Light'),
        which Windows needs in two steps."""
        for p in (src, dst):
            if self.folders.is_protected(p):
                raise PermissionError(f"protected Audiobookshelf folder, not touched: {p}")
        rename_in_two_steps(src, dst)
        self.journal.write(op="rename", src=str(src), dst=str(dst))

    def move_tree(self, src_dir: Path, dst_dir: Path) -> int:
        """Move every file of src_dir into dst_dir (keeping sub-folders), then drop empty folders."""
        n = 0
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                self.move_file(p, dst_dir / p.relative_to(src_dir))
                n += 1
        remove_empty_dirs(src_dir)
        return n

    def park(self, path: Path, root: Path) -> None:
        """Move a file out of the library into the parked folder (undoable)."""
        if path.is_file():
            self.move_file(path, self.parked / path.relative_to(root.anchor))

    def park_abs_metadata(self, book_dir: Path) -> None:
        self.park(book_dir / ABS_METADATA, book_dir)

    def ensure_dir(self, d: Path) -> None:
        if self.folders.is_protected(d):
            raise PermissionError(f"protected Audiobookshelf folder, not touched: {d}")
        missing = []
        while not d.exists():
            missing.append(d)
            d = d.parent
        for p in reversed(missing):
            p.mkdir()
            self.journal.write(op="mkdir", path=str(p))

    def updated(self, path: Path) -> None:
        self.journal.write(op="update", dst=str(path), size=path.stat().st_size)

    # -- tools ---------------------------------------------------------------
    def convert_to_m4b(self, book: Book, http, on_progress: Callable[[float], None] | None = None) -> Path:
        """A sorted multi-file audiobook becomes one chaptered m4b in its folder; the old audio files
        and ABS's metadata.json are parked."""
        folder = book.folder or book.main_file.parent
        if self.folders.is_protected(folder):
            raise PermissionError(f"protected Audiobookshelf folder, not touched: {folder}")
        out, source = m4b.make_m4b(book, folder, http, on_progress=on_progress)
        self.journal.write(op="create", dst=str(out), size=out.stat().st_size)
        expected = sum(m4b.duration(f) for f in book.files)
        if problem := self.play_test(out, expected):
            out.unlink()  # never trade the old files for a broken one
            raise OSError(f"the new m4b failed the play test ({problem}) - removed it, the old files stay")
        for f in book.files:
            self.park(f, folder)
        self.park_abs_metadata(folder)
        log.info("%s: m4b with chapters from %s", out.name, source)
        return out


    def play_test(self, path: Path, expected: float | None = None) -> str:
        """The Settings > Play test (full / quick); '' when it plays or testing is off."""
        if self.folders.play_test == "off":
            return ""
        return m4b.play_test(path, expected or None, full=self.folders.play_test == "full")

    def fix_xhe_aac(self, path: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
        """Replace an xHE-AAC file (ABS can't read it) by a plain-AAC one; the original is parked."""
        if self.folders.is_protected(path):
            raise PermissionError(f"protected Audiobookshelf folder, not touched: {path}")
        new = path.with_name(path.stem + ".aac-lc" + path.suffix)
        m4b.reencode_aac(path, new, cancelled)
        if problem := self.play_test(new, m4b.duration(path)):
            new.unlink()
            raise OSError(f"the AAC copy failed the play test ({problem}) - removed it, the original stays")
        self.park(path, path.parent)
        new.rename(path)
        self.journal.write(op="create", dst=str(path), size=path.stat().st_size)
        self.park_abs_metadata(path.parent)


    def repair_audio(self, path: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
        """A file with damaged audio packets (a bad download) re-encoded to clean AAC: players don't
        stumble over the broken bits any more (the few missing milliseconds stay missing). The original
        is parked - undo puts it back."""
        if self.folders.is_protected(path):
            raise PermissionError(f"protected Audiobookshelf folder, not touched: {path}")
        new = path.with_name(path.stem + ".repaired" + path.suffix)
        m4b.reencode_aac(path, new, cancelled)
        problem = self.play_test(new, m4b.duration(path)) or (
            "still damaged" if m4b.damaged_packets(new, cancelled) else "")
        if problem:
            new.unlink()
            raise OSError(f"the repaired copy failed the play test ({problem}) - removed it, the original stays")
        self.park(path, path.parent)
        new.rename(path)
        self.journal.write(op="create", dst=str(path), size=path.stat().st_size)
        self.park_abs_metadata(path.parent)


def find_xhe_aac(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*")) if p.suffix.lower() in (".m4b", ".m4a") and m4b.is_xhe_aac(p)]


PLAYABLE_EXTS = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".wav"}


def audio_files_under(roots: list[Path]) -> list[Path]:
    seen: dict[str, Path] = {}
    for root in roots:
        if root and root.is_dir():
            for p in root.rglob("*"):
                if p.suffix.lower() in PLAYABLE_EXTS and p.is_file():
                    seen.setdefault(str(p).lower(), p)
    return sorted(seen.values())


def play_test_library(files: list[Path], full: bool = False,
                      on_progress: Callable[[int, int, str], None] | None = None,
                      cancelled: Callable[[], bool] = lambda: False) -> tuple[int, list[tuple[Path, str]]]:
    """Read-only: play every file through ffmpeg (m4b.play_test). (files tested, [(file, problem)])."""
    bad: list[tuple[Path, str]] = []
    done = 0
    for i, f in enumerate(files, 1):
        if cancelled():
            break
        if on_progress:
            on_progress(i, len(files), f.name)
        problem = m4b.play_test(f, full=full, cancelled=cancelled)
        done += 1
        if problem:
            bad.append((f, problem))
    return done, bad


def remove_empty_dirs(top: Path) -> None:
    for d in sorted((p for p in top.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        top.rmdir()
    except OSError:
        pass


# -- splitting Books Sorted into Manga / Light Novels / EBooks / Comics ------
_SUFFIX_RE = re.compile(r"\s*\((Manga|Comic)\)$")
_LN_HINT_RE = re.compile(r"(?i)\blight novel\b|\(novel\)")


@dataclass
class ReorgResult:
    moved: list[str] = field(default_factory=list)    # "Author\Series -> Kind"
    merged: list[str] = field(default_factory=list)   # series joined into its "(Manga)" twin
    skipped: list[str] = field(default_factory=list)
    run_id: str = ""


def reorganize_books(folders: Folders, is_light_novel: Callable[[str], bool],
                     dry_run: bool = False) -> ReorgResult:
    """Move <Books Sorted>\\<Author>\\<Series> into <Books Sorted>\\<Kind>\\<Author>\\<Series>.

    Kind: "(Manga)" series -> Manga, "(Comic)" -> Comics, else Light Novels when
    is_light_novel(series) says so, else EBooks. A series that only lacks the "(Manga)" suffix
    of a twin next to it is merged into that twin (its metadata.opf gets the suffix too).
    """
    root = folders.books_sorted
    kinds = set(folders.kind_dirs)
    result = ReorgResult()
    plan: list[tuple[Path, Path, str]] = []  # (series dir, destination, note)
    for author in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in kinds):
        series_dirs = [p for p in author.iterdir() if p.is_dir()]
        suffixed = {fold(_SUFFIX_RE.sub("", p.name)): p for p in series_dirs if _SUFFIX_RE.search(p.name)}
        for s in sorted(series_dirs):
            m = _SUFFIX_RE.search(s.name)
            if m:
                kind = folders.manga_dir if m.group(1) == "Manga" else folders.comic_dir
                plan.append((s, root / kind / author.name / s.name, kind))
            elif fold(s.name) in suffixed:  # e.g. vol 7 sorted without "(Manga)" next to vol 1-6
                twin = suffixed[fold(s.name)]
                kind = folders.manga_dir if twin.name.endswith("(Manga)") else folders.comic_dir
                plan.append((s, root / kind / author.name / twin.name, f"merge:{twin.name}"))
            else:  # a series folder, or a book without series: <Author>\<Title>\files
                hinted = any(_LN_HINT_RE.search(p.name) for p in [s, *s.iterdir()])  # "Vol. 1 (novel)"
                kind = folders.light_novel_dir if hinted or is_light_novel(s.name) else folders.ebook_dir
                plan.append((s, root / kind / author.name / s.name, kind))
    if dry_run:
        result.moved = [f"{s.parent.name}\\{s.name} -> {note}" for s, _, note in plan]
        return result

    with LibraryEditor(folders, "reorganize") as ed:
        result.run_id = ed.run_id
        for src, dst, note in plan:
            label = f"{src.parent.name}\\{src.name}"
            if note.startswith("merge:"):
                series = note.split(":", 1)[1]
                for book_dir in sorted(p for p in src.iterdir() if p.is_dir()):
                    target = dst / book_dir.name
                    if target.exists():
                        result.skipped.append(f"{label}\\{book_dir.name}: already in {series}")
                        continue
                    ed.move_tree(book_dir, target)
                    opf = target / "metadata.opf"
                    if opf.is_file() and _set_opf_series(opf, series):
                        ed.updated(opf)
                    ed.park_abs_metadata(target)  # its series changed
                remove_empty_dirs(src)
                result.merged.append(f"{label} -> {series}")
                continue
            if dst.exists() and any(dst.iterdir()):
                # the series folder already exists in the kind folder: move book by book
                for book_dir in sorted(p for p in src.iterdir()):
                    if (dst / book_dir.name).exists():
                        result.skipped.append(f"{label}\\{book_dir.name}: already there")
                    elif book_dir.is_dir():
                        ed.move_tree(book_dir, dst / book_dir.name)
                    else:
                        ed.move_file(book_dir, dst / book_dir.name)
                remove_empty_dirs(src)
            else:
                ed.move_tree(src, dst)
            result.moved.append(f"{label} -> {note}")
        for author in [p for p in root.iterdir() if p.is_dir() and p.name not in kinds]:
            remove_empty_dirs(author)
    return result


def reorganize_audio(folders: Folders, is_light_novel: Callable[[str], bool],
                     dry_run: bool = False) -> ReorgResult:
    """Move <AudioBooks Sorted>\\<Author>\\<Series or Title> into \\Light Novels\\... or \\Audiobooks\\...

    Light novel: "light novel" / "(novel)" in the folder names, or is_light_novel(series/title)."""
    root = folders.audio_sorted
    kinds = {folders.audio_ln_dir, folders.audio_dir}
    result = ReorgResult()
    plan: list[tuple[Path, Path, str]] = []
    for author in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in kinds):
        for s in sorted(p for p in author.iterdir() if p.is_dir()):
            hinted = any(_LN_HINT_RE.search(p.name) for p in [s, *s.iterdir()])
            name = re.sub(r"^\d+(?:\.\d+)? - ", "", s.name)  # "<N> - Title" book folder without series
            kind = folders.audio_ln_dir if hinted or is_light_novel(name) else folders.audio_dir
            plan.append((s, root / kind / author.name / s.name, kind))
    if dry_run:
        result.moved = [f"{s.parent.name}\\{s.name} -> {kind}" for s, _, kind in plan]
        return result
    with LibraryEditor(folders, "reorganize-audio") as ed:
        result.run_id = ed.run_id
        for src, dst, kind in plan:
            label = f"{src.parent.name}\\{src.name}"
            if dst.exists():
                for book_dir in sorted(src.iterdir()):
                    if (dst / book_dir.name).exists():
                        result.skipped.append(f"{label}\\{book_dir.name}: already there")
                    elif book_dir.is_dir():
                        ed.move_tree(book_dir, dst / book_dir.name)
                    else:
                        ed.move_file(book_dir, dst / book_dir.name)
                remove_empty_dirs(src)
            else:
                ed.move_tree(src, dst)
            result.moved.append(f"{label} -> {kind}")
        for author in [p for p in root.iterdir() if p.is_dir() and p.name not in kinds]:
            remove_empty_dirs(author)
    return result



# -- updating manga that is already sorted: official covers + volume titles --------------
_VOLUME_DIR_RE = re.compile(r"^(\d+(?:\.\d+)?) - ")
COMIC_FILE_EXTS = (".cbz", ".cbr")


@dataclass
class MangaChange:
    folder: Path
    cover_url: str | None = None
    new_title: str | None = None
    year: int | None = None
    isbn: str | None = None

    @property
    def summary(self) -> str:
        parts = []
        if self.cover_url:
            parts.append("cover")
        if self.new_title:
            parts.append(f"title '{self.new_title}'")
        if self.year:
            parts.append(f"year {self.year}")
        if self.isbn:
            parts.append(f"ISBN {self.isbn}")
        return f"{self.folder.parent.name}\\{self.folder.name}: " + ", ".join(parts)


def cover_marker(opf: Path) -> str | None:
    """The cover URL a previous refresh recorded in metadata.opf (so it isn't fetched again)."""
    try:
        m = re.search(r'name="booksorter:cover" content="([^"]+)"', opf.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1).replace("&amp;", "&") if m else None


def plan_manga_refresh(folders: Folders, lookup, cancelled: Callable[[], bool] = lambda: False,
                       on_progress: Callable[[int, int, str], None] | None = None) -> list[MangaChange]:
    """What MangaDex / the Fandom wikis would add to the manga already in the Books library (read-only)."""
    from ..providers.fandom import parse_wiki_list  # noqa: PLC0415
    from ..readers.opf import parse_opf  # noqa: PLC0415
    root = folders.books_sorted / folders.manga_dir if folders.split_books else folders.books_sorted
    wikis = parse_wiki_list(folders.fandom_wikis)
    dirs = [d for d in sorted(root.glob("*/*/*")) if d.is_dir() and _VOLUME_DIR_RE.match(d.name)]
    changes = []
    for i, d in enumerate(dirs, 1):
        if cancelled():
            break
        if on_progress:
            on_progress(i, len(dirs), d.name)
        if not any(p.suffix.lower() in COMIC_FILE_EXTS for p in d.iterdir()):
            continue
        series = _SUFFIX_RE.sub("", d.parent.name)
        n = float(_VOLUME_DIR_RE.match(d.name).group(1))
        opf = d / "metadata.opf"
        meta = parse_opf(opf.read_bytes()) if opf.is_file() else None
        ch = MangaChange(d)
        if lookup.mangadex:
            try:
                url = lookup.mangadex.volume_cover(series, n, english_only=True)  # never the Japanese one
            except OSError:
                url = None
            if url and url != cover_marker(opf):  # not done before
                ch.cover_url = url
        wiki = wikis.get(fold(series))
        if wiki:
            try:
                info = lookup.fandom.volume(wiki[0], n, wiki[1], series=series)
            except OSError:
                info = None
            if info:
                base = f"{series}, Vol. {n:g}"
                if info.get("title") and (not meta or not (meta.title or "").endswith(info["title"])):
                    ch.new_title = f"{base}: {info['title']}"
                if info.get("year") and not (meta and meta.year):
                    ch.year = info["year"]
                if info.get("isbn") and not (meta and meta.isbn):
                    ch.isbn = info["isbn"]
        if ch.cover_url or ch.new_title or ch.year or ch.isbn:
            changes.append(ch)
    return changes


def apply_manga_refresh(folders: Folders, changes: list[MangaChange],
                        cancelled: Callable[[], bool] = lambda: False,
                        on_progress: Callable[[int, int, str], None] | None = None) -> tuple[str, int]:
    """Write the new covers / opf data, rename folders for new titles; old files are parked (undoable)."""
    from .covers import IMAGE_EXTS  # noqa: PLC0415
    from .metadata import format_index  # noqa: PLC0415
    from .models import BookType, Metadata  # noqa: PLC0415
    from .planner import clean_name  # noqa: PLC0415
    from .sidecars import download, opf_xml  # noqa: PLC0415
    from ..readers.opf import parse_opf  # noqa: PLC0415
    done = 0
    with LibraryEditor(folders, "manga-refresh") as ed:
        for i, ch in enumerate(changes, 1):
            if cancelled():
                break
            if on_progress:
                on_progress(i, len(changes), ch.folder.name)
            d = ch.folder
            if ch.cover_url:
                try:
                    data = download(ch.cover_url)
                except OSError as e:
                    log.warning("cover download failed for %s: %s", d.name, e)
                    data = None
                ext = Path(ch.cover_url).suffix.lower()
                ext = ext if ext in IMAGE_EXTS else ".jpg"
                old = [p for p in d.iterdir() if p.stem.lower() == "cover" and p.suffix.lower() in IMAGE_EXTS]
                if data and not any(p.stat().st_size == len(data) and p.read_bytes() == data for p in old):
                    for p in old:
                        ed.park(p, p.parent)
                    target = d / f"cover{ext}"
                    target.write_bytes(data)
                    ed.journal.write(op="create", dst=str(target), size=len(data))
            opf = d / "metadata.opf"
            marker = ch.cover_url or cover_marker(opf)  # where the cover came from, kept in the opf
            if ch.new_title or ch.year or ch.isbn or ch.cover_url:
                comic = next(p for p in d.iterdir() if p.suffix.lower() in COMIC_FILE_EXTS)
                meta = parse_opf(opf.read_bytes()) if opf.is_file() else Metadata()
                meta.series = _SUFFIX_RE.sub("", d.parent.name)
                meta.series_index = float(_VOLUME_DIR_RE.match(d.name).group(1))
                meta.title = ch.new_title or meta.title or f"{meta.series}, Vol. {format_index(meta.series_index)}"
                meta.year = meta.year or ch.year
                meta.isbn = meta.isbn or ch.isbn
                book = Book(BookType.COMIC, [comic], d, manga=d.parent.name.endswith("(Manga)"))
                book.meta = meta
                if opf.is_file():
                    ed.park(opf, opf.parent)
                text = opf_xml(book)
                if marker:
                    text = text.replace("  </metadata>", f'    <meta name="booksorter:cover" content="{xml_escape(marker)}"/>\n'
                                                         "  </metadata>", 1)
                xml = text.encode("utf-8")
                opf.write_bytes(xml)
                ed.journal.write(op="create", dst=str(opf), size=len(xml))
            ed.park_abs_metadata(d)
            if ch.new_title:
                new_dir = d.parent / clean_name(f"{format_index(float(_VOLUME_DIR_RE.match(d.name).group(1)))} - {ch.new_title}")
                if new_dir != d and not new_dir.exists():
                    ed.move_tree(d, new_dir)
            done += 1
        return ed.run_id, done



# -- moving duplicates out of Incoming -----------------------------------------------------
def move_duplicates(folders: Folders, moves: list, cancelled: Callable[[], bool] = lambda: False,
                    on_progress: Callable[[int, int, str], None] | None = None) -> tuple[str, int]:
    """Move extra copies (and books the library already has) from Incoming into the Duplicates folder,
    keeping their folder structure. Missing description/ISBN/year of the library copy are filled in from
    the copy that leaves. One undoable run; nothing is deleted."""
    done = 0
    with LibraryEditor(folders, "duplicates") as ed:
        for i, mv in enumerate(moves, 1):
            if cancelled():
                break
            book = mv.book
            if on_progress:
                on_progress(i, len(moves), book.meta.title or book.main_file.name)
            if mv.library is not None:
                _fill_library_copy(ed, mv.library, book)
            _move_out(ed, book, folders.duplicates)
            done += 1
        return ed.run_id, done


def _move_out(ed: LibraryEditor, book: Book, dest_base: Path) -> None:
    """Move a book out of Incoming into dest_base\\<incoming folder name>\\..., keeping its folders,
    with what it leaves behind (cover.jpg, metadata.json...); empty folders are removed."""
    dest_root = dest_base / book.source_root.name
    if book.folder is not None:
        ed.move_tree(book.folder, dest_root / book.folder.relative_to(book.source_root))
        drop_empty_parents(book.folder.parent, book.source_root)
        return
    for f in book.files:
        ed.move_file(f, dest_root / f.relative_to(book.source_root))
    for f in leftovers(book.main_file.parent, book.source_root):
        ed.move_file(f, dest_root / f.relative_to(book.source_root))
    drop_empty_parents(book.main_file.parent, book.source_root)


def find_sorted_originals(folders: Folders) -> tuple[list[Book], list[tuple[Path, Path]]]:
    """Books still in Incoming that were sorted already (or marked as already in the library), and
    (folder, incoming root) of folders holding only leftovers (cover.jpg, metadata.json...)."""
    import time  # noqa: PLC0415
    from .auto import SETTLE_SECONDS, still_downloading  # noqa: PLC0415
    from .history import Processed  # noqa: PLC0415
    from .pipeline import load_books  # noqa: PLC0415
    processed = Processed(folders.state / "processed.txt")
    sources = [p for p in folders.sources if p.is_dir()]
    books = [b for b in load_books(sources) if processed.contains(b) and not still_downloading(b)]
    orphans: list[tuple[Path, Path]] = []
    for root in sources:
        for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts)):
            if any(d == o or o in d.parents for o, _ in orphans):
                continue
            files = leftovers(d, root)
            if files and all(time.time() - f.stat().st_mtime > SETTLE_SECONDS for f in files):
                orphans.append((d, root))
    return books, orphans


def move_sorted_originals(folders: Folders, books: list[Book], orphans: list[tuple[Path, Path]] = (),
                          cancelled: Callable[[], bool] = lambda: False,
                          on_progress: Callable[[int, int, str], None] | None = None) -> tuple[str, int]:
    """Move the originals of sorted books - and folders holding only leftovers - from Incoming into
    the Done folder, keeping their folder structure. One undoable run; nothing is deleted."""
    done, total = 0, len(books) + len(orphans)
    dest = done_dir(folders.done)
    with LibraryEditor(folders, "done") as ed:
        for book in books:
            if cancelled():
                break
            done += 1
            if on_progress:
                on_progress(done, total, book.meta.title or book.main_file.name)
            _move_out(ed, book, dest)
        for folder, root in orphans:
            if cancelled():
                break
            done += 1
            if on_progress:
                on_progress(done, total, folder.name)
            ed.move_tree(folder, dest / root.name / folder.relative_to(root))
            drop_empty_parents(folder.parent, root)
        return ed.run_id, done


def empty_done(folders: Folders, recycle: Callable[[Path], None] = to_recycle_bin) -> list[Path]:
    """Send Done\\<day> folders older than folders.done_days days to the Recycle Bin. Returns them."""
    gone = []
    for d in old_done_days(folders.done, folders.done_days):
        if folders.is_protected(d):
            continue
        try:
            recycle(d)
            gone.append(d)
        except OSError as e:
            log.warning("Done folder: %s", e)
    return gone


def _fill_library_copy(ed: LibraryEditor, folder: Path, book: Book) -> None:
    """Give the library copy what it lacks (description, ISBN, year...) from the copy that leaves."""
    from .models import BookType, Metadata  # noqa: PLC0415
    from .sidecars import opf_xml  # noqa: PLC0415
    from ..readers.opf import parse_opf  # noqa: PLC0415
    m = book.meta
    if book.type is BookType.AUDIO:
        for name, text in (("desc.txt", m.description), ("reader.txt", ", ".join(m.narrators))):
            target = folder / name
            if text and not target.exists():
                data = text.encode("utf-8")
                target.write_bytes(data)
                ed.journal.write(op="create", dst=str(target), size=len(data))
        return
    opf = folder / "metadata.opf"
    if not opf.is_file():
        return
    have = parse_opf(opf.read_bytes())
    fill = Metadata(description=m.description, isbn=m.isbn, year=m.year, publisher=m.publisher, language=m.language)
    if not have.fill_from(fill):
        return
    marker = cover_marker(opf)
    lib_book = Book(book.type, [next(iter(sorted(folder.glob("*.*"))), opf)], folder.parent,
                    manga=folder.parent.name.endswith("(Manga)"))
    have.series = _SUFFIX_RE.sub("", have.series or "") or None
    lib_book.meta = have
    text = opf_xml(lib_book)
    if marker:
        text = text.replace("  </metadata>", f'    <meta name="booksorter:cover" content="{xml_escape(marker)}"/>\n'
                                             "  </metadata>", 1)
    ed.park(opf, opf.parent)
    data = text.encode("utf-8")
    opf.write_bytes(data)
    ed.journal.write(op="create", dst=str(opf), size=len(data))
    ed.park_abs_metadata(folder)



# -- correcting a book that is already in the library ---------------------------------------
def load_sorted_book(folders: Folders, folder: Path) -> Book | None:
    """The book in a sorted book folder, with its current metadata (tags / opf / folder names)."""
    from .metadata import read_local  # noqa: PLC0415
    from .pipeline import load_books  # noqa: PLC0415
    folder = Path(folder)
    book = next((b for b in load_books([folder.parent]) if b.folder == folder), None)
    if book is None:  # a single-file book whose folder holds nothing else (e.g. an ebook folder)
        from .grouper import group  # noqa: PLC0415
        from .scanner import scan  # noqa: PLC0415
        books = group(scan([folder]), folder.parent)
        book = books[0] if len(books) == 1 else None
        if book is not None:
            book.folder = folder
    if book is not None:
        from .models import Metadata  # noqa: PLC0415
        book.meta, book.sources = Metadata(), {}  # load_books read metadata.json - start over without it
        read_local(book, abs_json=False)  # ABS may have guessed it from our kind folders
        rel = folder.relative_to(_library_root(folders, folder))
        kinds = {folders.audio_dir, folders.audio_ln_dir, *folders.kind_dirs}
        kind = rel.parts[0] if rel.parts and rel.parts[0] in kinds else ""
        book.light_novel = kind in (folders.light_novel_dir, folders.audio_ln_dir)
        book.manga = kind == folders.manga_dir or folder.parent.name.endswith("(Manga)")
        book.kind_known = True  # the sorted folder already says manga or comic
    return book


def _library_root(folders: Folders, folder: Path) -> Path:
    for root in (folders.audio_sorted, folders.books_sorted, folders.swedish_sorted):
        if root and (root == folder or root in folder.parents):
            return root
    raise ValueError(f"not inside a sorted library: {folder}")


def edit_sorted_book(folders: Folders, folder: Path, values: dict) -> tuple[str, Path]:
    """Set author/title/series/number/narrators/year of a sorted book: the folder moves to where the
    new data puts it (same library and kind folder), files are re-tagged, sidecars rewritten, ABS's
    metadata.json parked. One undoable run. Returns (run id, new folder)."""
    from .metadata import edit_book  # noqa: PLC0415
    from .models import BookType  # noqa: PLC0415
    from .planner import Planner, clean_name  # noqa: PLC0415
    from .sidecars import opf_xml  # noqa: PLC0415
    from .tagger import write_tags  # noqa: PLC0415
    folder = Path(folder)
    book = load_sorted_book(folders, folder)
    if book is None:
        raise ValueError(f"no book found in {folder}")
    old_title = book.meta.title
    from .infolog import InfoLog, what_was_decided, what_was_read  # noqa: PLC0415
    read, before, asked = what_was_read(book), what_was_decided(book), dict(values)
    values = dict(values)
    light_novel = values.pop("light_novel", None)  # audiobooks: move to Light Novels / Audiobooks
    import copy  # noqa: PLC0415
    from .rules import Rules, book_kind  # noqa: PLC0415
    old_meta, old_kind = copy.deepcopy(book.meta), book_kind(book)
    author_dir = folder.parent.parent if book.meta.series else folder.parent
    if old_meta.authors and fold(author_dir.name) != fold(clean_name(old_meta.authors[0])):
        # the tags already say 'Shoji Goji', the folder still 'Goji Shoji': the folder's name is what you fixed
        old_meta.authors = [author_dir.name, *old_meta.authors[1:]]
    edit_book(book, **values)
    m = book.meta
    root = _library_root(folders, folder)
    rel = folder.relative_to(root)
    planner = Planner(folders)
    # keep the kind folder the book already lives in (Audiobooks, Light Novels, Manga...)
    kind_parts = rel.parts[: max(0, len(rel.parts) - (2 + (1 if _had_series(rel, folders) else 0)))]
    if light_novel is not None and book.type is BookType.AUDIO and folders.split_audio \
            and root == folders.audio_sorted:
        kind_parts = (folders.audio_ln_dir if light_novel else folders.audio_dir,)
    elif light_novel is not None and book.type is BookType.EBOOK and folders.split_books \
            and root == folders.books_sorted:
        kind_parts = (folders.light_novel_dir if light_novel else folders.ebook_dir,)
    if light_novel is not None:
        book.light_novel = bool(light_novel)
    new_dir = root.joinpath(*kind_parts) / clean_name(book.author or "Unknown Author")
    if m.series:
        new_dir /= planner.series_folder(book)
    new_dir /= clean_name(planner.book_name(book))
    if folders.is_protected(new_dir):
        raise PermissionError(f"protected folder: {new_dir}")
    with LibraryEditor(folders, "edit") as ed:
        InfoLog(folders.state, ed.run_id).write(edit=str(folder), asked=asked, read=read, before=before,
                                                after=what_was_decided(book, new_dir))
        if str(new_dir) != str(folder) and same_place(new_dir, folder):
            ed.rename(folder, new_dir)  # only the case changed ('(light Novel)' -> '(Light Novel)')
        elif new_dir != folder:
            if new_dir.exists() and any(new_dir.iterdir()):
                raise FileExistsError(f"already exists: {new_dir}")
            ed.move_tree(folder, new_dir)
            for parent in (folder.parent, folder.parent.parent):  # old author / series folders
                if parent != root and root in parent.parents:
                    remove_empty_dirs(parent)
        files = [new_dir / f.relative_to(folder) for f in book.files]
        if book.type is not BookType.AUDIO and old_title and m.title:
            # a sorted ebook's file is named after its title: follow the new title
            for i, f in enumerate(files):
                if f.stem.lower() == clean_name(old_title).lower() and f.stem != clean_name(m.title):
                    new_file = f.with_name(clean_name(m.title) + f.suffix)
                    if same_place(new_file, f):
                        ed.rename(f, new_file)
                    else:
                        ed.move_file(f, new_file)
                    files[i] = new_file
        for f in files:
            ed.keep_tags(f)  # the sorted book's own files: undo puts the old tags back
            if write_tags(f, book, whole_book=len(files) == 1):
                ed.updated(f)
        if book.type is BookType.AUDIO:
            for name, text in (("reader.txt", ", ".join(m.narrators)), ("desc.txt", m.description or "")):
                target = new_dir / name
                if text and (not target.exists() or target.read_text(encoding="utf-8", errors="replace") != text):
                    ed.park(target, target.parent)
                    data = text.encode("utf-8")
                    target.write_bytes(data)
                    ed.journal.write(op="create", dst=str(target), size=len(data))
        else:
            opf = new_dir / "metadata.opf"
            marker = cover_marker(opf)
            book.files = files
            text = opf_xml(book)
            if marker:
                text = text.replace("  </metadata>", f'    <meta name="booksorter:cover" content="{xml_escape(marker)}"/>\n'
                                                     "  </metadata>", 1)
            ed.park(opf, opf.parent)
            data = text.encode("utf-8")
            opf.write_bytes(data)
            ed.journal.write(op="create", dst=str(opf), size=len(data))
        ed.park_abs_metadata(new_dir)
        try:  # the next sort does the same by itself (undo of this edit removes the rule)
            Rules.load(folders.state).learn(old_meta, old_kind, book, run_id=ed.run_id)
        except (OSError, ValueError):
            pass
        return ed.run_id, new_dir


def _had_series(rel: Path, folders: Folders) -> bool:
    """Does the sorted path <kind?>/<Author>/<Series?>/<Book> include a series folder?"""
    parts = rel.parts
    kinds = {folders.audio_dir, folders.audio_ln_dir, *folders.kind_dirs}
    if parts and parts[0] in kinds:
        parts = parts[1:]
    return len(parts) >= 3


def _set_opf_series(opf: Path, series: str) -> bool:
    text = opf.read_text(encoding="utf-8")
    new = re.sub(r'(<meta\b[^>]*\bname="calibre:series"[^>]*\bcontent=")[^"]*(")',
                 lambda m: m.group(1) + series.replace("&", "&amp;").replace('"', "&quot;") + m.group(2), text, count=1)
    if new == text:
        return False
    opf.write_text(new, encoding="utf-8")
    return True


# -- better covers for books that are already sorted -----------------------------------------
@dataclass
class CoverUpgrade:
    folder: Path
    old: Path | None          # the cover.* there now (parked), or None
    data: bytes
    why: str                  # "1400x1990 from inside the book"
    before: str               # "400x564" / "none"


_BOOK_FILE_EXTS = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".epub", ".pdf", ".cbz", ".cbr",
                   ".azw3", ".mobi"}


def plan_cover_upgrades(folders: Folders, lookup, cancelled: Callable[[], bool] = lambda: False,
                        on_progress: Callable[[int, int, str], None] | None = None) -> list[CoverUpgrade]:
    """Sorted books with no cover or a low-res one (< 600 px): a bigger cover from inside the book,
    Audible (by ASIN, else exact title + author), or the same edition by ISBN. Read-only."""
    from .covers import extract_cover, image_size  # noqa: PLC0415
    from .models import BookType  # noqa: PLC0415
    from .online import MIN_COVER_WIDTH  # noqa: PLC0415
    from .sidecars import IMAGE_EXTS, download  # noqa: PLC0415
    roots = [r for r in (folders.audio_sorted, folders.books_sorted, folders.swedish_sorted) if r and r.is_dir()]
    dirs = [d for r in roots for d in sorted(r.rglob("*")) if d.is_dir()
            and any(p.suffix.lower() in _BOOK_FILE_EXTS for p in d.iterdir() if p.is_file())]
    found = []
    for i, d in enumerate(dirs, 1):
        if cancelled():
            break
        if on_progress:
            on_progress(i, len(dirs), d.name)
        old = next((p for p in sorted(d.iterdir()) if p.is_file() and p.stem.lower() == "cover"
                    and p.suffix.lower() in IMAGE_EXTS), None)
        size = (image_size(old.read_bytes()) if old else None) or (0, 0)
        if size[0] >= MIN_COVER_WIDTH or folders.is_protected(d):
            continue
        try:
            book = load_sorted_book(folders, d)
        except ValueError:
            book = None
        if book is None:
            continue
        options: list[tuple[bytes, str]] = []
        if inner := extract_cover(book):
            options.append((inner[0], "inside the book"))
        for url, where in _cover_urls(book, lookup):
            try:
                options.append((download(url), where))
            except OSError:
                continue
        best = None
        for data, where in options:
            s = image_size(data) or (0, 0)
            if s[0] > (best[2][0] if best else size[0]):
                best = (data, where, s)
        if best and best[2][0] >= max(size[0] * 1.3, size[0] + 100):
            found.append(CoverUpgrade(d, old, best[0], f"{best[2][0]}x{best[2][1]} from {best[1]}",
                                      f"{size[0]}x{size[1]}" if old else "none"))
    return found


def _cover_urls(book: Book, lookup) -> list[tuple[str, str]]:
    from .models import BookType  # noqa: PLC0415
    urls = []
    m = book.meta
    if book.type is BookType.AUDIO and lookup is not None:
        try:
            cands = lookup.audible.by_asin(m.asin) if m.asin else []
            if not cands and m.title and book.author:
                cands = [c for c in lookup.audible.search(m.title, book.author)
                         if fold(c.meta.title or "") == fold(m.title)
                         and fold(book.author) in {fold(a) for a in c.meta.authors}]
        except OSError:
            cands = []
        if cands and cands[0].meta.cover_url:
            urls.append((cands[0].meta.cover_url, "Audible"))
    if lookup is not None:
        urls += [(u, "same ISBN online") for u in lookup._cover_candidates(book)]
    return urls


def apply_cover_upgrades(folders: Folders, upgrades: list[CoverUpgrade]) -> tuple[str, int]:
    """Write the better covers; the old ones and ABS's metadata.json are parked. One undoable run."""
    with LibraryEditor(folders, "covers") as ed:
        for up in upgrades:
            if folders.is_protected(up.folder):
                continue
            if up.old is not None:
                ed.park(up.old, up.folder)
            new = up.folder / ("cover.png" if up.data[:4] == b"\x89PNG" else "cover.jpg")
            new.write_bytes(up.data)
            ed.journal.write(op="create", dst=str(new), size=len(up.data))
            audio = [p for p in up.folder.iterdir() if p.suffix.lower() in (".m4b", ".m4a")]
            for f in audio if len(audio) <= 5 else []:  # the cover inside the audio file too
                if m4b.embed_cover_if_bigger(f, up.data):
                    ed.updated(f)
            ed.park_abs_metadata(up.folder)
        return ed.run_id, len(upgrades)


def replace_damaged_copies(folders: Folders, plan, books: list, cancelled: Callable[[], bool] = lambda: False,
                           on_step: Callable[[str, str], None] | None = None) -> list:
    """New downloads of books the library already has: when the sorted copy is damaged (it fails the
    full play test - '56 damaged audio packets') and the new one plays clean, the old book folder goes
    to Done/Replaced (damaged)/... (undoable) so the new copy can take its place. Returns those books."""
    from . import m4b  # noqa: PLC0415
    from .models import AUDIO_EXTS, BookType  # noqa: PLC0415
    if not m4b.ffmpeg():
        return []
    replaced = []
    for book in books:
        target = plan.targets.get(book)
        if cancelled() or book.type is not BookType.AUDIO or target is None or not target.is_dir() \
                or folders.is_protected(target):
            continue
        old = sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        if not old:
            continue
        title = book.meta.title or book.main_file.stem
        if on_step:
            on_step(title, "play-testing the sorted copy (is it damaged?)")
        if not any(m4b.play_test(f, full=True, cancelled=cancelled) for f in old):
            continue  # the library's copy is fine: the new one is just another copy
        if on_step:
            on_step(title, "the sorted copy is damaged - play-testing the new copy")
        problems = [m4b.play_test(f, full=True, cancelled=cancelled) for f in book.files]
        if cancelled():
            continue
        if any(problems):
            # damaged too: only a copy with at most half the damaged packets (and nothing else wrong) wins
            if not all(not p or "damaged audio packets" in p for p in problems):
                continue
            if on_step:
                on_step(title, "both copies are damaged - counting the damaged packets")
            before = sum(m4b.damaged_packets(f, cancelled) for f in old)
            after = sum(m4b.damaged_packets(f, cancelled) for f in book.files)
            if not before or after * 2 > before:
                continue
        try:
            rel = target.relative_to(folders.audio_sorted)
        except ValueError:
            rel = Path(target.name)
        with LibraryEditor(folders, "replace-damaged") as ed:
            ed.move_tree(target, folders.done / "Replaced (damaged)" / rel)
        replaced.append(book)
    return replaced
