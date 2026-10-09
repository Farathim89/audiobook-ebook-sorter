"""Collect a book's metadata from every local source, best source first."""
from __future__ import annotations

import re
from pathlib import Path

from ..readers.audio import read_audiobook
from ..readers.comic import read_comic
from ..readers.ebook import read_ebook
from ..readers.names import (split_people, context_from_dirs, parse_name, parse_volume,
                             split_series_from_title)
from ..readers.sidecar import read_sidecars
from .models import Book, BookType, Metadata, Status
from .text import clean_title, dedupe, fold, same_text

GUESSED = ("filename", "folder")
_LN_HINT_RE = re.compile(r"(?i)\blight novel\b|\(novel\)")
_PART_VOLUME_RE = re.compile(r"(?i)^(?P<s>.+?)[\s:,\-]+Part\s+(?P<p>\d+)[\s:,\-]+(?:Volume|Vol\.?)\s*(?P<v>\d+(?:\.\d+)?)\s*$")
_ROLE_RE = re.compile(r"(?i)[\s(\-–,]+(?:translator|translated by|översättare|översättning|illustrator|editor|redaktör)\)?\s*$")
_KIND_SUFFIX_RE = re.compile(r"\s*\((Manga|Comic)\)\s*$", re.I)
_KIND_FOLDERS = {"audiobooks", "lightnovels", "manga", "ebooks", "comics", "books", "audiobooksorted", "bookssorted"}
_MANGA_HINT_RE =re.compile(r"(?i)\(manga\)|\bmanga\b")
# '(Light Novel)' in a title/series: a kind, not a name ('A Tale of the Secret Saint (Light Novel), Vol. 1')
_LN_TAG_RE = re.compile(r"(?i)\s*[(\[](?:the\s+)?(?:light\s+)?novels?[)\]]")  # also '(Novels)', '(The Novel)'
# shop genre tags: 'A Summoner Awakens: Origins: (A Deck Building LitRPG)', '(A LitRPG Adventure)'
_GENRE_TAG_RE = re.compile(r"(?i)\s*[(\[][^()\[\]]*\b(?:LitRPG|GameLit|Progression Fantasy|Cultivation|Isekai|"
                           r"Harem|Dungeon Core|Portal Fantasy|Base Building|Deck Building|Kingdom Building|"
                           r"System Apocalypse|Monster Evolution|Fantasy Adventure)\b[^()\[\]]*[)\]]")


def read_local(book: Book, abs_json: bool = True) -> None:
    """Fill book.meta from sidecars > embedded tags > file/folder name > parent folders."""
    book.absorb(read_sidecars(book, abs_json), "sidecar")
    _roles_from_labels(book)
    _roles_from_book_info(book)
    _narrator_from_braces(book)
    book.absorb(_embedded(book), "tags")
    guess, dirs = _name_guess(book)
    book.absorb(guess, "filename")
    book.absorb(context_from_dirs(dirs, book.type, book.meta), "folder")
    _tidy(book)
    update_status(book)


def update_status(book: Book) -> None:
    book.status = _status(book)


def edit_book(book: Book, **values) -> None:
    """Manual edits from the GUI: set exactly these fields (empty clears) and mark them 'manual'."""
    for name, value in values.items():
        if name == "manga":
            book.manga = bool(value)
            book.kind_known = True
            continue
        if value == "":
            value = None
        if getattr(book.meta, name) != value:
            setattr(book.meta, name, value)
            book.sources[name] = "manual"
    book.doubt = ""  # the user has looked at it
    update_status(book)


_RANGE_RE = re.compile(r"(?i)\b(?:books?|volumes?|vols?\.?|parts?|#)\s*(\d+(?:\.\d+)?)\s*(?:-|–|—|to|thru|through)\s*"
                       r"(\d+(?:\.\d+)?)\b")


def series_number(book: Book) -> str | None:
    """The series number as the folder and tags show it: '3', '4.5' - or '1-4' for a box set
    ("Hell's Rejects, Books 1-4" with series number 1)."""
    m = book.meta
    if m.series_index is None:
        return None
    hit = _RANGE_RE.search(m.title or "")
    if hit and float(hit.group(1)) == m.series_index and float(hit.group(2)) > m.series_index:
        return f"{format_index(m.series_index)}-{format_index(float(hit.group(2)))}"
    return format_index(m.series_index)


