"""One spelling per author and per series, so Audiobookshelf doesn't split them.

'Ursula K. LeGuin' / 'Ursula K. Le Guin', 'By the Grace of the Gods Series' /
'By the Grace of the Gods', 'MOJIKAKIYA' / 'Mojikakiya' ... Folder names that
already exist in the sorted libraries win, then the most common spelling.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .models import Book
from .text import fold, name_key

EXISTING_WEIGHT = 1000
_SERIES_NOISE_RE = re.compile(r"(?i)^(?:the|an?)\s+|\s+series$|\s*\((?:light novel|manga|comic)\)$|\s+light novel$")


def series_key(name: str) -> str:
    prev = None
    while prev != name:
        prev, name = name, _SERIES_NOISE_RE.sub("", name).strip()
    return fold(name)


def unify_names(books: list[Book], library_roots: list[Path]) -> None:
    author_votes: dict[str, Counter] = defaultdict(Counter)
    series_votes: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for root in library_roots:
        for author_dir in _subdirs(root):
            author_votes[name_key(author_dir.name)][author_dir.name] += EXISTING_WEIGHT
            for d in _subdirs(author_dir):
                name = re.sub(r"\s*\((?:Manga|Comic)\)$", "", d.name)
                series_votes[(fold(author_dir.name), series_key(name))][name] += EXISTING_WEIGHT

    for b in books:
        for a in b.meta.authors:
            author_votes[_close_key(name_key(a), author_votes)][a] += 1
    for b in books:
        b.meta.authors = [author_votes[_close_key(name_key(a), author_votes)].most_common(1)[0][0]
                          for a in b.meta.authors]
    _one_author_per_series(books)
    for b in books:
        if b.meta.series and b.author:
            series_votes[(fold(b.author), series_key(b.meta.series))][b.meta.series] += 1
    for b in books:
        if b.meta.series and b.author:
            b.meta.series = _pick(series_votes[(fold(b.author), series_key(b.meta.series))])


def _pick(votes: Counter) -> str:
    """The most used spelling; on a tie the one with its article ('A Cave King's...' over 'Cave King's...').
    A folder already in the library weighs most, so a sorted series never splits in two."""
    return max(votes.items(), key=lambda kv: (kv[1], bool(re.match(r"(?i)(?:the|an?)\s", kv[0]))))[0]


def _one_author_per_series(books: list[Book]) -> None:
    """All volumes of a series under ONE author folder. 'One Piece - Heroines' credits 'Oda, Esaka' on
    one volume and 'Esaka, Oda' on the next: the author credited on every volume goes first. Light
    novels whose volumes share no author ('Ace's Story' 1 by Sho Hinata, 2 by Tatsuya Hamazaki) follow
    the first volume's author. (Audiobook series with several authors - Star Wars - are left alone.)"""
    groups: dict[str, list[Book]] = defaultdict(list)
    for b in books:
        if b.meta.series and b.meta.authors and b.sources.get("authors") != "manual":
            groups[series_key(b.meta.series)].append(b)
    for vols in groups.values():
        if len({fold(b.author) for b in vols}) < 2:
            continue
        vols = sorted(vols, key=lambda b: (b.meta.series_index is None, b.meta.series_index or 0))
        common = set.intersection(*({fold(a) for a in b.meta.authors} for b in vols))
        if common:
            first = Counter(fold(b.author) for b in vols if fold(b.author) in common)
            pick = max(common, key=lambda k: (first[k], -[fold(a) for a in vols[0].meta.authors].index(k)))
            lead = next(a for a in vols[0].meta.authors if fold(a) == pick)
        elif all(b.light_novel for b in vols):
            lead = vols[0].author
        else:
            continue
        for b in vols:
            b.meta.authors = [lead] + [a for a in b.meta.authors if fold(a) != fold(lead)]


def _close_key(key: str, votes: dict) -> str:
    """An existing key that is nearly the same name ('eiichirouoda' ~ 'eiichirooda'), else key itself."""
    if key in votes or len(key) < 8:
        return key
    best = max(votes, key=lambda k: SequenceMatcher(None, key, k).ratio(), default=None)
    if best and SequenceMatcher(None, key, best).ratio() >= 0.92:
        return best
    return key


def _subdirs(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []
