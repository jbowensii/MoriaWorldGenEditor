# Advanced — Path Configuration

The standard setup flow uses `install_tools.py` and `extract_data.py` to populate `tools/` and `experiments/worldgen_research/` automatically, and the editor finds them there with no extra steps. This document covers how that resolution works and how to override it if your tools or data live somewhere else.

## Default layout (set up by the quick-start scripts)

```
MoriaWorldGenEditor/                    (this repo = PROJECT_ROOT)
├── tools/                              (populated by install_tools.py)
│   ├── UAssetGUI/UAssetGUI.exe
│   └── retoc/bin/retoc.exe
├── experiments/                        (populated by extract_data.py)
│   └── worldgen_research/
│       └── DT_Moria_*.json
├── SandboxZoneEditor.py
└── ...
```

`install_tools.py` and `extract_data.py` write `tools/` and `experiments/` **at the repo root**, and the editor looks for them **at the repo root** too. So a fresh `clone → install_tools → extract_data → run` works with no manual moves.

## How the editor resolves paths

At startup the editor determines `PROJECT_ROOT` — the directory that should contain `tools/` and `experiments/` — in this order:

1. **`[paths] project_root` in `SandboxZoneEditor.ini`** — an explicit absolute override (see below).
2. **The repo root itself** — where the setup scripts write. This is the default.
3. **The repo root's parent** — the legacy `Moria-Replication` sibling layout (see bottom). Auto-detected only if `experiments/worldgen_research/` or `tools/` actually exists one level up.

If none of those locate existing data, it falls back to the repo root, so a first run before extraction still points at the right place once you run the scripts.

When frozen into a single `.exe` (PyInstaller), `PROJECT_ROOT` anchors to the **folder containing the `.exe`**, not the temporary unpack directory — so drop the `.exe` next to your `tools/` and `experiments/` folders.

```python
# Simplified — see SandboxZoneEditor.py for the full resolver
SCRIPT_DIR = exe-folder if frozen else folder-of-this-script
PROJECT_ROOT = ini override  ||  repo root  ||  repo-root parent (if it has the data)
UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE     = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
WGR_DIR       = PROJECT_ROOT / 'experiments' / 'worldgen_research'
```

## Overriding paths via the `.ini`

Add a `[paths]` section to `SandboxZoneEditor.ini` (next to `SandboxZoneEditor.py`, or next to the `.exe`). Any key you omit keeps its default. All values may use `~` for your home directory.

```ini
[paths]
; Point the whole layout at a different root (tools/ + experiments/ under it):
project_root = C:\Tools\MoriaWorldGen

; ...or override individual targets (these win over project_root):
uassetgui_exe = C:\Tools\UAssetGUI\UAssetGUI.exe
retoc_exe     = C:\Tools\retoc\bin\retoc.exe
worldgen_dir  = D:\RtoM\extracted\worldgen_research
```

This is the recommended way to relocate things — it survives `git pull` (the editor reads it at runtime; no source edits) and keeps per-machine paths out of the tracked code.

## Coexisting with Moria-Replication (legacy sibling layout)

The editor originated in `Moria-Replication/scripts/`, where `tools/` and `experiments/` lived one directory **above** the script. That layout is still auto-detected: if you place this repo as a sibling of `Moria-Replication/scripts/` and the shared `tools/` / `experiments/` exist at `Moria-Replication/`, the editor finds them via resolution step 3.

```
Moria-Replication/
├── tools/                    (shared)
├── experiments/              (shared)
└── scripts/

MoriaWorldGenEditor/          (this repo, sibling of scripts/)
├── SandboxZoneEditor.py
└── ...
```

No configuration needed — but you can always pin it explicitly with `[paths] project_root` if you prefer.