def format_index(index: float) -> str:
    """1.0 -> '1', 1.5 -> '1.5' (Audiobookshelf reads the number at the start of the folder)."""
    return str(int(index)) if index == int(index) else f"{index:g}"


def _embedded(book: Book) -> Metadata:
    if book.type is BookType.AUDIO:
        return read_audiobook(book.files)
    if book.type is BookType.COMIC:
        return read_comic(book.main_file)
    return read_ebook(book.files)


def _name_guess(book: Book) -> tuple[Metadata, list[str]]:
    """Parse the file name, or the book folder's name for multi-file audiobooks.

    Also returns the folders above the name that was used, for the author/series context.
    """
    stem = parse_name(book.main_file.stem, book.type)
    stem_dirs = _rel_dirs(book.main_file.parent, book.source_root)
    if book.type is not BookType.AUDIO or book.folder is None:
        return _swap_if_reversed(stem, stem_dirs), stem_dirs
    folder = parse_name(book.folder.name, book.type)
    folder_dirs = _rel_dirs(book.folder.parent, book.source_root)
    if len(book.files) > 1 or _score(folder) > _score(stem):
        return _swap_if_reversed(folder, folder_dirs), folder_dirs
    return _swap_if_reversed(stem, stem_dirs), stem_dirs


def _swap_if_reversed(meta: Metadata, dirs: list[str]) -> Metadata:
    """Calibre names files 'Title - Author': if the 'title' is a folder above, it's the author."""
    if meta.title and len(meta.authors) == 1 and any(same_text(meta.title, d) for d in dirs):
        meta.title, meta.authors = meta.authors[0], [meta.title]
    return meta


_GENERIC_NAME_RE = re.compile(r"^\W*(?:chapter|track|part|disc|cd|kapitel|audiobook|book)?\W*\d*\W*$", re.I)


def _score(meta: Metadata) -> int:
    if not meta.title or _GENERIC_NAME_RE.match(meta.title):
        return 0  # "01", "Track 03", "audiobook" say nothing
    return sum(bool(x) for x in (meta.title, meta.authors, meta.series, meta.series_index is not None))


def _rel_dirs(path: Path, root: Path) -> list[str]:
    try:
        return list(path.relative_to(root).parts)
    except ValueError:
        return []


_SMALL_WORDS = {"a", "an", "the", "and", "but", "or", "of", "in", "on", "at", "to", "for", "with", "by", "is"}


