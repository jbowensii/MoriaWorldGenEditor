#!/usr/bin/env python3
"""extract_data.py — Extract worldgen DataTables from a Return to Moria install.

The editor operates on JSON-form DataTable files (DT_Moria_*.json). Those
JSONs originate from cooked .uasset files shipped inside the game's IoStore
.utoc/.ucas container. This script automates the extraction:

    1. retoc to-legacy --filter "DT_Moria_*"  →  extract uassets from .utoc
    2. UAssetGUI tojson on each extracted uasset  →  produce .json
    3. Copy .json files into ./experiments/worldgen_research/
    4. Also write a .original.json sibling for each (the pristine snapshot
       used by the editor's restore-from-pristine feature)

You must own a copy of Return to Moria and provide its install path.
We never redistribute game content.

Usage:
    python scripts/extract_data.py --rtom-path "C:\\Program Files\\Epic Games\\ReturnToMoria"

Prerequisites:
    - install_tools.py has been run (tools/UAssetGUI and tools/retoc populated)
    - The provided --rtom-path contains Moria/Content/Paks/*.utoc

Options:
    --force                       Re-extract even if outputs already exist
    --staging <dir>               Override the temp staging dir
    --no-original                 Skip writing .original.json copies
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

UASSETGUI = REPO_ROOT / "tools" / "UAssetGUI" / "UAssetGUI.exe"
RETOC = REPO_ROOT / "tools" / "retoc" / "bin" / "retoc.exe"
WORLDGEN_DIR = REPO_ROOT / "experiments" / "worldgen_research"

# UAssetGUI/retoc version strings for UE4.27 (Return to Moria's engine version)
UASSETGUI_VERSION = "VER_UE4_27"
RETOC_VERSION = "UE4_27"

# DataTables the editor reads/writes
DATATABLES = [
    "DT_Moria_Zones",
    "DT_Moria_Chapters",
    "DT_Moria_Biomes",
    "DT_Moria_ZoneDeck",
    "DT_Moria_ZoneBubbleFilters",
    "DT_Moria_Landmarks",
    "DT_Moria_LayoutConnections",
    "DT_Moria_ZoneTemplates",
    "DT_Moria_AdditiveZonePass",
]


def find_utoc(rtom_path: Path) -> Path:
    """Locate the main IoStore container inside an RtoM install."""
    paks = rtom_path / "Moria" / "Content" / "Paks"
    if not paks.is_dir():
        raise FileNotFoundError(
            f"Expected Moria\\Content\\Paks under {rtom_path}, not found.\n"
            "Make sure --rtom-path points at the RtoM install root\n"
            "(typically C:\\Program Files\\Epic Games\\ReturnToMoria)."
        )
    # Find the main .utoc — usually Moria-WindowsNoEditor.utoc but might differ per build
    utocs = sorted(paks.glob("*.utoc"))
    if not utocs:
        raise FileNotFoundError(f"No .utoc files found in {paks}")
    # Prefer the largest (main content), skip small mod paks
    utocs.sort(key=lambda p: p.stat().st_size, reverse=True)
    return utocs[0]


def run_retoc_extract(utoc: Path, output_dir: Path) -> None:
    """Run retoc to-legacy with a filter for DT_Moria_* assets."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(RETOC), "to-legacy",
        "--filter", "DT_Moria_",
        "--version", RETOC_VERSION,
        "--no-shaders",
        str(utoc),
        str(output_dir),
    ]
    print(f"\n[retoc] Extracting DT_Moria_* assets from {utoc.name}")
    print(f"        Output: {output_dir}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"[retoc] FAILED (exit={result.returncode})")
        if result.stderr:
            print(f"[retoc] stderr (last 1KB):\n{result.stderr[-1000:]}")
        raise RuntimeError("retoc to-legacy failed")
    if result.stdout:
        # Just last few lines for sanity
        tail = result.stdout.strip().splitlines()[-5:]
        for line in tail:
            print(f"[retoc] {line}")


def run_uassetgui_tojson(uasset: Path, json_out: Path) -> None:
    """Convert a single .uasset to UAssetAPI JSON."""
    cmd = [str(UASSETGUI), "tojson", str(uasset), str(json_out), UASSETGUI_VERSION]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"  [tojson FAIL] {uasset.name} (exit={result.returncode})")
        if result.stderr:
            print(f"    stderr: {result.stderr[:500]}")
        return False
    return True


