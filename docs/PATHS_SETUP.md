# Paths Setup

The editor hard-codes paths to external tools and data relative to a computed `PROJECT_ROOT`. By default:

```python
SCRIPT_DIR = Path(__file__).resolve().parent       # this repo's root
PROJECT_ROOT = SCRIPT_DIR.parent                   # one level UP
UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
```

This means out-of-the-box, the editor expects to live as a sibling to `tools/` and `experiments/` directories — typical of the `Moria-Replication` repo layout this editor originated from.

## Three setup options

### Option A — Sibling layout (matches editor defaults)

Put this repo next to a `tools/` directory containing UAssetGUI + retoc:

```
ParentDir/
├── MoriaWorldGenEditor/        (this repo)
│   ├── SandboxZoneEditor.py
│   └── SandboxZoneEditor.ini
├── tools/
│   ├── UAssetGUI/UAssetGUI.exe
│   └── retoc/bin/retoc.exe
└── experiments/
    └── worldgen_research/
        └── DT_Moria_*.json
```

Then `PROJECT_ROOT` (= one level above the script) lands on `ParentDir/` and the paths resolve correctly.

### Option B — Symlinks

Inside this repo, create symbolic links pointing at your tool/data locations:

```bash
# from inside MoriaWorldGenEditor/
mklink /D tools "C:\path\to\your\tools"
mklink /D experiments "C:\path\to\your\experiments"
```

(Use `ln -s` on Linux/macOS.) The editor's `PROJECT_ROOT = SCRIPT_DIR.parent` won't find them via that path, so you'd ALSO need to adjust the script's `PROJECT_ROOT` constant (next option).

### Option C — Edit the constants

Open `SandboxZoneEditor.py`, find the path constants near the top:

```python
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
```

Replace with absolute paths to your installation:

```python
UASSETGUI_EXE = Path(r'C:\YourTools\UAssetGUI\UAssetGUI.exe')
RETOC_EXE = Path(r'C:\YourTools\retoc\bin\retoc.exe')
WORLDGEN_DIR = Path(r'C:\YourData\worldgen_research')
```

You'll also need to find any reference to `experiments/worldgen_research` further down in the file and point those at your data directory.

## Recommended

Option A is simplest if you already have a `Moria-Replication` checkout. Place this repo as a sibling to its `scripts/` directory and it Just Works.

For standalone use, Option C is most explicit — easier to debug if paths don't resolve.

## Getting the DT files

The `DT_Moria_*.json` files come from running `UAssetGUI tojson` against the cooked game uassets in `Moria/Content/Tech/Data/GameWorld/`. See the parent `Moria-Replication` project for the extraction pipeline.