def _no_all_lowercase(value: str | None) -> str | None:
    """'your name.' (a stylised cover) -> 'Your Name.' - like its ebook, one folder for both."""
    if not value or value != value.lower() or len(re.sub(r"[^a-z]", "", value)) < 4:
        return value
    words = value.split(" ")
    return " ".join(w if i and w in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _tidy(book: Book) -> None:
    m = book.meta
    if book.type is BookType.COMIC and not book.manga:
        names = [m.title, m.series, book.main_file.stem, *book.main_file.parent.relative_to(book.source_root).parts] \
            if book.source_root in book.main_file.parents else [m.title, m.series, book.main_file.stem]
        if _MANGA_HINT_RE.search(" ".join(filter(None, names))):  # 'The Eminence in Shadow (manga)'
            book.manga = book.kind_known = True
    strip_tags(book)
    if m.title and ";" in m.title:
        variants = [v.strip() for v in m.title.split(";") if v.strip()]
        if len(variants) == 2 and fold(variants[0])[:6] == fold(variants[1])[:6]:
            m.title = max(variants, key=lambda v: (":" in v, len(v)))  # the more complete one
    m.title = _no_all_lowercase(clean_title(m.title))
    m.series = _no_all_lowercase(clean_title(m.series))
    if m.series and (kind := _KIND_SUFFIX_RE.search(m.series)):
        # 'One Piece (Manga)' from an already-sorted folder: the series is 'One Piece'
        m.series = m.series[:kind.start()].strip()
        if book.type is BookType.COMIC:
            book.manga, book.kind_known = kind.group(1).lower() == "manga", True
    if any(fold(a) in ("fullcast", "variousnarrators") for a in m.authors):
        # metadata.json: 'Full Cast' as the author of a dramatized edition - that's who reads it
        cast = [a for a in m.authors if fold(a) in ("fullcast", "variousnarrators")]
        rest = [a for a in m.authors if a not in cast]
        if rest:
            m.authors, m.narrators = rest, m.narrators or cast
        else:  # the 'narrator' is the writer: 'Full Cast' / 'Hideyuki Kikuchi' the wrong way round
            m.authors, m.narrators = list(m.narrators), cast
    if m.authors and book.sources.get("authors") in GUESSED and any(
            fold(a) in _KIND_FOLDERS or a.startswith(("-=", "="))
            or re.match(r"(?i)^(?:vol(?:ume)?\.?|book|part|tome|v)\s*\d+(?:\.\d+)?$", a) for a in m.authors):
        m.authors = []  # 'Audiobooks' / '-=Books Review=-' / 'Volume 12' is a folder, not a person
        book.sources.pop("authors", None)
    if not m.series and m.title and book.folder is not None and book.sources.get("title") not in GUESSED \
            and (hit := re.match(r"(?i)^(.+?)\s*[-–,]\s*vol(?:ume)?\.?\s*0*(\d+(?:\.\d+)?)\b", book.folder.name)) \
            and fold(hit.group(1)) and (fold(hit.group(1)).startswith(fold(m.title))
                                        or fold(m.title).startswith(fold(hit.group(1)))):
        # 'Toradora! - Vol. 10 {Courtney Shaw}' (no author folder) with the title tag 'Toradora': series #10
        m.series, m.series_index = hit.group(1).strip(), float(hit.group(2))
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
        book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "tags")
    if m.series and book.sources.get("series") in GUESSED and (vol := parse_volume(m.series)) \
            and not re.match(r"(?i)^(?:vol(?:ume)?\.?|book|part|tome|v)\s*\d", m.series):
        # the book's own folder 'Constellations, Vol. 01' taken for the series: it is 'Constellations' #1
        m.series = vol[0]
        if m.series_index is None:
            m.series_index = vol[1]
            book.sources["series_index"] = book.sources["series"]
    if m.series and book.sources.get("series") in GUESSED and (
            fold(m.series) in {fold(a) for a in m.authors} or fold(m.series) in _KIND_FOLDERS
            or m.series.startswith("-=") or m.series.startswith("=")
            or re.match(r"(?i)^(?:vol(?:ume)?\.?|book|part|tome|v)\s*\d+(?:\.\d+)?\b", m.series)):
        # a folder name taken for the series: the author's own folder, a kind folder, a staging folder
        m.series, m.series_index = None, None if book.sources.get("series_index") in GUESSED else m.series_index
        book.sources.pop("series", None)
    if m.series and m.series_index is None and (vol := parse_volume(book.main_file.stem)) \
            and _same_series(vol[0], m.series):
        m.series_index = vol[1]  # 'One Piece - Heroines Volume 01.epub' with series but no number
        book.sources["series_index"] = "filename"
    if m.title and book.type is not BookType.COMIC:
        split = split_series_from_title(m.title)
        if split and m.series and _same_series(split[1], m.series):
            m.title = split[0]  # the series is known already; just drop it from the title
            if m.series_index is not None and split[2] != m.series_index:
                # the title says 'Volume 1' but the metadata file's series says '#02': the title wins,
                # unless the file name agrees with the series number
                file_vol = parse_volume(book.main_file.stem)
                if not (file_vol and file_vol[1] == m.series_index):
                    m.series_index = split[2]
        elif split and not m.series:
            m.title, m.series, m.series_index = split
            book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
        elif not m.series and (vol := parse_volume(m.title)):
            m.series, m.series_index = vol
            book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
    if m.title and m.series and (part := _PART_VOLUME_RE.match(m.title)) \
            and fold(part.group("s")) == fold(m.series):
        # 'Ascendance of a Bookworm: Part 5 Volume 10' (sidecar number 5.10): each part is a series
        m.series = f"{part.group('s').strip()}: Part {int(part.group('p'))}"
        m.series_index = float(part.group("v"))
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if m.series and m.series_index is None and m.title and re.match(re.escape(m.series) + r"\s*[:\-]\s*\S", m.title):
        # 'Ascendance of a Bookworm: Royal Academy Stories - First Year' with series 'Ascendance of a
        # Bookworm' but no number: a stand-alone spin-off, not a volume of that series
        m.series = None
        book.sources.pop("series", None)
    if m.title and m.series and fold(m.title) != fold(m.series) and _swapped_halves(book):
        # title 'Ascendance of a Bookworm' + series 'Short Story Collection', filed under
        # '...\Ascendance of a Bookworm\Ascendance of a Bookworm - Short Story Collection Volume 02'
        m.series = f"{m.title}: {m.series}"
        m.title = m.series + (f", Vol. {format_index(m.series_index)}" if m.series_index is not None else "")
    if m.title and m.series and book.type is not BookType.COMIC and fold(m.title) != fold(m.series) \
            and fold(m.series).startswith(fold(m.title)):
        # the title is just the start of the series ('One Piece' in 'One Piece - Ace's Story'): say which book
        m.title = m.series + (f", Vol. {format_index(m.series_index)}" if m.series_index is not None else "")
    if m.title and m.series and m.series_index is not None and book.type is not BookType.COMIC \
            and (vol := parse_volume(m.title)) and same_name(vol[0], m.series) and vol[1] == m.series_index:
        # one style for 'series + number' titles: 'Hell Mode: Volume 1' -> 'Hell Mode, Vol. 1' (like the manga)
        _keep_volume_subtitle(m)
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if book.type is BookType.COMIC and m.series and m.series_index is not None:
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"
    if book.type is not BookType.COMIC and _LN_HINT_RE.search(" ".join(filter(None, (m.title, m.series)))):
        book.light_novel = True
    # a translator is no narrator either ('J. Torres - translator' as the reader of Omniscient Reader)
    m.narrators = [n for n in m.narrators if not _ROLE_RE.search(n)]
    # 'Lena Fries-Gedin - translator' is a credit, not an author (Audiobookshelf would list her as one)
    book.credits = list(dict.fromkeys([*book.credits, *(_ROLE_RE.sub("", a).strip() for a in m.authors
                                                       if _ROLE_RE.search(a))]))
    m.authors = [a for a in m.authors if not _ROLE_RE.search(a)] or m.authors
    m.authors = dedupe(m.authors)
    if book.sources.get("narrators") == "sidecar":  # a metadata file says so: authors read their own books too
        m.narrators = dedupe(m.narrators)
    else:  # in tags the author is often repeated as composer/narrator by mistake
        m.narrators = [n for n in dedupe(m.narrators) if fold(n) not in {fold(a) for a in m.authors}]


