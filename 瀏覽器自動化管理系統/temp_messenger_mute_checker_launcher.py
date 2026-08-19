"""ASCII-only launcher for the temporary Messenger mute checker."""

from __future__ import annotations

from pathlib import Path
import py_compile
import runpy
import sys


BASE_DIR = Path(__file__).resolve().parent
TARGET_NAME = "\u81e8\u6642_\u804a\u5929\u5ba4\u7981\u8a00\u6aa2\u67e5.py"
TARGET = BASE_DIR / TARGET_NAME


def main() -> None:
    if not TARGET.is_file():
        raise FileNotFoundError(f"Temporary checker not found: {TARGET}")
    if "--self-test" in sys.argv[1:]:
        py_compile.compile(str(TARGET), doraise=True)
        print("launcher self-test passed")
        return
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
