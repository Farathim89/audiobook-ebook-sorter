"""MP3 (or any audio) -> one chaptered .m4b with ffmpeg, and better chapter names.

Chapter names, best first:
1. Audible's real chapter list (Audnexus, by ASIN) when its total length matches the files
2. the files themselves: track titles / file names with the shared prefix ("Carl's Doomsday
   Scenario (01)") and track numbers stripped, else "Chapter N"
A single .m4b that already exists can get Audible chapters by remuxing (no re-encoding).
"""
from __future__ import annotations

import logging
import os
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import mutagen
from mutagen.mp4 import MP4, MP4Cover

from ..providers.http import Http
from .tools import find_tool
from ..readers.audio import read_tags
from .models import Book
from .text import natural_key

log = logging.getLogger(__name__)
AUDNEXUS = "https://api.audnex.us/books/{asin}/chapters"
MATCH_TOLERANCE = 0.015  # Audible's runtime may differ this much from the files (1.5 %)
_NUMBER_ONLY_RE = re.compile(r"^\W*(?:chapter|track|part|kapitel|ch\.?)?\W*(\d+)\W*$", re.I)
_LEADING_TRACK_RE = re.compile(r"^\s*(?:track\s*)?\d{1,4}\s*(?:[-–—._:)]\s*|\s+(?=\D))", re.I)
_BRACKET_NUMBER_RE = re.compile(r"\s*[\(\[]\s*\d{1,4}\s*(?:of\s*\d+)?\s*[\)\]]\s*$", re.I)


@dataclass
class Chapter:
    start: float  # seconds
    end: float
    title: str


def ffmpeg() -> str | None:
    return find_tool("ffmpeg")


# -- durations ---------------------------------------------------------------
def duration(path: Path) -> float:
    try:
        f = mutagen.File(path)
        if f is not None and f.info and f.info.length:
            return float(f.info.length)
    except Exception:  # noqa: BLE001
        pass
    probe = find_tool("ffprobe")
    if probe:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, creationflags=_NO_WINDOW)
        try:
            return float(out.stdout.strip())
        except ValueError:
            pass
    return 0.0


# -- chapter names -----------------------------------------------------------
def chapters_from_files(files: list[Path], durations: list[float]) -> list[Chapter]:
    names = []
    for f in files:
        title = read_tags(f).get("title")
        names.append(title if title and not _NUMBER_ONLY_RE.match(title) else f.stem)
    names = tidy_chapter_names(names)
    chapters, t = [], 0.0
    for name, d in zip(names, durations):
        chapters.append(Chapter(t, t + d, name))
        t += d
    return chapters


def tidy_chapter_names(names: list[str]) -> list[str]:
    """Drop what every name shares (book title, author...), track numbers; empty -> 'Chapter N'."""
    words = [n.replace("_", " ").split() for n in names]
    if len(words) > 1:
        k = len(os.path.commonprefix(words))                       # shared leading words
        j = len(os.path.commonprefix([w[::-1] for w in words]))    # shared trailing words
        while k + j and any(len(w) <= k + j for w in words):       # never cut a whole name away
            if j:
                j -= 1
            else:
                k -= 1
        words = [w[k:len(w) - j] for w in words]
    cleaned = [" ".join(w) for w in words]
    out = []
    for i, n in enumerate(cleaned, 1):
        n = _BRACKET_NUMBER_RE.sub("", n)
        n = _LEADING_TRACK_RE.sub("", n).strip(" -–—_.:")
        m = _NUMBER_ONLY_RE.match(n) if n else None
        if not n or m:
            n = f"Chapter {int(m.group(1)) if m else i}"
        out.append(n)
    return out