_NARRATED_BY_RE = re.compile(r"(?i)^\s*(?:narrated|read|performed)\s+by\s*:?\s+(.+?)\s*$")
_WRITTEN_BY_RE = re.compile(r"(?i)^\s*(?:written|authored)\s+by\s*:?\s+(.+?)\s*$")


def _roles_from_labels(book: Book) -> None:
    """Authors 'Narrated By Jim Dale', 'Written By J. K. Rowling' (some rips): the narrator and the
    author. A labelled narrator wins over another one from the same files (a wrong edition's reader)."""
    m = book.meta
    readers = [x.group(1) for a in m.authors if (x := _NARRATED_BY_RE.match(a))]
    writers = [x.group(1) for a in m.authors if (x := _WRITTEN_BY_RE.match(a))]
    if not readers and not writers:
        return
    m.authors = writers + [a for a in m.authors if not _NARRATED_BY_RE.match(a) and not _WRITTEN_BY_RE.match(a)]
    if readers:
        m.narrators = readers
        book.sources["narrators"] = "label"  # said in so many words: an online match must agree


def same_name(a: str, b: str) -> bool:
    """'Legend of Galactic Heroes' is 'Legend of the Galactic Heroes' (a typo in a download)."""
    strip = lambda s: fold(re.sub(r"(?i)\b(?:the|an?)\b", " ", s or ""))  # noqa: E731
    return bool(strip(a)) and strip(a) == strip(b)


def _keep_volume_subtitle(m) -> None:
    """'..., Vol. 03: Endurance' becomes '..., Vol. 3' - 'Endurance' stays as the subtitle (as Audible has it)."""
    rest = re.search(r"(?i)(?:vol(?:ume)?\.?|book|part|tome)\s*\d+(?:\.\d+)?\s*[:\-–]\s*(.+)$", m.title or "")
    if rest and not m.subtitle:
        m.subtitle = rest.group(1).strip()


