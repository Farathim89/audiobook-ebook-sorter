"""Rules learned from your edits: fix a book once by hand, and the next sort does the same by itself.

    author alias   'Goji Shoji' -> 'Shoji Goji'          (every book by that name)
    series alias   'Reborn as a Space Mercenary' -> ...  (every volume)
    kind           series / author -> light novel, audiobook, manga, comic
    narrators      a series' reader - only for volumes no source names one

Saved in <state>/rules.json. A rule made by Edit (a sorted book) remembers its run, so undoing that
edit takes the rule away too.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Book, BookType, Metadata
from .text import fold

_LOCK = threading.Lock()
KINDS = ("light novel", "audiobook", "ebook", "manga", "comic")


@dataclass
class Rule:
    kind: str           # 'author' | 'series' | 'kind' | 'narrators'
    match: str          # what it applies to (shown as written; compared folded)
    value: str | list   # the new name / kind / narrator list
    scope: str = ""     # kind rules: 'series' or 'author'
    made: str = ""      # '2026-10-09'
    book: str = ""      # the edited book's title (shown in Tools > Rules)
    run_id: str = ""    # Edit of a sorted book: its undoable run

    def describe(self) -> str:
        value = ", ".join(self.value) if isinstance(self.value, list) else self.value
        what = {"author": "author", "series": "series", "narrators": "narrator of series",
                "kind": f"kind of {self.scope or 'series'}"}.get(self.kind, self.kind)
        arrow = "->" if self.kind in ("author", "series") else "="
        return f"{what} '{self.match}' {arrow} '{value}'"


@dataclass
class Rules:
    path: Path
    items: list[Rule] = field(default_factory=list)

    @classmethod
    def load(cls, state: Path) -> "Rules":
        path = Path(state) / "rules.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        names = set(Rule.__dataclass_fields__)
        return cls(path, [Rule(**{k: v for k, v in d.items() if k in names}) for d in data if isinstance(d, dict)])

    def save(self) -> None:
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(r) for r in self.items], ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    # -- learning --------------------------------------------------------------
    def learn(self, before: Metadata, before_kind: str, book: Book, run_id: str = "") -> list[Rule]:
        """Compare the book before and after an edit; every name or kind that changed becomes a rule."""
        after = book.meta
        today = time.strftime("%Y-%m-%d")
        title = after.title or before.title or ""
        new: list[Rule] = []
        add = lambda **kw: new.append(Rule(made=today, book=title, run_id=run_id, **kw))  # noqa: E731
        old_a, new_a = list(before.authors), list(after.authors)
        if old_a and new_a and old_a != new_a:
            if {fold(a) for a in old_a} == {fold(a) for a in new_a} and \
                    {a for a in old_a} == {a for a in new_a}:
                pairs = []  # only the order changed ('Shiba, Morita' -> 'Morita, Shiba'): nobody is renamed
            elif len(old_a) == len(new_a):
                # a name that is still in the list somewhere is not renamed (a swap plus one fix)
                pairs = [(o, n) for o, n in zip(old_a, new_a)
                         if (fold(o) != fold(n) or o != n) and o not in new_a and n not in old_a]
            else:  # 'Mike Langwiser - translator Hayaken' -> 'Hayaken': the first name stands for the book
                pairs = [(old_a[0], new_a[0])] if old_a[0] != new_a[0] else []
            for o, n in pairs:
                add(kind="author", match=o, value=n)
        if before.series and after.series and before.series != after.series:
            add(kind="series", match=before.series, value=after.series)
        series = after.series or before.series
        kind = book_kind(book)
        if before_kind and kind != before_kind:
            if series:
                add(kind="kind", match=series, value=kind, scope="series")
            elif book.author:
                add(kind="kind", match=book.author, value=kind, scope="author")
        if not before.narrators and after.narrators and series and book.type is BookType.AUDIO:
            add(kind="narrators", match=series, value=list(after.narrators))
        for rule in new:  # a newer rule for the same thing replaces the old one
            self.items = [r for r in self.items if not (r.kind == rule.kind and r.scope == rule.scope
                                                        and fold(r.match) == fold(rule.match))]
            # 'A' -> 'B' after 'B' -> 'A': only the new one counts
            self.items = [r for r in self.items if not (r.kind == rule.kind and isinstance(r.value, str)
                                                        and fold(r.match) == fold(str(rule.value))
                                                        and fold(r.value) == fold(rule.match))]
            self.items.append(rule)
        if new:
            self.save()
        return new

    def forget_run(self, run_id: str) -> int:
        """Undo of an Edit: the rules it made go too."""
        keep = [r for r in self.items if not run_id or r.run_id != run_id]
        gone = len(self.items) - len(keep)
        if gone:
            self.items = keep
            self.save()
        return gone

    def remove(self, index: int) -> None:
        del self.items[index]
        self.save()

    # -- using -----------------------------------------------------------------
    def apply(self, books: list[Book]) -> int:
        """Use the rules on these books (never on a field you set by hand). Returns how many changed."""
        if not self.items:
            return 0
        from .consistency import series_key  # noqa: PLC0415
        from .metadata import update_status  # noqa: PLC0415
        authors = {fold(r.match): r for r in self.items if r.kind == "author"}
        series = {series_key(r.match): r for r in self.items if r.kind == "series"}
        changed = 0
        for b in books:
            m, notes = b.meta, []
            if b.sources.get("authors") != "manual" and m.authors:
                new = [str(authors[fold(a)].value) if fold(a) in authors else a for a in m.authors]
                if new != m.authors:
                    notes += [f"author '{a}' -> '{n}'" for a, n in zip(m.authors, new) if a != n]
                    m.authors = list(dict.fromkeys(new))
                    b.sources["authors"] = "rule"
            if b.sources.get("series") != "manual" and m.series and (r := series.get(series_key(m.series))) \
                    and m.series != r.value:
                notes.append(f"series '{m.series}' -> '{r.value}'")
                m.series = str(r.value)
                b.sources["series"] = "rule"
            for r in self.items:
                if r.kind == "kind" and _matches(r, b) and book_kind(b) != r.value and set_kind(b, str(r.value)):
                    notes.append(f"kind -> {r.value}")
                if r.kind == "narrators" and not m.narrators and b.type is BookType.AUDIO and m.series \
                        and series_key(m.series) == series_key(r.match):
                    m.narrators = list(r.value)
                    b.sources["narrators"] = "rule"
                    notes.append(f"narrator {', '.join(r.value)} (as the series' other volumes)")
            if notes:
                changed += 1
                b.notes = [n for n in b.notes if not n.startswith("rule from your edits")]
                b.notes.append("rule from your edits: " + "; ".join(notes))
                if b.doubt and b.sources.get("authors") == "rule":
                    b.doubt = ""  # 'Audible lists this title by ...' - you already decided
                update_status(b)
        return changed


def _matches(rule: Rule, book: Book) -> bool:
    from .consistency import series_key  # noqa: PLC0415
    if rule.scope == "author":
        return bool(book.author) and fold(book.author) == fold(rule.match)
    return bool(book.meta.series) and series_key(book.meta.series) == series_key(rule.match)


def book_kind(book: Book) -> str:
    if book.type is BookType.COMIC:
        return "manga" if book.manga else "comic"
    if book.light_novel:
        return "light novel"
    return "audiobook" if book.type is BookType.AUDIO else "ebook"


def set_kind(book: Book, kind: str) -> bool:
    """Put the book in that kind if its file type allows it (an audiobook can't become manga)."""
    if book.type is BookType.COMIC:
        if kind not in ("manga", "comic"):
            return False
        book.manga, book.kind_known = kind == "manga", True
        return True
    if kind not in ("light novel", "audiobook", "ebook"):
        return False
    book.light_novel = kind == "light novel"
    return True