def audible_chapters(http: Http, asin: str) -> tuple[list[Chapter], float] | None:
    """Audible's own chapter list and runtime (seconds), unscaled."""
    try:
        data = http.get_json(AUDNEXUS.format(asin=asin), {"region": "us"})
    except OSError as e:
        log.info("no Audible chapters for %s: %s", asin, e)
        return None
    items = data.get("chapters") or []
    runtime = (data.get("runtimeLengthMs") or 0) / 1000
    if not items or not runtime:
        return None
    chapters = []
    for i, c in enumerate(items, 1):
        start = c.get("startOffsetMs", 0) / 1000
        chapters.append(Chapter(start, start + c.get("lengthMs", 0) / 1000, (c.get("title") or "").strip() or f"Chapter {i}"))
    return chapters, runtime


def _scaled(chapters: list[Chapter], runtime: float, total: float) -> list[Chapter]:
    k = total / runtime
    out = [Chapter(c.start * k, c.end * k, c.title) for c in chapters]
    out[-1].end = total
    return out


def chapters_in(path: Path) -> list[Chapter]:
    """The chapters already inside an m4b (ffprobe), [] when none or no ffprobe."""
    probe = find_tool("ffprobe")
    if not probe:
        return []
    out = subprocess.run([probe, "-v", "error", "-show_chapters", "-of", "json", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    try:
        found = json.loads(out.stdout or "{}").get("chapters") or []
    except ValueError:
        return []
    return [Chapter(float(c.get("start_time") or 0), float(c.get("end_time") or 0),
                    ((c.get("tags") or {}).get("title") or "").strip()) for c in found]


_GENERIC_CHAPTER_RE = re.compile(r"(?i)^(?:(?:chapter|ch\.?|kapitel|track|part|del|disc|cd|file)\s*)?[\divxlc]+\.?$|^$")
SAME_CHAPTER_SECONDS = 5.0


def chapters_are_good(existing: list[Chapter], audible: list[Chapter] | None = None) -> bool:
    """Keep the book's own chapters (M4B -> M4B skips the file) when they have real names
    ('Prologue', 'The Bound Demon' - not 'Chapter 3' / 'Track 01'), or when they are Audible's anyway
    (same count, every start within a few seconds)."""
    if len(existing) < 2:
        return False
    if audible and len(audible) == len(existing) and all(
            abs(a.start - b.start) <= SAME_CHAPTER_SECONDS for a, b in zip(existing, audible)):
        return True
    named = sum(1 for c in existing if not _GENERIC_CHAPTER_RE.match(c.title))
    return named >= 0.8 * len(existing)


def chapters_from_audible(http: Http, asin: str, total: float) -> list[Chapter] | None:
    """Audible's chapter list, stretched to our exact length, if the runtimes agree."""
    found = audible_chapters(http, asin)
    if not found or not total:
        return None
    chapters, runtime = found
    if abs(runtime - total) / total > MATCH_TOLERANCE:
        return None
    return _scaled(chapters, runtime, total)


# -- chapter breaks from silences ----------------------------------------------
SILENCE_MAX_DIFF = 0.25     # Audible runtime may differ this much (another recording) for silence snapping
SILENCE_MIN = 0.8           # seconds of quiet that count as a pause
SILENCE_NOISE = "-35dB"
_SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def detect_silences(files: list[Path], cancelled: Callable[[], bool] = lambda: False) -> list[tuple[float, float]]:
    """[(start, end)] of every pause in the joined audio (decodes it once, no output file)."""
    exe = ffmpeg()
    if not exe:
        return []
    with tempfile.TemporaryDirectory() as td:
        listing = Path(td) / "files.txt"
        listing.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in files), encoding="utf-8")
        cmd = [exe, "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing), "-vn",
               "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN}", "-f", "null", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        silences, start = [], None
        for line in proc.stderr:
            if cancelled():
                proc.kill()
                return []
            for kind, value in _SILENCE_RE.findall(line):
                if kind == "start":
                    start = max(0.0, float(value))
                elif start is not None:
                    silences.append((start, float(value)))
                    start = None
        proc.wait()
    return silences


def snap_to_silences(chapters: list[Chapter], silences: list[tuple[float, float]], total: float,
                     window: float = 0.03, joins: list[float] = ()) -> list[Chapter] | None:
    """Move each chapter start (except the first) to the best pause near it.

    Best = long and close: pause length * (1 - distance / window), x1.5 when the pause is at a
    file join (rips are often split at chapters). Gives up (None) when more than a fifth of the
    chapters have no pause nearby."""
    win = max(30.0, total * window)

    def score(p: tuple[float, float], target: float) -> float:
        mid = (p[0] + p[1]) / 2
        s = (p[1] - p[0]) * (1 - abs(mid - target) / win)
        return s * 1.5 if any(p[0] - 1 <= j <= p[1] + 1 for j in joins) else s

    starts, misses, last = [0.0], 0, 0.0
    for c in chapters[1:]:
        near = [(s, e) for s, e in silences if abs((s + e) / 2 - c.start) <= win and (s + e) / 2 > last + 5]
        if near:
            s, e = max(near, key=lambda p: score(p, c.start))
            t = max(s, e - 0.5)  # start just before the voice comes back
        else:
            misses += 1
            t = max(c.start, last + 5)
        starts.append(t)
        last = t
    if misses > max(1, len(chapters) // 5):
        return None
    out = [Chapter(st, starts[i + 1] if i + 1 < len(starts) else total, c.title)
           for i, (st, c) in enumerate(zip(starts, chapters))]
    return out


def plan_chapters(book: Book, files: list[Path], http: Http | None,
                  cancelled: Callable[[], bool] = lambda: False) -> tuple[list[Chapter], str, float]:
    """(chapters, 'audible' / 'audible+silence' / 'files', total seconds)."""
    durations = [duration(f) for f in files]
    total = sum(durations)
    if http and book.meta.asin and total and (found := audible_chapters(http, book.meta.asin)):
        chapters, runtime = found
        diff = abs(runtime - total) / total
        if diff <= MATCH_TOLERANCE:
            return _scaled(chapters, runtime, total), "audible", total
        if diff <= SILENCE_MAX_DIFF and len(chapters) > 1 and ffmpeg():
            # another recording: Audible's titles, breaks moved to the real pauses in this one
            joins = [sum(durations[:i]) for i in range(1, len(durations))]
            snapped = snap_to_silences(_scaled(chapters, runtime, total), detect_silences(files, cancelled), total,
                                       joins=joins)
            if snapped:
                return snapped, "audible+silence", total
    return chapters_from_files(files, durations), "files", total


# -- ffmpeg ----------------------------------------------------------------
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmeta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def ffmetadata(chapters: list[Chapter], title: str | None) -> str:
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_ffmeta_escape(title)}")
    for c in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(c.start * 1000)}", f"END={int(c.end * 1000)}",
                  f"title={_ffmeta_escape(c.title)}"]
    return "\n".join(lines) + "\n"


