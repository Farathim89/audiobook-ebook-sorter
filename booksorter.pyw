"""Entry point for the packaged BookSorter.exe.

    BookSorter.exe                 the window
    BookSorter.exe --auto [...]    any CLI command without a window (e.g. from Task Scheduler);
                                   output goes to <app data>\\logs\\cli-<date>.log
"""
import sys
import time


def main() -> int:
    if len(sys.argv) > 1:
        from app.cli import main as cli
        from app.config import load_folders
        if sys.stdout is None:  # windowed exe: no console to print to
            logs = load_folders().state / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            sys.stdout = sys.stderr = open(logs / f"cli-{time.strftime('%Y%m%d')}.log", "a", encoding="utf-8")
            print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  {' '.join(sys.argv[1:])}")
        return cli(sys.argv[1:] + (["--yes"] if "--apply" in sys.argv else []))
    from app.ui.main_window import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
