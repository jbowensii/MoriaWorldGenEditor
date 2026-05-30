#!/usr/bin/env python3
"""build_exe.py — Freeze the editor into a single-file Windows executable.

Produces a standalone `SandboxZoneEditor.exe` (PyInstaller onefile, windowed)
that runs without a Python install. The exe still needs `tools/` and
`experiments/worldgen_research/` next to it -- run install_tools.py and
extract_data.py, then drop the exe alongside them (the editor anchors paths to
the exe's own folder when frozen).

Usage:
    python scripts/build_exe.py
    python scripts/build_exe.py --clean    # wipe build/ and dist/ first

Output:
    release/SandboxZoneEditor.exe

Requires PyInstaller (`pip install pyinstaller`).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

ENTRY = REPO_ROOT / "SandboxZoneEditor.py"
APP_NAME = "SandboxZoneEditor"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
RELEASE_DIR = REPO_ROOT / "release"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clean", action="store_true",
                    help="Remove build/ and dist/ before building")
    args = ap.parse_args()

    if not ENTRY.exists():
        print(f"ERROR: entry point not found: {ENTRY}")
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ERROR: PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    if args.clean:
        for d in (BUILD_DIR, DIST_DIR):
            if d.exists():
                print(f"Removing {d}")
                shutil.rmtree(d, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",          # GUI app: no console window
        "--name", APP_NAME,
        "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
        str(ENTRY),
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"\nPyInstaller failed (exit={result.returncode})")
        return result.returncode

    built = DIST_DIR / f"{APP_NAME}.exe"
    if not built.exists():
        print(f"\nERROR: expected output not found: {built}")
        return 1

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    final = RELEASE_DIR / f"{APP_NAME}.exe"
    shutil.copy2(built, final)
    size_mb = final.stat().st_size / (1024 * 1024)
    print("\nBuild succeeded:")
    print(f"  {final}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