def pick_bitrate(files: list[Path]) -> str:
    """The source's bitrate (AAC sounds at least as good as mp3 at the same rate), 32k..128k."""
    try:
        kbps = (mutagen.File(files[0]).info.bitrate or 0) // 1000
    except Exception:  # noqa: BLE001
        kbps = 0
    if not kbps:
        return "64k"
    return f"{next((s for s in (32, 48, 64, 96, 128) if kbps <= s + 4), 128)}k"


Progress = Callable[[float], None]  # 0..1


def convert(files: list[Path], out: Path, chapters: list[Chapter], title: str | None, total: float,
            on_progress: Progress | None = None, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Encode `files` (in order) into `out` (.m4b) with chapters. Writes out.partial, then renames."""
    _run_ffmpeg(files, out, chapters, title, total, encode=True, on_progress=on_progress, cancelled=cancelled)


def rechapter(m4b: Path, out: Path, chapters: list[Chapter], title: str | None, total: float,
              cancelled: Callable[[], bool] = lambda: False) -> None:
    """Copy an m4b with new chapters - no re-encoding."""
    _run_ffmpeg([m4b], out, chapters, title, total, encode=False, cancelled=cancelled)


def encode_m4b(m4b: Path, out: Path, chapters: list[Chapter], title: str | None, total: float,
               on_progress: Progress | None = None, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Re-encode an m4b to AAC (same bitrate, 32k..128k) with new chapters."""
    _run_ffmpeg([m4b], out, chapters, title, total, encode=True, on_progress=on_progress, cancelled=cancelled)


def _run_ffmpeg(files, out, chapters, title, total, encode, on_progress=None, cancelled=lambda: False) -> None:
    exe = ffmpeg()
    if not exe:
        raise OSError("ffmpeg was not found - install it (winget install Gyan.FFmpeg) and restart")
    if out.exists():
        raise FileExistsError(f"already exists: {out}")
    tmp_out = out.with_name(out.name + ".partial")
    with tempfile.TemporaryDirectory() as td:
        listing = Path(td) / "files.txt"
        listing.write_text("".join("file '{}'\n".format(str(f).replace("'", "'\\''")) for f in files), encoding="utf-8")
        meta = Path(td) / "meta.txt"
        meta.write_text(ffmetadata(chapters, title), encoding="utf-8")
        cmd = [exe, "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-i", str(meta), "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        cmd += ["-c:a", "aac", "-b:a", pick_bitrate(files)] if encode else ["-c", "copy"]
        cmd += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-loglevel", "error", str(tmp_out)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        try:
            for line in proc.stdout:
                if cancelled():
                    proc.kill()
                    break
                if line.startswith("out_time_us=") and on_progress and total:
                    try:
                        on_progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / total))
                    except ValueError:
                        pass
            proc.wait()
            if cancelled():
                raise OSError("cancelled")
            if proc.returncode != 0:
                raise OSError(f"ffmpeg failed: {proc.stderr.read()[-500:]}")
        except BaseException:
            proc.kill()
            tmp_out.unlink(missing_ok=True)
            raise
    os.rename(tmp_out, out)


