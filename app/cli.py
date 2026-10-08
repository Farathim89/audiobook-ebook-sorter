"""Command line: preview, sort and undo.

    python -m app.cli                       preview the default Incoming folders
    python -m app.cli --online --files      ... with Audible lookup, every file listed
    python -m app.cli --online --apply      copy into Sorted (asks first)
    python -m app.cli --history             list past runs
    python -m app.cli --undo last           undo the last run
    python -m app.cli --auto                copy only the sure books, no questions (for Task Scheduler)
    python -m app.cli --online --apply --m4b   ... and turn mp3 audiobooks into chaptered .m4b files
    python -m app.cli --to-m4b "D:\Book" --online   just convert one folder (output next to it)
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from .config import load_folders
from .core.auto import auto_sort, find_duplicates
from .core.consistency import unify_names
from .core.executor import Executor
from .core.history import Processed, list_runs, undo
from .core.library import (LibraryEditor, apply_manga_refresh, edit_sorted_book, find_xhe_aac, load_sorted_book,
                           apply_cover_upgrades, empty_done, find_sorted_originals, move_duplicates,
                           plan_cover_upgrades, move_sorted_originals,
                           plan_manga_refresh,
                           reorganize_audio, reorganize_books)
from .core import m4b
from .core.models import Book, BookType, Status
from .core.online import OnlineLookup
from .core.pipeline import load_books
from .core.planner import Plan, Planner


def main(argv: list[str] | None = None) -> int:
    folders = load_folders()
    ap = argparse.ArgumentParser(prog="booksorter")
    ap.add_argument("sources", nargs="*", type=Path,
                    help="folders to scan (default: the audiobook and book Incoming folders)")
    ap.add_argument("--audio-out", type=Path, default=folders.audio_sorted)
    ap.add_argument("--books-out", type=Path, default=folders.books_sorted)
    ap.add_argument("--review", type=Path, default=folders.review)
    ap.add_argument("--online", action="store_true", help="look audiobooks up on Audible")
    ap.add_argument("--all", action="store_true", help="include books that were already sorted")
    ap.add_argument("--files", action="store_true", help="list every file, not just the book folder")
    ap.add_argument("--status", choices=[s.value for s in Status], help="only show books with this status")
    ap.add_argument("--apply", action="store_true", help="really copy the books (asks first)")
    ap.add_argument("--move", action="store_true", help="with --apply: move instead of copy")
    ap.add_argument("--m4b", action="store_true", help="with --apply/--auto: multi-file audiobooks -> one .m4b")
    ap.add_argument("--to-m4b", metavar="FOLDER", type=Path, help="convert the audiobook(s) in FOLDER, no sorting")
    ap.add_argument("--out", type=Path, help="with --to-m4b: where to write (default: next to the folder)")
    ap.add_argument("--reorganize-books", action="store_true",
                    help="split Books Sorted into Manga / Light Novels / EBooks / Comics (preview; add --apply)")
    ap.add_argument("--edit-sorted", metavar="FOLDER", type=Path,
                    help="correct a sorted book (with --set-title/--set-author/--set-series/--set-number/--set-narrator/--set-year)")
    for opt in ("title", "author", "series", "number", "narrator", "year"):
        ap.add_argument(f"--set-{opt}", metavar=opt.upper())
    ap.add_argument("--compare", metavar="FOLDER", type=Path,
                    help="which books in FOLDER are in your libraries? sort them into FOLDER\\-=Have=- and -=Do Not Have=- (preview; add --apply)")
    ap.add_argument("--sort-collections", action="store_true",
                    help="put Audiobookshelf collections in reading order (preview; add --apply)")
    ap.add_argument("--improve-covers", action="store_true",
                    help="better covers for sorted books with none or a low-res one (preview; add --apply)")
    ap.add_argument("--empty-done", action="store_true",
                    help="send Done\\<day> folders older than the Settings limit to the Recycle Bin")
    ap.add_argument("--clean-originals", action="store_true",
                    help="move the originals of already-sorted books from Incoming to the Done folder "
                         "(preview; add --apply)")
    ap.add_argument("--move-duplicates", action="store_true",
                    help="move extra copies / books the library has out of Incoming (preview; add --apply)")
    ap.add_argument("--refresh-manga", action="store_true",
                    help="sorted manga: MangaDex covers + Fandom volume titles/year/ISBN (preview; add --apply)")
    ap.add_argument("--reorganize-audio", action="store_true",
                    help="split AudioBooks Sorted into Light Novels / Audiobooks (preview; add --apply)")
    ap.add_argument("--fix-xhe", metavar="FOLDER", type=Path, nargs="?", const=Path(),
                    help="re-encode xHE-AAC files in a sorted library (default: AudioBooks Sorted) to plain AAC")
    ap.add_argument("--m4b-in-library", metavar="FOLDER", type=Path,
                    help="turn a SORTED mp3 audiobook folder into one chaptered m4b (old files parked)")
    ap.add_argument("--yes", action="store_true", help="with --apply: don't ask")
    ap.add_argument("--auto", action="store_true",
                    help="sort only Ready books (sure match, no problems) without asking; copy mode")
    ap.add_argument("--history", action="store_true", help="list past runs")
    ap.add_argument("--undo", metavar="RUN", help="undo a run ('last' or a run id from --history)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    folders.audio_sorted, folders.books_sorted, folders.review = args.audio_out, args.books_out, args.review
    history_dir = folders.state / "history"
    processed = Processed(folders.state / "processed.txt")

    if args.compare:
        from .core.compare import apply_compare, plan_compare  # noqa: PLC0415
        result = plan_compare(folders, [args.compare])
        print(f"Compared with {result.library_books} library books: "
              f"have {len(result.have)}, do not have {len(result.have_not)}")
        for b, lib in result.have:
            print(f"  HAVE      {b.folder or b.main_file}\n            = {lib.folder or lib.main_file}")
        for b in result.have_not:
            print(f"  NOT HAVE  {b.folder or b.main_file}")
        if not args.apply:
            print("Preview only - add --apply to sort them into -=Have=- / -=Do Not Have=-.")
        else:
            run_id, n = apply_compare(folders, result, args.compare)
            print(f"{n} files moved - undo with: --undo {run_id}")
        return 0
    if args.sort_collections:
        from .core.abs_collections import apply_collection_sort, plan_collection_sort  # noqa: PLC0415
        from .providers.abs import AbsClient  # noqa: PLC0415
        if not folders.abs_url or not folders.abs_api_key:
            print("Set the Audiobookshelf address and API key in Settings > Online first.")
            return 1
        client = AbsClient(folders.abs_url, folders.abs_api_key)
        changes = plan_collection_sort(client)
        for c in changes:
            print(f"{c.name} ({c.books} books): " + " | ".join(c.first) + " ...")
        if not changes:
            print("All collections are already in reading order.")
        elif not args.apply:
            print(f"{len(changes)} collection(s) - preview only, add --apply.")
        else:
            print(f"{apply_collection_sort(client, changes)} collection(s) sorted.")
        return 0
    if args.improve_covers:
        ups = plan_cover_upgrades(folders, OnlineLookup(folders))
        for up in ups:
            print(f"{up.before:>9} -> {up.why}  |  {up.folder}")
        if not ups:
            print("No sorted book needs a better cover (or none was found).")
        elif not args.apply:
            print(f"{len(ups)} covers - preview only, add --apply.")
        else:
            run_id, n = apply_cover_upgrades(folders, ups)
            print(f"{n} covers replaced - undo with: --undo {run_id}")
        return 0
    if args.empty_done:
        gone = empty_done(folders)
        print("\n".join(f"Recycle Bin: {d}" for d in gone) or
              f"Nothing older than {folders.done_days} days in {folders.done}" if folders.done_days else
              "Emptying Done is off (Settings > Sorting).")
        return 0
    if args.history:
        for run in list_runs(history_dir):
            print(f"{run.run_id}  {run.time}  {run.mode:4}  {run.files:5} files" + ("  (undone)" if run.undone else ""))
        return 0
    if args.undo:
        runs = [r for r in list_runs(history_dir) if not r.undone]
        run = runs[0] if args.undo == "last" and runs else next((r for r in runs if r.run_id == args.undo), None)
        if run is None:
            print("No such run (see --history).")
            return 1
        res = undo(run, processed)
        print(f"Undid {run.run_id}: {res.restored} files undone")
        for s in res.skipped:
            print(f"  kept: {s}")
        return 0

    if args.to_m4b:
        return _to_m4b(args, folders)
    if args.edit_sorted:
        return _edit_sorted(args, folders)
    if args.move_duplicates:
        return _move_duplicates(args, folders)
    if args.clean_originals:
        return _clean_originals(args, folders)
    if args.refresh_manga:
        return _refresh_manga(args, folders)
    if args.reorganize_books or args.reorganize_audio:
        return _reorganize(args, folders)
    if args.m4b_in_library:
        return _m4b_in_library(args, folders)
    if args.fix_xhe is not None:
        return _fix_xhe(args, folders)
    if args.m4b:
        folders.to_m4b = True
    if args.auto:
        res = auto_sort(folders, processed, sources=args.sources or None)
        if res.run:
            print(f"Run {res.run.run_id} - undo with: --undo {res.run.run_id}")
        return 0

    sources = args.sources or [p for p in folders.sources if p.is_dir()]
    if not sources:
        print("No source folders found.")
        return 1

    books = load_books(sources)
    if not args.all:
        skipped = [b for b in books if processed.contains(b)]
        books = [b for b in books if not processed.contains(b)]
        if skipped:
            print(f"({len(skipped)} books were sorted before - use --all to include them)")
    if args.online:
        OnlineLookup(folders).run(books, on_progress=lambda i, n, b: print(f"\rlooking up {i}/{n}", end="", flush=True))
        print()
    unify_names(books, folders.library_roots())
    plan = Planner(folders).plan(books)
    for book in books:
        if not args.status or book.status.value == args.status:
            _print_book(book, plan, args.files)

    counts = Counter(b.status.value for b in books)
    print(f"\n{len(books)} books: " + ", ".join(f"{counts[s.value]} {s.value}" for s in Status)
          + f" | {len(plan.review)} to Review | {len(plan.problems)} skipped (problems)")

    if not args.apply:
        print("Preview only - nothing was copied. Add --apply to sort.")
        return 0
    mode = "move" if args.move or folders.move_originals else "copy"
    if not args.yes and input(f"{mode.capitalize()} these books now? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 0
    result = Executor(folders, mode, processed, to_m4b=folders.to_m4b,
                      on_detail=lambda s: print(f"\r{s:60}", end="", flush=True)).run(
        plan, on_progress=lambda i, n, b: print(f"\r{mode} {i}/{n}", end="", flush=True))
    print()
    for r in result.results:
        if r.status == "failed":
            print(f"FAILED  {r.book.meta.title}: {r.detail}")
    print(f"Run {result.run_id}: {result.count('done')} sorted, {result.count('review')} to Review, "
          f"{result.count('skipped')} skipped, {result.count('failed')} failed. Undo: --undo {result.run_id}")
    return 0


def _edit_sorted(args, folders) -> int:
    values = {}
    if args.set_title:
        values["title"] = args.set_title
    if args.set_author:
        values["authors"] = [a.strip() for a in args.set_author.split(";") if a.strip()]
    if args.set_series is not None:
        values["series"] = args.set_series
    if args.set_number:
        values["series_index"] = float(args.set_number)
    if args.set_narrator:
        values["narrators"] = [n.strip() for n in args.set_narrator.split(";") if n.strip()]
    if args.set_year:
        values["year"] = int(args.set_year)
    if not values:
        book = load_sorted_book(folders, args.edit_sorted)
        print(book.meta if book else "No book there.")
        return 0
    run_id, new = edit_sorted_book(folders, args.edit_sorted, values)
    print(f"{new}  - undo with: --undo {run_id}")
    return 0


def _clean_originals(args, folders) -> int:
    books, orphans = find_sorted_originals(folders)
    for book in books:
        print(f"SORTED    {book.folder or book.main_file}")
    for folder, _ in orphans:
        print(f"LEFTOVERS {folder}")
    if not books and not orphans:
        print("No sorted originals or leftovers in Incoming.")
    elif not args.apply:
        print(f"{len(books)} sorted books + {len(orphans)} leftover folders to move to {folders.done}"
              " - preview only, add --apply.")
    else:
        run_id, n = move_sorted_originals(folders, books, orphans)
        print(f"{n} moved to {folders.done} - undo with: --undo {run_id}")
    return 0


def _move_duplicates(args, folders) -> int:
    moves, better = find_duplicates(folders)
    for mv in moves:
        print(f"MOVE  {mv.book.folder or mv.book.main_file}\n      {mv.reason}")
    for book, why in better:
        print(f"KEEP  {book.folder or book.main_file}\n      {why}")
    if not moves:
        print("No duplicates to move.")
    elif not args.apply:
        print(f"{len(moves)} to move to {folders.duplicates} - preview only, add --apply.")
    else:
        run_id, n = move_duplicates(folders, moves)
        print(f"{n} moved to {folders.duplicates} - undo with: --undo {run_id}")
    return 0


def _refresh_manga(args, folders) -> int:
    changes = plan_manga_refresh(folders, OnlineLookup(folders))
    for ch in changes:
        print(ch.summary)
    if not changes:
        print("Nothing to update.")
    elif not args.apply:
        print(f"{len(changes)} volumes - preview only, add --apply to update them.")
    else:
        run_id, n = apply_manga_refresh(folders, changes)
        print(f"{n} volumes updated - undo with: --undo {run_id}")
    return 0


def _reorganize(args, folders) -> int:
    lookup = OnlineLookup(folders)
    split = reorganize_audio if args.reorganize_audio else reorganize_books
    res = split(folders, lookup.is_light_novel, dry_run=not args.apply)
    for line in res.moved + [f"merge {m}" for m in res.merged]:
        print(line)
    for s in res.skipped:
        print(f"skipped: {s}")
    if not args.apply:
        print("Preview only - add --apply to move the folders.")
    elif res.run_id:
        print(f"Run {res.run_id} - undo with: --undo {res.run_id}")
    return 0


def _fix_xhe(args, folders) -> int:
    root = args.fix_xhe if str(args.fix_xhe) not in ("", ".") else folders.audio_sorted
    files = find_xhe_aac(root)
    if not files:
        print("No xHE-AAC files found.")
        return 0
    with LibraryEditor(folders, "fix-xhe") as ed:
        for f in files:
            print(f"xHE-AAC -> AAC: {f.name}")
            ed.fix_xhe_aac(f)
        print(f"{len(files)} fixed - undo with: --undo {ed.run_id}")
    return 0


def _m4b_in_library(args, folders) -> int:
    if not m4b.ffmpeg():
        print("ffmpeg was not found - install it with: winget install Gyan.FFmpeg")
        return 1
    folder = args.m4b_in_library
    book = next((b for b in load_books([folder.parent]) if b.folder == folder and b.type is BookType.AUDIO), None)
    if book is None or all(f.suffix.lower() == ".m4b" for f in book.files):
        print("No multi-file audiobook in that folder.")
        return 1
    with LibraryEditor(folders, "m4b") as ed:
        out = ed.convert_to_m4b(book, OnlineLookup(folders).audible.http,
                                on_progress=lambda f: print(f"\r  {f:.0%}", end="", flush=True))
        print(f"\r{out}  - undo with: --undo {ed.run_id}")
    return 0


def _to_m4b(args, folders) -> int:
    if not m4b.ffmpeg():
        print("ffmpeg was not found - install it with: winget install Gyan.FFmpeg")
        return 1
    books = [b for b in load_books([args.to_m4b]) if b.type is BookType.AUDIO]
    if not books:
        print("No audiobook found there.")
        return 1
    lookup = OnlineLookup(folders) if args.online else None
    if lookup:
        lookup.run(books)
    http = lookup.audible.http if lookup else None
    for b in books:
        out_dir = args.out or (b.folder or b.main_file.parent).parent
        print(f"{b.meta.title}: {len(b.files)} files -> {out_dir}")
        path, source = m4b.make_m4b(b, out_dir, http, on_progress=lambda f: print(f"\r  {f:.0%}", end="", flush=True))
        print(f"\r  {path}  (chapters from {source})")
    return 0


def _print_book(book: Book, plan: Plan, show_files: bool) -> None:
    m = book.meta
    series = f"{m.series} #{m.series_index:g}" if m.series and m.series_index is not None else (m.series or "")
    line = " | ".join(x for x in (", ".join(m.authors) or "?", series, m.title or "?") if x)
    print(f"[{book.status.value.upper():7}] {book.type.value:9} {line}")
    print(f"          from {_rel(book.folder or book.main_file, book.source_root)}  ({len(book.files)} files"
          + (f" + {len(book.extras)} extras)" if book.extras else ")"))
    print(f"          to   {plan.targets[book]}")
    for note in book.notes + plan.problems.get(book, []):
        print(f"          !    {note}")
    if show_files:
        for mv in (mv for mv in plan.moves if mv.book is book):
            print(f"             {mv.src.name}  ->  {mv.dst.relative_to(plan.targets[book])}")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)) or "."
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
