"""
Build a single Windows GUI exe for local UPD processing.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "standalone_upd" / "process_upd.py"
OUTPUT_DIR = ROOT.parent / "UPD_Parser_OneFile"
BUILD_DIR = ROOT / ".pyinstaller_build"
SPEC_DIR = ROOT / ".pyinstaller_spec"
EXE_NAME = "UPD_Parser"


def pyinstaller_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        EXE_NAME,
        "--paths",
        str(ROOT),
        "--distpath",
        str(OUTPUT_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(SPEC_DIR),
        "--hidden-import",
        "ai_parser",
        "--hidden-import",
        "config",
        "credential_store",
        "--hidden-import",
        "excel_writer",
        "--hidden-import",
        "requests",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "dotenv",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "tkinter",
        "--hidden-import",
        "tkinter.filedialog",
        "--hidden-import",
        "tkinter.messagebox",
        "--hidden-import",
        "tkinter.scrolledtext",
        "--hidden-import",
        "tkinter.simpledialog",
        "--hidden-import",
        "tkinter.ttk",
        str(RUNNER),
    ]


def ensure_pyinstaller() -> None:
    if importlib.util.find_spec("PyInstaller") is not None:
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def clean_output() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build() -> Path:
    ensure_pyinstaller()
    clean_output()
    subprocess.run(pyinstaller_command(), check=True)
    exe_path = OUTPUT_DIR / f"{EXE_NAME}.exe"
    if not exe_path.exists():
        raise FileNotFoundError(f"Exe was not created: {exe_path}")
    return exe_path


def main() -> int:
    exe_path = build()
    print()
    print(f"One-file exe created: {exe_path}")
    print("Run the exe, add photos in the window, and start processing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