def add_cover(m4b: Path, image: bytes) -> None:
    f = MP4(m4b)
    if f.tags is None:
        f.add_tags()
    fmt = MP4Cover.FORMAT_PNG if image[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
    f.tags["covr"] = [MP4Cover(image, imageformat=fmt)]
    f.save()


def embed_cover_if_bigger(path: Path, image: bytes) -> bool:
    """Put the cover into an .m4b/.m4a too (players that ignore cover.jpg), when it is bigger than the
    art already inside. True when the file was changed."""
    from .covers import image_size  # noqa: PLC0415
    if path.suffix.lower() not in (".m4b", ".m4a"):
        return False
    new = image_size(image)
    if not new:
        return False
    try:
        tags = MP4(path).tags
        old = bytes(tags["covr"][0]) if tags is not None and tags.get("covr") else None
    except Exception:  # noqa: BLE001 - not a readable MP4: leave it alone
        return False
    if old is not None and (image_size(old) or (0, 0))[0] >= new[0]:
        return False
    add_cover(path, image)
    return True


def read_chapters(m4b: Path) -> list[str]:
    """Chapter titles of an m4b (for checks and the GUI)."""
    probe = find_tool("ffprobe")
    if not probe:
        return []
    out = subprocess.run([probe, "-v", "error", "-show_chapters", "-of", "csv=p=0:nk=1", str(m4b)],
                         capture_output=True, text=True, encoding="utf-8", creationflags=_NO_WINDOW)
    return [line.rsplit(",", 1)[-1] for line in out.stdout.splitlines() if line.strip()]


def make_m4b(book: Book, out_dir: Path, http: Http | None, on_progress: Progress | None = None,
             cancelled: Callable[[], bool] = lambda: False) -> tuple[Path, str]:
    """Stand-alone conversion (no sorting): '<out_dir>/<Title>.m4b' with chapters, tags and cover.

    Returns (m4b path, 'audible' / 'files')."""
    from .covers import extract_cover  # noqa: PLC0415 - avoid an import cycle at load time
    from .planner import clean_name  # noqa: PLC0415
    from .sidecars import download  # noqa: PLC0415
    from .tagger import write_tags  # noqa: PLC0415

    files = audio_files_in_order(book)
    chapters, source, total = plan_chapters(book, files, http)
    out = Path(out_dir) / f"{clean_name(book.meta.title or files[0].stem)}.m4b"
    out.parent.mkdir(parents=True, exist_ok=True)
    convert(files, out, chapters, book.meta.title, total, on_progress=on_progress, cancelled=cancelled)
    write_tags(out, book, whole_book=True)
    cover = extract_cover(book)
    image = cover[0] if cover else None
    if image is None and book.meta.cover_url:
        try:
            image = download(book.meta.cover_url)
        except OSError:
            pass
    if image:
        add_cover(out, image)
    return out, source


# -- xHE-AAC (Audible's newer codec) -------------------------------------------
# Audiobookshelf's bundled ffmpeg 5.1 cannot decode xHE-AAC ("Audio object type 42 is not
# implemented"), so such files never show up in ABS. Re-encoding to plain AAC fixes that.

def is_xhe_aac(path: Path) -> bool:
    probe = find_tool("ffprobe")
    if not probe or path.suffix.lower() not in (".m4b", ".m4a", ".mp4", ".aac"):
        return False
    out = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=profile",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, creationflags=_NO_WINDOW)
    return any(k in out.stdout.lower() for k in ("xhe", "usac"))


def _probe_kbps(path: Path) -> int:
    """The audio bitrate in kbit/s; ffprobe first (mutagen reads 0 for xHE-AAC), then mutagen."""
    probe = find_tool("ffprobe")
    if probe:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "stream=bit_rate:format=bit_rate", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, creationflags=_NO_WINDOW)
        for value in out.stdout.replace(",", "\n").split():
            if value.isdigit() and int(value) > 0:
                return int(value) // 1000
    try:
        return (mutagen.File(path).info.bitrate or 0) // 1000
    except Exception:  # noqa: BLE001
        return 0


