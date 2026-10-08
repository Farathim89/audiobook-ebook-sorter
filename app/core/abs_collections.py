"""Audiobookshelf collections keep the order books were added in (no sort option). Put them in
reading order: the collection's own series by number, then other series, then stand-alone books."""
from __future__ import annotations

import re
from dataclasses import dataclass

_KIND_RE = re.compile(r"(?i)\s*\((?:manga|comic|light novel)\)$")


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", _KIND_RE.sub("", name or "").lower())


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reading_order(collection: dict) -> list[str]:
    """Library item ids of an ABS collection in reading order."""
    main = _norm(collection.get("name"))
    rows = []
    for b in collection.get("books") or []:
        meta = ((b.get("media") or {}).get("metadata") or {})
        series = (meta.get("series") or [None])[0] or {}
        name = series.get("name")
        seq = _number(series.get("sequence"))
        group = 2 if not name else (0 if _norm(name) == main else 1)
        rows.append((group, (name or "").casefold(), seq is None, seq or 0.0, (meta.get("title") or "").casefold(), b["id"]))
    return [r[-1] for r in sorted(rows)]


@dataclass
class CollectionChange:
    id: str
    name: str
    books: int
    order: list[str]
    first: list[str]  # first titles in the new order, for the preview


def plan_collection_sort(client) -> list[CollectionChange]:
    """Collections whose order would change (read-only)."""
    changes = []
    for c in client.collections():
        order = reading_order(c)
        if order != [b["id"] for b in c.get("books") or []]:
            titles = {b["id"]: ((b.get("media") or {}).get("metadata") or {}).get("title") or "" for b in c["books"]}
            changes.append(CollectionChange(c["id"], c.get("name") or "", len(order), order,
                                            [titles[i] for i in order[:3]]))
    return changes


def apply_collection_sort(client, changes: list[CollectionChange]) -> int:
    for ch in changes:
        client.set_collection_order(ch.id, ch.order)  # same books, new order only
    return len(changes)