def _narrator_from_braces(book: Book) -> None:
    """'Volume 01 {Courtney Shaw}': the narrator in the folder name (a common release style). Also put
    right when the metadata names the AUTHOR as narrator ('Inori' reading her own Villainess)."""
    m = book.meta
    try:
        names = [book.main_file.name, *book.main_file.parent.relative_to(book.source_root).parts]
    except ValueError:
        names = [book.main_file.name]
    found = next((x.group(1).strip() for n in names for x in [re.search(r"\{([A-Z][^{}]{2,})\}", n)] if x), None)
    authors = {fold(a) for a in m.authors}
    wrong = bool(m.narrators) and {fold(n) for n in m.narrators} <= authors
    if found and (not m.narrators or wrong):
        m.narrators = split_people(found)
        book.sources["narrators"] = "folder"
    elif wrong and book.sources.get("narrators") in ("sidecar", "tags"):
        m.narrators = []


def _roles_from_book_info(book: Book) -> None:
    """Some downloads' metadata.json have author and narrator the wrong way round ('Brittany Pressley'
    as the author of Perfect Blue); their 'book info.txt' (the shop page) says who is who."""
    from ..readers.sidecar import _own_folder, read_book_info  # noqa: PLC0415
    authors, narrators, light_novel = read_book_info(book.folder or _own_folder(book))
    if light_novel and book.type is BookType.AUDIO:
        book.light_novel = True
    m = book.meta
    keys = lambda names: {fold(n) for n in names}  # noqa: E731
    if authors and not narrators and m.authors and m.narrators and keys(m.narrators) <= keys(authors) \
            and not keys(m.authors) & keys(authors):
        # 'Paul Boehmer' as author, 'Hideyuki Kikuchi' as narrator - book info names only the writer
        m.authors, m.narrators = list(authors), list(m.authors)
        return
    if not authors or not narrators:
        return
    keys = lambda names: {fold(n) for n in names}  # noqa: E731
    junk = {fold(m.title or ""), fold(m.series or "")} - {""}
    if m.authors and keys(m.authors) <= junk:
        # metadata.json with the title as the author ('Hybrid Heart' by 'Hybrid Heart')
        m.authors = list(authors)
        book.sources["authors"] = "sidecar"
    elif m.authors and keys(m.authors) == keys(narrators) and keys(authors) != keys(narrators) \
            and (not m.narrators or keys(m.narrators) == keys(authors)):
        m.authors, m.narrators = list(authors), list(narrators)
    elif m.authors and m.narrators and keys(m.authors) == keys(narrators) \
            and keys(m.narrators) < keys(authors):
        # 'How Do You Live?': metadata.json swapped writer and reader; book info also credits the
        # translator and the foreword - the swapped 'narrator' is the writer
        m.authors, m.narrators = list(m.narrators), list(narrators)
    elif m.authors and keys(m.authors) <= keys(narrators) and not keys(m.authors) & keys(authors):
        # 'Nancy Wu' (the reader) as author, 'Sarah Roberts' (another book's reader) as narrator:
        # book info.txt (the shop page) says who is who
        m.authors, m.narrators = list(authors), list(narrators)
    elif m.narrators and keys(m.narrators) & keys(authors) and not keys(m.authors) & keys(authors):
        # 'Kiki's Delivery Service': metadata.json gives the translator as author and the writer as
        # narrator - book info.txt (the shop page) has both right
        m.authors, m.narrators = list(authors), list(narrators)
    elif not m.authors:
        m.authors = list(authors)
        book.sources["authors"] = "sidecar"


_GENRE_WORDS = (r"LitRPG|GameLit|Xianxia|Wuxia|Cultivation|Progression Fantasy|Isekai|Harem|Dungeon Core|"
                r"Portal Fantasy|Base Building|Deck Building|Kingdom Building|System Apocalypse|Monster Evolution")
_GENRE_SUBTITLE_RE = re.compile(r"(?i)\s*[:\-–]\s*an?\s+[^:,]*?\b(?:" + _GENRE_WORDS + r")\b[^:,]*?"
                                r"(?=(?:,\s*vol(?:ume)?\.?\s*\d+(?:\.\d+)?)?\s*$)")
# Seven Seas & co. sell Japanese novels with the subtitle 'Novel' / '(Novel)': a light novel
_NOVEL_SUBTITLE_RE = re.compile(r"(?i)^\(?(?:light\s+)?novel\)?$|\((?:light\s+)?novel\)|\blight novel\b")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_SUBTITLE_VOLUME_RE = re.compile(r"(?i)\b(?:volume|vol\.?|book)\s*0*(\d+(?:\.\d+)?)\s*$")


def strip_tags(book: Book) -> None:
    """Shop tags out of title/series - also after an online match: 'A Summoner Awakens: Origins:
    (A Deck Building LitRPG)' -> 'A Summoner Awakens: Origins'; audiobooks: 'A Tale of the Secret Saint
    (Light Novel) Vol. 1' -> 'A Tale of the Secret Saint, Vol. 1' (the Light Novels folder says the kind)."""
    m = book.meta
    if book.type is BookType.COMIC:
        return
    from ..readers.names import words_to_numbers  # noqa: PLC0415
    if m.title:
        m.title = _no_all_lowercase(words_to_numbers(m.title))  # also the shop's 'your name.'
    m.series = _no_all_lowercase(m.series)
    if m.series and book.sources.get("series") in GUESSED and m.title \
            and book.sources.get("title") not in GUESSED and (vol := parse_volume(m.title)) \
            and fold(vol[0]) == fold(m.series) and (m.series_index is None or vol[1] == m.series_index):
        # the folder's guess is what the (sure) title says too - 'Constellations Volume 1': not a guess
        book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
        m.series_index = vol[1]
    elif m.series and m.series_index is not None and book.sources.get("series") in GUESSED and m.title \
            and book.sources.get("title") not in GUESSED and re.match(
                re.escape(m.series) + r"\s*,?\s*(?:vol(?:ume)?\.?\s*|#)?0*" + re.escape(format_index(m.series_index))
                + r"(?!\d)", m.title, re.I):
        # 'Sword Art Online 17: Alicization Awakening' says series 'Sword Art Online' #17 too
        book.sources["series"] = book.sources["series_index"] = book.sources.get("title", "")
    changed = False
    if m.subtitle and book.type is not BookType.COMIC and _NOVEL_SUBTITLE_RE.search(m.subtitle):
        book.light_novel = True
    for field in ("title", "series"):
        value = getattr(m, field)
        if value and _GENRE_SUBTITLE_RE.search(value):
            value = _GENRE_SUBTITLE_RE.sub("", value).strip()
        if value and _LN_TAG_RE.search(value):  # audiobooks and ebooks ('I Had That Same Dream Again (Novel)')
            book.light_novel = True
            value = _LN_TAG_RE.sub("", value)
        if value and _GENRE_TAG_RE.search(value):
            value = _GENRE_TAG_RE.sub("", value).strip().rstrip(":-–,;").strip()
        if value and field == "series" and len(value) > 20 \
                and re.search(r"(?i)[\s:,-]+light novels?(?:\s+series)?\s*$", value):
            value = re.sub(r"(?i)[\s:,-]+light novels?(?:\s+series)?\s*$", "", value).strip()  # 'The NPCs in This Sim Game Must Be Real! Light Novel'
            book.light_novel = book.light_novel or book.type is not BookType.COMIC
        if value != getattr(m, field):
            setattr(m, field, value)
            changed = True
    article_from_title(book)
    if m.series and m.title and book.sources.get("series") != "manual" \
            and (parts := re.split(r"\s*[:：]\s*", m.title, maxsplit=1)) and len(parts) == 2 \
            and fold(parts[1]).startswith(fold(m.series)) and len(fold(parts[0])) >= 3 \
            and not re.search(r"(?i)\bvol(?:ume)?\.?\s*\d", parts[0]):  # not 'Vampire Hunter D, Vol. 01: Vampire...'
        # Audible: series 'What Do You Mean I'm the Strongest?...', title 'I Parry Everything: What Do You
        # Mean I'm the Strongest?..., Volume 1' - the series is the whole name
        m.series = f"{parts[0].strip()}: {m.series}"
    if m.series and re.search(r"(?i)\s+series$", m.series) and len(m.series) > 12:
        m.series = re.sub(r"(?i)\s+series$", "", m.series)  # Audible: 'By the Grace of the Gods Series'
    if m.series and _CJK_RE.search(m.series) and m.title and not _CJK_RE.search(m.title):
        # '四畳半シリーズ' on 'The Tatami Time Machine Blues': a name for another language's edition
        m.series, m.series_index = None, None
    if m.title and m.series_index is not None and m.subtitle and not re.search(r"\d", m.title) \
            and not (m.series and fold(m.title).startswith(fold(m.series)) and fold(m.title) != fold(m.series)) \
            and not (m.series and re.fullmatch(re.escape(m.series) + r",\s*(?:book|vol(?:ume)?\.?)\s*[\d.]+",
                                               m.subtitle.strip(), re.I)) \
            and (hit := _SUBTITLE_VOLUME_RE.search(m.subtitle)) and float(hit.group(1)) == m.series_index:
        # Audible: title 'Peddler in Another World', subtitle '... Whenever I Want! Volume 2' - every
        # volume has the same title, so say which one. Not 'Silvers' with the shop's series label 'Quest Academy,
        # Book 1' or 'Rascal Does Not Dream of a Sister Home Alone' (their own titles)
        m.title = f"{m.title}, Vol. {format_index(m.series_index)}"
    if m.title and m.series and m.series_index is not None and (vol := parse_volume(m.title)) \
            and vol[1] == m.series_index and fold(vol[0]).startswith(fold(m.series)) \
            and fold(vol[0]) != fold(m.series):
        # 'Even Dogs Go to Other Worlds: Life in Another World with My Beloved Hound Vol. 2': one style
        # for every volume ('..., Vol. 2'); the series name stays (it must match the volumes you have)
        m.title = f"{vol[0]}, Vol. {format_index(m.series_index)}"
    if m.title and m.series and m.series_index is not None \
            and re.fullmatch(re.escape(m.series) + r"\s+0*" + re.escape(format_index(m.series_index)), m.title, re.I):
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"  # 'Nowhere Stars 2'
    if m.title and m.series and m.series_index is not None \
            and (vol := parse_volume(m.title)) and same_name(vol[0], m.series) and vol[1] == m.series_index:
        _keep_volume_subtitle(m)
        m.title = f"{m.series}, Vol. {format_index(m.series_index)}"  # one style, like the manga


