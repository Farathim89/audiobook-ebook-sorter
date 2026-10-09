"""Check a sort run from its info log: list the books that look off (wrong style, swapped names, a
series split over two folders...). Only reads the log - nothing is changed."""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .history import list_runs
from .infolog import read_info
from .text import fold

# a shop tag: '(A Deck Building LitRPG)' or ': A Xianxia Cultivation Novel' - not 'Harem' in a real title
_GENRE_RE = re.compile(r"(?i)[(\[][^()\[\]]*\b(?:litrpg|gamelit|xianxia|wuxia|cultivation|progression fantasy|isekai)\b"
                       r"|:\s*an?\s+[^:]*\b(?:litrpg|gamelit|xianxia|wuxia|cultivation|progression fantasy)\b")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


@dataclass
class Finding:
    target: str                  # where the book went
    title: str
    problems: list[str] = field(default_factory=list)


def last_sort_run(state: Path) -> str | None:
    """The newest sort (copy/move) run that has an info log."""
    for run in list_runs(state / "history"):
        if run.mode in ("copy", "move") and not run.undone and read_info(state, run.run_id):
            return run.run_id
    return None


def check_run(state: Path, run_id: str) -> tuple[int, list[Finding]]:
    """(books in the run, findings - only the books with a problem)."""
    entries = [e for e in read_info(state, run_id) if "decided" in e]
    findings: dict[int, Finding] = {}

    def flag(i: int, e: dict, text: str) -> None:
        v = e["decided"]["values"]
        f = findings.setdefault(i, Finding(e["decided"].get("target", e.get("book", "?")), v.get("title") or "?"))
        if text not in f.problems:
            f.problems.append(text)

    series_dirs: dict[tuple[str, str], set[str]] = defaultdict(set)    # (library, series) -> series folders
    series_authors: dict[tuple[str, str], set[str]] = defaultdict(set)  # (library, series) -> author folders
    series_members: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        d, v = e["decided"], e["decided"]["values"]
        title, series, idx = v.get("title") or "", v.get("series") or "", v.get("series_index")
        authors = v.get("authors") or []
        if e.get("result") not in (None, "done", "review"):
            flag(i, e, f"not sorted: {e.get('result')} - {e.get('detail', '')[:150]}")
        if _GENRE_RE.search(title) or _GENRE_RE.search(series):
            flag(i, e, "shop genre tag in the title or series")
        if re.search(r"(?i)(?:[:\s]volume|:\s*vol\.?)\s*\d+(?:\.\d+)?\s*$", title):  # not 'Volume 4 Canto II'
            flag(i, e, "title style 'Volume N' (others use ', Vol. N')")
        if re.search(r"(?i)\s(?:series|\(light novel\))$", series):
            flag(i, e, "the series name ends in 'Series' / '(Light Novel)'")
        if any(_CJK_RE.search(a) for a in authors):
            flag(i, e, f"author in Japanese/Chinese script: {', '.join(authors)}")
        if series and _CJK_RE.search(series) and not _CJK_RE.search(title):
            flag(i, e, f"series in another script: {series}")
        read = e.get("read") or {}
        info = read.get("book info.txt") if isinstance(read.get("book info.txt"), dict) else {}
        if info.get("narrators") and {fold(n) for n in info["narrators"]} & {fold(a) for a in authors}:
            flag(i, e, "an author is the narrator in 'book info.txt' - swapped?")
        files = read.get("metadata files") if isinstance(read.get("metadata files"), dict) else {}
        if files.get("series_index") is not None and idx is not None and files["series_index"] != idx:
            flag(i, e, f"number {idx:g} - its metadata file says {files['series_index']:g}")
        detail = e.get("detail") or ""
        for note in re.findall(r"(?:the new m4b|the xHE-AAC fix|old copy kept in Done|[^;\\]*failed the play test after)"
                               r"[^;]*", detail):
            flag(i, e, note.strip()[:180])
        target = d.get("target")
        if series and target:
            t = Path(target)
            # per kind folder: a manga and its light novel are two series folders on purpose
            lib = os.path.normcase(str(t.parents[2])) if len(t.parents) > 2 else ""
            key = (lib, fold(series))
            series_dirs[key].add(os.path.normcase(str(t.parent)))
            series_authors[key].add(os.path.normcase(t.parent.parent.name))
            series_members[key].append(i)
    for key, members in series_members.items():
        if len(series_dirs[key]) > 1:
            for i in members:
                flag(i, entries[i], "this series went into more than one series folder")
        if len(series_authors[key]) > 1:
            for i in members:
                flag(i, entries[i], f"this series went under {len(series_authors[key])} author folders")
        counts = [len(entries[i]["decided"]["values"].get("authors") or []) for i in members]
        if len(members) > 1 and min(counts) == 1:
            for i, n in zip(members, counts):
                if n > 1:
                    flag(i, entries[i], "more authors than the other volumes - a second spelling or the illustrator?")
    return len(entries), [findings[i] for i in sorted(findings)]
