"""The last scan and lookups, saved so the app opens with everything at once (collection.json)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Owned, Series, series_key


def save(state: Path, series: dict[str, Series], alone: list[Owned], scanned: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / "collection.tmp"
    tmp.write_text(json.dumps({"scanned": scanned, "series": [s.to_json() for s in series.values()],
                               "standalone": [asdict(o) for o in alone]}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(state / "collection.json")


def load(state: Path) -> tuple[dict[str, Series], list[Owned], str]:
    try:
        data = json.loads((state / "collection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, [], ""
    series = {d["key"]: Series.from_json(d) for d in data.get("series") or []}
    return series, [Owned(**o) for o in data.get("standalone") or []], data.get("scanned", "")


def merge_lookups(new: dict[str, Series], old: dict[str, Series]) -> None:
    """A fresh scan keeps what the last online check found (no need to ask again)."""
    for key, s in new.items():
        o = old.get(key)
        if o is not None and series_key(o.name) != series_key(s.name):
            o = None  # the key now holds another series ('86--EIGHTY-SIX Alter', split from 86): ask again
        if o is not None:
            s.audible, s.others, s.total_hint, s.status = o.audible, o.others, o.total_hint, o.status
            s.links, s.checked, s.track = o.links, o.checked, o.track
            s.manga_hint = o.manga_hint
