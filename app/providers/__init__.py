"""Online metadata providers. Results are cached; a wrong match is worse than no match."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Metadata


@dataclass(eq=False)
class Candidate:
    meta: Metadata
    provider: str           # "audible", "anilist", ...
    url: str = ""           # a page to look at it
    score: float = 0.0      # 0..1, set by match.rank()
    confident: bool = False  # good enough to apply without asking
    length: float = 0.0     # audiobook length in seconds, when the source tells us
    exact: bool = True      # False: only the main title (before ':') matched - e.g. AniList 'Hell Mode'

    @property
    def label(self) -> str:
        m = self.meta
        series = f" ({m.series} #{m.series_index:g})" if m.series and m.series_index is not None else ""
        who = ", ".join(m.authors) or "?"
        if not m.title:  # AniList answers a series, not a book
            return f"{m.series or '?'} (series) - {who}"
        return f"{m.title}{series} - {who}" + (f" - read by {', '.join(m.narrators)}" if m.narrators else "")