def _probe_rate(path: Path) -> int:
    probe = find_tool("ffprobe")
    if not probe:
        return 0
    out = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, creationflags=_NO_WINDOW)
    return int(out.stdout.strip()) if out.stdout.strip().isdigit() else 0


def reencode_aac(src: Path, out: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Plain AAC copy of src (chapters, tags and cover kept) written to out (must not exist)."""
    exe = ffmpeg()
    if not exe:
        raise OSError("ffmpeg was not found - install it (winget install Gyan.FFmpeg) and restart")
    if out.exists():
        raise FileExistsError(f"already exists: {out}")
    kbps = max(64, min(128, _probe_kbps(src) or 96))
    tmp = out.with_name(out.name + ".partial")
    audio = out.with_name(out.name + ".audio.m4a")
    if is_xhe_aac(src):
        # ffmpeg loses ~24% of xHE-AAC packets (dropouts): Windows decodes, ffmpeg only copies chapters/tags
        from .xhe import windows_decode_to_aac  # noqa: PLC0415
        windows_decode_to_aac(src, audio, kbps, _probe_rate(src), cancelled)
        cmd = [exe, "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(audio), "-i", str(src),
               "-map", "0:a:0", "-c", "copy", "-map_metadata", "1", "-map_chapters", "1",
               "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    else:
        cmd = [exe, "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(src), "-map", "0:a:0",
               "-map_metadata", "0", "-map_chapters", "0", "-c:a", "aac", "-b:a", f"{kbps}k",
               "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    while proc.poll() is None:
        if cancelled():
            proc.kill()
            tmp.unlink(missing_ok=True)
            raise OSError("cancelled")
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    audio.unlink(missing_ok=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise OSError(f"ffmpeg failed: {proc.stderr.read()[-500:]}")
    try:
        cover = MP4(src).tags.get("covr") if MP4(src).tags else None
        if cover:
            add_cover(tmp, bytes(cover[0]))
    except Exception:  # noqa: BLE001 - the cover is nice to have
        pass
    os.rename(tmp, out)


def audio_files_in_order(book: Book) -> list[Path]:
    return sorted(book.files, key=lambda f: natural_key(str(f.relative_to(book.folder) if book.folder else f.name)))
