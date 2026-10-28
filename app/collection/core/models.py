"""What you own (Owned), what a series has (Volume), and the two put together (Series)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from ...core.text import fold  # the sorter's

AUDIO, EBOOK = "audio", "ebook"
KINDS = ("audiobook", "light novel", "ebook", "manga", "comic")
_NOISE_RE = re.compile(r"(?i)^(?:the|an?)\s+|\s+series$|\s*\((?:light novel|novel|manga|comic)\)$|\s+light novel$")


def series_key(name: str, manga: bool = False) -> str:
    """'The Saga of Tanya the Evil (Manga)' -> 'sagaoftanyatheevil|manga': audio and ebook volumes of one
    novel series meet, its manga stays apart."""
    prev = None
    while prev != name:
        prev, name = name, _NOISE_RE.sub("", name).strip()
    return fold(name) + ("|manga" if manga else "")


def fmt_index(i: float | None) -> str:
    return "" if i is None else (f"{i:g}")


@dataclass
class Owned:
    title: str
    authors: list[str]
    series: str | None
    index: float | None
    fmt: str            # AUDIO / EBOOK
    kind: str           # one of KINDS
    source: str         # 'ABS: Light Novels', 'Folder', 'Staging'
    path: str = ""
    asin: str = ""
    finished: bool = False
    progress: float = 0.0
    item_id: str = ""   # Audiobookshelf's id (for its cover)

    @property
    def author(self) -> str:
        return self.authors[0] if self.authors else ""


@dataclass
class Volume:
    index: float
    title: str = ""
    release: str = ""   # 'YYYY-MM-DD'
    asin: str = ""
    url: str = ""
    source: str = ""    # 'audible' / 'anilist' / 'google'
    cover: str = ""     # image URL

    @property
    def upcoming(self) -> bool:
        return bool(self.release) and self.release > dt.date.today().isoformat()


@dataclass
class Series:
    key: str
    name: str
    author: str
    kind: str
    owned: list[Owned] = field(default_factory=list)
    audible: dict[float, Volume] = field(default_factory=dict)   # what Audible sells (audio)
    others: dict[float, Volume] = field(default_factory=dict)    # AniList / Google (books)
    total_hint: int | None = None   # AniList: number of volumes
    manga_hint: int | None = None   # a light novel's manga on AniList: its volumes (0 = exists, count unknown)
    status: str = ""                # AniList: FINISHED / RELEASING
    links: dict[str, str] = field(default_factory=dict)          # source -> page
    checked: str = ""               # when looked up online
    track: list[str] = field(default_factory=list)  # formats you collect it in ([] = the ones you own)
    # volumes of this list you have in ANOTHER series of the author ('The Hobbit', LOTR #0.5) - not saved
    elsewhere: dict = field(default_factory=dict, repr=False, compare=False)

    # -- what you have ------------------------------------------------------
    def index_of(self, o: Owned) -> float | None:
        """Your copy's number - Audible's, when the ASIN is the same ('6.1' Part One is Audible's #6)."""
        if o.asin and sum(1 for x in self.owned if x.asin == o.asin) == 1:
            # ... unless the ASIN is wrong in your library: shared by several of your copies, or far from the
            # copy's own number ('Mushoku Tensei' Vol. 2 carrying Vol. 1's ASIN)
            hit = next((i for i, v in self.audible.items() if v.asin and v.asin == o.asin), None)
            if hit is not None and (o.index is None or abs(hit - o.index) < 1):
                return hit
        return o.index

    def numbers_of(self, o: Owned) -> set[float]:
        """The volumes one copy holds: its number - or all of a box set ('Hell's Rejects, Books 1-4', or the
        folder '1-4 - Hell's Rejects' -> 1, 2, 3, 4)."""
        i = self.index_of(o)
        out = {i} if i is not None else set()
        folders = re.split(r"[\\/]", (o.path or "").rstrip("\\/"))[-2:]
        m = re.search(r"(?i)\bbooks?\s+(\d+)\s*-\s*(\d+)\b", o.title or "") or next(
            (m for f in reversed(folders) if (m := re.match(r"^(\d+)-(\d+) - ", f))), None)
        if m and int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + 30:
            out |= {float(n) for n in range(int(m.group(1)), int(m.group(2)) + 1)}
        elif (n := box_size(f"{o.title or ''} {folders[-1] if folders else ''}")) and not box_size(o.series or ""):
            # 'Pangea Online: The Complete Trilogy' - but not every book of 'The Earthsea Quartet'
            start = int(i) if i is not None else 1
            out |= {float(k) for k in range(start, start + n)}
        return out

    def have(self, fmt: str) -> set[float]:
        return {i for o in self.owned if o.fmt == fmt for i in self.numbers_of(o)} | \
            {i for (i, f), _ in self.elsewhere.items() if f == fmt}

    @property
    def formats(self) -> set[str]:
        """The formats you collect this series in: your choice, else the ones you own."""
        return set(self.track) if self.track else {o.fmt for o in self.owned}

    # -- what exists ----------------------------------------------------------
    def known(self, fmt: str) -> dict[float, Volume]:
        """Volumes that exist (released or announced) for this format."""
        if fmt == AUDIO:
            return dict(self.audible)
        out = dict(self.others)
        for i, v in self.audible.items():  # an audiobook volume exists as a book too
            out.setdefault(i, v)
        owned = [i for o in self.owned for i in self.numbers_of(o)]
        hint = self._whole_hint(out, owned)
        # gaps up to the last volume - but not up to an odd one far out ('Adachi and Shimamura, Vol. 99.9')
        nums = sorted({*out, *owned})
        while len(nums) > 1 and nums[-1] > nums[-2] * 2 + 10:
            nums.pop()
        top = max([*nums, float(hint)], default=0)
        for n in range(1, int(top) + 1):  # 1..N: AniList knows the count, a gap in yours shows too
            out.setdefault(float(n), Volume(float(n), source="anilist" if n <= hint else "gap"))
        return out

    def _whole_hint(self, known, owned) -> int:
        """AniList's count as numbered volumes. It counts side volumes too ('Classroom of the Elite (Year 2)':
        15 = 12 + 4.5, 9.5, 12.5) - with side volumes in the series it can't say which number is missing,
        so it adds no rows then (anilist_unlisted tells how many it knows more)."""
        if any(i != int(i) for i in [*known, *owned]):
            return 0
        return self.total_hint or 0

    def anilist_unlisted(self, fmt: str) -> int:
        """How many volumes AniList counts that aren't in this list (e.g. a .5 volume nobody sells yet)."""
        return max(0, (self.total_hint or 0) - len(self.known(fmt)))

    def missing(self, fmt: str) -> list[Volume]:
        """Released volumes you don't have - only for a format you collect this series in."""
        if fmt not in self.formats:
            return []
        have = self.have(fmt)
        return [v for i, v in sorted(self.known(fmt).items()) if i not in have and not v.upcoming]

    def upcoming(self) -> list[Volume]:
        seen = {**self.others, **self.audible}
        return [v for _, v in sorted(seen.items()) if v.upcoming]

    @property
    def missing_count(self) -> int:
        return sum(len(self.missing(f)) for f in (AUDIO, EBOOK))

    @property
    def next_release(self) -> Volume | None:
        ups = self.upcoming()
        return min(ups, key=lambda v: v.release) if ups else None

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("elsewhere", None)  # worked out again on every load
        d["audible"] = {fmt_index(k): asdict(v) for k, v in self.audible.items()}
        d["others"] = {fmt_index(k): asdict(v) for k, v in self.others.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Series":
        s = cls(d["key"], d["name"], d["author"], d["kind"])
        s.owned = [Owned(**o) for o in d.get("owned", [])]
        s.audible = {float(k): Volume(**v) for k, v in (d.get("audible") or {}).items()}
        s.others = {float(k): Volume(**v) for k, v in (d.get("others") or {}).items()}
        s.total_hint, s.status = d.get("total_hint"), d.get("status", "")
        s.manga_hint = d.get("manga_hint")
        s.links, s.checked = d.get("links") or {}, d.get("checked", "")
        s.track = list(d.get("track") or [])
        return s


_BOX_WORDS = {"duology": 2, "dilogy": 2, "trilogy": 3, "tetralogy": 4, "quadrilogy": 4, "quartet": 4, "pentalogy": 5}


def box_size(text: str) -> int:
    """How many books a box set holds by its name: 'The Complete Trilogy' -> 3, 'Duology' -> 2 (0: none)."""
    m = re.search(r"(?i)\b(" + "|".join(_BOX_WORDS) + r")\b", text or "")
    return _BOX_WORDS[m.group(1).lower()] if m else 0


def link_other_series(series: dict, alone: list | None = None) -> None:
    """A volume of a series' list you have elsewhere - in another series of the same author or as a standalone
    book - counts as had: 'The Hobbit' (its own folder) is #0.5 of Audible's 'The Lord of the Rings'. Same ASIN,
    or exactly the same real title (not a title that is only a series name), and the same kind (manga / not)."""
    by_author: dict[str, list] = {}
    for s in series.values():
        for o in s.owned:
            by_author.setdefault(fold(s.author), []).append((s, o))
    for o in alone or []:
        by_author.setdefault(fold(o.author), []).append((None, o))
    names = {fold(re.sub(r"(?i)\s*\((?:manga|comic)\)", "", s.name)) for s in series.values()}
    for s in series.values():
        s.elsewhere = {}
        manga = s.kind == "manga" or s.key.endswith("|manga")
        others = [o for g, o in by_author.get(fold(s.author), []) if g is not s
                  and (o.kind == "manga") == manga]
        if not others:
            continue
        for i, v in {**s.others, **s.audible}.items():
            fmts = [AUDIO] if i in s.audible and i not in s.others else [AUDIO, EBOOK]
            title = fold(v.title or "")
            for o in others:
                same = (v.asin and o.asin and v.asin == o.asin) or (
                    title and title not in names and title == fold(o.title or ""))
                if same and o.fmt in fmts:
                    s.elsewhere.setdefault((i, o.fmt), o)
