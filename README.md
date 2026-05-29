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

- Python 3.10+ (uses only stdlib: `tkinter`, `json`, `pathlib`, `configparser`, `subprocess`, etc. — no pip installs needed)
- **UAssetGUI** — external tool, must be installed separately
- **retoc** — external tool, must be installed separately
- Vanilla `DT_Moria_*.json` source files extracted from a Return to Moria install

## Setup

The editor expects a specific directory layout for external tools and data files. By default it computes a `PROJECT_ROOT` two levels up from the script and looks for:

```
PROJECT_ROOT/
├── tools/
│   ├── UAssetGUI/UAssetGUI.exe
│   └── retoc/bin/retoc.exe
└── experiments/worldgen_research/
    ├── DT_Moria_Zones.json
    ├── DT_Moria_Chapters.json
    ├── DT_Moria_Biomes.json
    ├── DT_Moria_ZoneDeck.json
    ├── DT_Moria_ZoneBubbleFilters.json
    ├── DT_Moria_Landmarks.json
    ├── DT_Moria_LayoutConnections.json
    └── DT_Moria_ZoneTemplates.json
```

See [`docs/PATHS_SETUP.md`](docs/PATHS_SETUP.md) for instructions on configuring paths if your install differs.

## Running

```bash
python SandboxZoneEditor.py
```

Or open `MoriaWorldGenEditor.sln` in Visual Studio and press F5.

## Releases

- **v2.5.3** — chapter rename + stair conventions + cleanup (current)
- **v2.5.2** — full 14-floor elevator chain + ChapterID renumber + Zones-tab UX
- **v2.5.1** — 14-floor Z-shift + 5-stair architecture + Lv-4 fix
- **v2.5.0** — row CRUD on every data tab + humanized validator UX
- **v2.0.0** — 14-chapter SandboxSmall expansion + validation pipeline

See `git log` for full release notes per tag.

## License

Personal modding tool. No warranty. Use at your own risk against backups of your DT files.

## Credits

Built by John Owens (jbowensii) in collaboration with Claude.
