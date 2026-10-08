"""Small string helpers shared by the readers and the name parser."""
from __future__ import annotations

import re

_YEAR_RE = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_JUNK_RE = re.compile(
    r"\s*[\(\[\{]\s*(?:unabridged|abridged|retail|audio\s?book|ebook|digital|webrip|scan|c2c"
    r"|epub|mobi|azw3|pdf|m4b|mp3|aac|flac|\d{2,3}\s?k(?:bps)?)\s*[\)\]\}]",
    re.I,
)
_KIND_TAGS = {"light novel": "Light Novel", "manga": "Manga"}
_KIND_TAG_RE = re.compile(r"\((light novel|manga)\)", re.I)
_CATALOG_CODE_RE = re.compile(r"\s+[A-Z]\d{4,7}$")
_TRAILING_JUNK_RE = re.compile(r"[\s\-–—:,]*\b(?:unabridged|abridged)\s*$", re.I)
_PEOPLE_SPLIT_RE = re.compile(r"\s*(?:;|/|\|| & | and | feat\.? )\s*", re.I)


def clean_title(value: str | None) -> str | None:
    """Strip release junk like "(Unabridged)" or "[64kbps]" and tidy whitespace."""
    if not value:
        return None
    s = _JUNK_RE.sub("", value)
    s = _TRAILING_JUNK_RE.sub("", s)
    s = _CATALOG_CODE_RE.sub("", s)  # 'Lotta på Bråkmakargatan C41663' (a library's catalogue number)
    s = re.sub(r"\s+", " ", s).strip(" -–—_,;:")
    s = _KIND_TAG_RE.sub(lambda m: f"({_KIND_TAGS[m.group(1).lower()]})", s)  # shops write "(light Novel)"
    return s or None


def parse_year(value) -> int | None:
    if value is None:
        return None
    m = _YEAR_RE.search(str(value))
    return int(m.group(1)) if m else None


def parse_index(value) -> float | None:
    """'3' -> 3.0, '3/10' -> 3.0, 'Book 2.5' -> 2.5."""
    if value is None:
        return None
    m = _NUMBER_RE.search(str(value))
    if not m:
        return None
    try:
        return float(m.group().replace(",", "."))
    except ValueError:
        return None


def split_people(value: str | None) -> list[str]:
    """Split an author/narrator string into names; 'Sanderson, Brandon' -> 'Brandon Sanderson'."""
    if not value:
        return []
    out: list[str] = []
    for part in _PEOPLE_SPLIT_RE.split(value):
        part = part.strip(" ,")
        if not part:
            continue
        if part.count(",") == 1:
            last, first = (x.strip() for x in part.split(","))
            if last and first and " " not in last:
                out.append(f"{first} {last}")
            else:
                out.extend(x for x in (last, first) if x)
        elif "," in part:
            out.extend(x.strip() for x in part.split(",") if x.strip())
        else:
            out.append(part)
    return dedupe(out)


def dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = fold(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def fold(text: str | None) -> str:
    """Lower-case and drop everything but letters and digits, for loose comparisons."""
    return re.sub(r"[\W_]+", "", text or "").casefold()


def name_key(name: str | None) -> str:
    """A person's name across romanizations: 'Shōgo' = 'Shougo' = 'Syougo' = 'Shogo',
    'Eiichirou' = 'Eiichiro', 'Ōta' = 'Oota' = 'Ota'."""
    import unicodedata  # noqa: PLC0415
    s = unicodedata.normalize("NFKD", name or "")
    s = fold("".join(c for c in s if not unicodedata.combining(c)))
    for old, new in (("sy", "sh"), ("ty", "ch"), ("zy", "j"), ("jy", "j"), ("ou", "o"), ("oo", "o"),
                     ("uu", "u"), ("aa", "a"), ("ii", "i"), ("ee", "e")):
        s = s.replace(old, new)
    return s


def same_text(a: str | None, b: str | None) -> bool:
    return bool(a and b) and fold(a) == fold(b)


def natural_key(text) -> list:
    """Sort key that orders 'Part 2' before 'Part 10'."""
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", str(text))]
