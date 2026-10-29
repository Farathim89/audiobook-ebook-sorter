"""An author's works, one per series (or standalone book), from the shops' and databases' answers, with what your
Audiobookshelf has of each - per kind: audiobook, light novel, manga, ebook.

    'Reki Kawahara' -> Sword Art Online (Audible 21 audiobooks; AniList light novel), Accel World (AniList light novel
    + manga), Sword Art Online: Aincrad (manga, 2 volumes), The Isolator, ...

The parsers below take the sources' raw JSON (so they are tested without the network); sources.py fetches it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.abs_index import _skey
from ..core.text import fold, name_key
from ..readers.names import parse_volume

KINDS = ("audio", "ln", "manga", "ebook")
KIND_LABELS = {"audio": "🎧 Audiobook", "ln": "📖 Light novel", "manga": "🗯 Manga", "ebook": "📘 Ebook"}
_ROLE_OK = re.compile(r"(?i)\b(story|original creator|original work|original|creator|author)\b")  # wrote it
_ART_ONLY = re.compile(r"(?i)^\s*(art|art assistant|original character design|illustration|character design)\s*$")
_CJK = re.compile("[぀-ヿ㐀-鿿가-힯฀-๿Ѐ-ӿͰ-Ͽ؀-ۿ֐-׿ऀ-ॿ]")  # not Latin script
# one chapter sold on its own ('Goblin Slayer, Chapter 8 (manga)', '... (ch. 13-18)'): not a volume, not a work
_CHAPTER = re.compile(r"(?i)\bchapters?\s*\d|\bch\.\s*\d|\bepisode\s*\d")
# a shop's bundle ('Classroom of the Elite (Light Novel) Series, 6 Books Collection Set'): not a work of its own
_BUNDLE = re.compile(r"(?i)\b(?:box(?:ed)?[ -]set|collection set|books? (?:collection|set)|\d+[ -]books? set"
                     r"|(?:manga|novel|light novel) set)\b|\bvol(?:ume)?s?\.?\s*\d+\s*[-–]\s*\d+")  # 'Vol 17-22'
_KIND_TAG = re.compile(r"(?i)\s*[\(\[](?:manga|comic|light novel|novel|graphic novel)[\)\]][\s.]*$")
# anywhere: Google's 'Accel World, Vol. 1 (light novel)' + its subtitle is 'Accel World, Vol. 1 (light novel): Kuroyukihime's
# Return' - the tag in the middle hides the volume number
_ANY_KIND_TAG = re.compile(r"(?i)\s*[\(\[](?:manga|comic|light novel|novel|graphic novel)[\)\]]")
# 'Sword Art Online 10: Alicization Running' - a number after a series name we know, then the volume's title
_NUMBER_TITLE = re.compile(r"^(?P<s>.*?\D)(?:\s+#?|\.)(?P<n>\d{1,3}(?:\.\d+)?)\s*[:\-–—]\s+\S.*$")


@dataclass
class Volume:
    number: float | None
    title: str
    kind: str
    source: str           # 'audible' / 'anilist' / 'google' / 'openlibrary'
    date: str = ""        # release date, when the source says
    url: str = ""


@dataclass
class Work:
    name: str
    volumes: list[Volume] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)       # kind -> volumes a source counts (AniList)
    kinds: set[str] = field(default_factory=set)               # kinds the sources know it in
    links: dict[str, str] = field(default_factory=dict)        # 'Audible' / 'AniList' -> url
    mine: dict[str, set[float]] = field(default_factory=dict)  # kind -> your volume numbers (0: unnumbered)
    status: str = ""                                           # AniList: 'RELEASING' / 'FINISHED'
    other_names: list[str] = field(default_factory=list)       # 'Tensei Shitara Slime Datta Ken' (romaji) ...

    @property
    def key(self) -> str:
        return work_key(self.name)

    def numbers(self, kind: str) -> set[float]:
        """The volume numbers of this kind: the ones a source lists, and the ones between them - Open Library
        lists 'Accel World, Vol. 12, 14, 16 ... 26': volumes 1 to 26 exist. (One far-off number - a box set,
        a misread year - doesn't stretch the series: 1, 2, 3, 250 is 1-3.)"""
        listed = sorted({v.number for v in self.volumes if v.kind == kind and v.number is not None})
        # a volume you have exists, listed or not - once a source knows the series in this kind at all
        known = listed or self.counts.get(kind) or any(v.kind == kind for v in self.volumes)
        yours = {n for n in self.mine.get(kind, ()) if n} if known else set()
        if len(listed) >= 2 and listed[-1] > listed[-2] * 2 + 10:
            listed = listed[:-1]
        if not listed:  # only a count (AniList / MangaDex): volumes 1 .. count
            return {float(n) for n in range(1, self.counts.get(kind, 0) + 1)} | yours
        top = int(listed[-1])
        if self.counts.get(kind) and self.status == "FINISHED":
            # a finished series' own count (AniList) wins: a stray 'Vol. 5' in a book shop doesn't add volumes
            top = min(top, self.counts[kind])
            listed = [n for n in listed if n <= top]
        return set(listed) | {float(n) for n in range(1, top + 1)} | yours

    def loose(self, kind: str) -> list[Volume]:
        """The unnumbered volumes of this kind worth a row: next to numbered ones, a book titled just the series'
        name ('Accel World', 'The Isolator, realization of absolute solitude') is one of them again (volume 1)."""
        loose = [v for v in self.volumes if v.kind == kind and v.number is None]
        if not any(v.kind == kind and v.number is not None for v in self.volumes):
            return loose
        names = [_bare(n) for n in [self.name, *self.other_names] if n]
        first_listed = any(v.kind == kind and v.number == 1 for v in self.volumes)

        def repeats_name(title: str) -> bool:
            t = _bare(title)
            if t in names:
                return True  # 'Accel World', 'In Another World with My Smartphone:'
            # 'The Isolator, realization of absolute solitude' is volume 1 - unless volume 1 is listed: then it
            # is a book of its own ('Mushoku Tensei: Jobless Reincarnation: Recollections')
            return not first_listed and any(t.startswith(n + " ") for n in names)
        return [v for v in loose if not repeats_name(v.title)]

    def online(self, kind: str) -> int:
        """How many volumes of this kind the sources know of."""
        numbers = self.numbers(kind)
        return max(len(numbers) or len(self.loose(kind)), self.counts.get(kind, 0))

    def have(self, kind: str) -> int:
        return len(self.mine.get(kind, ()))

    def missing(self, kind: str) -> int:
        return max(0, self.online(kind) - self.have(kind))


def _bare(name: str) -> str:
    """For comparing a title with a series name: folded, without 'The' / 'A' and kind words ('The Reprise of the
    Spear Hero: Light Novel' = 'Reprise of the Spear Hero')."""
    t = " ".join(re.sub(r"[\W_]+", " ", _ANY_KIND_TAG.sub("", name or "")).casefold().split())  # words kept
    t = re.sub(r"\s*\b(?:light novel|manga|novel)$", "", t)
    return re.sub(r"^(?:the|an?) ", "", t)


def work_key(name: str) -> str:
    return _skey(_KIND_TAG.sub("", name or ""))


def same_author(want: str, names: list[str]) -> bool:
    """'Reki Kawahara' among a book's credits ('Reki Kawahara', 'abec - illustrator'); romanizations count."""
    from ..readers.names import single_name  # noqa: PLC0415
    w = name_key(want)
    for n in names or []:
        n = re.sub(r"\s+-\s+.*$", "", n)
        if w in (name_key(n), name_key(single_name(n))):  # Audible's 'zaifyr zaifyr' is 'zaifyr'
            return True
    return False


class Works:
    """The works found so far, merged by series name (Audible's 'Sword Art Online' = AniList's)."""

    def __init__(self) -> None:
        self.by_key: dict[str, Work] = {}
        self.aliases: dict[str, str] = {}  # another name's key -> the work's key ('saikyounoshokugyou...' -> ...)

    def _key(self, name: str) -> str:
        key = work_key(name)
        return self.aliases.get(key, key)

    def find(self, name: str) -> Work | None:
        return self.by_key.get(self._key(name))

    def get(self, name: str) -> Work:
        key = self._key(name)
        if key not in self.by_key:
            self.by_key[key] = Work(" ".join(_ANY_KIND_TAG.sub("", name).split()))  # no '(Light Novel)' inside
        return self.by_key[key]

    def alias(self, names: list[str], name: str) -> Work:
        """These names are one work, shown as `name` ('Saikyou no Shokugyou wa ...' = 'The strongest job is
        apparently not a hero ...'): a work found under one of them before is merged in."""
        main = self.get(name)
        key = self._key(name)
        for other in names:
            k = work_key(other)
            if not k or k == key:
                continue
            if k in self.by_key and self.by_key[k] is not main:
                _merge(main, self.by_key.pop(k))
            self.aliases[k] = key
            if other not in main.other_names:
                main.other_names.append(other)
        return main

    def sorted(self) -> list[Work]:
        return sorted(self.by_key.values(), key=lambda w: fold(w.name))


def _merge(into: Work, other: Work) -> None:
    into.volumes += other.volumes
    into.kinds |= other.kinds
    for k, n in other.counts.items():
        into.counts[k] = max(into.counts.get(k, 0), n)
    for k, v in other.links.items():
        into.links.setdefault(k, v)
    for k, nums in other.mine.items():
        into.mine.setdefault(k, set()).update(nums)
    into.status = into.status or other.status


_ENGLISH = re.compile(r"(?i)\b(the|a|an|of|is|but|in|with|my|and|to|not|for|i|i'm)\b")


def english_name(english: str | None, romaji: str | None, synonyms: list[str]) -> str:
    """AniList's English title, else an English-looking other name ('The strongest job is apparently not a hero
    or a sage...'), else the romaji one."""
    if english:
        return english
    for n in synonyms or []:
        if n.isascii() and len(_ENGLISH.findall(n)) >= 2:
            return n
    return romaji or (synonyms[0] if synonyms else "")


def parents(works: list[Work]) -> dict[int, int | None]:
    """index -> the index of the work it belongs under (or None): the longest other work whose whole name begins
    it - 'Sword Art Online: Aincrad' / 'Sword Art Online Progressive' under 'Sword Art Online', 'Sword Art Online
    Progressive Barcarolle of Froth' under 'Sword Art Online Progressive', 'The Isolator, realization of ...'
    under 'The Isolator'. Only at a word's end ('Sword Art Onlines' would not belong)."""
    names = [w.name.casefold().strip() for w in works]
    every = [[n.casefold().strip() for n in [w.name, *w.other_names] if n] for w in works]
    out: dict[int, int | None] = {}
    for i, name in enumerate(names):
        best, best_len = None, 0
        for j in range(len(works)):
            if j == i:
                continue
            for other in every[j]:
                if len(other) >= len(name) or len(other) <= best_len:
                    continue
                at = name.find(other)
                begins = at == 0 and not name[len(other)].isalnum()
                # 'The Slime Diaries: That Time I Got Reincarnated as a Slime' - a long name inside, word-bounded
                inside = at > 0 and len(other) >= 15 and not name[at - 1].isalnum() \
                    and (at + len(other) == len(name) or not name[at + len(other)].isalnum())
                if begins or inside:
                    best, best_len = j, len(other)
        out[i] = best
    return out


# -- the sources' answers ---------------------------------------------------------------------------------
def add_audible(works: Works, products: list[dict], author: str) -> None:
    """Audible products: each audiobook under its (numbered) series, or as a book of its own. Translations
    ('Sword art online (Italian Edition)') are left out."""
    for p in products:
        names = [a.get("name", "") for a in p.get("authors") or []]
        if not same_author(author, names) or (p.get("language") or "english").lower() != "english":
            continue
        listed = p.get("series") or []
        numbered = [s for s in listed if _number(s.get("sequence")) is not None]
        s = numbered[0] if numbered else (listed[0] if listed else None)
        title = (p.get("title") or "").strip()
        number = _number(s.get("sequence")) if s else None
        name = s["title"] if s and s.get("title") else title
        if not s and (vol := parse_volume(title, trailing_number=False)):
            name, number = vol  # 'Heroine? Saint? No, I'm an All-Works Maid (and Proud of It)!, Vol. 5'
        w = works.get(name)
        if number is None and any(v.kind == "audio" and v.number is None and fold(v.title) == fold(title)
                                  for v in w.volumes):
            continue  # the same unnumbered audiobook listed twice (another edition / store entry)
        w.kinds.add("audio")
        w.volumes.append(Volume(number, title, "audio", "audible",
                                (p.get("release_date") or p.get("issue_date") or "")[:10],
                                f"https://www.audible.com/pd/{p.get('asin')}"))
        if s and s.get("asin"):
            w.links.setdefault("Audible", f"https://www.audible.com/series/{s['asin']}")


def add_anilist(works: Works, staff: dict) -> None:
    """AniList's staff page: the light novels and manga this person wrote (not anime, not art-only credits)."""
    for edge in ((staff or {}).get("staffMedia") or {}).get("edges") or []:
        node, role = edge.get("node") or {}, edge.get("staffRole") or ""
        kind = {"NOVEL": "ln", "MANGA": "manga", "ONE_SHOT": "manga"}.get(node.get("format") or "")
        if kind is None or not _ROLE_OK.search(role) or _ART_ONLY.match(role) \
                or re.search(r"(?i)\b(translat|letter|edit)", role):
            continue
        t = node.get("title") or {}
        synonyms = [n for n in node.get("synonyms") or [] if n]
        name = english_name(t.get("english"), t.get("romaji"), synonyms)
        if not name:
            continue
        w = works.alias([n for n in (t.get("english"), t.get("romaji"), *synonyms) if n], name)
        w.kinds.add(kind)
        if node.get("volumes"):
            w.counts[kind] = max(w.counts.get(kind, 0), int(node["volumes"]))
        w.status = w.status or (node.get("status") or "")
        if node.get("siteUrl"):
            w.links.setdefault("AniList", node["siteUrl"])


def add_books(works: Works, books: list[tuple[str, list[str], str, str]], author: str, source: str) -> None:
    """Google Books / OpenLibrary titles (title, authors, date, url): 'Accel World, Vol. 3' is volume 3 of Accel
    World; a light novel or manga the sources already know counts there, anything else is an ebook."""
    for title, names, date, url in books:
        if not title or not same_author(author, names) or _CJK.search(title) or _CHAPTER.search(title)                 or _BUNDLE.search(title):
            continue  # 'Sword Art Online刀劍神域 (1)': another language's edition
        manga = bool(re.search(r"(?i)\((?:manga|comic)\)|\bmanga\b", title))
        # no kind tags, no publisher's line ('(The Yoru Sumino Collection Book 5)'), no dot a tag left behind
        clean = re.sub(r"\s*\([^()]*\bcollection\b[^()]*\)\s*$", "", _ANY_KIND_TAG.sub("", title), flags=re.I)
        clean = clean.rstrip(" .") if clean != title else clean
        # Google's title + subtitle 'High School DXD, Vol. 15' + 'Volume 15': the number once
        clean = re.sub(r"(?i)(\bvol(?:ume)?\.?\s*(\d+)\b.*?),\s*vol(?:ume)?\.?\s*\2\s*$", r"\1", clean)
        vol = parse_volume(clean, trailing_number=True)
        if not vol and (m := _NUMBER_TITLE.match(clean)) and works.find(m["s"]) is not None:
            vol = (m["s"].strip(" ,:"), float(m["n"]))  # never 'Fahrenheit 451: ...' - no series 'Fahrenheit'
        name, number = (vol[0], vol[1]) if vol else (" ".join(clean.split()), None)
        w = works.get(name)
        # the kind AniList knows it as: One Piece's 'One Piece, Vol. 7' is a manga without saying so
        kind = "manga" if manga or ("manga" in w.kinds and "ln" not in w.kinds) else (
            "ln" if "ln" in w.kinds else "ebook")
        if kind == "ln" and not manga and "manga" in w.kinds and number is not None                 and number > w.counts.get("ln", 999) and number <= w.counts.get("manga", 0):
            kind = "manga"  # both exist: 'Bungo Stray Dogs, Vol. 17' is past the light novels - the manga's
        if any(v.kind == kind and v.number == number and (number is not None or _bare(v.title) == _bare(title))
               for v in w.volumes):
            continue  # the same volume from another edition / source
        w.kinds.add(kind)
        w.volumes.append(Volume(number, _ANY_KIND_TAG.sub("", title).strip(), kind, source, date[:10], url))


def drop_subtitles(works: Works) -> None:
    """A book shop's lone 'The Red Storm Princess' is 'Accel World, Vol. 2: The Red Storm Princess' - a volume
    already listed under its series, not a work of its own (only for such shop-only works: nothing of yours,
    no count, no other source)."""
    subtitles = {}
    for w in works.by_key.values():
        for v in w.volumes:
            if v.number is not None and ":" in v.title:
                sub = _bare(v.title.split(":", 1)[1])
                if len(sub) >= 8:
                    subtitles.setdefault(sub, w)
    for key, w in list(works.by_key.items()):
        shop_only = not any(w.mine.values()) and not w.counts and not w.links and             all(v.number is None and v.source in ("google", "openlibrary") for v in w.volumes)
        if shop_only and subtitles.get(_bare(w.name)) not in (None, w):
            del works.by_key[key]


def add_yours(works: Works, abs_books: list, author: str, only_known: bool = False) -> None:
    """What your Audiobookshelf has by this author, per work and kind. only_known: no new rows for books the
    sources don't list."""
    for b in abs_books:
        if b.missing or not same_author(author, b.authors):
            continue
        kind = "manga" if b.comic else ("audio" if b.fmt == "audio" else
                                        ("ln" if "light novel" in b.library.lower() else "ebook"))
        named = [(n, i) for n, i in b.series] or [(b.title, None)]
        for name, idx in named:
            if only_known and works.find(name) is None:
                continue
            w = works.get(name)
            # this series' number - a box set ('Books 1-4') its range; never another series' number
            # (Mistborn #1 is also Cosmere #12)
            numbers = (b.numbers if len(named) == 1 and b.numbers else None) or \
                ({idx} if idx is not None else {0.0})
            w.mine.setdefault(kind, set()).update(numbers)


def _number(seq) -> float | None:
    try:
        return float(str(seq).split("-")[0]) if seq not in (None, "") else None
    except ValueError:
        return None
