"""Ask an AI about the books the rules can't decide (CHECK, a shop that disagrees, swapped roles...).

Two kinds, both plain HTTP (no extra packages):
    Ollama  - a model on this PC (free, private): http://localhost:11434, e.g. qwen2.5:14b
    Claude  - Anthropic's API with your own key (claude-haiku-4-5: about $0.001 a book)

The AI only sees text: what the book's own files say (metadata files, tags, book info, file and folder
names) and the shop matches - never the audio. Its answer is checked: every author / narrator it names
must appear in that text, so it can't invent a person. A sure answer (Settings: confidence) is used
right away, marked 'ai' with its reason in the notes; a less sure one waits in the book's panel for
Accept. Answers are cached, so a second scan asks nothing.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .models import Book, BookType, Status
from .rules import KINDS, book_kind, set_kind
from .text import fold

log = logging.getLogger(__name__)
_LOCK = threading.Lock()

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_PRICE = (1.0, 5.0)  # USD per million tokens in / out (Haiku 4.5)

SCHEMA = {
    "type": "object",
    "properties": {
        "authors": {"type": "array", "items": {"type": "string"},
                    "description": "the writer(s) only - no narrator, translator or illustrator"},
        "narrators": {"type": "array", "items": {"type": "string"}, "description": "the reader(s); [] for an ebook"},
        "series": {"type": "string", "description": "the series name, '' if none"},
        "series_index": {"type": "number", "description": "the volume number in the series, 0 if none"},
        "title": {"type": "string", "description": "the book's title"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "confidence": {"type": "number", "description": "0 to 1: how sure, given the evidence"},
        "reason": {"type": "string", "description": "one short sentence: which source settled it"},
    },
    "required": ["authors", "narrators", "series", "series_index", "title", "kind", "confidence", "reason"],
}

SYSTEM = """You fix the metadata of downloaded audiobooks and ebooks before they go into an Audiobookshelf library.
You get what each source says about ONE book: its metadata files (often metadata.json from another tool - it
sometimes swaps author and narrator), the audio/ebook tags (uploaders put junk there: release group names,
the series as the author, the author as the narrator), a 'book info.txt' copy of the shop page (usually right
about who wrote and who read it), file and folder names, and online shop matches (Audible, AniList...;
'sure' means title, author and length matched).

Decide the real values:
- authors: the writer(s) only. Never the narrator, translator, illustrator or a release group. Use the
  English edition's Latin spelling and name order as the shop page / Audible writes it ('Shoji Goji').
  A one-word pen name written twice ('Twoony Twoony') is one name ('Twoony').
- narrators: the reader(s) of THIS recording ([] for an ebook). A full cast in the files beats a shop match
  that lists someone else (that is another recording).
- series / series_index: the series as the shop names it, without '(Light Novel)' or similar tags; '' and 0
  for a standalone book. Don't invent a series from the title alone.
- title: the book's own title. When every volume is just named after the series, use '<Series>, Vol. N'.
  Keep a volume's own title as it is ('Silvers', 'Rascal Does Not Dream of a Sister Home Alone').
