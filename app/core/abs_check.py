"""Check Audiobookshelf (read only): everything in your libraries that looks off, grouped -
missing or empty books, the same book twice, a volume number twice, a series or author spelled two ways,
translators listed as authors, Japanese-script authors, shop tags in names."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Callable

from ..providers.abs import AbsClient
from .abs_index import split_series
from .text import fold

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_ROLE = re.compile(r"(?i)\s*-\s*(translator|translated by|illustrator|introduction|editor)\s*$")
_TAG = re.compile(r"(?i)\((?:light )?novels?\)|\(the novel\)|\blight novel( series)?$|\s+series$")

CHECKS = [
    "Missing (files gone) - in ABS: Issues > Remove all missing items",
    "Empty (no audio, no ebook)",
    "No author",
    "Translator / illustrator listed as author",
    "Author in Japanese / Chinese script",
    "Shop tag in series or title ('(Light Novel)', 'Series')",
    "The same book twice",
    "The same volume number twice in a series",
    "Series spelled two ways",
    "Author spelled two ways",
    "The same ASIN on different books (a wrong ASIN - Audible / the sorter see one book)",
]


def _skey(name: str) -> str:
    return fold(re.sub(r"(?i)^(the|an?)\s+|\s*\((?:light novel|novel|manga|comic)\)$|\s+(light novel|series)$", "",
                       name or ""))


def check_abs(client: AbsClient, on_progress: Callable[[str], None] = lambda _: None) -> tuple[int, dict[str, list[str]]]:
    """(books, {check: [lines]}) - only the checks that found something."""
    out: dict[str, list[str]] = defaultdict(list)
    items = []
    for lib in client.libraries():
        if lib.get("mediaType") != "book":
            continue
        on_progress(f"Reading {lib.get('name')}")
        for it in client.library_items(lib["id"]):
            media = it.get("media") or {}
            md = media.get("metadata") or {}
            fmt = "audio" if media.get("numAudioFiles") else ("ebook" if media.get("ebookFormat") or media.get("ebookFile")
                                                             else "empty")
            ser = (split_series(md.get("seriesName") or "") or [""])[0]
            name = re.sub(r"\s*#\s*[\d.]+$", "", ser).strip()
            num = (re.search(r"#\s*([\d.]+)$", ser) or [None, None])[1]
            authors = [a.strip() for a in (md.get("authorName") or "").split(",") if a.strip()]
            r = dict(lib=lib["name"], title=md.get("title") or "", authors=authors, series=name, num=num, fmt=fmt,
                     path=it.get("path") or "", missing=bool(it.get("isMissing")),
                     asin=(md.get("asin") or "").upper())
            items.append(r)
            line = f"[{r['lib']}] {', '.join(authors) or '(no author)'} | " + (f"{name} #{num}" if num else name or r["title"])
            if r["missing"]:
                out[CHECKS[0]].append(line)
                continue
            if it.get("isInvalid") or fmt == "empty":
                out[CHECKS[1]].append(f"{line}   {r['path']}")
            if not authors:
                out[CHECKS[2]].append(line)
            if any(_ROLE.search(a) for a in authors):
                out[CHECKS[3]].append(line)
            if any(_CJK.search(a) for a in authors):
                out[CHECKS[4]].append(line)
            if _TAG.search(name) or _TAG.search(r["title"]):
                out[CHECKS[5]].append(line)
    live = [r for r in items if not r["missing"]]
    same = defaultdict(list)
    for r in live:  # a dramatized edition next to the reading is two books on purpose
        same[(r["lib"], r["fmt"], fold(r["authors"][0] if r["authors"] else ""), fold(r["title"]),
              "dramatized" in r["path"].lower(), r["num"] or "")].append(r)
    for rs in same.values():
        if len(rs) > 1:
            out[CHECKS[6]].append(f"[{rs[0]['lib']}] {rs[0]['title']} ({rs[0]['fmt']}) x{len(rs)}: "
                                  + "  |  ".join(r["path"] for r in rs))
    nums = defaultdict(list)
    for r in live:
        if r["series"] and r["num"]:
            nums[(r["lib"], r["fmt"], _skey(r["series"]), float(r["num"]), "dramatized" in r["path"].lower())].append(r)
    for (lib, fmt, _, n, _), rs in nums.items():
        if len(rs) > 1:
            out[CHECKS[7]].append(f"[{lib}] {rs[0]['series']} #{n:g} ({fmt}): " + "  |  ".join(r["title"] for r in rs))
    spell = defaultdict(Counter)
    for r in live:
        if r["series"]:
            manga = "(manga)" in r["series"].lower() or r["lib"].lower() == "manga"
            spell[(_skey(r["series"]), manga)][r["series"]] += 1
    for names in spell.values():
        if len(names) > 1:
            out[CHECKS[8]].append(" / ".join(f"'{n}' ({c})" for n, c in names.items()))
    auth = defaultdict(Counter)
    for r in live:
        for a in r["authors"]:
            if not _ROLE.search(a) and fold(a):
                auth[fold(a)][a] += 1
    for names in auth.values():
        if len(names) > 1:
            out[CHECKS[9]].append(" / ".join(f"'{n}' ({c})" for n, c in names.items()))
    keys = sorted(auth)
    for a, b in zip(keys, keys[1:]):  # 'Kumo Kagyu' / 'Kumo Kagyuu'
        if len(a) > 6 and (a.replace("uu", "u") == b.replace("uu", "u") or a.replace("oo", "o") == b.replace("oo", "o")):
            out[CHECKS[9]].append(" / ".join(f"'{n}'" for n in [*auth[a], *auth[b]]))
    asins = defaultdict(list)
    for r in live:
        if r.get("asin"):
            asins[(r["lib"], r["asin"])].append(r)
    for (lib, asin), rs in asins.items():
        if len({(r["series"], r["num"], r["title"]) for r in rs}) > 1:
            out[CHECKS[10]].append(f"[{lib}] {asin}: " + "  |  ".join(
                f"{r['series']} #{r['num']}" if r["num"] else r["title"] for r in rs))
    return len(live), {k: sorted(set(out[k])) for k in CHECKS if out.get(k)}


def report(total: int, found: dict[str, list[str]]) -> str:
    lines = [f"Audiobookshelf check - {total} books - read only, nothing changed", ""]
    if not found:
        lines.append("No problems found.")
    for k, v in found.items():
        lines.append(f"{k}: {len(v)}")
        lines += [f"    {x}" for x in v]
        lines.append("")
    return "\n".join(lines)
