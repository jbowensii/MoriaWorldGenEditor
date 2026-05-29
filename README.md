# Moria WorldGen Editor

A Tkinter-based editor for Return to Moria worldgen DataTables.

Operates on cooked `DT_Moria_*.json` files (zones, chapters, biomes, landmarks, layout connections, zone deck, zone templates, bubble filters), validates against engine constraints, and round-trips through `UAssetGUI fromjson` + `retoc to-zen` to produce installable IoStore mod paks.

## Features

- **Visual editing** of 9 worldgen DataTables across tabbed views
- **Row CRUD** (add / copy / delete) on every data tab
- **Live validation pipeline** — 20+ checks for Z-bounds, unanchored zones, landmark alignment, ChapterID uniqueness, stair conventions, connection endpoints, NameMap completeness, etc.
- **Humanized validator UI** — plain-English error explanations + per-issue auto-fix checkboxes
- **Zone mover** — right-click "Move to chapter…" with snapshot, preflight, conflict resolution, rollback
- **Map view** — XY layout visualization with chapter filter, connection overlay, landmark pins, scale/pan/rotate
- **Build pipeline** — produces a SandboxMod IoStore pak from edited JSON via UAssetGUI + retoc

## Requirements

- Windows 10/11
- Python 3.10+ (stdlib only — no pip installs needed; uses `tkinter`, `json`, `pathlib`, `configparser`, `subprocess`)
- **A Return to Moria install** — you must own a copy. Game-extracted DataTables are not redistributed; you provide them via the extraction script (below).

## Quick start (clone → run)

```powershell
# 1. Clone
git clone https://github.com/jbowensii/MoriaWorldGenEditor.git
cd MoriaWorldGenEditor

# 2. Install the two external tools (UAssetGUI + retoc) into ./tools/
#    They're third-party (MIT-licensed) and fetched from their upstream releases.
python scripts\install_tools.py

# 3. Extract worldgen DataTables from YOUR Return to Moria install
#    (You must own and have RtoM installed. This pulls DT_Moria_* uassets
#    from the game's IoStore container, converts them to JSON, and places
#    them in experiments/worldgen_research/. Nothing is redistributed.)
python scripts\extract_data.py --rtom-path "C:\Program Files\Epic Games\ReturnToMoria"

# 4. Run the editor
python SandboxZoneEditor.py
```

That's it. The editor opens, loads the DataTables, and you're editing.

### Open in Visual Studio or VS Code

- **Visual Studio:** open `MoriaWorldGenEditor.sln`. Requires Python Tools for Visual Studio. Press F5 to run.
- **VS Code:** open the folder. `.vscode/launch.json` is pre-configured — press F5 to run with debugger.

## How the build pipeline works

When you save changes and click "Build mod" in the editor:

1. Editor writes the modified JSON to `experiments/worldgen_research/`
2. Runs `tools/UAssetGUI/UAssetGUI.exe fromjson` on each modified JSON → produces cooked `.uasset` + `.uexp`
3. Stages them under `Moria/Content/Tech/Data/GameWorld/`
4. Runs `tools/retoc/bin/retoc.exe to-zen` to produce a `SandboxMod_P.{pak,ucas,utoc}` IoStore triplet
5. Zips to `~/Downloads/` ready to drop into `Moria/Content/Paks/mods/`

## What gets shipped vs not

This repo ships:
- The editor source (`SandboxZoneEditor.py`, `SandboxZoneEditor.ini`)
- Visual Studio + VS Code project files
- Setup scripts (`scripts/install_tools.py`, `scripts/extract_data.py`)
- Documentation

This repo does **not** ship:
- UAssetGUI / retoc binaries (fetched by `install_tools.py` from upstream)
- `DT_Moria_*.json` game data (extracted by `extract_data.py` from your own RtoM install)

Both omissions are intentional. Tools are fetched fresh to track upstream updates; game data isn't ours to redistribute.

## Releases

- **v2.5.3** — chapter rename + stair conventions + cleanup (current)
- **v2.5.2** — full 14-floor elevator chain + ChapterID renumber + Zones-tab UX
- **v2.5.1** — 14-floor Z-shift + 5-stair architecture + Lv-4 fix
- **v2.5.0** — row CRUD on every data tab + humanized validator UX
- **v2.0.0** — 14-chapter SandboxSmall expansion + validation pipeline

See [Releases](https://github.com/jbowensii/MoriaWorldGenEditor/releases) for full per-version notes, or `git log v2.5.3` etc.

## Documentation

- [`docs/PATHS_SETUP.md`](docs/PATHS_SETUP.md) — alternate ways to point the editor at tools and data if you don't want the default sibling layout

## Troubleshooting

**`extract_data.py` says "No .utoc files found"** — your RtoM install isn't where you said it was. Check `--rtom-path` points at the install ROOT (the directory containing `Moria/Content/Paks/`).

**`install_tools.py` fails partway through** — likely a network hiccup. Re-run with `--force` to redownload.

**Editor says it can't find UAssetGUI / retoc** — `install_tools.py` didn't finish, or the upstream release ZIP layout changed. Inspect `tools/UAssetGUI/` and `tools/retoc/bin/` and confirm `UAssetGUI.exe` and `retoc.exe` are present at those paths.

**`extract_data.py` runs but produces zero JSON files** — the filter pattern didn't match anything. Likely a RtoM update changed the cooked filename layout. Open an issue or run `tools/retoc/bin/retoc.exe list <utoc>` to see what's in the container.

## License

Editor source: personal modding tool. No warranty. Use against backups of your DT files.

Third-party tools downloaded by `install_tools.py` are MIT-licensed (see their respective LICENSE files in `tools/UAssetGUI/` and `tools/retoc/` after running the installer).

Return to Moria game assets are © Free Range Games / North Beach Games and are not redistributed by this repository.

## Credits

Built by John Owens (jbowensii) in collaboration with Claude.
