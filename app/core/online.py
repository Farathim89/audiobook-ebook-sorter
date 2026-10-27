"""Online lookup step: find candidates for each book and apply the ones we're sure about.

Audiobooks -> Audible.  Manga/comics, and light novels without an author -> AniList.
"""
from __future__ import annotations

import logging
import os
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
from .metadata import GUESSED, strip_tags, update_status
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


def _comics_like_their_folder(books: list[Book]) -> None:
    """Manga volumes that only their file name names:
    - 'Youjo Senki Vol. 29.cbz' in a folder 'The Saga of Tanya the Evil', next to 28 volumes with that
      (English) series name by the same author: the same series;
    - 'Solo Leveling Ragnarok 047 - Season 1 End.cbz' (no author found) next to '... 046' by Daul:
      series, number, author and kind of its neighbours."""
    from collections import Counter  # noqa: PLC0415
    from .metadata import update_status  # noqa: PLC0415
    comics = [b for b in books if b.type is BookType.COMIC]
    series_count = Counter((fold(b.author or ""), fold(b.meta.series)) for b in comics if b.meta.series)
    names = {fold(b.meta.series): b.meta.series for b in comics if b.meta.series}
    by_folder: dict[str, list[Book]] = {}
    for b in comics:
        by_folder.setdefault(os.path.normcase(str(b.main_file.parent)), []).append(b)
    for b in comics:
        if b.sources.get("series") == "manual":
            continue
        folder = fold(b.main_file.parent.name)
        if b.meta.series and fold(b.meta.series) != folder and folder in names and b.author \
                and series_count[(fold(b.author), folder)] > series_count[(fold(b.author), fold(b.meta.series))]:
            b.meta.series = names[folder]
            continue
        if b.author or b.meta.series:
            continue
        mates = [m for m in by_folder[os.path.normcase(str(b.main_file.parent))] if m is not b and m.author and m.meta.series]
        for mate in mates:
            hit = re.match(re.escape(mate.meta.series) + r"\W+0*(\d+(?:\.\d+)?)\b", b.meta.title or b.main_file.stem, re.I)
            if hit:
                b.meta.series, b.meta.series_index = mate.meta.series, float(hit.group(1))
                b.meta.authors = list(mate.meta.authors)
                b.manga, b.kind_known = mate.manga, mate.kind_known
                for f in ("series", "series_index", "authors"):
                    b.sources[f] = "sibling"
                if not b.meta.title or b.sources.get("title") in GUESSED:
                    b.meta.title = f"{mate.meta.series}, Vol. {hit.group(1).lstrip('0') or '0'}"
                    b.sources["title"] = "sibling"
                update_status(b)
                break


