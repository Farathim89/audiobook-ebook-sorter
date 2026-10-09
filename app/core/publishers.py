"""Publishers and the kind of books they publish: 'Yen Audio' -> light novel, 'VIZ Media' -> manga.

The sort reads the publisher from the book's metadata, its 'book info.txt' ('Publisher: Yen Audio') and a
sure shop match, and puts the book in that kind: a novel of a light-novel publisher goes to Light Novels,
a comic of a manga/light-novel publisher to Manga. Kept in <state>/publishers.json (Tools > Publishers);
the first run starts it with the publishers below. Rules from your edits still win.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Book, BookType
from .text import fold

PUBLISHER_KINDS = ("light novel", "manga", "normal")  # normal: Audiobooks / EBooks / Comics

DEFAULTS: list[tuple[str, str]] = [
    ("Yen Audio", "light novel"), ("Yen On", "light novel"), ("Yen Press", "light novel"),
    ("J-Novel Club", "light novel"), ("J-Novel Heart", "light novel"),
    ("Seven Seas Siren", "light novel"), ("Airship", "light novel"),
    ("Cross Infinite World", "light novel"), ("Tentai Books", "light novel"), ("Sol Press", "light novel"),
    ("Square Enix Books", "light novel"), ("Hanashi Media", "light novel"),
    ("Ize Press", "light novel"), ("Tapas Media", "light novel"), ("Wuxiaworld", "light novel"),
    ("Webnovel", "light novel"), ("Kodansha Light Novels", "light novel"),
    ("VIZ Media", "manga"), ("Kodansha Comics", "manga"), ("Seven Seas Entertainment", "light novel"),
]


@dataclass
class Publisher:
    name: str
    kind: str


class Publishers:
    def __init__(self, path: Path, items: list[Publisher]):
        self.path, self.items = path, items

    @classmethod
    def load(cls, state: Path) -> "Publishers":
        path = Path(state) / "publishers.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = [Publisher(str(d["name"]), str(d["kind"])) for d in data if d.get("name")]
        except (OSError, ValueError, KeyError, TypeError):
            items = [Publisher(n, k) for n, k in DEFAULTS]  # first run: the known ones
        return cls(path, items)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([{"name": p.name, "kind": p.kind} for p in self.items], ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def match(self, text: str) -> Publisher | None:
        """The longest listed name inside this publisher text ('Seven Seas Entertainment, Seven Seas Siren')."""
        t = fold(text)
        hits = [p for p in self.items if fold(p.name) and fold(p.name) in t]
        return max(hits, key=lambda p: len(fold(p.name)), default=None)

    def apply(self, books: list[Book]) -> int:
        """Put each book in its publisher's kind. Returns how many changed."""
        changed = 0
        for b in books:
            text = " ; ".join(publisher_texts(b))
            p = self.match(text) if text else None
            if p is None or p.kind not in PUBLISHER_KINDS:
                continue
            if b.type is BookType.COMIC:
                want_manga = p.kind in ("manga", "light novel")
                if b.manga != want_manga or not b.kind_known:
                    b.manga, b.kind_known = want_manga, True
                    changed += 1
                    b.notes.append(f"publisher {p.name} -> {'manga' if want_manga else 'comic'}")
                continue
            if p.kind == "manga":
                continue  # a manga publisher's novel: no say
            want_ln = p.kind == "light novel"
            if b.light_novel != want_ln:
                b.light_novel = want_ln
                changed += 1
                b.notes.append(f"publisher {p.name} -> {'light novel' if want_ln else 'not a light novel'}")
        return changed


_INFO_PUBLISHER_RE = re.compile(r"(?im)^\s*publisher\s*[:\t]\s*(.+?)\s*$")


def publisher_texts(book: Book) -> list[str]:
    out = [book.meta.publisher] if book.meta.publisher else []
    sure = next((c for c in book.candidates if c.confident and c.meta.publisher), None)
    if sure is not None:
        out.append(sure.meta.publisher)
    from ..readers.sidecar import _own_folder  # noqa: PLC0415
    folder = book.folder or _own_folder(book)
    info = folder / "book info.txt" if folder else None
    if info is not None and info.is_file():
        try:
            out += _INFO_PUBLISHER_RE.findall(info.read_text(encoding="utf-8", errors="replace"))[:2]
        except OSError:
            pass
    return out