_ARTICLE_RE = re.compile(r"(?i)^(the|an?)\s+(.+)$")


def article_from_title(book: Book) -> None:
    """Audible drops the article from series names: title 'A Cave King's Road to Paradise, Vol. 1' with
    series 'Cave King's Road to Paradise: Climbing...' -> series 'A Cave King's Road to Paradise: ...'."""
    m = book.meta
    if not m.title or not m.series or _ARTICLE_RE.match(m.series):
        return
    hit = _ARTICLE_RE.match(m.title)
    start = fold(re.split(r"\s*[:,(]", hit.group(2))[0]) if hit else ""
    if start and fold(m.series).startswith(start):
        m.series = f"{hit.group(1)} {m.series}"


def _swapped_halves(book: Book) -> bool:
    """The title is the franchise and the series its sub-series: a parent folder is named like the
    title, and the file/folder names say '<title> - <series>'. ('Tehanu - The Earthsea Cycle' isn't:
    no parent folder is called 'Tehanu'.)"""
    m = book.meta
    try:
        parents = list(book.main_file.parent.relative_to(book.source_root).parts)
    except ValueError:
        parents = []
    if fold(m.title) not in {fold(p) for p in parents}:
        return False
    both = fold(m.title) + fold(m.series)
    return any(both in fold(n) for n in [book.main_file.stem, *parents])


def _same_series(a: str, b: str) -> bool:
    """'The Earthsea Cycle' ~ 'Earthsea Cycle' ~ 'Earthsea Cycle (Light Novel)'."""
    fa, fb = fold(re.sub(r"(?i)^the\s+", "", a)), fold(re.sub(r"(?i)^the\s+", "", b))
    return bool(fa and fb) and (fa in fb or fb in fa)


def _status(book: Book) -> Status:
    m = book.meta
    book.notes = [n for n in book.notes if not n.startswith(("no ", "guessed", "manga or comic")) and n != book.doubt]
    if not m.title:
        book.notes.append("no title")
        return Status.UNKNOWN
    status = Status.READY
    if not m.authors:
        book.notes.append("no author - goes to Review")
        status = Status.CHECK
    guessed = [f for f in ("title", "authors", "series") if book.sources.get(f) in GUESSED]
    if guessed:
        book.notes.append("guessed from names: " + ", ".join(guessed))
        status = Status.CHECK
    if book.doubt:
        book.notes.append(book.doubt)
        status = Status.CHECK
    if book.type is BookType.COMIC and not book.kind_known:
        book.notes.append("manga or comic? - Look up online (AniList) decides")
        status = Status.CHECK
    return status