def _doubled_pen_names(books: list[Book]) -> None:
    """'Twoony Twoony' (a shop filled both name fields) is 'Twoony' when another book here says 'Twoony'.
    Only then: 'Asato Asato' (86) is a real name written that way."""
    from ..readers.names import single_name  # noqa: PLC0415
    singles = {fold(a) for b in books for a in b.meta.authors if " " not in a.strip()}
    for b in books:
        if b.sources.get("authors") == "manual":
            continue
        new = [s if (s := single_name(a)) != a and fold(s) in singles else a for a in b.meta.authors]
        if new != b.meta.authors:
            b.meta.authors = list(dict.fromkeys(new))
            if b.doubt and "lists this title by" in b.doubt:
                b.doubt = ""  # 'Audible lists this title by Twoony Twoony' - the same person
            from .metadata import update_status  # noqa: PLC0415
            update_status(b)


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
        from .rules import Rules  # noqa: PLC0415
        self.rules = Rules.load(folders.state)  # learned from your edits
        self.folders = folders
        self.abs_index = None  # what your Audiobookshelf has (read at the start of run)
        from .publishers import Publishers  # noqa: PLC0415
        self.publishers = Publishers.load(folders.state)
        from .ai import make_ai  # noqa: PLC0415
        self.ai = make_ai(folders)  # None when off
        if self.ai is not None:
            self.ai.rules = self.rules

    @staticmethod
    def wants_lookup(book: Book) -> bool:
        if book.type is BookType.AUDIO:
            return True
        if book.type is BookType.COMIC:
            return bool(book.meta.series or book.meta.title)
        return bool(book.meta.title)  # light novel check (AniList) + gaps (Google Books / Open Library)

    def run(self, books: list[Book], on_progress: Callable[[int, int, Book], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> None:
        from .abs_index import AbsIndex  # noqa: PLC0415
        from .known_series import apply_known_series  # noqa: PLC0415
        self.abs_index = AbsIndex.load(self.folders, fresh=True)
        # 'Dungeon Crawler Carl - Season 2 - Audio Immersion Tunnel': no shop sells it, so none is asked
        known = [b for b in books if apply_known_series(b, self.abs_index)]
        todo = [b for b in books if self.wants_lookup(b) and b not in known]
        own_series = {id(b): b.meta.series for b in books}  # what the books' own files say, before the shops
        own_titles = {id(b): b.meta.title for b in books}
        for b in books:
            if b not in todo:
                b.looked_up = True  # nothing to ask for this one
        for i, book in enumerate(todo, 1):
            if cancelled():
                break
            book.looked_up = True
            if book.type is BookType.AUDIO:
                self.lookup(book)
                self.fill_narrators(book)
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
        _drop_credited(books)
        _latin_author_names(books)
        _doubled_pen_names(books)
        _full_series_names(books)
        _keep_shared_series(books, own_series)
        _own_series_when_unnumbered(books, own_series, own_titles)
        _series_head_vote(books)
        _from_folder_sibling(books)
        _odd_one_in_folder(books)
        _roles_by_series(books)
        self.publishers.apply(books)  # 'Yen Audio' -> light novel (Tools > Publishers)
        _same_kind_per_series(books)
        _lone_extra_authors(books)  # after the kinds: an ebook becomes a light novel next to its audiobook
        _note_mixed_kinds(books)
        _spin_offs_of_light_novels(books)
        _comics_like_their_folder(books)  # last: after the shared-series vote
        for b in known:  # the votes above don't rename a known production
            apply_known_series(b, self.abs_index)
        self.rules.apply(books)  # your own fixes win over the shops
        self._series_as_in_abs(books)
        self._numbers_from_series_lists(books)
        if self.ai is not None and not cancelled():
            self.ai.run(books, cancelled=cancelled)  # only the books still unsure
            self.rules.apply(books)  # and your fixes still win over the AI's spelling
            for b in books:  # 'Yuka Tachibana, 橘 由華' - the English name is enough
                latin = [a for a in b.meta.authors if not _CJK_NAME_RE.search(a)]
                if latin and len(latin) < len(b.meta.authors) and b.sources.get("authors") != "manual":
                    b.meta.authors = latin

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

    NARRATOR_REGIONS = ("uk", "ca", "au")  # Audible stores that often list a narrator the US one lacks

    def _series_as_in_abs(self, books: list[Book]) -> None:
        """A series you already have keeps the exact name ABS uses ('...in a Dungeon?' - a folder name
        can't hold the '?'), so a new volume joins it instead of starting a second series."""
        if not self.abs_index or not self.abs_index.books:
            return
        for b in books:
            if not b.meta.series or b.sources.get("series") == "manual":
                continue
            if b.type is BookType.COMIC:
                # your manga library's spelling, else the light novel's ('Trapped in a Dating Sim: The World of
                # ...' - not the folder's '... - The World of ...') with the manga tag
                base = re.sub(r"(?i)\s*\((?:manga|comic)\)$", "", b.meta.series)
                tag = b.meta.series[len(base):]
                name = self.abs_index.series_name(b.author, base, comic=True)
                if not name and (ln := self.abs_index.series_name(b.author, base)):
                    name = ln + tag
            else:
                name = self.abs_index.series_name(b.author, b.meta.series,
                                                  swedish=norm_language(b.meta.language) == "swedish")
                if not name:
                    name = self._sibling_style(b)
            if name and name != b.meta.series:
                b.notes.append(f"series spelled as in your library: {name}")
                b.meta.series = name

    def _sibling_style(self, b: Book) -> str | None:
        """A new part of a series you have, written the way your library writes the others: 'Classroom of the
        Elite: Year 3' next to your 'Classroom of the Elite (Year 2)' -> 'Classroom of the Elite (Year 3)'."""
        m = re.match(r"^(.*?)(?::\s*|\s+-\s+|\s*\()(Year|Part|Season|Arc)\s*(\d+)\)?$", b.meta.series or "", re.I)
        if not m:
            return None
        base, word, n = m.group(1).strip(), m.group(2), m.group(3)
        mine = {fold(a) for a in b.meta.authors}
        for a in self.abs_index.books:
            if a.missing or not mine & {fold(x) for x in a.authors}:
                continue
            for name, _ in a.series:
                s = re.match(rf"^{re.escape(base)}(:\s*|\s+-\s+|\s*\()({word})\s*\d+(\)?)$", name, re.I)
                if s:
                    return f"{base}{s.group(1)}{s.group(2)} {n}{s.group(3)}"
        return None

    def _numbers_from_series_lists(self, books: list[Book]) -> None:
        """A book with no number - or no series at all - that no shop match numbered ('I am Blue, in Pain, and
        Fragile': not out as an audiobook, so Audible's search hides it): look it up in the series' own list
        on Audible. Its own series, else every series its author has in this batch or your library. Any book
        of a series with an ASIN leads to that list."""
        from ..core.abs_index import _skey  # noqa: PLC0415
        # series key -> (spelling, ASINs, folded authors)
        known: dict[str, tuple[str, list[str], set[str]]] = {}

        def add(name: str, asin: str | None, authors) -> None:
            k = _skey(name)
            if not k:
                return
            spelling, asins, who = known.setdefault(k, (name, [], set()))
            if asin and asin not in asins:
                asins.append(asin)
            who.update(fold(a) for a in authors)

        if self.abs_index is not None:  # your library first: its spelling wins
            for a in self.abs_index.books:
                if not a.missing and not a.comic:
                    for n, _ in a.series:
                        add(n, a.asin, a.authors)
        for o in books:
            if o.meta.series and o.type is not BookType.COMIC:
                add(o.meta.series, o.meta.asin, o.meta.authors)
        lists: dict[str, dict[str, float]] = {}

        def members(k: str) -> dict[str, float]:
            if k not in lists:
                lists[k] = {}
                name, asins, _ = known[k]
                for asin in asins[:3]:
                    try:
                        lists[k] = self.audible.series_numbers(asin, name)
                    except Exception:  # noqa: BLE001 - offline / Audible down: no number, as before
                        lists[k] = {}
                    if lists[k]:
                        break
            return lists[k]

        from .metadata import update_status  # noqa: PLC0415
        bare = lambda t: re.sub(r"^(the|an?)(?=.)", "", t)  # noqa: E731
        for b in books:
            m = b.meta
            guessed = m.series and b.sources.get("series") in GUESSED  # 'Barbecue and God's Blessings' in a
            # 'Campfire Cooking...' folder: the list confirms it
            if (m.series_index is not None and not guessed) or not m.title or b.type is BookType.COMIC \
                    or "manual" in (b.sources.get("series"), b.sources.get("series_index")):
                continue
            if m.series:
                keys = [_skey(m.series)] if _skey(m.series) in known else []
            else:
                mine = {fold(a) for a in m.authors}
                keys = [k for k, (_, asins, who) in known.items() if asins and mine & who][:8]
            title = bare(fold(m.title))
            for k in keys:
                n = next((v for t, v in members(k).items() if bare(t) == title), None)
                if n is None or (m.series_index is not None and n != m.series_index):
                    continue
                if not m.series:
                    m.series = known[k][0]
                b.sources["series"] = "audible series list"
                if m.series_index is None:
                    m.series_index = n
                    b.notes.append(f"#{n:g} from Audible's list of {m.series}")
                b.sources["series_index"] = "audible series list"
                update_status(b)
                break

    def fill_narrators(self, book: Book) -> None:
        """No narrator after the lookup (a new release, an incomplete shop page): who reads the other
        volumes of the series in your library, else the other Audible stores (same ASIN) and Storytel
        (same title and author). Only the narrator is taken."""
        m = book.meta
        if book.type is not BookType.AUDIO or m.narrators:
            return
        found: list[str] = []
        source = ""
        if self.abs_index and m.series and (same := self.abs_index.narrators_for(
                book.author, m.series, swedish=norm_language(m.language) == "swedish")):
            m.narrators, book.sources["narrators"] = list(same), "library"
            book.notes.append(f"narrator as the series' other volumes in your library: {', '.join(same)}")
            return
        from ..readers.sidecar import _own_folder, read_book_info  # noqa: PLC0415
        authors, narrators, _ = read_book_info(book.folder or _own_folder(book))
        if narrators and {fold(a) for a in authors} & {fold(a) for a in m.authors}:
            found, source = [n for n in narrators if fold(n) not in {fold(a) for a in m.authors}], "sidecar"
        if m.asin and not found:
            for region in self.NARRATOR_REGIONS:
                if region == self.audible.region:
                    continue
                try:
                    cands = Audible(self.audible.http, region).by_asin(m.asin)
                except OSError:
                    continue
                if cands and cands[0].meta.narrators:
                    found, source = cands[0].meta.narrators, f"audible.{region}"
                    break
        if not found and self.storytel and m.title:
            try:
                ranked = rank(book, self.storytel.search(f"{m.title} {book.author or ''}".strip()), language="")
            except OSError:
                ranked = []
            sure = next((c for c in ranked if c.confident and c.meta.narrators), None)
            if sure:
                found, source = sure.meta.narrators, "storytel"
        if found:
            m.narrators = list(found)
            book.sources["narrators"] = source
        else:
            book.notes = [n for n in book.notes if not n.startswith("narrator not listed")]
            book.notes.append("narrator not listed anywhere (Audible, Storytel) - add it with Edit if you know it")

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
            same = self.audible.by_asin(m.asin)
            ours = book_length(book)
            labelled = {fold(n) for n in m.narrators} if book.sources.get("narrators") == "label" else set()
            other_reader = bool(same and labelled and same[0].meta.narrators
                                and not labelled & {fold(n) for n in same[0].meta.narrators})
            if same and (other_reader or same[0].length and ours
                         and abs(same[0].length - ours) / ours > LENGTH_TOLERANCE):
                # Harry Potter: metadata.json has the Stephen Fry edition's ASIN (12.05 h), the files are
                # Jim Dale's (11.80 h) - another edition: not this book's ASIN, cover or narrator
                book.notes.append(f"the ASIN {m.asin} is another edition ({same[0].length / 3600:.1f} h, "
                                  f"read by {', '.join(same[0].meta.narrators[:2]) or '?'}; this book "
                                  f"{ours / 3600:.1f} h) - not used")
                m.asin = None
                book.sources.pop("asin", None)
                if book.sources.get("narrators") in ("sidecar", "tags") and same[0].meta.narrators \
                        and {fold(n) for n in m.narrators} == {fold(n) for n in same[0].meta.narrators}:
                    m.narrators = []  # that edition's reader, not this one's
                same = []
            found.update((c.meta.asin or c.url, c) for c in same)
        seen = set()
        for q in queries:
            key = tuple(sorted((k, v) for k, v in q.items() if v))
            if key in seen:
                continue
            seen.add(key)
            ours = book_length(book)
            if any(c.confident and not (c.length and ours and abs(c.length - ours) / ours > LENGTH_TOLERANCE)
                   for c in rank(book, list(found.values()), self.audible.language)):
                break
            found.update((c.meta.asin or c.url, c) for c in self.audible.search(**q))
        ranked = rank(book, list(found.values()), self.audible.language)
        ours = book_length(book)
        labelled = {fold(n) for n in m.narrators} if book.sources.get("narrators") == "label" else set()
        cast = {fold(n) for n in m.narrators} if len(m.narrators) >= 2 \
            and book.sources.get("narrators") in ("sidecar", "tags") else set()
        cast -= {fold(a) for a in m.authors}
        for c in ranked:
            if c.confident and c.length and ours and abs(c.length - ours) / ours > LENGTH_TOLERANCE:
                # same title and author, other length: another edition (Stephen Fry instead of Jim Dale)
                c.confident = False
            if c.confident and labelled and c.meta.narrators and not labelled & {fold(n) for n in c.meta.narrators}:
                # the files say 'Narrated By Jim Dale': Stephen Fry's edition is another recording
                c.confident = False
            if c.confident and cast and c.meta.narrators and not cast & {fold(n) for n in c.meta.narrators}:
                # the files name 7 readers, Audible's match only David Buck: another recording
                c.confident = False
        return _best_first(ranked)

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
            own = info.get("title") or ""
            # 'Hideyuki Kikuchi's Vampire Hunter D: Volume 1' is the series again, not the volume's own title
            if own and (fold(main_title(m.series)) in fold(own) or re.search(r"(?i)\bvol(?:ume)?\.?\s*\d", own)):
                own = ""
            if own and book.sources.get("title") != "manual":
                book.override(Metadata(title=f"{base}: {own}"), "fandom")
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
            if book.meta.series and (len(book.meta.authors) > 1 or (book.meta.authors and all(
                    _CJK_NAME_RE.search(a) for a in book.meta.authors))):  # the illustrator / a Japanese name
                name = re.sub(r"(?i)\s+series$", "", _LN_SUFFIX_RE.sub("", book.meta.series))
                if (cand := self._ln_candidate([name], book.meta.authors)) is not None:
                    _only_original_author(book, cand)
            return
        if book.meta.series:
            book.light_novel = self.is_light_novel(book.meta.series, book.meta.authors)
            if book.light_novel:
                name = re.sub(r"(?i)\s+series$", "", _LN_SUFFIX_RE.sub("", book.meta.series))
                bare = re.sub(r"(?i)^(?:the|an?)\s+", "", name)
                cand = self._ln_candidate(dict.fromkeys([name, bare, f"A {bare}", f"The {bare}"]),
                                          book.meta.authors)
                if cand is not None:
                    _only_original_author(book, cand)
            if not book.light_novel and book.meta.title:
                # Audible: series 'I Can Go Back to My World Whenever I Want', AniList: 'Peddler in Another
                # World: I Can Go Back to My World Whenever I Want!' - found by the title
                name = re.sub(r"(?i),?\s*vol(?:ume)?\.?\s*\d+.*$", "", main_title(book.meta.title)).strip()
                try:
                    cand = self.anilist.series(name, novel=True, prefix_ok=True, authors=book.meta.authors) \
                        if name else None
                except OSError:
                    cand = None
                full = cand.meta.series if cand else None
                parts = [fold(x) for x in re.split(r"\s*[:：]\s*", full or "")]
                if full and any(x.startswith(fold(book.meta.series)) for x in parts):
                    book.light_novel = True
                    book.meta.series = full
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
        # Audible drops the article ('Cave King's Road...' - AniList: 'A Cave King's Road...'): try both
        bare = re.sub(r"(?i)^(?:the|an?)\s+", "", name)
        names = dict.fromkeys([name, bare, f"A {bare}", f"The {bare}"])
        return self._ln_candidate(names, authors) is not None

    def _ln_candidate(self, names, authors: list[str] | None = None):
        """AniList's NOVEL entry for the first of these names that it knows (or None)."""
        try:
            for n in names:
                if (cand := self.anilist.series(n, novel=True, prefix_ok=True, authors=authors)) is not None:
                    return cand
        except OSError:
            pass
        return None

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
    own_subtitle = book.meta.subtitle
    book.override(cand.meta, cand.provider)
    m = book.meta
    if own_subtitle:
        # Audible's 'Legend of the Galactic Heroes, Vol. 3' carries volume 1's subtitle 'Dawn': the book's
        # own subtitle ('Endurance') stays; the shop's only fills an empty one
        m.subtitle = own_subtitle
    if m.title:
        from ..readers.names import words_to_numbers  # noqa: PLC0415
        m.title = words_to_numbers(m.title)  # 'Constellations Volume One' -> '... Volume 1'
    # light novels: the shop calls every volume just "<Series>" - keep the volume in the title
    if m.series_index is not None and m.title and not _VOLUME_MARK_RE.search(m.title) \
            and not re.search(r"\d", m.title) and (had_volume or _starts_series(m.title, m.series)):
        m.title = f"{m.title}, Vol. {m.series_index:g}"
    strip_tags(book)  # the shop's '(A Deck Building LitRPG)' / '(Light Novel)'
    update_status(book)


def _starts_series(title: str, series: str | None) -> bool:
    """'A Cave King's Road to Paradise' is the start of 'Cave King's Road to Paradise: Climbing...'."""
    from .consistency import series_key  # noqa: PLC0415
    t, s = series_key(title), series_key(series or "")
    return bool(t and s) and s != t and s.startswith(t)  # a title that IS the series stays as it is


_CJK_NAME_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _keep_shared_series(books: list[Book], own: dict[int, str | None]) -> None:
    """Every Monogatari volume says 'Monogatari' #1..#8 in its own files; Audible calls volume 1
    'Bakemonogatari'. When 2+ other volumes of the author share the book's own series name, the
    book keeps it - one series folder for them all."""
    from collections import Counter  # noqa: PLC0415
    from .consistency import series_key  # noqa: PLC0415
    shared: Counter = Counter()
    for b in books:
        if (name := own.get(id(b))) and b.author:
            shared[(fold(b.author), series_key(name))] += 1
    for b in books:
        name = own.get(id(b))
        if not name or not b.meta.series or not b.author or b.sources.get("series") == "manual":
            continue
        if series_key(b.meta.series) == series_key(name) or shared[(fold(b.author), series_key(name))] < 3:
            continue
        parts = re.split(r"\s*[:：]\s*", b.meta.series, maxsplit=1)
        if len(parts) == 2 and series_key(parts[1]) == series_key(name):
            continue  # 'Trapped in a Dating Sim: The World of Otome Games...' is the whole of 'The World of...'
        from ..readers.names import parse_volume  # noqa: PLC0415
        vol = parse_volume(b.meta.title or "")
        if vol and len(parts) == 2 and fold(vol[0]) == fold(b.meta.series):
            continue  # the title names its own sub-series: 'Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!, Vol. 1'
        b.meta.series = name


def _series_head_vote(books: list[Book]) -> None:
    """The audiobooks say 'Min-Maxing My TRPG Build in Another World', two ebooks 'Min-Maxing My TRPG
    Build in Another World: Preach the Good Word of Mr. Henderson': one series - the name most volumes use."""
    from collections import Counter  # noqa: PLC0415
    by_author: dict[str, Counter] = {}
    for b in books:
        if b.meta.series and b.author:
            by_author.setdefault(fold(b.author), Counter())[b.meta.series] += 1
    for b in books:
        if not b.meta.series or not b.author or b.sources.get("series") in ("manual", OWN_KEPT):
            continue
        names = by_author.get(fold(b.author), Counter())
        mine = b.meta.series
        head = re.split(r"\s*[:：]\s*", mine, maxsplit=1)[0]
        kin = {n: c for n, c in names.items()
               if n != mine and (fold(n) == fold(head) or re.split(r"\s*[:：]\s*", n, maxsplit=1)[0] == mine)}
        for name, count in kin.items():
            if count > names[mine]:
                b.meta.series = name


_STORE_RE = re.compile(r"(?i)book\W*walker|\byen (?:press|on|audio)\b|j-novel|\bseven seas\b|\bkodansha\b|"
                       r"\bviz media\b|\bcross infinite\b|\bairship\b|\baudible (?:studios|originals)\b")


OWN_KEPT = "own (the shop had no number)"


def _own_series_when_unnumbered(books: list[Book], own_series: dict[int, str | None],
                                own_titles: dict[int, str | None] | None = None) -> None:
    """'Adachi and Shimamura - Short Stories', Vol. 1 (the folder): Audible files 'Short Stories 2' under plain
    'Adachi and Shimamura' without a number - the number came from the folder, so the folder's own, more exact
    series stays (else it would land on the main series' Vol. 1)."""
    from .consistency import series_key  # noqa: PLC0415
    for b in books:
        own, cur = own_series.get(id(b)), b.meta.series
        src = b.sources.get("series")
        if not own or not cur or src in (*GUESSED, "manual", None) or b.sources.get("series_index") == src:
            continue
        ko, kc = series_key(own), series_key(cur)
        if ko != kc and ko.startswith(kc) and len(ko) - len(kc) >= 4 and not re.search(r"\d", ko[len(kc):]):
            b.notes.append(f"series '{own}' kept - the shop's '{cur}' has no number for it")
            b.meta.series, b.sources["series"] = re.sub(r"\s+-\s+", ": ", own, count=1), OWN_KEPT
            if own_titles and own_titles.get(id(b)):  # the shop's match was another book of that side series
                b.meta.title = own_titles[id(b)]


def _odd_one_in_folder(books: list[Book]) -> None:
    """'...v04.6 - Pinup Art Collection [BOOK☆WALKER Exclusive].epub' in a folder of Alya volumes: the store as
    its author and no series - its title starts with the folder's series, so it takes that author and series,
    and the number its file name says (v04.6 - the real 4.5 is next to it)."""
    from .consistency import series_key  # noqa: PLC0415
    from .metadata import update_status  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415
    by_folder: dict[str, list[Book]] = {}
    for b in books:
        by_folder.setdefault(os.path.normcase(str(b.main_file.parent)), []).append(b)
    for group in by_folder.values():
        if len(group) < 3:
            continue
        pairs = Counter((tuple(b.meta.authors), b.meta.series) for b in group if b.meta.series and b.meta.authors)
        if not pairs:
            continue
        (authors, series), votes = pairs.most_common(1)[0]
        if votes < len(group) * 2 / 3:
            continue
        for b in group:
            m = b.meta
            store = bool(m.authors) and all(_STORE_RE.search(a) for a in m.authors)
            if (m.series and not store) or b.sources.get("authors") == "manual" \
                    or not series_key(m.title or "").startswith(series_key(series)):
                continue
            m.authors, b.sources["authors"] = list(authors), "folder sibling"
            m.series, b.sources["series"] = series, "folder sibling"
            num = re.search(r"(?i)\bv(?:ol)?\.?\s*(\d+(?:\.\d+)?)\b", b.main_file.stem)
            if num:
                m.series_index, b.sources["series_index"] = float(num.group(1)), "filename"
            b.notes.append(f"author and series from the other books in its folder ({series})")
            update_status(b)


def _from_folder_sibling(books: list[Book]) -> None:
    """'Lily Clairet, Vol. 02.epub' has no metadata (author guessed from the folder) but lies next to
    'Lily Clairet, Vol. 02.m4b', which knows the author: the same book - take author, series, number
    and kind from it."""
    from .consistency import series_key  # noqa: PLC0415
    from .metadata import GUESSED, update_status  # noqa: PLC0415
    by_folder: dict[str, list[Book]] = {}
    for b in books:
        by_folder.setdefault(os.path.normcase(str(b.main_file.parent)), []).append(b)
    for group in by_folder.values():
        sure = [b for b in group if b.meta.authors and b.sources.get("authors") not in GUESSED]
        for b in group:
            if b in sure or b.sources.get("authors") == "manual":
                continue
            mate = next((s for s in sure if s.type is not b.type and (
                (b.meta.series and s.meta.series and series_key(b.meta.series) == series_key(s.meta.series)
                 and b.meta.series_index == s.meta.series_index)
                or fold(b.meta.title or "") == fold(s.meta.title or ""))), None)
            if mate is None:
                continue
            b.meta.authors = list(mate.meta.authors)
            b.sources["authors"] = "sibling"
            for f in ("series", "series_index"):
                if getattr(mate.meta, f) is not None and b.sources.get(f) in (*GUESSED, None):
                    setattr(b.meta, f, getattr(mate.meta, f))
                    b.sources[f] = "sibling"
            if b.sources.get("title") in GUESSED:
                b.sources["title"] = "sibling"
            if b.type is not BookType.COMIC and mate.light_novel:
                b.light_novel = True
            update_status(b)


def _roles_by_series(books: list[Book]) -> None:
    """A download whose metadata.json swaps writer and reader on every volume ('Cliff Kurt' by...):
    where Audible was sure, the volumes are right - the others follow them. A name that is the
    narrator on most volumes of the series is the narrator; one that is mostly the author, the author."""
    from collections import Counter  # noqa: PLC0415
    from .consistency import series_key  # noqa: PLC0415
    groups: dict[str, list[Book]] = {}
    for b in books:
        if b.type is BookType.AUDIO and b.meta.series:
            groups.setdefault(series_key(b.meta.series), []).append(b)
    for vols in groups.values():
        if len(vols) < 3:
            continue
        as_author = Counter(fold(a) for b in vols for a in b.meta.authors)
        as_reader = Counter(fold(n) for b in vols for n in b.meta.narrators)
        for b in vols:
            if b.sources.get("authors") == "manual":
                continue
            readers = [a for a in b.meta.authors if as_reader[fold(a)] > as_author[fold(a)]]
            writers = [n for n in b.meta.narrators if as_author[fold(n)] > as_reader[fold(n)]]
            if readers and writers:
                b.meta.authors = writers + [a for a in b.meta.authors if a not in readers and a not in writers]
                b.meta.narrators = readers + [n for n in b.meta.narrators if n not in writers and n not in readers]
                b.doubt = ""  # 'Audible lists this title by ...' - it was the swap
                update_status(b)


def _full_series_names(books: list[Book]) -> None:
    """Volume 1 knows the whole name 'I Parry Everything: What Do You Mean...', volume 2 only Audible's
    'What Do You Mean...': both get the whole name (else they end up in two series folders)."""
    full: dict[tuple[str, str], str] = {}
    for b in books:
        if b.meta.series and ":" in b.meta.series:
            tail = b.meta.series.split(":", 1)[1]
            full.setdefault((fold(b.author or ""), fold(tail)), b.meta.series)
    for b in books:
        if b.meta.series and b.sources.get("series") != "manual" \
                and (name := full.get((fold(b.author or ""), fold(b.meta.series)))):
            b.meta.series = name


def _lone_extra_authors(books: list[Book]) -> None:
    """A light novel series where the other volumes credit just 'Kisetsu Morita': the one volume that
    also lists 'Yusuke Shiba' (the illustrator) gets just the author too."""
    from collections import Counter  # noqa: PLC0415
    from .consistency import series_key  # noqa: PLC0415
    groups: dict[str, list[Book]] = {}
    for b in books:
        if b.light_novel and b.meta.series and b.meta.authors and b.sources.get("authors") != "manual":
            groups.setdefault(series_key(b.meta.series), []).append(b)
    for vols in groups.values():
        singles = Counter(fold(b.meta.authors[0]) for b in vols if len(b.meta.authors) == 1)
        if sum(singles.values()) < 2:
            continue
        lead = singles.most_common(1)[0][0]
        credited = Counter(fold(a) for b in vols for a in b.meta.authors)
        for b in vols:
            if len(b.meta.authors) == 1 and fold(b.meta.authors[0]) != lead and credited[fold(b.meta.authors[0])] == 1                     and singles[lead] >= 2:
                # the epub of volume 4 names only 'Suzuhito Yasuda' (the illustrator); every other volume
                # is by 'Fujino Omori'
                b.meta.authors = [next(a for v in vols for a in v.meta.authors if fold(a) == lead)]
                continue
            if len(b.meta.authors) > 1 and lead in {fold(a) for a in b.meta.authors}:
                b.meta.authors = [a for a in b.meta.authors if fold(a) == lead or credited[fold(a)] > 1
                                  and credited[fold(a)] >= sum(singles.values())]


def _only_original_author(book: Book, cand: Candidate) -> None:
    """A light novel is by its original author (AniList's 'Story'); Audible also credits the illustrator
    ('Kisetsu Morita, Yusuke Shiba'). Our own spelling of the author is kept."""
    if book.sources.get("authors") == "manual" or not cand.meta.authors:
        return
    if book.meta.authors and all(_CJK_NAME_RE.search(a) for a in book.meta.authors) \
            and not _CJK_NAME_RE.search(cand.meta.authors[0]):
        book.meta.authors = [cand.meta.authors[0]]  # '赤井まつり' -> 'Matsuri Akai' (a folder you can read)
        return
    if len(book.meta.authors) < 2:
        return
    from .text import name_key  # noqa: PLC0415
    original = name_key(cand.meta.authors[0])
    ours = [a for a in book.meta.authors if name_key(a) == original or _close_to(fold(a), {fold(cand.meta.authors[0])})]
    if ours:
        book.meta.authors = ours[:1]


def _latin_author_names(books: list[Book]) -> None:
    """Audible credits '猫子, Necoco' on some volumes and only '猫子' on others: the same person -
    every volume of that series gets 'Necoco' (a folder name you can read and type)."""
    from .consistency import series_key  # noqa: PLC0415
    alias: dict[tuple[str, str], str] = {}
    for b in books:
        cjk = [a for a in b.meta.authors if _CJK_NAME_RE.search(a)]
        latin = [a for a in b.meta.authors if not _CJK_NAME_RE.search(a)]
        if cjk and latin:
            for a in cjk:
                alias.setdefault((series_key(b.meta.series or ""), a), latin[0])
    # a volume with ONLY the Japanese name ('三嶋与夢') while the others say 'Yomu Mishima'
    from collections import Counter  # noqa: PLC0415
    latin_lead: dict[str, Counter] = {}
    for b in books:
        latin = [a for a in b.meta.authors if not _CJK_NAME_RE.search(a)]
        if latin and b.meta.series:
            latin_lead.setdefault(series_key(b.meta.series), Counter())[latin[0]] += 1
    for b in books:
        if b.sources.get("authors") == "manual":
            continue
        key = series_key(b.meta.series or "")
        names = [alias.get((key, a), a) for a in b.meta.authors]
        if names and all(_CJK_NAME_RE.search(a) for a in names) and latin_lead.get(key):
            names = [latin_lead[key].most_common(1)[0][0]]
        elif any(not _CJK_NAME_RE.search(a) for a in names):
            names = [a for a in names if not _CJK_NAME_RE.search(a)]
        b.meta.authors = list(dict.fromkeys(names))


def _drop_credited(books: list[Book]) -> None:
    """Audible lists the illustrator as an author ('Kafka Asagiri, Sango Harukawa'); one volume's own
    metadata says 'Sango Harukawa - illustrator': not an author of any of them."""
    credited = {fold(n) for b in books for n in b.credits}
    if not credited:
        return
    for b in books:
        kept = [a for a in b.meta.authors if fold(a) not in credited]
        if kept and kept != b.meta.authors and b.sources.get("authors") != "manual":
            b.meta.authors = kept


def _same_kind_per_series(books: list[Book]) -> None:
    """Volumes of one series are all light novels when one is (AniList may know only one spelling) -
    audiobooks and ebooks alike ('Min-Maxing' ebooks next to their light novel audiobooks)."""
    from .consistency import series_key  # noqa: PLC0415
    groups: dict[tuple[str, str], list[Book]] = {}
    for b in books:
        if b.type is not BookType.COMIC and b.meta.series:
            groups.setdefault((series_key(b.meta.series), fold(b.author or "")), []).append(b)
    for group in groups.values():
        if any(b.light_novel for b in group):
            for b in group:
                b.light_novel = True
    # the same book as audio and ebook ('I Want to Eat Your Pancreas'): one kind for both
    by_title: dict[tuple[str, str], list[Book]] = {}
    for b in books:
        if b.type is not BookType.COMIC and b.meta.title and b.author:
            by_title.setdefault((fold(b.author), fold(b.meta.title)), []).append(b)
    for group in by_title.values():
        if len(group) > 1 and any(b.light_novel for b in group):
            for b in group:
                b.light_novel = True


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
        _only_original_author(book, cand)
    book.absorb(Metadata(description=m.description, cover_url=m.cover_url), cand.provider)
    if book.sources.get("series") in GUESSED:
        book.sources["series"] = cand.provider  # AniList only answers an exact series match: confirmed
    b = book.meta
    if book.sources.get("title") in GUESSED and b.series and b.series_index is not None and b.title \
            and fold(b.title) == fold(f"{b.series}, Vol. {b.series_index:g}"):
        book.sources["title"] = cand.provider  # 'Series, Vol. 12' from a confirmed series + number
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