- kind: 'light novel' for Japanese/Chinese/Korean novels and web novels (Yen Press, J-Novel Club, Seven Seas,
  Airship, Tantor's light novel lines...), even as audiobooks; 'audiobook' for other audio; 'ebook' for other
  text; 'manga' or 'comic' for comics.
Only use names that appear in the evidence. If the sources disagree and nothing settles it, give your best
guess with a low confidence (below 0.6). Answer with the JSON object only."""


def make_ai(folders) -> "AiHelper | None":
    mode = getattr(folders, "ai_mode", "off")
    if mode == "ollama":
        client = OllamaClient(folders.ai_url or "http://localhost:11434", folders.ai_model or "qwen2.5:14b")
    elif mode == "claude" and getattr(folders, "ai_api_key", ""):
        client = ClaudeClient(folders.ai_api_key, folders.ai_claude_model or "claude-haiku-4-5-20251001")
    else:
        return None
    return AiHelper(client, folders.state, auto_apply=folders.ai_auto_apply,
                    min_confidence=folders.ai_min_confidence / 100)


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                 {"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - the user's own AI address
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise OSError(f"{e.code} {e.reason}: {detail}") from e
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from e
    except (ValueError, http.client.HTTPException) as e:  # not JSON / cut off: no answer, not a crash
        raise OSError(f"bad answer from the AI ({type(e).__name__})") from e


class OllamaClient:
    name = "Ollama"

    def __init__(self, url: str, model: str):
        self.url, self.model = url.rstrip("/"), model

    def ask(self, system: str, user: str) -> tuple[dict, float]:
        data = _post(f"{self.url}/api/chat", {
            "model": self.model, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }, {}, timeout=300)  # the first call loads the model into the GPU
        try:
            answer = json.loads(data["message"]["content"])
        except (KeyError, TypeError, ValueError) as e:  # an odd reply is no answer, not a crash of the lookup
            raise OSError(f"Ollama gave no usable answer ({type(e).__name__})") from e
        if not isinstance(answer, dict):
            raise OSError("Ollama gave no usable answer")
        return answer, 0.0

    def test(self) -> str:
        req = urllib.request.Request(f"{self.url}/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
                models = [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise OSError(f"Ollama is not answering at {self.url} ({e}). Is it installed and running?") from e
        if not any(m == self.model or m.split(":")[0] == self.model for m in models):
            raise OSError(f"Ollama runs, but the model '{self.model}' is missing. Get it with:\n"
                          f"ollama pull {self.model}\n\nModels you have: {', '.join(models) or 'none'}")
        return f"Ollama is running with {self.model}."


class ClaudeClient:
    name = "Claude"

    def __init__(self, key: str, model: str):
        self.key, self.model = key, model

    def ask(self, system: str, user: str) -> tuple[dict, float]:
        data = _post(CLAUDE_URL, {
            "model": self.model, "max_tokens": 700, "temperature": 0, "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "answer", "description": "The book's real metadata.", "input_schema": SCHEMA}],
            "tool_choice": {"type": "tool", "name": "answer"},
        }, {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, timeout=90)
        usage = data.get("usage") or {}
        cost = (usage.get("input_tokens", 0) * CLAUDE_PRICE[0] + usage.get("output_tokens", 0) * CLAUDE_PRICE[1]) / 1e6
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if block is None:
            raise OSError("Claude gave no answer")
        return block.get("input") or {}, cost

    def test(self) -> str:
        answer, cost = self.ask("Answer with the JSON object only.",
                                "Book: 'Silvers' by Brian J. Nordon, read by Daniel Wisniewski, Quest Academy #1. "
                                "Kind: audiobook. Confidence 1, reason 'test'.")
        return f"Claude answered ({self.model}, ${cost:.4f}): {answer.get('title')} by {', '.join(answer.get('authors', []))}"


class AiHelper:
    def __init__(self, client, state: Path, auto_apply: bool = True, min_confidence: float = 0.85):
        self.client = client
        self.state = Path(state)
        self.auto_apply = auto_apply
        self.min_confidence = min_confidence
        self.cache_path = self.state / "ai_cache.json"
        self.usage_path = self.state / "ai_usage.json"
        self.failed = ""
        self.rules = None  # your earlier fixes (core.rules): the ones about this book go along as hints

    @staticmethod
    def wanted(book: Book) -> bool:
        """Only the unsure ones: not READY, a shop that disagrees - and never what you set by hand."""
        if book.sources.get("authors") == "manual" or book.sources.get("title") == "manual":
            return False
        return book.status is not Status.READY or bool(book.doubt)

    def run(self, books: list[Book], cancelled: Callable[[], bool] = lambda: False,
            on_progress: Callable[[int, int, Book], None] | None = None) -> int:
        todo = [b for b in books if self.wanted(b)]
        used = 0
        self.failed = ""  # each lookup tries again (Ollama may have started meanwhile)
        for i, book in enumerate(todo, 1):
            if cancelled() or self.failed:
                break
            try:
                used += self.ask_and_use(book)
            except OSError as e:
                self.failed = f"AI ({self.client.name}) not reachable: {e}"
                book.notes.append(self.failed)
                log.warning(self.failed)
            if on_progress:
                on_progress(i, len(todo), book)
        return used

    def ask_and_use(self, book: Book) -> bool:
        evidence = self.evidence(book)
        user = json.dumps(evidence, ensure_ascii=False, indent=1)
        key = hashlib.sha1(f"{self.client.name}|{getattr(self.client, 'model', '')}|{user}".encode()).hexdigest()
        cache = self._read(self.cache_path)
        answer = cache.get(key)
        if answer is None:
            answer, cost = self.client.ask(SYSTEM, "The book:\n" + user)
            with _LOCK:
                cache = self._read(self.cache_path)
                cache[key] = answer
                self._write(self.cache_path, cache)
                usage = self._read(self.usage_path)
                usage["calls"] = usage.get("calls", 0) + 1
                usage["cost_usd"] = round(usage.get("cost_usd", 0.0) + cost, 6)
                self._write(self.usage_path, usage)
        answer = check_answer(book, answer, user)
        book.ai = answer
        if answer.get("rejected"):
            book.notes.append(f"🤖 AI answer not used: {answer['rejected']}")
            return False
        sure = float(answer.get("confidence") or 0) >= self.min_confidence
        if self.auto_apply and sure:
            use_answer(book, answer)
            return True
        book.notes.append(f"🤖 AI suggests (not used, {answer.get('confidence', 0):.0%} sure): "
                          f"{summary(answer)} - {answer.get('reason', '')}")
        return False

    def evidence(self, book: Book) -> dict:
        out = self._evidence(book)
        if self.rules is not None and self.rules.items:
            text = fold(json.dumps(out, ensure_ascii=False))
            mine = [r.describe() for r in self.rules.items if fold(r.match) and fold(r.match) in text]
            if mine:
                out["the user's earlier fixes (follow them)"] = mine
        return out

    @staticmethod
    def _evidence(book: Book) -> dict:
        from .infolog import what_was_decided, what_was_read  # noqa: PLC0415
        decided = what_was_decided(book)
        ours = 0.0
        if book.type is BookType.AUDIO:
            from .online import book_length  # noqa: PLC0415
            try:
                ours = book_length(book)
            except Exception:  # noqa: BLE001 - no length: no length check
                ours = 0.0
        files = [str(f.relative_to(book.source_root)) if book.source_root in f.parents else f.name
                 for f in book.files[:3]]
        out = {
            "file type": book.type.name.lower(),
            "files": files + (["..."] if len(book.files) > 3 else []),
            "sources": what_was_read(book),
            "current guess": {k: decided.get(k) for k in ("values", "kind", "notes", "doubt") if decided.get(k)},
            "shop matches": [{
                "shop": c.provider, "sure": c.confident, "title": c.meta.title, "subtitle": c.meta.subtitle,
                "authors": c.meta.authors, "series": c.meta.series,
                "number": c.meta.series_index, "publisher": c.meta.publisher,
                "hours": round(c.length / 3600, 1) if getattr(c, "length", None) else None,
                # a match of another length is another recording (Andy Serkis' 22 h Fellowship for a 19 h
                # file read by Rob Inglis): its readers are not this book's
                **({"narrators": c.meta.narrators} if _same_recording(c, ours) else
                   {"note": "another recording (other length) - its narrators are not this book's"}),
            } for c in book.candidates[:3]],
        }
        if ours:
            out["this book's length (hours)"] = round(ours / 3600, 1)
        out["current guess"].get("values", {}).pop("cover_url", None)
        return out

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _same_recording(cand, ours: float) -> bool:
    """No length known on either side: maybe. Else within 3 % (like the shop match itself)."""
    from .online import LENGTH_TOLERANCE  # noqa: PLC0415
    length = getattr(cand, "length", None)
    return not (length and ours) or abs(length - ours) / ours <= LENGTH_TOLERANCE


def usage(state: Path) -> dict:
    return AiHelper._read(Path(state) / "ai_usage.json")


def _name_key(name: str) -> str:
    from ..readers.names import single_name  # noqa: PLC0415
    return fold(single_name(name))


def _words(text: str) -> str:
    """' kumo kagyu ' - words between spaces, so a name only counts as a whole name, not as letters that
    happen to run together across two fields."""
    return " " + " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split()) + " "


def _in_evidence(name: str, words: str) -> bool:
    from ..readers.names import single_name  # noqa: PLC0415
    return any(_words(n).strip() and _words(n) in words for n in {name, single_name(name)})


def check_answer(book: Book, answer: dict, evidence: str) -> dict:
    """Keep the AI honest: a person must appear in the evidence; a kind must suit the file type."""
    a = dict(answer or {})
    words = _words(evidence)
    authors = [str(x).strip() for x in a.get("authors") or [] if str(x).strip()]
    unknown = [x for x in authors if not _in_evidence(x, words)]
    if not authors:
        a["rejected"] = "no author"
    elif unknown:
        a["rejected"] = f"names an author found in no source ({', '.join(unknown)})"
    if len(authors) > 1:  # a co-author only when the book already had them (else the illustrator, 'Keji Mizoguchi')
        mine = {_name_key(x) for x in book.meta.authors}
        authors = authors[:1] + [x for x in authors[1:] if _name_key(x) in mine]
    a["authors"] = authors
    a["narrators"] = [str(x).strip() for x in a.get("narrators") or []
                      if str(x).strip() and _in_evidence(str(x), words)]
    # a shop's placeholder is nobody: 'Narrator Info Added Soon'
    a["narrators"] = [n for n in a["narrators"] if not re.search(r"(?i)\b(?:info|added|soon|unknown|tba|n/a)\b", n)]
    if book.type is not BookType.AUDIO:
        a["narrators"] = []
    kind = str(a.get("kind") or "")
    allowed = ("manga", "comic") if book.type is BookType.COMIC else (
        ("light novel", "audiobook") if book.type is BookType.AUDIO else ("light novel", "ebook"))
    a["kind"] = kind if kind in allowed else book_kind(book)
    try:
        a["series_index"] = float(a.get("series_index") or 0) or None
    except (TypeError, ValueError):
        a["series_index"] = None
    a["series"] = str(a.get("series") or "").strip() or None
    if a["series"] is None:
        a["series_index"] = None
    a["title"] = str(a.get("title") or "").strip() or book.meta.title
    try:
        a["confidence"] = max(0.0, min(1.0, float(a.get("confidence") or 0)))
    except (TypeError, ValueError):
        a["confidence"] = 0.0
    return a


def summary(answer: dict) -> str:
    from .metadata import format_index  # noqa: PLC0415
    s = f"'{answer.get('title')}' by {', '.join(answer.get('authors') or [])}"
    if answer.get("narrators"):
        s += f", read by {', '.join(answer['narrators'])}"
    if answer.get("series"):
        idx = answer.get("series_index")
        s += f", {answer['series']}" + (f" #{format_index(idx)}" if idx else "")
    return s + f" ({answer.get('kind')})"


def use_answer(book: Book, answer: dict) -> None:
    """Put the AI's answer on the book (not over fields you set by hand); Accept in the panel does the same."""
    from .metadata import update_status  # noqa: PLC0415
    m = book.meta
    for name in ("authors", "narrators", "series", "series_index", "title"):
        value = answer.get(name)
        if book.sources.get(name) == "manual" or (name == "narrators" and not value):
            continue
        if value in (None, "", []) and name in ("authors", "title"):
            continue
        changed = getattr(m, name) != value
        if changed:
            setattr(m, name, list(value) if isinstance(value, list) else value)
        if changed or value not in (None, "", []):
            book.sources[name] = "ai"  # also when it only confirmed a guess, or said 'no series'
    kind = str(answer.get("kind") or book_kind(book))
    # the AI may move a book INTO Light Novels / Manga, never out: it calls light-novel ebooks just 'ebook',
    # while AniList, the publisher list and book info are sure about those
    if not ((book.light_novel and kind in ("audiobook", "ebook")) or (book.manga and kind == "comic")):
        set_kind(book, kind)
    if book.type is BookType.COMIC:
        book.kind_known = True
    book.doubt = ""  # the AI looked at the disagreement and decided
    book.notes = [n for n in book.notes if not n.startswith("🤖")]
    book.notes.append(f"🤖 AI ({answer.get('confidence', 0):.0%} sure): {answer.get('reason', '')}")
    update_status(book)
