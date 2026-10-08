"""Online lookup step: find candidates for each book and apply the ones we're sure about.

Audiobooks -> Audible.  Manga/comics, and light novels without an author -> AniList.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from ..config import Folders
from ..providers import Candidate
from ..providers.anilist import AniList
from ..providers.audible import Audible
from ..providers.http import Cache, Http
from ..providers.match import AUTO_TITLE, norm_language, rank, title_score
from ..providers.fandom import Fandom, parse_wiki_list
from ..providers.googlebooks import GoogleBooks
from ..providers.itunes import ITunes
from ..providers.mangadex import MangaDex
from ..providers.openlibrary import OpenLibrary
from ..providers.storytel import Storytel
from .metadata import GUESSED, update_status
from .models import Book, BookType, Metadata
from .text import fold

log = logging.getLogger(__name__)
MIN_COVER_WIDTH = 600  # px; smaller covers look blurry in Audiobookshelf
_LN_SUFFIX_RE = re.compile(r"(?i)\s*\(?light novel\)?$")
_SWEDISH_RE = re.compile(r"[åäöÅÄÖ]")
LENGTH_TOLERANCE = 0.03
_VOLUME_MARK_RE = re.compile(r"(?i)\b(?:vol(?:ume)?\.?|book|part|tome)\s*\d+|\bv\d{1,3}\b|#\s*\d+")


def looks_swedish(book: Book) -> bool:
    m = book.meta
    if m.language:
        return norm_language(m.language) == "swedish"
    return bool(_SWEDISH_RE.search(" ".join([m.title or "", *m.authors, m.series or ""])))


def book_length(book: Book) -> float:
    """Total seconds of an audiobook (0 when unknown)."""
    from .m4b import duration  # noqa: PLC0415 - mutagen-only helper, avoids a cycle at import time
    return sum(duration(f) for f in book.files)


def _best_first(cands: list[Candidate]) -> list[Candidate]:
    return sorted(cands, key=lambda c: (c.confident, c.score), reverse=True)


def main_title(name: str) -> str:
    """'Hell Mode: The Hardcore Gamer...' -> 'Hell Mode'."""
    return re.split(r"\s*[:：]\s*", name)[0].strip() or name


def _close_to(name: str, names: set[str]) -> bool:
    """'eiichirooda' ~ 'eiichirouoda' (same rule as consistency.unify_names)."""
    from difflib import SequenceMatcher  # noqa: PLC0415
    from .text import name_key  # noqa: PLC0415
    keys = {name_key(n) for n in names}
    name = name_key(name)
    return name in keys or (len(name) >= 8 and any(SequenceMatcher(None, name, n).ratio() >= 0.92 for n in keys))


MIXED_NOTE = "a {odd} among this series' {main}s - the {what}?"


def _spin_offs_of_light_novels(books: list[Book]) -> None:
    """'Ascendance of a Bookworm: Fanbook' / ': Hannelore's Fifth Year...' by the same author as the
    light novel 'Ascendance of a Bookworm' are light novels too (AniList rarely lists spin-offs)."""
    franchises = {(fold(main_title(b.meta.series)), fold(b.author)) for b in books
                  if b.type is BookType.EBOOK and b.light_novel and b.meta.series and b.author}
    for b in books:
        if b.type is BookType.EBOOK and not b.light_novel and b.author:
            name = b.meta.series or b.meta.title or ""
            if (fold(main_title(name)), fold(b.author)) in franchises:
                b.light_novel = True


def _note_mixed_kinds(books: list[Book]) -> None:
    """A .cbz among a series' epubs (Hell Mode vol 6 = the manga adaptation) or an epub among its
    .cbz volumes: a note on the odd ones, so they're easy to spot. Sorting isn't blocked."""
    from .consistency import series_key  # noqa: PLC0415
    groups: dict[str, list[Book]] = {}
    for b in books:
        b.notes = [n for n in b.notes if not n.startswith("a comic among") and not n.startswith("a ebook among")]
        if b.meta.series and b.type is not BookType.AUDIO:
            groups.setdefault(series_key(b.meta.series), []).append(b)
    for vols in groups.values():
        kinds = {b.type for b in vols}
        if len(kinds) < 2:
            continue
        main = max(kinds, key=lambda k: sum(b.type is k for b in vols))
        for b in vols:
            if b.type is not main:
                odd, what = ("comic", "manga adaptation") if b.type is BookType.COMIC else ("ebook", "light novel / novel")
                b.notes.append(MIXED_NOTE.format(odd=odd, main=main.value, what=what))


class OnlineLookup:
    def __init__(self, folders: Folders, cache: Cache | None = None):
        cache = cache or Cache(folders.state / "cache.sqlite")
        self.google_key = folders.google_books_key
        self.manga_root = (folders.books_sorted / folders.manga_dir) if folders.split_books else None
        self.covers_http = Http(cache, min_interval=0.3)  # Google Books by ISBN, for bigger covers
        self.covers_enabled = folders.use_google_books or folders.use_openlibrary
        self.audible = Audible(Http(cache, min_interval=0.3), folders.audible_region)
        self.anilist = AniList(Http(cache, min_interval=0.8))  # AniList allows ~90 requests/minute
        self.storytel = (Storytel(Http(cache, min_interval=0.5), folders.storytel_locale)
                         if folders.use_storytel else None)
        # gap fillers (like Audiobookshelf's sources): description, cover, year, ISBN
        self.itunes = ITunes(Http(cache, min_interval=0.3), folders.audible_region) if folders.use_itunes else None
        self.google = (GoogleBooks(Http(cache, min_interval=0.3), folders.google_books_key)
                       if folders.use_google_books else None)
        self.openlib = OpenLibrary(Http(cache, min_interval=0.5)) if folders.use_openlibrary else None
        # manga volumes: official covers (MangaDex) and volume titles (Fandom wikis, opt-in per series)
        self.mangadex = MangaDex(Http(cache, min_interval=0.3)) if folders.use_mangadex else None
        self.fandom = Fandom(Http(cache, min_interval=0.5))
        self.wikis = parse_wiki_list(folders.fandom_wikis)
        self.fandom_auto = folders.fandom_auto

    @staticmethod
    def wants_lookup(book: Book) -> bool:
        if book.type is BookType.AUDIO:
            return True
        if book.type is BookType.COMIC:
            return bool(book.meta.series or book.meta.title)
        return bool(book.meta.title)  # light novel check (AniList) + gaps (Google Books / Open Library)

    def run(self, books: list[Book], on_progress: Callable[[int, int, Book], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> None:
        todo = [b for b in books if self.wants_lookup(b)]
        for b in books:
            if b not in todo:
                b.looked_up = True  # nothing to ask for this one
        for i, book in enumerate(todo, 1):
            if cancelled():
                break
            book.looked_up = True
            if book.type is BookType.AUDIO:
                self.lookup(book)
                self.mark_light_novel(book)
            else:
                self.lookup_anilist(book)
                if book.type is BookType.COMIC:
                    self.manga_volume(book)
                if book.type is BookType.EBOOK and self._needs_gaps_filled(book):
                    self.fill_gaps(book, [s for s in (self.google, self.openlib) if s])
                self.check_cover(book)
            if on_progress:
                on_progress(i, len(todo), book)
        self._kind_from_author(books, self.manga_authors_in_library)
        _note_mixed_kinds(books)
        _spin_offs_of_light_novels(books)

    @property
    def manga_authors_in_library(self) -> set[str]:
        """Author folders already in the sorted Manga folder (folded names)."""
        try:
            return {fold(d.name) for d in self.manga_root.iterdir() if d.is_dir()} if self.manga_root else set()
        except OSError:
            return set()

    @staticmethod
    def _kind_from_author(books: list[Book], known_manga_authors: set[str] = frozenset()) -> None:
        """A comic AniList couldn't decide (e.g. no series: "Wanted! Eiichiro Oda Before One Piece") is
        manga when the same author's other comics here are confirmed manga."""
        manga_authors = {fold(b.author) for b in books if b.author and (
            (b.type is BookType.COMIC and b.manga) or (b.type is BookType.EBOOK and b.light_novel))}
        manga_authors |= set(known_manga_authors)  # e.g. One Piece sorted in an earlier run
        for b in books:
            if b.type is BookType.COMIC and not b.kind_known and b.author and _close_to(fold(b.author), manga_authors):
                b.manga = b.kind_known = True
                update_status(b)
        # a novel credited to a manga's creator and named after that manga ('One Piece: Law's Story',
        # Oda + Sakagami) is a light novel, even when AniList doesn't know it
        manga_series = {fold(b.meta.series): {fold(a) for a in b.meta.authors}
                        for b in books if b.type is BookType.COMIC and b.manga and b.meta.series}
        for b in books:
            if b.type is BookType.EBOOK and not b.light_novel:
                name = fold(b.meta.series or b.meta.title or "")
                mine = {fold(a) for a in b.meta.authors}
                if any(name.startswith(s) and mine & creators for s, creators in manga_series.items()):
                    b.light_novel = True

    def check_cover(self, book: Book) -> None:
        """Ebooks / manga whose own cover is small (a thumbnail): look for a bigger one of the same
        edition (by ISBN) on Open Library and Google Books; note a cover that stays low-res."""
        from .covers import image_size  # noqa: PLC0415
        from .sidecars import best_local_cover, download  # noqa: PLC0415
        book.notes = [n for n in book.notes if not n.startswith(("low-res cover", "no cover"))]
        if book.sources.get("cover_url") in ("mangadex", "bigger-cover"):
            return  # an official English volume cover / already found
        try:
            local = best_local_cover(book)
        except OSError:  # the book left Incoming meanwhile
            return
        size = (image_size(local) if local else None) or (0, 0)
        if size[0] >= MIN_COVER_WIDTH:
            return
        best_url, best = None, size
        for url in self._cover_candidates(book):
            try:
                found = image_size(download(url)) or (0, 0)
            except OSError:
                continue
            if found[0] > best[0]:
                best_url, best = url, found
        if best_url and best[0] >= max(size[0] * 1.3, size[0] + 100):
            book.override(Metadata(cover_url=best_url), "bigger-cover")
        else:
            best = size
        if best[0] < MIN_COVER_WIDTH:
            book.notes.append(f"low-res cover ({best[0]}x{best[1]})" if best[0] else "no cover found")

    def _cover_candidates(self, book: Book) -> list[str]:
        isbn = re.sub(r"[^0-9Xx]", "", book.meta.isbn or "")
        urls = []
        if isbn and self.covers_enabled:
            urls.append(f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false")
            try:
                params = {"q": f"isbn:{isbn}"} | ({"key": self.google_key} if self.google_key else {})
                data = self.covers_http.get_json("https://www.googleapis.com/books/v1/volumes", params)
                for item in (data or {}).get("items") or []:
                    links = (item.get("volumeInfo") or {}).get("imageLinks") or {}
                    if thumb := links.get("thumbnail") or links.get("smallThumbnail"):
                        urls.append(thumb.replace("http://", "https://").replace("&edge=curl", "") + "&fife=w1200")
            except OSError as e:
                log.info("Google Books cover lookup failed: %s", e)
        m = book.meta
        if m.cover_url and book.sources.get("cover_url") not in ("mangadex-ja", "anilist"):
            urls.append(m.cover_url)  # e.g. Open Library / Google from the gap filler
        return urls

    def lookup(self, book: Book) -> None:
        """Audible and Storytel (Storytel first for Swedish-looking books); apply the first sure match."""
        sources = [("Audible", self._audible)] + ([("Storytel", self._storytel)] if self.storytel else [])
        if looks_swedish(book):
            sources.reverse()
        found: list[Candidate] = []
        for name, search in sources:
            try:
                ranked = search(book)
            except OSError as e:
                book.notes.append(f"{name} lookup failed: {e}")
                log.warning("%s lookup failed for %s: %s", name, book.meta.title, e)
                continue
            found += ranked
            if ranked and ranked[0].confident:
                book.candidates = _best_first(found)[:10]
                apply_candidate(book, ranked[0])
                return
        book.candidates = _best_first(found)[:10]
        self.fill_gaps(book, [s for s in (self.itunes, self.google) if s])  # no shop match: at least fill gaps
        # a shop has this exact title, but by someone else: our "author" may be the narrator
        same_title = [c for c in book.candidates if title_score(book.meta.title, c) >= AUTO_TITLE and c.meta.authors
                      and not any(_close_to(fold(a), {fold(x) for x in c.meta.authors}) for a in book.meta.authors)]
        if same_title and book.sources.get("authors") != "manual":
            c = same_title[0]
            book.doubt = f"{c.provider.capitalize()} lists this title by {c.meta.authors[0]} - check the author"
        update_status(book)

    def _audible(self, book: Book) -> list[Candidate]:
        """Try the ASIN, then a few searches from narrow to broad; stop at the first sure match."""
        m = book.meta
        who = book.author  # may really be the narrator: rips often swap the tags
        queries: list[dict] = []
        if m.title:
            queries += [dict(title=m.title, author=who), dict(title=m.title, narrator=who), dict(title=m.title)]
        if m.series and m.series_index is not None:
            # light novels: Audible calls volume 2 just "<Series>", with series number 2
            series = _LN_SUFFIX_RE.sub("", m.series)
            queries += [dict(title=series, narrator=who), dict(keywords=f"{series} Volume {m.series_index:g}")]
        found: dict[str, Candidate] = {}
        if m.asin:
            found.update((c.meta.asin or c.url, c) for c in self.audible.by_asin(m.asin))
        seen = set()
        for q in queries:
            key = tuple(sorted((k, v) for k, v in q.items() if v))
            if key in seen:
                continue
            seen.add(key)
            if any(c.confident for c in rank(book, list(found.values()), self.audible.language)):
                break
            found.update((c.meta.asin or c.url, c) for c in self.audible.search(**q))
        return rank(book, list(found.values()), self.audible.language)

    def _storytel(self, book: Book) -> list[Candidate]:
        """Storytel also sells translations under the same title, so a match is only 'sure' when the
        length agrees with our files (within 3 %)."""
        m = book.meta
        if not m.title:
            return []
        found: dict[str, Candidate] = {}
        for q in dict.fromkeys(filter(None, (f"{m.title} {book.author or ''}".strip(), m.title))):
            found.update((c.url, c) for c in self.storytel.search(q))
            if found:
                break
        ranked = rank(book, list(found.values()), language="")
        ours = book_length(book)
        for c in ranked:
            length_ok = bool(c.length and ours and abs(c.length - ours) / ours <= LENGTH_TOLERANCE)
            c.confident = c.confident and length_ok
        return _best_first(ranked)

    @staticmethod
    def _needs_gaps_filled(book: Book) -> bool:
        m = book.meta
        guessed = any(book.sources.get(f) in GUESSED for f in ("title", "authors"))
        return bool(m.title) and (guessed or not m.description or (not m.cover_url and not m.isbn))

    def fill_gaps(self, book: Book, providers: list) -> bool:
        """Secondary sources: the first sure match fills empty fields (and confirms a title/author that
        was only guessed from the file name). Returns True when something was applied."""
        m = book.meta
        if not m.title:
            return False
        for provider in providers:
            try:
                ranked = rank(book, provider.search(m.title, book.author), language="")
            except OSError as e:
                log.info("%s lookup failed for %s: %s", provider.name, m.title, e)
                continue
            book.candidates = _best_first(book.candidates + ranked)[:12]
            if ranked and ranked[0].confident:
                best = ranked[0]
                if provider is self.openlib:
                    try:
                        self.openlib.add_description(best)
                    except OSError:
                        pass
                apply_fill(book, best)
                return True
        return False

    def manga_volume(self, book: Book) -> None:
        """Per-volume extras for manga: the official cover (MangaDex) and the volume's own title
        from the series' Fandom wiki ('One Piece, Vol. 1: Romance Dawn')."""
        m = book.meta
        if not m.series or m.series_index is None:
            return
        names = list(dict.fromkeys([m.series, main_title(m.series)]))  # full name, then 'Hell Mode'
        if self.mangadex:
            try:
                url = next((u for n in names if (u := self.mangadex.volume_cover(n, m.series_index, english_only=True))), None)
                if url:
                    book.override(Metadata(cover_url=url), "mangadex")
                elif url := next((u for n in names if (u := self.mangadex.volume_cover(n, m.series_index))), None):
                    book.override(Metadata(cover_url=url), "mangadex-ja")  # only if the book has none of its own
            except (OSError, ValueError) as e:
                log.info("MangaDex failed for %s: %s", m.series, e)
        wiki = next((w for n in names if (w := self.wikis.get(fold(n)))), None)
        if wiki is None and self.fandom_auto:
            wiki = (self.fandom.guess_wiki(main_title(m.series)), "Volume {n}")
        if wiki is None:
            return
        try:
            info = self.fandom.volume(wiki[0], m.series_index, wiki[1], series=m.series)
        except (OSError, ValueError) as e:  # ValueError: a name that can't be a web address
            log.info("Fandom failed for %s: %s", m.series, e)
            return
        if info:
            base = f"{m.series}, Vol. {m.series_index:g}"
            if info.get("title") and book.sources.get("title") != "manual":
                book.override(Metadata(title=f"{base}: {info['title']}"), "fandom")
            book.absorb(Metadata(year=info.get("year"), isbn=info.get("isbn")), "fandom")
            update_status(book)

    def lookup_anilist(self, book: Book) -> None:
        """Manga: original author + '(Manga)' suffix. Light novels: author when missing."""
        series = _LN_SUFFIX_RE.sub("", book.meta.series or "")
        by_title = not series
        if by_title:  # a stand-alone manga ('Wanted! Eiichiro Oda Before One Piece') or light novel
            series = _LN_SUFFIX_RE.sub("", book.meta.title or "")
        if not series:
            return
        try:
            cand = self.anilist.series(series, novel=book.type is BookType.EBOOK, authors=book.meta.authors)
            bare = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", series).strip()
            if cand is None and bare and bare != series and book.meta.authors:
                # 'Classroom of the Elite (Year 1)': AniList calls it 'Classroom of the Elite' - same author needed
                cand = self.anilist.series(bare, novel=book.type is BookType.EBOOK, authors=book.meta.authors)
                if cand is not None and not _close_to(fold(cand.meta.authors[0]), {fold(a) for a in book.meta.authors}):
                    cand = None
        except OSError as e:
            book.notes.append(f"AniList lookup failed: {e}")
            log.warning("AniList lookup failed for %s: %s", series, e)
            return
        if cand is not None and by_title and book.type is BookType.EBOOK and book.meta.authors \
                and not any(_close_to(fold(a), {fold(x) for x in cand.meta.authors}) for a in book.meta.authors):
            cand = None  # same title, other author: a normal novel that shares a name with a light novel
        if cand is None:
            if book.type is BookType.COMIC and not book.kind_known:
                book.notes = [n for n in book.notes if not n.startswith("AniList doesn't know")]
                book.notes.append("AniList doesn't know it - tick Manga (or leave it a comic) and Save")
            update_status(book)
            return
        book.kind_known = True
        book.candidates = [cand] + [c for c in book.candidates if c.provider != cand.provider]
        apply_anilist(book, cand)

    def mark_light_novel(self, book: Book) -> None:
        """Audiobooks of light novels go to their own folder: hint in the name, or AniList NOVEL."""
        if book.light_novel:
            return
        if book.meta.series:
            book.light_novel = self.is_light_novel(book.meta.series, book.meta.authors)
        elif book.meta.title:  # a stand-alone audiobook: exact title AND the same author on AniList
            if re.search(r"(?i)\blight novel\b|\(novel\)", book.meta.title):
                book.light_novel = True
                return
            try:
                cand = self.anilist.series(_LN_SUFFIX_RE.sub("", book.meta.title), novel=True)
            except OSError:
                return
            book.light_novel = cand is not None and (not book.meta.authors or any(
                _close_to(fold(a), {fold(x) for x in cand.meta.authors}) for a in book.meta.authors))

    def is_light_novel(self, series: str, authors: list[str] | None = None) -> bool:
        """For sorting already-sorted folders: does AniList know this series as a NOVEL?"""
        if re.search(r"(?i)\blight novel\b|\(novel\)", series):
            return True
        name = re.sub(r"(?i)\s+series$", "", _LN_SUFFIX_RE.sub("", series))  # Audible's "... Series"
        try:
            return self.anilist.series(name, novel=True, prefix_ok=True, authors=authors) is not None
        except OSError:
            return False

    def search(self, book: Book, query: str) -> list[Candidate]:
        """Free-text search from the GUI, ranked against the book."""
        if book.type is not BookType.AUDIO:
            cand = self.anilist.series(query, novel=book.type is BookType.EBOOK)
            return [cand] if cand else []
        audible = rank(book, self.audible.search(keywords=query), self.audible.language)[:10]
        extra: list[Candidate] = []
        for provider in (self.storytel, self.itunes, self.google):
            if provider is not None:
                try:
                    extra += rank(book, provider.search(query), language="")[:6]
                except OSError:
                    pass
        return _best_first(audible + extra)


def apply_candidate(book: Book, cand: Candidate) -> None:
    """Use an online match (automatically when confident, or picked by the user)."""
    book.doubt = ""
    if cand.provider == "anilist":
        apply_anilist(book, cand)
        return
    had_volume = bool(_VOLUME_MARK_RE.search(book.meta.title or ""))
    book.override(cand.meta, cand.provider)
    m = book.meta
    # light novels: the shop calls every volume just "<Series>" - keep the volume in the title
    if had_volume and m.series_index is not None and m.title and not _VOLUME_MARK_RE.search(m.title) \
            and not re.search(r"\d", m.title):
        m.title = f"{m.title}, Vol. {m.series_index:g}"
    update_status(book)


def apply_fill(book: Book, cand: Candidate) -> None:
    """A gap filler's sure match: empty fields only; a guessed title/author becomes confirmed."""
    m = cand.meta
    confirm = {f: getattr(m, f) for f in ("title", "authors") if book.sources.get(f) in GUESSED and getattr(m, f)}
    if confirm:
        book.override(Metadata(**confirm), cand.provider)
    book.absorb(Metadata(year=m.year, isbn=m.isbn, publisher=m.publisher, description=m.description,
                         cover_url=m.cover_url, language=m.language), cand.provider)
    book.doubt = ""
    update_status(book)


def apply_anilist(book: Book, cand: Candidate) -> None:
    """Keep our own series name (it has to match earlier volumes); take author/description/cover."""
    m = cand.meta
    if book.type is BookType.COMIC:
        book.manga = True
        if book.sources.get("authors") != "manual":
            book.override(Metadata(authors=m.authors), cand.provider)  # beats a guessed author folder
    else:
        book.light_novel = True
        book.absorb(Metadata(authors=m.authors), cand.provider)
    book.absorb(Metadata(description=m.description, cover_url=m.cover_url), cand.provider)
    if book.sources.get("series") in GUESSED:
        book.sources["series"] = cand.provider  # AniList only answers an exact series match: confirmed
    if not cand.exact:  # only 'Hell Mode' matched: take the real, full name
        _use_full_series_name(book, m.series, cand.provider)
    update_status(book)


def _use_full_series_name(book: Book, full: str | None, provider: str) -> None:
    """Our 'Hell Mode' matched only the start of AniList's 'Hell Mode: The Hardcore Gamer Dominates
    in Another World with Garbage Balancing': use the real, full name (and a 'Series, Vol. N' title
    follows it). A name we set by hand is kept."""
    from .metadata import format_index  # noqa: PLC0415
    m = book.meta
    if not full or not m.series or book.sources.get("series") == "manual":
        return
    short, full = m.series, _LN_SUFFIX_RE.sub("", full).strip()
    if fold(full) == fold(short) or not fold(full).startswith(fold(short)):
        return
    m.series = full
    if m.series_index is not None and m.title and fold(m.title) == fold(f"{short}, Vol. {format_index(m.series_index)}"):
        m.title = f"{full}, Vol. {format_index(m.series_index)}"
    elif m.title and fold(m.title) == fold(short):
        m.title = full
