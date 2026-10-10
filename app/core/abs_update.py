"""Update Audiobookshelf after a sort, in one go:

    1. back up the listening progress
    2. scan every book library (and wait until ABS is done)
    3. a book that got a new entry (moved, renamed) gets its progress back - found by ASIN / title + author
    4. check the libraries and report what looks off

Nothing in the library folders is touched - ABS only re-reads them."""
from __future__ import annotations

import time
from typing import Callable

from ..config import Folders
from ..providers.abs import AbsClient
from .abs_check import check_abs, report
from .abs_progress import apply_restore, backup_progress, plan_restore

SCAN_TIMEOUT = 30 * 60  # seconds per library
POLL = 3


def _last_scan(client: AbsClient, library_id: str):
    lib = client._call("GET", f"/libraries/{library_id}") or {}
    return (lib.get("library") or lib).get("lastScan")


def update_abs(folders: Folders, client: AbsClient, on_progress: Callable[[str], None] = lambda _: None,
               sleep: Callable[[float], None] = time.sleep) -> str:
    lines = []
    on_progress("Backing up listening progress")
    backup, n = backup_progress(folders, client, prefix="abs-progress-before-update")
    lines.append(f"Listening progress backed up: {n} books -> {backup}")
    for lib in [x for x in client.libraries() if x.get("mediaType") == "book"]:
        name = lib.get("name") or lib["id"]
        before = _last_scan(client, lib["id"])
        on_progress(f"Scanning {name}")
        client._call("POST", f"/libraries/{lib['id']}/scan")
        waited = 0
        while waited < SCAN_TIMEOUT and _last_scan(client, lib["id"]) == before:
            sleep(POLL)
            waited += POLL
            on_progress(f"Scanning {name} ({waited // 60}:{waited % 60:02d})")
        lines.append(f"Scanned {name}" + ("" if waited < SCAN_TIMEOUT else " - still busy, not waiting longer"))
    on_progress("Finding moved books")
    steps = [s for s in plan_restore(client, backup) if s.action == "set" and s.how != "same item"]
    if steps:
        before_restore, done = apply_restore(folders, client, steps)
        lines.append(f"Progress moved to the new entry of {done} books (as it was before: {before_restore}):")
        lines += [f"    {s.title} [{s.library}] - {s.state} (by {s.how})" for s in steps]
    else:
        lines.append("No progress had to be moved.")
    on_progress("Checking the libraries")
    lines += ["", report(*check_abs(client, on_progress))]
    return "\n".join(lines)
