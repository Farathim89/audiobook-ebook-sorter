"""Every module compiles - PyInstaller silently leaves out a module it can't parse (and the whole UI with it)."""
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_module_compiles():
    files = [*ROOT.joinpath("app").rglob("*.py"), ROOT / "shelfkeeper.pyw"]
    for f in files:
        py_compile.compile(str(f), doraise=True, cfile=str(Path(py_compile.importlib.util.cache_from_source(str(f)))))
    assert len(files) > 30


def test_the_main_window_imports():
    import app.ui.main_window  # noqa: F401