def find_extracted_uasset(staging_dir: Path, stem: str) -> Path | None:
    """Find a specific DataTable's .uasset under the staging directory."""
    matches = list(staging_dir.rglob(f"{stem}.uasset"))
    if not matches:
        return None
    return matches[0]  # take the first if multiple


def check_prerequisites() -> int:
    """Verify tools are installed before doing anything."""
    missing = []
    if not UASSETGUI.exists():
        missing.append(f"UAssetGUI not found at {UASSETGUI}")
    if not RETOC.exists():
        missing.append(f"retoc not found at {RETOC}")
    if missing:
        print("ERROR: prerequisites not met")
        for m in missing:
            print(f"  {m}")
        print("\nRun install_tools.py first:")
        print("  python scripts/install_tools.py")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rtom-path", required=True,
                    help="Path to your Return to Moria install root "
                         '(e.g. "C:\\Program Files\\Epic Games\\ReturnToMoria")')
    ap.add_argument("--force", action="store_true",
                    help="Re-extract even if output JSON files exist")
    ap.add_argument("--staging", default=None,
                    help="Override staging directory (default: temp dir)")
    ap.add_argument("--no-original", action="store_true",
                    help="Skip writing .original.json pristine copies")
    args = ap.parse_args()

    if check_prerequisites() != 0:
        return 1

    rtom = Path(args.rtom_path).resolve()
    if not rtom.is_dir():
        print(f"ERROR: --rtom-path does not exist: {rtom}")
        return 1

    try:
        utoc = find_utoc(rtom)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    print(f"Located game container: {utoc}")

    # Set up staging dir
    cleanup_staging = False
    if args.staging:
        staging = Path(args.staging).resolve()
        staging.mkdir(parents=True, exist_ok=True)
    else:
        staging = Path(tempfile.mkdtemp(prefix="MoriaWGE_extract_"))
        cleanup_staging = True
    print(f"Staging:  {staging}")
    print(f"Output:   {WORLDGEN_DIR}")

    try:
        # Step 1 — extract uassets from the IoStore container
        run_retoc_extract(utoc, staging)

        # Step 2 — convert each DataTable to JSON
        WORLDGEN_DIR.mkdir(parents=True, exist_ok=True)
        print(f"\n[tojson] Converting {len(DATATABLES)} DataTables to JSON")
        produced = 0
        skipped = 0
        for stem in DATATABLES:
            uasset = find_extracted_uasset(staging, stem)
            if uasset is None:
                print(f"  [missing] {stem}.uasset not found in extraction")
                skipped += 1
                continue
            json_path = WORLDGEN_DIR / f"{stem}.json"
            if json_path.exists() and not args.force:
                print(f"  [skip]    {stem}.json already exists (use --force to overwrite)")
                skipped += 1
                continue
            print(f"  [tojson]  {stem}")
            if run_uassetgui_tojson(uasset, json_path):
                produced += 1
                if not args.no_original:
                    # Pristine snapshot for the editor's restore feature
                    original = WORLDGEN_DIR / f"{stem}.original.json"
                    shutil.copy2(json_path, original)

        print(f"\nProduced {produced} JSON file(s), skipped {skipped}.")
        if not args.no_original:
            print(f"Pristine .original.json snapshots also written for each.")

        if produced == 0 and skipped < len(DATATABLES):
            return 2
    finally:
        if cleanup_staging and staging.exists():
            print(f"\nCleaning up staging: {staging}")
            shutil.rmtree(staging, ignore_errors=True)

    print(f"\nExtraction complete. Editor data ready in:")
    print(f"  {WORLDGEN_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
