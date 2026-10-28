"""Back up and restore your Audiobookshelf listening progress.

Progress belongs to a library item: when books move to another library (or a library is split), ABS
sees new items and the progress stays behind. The backup keeps, per book, what is needed to find it
again (title, authors, ASIN, path, length); the restore finds each book in the libraries you have now
(same item, else ASIN, else title + author) and writes the progress only where it is missing or older.
Before writing, the current progress is saved too. Only ABS's progress is changed - no files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Folders
from ..portable import PORTABLE
from ..providers.abs import AbsClient
from .text import fold


def backups_dir(folders: Folders) -> Path:
    return (PORTABLE if PORTABLE is not None else folders.state) / "abs-backups"


def _names(value) -> list[str]:
    """Authors as a list - ABS's item list gives one text ('Kevin Gifford -translator, Fuse')."""
    if isinstance(value, str):
        value = re.split(r"\s*,\s*", value)
    return [n for n in (value or []) if n and not re.search(r"(?i)translat|illustrat", n)]


def library_items(client: AbsClient) -> tuple[list[dict], list[dict]]:
    """(libraries, items) - every item with what is needed to find it again."""
    libraries = client.libraries()
    items = []
    for lib in libraries:
        for it in client.library_items(lib["id"]):
            media = it.get("media") or {}
            md = media.get("metadata") or {}
            items.append({
                "id": it["id"], "library": lib["name"], "libraryId": lib["id"], "path": it.get("path"),
                "missing": bool(it.get("isMissing")), "title": md.get("title"), "asin": md.get("asin"),
                "isbn": md.get("isbn"), "authors": _names([a.get("name") for a in md.get("authors") or []]
                                                          or md.get("authorName")),
                "series": md.get("seriesName") or md.get("series"), "duration": media.get("duration") or 0})
    return libraries, items


def backup_progress(folders: Folders, client: AbsClient, prefix: str = "abs-progress") -> tuple[Path, int]:
    """Save every progress and bookmark of the API key's user. (file, progress entries)."""
    me = client.me()
    libraries, items = library_items(client)
    by_id = {i["id"]: i for i in items}
    data = {
        "exported": datetime.now().isoformat(timespec="seconds"),
        "server": client.url, "user": {"id": me.get("id"), "username": me.get("username")},
        "libraries": [{"id": lib["id"], "name": lib["name"]} for lib in libraries],
        "progress": [dict(p, item=by_id.get(p.get("libraryItemId"))) for p in me.get("mediaProgress") or []],
        "bookmarks": [dict(b, item=by_id.get(b.get("libraryItemId"))) for b in me.get("bookmarks") or []],
    }
    stem = f"{prefix}-{me.get('username') or 'user'}-{datetime.now():%Y%m%d-%H%M%S}"
    out = backups_dir(folders) / f"{stem}.json"
    n = 1
    while out.exists():  # never over another backup
        n += 1
        out = out.with_name(f"{stem}-{n}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return out, len(data["progress"])


@dataclass
class RestoreStep:
    action: str                 # "set", "keep" (has newer progress already), "skip" (not found / unclear)
    title: str
    state: str                  # "finished" / "34% (9.1 h)"
    library: str = ""
    how: str = ""               # "same item" / "ASIN" / "title + author" / why skipped
    item_id: str = ""
    duration: float = 0
    progress: dict = field(default_factory=dict)


def _is_ebook_progress(p: dict) -> bool:
    """Reading progress of an ebook (a page / location), not a listening position."""
    return bool(p.get("ebookLocation") or p.get("ebookProgress")) and not p.get("currentTime")


def plan_restore(client: AbsClient, backup: Path) -> list[RestoreStep]:
    data = json.loads(backup.read_text(encoding="utf-8"))
    _, items = library_items(client)
    current = {p.get("libraryItemId"): p for p in client.me().get("mediaProgress") or []}
    live = [i for i in items if not i["missing"]]
    steps = []
    for p in data.get("progress") or []:
        old = p.get("item") or {}
        title = old.get("title") or "?"
        state = ("finished" if p.get("isFinished")
                 else f"{(p.get('progress') or 0):.0%} ({(p.get('currentTime') or 0) / 3600:.1f} h)")
        found, how = [i for i in live if old.get("id") and i["id"] == old["id"]], "same item"
        # by ASIN / title only the same kind: an audiobook's listening position never lands on the ebook
        ebook = _is_ebook_progress(p)
        same_kind = [i for i in live if (not i["duration"]) == ebook]
        if not found and old.get("asin"):
            found, how = [i for i in same_kind if i["asin"] and i["asin"].upper() == old["asin"].upper()], "ASIN"
        if not found:
            mine = {fold(a) for a in _names(old.get("authors"))}
            found = [i for i in same_kind if fold(i["title"]) == fold(title)
                     and (not mine or mine & {fold(a) for a in i["authors"]})]
            how = "title + author"
        if len(found) != 1:
            steps.append(RestoreStep("skip", title, state, how=f"{len(found)} matching books", progress=p))
            continue
        it = found[0]
        now = current.get(it["id"])
        if now and (now.get("isFinished") or now.get("currentTime")) \
                and (now.get("lastUpdate") or 0) >= (p.get("lastUpdate") or 0):
            steps.append(RestoreStep("keep", title, state, it["library"], "has newer progress already",
                                     it["id"], it["duration"], p))
            continue
        steps.append(RestoreStep("set", title, state, it["library"], how, it["id"], it["duration"], p))
    return steps


def apply_restore(folders: Folders, client: AbsClient, steps: list[RestoreStep]) -> tuple[Path, int]:
    """Write the 'set' steps. The progress as it is now is saved first (a backup to restore)."""
    before, _ = backup_progress(folders, client, prefix="abs-progress-before-restore")
    done = 0
    for s in steps:
        if s.action != "set":
            continue
        p = s.progress
        body = {"isFinished": bool(p.get("isFinished")), "currentTime": p.get("currentTime") or 0,
                "duration": s.duration or p.get("duration") or 0, "progress": p.get("progress") or 0,
                "hideFromContinueListening": bool(p.get("hideFromContinueListening"))}
        body.update({k: p[k] for k in ("startedAt", "finishedAt", "lastUpdate", "ebookLocation", "ebookProgress")
                     if p.get(k)})  # an ebook's page too
        client.set_progress(s.item_id, body)
        done += 1
    return before, done
