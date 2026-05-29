#!/usr/bin/env python3
"""install_tools.py — Download UAssetGUI + retoc from their upstream releases.

Run once after cloning. Populates ./tools/ with the two external tools the
editor depends on.

Usage:
    python scripts/install_tools.py
    python scripts/install_tools.py --force    # re-download even if present

Sources:
    UAssetGUI - https://github.com/atenfyr/UAssetGUI (MIT license)
    retoc     - https://github.com/trumank/retoc (MIT license)

Both are redistributable; this script just fetches the latest tagged
release ZIP from each upstream and unpacks into the tools/ directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TOOLS_DIR = REPO_ROOT / "tools"

UASSETGUI_REPO = "atenfyr/UAssetGUI"
RETOC_REPO = "trumank/retoc"

UASSETGUI_DEST = TOOLS_DIR / "UAssetGUI"
RETOC_DEST = TOOLS_DIR / "retoc"


def fetch_latest_release_json(repo: str) -> dict:
    """Hit the GitHub API for a repo's latest release JSON."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "MoriaWorldGenEditor-installer"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_windows_zip(release: dict, repo: str) -> dict:
    """Pick the best Windows-x64 zip asset from a release."""
    assets = release.get("assets", [])
    if not assets:
        raise RuntimeError(f"{repo}: no assets in latest release")

    # Score each asset for Windows-x64 + zip preference
    def score(name: str) -> int:
        n = name.lower()
        s = 0
        if n.endswith(".zip"):
            s += 100
        if "windows" in n or "win" in n or "x86_64" in n or "x64" in n:
            s += 50
        if "pc" in n:
            s += 20
        if "linux" in n or "macos" in n or "darwin" in n or "arm" in n:
            s -= 100
        return s

    ranked = sorted(assets, key=lambda a: score(a["name"]), reverse=True)
    best = ranked[0]
    if not best["name"].lower().endswith(".zip"):
        raise RuntimeError(f"{repo}: no .zip asset in latest release (best was {best['name']})")
    return best


def download(url: str, dest: Path) -> None:
    """Download url to dest path, streaming."""
    print(f"  Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "MoriaWorldGenEditor-installer"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)
    print(f"  Wrote {dest.name} ({dest.stat().st_size // 1024} KB)")


def install_one(repo: str, dest: Path, label: str, force: bool) -> None:
    """Install one tool from its upstream GitHub Releases."""
    print(f"\n[{label}] Installing from {repo}")

    # Look for a marker file to skip if already present
    marker = dest / ".installed"
    if marker.exists() and not force:
        print(f"  Already installed at {dest}. Use --force to redownload.")
        return

    print(f"  Fetching latest release info...")
    release = fetch_latest_release_json(repo)
    tag = release.get("tag_name", "?")
    print(f"  Latest release: {tag}")

    asset = pick_windows_zip(release, repo)
    print(f"  Selected asset: {asset['name']}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_zip = Path(tmp) / asset["name"]
        download(asset["browser_download_url"], tmp_zip)

        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        print(f"  Extracting to {dest}")
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(dest)

    marker.write_text(f"installed from {repo} tag={tag}\n", encoding="utf-8")
    print(f"  Done: {label} @ {tag}")


def verify_install() -> int:
    """Confirm key executables ended up where we expect."""
    expected = [
        TOOLS_DIR / "UAssetGUI" / "UAssetGUI.exe",
        TOOLS_DIR / "retoc" / "bin" / "retoc.exe",
    ]
    missing = [str(p) for p in expected if not p.exists()]
    if missing:
        print("\nWARNING: expected files not found after install:")
        for m in missing:
            print(f"  {m}")
        print("\nThe upstream release layout may differ from what this script expects.")
        print(f"Open {TOOLS_DIR} and check the extracted folder structure.")
        print("You may need to move files into:")
        print("  tools/UAssetGUI/UAssetGUI.exe")
        print("  tools/retoc/bin/retoc.exe")
        return 1
    print("\nAll expected files in place. Install succeeded.")
    for p in expected:
        print(f"  {p}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if tools are already installed")
    args = ap.parse_args()

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    install_one(UASSETGUI_REPO, UASSETGUI_DEST, "UAssetGUI", args.force)
    install_one(RETOC_REPO, RETOC_DEST, "retoc", args.force)

    return verify_install()


if __name__ == "__main__":
    sys.exit(main())
