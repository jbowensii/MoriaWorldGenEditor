# Attributions

The Moria WorldGen Editor would not function without two third-party tools, both
MIT-licensed and fetched at install time by `scripts/install_tools.py`. They are
not redistributed by this repository — `install_tools.py` downloads them fresh
from their upstream GitHub Releases.

Credit and gratitude to the authors of these projects.

## UAssetGUI

- **Project:** [atenfyr/UAssetGUI](https://github.com/atenfyr/UAssetGUI)
- **Author:** atenfyr
- **License:** MIT
- **Role in this project:** Converts Unreal Engine `.uasset` / `.uexp` files
  to and from a round-trippable JSON form. Used as a CLI invocation
  (`UAssetGUI tojson` / `fromjson`) from the editor's build pipeline and
  setup scripts.
- **Acquired by:** `python scripts/install_tools.py`
- **Installed to:** `tools/UAssetGUI/`

UAssetGUI is built on top of the [UAssetAPI](https://github.com/atenfyr/UAssetAPI)
library by the same author. The JSON format this editor reads and writes is
the UAssetAPI format.

## retoc

- **Project:** [trumank/retoc](https://github.com/trumank/retoc)
- **Author:** trumank
- **License:** MIT
- **Role in this project:** IoStore container handling. `retoc to-legacy`
  extracts cooked uassets from the game's `.utoc` / `.ucas` container;
  `retoc to-zen` repackages modified uassets back into an installable
  `_P.{pak,ucas,utoc}` IoStore triplet that drops into
  `Moria\Content\Paks\mods\`.
- **Acquired by:** `python scripts/install_tools.py`
- **Installed to:** `tools/retoc/bin/`

## Tk / Tkinter

The editor's UI is built on Tk via Python's bundled `tkinter` module. Tk is
licensed under a permissive BSD-style license.

## Python standard library

`json`, `pathlib`, `configparser`, `subprocess`, `zipfile`, `urllib`, and
several others. PSF License.

## Game assets

Return to Moria game assets are © [Free Range Games](https://www.freerangegames.com/) /
North Beach Games. This repository never contains game-extracted data;
`scripts/extract_data.py` pulls them from each user's own installed copy of the
game at setup time.

## This editor

Built by John Owens (jbowensii). MIT licensed — see [LICENSE](LICENSE) for
terms.

Development assisted by Claude (Anthropic).
