# Advanced — Path Configuration

The standard setup flow uses `install_tools.py` and `extract_data.py` to populate `tools/` and `experiments/worldgen_research/` automatically, and the editor's defaults look in those locations. If you need to override the defaults — for example, you've already got UAssetGUI installed elsewhere or you keep your RtoM extracted data in a separate directory — you have a few options.

## Default layout (set up by the quick-start scripts)

```
MoriaWorldGenEditor/                    (this repo)
├── tools/                              (populated by install_tools.py)
│   ├── UAssetGUI/UAssetGUI.exe
│   └── retoc/bin/retoc.exe
├── experiments/                        (populated by extract_data.py)
│   └── worldgen_research/
│       └── DT_Moria_*.json
├── SandboxZoneEditor.py
└── ...
```

The editor computes:

```python
SCRIPT_DIR = Path(__file__).resolve().parent       # this repo's root
PROJECT_ROOT = SCRIPT_DIR.parent                   # one level UP
UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
```

Note that `PROJECT_ROOT` is **one level above the script**. If the script is at `MoriaWorldGenEditor/SandboxZoneEditor.py`, then `PROJECT_ROOT` is `MoriaWorldGenEditor/`'s parent — *not* the repo root itself. This is a quirk inherited from the editor's origin in `Moria-Replication/scripts/`. The setup scripts work around this by writing into `tools/` and `experiments/` at the repo root, but the editor then needs to walk up one level to find them.

**This means the default setup works if the repo's PARENT directory contains the `tools/` and `experiments/` you want to use.** When `install_tools.py` and `extract_data.py` write at the repo root, the editor will look one level higher and not find them.

## Three options to make the editor find the data

### Option A — Use the setup scripts as-is, then move files

Run `install_tools.py` and `extract_data.py`. They populate `MoriaWorldGenEditor/tools/` and `MoriaWorldGenEditor/experiments/`. Then move those directories one level up:

```powershell
cd C:\Users\johnb\OneDrive\Documents\Projects\MoriaWorldGenEditor
Move-Item tools ..
Move-Item experiments ..
```

So they become siblings of the repo, where the editor expects them.

### Option B — Symlink

Inside the repo, create symbolic links pointing at your `tools/` and `experiments/` locations:

```powershell
# Run as Administrator or in Developer Mode
cd C:\Users\johnb\OneDrive\Documents\Projects\MoriaWorldGenEditor
New-Item -ItemType SymbolicLink -Path tools -Target "C:\path\to\your\tools"
New-Item -ItemType SymbolicLink -Path experiments -Target "C:\path\to\your\experiments"
```

Then edit the editor's `PROJECT_ROOT` constant (see Option C) to point at the repo root instead of one level up.

Note: symlinks are local to your machine — they are not redistributed via git. Anyone cloning the repo will not have your symlinks.

### Option C — Edit the constants

Open `SandboxZoneEditor.py` and find the path constants near the top:

```python
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
```

Replace with absolute paths to your installation. For example:

```python
PROJECT_ROOT = Path(r'C:\Users\johnb\OneDrive\Documents\Projects\MoriaWorldGenEditor')
# OR keep PROJECT_ROOT and override the executables:
UASSETGUI_EXE = Path(r'C:\Tools\UAssetGUI\UAssetGUI.exe')
RETOC_EXE = Path(r'C:\Tools\retoc\bin\retoc.exe')
```

You'll also need to find the references to `experiments/worldgen_research` further down in the file and point them at your data directory.

## Coexisting with Moria-Replication

If you have a `Moria-Replication` checkout that already includes `tools/` and `experiments/worldgen_research/`, you can place this repo as a sibling of its `scripts/` directory and reuse those:

```
Moria-Replication/
├── tools/                    (shared)
├── experiments/              (shared)
└── scripts/

MoriaWorldGenEditor/          (this repo, sibling of scripts/)
├── SandboxZoneEditor.py
└── ...
```

This works out of the box because the editor's `PROJECT_ROOT = SCRIPT_DIR.parent` lands on `Moria-Replication/`.

## Future improvement

A cleaner architecture would be to read tool and data paths from the `.ini` file rather than computing them from the script's location. If you'd like to contribute that, it's about a 20-line edit near the top of `SandboxZoneEditor.py` and a corresponding section in `SandboxZoneEditor.ini`. Open a PR.
