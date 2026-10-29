"""No control characters in the source: a '\b' typed into a normal (not raw) string becomes a backspace, and the
regex it was meant for never matches ('_VOL_TITLE_RE' did that for a while)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_control_characters_in_the_source():
    bad = []
    for f in [*ROOT.joinpath("app").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py"), ROOT / "shelfkeeper.pyw"]:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if any(ord(c) < 32 and c != "\t" for c in line):
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert not bad, bad
