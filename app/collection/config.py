"""Collection settings: where your books are (Audiobookshelf, library folders, the sorter's staging folders) and
which online sources say what a series has. Saved as collection\\settings.json next to the sorter's data; the
Audiobookshelf address and key are always the sorter's own (Settings), so they live in one place."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .portable import PORTABLE, take_over_old_checker

STATE: Path = PORTABLE
SETTINGS_FILE = STATE / "settings.json"


@dataclass
class Settings:
    use_abs: bool = True
    abs_url: str = ""            # e.g. http://localhost:13378/audiobookshelf
    abs_api_key: str = ""        # ABS: Settings > API Keys
    library_folders: list[str] = field(default_factory=list)   # read only - never written to
    staging_folders: list[str] = field(default_factory=list)   # e.g. the sorter's Sorted folders
    audible_region: str = "us"
    use_audible: bool = True     # every volume Audible sells in the series, with release dates
    use_anilist: bool = True     # light novel / manga volume counts
    use_google: bool = True      # ebook volumes (Google Books)
    google_books_key: str = ""   # optional; without it Google often answers 'too many requests'
    hidden_series: list[str] = field(default_factory=list)  # series keys you don't want to see
    theme: str = "Gold (dark)"


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    if path == SETTINGS_FILE:
        take_over_old_checker()  # first use: the old Checker's settings, collection and cache
    s = _load(path)
    sorter = _sorter_settings()
    if sorter:  # always the sorter's (a changed or removed key there counts here too) - never a stale copy
        s.abs_url, s.abs_api_key = sorter.get("abs_url") or "", sorter.get("abs_api_key") or ""
    return s


def _load(path: Path) -> Settings:
    s = Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:  # damaged: keep it aside, never silently overwrite hidden series / folders
        try:
            path.replace(path.with_name(path.name + ".broken"))
        except OSError:
            pass
        data = None
    except OSError:
        data = None
    if data is None:
        s = import_from_book_sorter(s)
        try:
            save_settings(s, path)  # first run: keep what came from the sorter's settings
        except OSError:
            pass
        return s
    for f in fields(Settings):
        if f.name in data:
            setattr(s, f.name, data[f.name])
    return s


NOT_SAVED = ("abs_url", "abs_api_key")  # the sorter's settings hold them - one place for the key


def save_settings(s: Settings, path: Path = SETTINGS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in asdict(s).items() if k not in NOT_SAVED}
    tmp = path.with_name(path.name + ".tmp")  # never a half-written file
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def book_sorter_settings() -> Path | None:
    """The sorter's own settings.json (this app)."""
    from ..config import SETTINGS_FILE as SORTER_SETTINGS  # noqa: PLC0415
    return SORTER_SETTINGS if SORTER_SETTINGS.is_file() else None


def _sorter_settings() -> dict:
    path = book_sorter_settings()
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}


def import_from_book_sorter(s: Settings) -> Settings:
    """First run without an old Checker: take the ABS address/key, the library folders (the sorter's protected
    ones) and its sorted (staging) folders from the sorter's settings - nothing to type."""
    data = _sorter_settings()
    if not data:
        return s
    s.abs_url = data.get("abs_url") or s.abs_url
    s.abs_api_key = data.get("abs_api_key") or s.abs_api_key
    s.audible_region = data.get("audible_region") or s.audible_region
    s.google_books_key = data.get("google_books_key") or s.google_books_key
    s.library_folders = [p for p in data.get("protected") or [] if "podcast" not in p.lower()]
    s.staging_folders = [p for p in (data.get("audio_sorted"), data.get("books_sorted"),
                                     data.get("swedish_sorted")) if p]
    return s
