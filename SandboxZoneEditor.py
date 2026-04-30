"""
SandboxZoneEditor.py — Moria WorldGen Editor (Phase 1 + 2 + 3)
---------------------------------------------------------------
Tkinter GUI for editing Return to Moria's worldgen DataTables.

Tabs:
  - Zones        edit 44 SandboxSmall zones; landmarks now editable
  - Chapters     edit all chapters, add new
  - Biomes       view + edit display-level fields; object refs viewable
  - Bubbles      edit ZoneDeck DeckEntries (bubble, appearances, entrance)
  - Filters      edit ZoneBubbleFilters whitelist/blacklist per filter row
  - Landmarks    edit DT_Moria_Landmarks: BaseBubbleName, Placement,
                 GuaranteedConnections (connectivity), flags
  - Map          isometric visualizer of zone layout w/ connection overlay

Build packages ALL loaded DataTables into one IoStore mod pak.
Uninstalling the pak restores original game behavior.
"""

import configparser
import copy
import json
import math
import re
import subprocess
import sys
import tkinter as tk
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

# -----------------------------------------------------------------------------
# Feature flags
# -----------------------------------------------------------------------------
# Drag-and-drop "move zone to chapter" feature on the Zones tab. When enabled,
# the Zones tab Treeview is rendered as a hierarchical tree (chapter parents
# with zone children) and the user can drag any zone row onto another chapter
# parent row to fire the ZoneMover pipeline (snapshot -> pre-flight -> apply
# -> validate -> result popup).
#
# Entry points (defined further down in this file):
#   - ZoneMover            move pipeline implementation
#   - ZoneMoveDialog       conflict-resolution modal (block / expand / shrink)
#   - ZoneMoveResultDialog post-move summary + roll-back
#   - ZoneTab._dnd_*       drag-drop event handlers wired into the Zones tab
#   - ZONE_DRAG_TAG        Treeview tag used to highlight the drop target
#
# Flip ENABLE_ZONE_DRAG_DROP to False to fall back to the original flat
# Treeview behaviour (no grouping, no drag-drop).
#
# Drag-drop turned out to be hard to use when the destination chapter is
# scrolled off-screen, so it ships disabled and the right-click "Move to
# chapter..." flow (ENABLE_ZONE_RIGHT_CLICK_MOVE) is the recommended path.
# All drag-drop code is left in place behind the flag in case the user
# wants to re-enable it.
ENABLE_ZONE_DRAG_DROP = False

# Right-click "Move to chapter..." replacement for drag-drop. Pops a
# ZoneMoveChapterPicker modal listing every valid destination chapter
# (sorted Layer descending, like the level-list skill output) and feeds
# the choice into the existing ZoneMover pipeline. Reuses the same
# ZoneMoveDialog conflict modal and ZoneMoveResultDialog summary popup.
ENABLE_ZONE_RIGHT_CLICK_MOVE = True

# Either flag enables the chapter-grouped Treeview rendering on the Zones
# tab. The grouped view is independently useful (drag-drop or not), so we
# keep it whenever either trigger is wired up.
ENABLE_ZONE_CHAPTER_GROUPING = ENABLE_ZONE_DRAG_DROP or ENABLE_ZONE_RIGHT_CLICK_MOVE

# Treeview tag used to flash a chapter parent row green during drag-over.
ZONE_DRAG_TAG = 'zone-drag-target'
# iid prefix used for chapter parent rows in the Zones tab grouped tree.
# Zone iids are the zone's row name (no prefix), so this prefix can never
# collide with a real zone name.
ZONE_CHAPTER_IID_PREFIX = '__chap__:'

# -----------------------------------------------------------------------------
# Paths & constants
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
WGR_DIR = PROJECT_ROOT / 'experiments' / 'worldgen_research'

UASSETGUI_EXE = PROJECT_ROOT / 'tools' / 'UAssetGUI' / 'UAssetGUI.exe'
RETOC_EXE = PROJECT_ROOT / 'tools' / 'retoc' / 'bin' / 'retoc.exe'
SETTINGS_PATH = SCRIPT_DIR / 'SandboxZoneEditor.ini'
UE_VERSION = 'VER_UE4_27'
RETOC_VERSION = 'UE4_27'

STAGED_REL_DIR = Path('Moria') / 'Content' / 'Tech' / 'Data' / 'GameWorld'
DOWNLOADS_DIR = Path.home() / 'Downloads'
MOD_NAME = 'SandboxMod'
# Version lives in SandboxZoneEditor.ini ([build] mod_version). Format is
# MAJ.MIN.PAT, each segment 0..999. Build auto-bumps patch +1 on success.
# Starting version (first run, if ini has no value yet): 1.0.1.
DEFAULT_MOD_VERSION = '2.5.3'

DATATABLES = {
    'zones':       ('DT_Moria_Zones.json',              'DT_Moria_Zones',              'Zones'),
    'chapters':    ('DT_Moria_Chapters.json',           'DT_Moria_Chapters',           'Chapters'),
    'biomes':      ('DT_Moria_Biomes.json',             'DT_Moria_Biomes',             'Biomes'),
    'decks':       ('DT_Moria_ZoneDeck.json',           'DT_Moria_ZoneDeck',           'ZoneDeck'),
    'filters':     ('DT_Moria_ZoneBubbleFilters.json',  'DT_Moria_ZoneBubbleFilters',  'BubbleFilters'),
    'landmarks':   ('DT_Moria_Landmarks.json',          'DT_Moria_Landmarks',          'Landmarks'),
    'strings':    ('World.json',                       'World',                       'Strings'),
    # No UI tab for these two, but they MUST be in the registry so the build
    # pipeline detects modifications and bundles updated uassets. Otherwise
    # the pak ships a mix of modified Zones/Chapters and pristine
    # LayoutConnections/ZoneTemplates — guaranteed routing crash at
    # FMorLayoutConnectionInstance::GetZone (offset 0x1a1).
    'connections': ('DT_Moria_LayoutConnections.json',  'DT_Moria_LayoutConnections',  'LayoutConnections'),
    'templates':   ('DT_Moria_ZoneTemplates.json',      'DT_Moria_ZoneTemplates',      'ZoneTemplates'),
}

# Staging sub-paths relative to the pak's Moria/Content root. Any doc whose
# key isn't listed here defaults to the GameWorld DataTable path.
STAGED_DIR_OVERRIDES = {
    'strings': Path('Moria') / 'Content' / 'Tech' / 'Data' / 'StringTables',
}

# Per-ChapterID background colour. Tag names are chap-01..chap-17.
# A helper (chapter_color_tag) extracts the ChapterID from any chapter row
# name -- legacy 'SandboxSmall-chapter-N' or new 'SandboxSmall-ChapterNN.<X>'.
CHAPTER_COLORS = {
    'chap-01': '#cfe8ff',
    'chap-02': '#d4f4cf',
    'chap-03': '#fff4b8',
    'chap-04': '#ffd9b3',
    'chap-05': '#ffd1e6',
    'chap-06': '#e0ccff',
    'chap-07': '#ffc9c9',
    'chap-08': '#e0e0e0',
    'chap-09': '#aad4ff',
    'chap-10': '#7ac77a',
    'chap-11': '#ffe066',
    'chap-12': '#ffb380',
    'chap-13': '#ff99cc',
    'chap-14': '#c299ff',
    'chap-15': '#ff7a7a',
    'chap-16': '#ffd1e6',
    'chap-17': '#aad4ff',
}

_CHAP_TAG_RE = re.compile(r'SandboxSmall-[Cc]hapter[-]?(\d+)')

def chapter_color_tag(chapter_name):
    """Return a 'chap-NN' tag for any chapter row name, or None."""
    if not chapter_name:
        return None
    m = _CHAP_TAG_RE.match(chapter_name)
    if m:
        return f'chap-{int(m.group(1)):02d}'
    return None

EXTRA_CHAPTER_OPTIONS = [
    'Moria-DurinTower', 'Moria-DimrillDale', 'Moria-TradingPost',
]

_NATSORT_RE = re.compile(r'(\d+)')

def natural_key(s):
    """Break a string into alternating text/int chunks so that any string with
    embedded digits sorts numerically within its text prefix.
    'SandboxSmall-chapter-2' < 'SandboxSmall-chapter-10' (correct).
    Case-insensitive. Use as `sorted(seq, key=natural_key)`. Safe for non-strings
    — falls back to (1, str(x))."""
    if not isinstance(s, str):
        return (1, str(s))
    parts = _NATSORT_RE.split(s.lower())
    return tuple(int(p) if p.isdigit() else p for p in parts)


VISUAL_MAP_STYLES = ['Urban', 'Cavernous', 'Secret', 'Outside']
# EZoneBubblePlacement values actually used in vanilla DT_Moria_Zones rows.
# Verified by scanning every Placement enum value in pristine data:
#   Fixed: 54x  Interior: 10x  Any: 3x  Center: 1x
# Earlier the list had 'Edge'/'Corner'/'Unspecified' which are NOT real
# enum members — picking those would write a value the engine rejects.
LANDMARK_PLACEMENTS = ['Fixed', 'Center', 'Interior', 'Any']
DECK_APPEARANCES = ['Required', 'Single', 'Multiple']

# Hover-over tooltip text for UI fields.  Keyed by a short identifier; see
# attach_tooltip() to wire to a widget.
TOOLTIPS = {
    'deck_appearance':
        "How often this bubble can appear in the deck:\n"
        "  Required  — MUST appear (mandatory placement)\n"
        "  Single    — may appear, at most 1 per run\n"
        "  Multiple  — may appear 0 or more times (deck filler)",
    'landmark_placement':
        "Where the landmark can spawn inside its zone:\n"
        "  Fixed       — at the landmark row's BasePosition cell\n"
        "  Any         — any valid cell (generator picks)\n"
        "  Interior    — inside zone, not touching boundary\n"
        "  Edge        — must touch zone boundary\n"
        "  Corner      — at a zone-corner cell\n"
        "  Unspecified — no constraint (equivalent to Any)",
    'landmark_row_placement':
        "The landmark's own placement strategy:\n"
        "  Fixed          — place at exact BasePosition coords\n"
        "  Random         — generator picks location within any host zone\n"
        "  RotateAndClamp — place with rotation + boundary clamping",
    'extended_connectivity':
        "Check if this is an elevator/stair spanning multiple chapter layers.\n"
        "Only the 5 vanilla stair landmarks use this (FirstStair…FifthStair).\n"
        "Gets special cross-chapter routing in the generator.",
    'zone_entrance':
        "Check if this landmark is a mandatory gateway into its zone.\n"
        "Player must pass through this face to enter. Rare — only 6 in vanilla.",
    'auto_connections':
        "How the zone requests connections to neighbors:\n"
        "  All    — auto-propose connections to every adjacent zone (default)\n"
        "  None   — no auto-connections; only GuaranteedConnections fire\n"
        "         used by self-contained zones like elevators\n"
        "  Single — exactly ONE auto-proposed connection (single-exit zones)",
    'visual_map_style':
        "The zone's art/aesthetic theme:\n"
        "  Urban     — dwarven cityscape (Dwarrowdelf, Trading Post)\n"
        "  Cavernous — natural cave / stone (Mines, LowerDeeps)\n"
        "  Secret    — hidden / lore-heavy area\n"
        "  Outside   — open-air exterior (Dimrill Dale)",
    'dirt_plug_density':
        "Probability (0.0-1.0) that unused cells in this zone get filled\n"
        "with dirt plug meshes to prevent visible void.\n"
        "  0.0 — no plugs (may show void)\n"
        "  0.6 — vanilla sandbox default\n"
        "  1.0 — every unused cell plugged",
    'dirt_plug_type':
        "Which dirt-plug material family to use:\n"
        "  DirtPlugTier1 — soft dirt, easy to dig through\n"
        "  DirtPlugTier2 — harder dirt, requires better pickaxe",
    'additional_opening':
        "Probability (0.0-1.0) that the generator adds extra openings\n"
        "from landmark bubbles to adjacent deck bubbles.\n"
        "Higher = more interconnected. Vanilla default: 0.75.",
    'target_bubbles':
        "How many bubbles the generator tries to place in this zone.\n"
        "  1       — landmark-only (zone contains one mandatory feature)\n"
        "  3-8     — sandbox deck zones (filler rooms)\n"
        "  higher  — denser zones with more filler content",
    'extend_footprint':
        "When ON, zone can expand into neighboring empty cells if content needs room.\n"
        "When OFF, zone is exactly the declared TargetSize.",
    'parcel_type':
        "How zone position is anchored:\n"
        "  Fixed — position locked at Position field\n"
        "  Free  — can flex/nudge based on neighbor pressure",
    'layer':
        "Vertical position in the chapter stack:\n"
        "   0 = ground floor\n"
        "  +1, +2… = above ground\n"
        "  -1, -2… = below ground (deep levels)",
    'chapter_id':
        "Progression order identifier (integer).\n"
        "Engine uses this to order chapter transitions.\n"
        "Does NOT have to match Layer; vanilla uses non-sequential IDs.",
    'min_z':
        "Floor of the chapter's vertical Z band (lowest Z cell).\n"
        "Zones in this chapter typically have Pos.Z = MinZ.",
    'max_z':
        "Ceiling of the chapter's vertical Z band (highest Z cell).\n"
        "Combined with MinZ defines chapter height = MaxZ-MinZ+1.",
    'prime_z':
        "Reference Z for lighting/fog/camera systems.\n"
        "Usually the midpoint of the chapter's Z band, rounded down.",
    'zone_set':
        "Which gameplay mode this row belongs to:\n"
        "  Moria         — main campaign\n"
        "  SandboxSmall  — sandbox mode (small)\n"
        "  SandboxMedium — unused in shipped game\n"
        "  Expedition    — Expedition mode",
    'enabled_state':
        "Whether this row generates in-game:\n"
        "  Live     — participates in generation\n"
        "  Disabled — row exists but is ignored by the generator",
    'bubble_deck':
        "The ZoneDeck row that supplies this zone's filler 'room' bubbles.\n"
        "Click the deck name to jump to its contents in the Bubbles tab.",
    'passage_deck':
        "The ZoneDeck row that supplies this zone's 'corridor' bubbles\n"
        "(tunnels connecting the filler rooms).",
    'target_size':
        "Zone footprint in grid cells: (X × Y × Z).\n"
        "  Z=1 = single floor\n"
        "  Z=4 = four-floor tall zone (like elevators)",

    # ----- Connections tab tooltips -----
    'conn_filter_zoneset':
        "Filter the row list by which campaign mode each connection applies to. "
        "Choose '(any)' to see them all.",
    'conn_filter_state':
        "Filter by Live / Disabled / Test enabled state. "
        "Choose '(any)' to see them all.",
    'conn_orphans_only':
        "Show only Live connections whose Origin or Destination landmark has no "
        "Live zone holding it. These crash the routing A* with a null deref "
        "(FMorLayoutConnectionInstance::GetZone, offset 0x1a1).",
    'conn_zoneset':
        "Which campaign mode this connection applies to. Moria=story; "
        "SandboxSmall=our 14-chapter sandbox; All=all sets.",
    'conn_state':
        "Live=router considers it. Disabled=loader skips it. "
        "Test=dev only, vanilla skips.",
    'conn_required':
        "If true, the router MUST find a path or world generation fails. "
        "False makes it optional.",
    'conn_exclusive':
        "If true, this connection's A* path edges are reserved for it alone. "
        "False allows sharing with other connections.",
    'conn_leaf':
        "If true, path may terminate at a leaf zone (no further outbound "
        "connections). False requires terminating at a non-leaf.",
    'conn_zonerule':
        "Determines how the path between Origin and Destination is constrained.  "
        "Shared=path may pass through any zone freely (most permissive).  "
        "Chapter=path must stay within a single chapter's scope (strictest — "
        "fails when endpoints span multiple chapters).  "
        "BelongsToOrigin=path owned by the Origin zone.  "
        "BelongsToDestination=path owned by the Destination zone.",
    'conn_origin_kind':
        "LandmarkInterface=connect via a specific landmark's predefined "
        "connection point (most vanilla connections use this). "
        "ZoneInterface=connect to a generic zone face directly.",
    'conn_origin_landmark':
        "Landmark whose interface is this end of the connection. "
        "Pick from existing landmarks or 'None' to clear.",
    'conn_origin_zone':
        "Zone hosting this endpoint. Used together with Landmark for "
        "LandmarkInterface kind, or alone for ZoneInterface.",
    'conn_dest_kind':
        "LandmarkInterface=connect via a specific landmark's predefined "
        "connection point (most vanilla connections use this). "
        "ZoneInterface=connect to a generic zone face directly.",
    'conn_dest_landmark':
        "Landmark whose interface is this end of the connection. "
        "Pick from existing landmarks or 'None' to clear.",
    'conn_dest_zone':
        "Zone hosting this endpoint. Used together with Landmark for "
        "LandmarkInterface kind, or alone for ZoneInterface.",
    'hide_unassigned_zones':
        "Hide zones whose primary chapter is not a Live SandboxSmall chapter "
        "(orphans, outdoor/bridge chapters, disabled chapters)",
}


class Tooltip:
    """Simple hover tooltip. Shows a small popup with delay after mouse enter,
    hides immediately on leave or on click anywhere else.
    Use attach_tooltip(widget, 'key') where key is in TOOLTIPS."""

    _active = None  # only one tooltip visible at a time

    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self._after = None
        self._tip = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<Button-1>', self._hide, add='+')
        widget.bind('<Destroy>', self._hide, add='+')

    def _schedule(self, _):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after is not None:
            try: self.widget.after_cancel(self._after)
            except Exception: pass
            self._after = None

    def _show(self):
        if self._tip is not None:
            return
        if Tooltip._active is not None and Tooltip._active is not self:
            try: Tooltip._active._hide()
            except Exception: pass
        try:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            return
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        tw.configure(background='#202020')
        lbl = tk.Label(tw, text=self.text, justify='left',
                       background='#fffacd', foreground='#1a1a1a',
                       relief='solid', borderwidth=1,
                       font=('Segoe UI', 9), padx=8, pady=6)
        lbl.pack()
        self._tip = tw
        Tooltip._active = self

    def _hide(self, _=None):
        self._cancel()
        if self._tip is not None:
            try: self._tip.destroy()
            except Exception: pass
            self._tip = None
        if Tooltip._active is self:
            Tooltip._active = None


def attach_tooltip(widget, key):
    """Convenience wrapper — does nothing if key not registered."""
    txt = TOOLTIPS.get(key)
    if txt:
        Tooltip(widget, txt)
MODIFIED_TAG = 'modified'
DISABLED_TAG = 'disabled_row'

BOOK_ACCURATE_CHAPTERS = [
    ('SandboxSmall-chapter-9',  9,  'Chapter9.Name',  'Level 5'),
    ('SandboxSmall-chapter-10', 10, 'Chapter10.Name', 'Level 6'),
    ('SandboxSmall-chapter-11', 11, 'Chapter11.Name', 'Level 7 (TOP)'),
    ('SandboxSmall-chapter-12', 12, 'Chapter12.Name', '5th Deep'),
    ('SandboxSmall-chapter-13', 13, 'Chapter13.Name', '6th Deep'),
    ('SandboxSmall-chapter-14', 14, 'Chapter14.Name', '7th Deep'),
    ('SandboxSmall-chapter-15', 15, 'Chapter15.Name', 'Foundations of Stone'),
]


# -----------------------------------------------------------------------------
# Settings (persistent across sessions — INI next to the script)
# -----------------------------------------------------------------------------

class Settings:
    """Thin wrapper around configparser that persists sort state and other
    small bits of UI state across editor sessions."""

    SECTION_SORT = 'sort'
    SECTION_LOCKS = 'locks'
    SECTION_BUILD = 'build'
    SECTION_FILTERS = 'filters'

    def __init__(self, path: Path):
        self.path = path
        self.cfg = configparser.ConfigParser()
        if self.path.exists():
            try:
                self.cfg.read(self.path, encoding='utf-8')
            except Exception:
                self.cfg = configparser.ConfigParser()
        for s in (self.SECTION_SORT, self.SECTION_LOCKS,
                  self.SECTION_BUILD, self.SECTION_FILTERS):
            if not self.cfg.has_section(s):
                self.cfg.add_section(s)

    # ---- sort state ----
    def get_sort(self, tree_key):
        raw = self.cfg.get(self.SECTION_SORT, tree_key, fallback=None)
        if not raw:
            return None, False
        parts = raw.split('|')
        col = parts[0] or None
        rev = len(parts) > 1 and parts[1].lower() in ('1', 'true', 'yes')
        return col, rev

    def set_sort(self, tree_key, column, reverse):
        self.cfg.set(self.SECTION_SORT, tree_key,
                     f'{column or ""}|{1 if reverse else 0}')
        self._save()

    # ---- filter / UI prefs (persists across editor sessions) ----
    def get_filter(self, key, default=''):
        """Get a saved filter / UI preference value (string)."""
        return self.cfg.get(self.SECTION_FILTERS, key, fallback=default)

    def set_filter(self, key, value):
        """Save a filter / UI preference value. Coerces bool/None to str."""
        if value is None:
            value = ''
        elif isinstance(value, bool):
            value = '1' if value else '0'
        self.cfg.set(self.SECTION_FILTERS, key, str(value))
        self._save()

    def get_filter_bool(self, key, default=False):
        raw = self.cfg.get(self.SECTION_FILTERS, key,
                           fallback='1' if default else '0')
        return raw.lower() in ('1', 'true', 'yes')

    # ---- locks (protected changes) ----
    def is_locked(self, lock_key):
        return self.cfg.getboolean(self.SECTION_LOCKS, lock_key, fallback=False)

    def set_lock(self, lock_key, value):
        if value:
            self.cfg.set(self.SECTION_LOCKS, lock_key, 'true')
        elif self.cfg.has_option(self.SECTION_LOCKS, lock_key):
            self.cfg.remove_option(self.SECTION_LOCKS, lock_key)
        self._save()

    def locked_keys(self):
        """Return list of every lock key currently true."""
        return [k for k, v in self.cfg.items(self.SECTION_LOCKS)
                if v.lower() in ('1', 'true', 'yes')]

    # ---- build version (MAJ.MIN.PAT, each 0..999, persists in ini) ----
    def get_mod_version(self):
        raw = self.cfg.get(self.SECTION_BUILD, 'mod_version',
                           fallback=DEFAULT_MOD_VERSION)
        parts = (raw or DEFAULT_MOD_VERSION).split('.')
        try:
            maj, minor, pat = (int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            maj, minor, pat = 1, 0, 1
        # clamp each to 0..999
        maj = max(0, min(999, maj))
        minor = max(0, min(999, minor))
        pat = max(0, min(999, pat))
        return f'{maj}.{minor}.{pat}'

    def bump_mod_version(self):
        """Increment patch by 1; roll minor on 1000, roll major on 1000."""
        cur = self.get_mod_version()
        maj, minor, pat = (int(x) for x in cur.split('.'))
        pat += 1
        if pat > 999:
            pat = 0
            minor += 1
        if minor > 999:
            minor = 0
            maj += 1
        maj = min(999, maj)
        new = f'{maj}.{minor}.{pat}'
        self.cfg.set(self.SECTION_BUILD, 'mod_version', new)
        self._save()
        return new

    # ---- internals ----
    def _save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                self.cfg.write(f)
        except Exception:
            pass  # best-effort — don't block the UI on a write failure


SETTINGS = Settings(SETTINGS_PATH)


# -----------------------------------------------------------------------------
# UAssetAPI JSON helpers
# -----------------------------------------------------------------------------

def find_prop(row_value, name):
    for p in row_value or []:
        if isinstance(p, dict) and p.get('Name') == name:
            return p
    return None


def get_enum(prop):
    if prop is None:
        return ''
    v = prop.get('Value', '')
    if isinstance(v, str) and '::' in v:
        return v.split('::', 1)[1]
    return str(v) if v is not None else ''


def set_enum(prop, new_name):
    if prop is None:
        return
    cur = prop.get('Value', '')
    if isinstance(cur, str) and '::' in cur:
        prefix = cur.split('::', 1)[0]
        prop['Value'] = f'{prefix}::{new_name}'
    else:
        et = prop.get('EnumType') or ''
        prop['Value'] = f'{et}::{new_name}' if et else new_name


def get_rowname(prop):
    if prop is None:
        return ''
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict) and item.get('Name') == 'RowName':
            return str(item.get('Value', ''))
    return ''


def set_rowname(prop, new_name):
    if prop is None:
        return
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict) and item.get('Name') == 'RowName':
            item['Value'] = new_name
            return


def get_tagname(prop):
    if prop is None:
        return ''
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict) and item.get('Name') == 'TagName':
            return str(item.get('Value', ''))
    return ''


def set_tagname(prop, new_tag):
    if prop is None:
        return
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict) and item.get('Name') == 'TagName':
            item['Value'] = new_tag
            return


def get_intvec(prop):
    if prop is None:
        return (0, 0, 0)
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict):
            v = item.get('Value')
            if isinstance(v, dict) and 'X' in v:
                return (int(v['X']), int(v['Y']), int(v['Z']))
    return (0, 0, 0)


def set_intvec(prop, x, y, z):
    if prop is None:
        return
    for item in prop.get('Value', []) or []:
        if isinstance(item, dict):
            v = item.get('Value')
            if isinstance(v, dict) and 'X' in v:
                v['X'] = int(x); v['Y'] = int(y); v['Z'] = int(z)
                return


def get_scalar(prop, default=0):
    if prop is None:
        return default
    return prop.get('Value', default)


def set_scalar(prop, value):
    if prop is not None:
        prop['Value'] = value


def _summarise_prop(prop):
    """Compact one-line human summary of a single UAssetAPI property for diff
    display. Handles enums, rowhandles, tagnames, intvectors, scalars, arrays
    and falls back to a generic shape description."""
    if prop is None:
        return '(none)'
    t = prop.get('$type', '').split('.')[-1]
    v = prop.get('Value')
    # Enum: 'Prefix::Value' — just show the value part
    if 'EnumPropertyData' in t and isinstance(v, str):
        return v.split('::', 1)[1] if '::' in v else v
    # Scalar boxed via Value at root
    if isinstance(v, (int, float, bool)) and 'StructProperty' not in t:
        return str(v)
    if isinstance(v, str):
        return v
    # Struct-wrapped rowhandle / tag / intvector
    if isinstance(v, list):
        # RowHandle with nested RowName
        for it in v:
            if isinstance(it, dict):
                if it.get('Name') == 'RowName':
                    return f"→row:{it.get('Value', '')}"
                if it.get('Name') == 'TagName':
                    return f"tag:{it.get('Value', '')}"
                inner = it.get('Value')
                if isinstance(inner, dict) and 'X' in inner:
                    return f"({inner['X']}, {inner['Y']}, {inner['Z']})"
        # Generic struct or array
        return f'[{len(v)} entries]'
    return str(v)


def resolve_object_ref(data, ref_idx):
    if not ref_idx:
        return 'None'
    if ref_idx < 0:
        imports = data.get('Imports', [])
        idx = -ref_idx - 1
        if 0 <= idx < len(imports):
            imp = imports[idx]
            return f"{imp.get('ObjectName','?')}  ({imp.get('ClassName','?')})"
        return f'Import#{idx}'
    exports = data.get('Exports', [])
    idx = ref_idx - 1
    if 0 <= idx < len(exports):
        exp = exports[idx]
        return f"{exp.get('ObjectName','?')}  (export)"
    return f'Export#{idx}'


# -----------------------------------------------------------------------------
# DataTableDoc
# -----------------------------------------------------------------------------

class DataTableDoc:
    """Wraps one DataTable JSON + a pristine sidecar (.original.json) used
    at build time to decide which tables actually need to be bundled.

    - json_path          : working JSON (edited in place)
    - original_path      : sidecar snapshot of the pristine decompiled JSON.
                           Created on first load if missing. Never overwritten
                           unless the user explicitly re-baselines.
    - _saved_snapshot    : string of current on-disk JSON, used for is_dirty()
    """

    def __init__(self, key, json_path, uasset_stem, label):
        self.key = key
        self.json_path = json_path
        self.uasset_stem = uasset_stem
        self.label = label
        # Pristine sidecar lives next to the working JSON
        self.original_path = json_path.with_name(
            json_path.stem + '.original' + json_path.suffix)
        self.data = None
        self.rows = []
        self._saved_snapshot = None

    def load(self):
        if not self.json_path.exists():
            return False
        with open(self.json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        exports = self.data.get('Exports', [])
        table = exports[0].get('Table', {}) if exports else {}
        # DataTable has rows under 'Data'; StringTable has entries under 'Value'
        # as [key, value] pairs. We expose both via .rows for generic access.
        if 'Data' in table:
            self.rows = table['Data']
            self.is_string_table = False
        else:
            self.rows = table.get('Value', []) or []
            self.is_string_table = True
        self._saved_snapshot = json.dumps(self.data, sort_keys=True)
        # First-ever load: stamp the pristine sidecar from what's on disk now.
        if not self.original_path.exists():
            with open(self.original_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2)
        return True

    def save(self):
        with open(self.json_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2)
        self._saved_snapshot = json.dumps(self.data, sort_keys=True)

    def is_dirty(self):
        """True when the in-memory data differs from last-saved on-disk JSON."""
        if self.data is None or self._saved_snapshot is None:
            return False
        return json.dumps(self.data, sort_keys=True) != self._saved_snapshot

    def differs_from_original(self):
        """True when the in-memory data differs from the pristine sidecar.
        This is what decides whether a table is bundled into the mod pak."""
        if self.data is None or not self.original_path.exists():
            return False
        with open(self.original_path, 'r', encoding='utf-8') as f:
            orig = json.load(f)
        return json.dumps(self.data, sort_keys=True) != json.dumps(orig, sort_keys=True)

    def reconcile_empty_struct_arrays(self):
        """For every empty ArrayProperty whose element type is StructProperty,
        ensure there's a DummyStruct field on the ArrayProperty. UAssetGUI
        fromjson crashes with 'Unable to reconstruct DummyStruct within empty
        StructProperty array' otherwise.

        Sources for the DummyStruct template (tried in order):
          1. Another instance of the same property Name with non-empty Value
             elsewhere in this doc
          2. The pristine .original.json sidecar
        Returns list of (row_name, property_name) that were patched."""
        if self.data is None:
            return []

        # Build a library of struct templates keyed by property name, sourced
        # from any non-empty struct array in this doc.
        templates = {}

        def collect(obj):
            if isinstance(obj, dict):
                if (obj.get('$type', '').endswith('ArrayPropertyData, UAssetAPI')
                        and obj.get('ArrayType') == 'StructProperty'):
                    name = obj.get('Name', '')
                    val = obj.get('Value') or []
                    if val and isinstance(val[0], dict) and name not in templates:
                        templates[name] = copy.deepcopy(val[0])
                for v in obj.values():
                    collect(v)
            elif isinstance(obj, list):
                for it in obj:
                    collect(it)
        collect(self.data.get('Exports', []))

        # Also pull from pristine as a fallback source
        if self.original_path.exists():
            try:
                with open(self.original_path, 'r', encoding='utf-8') as f:
                    orig = json.load(f)
                def collect2(obj):
                    if isinstance(obj, dict):
                        if (obj.get('$type', '').endswith('ArrayPropertyData, UAssetAPI')
                                and obj.get('ArrayType') == 'StructProperty'):
                            name = obj.get('Name', '')
                            val = obj.get('Value') or []
                            ds = obj.get('DummyStruct')
                            if name not in templates:
                                if val and isinstance(val[0], dict):
                                    templates[name] = copy.deepcopy(val[0])
                                elif isinstance(ds, dict):
                                    templates[name] = copy.deepcopy(ds)
                        for v in obj.values():
                            collect2(v)
                    elif isinstance(obj, list):
                        for it in obj:
                            collect2(it)
                collect2(orig.get('Exports', []))
            except Exception:
                pass

        # Now patch every empty struct-array that's missing a DummyStruct
        patched = []

        def patch(obj, cur_row=None):
            if isinstance(obj, dict):
                # Track the containing row name if we're inside a table row
                new_row = cur_row
                if obj.get('StructType') == 'DataTable' or 'Name' in obj and isinstance(obj.get('Value'), list):
                    # Heuristic: row dicts often have Name at top level + StructType set
                    pass
                name = obj.get('Name')
                if obj.get('$type', '').endswith('ArrayPropertyData, UAssetAPI') \
                        and obj.get('ArrayType') == 'StructProperty':
                    val = obj.get('Value') or []
                    if not val and 'DummyStruct' not in obj:
                        tpl = templates.get(name)
                        if tpl is not None:
                            obj['DummyStruct'] = copy.deepcopy(tpl)
                            patched.append((cur_row or '?', name))
                # If this dict looks like a table row, remember its Name for child walks
                row_id = cur_row
                if (obj.get('$type', '').endswith('StructPropertyData, UAssetAPI')
                        and obj.get('StructType') in (
                            'ZoneDefinition', 'MorChapterDefinition',
                            'BiomeDefinition', 'MorZoneDeckDefinition',
                            'MorZoneBubbleFilter', 'LandmarkDefinition')):
                    row_id = obj.get('Name')
                for v in obj.values():
                    patch(v, row_id)
            elif isinstance(obj, list):
                for it in obj:
                    patch(it, cur_row)

        patch(self.data.get('Exports', []))
        return patched

    def reconcile_namemap(self):
        """Walk the entire JSON and ensure every FName-style string reference
        is present in the NameMap, then sync the two count fields UAssetGUI
        does NOT auto-recompute on `fromjson`:
            - top-level `NamesReferencedFromExportDataCount`
            - `Generations[0].NameCount`

        Both must equal `len(NameMap)` or the loader treats trailing FName
        indices as out-of-range (the textbook crash signature in
        L2_RouteInterzoneConnections / GetZone).

        UAssetGUI fromjson refuses to serialize any RowName / TagName / Bubble
        value that isn't in the NameMap — it dies with 'Attempt to retrieve
        index of dummy FName'. This scan finds every such reference and
        appends missing ones.

        Names harvested from export data:
        - RowName / TagName / Bubble / BaseBubbleName values (raw NameProperty)
        - EnumProperty: BOTH the qualified value (e.g. 'ERowEnabledState::Live')
          AND the bare enum type ('ERowEnabledState') must be in NameMap.
        - StructType strings on every struct (these are FNames too)
        - Property Name strings (only when not already covered)

        Returns list of names added (may be empty)."""
        if self.data is None:
            return []
        namemap = self.data.setdefault('NameMap', [])
        present = set(namemap)
        added = []

        def add(s):
            if isinstance(s, str) and s and s != 'None' and s not in present:
                namemap.append(s); present.add(s); added.append(s)

        # Property-name tags that store FName-ish string values we care about.
        FNAME_PROPS = {'RowName', 'TagName', 'Bubble', 'BaseBubbleName'}

        def walk(obj):
            if isinstance(obj, dict):
                n = obj.get('Name')
                v = obj.get('Value')
                t = obj.get('$type', '') or ''
                # Raw FName-style values (RowName, TagName, etc.)
                if n in FNAME_PROPS and isinstance(v, str):
                    add(v)
                # EnumPropertyData: qualified value ('Type::Value') + bare type
                if 'EnumPropertyData' in t:
                    et = obj.get('EnumType')
                    add(et)
                    if isinstance(v, str):
                        add(v)
                        # Also add bare type if value is qualified
                        if '::' in v:
                            add(v.split('::', 1)[0])
                # StructType strings used by struct/array props
                st = obj.get('StructType')
                if st and st != 'Generic':
                    add(st)
                # Recurse
                for val in obj.values():
                    walk(val)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)

        walk(self.data.get('Exports', []))

        # Row Names themselves are FNames — make sure each row's Name is
        # in NameMap (otherwise GuessFName fails on row-handle resolution).
        for r in (self.data.get('Exports') or [{}])[0].get('Table', {}).get('Data', []):
            rn = r.get('Name') if isinstance(r, dict) else None
            if rn:
                add(rn)

        # Imports table FNames (ObjectName, ClassPackage, ClassName, PackageName)
        for imp in self.data.get('Imports', []) or []:
            for kk in ('ObjectName', 'ClassPackage', 'ClassName', 'PackageName'):
                add(imp.get(kk))

        # Sync the two stale count fields. Both MUST equal len(NameMap)
        # for the UE loader; UAssetGUI fromjson does NOT recompute these.
        n = len(namemap)
        cur_nref = self.data.get('NamesReferencedFromExportDataCount')
        if cur_nref != n:
            self.data['NamesReferencedFromExportDataCount'] = n
        gens = self.data.get('Generations') or []
        if gens and isinstance(gens, list) and isinstance(gens[0], dict):
            if gens[0].get('NameCount') != n:
                gens[0]['NameCount'] = n

        return added

    def change_summary(self):
        """Structured diff at the row level — returns (added, removed, modified) lists of row names.
        Used by the build-manifest dialog so the user can see what will be bundled."""
        if self.data is None or not self.original_path.exists():
            return [], [], []
        with open(self.original_path, 'r', encoding='utf-8') as f:
            orig = json.load(f)

        def index_rows(d):
            ex = d.get('Exports', [])
            rows = ex[0].get('Table', {}).get('Data', []) if ex else []
            return {r.get('Name', f'<row{i}>'): r for i, r in enumerate(rows)}

        cur_rows = index_rows(self.data)
        old_rows = index_rows(orig)
        added = sorted(set(cur_rows) - set(old_rows), key=natural_key)
        removed = sorted(set(old_rows) - set(cur_rows), key=natural_key)
        modified = []
        for name in sorted(set(cur_rows) & set(old_rows), key=natural_key):
            if json.dumps(cur_rows[name], sort_keys=True) != json.dumps(
                    old_rows[name], sort_keys=True):
                modified.append(name)
        return added, removed, modified

    def _load_original(self):
        """Return the parsed pristine JSON, or None if the sidecar is missing."""
        if self.data is None or not self.original_path.exists():
            return None
        with open(self.original_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _cur_rows_list(self):
        ex = self.data.get('Exports', []) if self.data else []
        return ex[0].get('Table', {}).get('Data', []) if ex else []

    def revert_row(self, row_name):
        """Undo every change to a single row.
        - If the row was added in this session, delete it from current.
        - If the row was removed, restore it from pristine.
        - If the row was modified, replace the whole row with pristine.
        Returns a short human description of what happened, or None if nothing
        to revert."""
        orig = self._load_original()
        if orig is None:
            return None
        cur_rows = self._cur_rows_list()
        orig_rows = orig.get('Exports', [{}])[0].get('Table', {}).get('Data', [])
        cur_by_name = {r.get('Name'): i for i, r in enumerate(cur_rows)}
        orig_by_name = {r.get('Name'): r for r in orig_rows}

        in_cur = row_name in cur_by_name
        in_orig = row_name in orig_by_name

        if in_cur and not in_orig:
            del cur_rows[cur_by_name[row_name]]
            self.rows = cur_rows
            return f'Removed added row {row_name}'
        if in_orig and not in_cur:
            cur_rows.append(copy.deepcopy(orig_by_name[row_name]))
            self.rows = cur_rows
            return f'Restored previously-deleted row {row_name}'
        if in_cur and in_orig:
            cur_rows[cur_by_name[row_name]] = copy.deepcopy(orig_by_name[row_name])
            self.rows = cur_rows
            return f'Reverted modifications on row {row_name}'
        return None

    def revert_field(self, row_name, field_name):
        """Undo the change to one field within a row."""
        orig = self._load_original()
        if orig is None:
            return None
        cur_rows = self._cur_rows_list()
        orig_rows = orig.get('Exports', [{}])[0].get('Table', {}).get('Data', [])
        cur_row = next((r for r in cur_rows if r.get('Name') == row_name), None)
        orig_row = next((r for r in orig_rows if r.get('Name') == row_name), None)
        if cur_row is None or orig_row is None:
            return None

        orig_props = {p.get('Name'): p for p in (orig_row.get('Value') or [])
                      if isinstance(p, dict)}
        cur_value = cur_row.get('Value', [])

        # Find the current prop by name
        for i, p in enumerate(list(cur_value)):
            if isinstance(p, dict) and p.get('Name') == field_name:
                if field_name in orig_props:
                    cur_value[i] = copy.deepcopy(orig_props[field_name])
                    return f'Reverted {row_name}.{field_name}'
                else:
                    del cur_value[i]
                    return f'Removed {row_name}.{field_name} (was absent in pristine)'
        # Field didn't exist in current — put the pristine one back
        if field_name in orig_props:
            cur_value.append(copy.deepcopy(orig_props[field_name]))
            return f'Restored {row_name}.{field_name}'
        return None

    def revert_all(self):
        """Restore the entire DataTable to its pristine state."""
        orig = self._load_original()
        if orig is None:
            return None
        self.data = copy.deepcopy(orig)
        ex = self.data.get('Exports', [])
        self.rows = ex[0].get('Table', {}).get('Data', []) if ex else []
        return f'Reverted {self.label} fully to pristine state'

    def row_field_diffs(self, row_name):
        """For a modified row, return a list of (field, old_summary, new_summary)
        tuples describing what changed at the property level."""
        if self.data is None or not self.original_path.exists():
            return []
        with open(self.original_path, 'r', encoding='utf-8') as f:
            orig = json.load(f)

        def index_rows(d):
            ex = d.get('Exports', [])
            rows = ex[0].get('Table', {}).get('Data', []) if ex else []
            return {r.get('Name', f'<row{i}>'): r for i, r in enumerate(rows)}

        cur_rows = index_rows(self.data)
        old_rows = index_rows(orig)
        cur = cur_rows.get(row_name); old = old_rows.get(row_name)
        if cur is None or old is None:
            return []

        def index_props(row):
            return {p.get('Name', f'<prop{i}>'): p
                    for i, p in enumerate(row.get('Value', []) or [])
                    if isinstance(p, dict)}

        cur_props = index_props(cur); old_props = index_props(old)
        out = []
        for name in sorted(set(cur_props) | set(old_props), key=natural_key):
            old_p = old_props.get(name)
            new_p = cur_props.get(name)
            if old_p is None:
                out.append((name, '(absent)', _summarise_prop(new_p)))
            elif new_p is None:
                out.append((name, _summarise_prop(old_p), '(absent)'))
            else:
                if json.dumps(old_p, sort_keys=True) != json.dumps(new_p, sort_keys=True):
                    out.append((name, _summarise_prop(old_p), _summarise_prop(new_p)))
        return out


# -----------------------------------------------------------------------------
# Build-time validator
# -----------------------------------------------------------------------------
# Pipeline of checks that runs BEFORE every UAssetGUI fromjson + retoc build.
# Catches the defect classes that have crashed the game during this project:
#   - NameMap inconsistencies (the GetZone/L2_RouteInterzoneConnections crash)
#   - Counter mismatches (NamesReferencedFromExportDataCount,
#     Generations[0].NameCount stale after NameMap edits)
#   - Empty StructProperty arrays without DummyStruct templates
#     (UAssetGUI fromjson exception: "Unable to reconstruct DummyStruct...")
#   - Cross-DT row references that don't resolve
#   - Live rows pointing at Disabled targets
#
# Each check is independent and reports zero or more Issue objects. Issues are
# grouped by severity (error / warning / info). Errors block the build by
# default; warnings prompt; info is reported but never blocks.
#
# Adding a new check: append a function returning list[Issue] to
# BuildValidator.CHECKS. It receives a dict {doc_key: DataTableDoc}.
# Auto-fixers are optional — set issue.fixer to a callable that mutates the
# data in place; the validator will call it during the auto-fix pass.

class Issue:
    """One validation finding. severity: 'error' | 'warning' | 'info'."""

    __slots__ = ('severity', 'check', 'doc_key', 'detail', 'fixer', 'fixer_label')

    def __init__(self, severity, check, doc_key, detail,
                 fixer=None, fixer_label=None):
        self.severity = severity
        self.check = check  # short identifier, e.g. 'namemap_counters'
        self.doc_key = doc_key  # which DataTableDoc this is about (or None)
        self.detail = detail  # human-readable
        self.fixer = fixer  # callable() that mutates doc.data in place
        self.fixer_label = fixer_label or ('auto-fix' if fixer else None)


# Feature flag — set False to fall back to the old messagebox flow.
USE_NEW_VALIDATOR_UI = True


# Plain-English titles + explanations for every validator check ID.
# Keys are the `Issue.check` values; values are (title, explanation).
# Some entries appear under two keys because the user-facing spec used
# different IDs than the code actually emits — both map to the same
# friendly text so future renames are painless.
_HUMAN_TITLES = {
    # New checks added 2026-04 from the 14-floor stair session
    'stair_bubble_z_oob': (
        "Stair bubble Z out of world bounds",
        "An elevator zone's bubble Z range (BP.Z + TargetSize.Z - 1) exceeds "
        "engine bounds [0..29]. The engine null-derefs when routing past "
        "world edges. Auto-fix shrinks TargetSize.Z so the bubble fits."),
    'chapter_layer_continuity': (
        "Chapter Layer sequence has gaps",
        "Live SS chapter Layer values aren't sequential — there's a gap "
        "between min(Layer) and max(Layer). The engine handles gaps but "
        "they can hide intentional missing floors and break stair traversal "
        "expectations. Warning, not error."),
    'chapter_has_at_least_one_zone': (
        "Chapter has no zones",
        "A Live SS chapter row isn't referenced by any Live SS zone via "
        "Chapter or AdditionalChapters. The chapter is dead weight — it "
        "won't render any space. Either delete the chapter row or add a "
        "zone for it. Warning, not error."),
    'live_landmark_has_host': (
        "Sandbox landmark has no host zone",
        "A Live Sandbox-namespaced landmark isn't referenced by any Live "
        "SS zone's LandmarkHandles. The engine still loads it but it "
        "doesn't physically exist in the world. Warning, not error."),
    # New checks added 2026-04 from the +7 Z-shift session
    'zone_preferred_z_in_band': (
        "Zone PreferredZOverride out of band",
        "A Live SS zone's PreferredZOverride field points at a Z cell that "
        "no Live SS chapter covers. The engine forces the zone to that Z and "
        "then null-derefs when GetZone() can't find a matching chapter. Fix "
        "by either shifting the override to a covered Z, setting it to -1 "
        "(no override), or extending a chapter's MinZ/MaxZ to include it."),
    'nested_subcell_z_in_band': (
        "Nested Subcell.Z out of chapter band",
        "A LayoutConnection has OriginInterface.Subcell.Z or "
        "DestinationInterface.Subcell.Z (nested IntVectors inside the "
        "interface struct) at a Z value not covered by any Live SS chapter. "
        "The A* router crashes when it walks the routing graph and hits "
        "this cell. These nested Subcells are easy to miss when shifting "
        "chapter Z bands — they're not the top-level Subcell field."),
    'ss_landmark_bp_in_band': (
        "Sandbox landmark BasePosition out of band",
        "A Sandbox-namespaced landmark (or bridge landmark like "
        "DurinsTower/TradingPost/DimrillDale) has BasePosition.Z at a Z cell "
        "no Live SS chapter covers. The engine still loads orphan SS "
        "landmarks even when they have no host zone via LandmarkHandles, "
        "and routing around them null-derefs. (X=0, Y=0 sentinels are "
        "exempt — those mean auto-place.)"),
    # NameMap family
    'counter_sync': (
        "NameMap counters out of sync",
        "Some DataTable's NameMap entry count doesn't match its counter "
        "fields. Auto-fix re-syncs them."),
    'nm_count_mismatch': (
        "NameMap counters out of sync",
        "Some DataTable's NameMap entry count doesn't match its counter "
        "fields. Auto-fix re-syncs them."),
    'namemap_completeness': (
        "NameMap missing referenced names",
        "DataTable references names that aren't listed in its NameMap. "
        "Auto-fix appends them."),
    'nm_missing_entries': (
        "NameMap missing referenced names",
        "DataTable references names that aren't listed in its NameMap. "
        "Auto-fix appends them."),
    'namemap_dups': (
        "NameMap has duplicate names",
        "Same name appears more than once in a NameMap. Auto-fix removes "
        "duplicates."),
    'nm_duplicate_entries': (
        "NameMap has duplicate names",
        "Same name appears more than once in a NameMap. Auto-fix removes "
        "duplicates."),

    # Z-bounds family
    'z_bounds_zone_top': (
        "Zone extends past world ceiling (Z=29)",
        "A zone's top extent (Position.Z + TargetSize.Z - 1) exceeds the "
        "world max of Z=29. Auto-fix shrinks TargetSize.Z so the zone "
        "fits within bounds."),
    'z_bounds_zone_pos': (
        "Zone Position.Z out of world bounds",
        "A zone's Position.Z is outside [0, 29]. Auto-fix clamps it "
        "back into range."),
    'z_bounds_zone_bottom': (
        "Zone extends below world floor (Z=0)",
        "A zone's bottom extent goes below Z=0. Auto-fix raises the "
        "zone so it sits at or above the world floor."),
    'z_bounds_chapter': (
        "Chapter Z values out of world bounds",
        "A chapter's MinZ/MaxZ/PrimeZ is outside [0, 29]. Auto-fix "
        "clamps each value into range."),
    'z_bounds_landmark': (
        "Landmark BasePosition.Z out of world bounds",
        "A landmark's BasePosition.Z is outside [0, 29]. Auto-fix "
        "clamps it into range."),

    # Landmark/zone alignment
    'landmark_zband_misalign': (
        "Landmark sits outside its zone's chapter",
        "A landmark's BasePosition.Z is outside the Z band of the "
        "chapter that hosts the zone using it. Auto-fix clamps the "
        "landmark to the chapter MinZ."),
    'landmark_not_at_minz': (
        "Landmark not anchored at chapter floor",
        "A landmark sits inside its chapter's Z band but not at MinZ. "
        "Engine usually tolerates this, but at world ceiling/floor it "
        "can push zones off-grid. Auto-fix clamps the landmark to the "
        "host chapter MinZ."),
    'unanchored_zone': (
        "Landmark-driven position references missing landmark",
        "A zone has bPositionFromLandmarks=true but its LandmarkHandles "
        "list is empty (or all entries are missing) AND has no explicit "
        "Position. Game will null-deref. Auto-fix turns off "
        "bPositionFromLandmarks so the generator picks a spot."),
    'landmark_pos_lm_loop': (
        "Landmark-driven position references missing landmark",
        "A zone has bPositionFromLandmarks=true but its LandmarkHandles "
        "list is empty or all entries are missing. Auto-fix turns off "
        "bPositionFromLandmarks."),

    # Connectivity / routing
    'extended_connectivity_no_neighbour': (
        "Stair extends to a level that doesn't exist",
        "A stair zone wants to extend connectivity to a Layer that has "
        "no chapter present. Game will crash in the A* router. Either "
        "move the stair away from the world edge, or clear the "
        "bExtendedConnectivityLandmark flag on its landmark."),
    'extended_connectivity_z_bounds': (
        "Extended-connectivity zone leaves the world Z grid",
        "A zone with bExtendedConnectivityLandmark=true has its full Z "
        "extent (Pos.Z + Size.Z - 1) below 0 or above 29. The engine "
        "actively walks that Z range while routing, so out-of-bounds = "
        "null-deref crash. Auto-fix shrinks Size.Z so the top fits "
        "inside the world, or clamps Pos.Z up to 0 if it was negative."),
    'orphan_added_data': (
        "User-added items are unreferenced",
        "StringTable entries, landmarks, or chapter rows that were added "
        "by editing (not in vanilla) and that no Live row currently "
        "references. Vanilla content is never flagged. Auto-fix removes "
        "the orphans and syncs NameMaps. Reversible via backup."),
    'chapter_stair_uniqueness': (
        "Chapter hosts more than one stair zone",
        "A Live SandboxSmall chapter row is the primary Chapter of two or "
        "more stair zones (zones with a LandmarkHandles entry whose "
        "bExtendedConnectivityLandmark=true). Each chapter must host at "
        "most ONE stair zone — multiple stairs on the same chapter cause "
        "AllocateCellToParcel 'cell already allocated' errors at "
        "generation time. Move the extra stair zone to a different "
        "chapter, or drop its extended-connectivity landmark."),
    'stair_xy_collision': (
        "Stair landmarks share an X,Y column",
        "Two or more stair landmarks (rows referenced by stair zones with "
        "bExtendedConnectivityLandmark=true) have the same BasePosition.X "
        "AND BasePosition.Y at different Z. The generator treats this as "
        "a vertical column collision and AllocateCellToParcel errors "
        "fire — the cell is already claimed by the first stair. Set "
        "distinct X,Y for each stair landmark."),
    'embedded_bottom_needs_headroom': (
        'Embedded-bottom zone has no headroom below',
        'DarkestDeeps zones extend below their chapter PrimeZ. Their host '
        'chapter must have MinZ < PrimeZ. Either expand the chapter band '
        'or move the zone to a chapter with room below.'),
    'stair_xy_sentinel_overlap': (
        "Multiple stair landmarks use auto-place sentinel (0,0,Z)",
        "Two or more stair landmarks have BasePosition (X==0, Y==0). "
        "(0,0,*) is the engine's auto-place sentinel: the runtime resolves "
        "it to a generated cell. With multiple sentinel stairs, runtime "
        "placement may still drop them onto the same X,Y column and fire "
        "AllocateCellToParcel 'cell already allocated'. Pin explicit X,Y "
        "values on each stair landmark to make placement deterministic."),
    'connection_null_endpoints': (
        "LayoutConnection has null endpoints",
        "A connection row has no Origin and/or Destination landmark. The "
        "engine null-derefs in FMorLayoutConnectionInstance::GetZone() at "
        "routing time. Auto-fix disables the row so the router skips it."),
    'connection_endpoint_disabled': (
        "Connection points to a missing zone",
        "A LayoutConnection row references an Origin/Destination zone "
        "that is Disabled or doesn't exist. Auto-fix disables the "
        "connection row."),
    'connection_orphan_endpoint': (
        "Connection points to a missing zone",
        "A LayoutConnection row references an Origin/Destination zone "
        "that doesn't exist (or is disabled). Auto-fix disables the "
        "connection."),

    # Cross-DT integrity
    'cross_dt_refs': (
        "Broken cross-table references",
        "RowHandle references that don't resolve to a row in the target "
        "DataTable. Fix by updating the references or restoring the "
        "target rows."),
    'duplicate_rows': (
        "Duplicate row names within a DataTable",
        "Two rows share the same Name in one DataTable; only the first "
        "is reachable. Rename or delete one."),
    'enabled_state': (
        "Unknown EnabledState value on row(s)",
        "Some rows have an EnabledState value the engine doesn't "
        "recognize. Set it to Live, Disabled, CookedOut, or Test."),
    'empty_struct_arrays': (
        "Empty struct arrays missing template",
        "Empty StructProperty arrays must have a DummyStruct template "
        "or UAssetGUI fromjson dies. Auto-fix injects the templates."),
    'live_to_disabled': (
        "Live row references a Disabled row",
        "A Live zone references a chapter/landmark/deck/zone that is "
        "Disabled. Auto-fix clears zone refs to None and re-enables "
        "other Disabled targets."),
    'chapterid_duplicates': (
        "Duplicate ChapterID values",
        "Two or more Live SandboxSmall chapters share the same "
        "ChapterID. Renumber so each is unique."),
    'chapter_displayname_missing': (
        "Chapter DisplayName missing from StringTable",
        "A chapter's DisplayName references a key that isn't in the "
        "World StringTable. Add the key, or change the reference."),
}


def humanize(issue):
    """Return (title, explanation) for an Issue.

    Falls back to a Title-Cased version of the check ID and the raw
    detail string if no entry exists. The detail text is always
    available separately via `issue.detail` for callers that want it.
    """
    entry = _HUMAN_TITLES.get(issue.check)
    if entry:
        title, explanation = entry
        return title, explanation
    # Fallback: snake_case -> Title Case
    pretty = (issue.check or 'unknown').replace('_', ' ').strip()
    pretty = pretty[:1].upper() + pretty[1:]
    return pretty, issue.detail or ''


class BuildValidator:
    """Runs a registry of checks against the loaded DataTableDocs.
    Returns a structured report and a list of auto-fixers."""

    # Sentinel FName values that are not real cross-references
    NULL_FNAMES = frozenset({'None', 'Null', ''})

    # ERowEnabledState values that are valid in serialized DTs
    VALID_ENABLED_STATES = frozenset({
        'ERowEnabledState::Live',
        'ERowEnabledState::Disabled',
        'ERowEnabledState::CookedOut',
        'ERowEnabledState::Test',
    })

    # HARD ENGINE LIMIT: world Z must be 0..29 inclusive.
    # Confirmed by build/test 2026-04-25. Exceeding this crashes worldgen
    # at FMorLayoutConnectionInstance::GetZone in L2_RouteInterzoneConnections.
    # Applies to ALL Z values: zone Position.Z, zone TargetSize range
    # (Position.Z + TargetSize.Z - 1), landmark BasePosition.Z,
    # chapter MinZ/MaxZ/PrimeZ. The unplaced sentinel is the *full vector*
    # Position=(0,0,0) — Z=0 alone is a valid coordinate.
    Z_MIN = 0
    Z_MAX = 29

    def __init__(self, docs):
        # docs: dict[str, DataTableDoc] — only docs with .data loaded
        self.docs = {k: d for k, d in docs.items() if d and d.data is not None}

    # --- helpers --------------------------------------------------------

    @staticmethod
    def _fp(v, n):
        for p in v or []:
            if isinstance(p, dict) and p.get('Name') == n:
                return p
        return None

    @classmethod
    def _get(cls, r, k):
        p = cls._fp(r.get('Value', []), k)
        if not p:
            return None
        v = p.get('Value')
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get('Name') == 'RowName':
                    return it.get('Value', '')
        return v

    @classmethod
    def _zstate(cls, r):
        p = cls._fp(r.get('Value', []), 'EnabledState')
        return str(p.get('Value', '')).split('::')[-1] if p else None

    @staticmethod
    def _rowset(doc):
        if not doc or not doc.data:
            return set()
        return {r.get('Name') for r in doc.rows
                if isinstance(r, dict) and r.get('Name')}

    @classmethod
    def _live_rowset(cls, doc):
        if not doc or not doc.data:
            return set()
        return {r.get('Name') for r in doc.rows
                if isinstance(r, dict) and r.get('Name')
                and cls._zstate(r) != 'Disabled'}

    # --- the checks -----------------------------------------------------

    def _check_counter_sync(self):
        """NamesReferencedFromExportDataCount and Generations[0].NameCount
        must equal len(NameMap). UAssetGUI fromjson does NOT recompute these.
        Auto-fix: bump counters."""
        out = []
        for k, doc in self.docs.items():
            d = doc.data
            n = len(d.get('NameMap', []))
            nr = d.get('NamesReferencedFromExportDataCount')
            gens = d.get('Generations') or []
            gn = gens[0].get('NameCount') if gens and isinstance(gens[0], dict) else None
            if nr != n or gn != n:
                def fix(_d=d, _n=n):
                    _d['NamesReferencedFromExportDataCount'] = _n
                    g = _d.get('Generations') or []
                    if g and isinstance(g[0], dict):
                        g[0]['NameCount'] = _n
                out.append(Issue(
                    'error', 'counter_sync', k,
                    f'NameMap={n} but NamesRef={nr} Gen.NameCount={gn}',
                    fixer=fix, fixer_label='Sync counters to len(NameMap)'))
        return out

    def _check_namemap_completeness(self):
        """Every FName referenced in export data + Imports + row Names must
        be in NameMap. UAssetGUI fromjson dies with 'dummy FName' otherwise.
        Auto-fix: append missing entries (uses doc.reconcile_namemap)."""
        out = []
        FNAME_PROPS = {'RowName', 'TagName', 'Bubble', 'BaseBubbleName'}
        for k, doc in self.docs.items():
            d = doc.data
            nm = set(d.get('NameMap', []))
            missing = []

            # Iterative walk (vs recursive) — DT_Moria_Zones.json is ~7MB
            # and Python recursion overhead made this run for minutes.
            # Stack-based iteration is ~50× faster on this scale.
            stack = [d.get('Exports', [])]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    n = obj.get('Name')
                    v = obj.get('Value')
                    t = obj.get('$type', '') or ''
                    if n in FNAME_PROPS and isinstance(v, str) and v and v != 'None':
                        if v not in nm:
                            missing.append(v)
                    if 'EnumPropertyData' in t:
                        et = obj.get('EnumType')
                        if et and et not in nm:
                            missing.append(et)
                        if isinstance(v, str) and v and v not in nm:
                            missing.append(v)
                            if '::' in v:
                                prefix = v.split('::', 1)[0]
                                if prefix not in nm:
                                    missing.append(prefix)
                    st = obj.get('StructType')
                    if st and st != 'Generic' and st not in nm:
                        missing.append(st)
                    # Fan out to nested dicts/lists ONLY — skip leaf strings/ints
                    for val in obj.values():
                        if isinstance(val, (dict, list)):
                            stack.append(val)
                elif isinstance(obj, list):
                    # Likewise — only push nested containers
                    for it in obj:
                        if isinstance(it, (dict, list)):
                            stack.append(it)
            for r in (d.get('Exports') or [{}])[0].get('Table', {}).get('Data', []):
                rn = r.get('Name')
                if rn and rn not in nm:
                    missing.append(rn)
            for imp in d.get('Imports', []):
                for kk in ('ObjectName', 'ClassPackage', 'ClassName', 'PackageName'):
                    v = imp.get(kk)
                    if isinstance(v, str) and v and v != 'None' and v not in nm:
                        missing.append(v)
            if missing:
                # Dedup while preserving order
                seen = set()
                miss_list = [x for x in missing
                             if not (x in seen or seen.add(x))]
                preview = miss_list[:5]
                more = '' if len(miss_list) <= 5 else f' (+{len(miss_list)-5} more)'
                # Downgraded from error to warning: vanilla SandboxSmall ships
                # with ~15 missing FNames (10 connections + 5 templates) and
                # the engine tolerates them. Auto-fixer remains available.
                out.append(Issue(
                    'warning', 'namemap_completeness', k,
                    f'{len(miss_list)} FName(s) missing from NameMap: {preview}{more}',
                    fixer=lambda _doc=doc: _doc.reconcile_namemap(),
                    fixer_label='Append missing FNames + sync counters'))
        return out

    def _check_namemap_dups(self):
        """NameMap entries should be unique. Duplicates cause index drift.
        Auto-fix: dedup (preserves order)."""
        out = []
        for k, doc in self.docs.items():
            nm = doc.data.get('NameMap', [])
            seen = {}
            for s in nm:
                seen[s] = seen.get(s, 0) + 1
            dups = {kk: c for kk, c in seen.items() if c > 1}
            if dups:
                def fix(_doc=doc):
                    src = _doc.data.get('NameMap', [])
                    seen2 = set()
                    deduped = [x for x in src
                               if not (x in seen2 or seen2.add(x))]
                    _doc.data['NameMap'] = deduped
                    n = len(deduped)
                    _doc.data['NamesReferencedFromExportDataCount'] = n
                    g = _doc.data.get('Generations') or []
                    if g and isinstance(g[0], dict):
                        g[0]['NameCount'] = n
                preview = list(dups.items())[:3]
                out.append(Issue(
                    'error', 'namemap_dups', k,
                    f'{len(dups)} duplicate NameMap entry(ies): {preview}',
                    fixer=fix, fixer_label='Dedup NameMap (order preserved)'))
        return out

    def _check_empty_struct_arrays(self):
        """Empty StructProperty arrays must have a DummyStruct template
        or UAssetGUI fromjson dies. Auto-fix is in
        DataTableDoc.reconcile_empty_struct_arrays."""
        out = []
        for k, doc in self.docs.items():
            bad = []

            def walk(obj):
                if isinstance(obj, dict):
                    t = obj.get('$type', '')
                    if 'ArrayPropertyData' in t and obj.get('ArrayType') == 'StructProperty':
                        v = obj.get('Value') or []
                        if not v and not obj.get('DummyStruct'):
                            bad.append(obj.get('Name', '?'))
                    for val in obj.values():
                        walk(val)
                elif isinstance(obj, list):
                    for it in obj:
                        walk(it)

            walk(doc.data.get('Exports', []))
            if bad:
                out.append(Issue(
                    'error', 'empty_struct_arrays', k,
                    f'{len(bad)} empty StructProperty array(s) lack DummyStruct: {bad[:5]}',
                    fixer=lambda _doc=doc: _doc.reconcile_empty_struct_arrays(),
                    fixer_label='Inject DummyStruct templates'))
        return out

    def _check_dup_rows(self):
        """Two rows with the same Name within one DT — second one is
        unreachable. Not auto-fixable; user must rename or delete.
        Skips StringTable docs (rows are [key,value] pairs, not dicts)."""
        out = []
        for k, doc in self.docs.items():
            if getattr(doc, 'is_string_table', False):
                continue
            seen = {}
            for r in doc.rows:
                if not isinstance(r, dict):
                    continue
                n = r.get('Name')
                if n:
                    seen[n] = seen.get(n, 0) + 1
            dups = {kk: c for kk, c in seen.items() if c > 1}
            if dups:
                out.append(Issue(
                    'error', 'duplicate_rows', k,
                    f'{len(dups)} duplicate row Name(s): {list(dups.items())[:3]}'))
        return out

    def _check_enabled_state_values(self):
        """EnabledState must be a known ERowEnabledState enum value.
        Skips StringTable docs."""
        out = []
        for k, doc in self.docs.items():
            if getattr(doc, 'is_string_table', False):
                continue
            bad = []
            for r in doc.rows:
                if not isinstance(r, dict):
                    continue
                p = self._fp(r.get('Value', []), 'EnabledState')
                if p:
                    v = p.get('Value')
                    if v not in self.VALID_ENABLED_STATES:
                        bad.append((r.get('Name'), v))
            if bad:
                out.append(Issue(
                    'error', 'enabled_state', k,
                    f'{len(bad)} row(s) with unknown EnabledState: {bad[:3]}'))
        return out

    def _check_cross_dt_refs(self):
        """RowHandle references that don't resolve to a row in target DT.
        Skips the 'None' / 'Null' null-FName sentinel."""
        out = []
        # Build target row sets
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        landmarks_doc = self.docs.get('landmarks')
        decks_doc = self.docs.get('decks')
        templates_doc = self.docs.get('templates')
        addpass_doc = self.docs.get('additive_pass')
        layoutconn_doc = self.docs.get('layout_connections')

        zone_rows = self._rowset(zones_doc)
        chap_rows = self._rowset(chapters_doc)
        lm_rows = self._rowset(landmarks_doc)
        deck_rows = self._rowset(decks_doc)
        tpl_rows = self._rowset(templates_doc)
        ap_rows = self._rowset(addpass_doc)

        def safe(v, target):
            return v and v not in self.NULL_FNAMES and target and v not in target

        # Zones cross-refs (skip Disabled rows — engine doesn't process them)
        if zones_doc:
            broken = []
            for r in zones_doc.rows:
                if self._zstate(r) == 'Disabled':
                    continue
                n = r.get('Name')
                for fld, target in (('Chapter', chap_rows),
                                    ('BubbleDeck', deck_rows),
                                    ('PassageDeck', deck_rows),
                                    ('Template', tpl_rows),
                                    ('AdditiveZonePass', ap_rows)):
                    v = self._get(r, fld)
                    if safe(v, target):
                        broken.append((n, fld, v))
                ac = self._fp(r.get('Value', []), 'AdditionalChapters')
                if ac:
                    for it in (ac.get('Value') or []):
                        inner = it.get('Value') if isinstance(it, dict) else None
                        if isinstance(inner, list):
                            for sub in inner:
                                if isinstance(sub, dict) and sub.get('Name') == 'RowName':
                                    v = sub.get('Value', '')
                                    if safe(v, chap_rows):
                                        broken.append((n, 'AdditionalChapters', v))
                lh = self._fp(r.get('Value', []), 'LandmarkHandles')
                if lh:
                    for e in (lh.get('Value') or []):
                        inner = e.get('Value') if isinstance(e, dict) else None
                        if isinstance(inner, list):
                            for sub in inner:
                                if isinstance(sub, dict) and sub.get('Name') == 'Landmark':
                                    for it in (sub.get('Value') or []):
                                        if isinstance(it, dict) and it.get('Name') == 'RowName':
                                            v = it.get('Value', '')
                                            if safe(v, lm_rows):
                                                broken.append((n, 'LandmarkHandles', v))
            if broken:
                out.append(Issue(
                    'error', 'cross_dt_refs', 'zones',
                    f'{len(broken)} unresolved RowHandle ref(s): {broken[:3]}'))

        # LayoutConnections cross-refs (skip Test_* — vanilla loader-skipped)
        if layoutconn_doc:
            broken = []
            for r in layoutconn_doc.rows:
                if self._zstate(r) == 'Test':
                    continue
                n = r.get('Name')
                if n and n.startswith('Test_'):
                    continue
                for fld, target in (('OriginZone', zone_rows),
                                    ('DestinationZone', zone_rows),
                                    ('OriginLandmark', lm_rows),
                                    ('DestinationLandmark', lm_rows)):
                    v = self._get(r, fld)
                    if safe(v, target):
                        broken.append((n, fld, v))
            if broken:
                out.append(Issue(
                    'error', 'cross_dt_refs', 'layout_connections',
                    f'{len(broken)} unresolved RowHandle ref(s): {broken[:3]}'))
        return out

    def _check_z_bounds(self):
        """HARD RULE: every Z coordinate must be in [Z_MIN, Z_MAX] = [0, 29].

        Checks:
          - Chapter MinZ, MaxZ, PrimeZ
          - Zone Position.Z (only when non-(0,0,0) — sentinel zones unaffected)
          - Zone Position.Z + TargetSize.Z - 1 (the zone's TOP must also fit)
          - Landmark BasePosition.Z (only when non-zero)

        Auto-fix: clamp out-of-range values to the valid range. This is a
        last-resort fix — values that need clamping suggest the author
        intent didn't account for the engine limit, so the user should
        review afterward.
        """
        out = []
        zmin, zmax = self.Z_MIN, self.Z_MAX

        def get_intvec_z(prop):
            if not prop: return None
            v = prop.get('Value')
            if isinstance(v, list) and v:
                inner = v[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    return inner['Value'].get('Z')
            return None

        def set_intvec_z(prop, z):
            v = prop.get('Value')
            if isinstance(v, list) and v:
                inner = v[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    inner['Value']['Z'] = z
                    return True
            return False

        # 1. Chapter MinZ/MaxZ/PrimeZ
        chapters_doc = self.docs.get('chapters')
        chap_violations = []
        if chapters_doc:
            for r in chapters_doc.rows:
                if not isinstance(r, dict): continue
                for fld in ('MinZ', 'MaxZ', 'PrimeZ'):
                    p = self._fp(r.get('Value', []), fld)
                    if not p: continue
                    z = p.get('Value')
                    if isinstance(z, int) and not (zmin <= z <= zmax):
                        chap_violations.append((r.get('Name'), fld, z))
            if chap_violations:
                def fix_chapters(_cv=chap_violations, _doc=chapters_doc):
                    for n, fld, _ in _cv:
                        for r in _doc.rows:
                            if r.get('Name') == n:
                                p = self._fp(r.get('Value', []), fld)
                                if p and isinstance(p.get('Value'), int):
                                    p['Value'] = max(zmin, min(zmax, p['Value']))
                preview = chap_violations[:3]
                out.append(Issue(
                    'error', 'z_bounds_chapter', 'chapters',
                    f'{len(chap_violations)} chapter Z value(s) outside [{zmin},{zmax}]: {preview}',
                    fixer=fix_chapters,
                    fixer_label=f'Clamp chapter Z values into [{zmin},{zmax}]'))

        # 2. Zone Position.Z + TargetSize.Z extents
        zones_doc = self.docs.get('zones')
        if zones_doc:
            zone_pos_violations = []
            zone_top_violations = []
            for r in zones_doc.rows:
                if not isinstance(r, dict): continue
                pos = self._fp(r.get('Value', []), 'Position')
                size = self._fp(r.get('Value', []), 'TargetSize')
                pz = get_intvec_z(pos)
                sz = get_intvec_z(size)
                # Skip the unplaced sentinel — full Pos=(0,0,0) means
                # generator-placed.  We detect by checking all three coords.
                if pos:
                    pv = pos.get('Value')
                    if isinstance(pv, list) and pv:
                        inner = pv[0].get('Value') if isinstance(pv[0], dict) else None
                        if isinstance(inner, dict):
                            if (inner.get('X') == 0 and inner.get('Y') == 0
                                    and inner.get('Z') == 0):
                                continue  # unplaced sentinel, skip
                if isinstance(pz, int) and not (zmin <= pz <= zmax):
                    zone_pos_violations.append((r.get('Name'), pz))
                # Top-of-zone check: pz + sz - 1 must also be in range
                if isinstance(pz, int) and isinstance(sz, int) and sz > 0:
                    top = pz + sz - 1
                    if not (zmin <= top <= zmax):
                        zone_top_violations.append((r.get('Name'), pz, sz, top))
            if zone_pos_violations:
                def fix_zone_pos(_v=zone_pos_violations, _doc=zones_doc):
                    for n, _ in _v:
                        for r in _doc.rows:
                            if r.get('Name') == n:
                                pos = self._fp(r.get('Value', []), 'Position')
                                pz = get_intvec_z(pos)
                                if isinstance(pz, int):
                                    set_intvec_z(pos, max(zmin, min(zmax, pz)))
                out.append(Issue(
                    'error', 'z_bounds_zone_pos', 'zones',
                    f'{len(zone_pos_violations)} zone Position.Z out of [{zmin},{zmax}]: {zone_pos_violations[:3]}',
                    fixer=fix_zone_pos,
                    fixer_label=f'Clamp zone Position.Z into [{zmin},{zmax}]'))
            if zone_top_violations:
                # Top exceeds Z_MAX — could shrink TargetSize.Z OR move Position
                # down. Cleanest: shrink TargetSize.Z so Position+Size-1 == Z_MAX.
                def fix_zone_top(_v=zone_top_violations, _doc=zones_doc):
                    for n, pz, sz, top in _v:
                        for r in _doc.rows:
                            if r.get('Name') == n:
                                size = self._fp(r.get('Value', []), 'TargetSize')
                                # New TargetSize.Z = Z_MAX - Position.Z + 1
                                new_sz = max(1, zmax - (pz or 0) + 1)
                                if size:
                                    set_intvec_z(size, new_sz)
                out.append(Issue(
                    'warning', 'z_bounds_zone_top', 'zones',
                    f'{len(zone_top_violations)} zone(s) top extent exceeds Z_MAX={zmax}: {zone_top_violations[:3]}',
                    fixer=fix_zone_top,
                    fixer_label='Shrink TargetSize.Z so zone top fits within Z_MAX'))

        # 3. Landmark BasePosition.Z
        lm_doc = self.docs.get('landmarks')
        if lm_doc:
            lm_violations = []
            for r in lm_doc.rows:
                if not isinstance(r, dict): continue
                bp = self._fp(r.get('Value', []), 'BasePosition')
                bz = get_intvec_z(bp)
                # Same sentinel rule: full (0,0,0) is "unplaced", skip
                if bp:
                    bv = bp.get('Value')
                    if isinstance(bv, list) and bv:
                        inner = bv[0].get('Value') if isinstance(bv[0], dict) else None
                        if isinstance(inner, dict):
                            if (inner.get('X') == 0 and inner.get('Y') == 0
                                    and inner.get('Z') == 0):
                                continue
                if isinstance(bz, int) and not (zmin <= bz <= zmax):
                    lm_violations.append((r.get('Name'), bz))
            if lm_violations:
                def fix_landmarks(_v=lm_violations, _doc=lm_doc):
                    for n, _ in _v:
                        for r in _doc.rows:
                            if r.get('Name') == n:
                                bp = self._fp(r.get('Value', []), 'BasePosition')
                                bz = get_intvec_z(bp)
                                if isinstance(bz, int):
                                    set_intvec_z(bp, max(zmin, min(zmax, bz)))
                out.append(Issue(
                    'error', 'z_bounds_landmark', 'landmarks',
                    f'{len(lm_violations)} landmark BasePosition.Z out of [{zmin},{zmax}]: {lm_violations[:3]}',
                    fixer=fix_landmarks,
                    fixer_label=f'Clamp landmark BasePosition.Z into [{zmin},{zmax}]'))
        return out

    def _check_unanchored_zones(self):
        """A zone with bPositionFromLandmarks=true AND no Landmark AND
        Position=(0,0,0) sentinel has NO anchor at all — the runtime
        has no way to derive its world placement. A* router walks into
        an unmapped grid cell and null-derefs at
        FMorLayoutConnectionInstance::GetZone (offset 0x1a1).

        Note: bPositionFromLandmarks=true with no landmark BUT with an
        explicit Position is fine — engine falls back to the stored
        Position. Only the trifecta (flag + no landmark + null Pos)
        is fatal.

        Auto-fix: clear bPositionFromLandmarks. Zone falls back to
        bPositionFromZoneTable + Position sentinel = generator picks
        a free spot inside its chapter (vanilla pattern)."""
        out = []
        zones_doc = self.docs.get('zones')
        if not zones_doc: return out

        def _intvec(prop):
            v = prop.get('Value') if prop else None
            if isinstance(v, list) and v:
                inner = v[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    d = inner['Value']
                    return (d.get('X'), d.get('Y'), d.get('Z'))
            return (None, None, None)

        bad = []
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled': continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value','')).split('::')[-1] != 'SandboxSmall':
                continue
            pflp = self._fp(r.get('Value', []), 'bPositionFromLandmarks')
            if not pflp or pflp.get('Value') is not True: continue

            pos = _intvec(self._fp(r.get('Value', []), 'Position'))
            if pos != (0, 0, 0): continue  # explicit position — fallback works

            # No-position zone with flag=true → must have a landmark
            has_landmark = False
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if lh:
                for e in (lh.get('Value') or []):
                    if not isinstance(e, dict): continue
                    inner = e.get('Value')
                    if not isinstance(inner, list): continue
                    lhprop = self._fp(inner, 'Landmark')
                    if not lhprop: continue
                    lv = lhprop.get('Value')
                    if isinstance(lv, list):
                        for it in lv:
                            if (isinstance(it, dict) and it.get('Name') == 'RowName'
                                    and it.get('Value','') not in ('', 'None')):
                                has_landmark = True; break
                    if has_landmark: break
            if not has_landmark:
                bad.append(r)
        if bad:
            def fix():
                for r in bad:
                    p = self._fp(r.get('Value', []), 'bPositionFromLandmarks')
                    if p and p.get('Value') is True:
                        p['Value'] = False
            preview = ', '.join(r.get('Name') for r in bad[:5])
            # Downgraded from error to warning: vanilla SandboxSmall ships
            # with 9 zones in this state (bPositionFromLandmarks=true with no
            # LandmarkHandle). The engine handles via runtime placement
            # fallback. Auto-fixer remains available.
            out.append(Issue(
                'warning', 'unanchored_zone', 'zones',
                f'{len(bad)} Live SS zone(s) have bPositionFromLandmarks=true '
                f'but no Landmark attached: {preview}'
                + ('…' if len(bad) > 5 else '') +
                '. Vanilla SandboxSmall has 9 zones in this state — engine '
                'uses runtime placement fallback. Auto-fix clears the flag '
                'if you prefer pinned placement.',
                fixer=fix,
                fixer_label='Set bPositionFromLandmarks=false on unanchored zones'))
        return out

    def _check_connection_null_endpoints(self):
        """Live LayoutConnection rows with null/empty OriginLandmark or
        DestinationLandmark RowName. Engine FMorLayoutConnectionInstance::GetZone()
        null-derefs at routing time when it tries to resolve the missing
        endpoint landmark. Auto-fix: set EnabledState=Disabled so the
        runtime skips the row entirely (no GetZone() lookup occurs)."""
        out = []
        conns_doc = self.docs.get('connections')
        if not conns_doc:
            return out
        bad_rows = []
        for r in conns_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            origin = self._get(r, 'OriginLandmark')
            dest = self._get(r, 'DestinationLandmark')
            o_null = origin in (None, '', 'None')
            d_null = dest in (None, '', 'None')
            if o_null or d_null:
                bad_rows.append((r, origin, dest, o_null, d_null))
        for r, origin, dest, o_null, d_null in bad_rows:
            row_name = r.get('Name')
            def make_fix(_row=r, _doc=conns_doc):
                def fix():
                    p = self._fp(_row.get('Value', []), 'EnabledState')
                    if p:
                        p['Value'] = 'ERowEnabledState::Disabled'
                    nm = _doc.data.get('NameMap', [])
                    if 'ERowEnabledState::Disabled' not in nm:
                        nm.append('ERowEnabledState::Disabled')
                        n = len(nm)
                        _doc.data['NamesReferencedFromExportDataCount'] = n
                        g = _doc.data.get('Generations') or []
                        if g and isinstance(g[0], dict):
                            g[0]['NameCount'] = n
                return fix
            # Downgraded from error to warning: vanilla SandboxSmall ships
            # with 20 LayoutConnections that have null/empty Origin or
            # Destination landmarks, and the engine does not crash on them.
            # Auto-fixer (disable the row) remains available.
            out.append(Issue(
                'warning', 'connection_null_endpoints', 'connections',
                f'LayoutConnection "{row_name}" has null/empty Origin or '
                f'Destination landmark. Vanilla ships with 20 such rows — '
                f'engine skips them silently. Auto-fix disables the row if '
                f'you prefer to clean it up. '
                f'(OriginLandmark={origin!r}, DestinationLandmark={dest!r})',
                fixer=make_fix(),
                fixer_label=f'Disable 1 null-endpoint connection(s)'))
        return out

    def _check_connection_endpoints_live(self):
        """A Live LayoutConnections row whose OriginZone or DestinationZone
        is Disabled (or missing) makes the router call GetZone() and get
        null -> crash at FMorLayoutConnectionInstance::GetZone (offset 0x1a1).

        Same crash signature as the routing class, but the defect lives in
        DT_Moria_LayoutConnections (not in the zone table). The
        existing _check_live_to_disabled covers Live ZONE -> Disabled refs;
        this one covers Live CONNECTION -> Disabled-zone endpoints.

        Auto-fix: set the connection row's EnabledState to Disabled so the
        runtime skips it. Safer than re-enabling whatever zones were
        intentionally disabled."""
        out = []
        zones_doc = self.docs.get('zones')
        conns_doc = self.docs.get('connections')
        if not (zones_doc and conns_doc):
            return out
        disabled_zones = self._rowset(zones_doc) - self._live_rowset(zones_doc)
        SS_RELEVANT = ('SandboxSmall', 'All')
        bad_rows = []
        for r in conns_doc.rows:
            if self._zstate(r) == 'Disabled': continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            zs_v = str(zs.get('Value', '')).split('::')[-1] if zs else 'None'
            if zs_v not in SS_RELEVANT: continue
            origin = self._get(r, 'OriginZone')
            dest = self._get(r, 'DestinationZone')
            issues = []
            if origin and origin != 'None' and origin in disabled_zones:
                issues.append(('OriginZone', origin))
            if dest and dest != 'None' and dest in disabled_zones:
                issues.append(('DestinationZone', dest))
            if issues:
                bad_rows.append((r, issues))
        if bad_rows:
            def fix():
                for r, _ in bad_rows:
                    p = self._fp(r.get('Value', []), 'EnabledState')
                    if p and p.get('Value') != 'ERowEnabledState::Disabled':
                        p['Value'] = 'ERowEnabledState::Disabled'
                # Make sure Disabled is in NameMap
                nm = conns_doc.data.get('NameMap', [])
                if 'ERowEnabledState::Disabled' not in nm:
                    nm.append('ERowEnabledState::Disabled')
                    n = len(nm)
                    conns_doc.data['NamesReferencedFromExportDataCount'] = n
                    g = conns_doc.data.get('Generations') or []
                    if g and isinstance(g[0], dict): g[0]['NameCount'] = n
            preview = '; '.join(
                f'{r.get("Name")} -> ' + ', '.join(f'{k}={v}' for k, v in iss)
                for r, iss in bad_rows[:3])
            out.append(Issue(
                'error', 'connection_endpoint_disabled', 'connections',
                f'{len(bad_rows)} Live LayoutConnection row(s) reference '
                f'Disabled zone endpoints (router-crash class 0x1a1): '
                + preview + ('…' if len(bad_rows) > 3 else ''),
                fixer=fix,
                fixer_label='Disable the affected LayoutConnection rows'))
        return out

    def _check_landmark_minz_anchor(self):
        """Warning: landmark-driven zones (bPositionFromLandmarks=true) whose
        attached landmark sits ABOVE the chapter's MinZ.

        The locked rule: zones grow UP from origin, so the landmark anchor
        should equal the host chapter's MinZ. A landmark in the middle or
        top of the band still works most of the time, but at the world
        ceiling (chap-7 MaxZ=29 or chap-8 MaxZ=2) it can push the zone's
        top off-grid via bExtendFootprint or Size.Z.

        Severity: WARNING (not error) — engine often tolerates this. The
        user is shown a plain-English summary at build time and can choose
        to fix or proceed.

        Auto-fix: clamp the landmark's BasePosition.Z down to the host
        chapter's MinZ."""
        out = []
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        landmarks_doc = self.docs.get('landmarks')
        if not (zones_doc and chapters_doc and landmarks_doc):
            return out

        chap_band = {}
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-chapter-'): continue
            if self._zstate(r) == 'Disabled': continue
            mn = self._get(r, 'MinZ'); mx = self._get(r, 'MaxZ')
            if isinstance(mn, int) and isinstance(mx, int):
                chap_band[n] = (mn, mx)

        # landmark name -> inner BasePosition struct (mutable)
        lm_bp = {}
        for r in landmarks_doc.rows:
            p = self._fp(r.get('Value', []), 'BasePosition')
            v = p.get('Value') if p else None
            if isinstance(v, list) and v and isinstance(v[0], dict):
                inner = v[0].get('Value')
                if isinstance(inner, dict):
                    lm_bp[r['Name']] = inner

        # Collect mismatches: landmark in-band but not at MinZ
        misanchored = []   # (zone_name, chapter, mn, mx, landmark_name, current_z)
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled': continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value','')).split('::')[-1] != 'SandboxSmall':
                continue
            chap = self._get(r, 'Chapter')
            if chap not in chap_band: continue
            mn, mx = chap_band[chap]
            pflp = self._fp(r.get('Value', []), 'bPositionFromLandmarks')
            if not pflp or pflp.get('Value') is not True: continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh: continue
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict): continue
                inner = e.get('Value')
                if not isinstance(inner, list): continue
                lhprop = self._fp(inner, 'Landmark')
                if not lhprop: continue
                lv = lhprop.get('Value')
                if isinstance(lv, list):
                    for it in lv:
                        if isinstance(it, dict) and it.get('Name') == 'RowName':
                            ln = it.get('Value', '')
                            if not ln or ln == 'None': continue
                            bp = lm_bp.get(ln)
                            if not bp: continue
                            bz = bp.get('Z')
                            if not isinstance(bz, int): continue
                            # In band but not at MinZ?
                            if mn <= bz <= mx and bz != mn:
                                misanchored.append((r['Name'], chap, mn, mx, ln, bz))

        if misanchored:
            def fix():
                for _, _, mn, _, ln, _ in misanchored:
                    bp = lm_bp.get(ln)
                    if bp and isinstance(bp.get('Z'), int):
                        bp['Z'] = mn

            # Plain-English message — what zones, what chapter, where the
            # landmark sits now vs where it would be moved.
            lines = []
            for zn, ch, mn, mx, ln, bz in misanchored:
                short_ch = ch.replace('SandboxSmall-chapter-', 'chap-')
                where = 'top of band' if bz == mx else 'middle of band'
                lines.append(
                    f'  - "{zn}" in {short_ch} (Z {mn}..{mx}). '
                    f'Its landmark "{ln}" sits at Z={bz} ({where}). '
                    f'Anchor rule says place at Z={mn}.')
            detail = (
                f'{len(misanchored)} zone(s) anchored above the floor of '
                f'their chapter:\n' + '\n'.join(lines) +
                '\nClick "Auto-fix" to clamp every flagged landmark to its '
                'host chapter MinZ. Or proceed without fixing — the engine '
                'usually tolerates this, but at the world ceiling (chap-7 '
                'MaxZ=29) or floor (chap-8 MinZ=0) it can push zones off-grid.')
            out.append(Issue(
                'warning', 'landmark_not_at_minz', 'landmarks',
                detail,
                fixer=fix,
                fixer_label='Move landmark BasePos.Z to host chapter MinZ'))
        return out

    def _check_landmark_zband_alignment(self):
        """For every Live SS zone with bPositionFromLandmarks=true, each
        attached landmark's BasePosition.Z must lie within the zone's
        chapter [MinZ, MaxZ]. Mismatch causes runtime to derive a zone
        position outside the chapter band, leading to A* router null
        deref at FMorLayoutConnectionInstance::GetZone (offset 0x1a1).

        Auto-fix: clamp landmark BasePosition.Z to the chapter MinZ."""
        out = []
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        landmarks_doc = self.docs.get('landmarks')
        if not (zones_doc and chapters_doc and landmarks_doc):
            return out

        chap_band = {}
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-chapter-'): continue
            if self._zstate(r) == 'Disabled': continue
            mn = self._get(r, 'MinZ'); mx = self._get(r, 'MaxZ')
            if isinstance(mn, int) and isinstance(mx, int):
                chap_band[n] = (mn, mx)

        # landmark name -> BasePosition struct (the inner dict with X/Y/Z)
        lm_bp = {}
        for r in landmarks_doc.rows:
            p = self._fp(r.get('Value', []), 'BasePosition')
            v = p.get('Value') if p else None
            if isinstance(v, list) and v and isinstance(v[0], dict):
                inner = v[0].get('Value')
                if isinstance(inner, dict):
                    lm_bp[r['Name']] = inner

        # For each Live SS zone with bPositionFromLandmarks=true, check landmarks
        needed = []  # (landmark_name, target_minz, target_maxz, src_zone, src_chap)
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled': continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value','')).split('::')[-1] != 'SandboxSmall':
                continue
            chap = self._get(r, 'Chapter')
            if chap not in chap_band: continue
            mn, mx = chap_band[chap]
            pflp = self._fp(r.get('Value', []), 'bPositionFromLandmarks')
            if not pflp or pflp.get('Value') is not True: continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh: continue
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict): continue
                inner = e.get('Value')
                if not isinstance(inner, list): continue
                lhprop = self._fp(inner, 'Landmark')
                if not lhprop: continue
                lv = lhprop.get('Value'); lname = ''
                if isinstance(lv, list):
                    for it in lv:
                        if isinstance(it, dict) and it.get('Name') == 'RowName':
                            lname = it.get('Value', '')
                if not lname or lname == 'None': continue
                bp = lm_bp.get(lname)
                if not bp: continue
                bpz = bp.get('Z')
                if not isinstance(bpz, int): continue
                # Exempt auto-place sentinel: BasePosition.X==0 AND Y==0
                # is the engine's "place me automatically" marker. Vanilla
                # SandboxSmall has 16 landmarks in this state and the engine
                # places them at runtime — they are NOT misaligned.
                if bp.get('X') == 0 and bp.get('Y') == 0:
                    continue
                if not (mn <= bpz <= mx):
                    needed.append((lname, mn, mx, r['Name'], chap, bpz))

        if needed:
            def fix():
                for lname, mn, mx, _zn, _ch, _bpz in needed:
                    bp = lm_bp.get(lname)
                    if bp and isinstance(bp.get('Z'), int):
                        cur = bp['Z']
                        if not (mn <= cur <= mx):
                            bp['Z'] = mn
            preview = '; '.join(
                f'{l} z={bz}->{mn} ({zn} in {ch})'
                for l, mn, mx, zn, ch, bz in needed[:3])
            # Downgraded from error to warning: with the auto-place sentinel
            # (X=0,Y=0) exemption applied above, vanilla SandboxSmall produces
            # zero hits here. Remaining hits indicate non-sentinel landmarks
            # outside their host band — flagged for review, not blocking.
            out.append(Issue(
                'warning', 'landmark_zband_misalign', 'landmarks',
                f'{len(needed)} landmark BasePosition.Z value(s) outside host '
                f'zone chapter Z band: {preview}'
                + ('…' if len(needed) > 3 else ''),
                fixer=fix,
                fixer_label='Clamp each landmark BasePosition.Z to host chapter MinZ'))
        return out

    def _check_chapterid_uniqueness(self):
        """Each Live SandboxSmall chapter must have a unique ChapterID.
        Duplicates collapse the in-game travel-stone map UI into one
        bucket and may break any routing that keys off ChapterID.
        No auto-fix: renumbering is design-level."""
        out = []
        chapters_doc = self.docs.get('chapters')
        if not chapters_doc: return out
        from collections import Counter
        ids = []
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-chapter-'): continue
            if self._zstate(r) == 'Disabled': continue
            cid = self._get(r, 'ChapterID')
            if cid is not None:
                ids.append((n, cid))
        cnt = Counter(cid for _, cid in ids)
        dups = {cid: c for cid, c in cnt.items() if c > 1}
        if dups:
            details = []
            for cid in sorted(dups):
                owners = [n for n, i in ids if i == cid]
                details.append(f'ID={cid}: {owners}')
            out.append(Issue(
                'error', 'chapterid_duplicates', 'chapters',
                f'Duplicate ChapterID values across Live SS chapters: '
                + ' | '.join(details)
                + '. Renumber so each Live SS chapter has a unique ID.'))
        return out

    def _check_chapter_displayname_resolves(self):
        """Each Live SS chapter's DisplayName must reference a key that
        exists in the World StringTable. If the StringTable doc isn't
        loaded, this check is a no-op. No auto-fix: invented strings
        require the user to choose the display text."""
        out = []
        chapters_doc = self.docs.get('chapters')
        strings_doc = self.docs.get('strings')
        if not (chapters_doc and strings_doc): return out
        # Build set of StringTable keys
        keys = set()
        try:
            for e in strings_doc.data.get('Exports', []):
                tbl = e.get('Table')
                if isinstance(tbl, dict):
                    for kv in tbl.get('Value', []) or []:
                        if isinstance(kv, list) and kv:
                            keys.add(kv[0])
        except Exception:
            return out
        if not keys: return out
        missing = []
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-chapter-'): continue
            if self._zstate(r) == 'Disabled': continue
            dn_p = self._fp(r.get('Value', []), 'DisplayName')
            if not dn_p: continue
            dn = dn_p.get('Value')
            if not dn or dn == 'None': continue
            if dn not in keys:
                missing.append((n, dn))
        if missing:
            out.append(Issue(
                'warning', 'chapter_displayname_missing', 'chapters',
                f'{len(missing)} chapter DisplayName ref(s) not in World '
                f'StringTable: ' + ', '.join(f'{n}->{dn}' for n, dn in missing[:5])
                + '. Add to World.json StringTable or fix the ref.'))
        return out

    def _check_extended_connectivity_neighbours(self):
        """Live SandboxSmall zones that carry an extended-connectivity stair
        landmark (LandmarkHandles[].bExtendedConnectivityLandmark=true) must
        have BOTH a Layer+1 and a Layer-1 Live chapter present in the stack.

        Missing either side causes the A* router in
        L2_RouteInterzoneConnections to walk into a null chapter pointer
        (crash signature: FMorLayoutConnectionInstance::GetZone, offset 0x1a1).

        No auto-fix: the right correction (move the zone, add a chapter,
        clear the flag) is design-level — flag for user."""
        out = []
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        if not (zones_doc and chapters_doc):
            return out

        # Build Live SS chapter -> Layer map. Match BOTH the legacy
        # 'SandboxSmall-chapter-N' pattern AND the new
        # 'SandboxSmall-Chapter##.<X>' pattern after the rename.
        chap_layers = {}
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not (n.startswith('SandboxSmall-chapter-')
                    or n.startswith('SandboxSmall-Chapter')):
                continue
            if self._zstate(r) == 'Disabled':
                continue
            L = None
            p = self._fp(r.get('Value', []), 'Layer')
            if p is not None:
                try: L = int(p.get('Value'))
                except (TypeError, ValueError): L = None
            if L is not None:
                chap_layers[n] = L
        live_layers = set(chap_layers.values())

        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            chap = self._get(r, 'Chapter')
            if chap not in chap_layers:
                continue
            L = chap_layers[chap]
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh:
                continue
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict):
                    continue
                inner = e.get('Value')
                if not isinstance(inner, list):
                    continue
                ext = self._fp(inner, 'bExtendedConnectivityLandmark')
                if not ext or ext.get('Value') is not True:
                    continue
                lname = ''
                lhprop = self._fp(inner, 'Landmark')
                if lhprop:
                    lv = lhprop.get('Value')
                    if isinstance(lv, list):
                        for it in lv:
                            if isinstance(it, dict) and it.get('Name') == 'RowName':
                                lname = it.get('Value', '')
                up = (L + 1) in live_layers
                dn = (L - 1) in live_layers
                if up and dn:
                    continue
                # World-edge exemption: if this zone sits at the top or bottom
                # of the live layer stack, the missing neighbour is a world
                # boundary, not a defect. Vanilla SandboxSmall ships with
                # extended-connectivity zones at the stack top/bottom and the
                # engine handles them gracefully. Only flag when the missing
                # side is INTERIOR (a gap in the middle of the stack).
                world_top = max(live_layers) if live_layers else L
                world_bottom = min(live_layers) if live_layers else L
                missing_up_is_edge = (not up) and (L >= world_top)
                missing_dn_is_edge = (not dn) and (L <= world_bottom)
                up_ok_or_edge = up or missing_up_is_edge
                dn_ok_or_edge = dn or missing_dn_is_edge
                if up_ok_or_edge and dn_ok_or_edge:
                    continue
                miss = []
                if not up_ok_or_edge: miss.append(f'Layer+1 (={L+1})')
                if not dn_ok_or_edge: miss.append(f'Layer-1 (={L-1})')
                # Downgraded from error to warning: vanilla SandboxSmall has
                # this pattern at non-edge positions and the engine tolerates
                # it. World-edge cases are exempted entirely above.
                out.append(Issue(
                    'warning', 'extended_connectivity_no_neighbour', 'zones',
                    f'{r.get("Name")} in {chap} (Layer {L:+d}) has '
                    f'extended-connectivity landmark "{lname}" but no '
                    f'{" and no ".join(miss)} chapter present. Vanilla '
                    f'tolerates this at world edges. If this floor is '
                    f'interior to your stack, consider clearing the flag '
                    f'or adding the missing neighbour chapter.'))
        return out

    def _check_extended_connectivity_z_bounds(self):
        """A zone with bExtendedConnectivityLandmark=true must keep its full
        Z extent within [Z_MIN, Z_MAX] = [0, 29]. The engine traverses the
        zone's Z range to wire connectivity to the next layer; if the
        extent goes past Z=29 (or below 0), the engine indexes outside the
        world grid and crashes.

        This is an ERROR (not a warning) because, unlike a normal zone
        bleed past the chapter MaxZ — which is harmless if no zone
        occupies those cells — an extended-connectivity zone's bleed is
        ACTIVELY WALKED by the routing pass. Out-of-bounds = null-deref.

        Auto-fix: shrink TargetSize.Z so the zone's top extent equals
        Z_MAX (=29). Mirrors z_bounds_zone_top fix but as an error.
        """
        out = []
        zones_doc = self.docs.get('zones')
        if not zones_doc:
            return out
        zmin, zmax = self.Z_MIN, self.Z_MAX

        def get_z(prop):
            if not prop: return None
            v = prop.get('Value')
            if isinstance(v, list) and v:
                inner = v[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    return inner['Value'].get('Z')
            return None

        violations = []
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs_p = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs_p or str(zs_p.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            # Skip Pos=(0,0,0) sentinel — engine places those itself
            pos = self._fp(r.get('Value', []), 'Position')
            if pos:
                pv = pos.get('Value')
                if isinstance(pv, list) and pv:
                    inner = pv[0].get('Value') if isinstance(pv[0], dict) else None
                    if isinstance(inner, dict):
                        if (inner.get('X') == 0 and inner.get('Y') == 0
                                and inner.get('Z') == 0):
                            continue

            # Does this zone carry an extended-connectivity landmark handle?
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh:
                continue
            has_ext = False
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict): continue
                inner = e.get('Value')
                if not isinstance(inner, list): continue
                ext = self._fp(inner, 'bExtendedConnectivityLandmark')
                if ext and ext.get('Value') is True:
                    has_ext = True; break
            if not has_ext:
                continue

            pz = get_z(pos)
            sz = get_z(self._fp(r.get('Value', []), 'TargetSize'))
            if not (isinstance(pz, int) and isinstance(sz, int) and sz > 0):
                continue
            top = pz + sz - 1
            if pz < zmin or top > zmax:
                violations.append((r.get('Name'), pz, sz, top))

        if violations:
            def fix_top(_v=violations, _doc=zones_doc, _zmin=zmin, _zmax=zmax):
                for n, pz, sz, top in _v:
                    for r in _doc.rows:
                        if r.get('Name') != n: continue
                        size = self._fp(r.get('Value', []), 'TargetSize')
                        # Clamp Position.Z if below; otherwise shrink Size.Z
                        # so top == Z_MAX
                        if pz < _zmin and size:
                            pos = self._fp(r.get('Value', []), 'Position')
                            pv = pos.get('Value') if pos else None
                            if isinstance(pv, list) and pv:
                                pv[0]['Value']['Z'] = _zmin
                        elif top > _zmax and size:
                            new_sz = max(1, _zmax - max(pz, _zmin) + 1)
                            sv = size.get('Value')
                            if isinstance(sv, list) and sv:
                                sv[0]['Value']['Z'] = new_sz
            preview = violations[:3]
            out.append(Issue(
                'error', 'extended_connectivity_z_bounds', 'zones',
                f'{len(violations)} extended-connectivity zone(s) extend '
                f'past Z bounds [{zmin},{zmax}]: {preview}. Engine will '
                f'null-deref while routing — this is harder than a normal '
                f'bleed warning. Either shrink TargetSize.Z to fit, or '
                f'clear the bExtendedConnectivityLandmark flag.',
                fixer=fix_top,
                fixer_label=f'Clamp extended-connectivity zone Z extent into [{zmin},{zmax}]'))
        return out

    # ----- New error-class checks added 2026-04 from Z-shift session -----
    # These catch the specific failure modes that crashed routing during
    # SS chapter Z-band shifts. Each check is ERROR severity because
    # vanilla doesn't violate them and the engine GetZone()/A* router
    # null-derefs when they fire.

    def _live_ss_chapter_cells(self):
        """Return the set of Z cells [MinZ..MaxZ] covered by any Live SS
        chapter row. Used by all the Z-band-membership checks below."""
        cells = set()
        chapters_doc = self.docs.get('chapters')
        if not chapters_doc: return cells
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not (n.startswith('SandboxSmall-Chapter') or
                    n.startswith('SandboxSmall-chapter')):
                continue
            if self._zstate(r) == 'Disabled': continue
            mn = self._get(r, 'MinZ'); mx = self._get(r, 'MaxZ')
            if isinstance(mn, int) and isinstance(mx, int):
                for z in range(mn, mx + 1):
                    cells.add(z)
        return cells

    def _check_zone_preferred_z_in_band(self):
        """Discovered 2026-04: every Live SS zone with PreferredZOverride >= 0
        must have that value within SOME Live SS chapter's Z band. The engine
        uses PreferredZOverride to force zone placement at a specific Z; if
        that Z isn't covered by any chapter, GetZone() null-derefs.

        The -1 sentinel means 'no override' and is always valid.
        Auto-fix: shift the override value to the nearest in-band Z."""
        out = []
        zones_doc = self.docs.get('zones')
        if not zones_doc: return out
        cells = self._live_ss_chapter_cells()
        if not cells: return out
        bad = []
        for r in zones_doc.rows:
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            if self._zstate(r) == 'Disabled': continue
            p = self._fp(r.get('Value', []), 'PreferredZOverride')
            if not p: continue
            v = p.get('Value')
            if not isinstance(v, int) or v < 0:
                continue  # -1 sentinel = no override
            if v not in cells:
                bad.append((r.get('Name'), v))
        if bad:
            preview = ', '.join(f'{n}={v}' for n, v in bad[:5])
            out.append(Issue(
                'error', 'zone_preferred_z_in_band', 'zones',
                f'{len(bad)} Live SS zone(s) with PreferredZOverride pointing '
                f'at a Z cell not covered by any Live SS chapter — '
                f'engine GetZone() will null-deref at routing time. '
                f'Examples: {preview}'))
        return out

    def _check_nested_subcell_z_in_band(self):
        """Discovered 2026-04: LayoutConnections have NESTED Subcell IntVectors
        inside OriginInterface and DestinationInterface. Their Z components
        (when non-zero) must point at a Live SS chapter's Z band, otherwise
        the A* router null-derefs.

        These were NOT a top-level Subcell field on the row — easy to miss
        when shifting Z values by hand."""
        out = []
        conns_doc = self.docs.get('connections')
        if not conns_doc: return out
        cells = self._live_ss_chapter_cells()
        if not cells: return out
        bad = []
        for r in conns_doc.rows:
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            if self._zstate(r) == 'Disabled': continue
            for fld in ('OriginInterface', 'DestinationInterface'):
                ifprop = self._fp(r.get('Value', []), fld)
                if not ifprop: continue
                v = ifprop.get('Value')
                if not isinstance(v, list): continue
                for inner in v:
                    if not isinstance(inner, dict): continue
                    if inner.get('Name') != 'Subcell': continue
                    sc_v = inner.get('Value')
                    if not isinstance(sc_v, list) or not sc_v: continue
                    d = sc_v[0].get('Value') if isinstance(sc_v[0], dict) else None
                    if not isinstance(d, dict): continue
                    z = d.get('Z')
                    if not isinstance(z, int) or z == 0: continue
                    if z not in cells:
                        bad.append((r.get('Name'), fld, z))
        if bad:
            preview = ', '.join(f'{n}.{f}.Z={z}' for n, f, z in bad[:5])
            out.append(Issue(
                'error', 'nested_subcell_z_in_band', 'connections',
                f'{len(bad)} nested Subcell.Z value(s) in Live SS connections '
                f'point at uncovered Z cells — A* router will null-deref. '
                f'Examples: {preview}'))
        return out

    def _check_ss_landmark_bp_in_band(self):
        """Discovered 2026-04: Sandbox-namespaced landmarks (and the bridge
        landmarks DurinsTower/TradingPost/DimrillDale) with non-sentinel
        BasePosition must have BP.Z within some Live SS chapter band.

        The previous landmark_zband_alignment check only flagged landmarks
        hosted via LandmarkHandles. This catches ORPHAN SS landmarks that
        the engine still loads and routes around."""
        out = []
        lm_doc = self.docs.get('landmarks')
        if not lm_doc: return out
        cells = self._live_ss_chapter_cells()
        if not cells: return out

        ss_namespaces = ('Sandbox.',)
        ss_bridge_names = {'TradingPost', 'DurinsTower', 'DimrillDale',
                            'Sandbox_DurinsTower', 'Sandbox_TradingPost',
                            'Sandbox_DimrillDale'}
        bad = []
        for r in lm_doc.rows:
            n = r.get('Name', '')
            if not (n.startswith(ss_namespaces) or n in ss_bridge_names):
                continue
            if self._zstate(r) == 'Disabled': continue
            bp_p = self._fp(r.get('Value', []), 'BasePosition')
            if not bp_p: continue
            v = bp_p.get('Value')
            if not isinstance(v, list) or not v: continue
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if not isinstance(d, dict): continue
            x = d.get('X'); y = d.get('Y'); z = d.get('Z')
            if x == 0 and y == 0:
                continue  # auto-place sentinel
            if not isinstance(z, int): continue
            if z not in cells:
                bad.append((n, z))
        if bad:
            preview = ', '.join(f'{n}.BP.Z={z}' for n, z in bad[:5])
            out.append(Issue(
                'error', 'ss_landmark_bp_in_band', 'landmarks',
                f'{len(bad)} Sandbox-namespaced landmark(s) with '
                f'BasePosition.Z outside any Live SS chapter band — '
                f'engine routing may null-deref. Examples: {preview}'))
        return out

    @staticmethod
    def _xyz_from_struct_prop(p):
        """Extract (X, Y, Z) tuple of ints from a Vector-like StructProperty.
        Returns None if structure missing or fields not int-like."""
        if not p:
            return None
        v = p.get('Value')
        if not isinstance(v, list) or not v:
            return None
        d = v[0].get('Value') if isinstance(v[0], dict) else None
        if not isinstance(d, dict):
            return None
        return (d.get('X'), d.get('Y'), d.get('Z'))

    def _stair_zones_and_landmark_names(self):
        """Helper shared by chapter_stair_uniqueness and stair_xy_collision.

        Walks every Live SandboxSmall zone and returns a list of tuples:
            (zone_row, chapter_rowname, [stair_landmark_names])
        for any zone that has at least one LandmarkHandles entry whose
        bExtendedConnectivityLandmark=true (i.e. a stair zone). Also
        returns the union set of stair landmark RowNames across all stair
        zones.

        Filter: a zone counts as a stair only if its TargetSize.Z >= 2.
        Single-Z zones (e.g. vanilla City_A_EasternBastion) may carry an
        extended-connectivity landmark for routing reasons but are NOT
        elevator-style multi-floor stairs and must not be classified as
        such — they cannot collide with real stairs in the chapter
        uniqueness sense."""
        zones_doc = self.docs.get('zones')
        stair_zones = []
        all_lm_names = set()
        if not zones_doc:
            return stair_zones, all_lm_names
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            # Filter: TargetSize.Z >= 2 to exclude single-floor zones that
            # happen to carry an extended-connectivity landmark.
            ts = self._fp(r.get('Value', []), 'TargetSize')
            ts_xyz = self._xyz_from_struct_prop(ts)
            if not ts_xyz or not isinstance(ts_xyz[2], int) or ts_xyz[2] < 2:
                continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh:
                continue
            lm_names_for_zone = []
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict):
                    continue
                inner = e.get('Value')
                if not isinstance(inner, list):
                    continue
                ext = self._fp(inner, 'bExtendedConnectivityLandmark')
                if not ext or ext.get('Value') is not True:
                    continue
                lhprop = self._fp(inner, 'Landmark')
                lname = ''
                if lhprop:
                    lv = lhprop.get('Value')
                    if isinstance(lv, list):
                        for it in lv:
                            if isinstance(it, dict) and it.get('Name') == 'RowName':
                                lname = it.get('Value', '')
                if lname:
                    lm_names_for_zone.append(lname)
            if lm_names_for_zone:
                chap = self._get(r, 'Chapter')
                stair_zones.append((r, chap, lm_names_for_zone))
                all_lm_names.update(lm_names_for_zone)
        return stair_zones, all_lm_names

    def _check_chapter_stair_uniqueness(self):
        """Each Live SS chapter must be the primary `Chapter` of at most ONE
        unique-footprint stair zone (a Live SS zone with TargetSize.Z >= 2
        and at least one LandmarkHandles entry whose
        bExtendedConnectivityLandmark=true). Multiple stair zones on the
        same chapter cause runtime cell collisions / generator bugs.

        Deduplication: stair zones sharing the SAME (Position, TargetSize)
        are treated as a single "variant slot" — vanilla SandboxSmall ships
        Elevator_E and Elevator_F as alt variants with identical pos+size
        (one is picked per game seed), so they must not double-count."""
        out = []
        stair_zones, _ = self._stair_zones_and_landmark_names()
        if not stair_zones:
            return out
        zones_doc = self.docs.get('zones')
        # Collect (zone_name, footprint) per chapter; dedupe by footprint.
        by_chapter = {}
        for zrow, chap, _lms in stair_zones:
            if not chap or chap == 'None':
                continue
            pos = self._fp(zrow.get('Value', []), 'Position')
            ts = self._fp(zrow.get('Value', []), 'TargetSize')
            footprint = (
                self._xyz_from_struct_prop(pos),
                self._xyz_from_struct_prop(ts),
            )
            by_chapter.setdefault(chap, []).append((zrow.get('Name'), footprint))
        for chap, entries in by_chapter.items():
            # Group by footprint; each unique footprint counts once.
            by_fp = {}
            for zname, fp_key in entries:
                by_fp.setdefault(fp_key, []).append(zname)
            unique_slots = len(by_fp)
            if unique_slots >= 2:
                # Build a per-slot summary for the message.
                slot_desc = []
                for fp_key, znames in by_fp.items():
                    if len(znames) > 1:
                        slot_desc.append(
                            f'{{variant slot {fp_key}: {sorted(znames)}}}')
                    else:
                        slot_desc.append(znames[0])
                all_znames = sorted(n for _, ns in by_fp.items() for n in ns)
                out.append(Issue(
                    'error', 'chapter_stair_uniqueness', 'zones',
                    f'Chapter "{chap}" is the primary Chapter of '
                    f'{unique_slots} distinct stair-footprint slots '
                    f'(zones: {all_znames}; slots: {slot_desc}). '
                    f'Each chapter row must host at most ONE stair-footprint '
                    f'slot — multiple stairs on the same chapter cause '
                    f'runtime AllocateCellToParcel "cell already allocated" '
                    f'errors. (Variants sharing identical Position+TargetSize '
                    f'count as ONE slot — vanilla pattern.)'))
        return out

    def _check_stair_xy_collision(self):
        """No two stair landmarks (BPs of zones with
        bExtendedConnectivityLandmark=true) may share both BasePosition.X AND
        BasePosition.Y. Same X+Y at different Z = vertical column collision
        in AllocateCellToParcel.

        Sentinel (X==0 AND Y==0) means "auto-place" — flagged separately as
        a warning (stair_xy_sentinel_overlap) because runtime placement of
        multiple (0,0,*) stairs may still collide."""
        out = []
        lm_doc = self.docs.get('landmarks')
        if not lm_doc:
            return out
        _, stair_lm_names = self._stair_zones_and_landmark_names()
        if not stair_lm_names:
            return out

        # landmark name -> (X, Y, Z)
        positions = {}
        for r in lm_doc.rows:
            n = r.get('Name', '')
            if n not in stair_lm_names:
                continue
            if self._zstate(r) == 'Disabled':
                continue
            bp_p = self._fp(r.get('Value', []), 'BasePosition')
            if not bp_p:
                continue
            v = bp_p.get('Value')
            if not isinstance(v, list) or not v:
                continue
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if not isinstance(d, dict):
                continue
            x = d.get('X'); y = d.get('Y'); z = d.get('Z')
            if not (isinstance(x, int) and isinstance(y, int)):
                continue
            positions[n] = (x, y, z)

        # Group by (X, Y)
        by_xy = {}
        for name, (x, y, z) in positions.items():
            by_xy.setdefault((x, y), []).append((name, z))

        # Real collisions (non-sentinel): X,Y not (0,0) and >=2 landmarks
        for (x, y), entries in by_xy.items():
            if x == 0 and y == 0:
                continue
            if len(entries) >= 2:
                names_zs = ', '.join(f'{n}(Z={z})' for n, z in sorted(entries))
                out.append(Issue(
                    'error', 'stair_xy_collision', 'landmarks',
                    f'{len(entries)} stair landmarks share '
                    f'BasePosition (X={x}, Y={y}) — vertical-column '
                    f'collision causes AllocateCellToParcel '
                    f'"cell already allocated" errors. Landmarks: '
                    f'{names_zs}. Set distinct X,Y per stair to fix.'))

        # Sentinel overlap warning
        sentinel_entries = by_xy.get((0, 0), [])
        if len(sentinel_entries) >= 2:
            names_zs = ', '.join(f'{n}(Z={z})' for n, z in sorted(sentinel_entries))
            out.append(Issue(
                'warning', 'stair_xy_sentinel_overlap', 'landmarks',
                f'{len(sentinel_entries)} stair landmarks use sentinel '
                f'(0,0,Z) — runtime auto-placement may collide with other '
                f'(0,0,*) stairs. Set explicit X,Y to prevent. '
                f'Landmarks: {names_zs}.'))

        return out

    def _check_embedded_bottom_needs_headroom(self):
        """Live SandboxSmall zones whose name contains 'DarkestDeeps' are
        embedded-bottom zones that extend BELOW their host chapter's
        PrimeZ. Their host chapter MUST have MinZ < PrimeZ (i.e. real
        headroom below PrimeZ). If MinZ >= PrimeZ, there's no chapter
        band beneath the embedded floor and the generator either crashes
        or routes connections through cells that don't exist.

        Fix: either expand the host chapter's Z band so MinZ < PrimeZ,
        or move the zone to a chapter that already has room below."""
        out = []
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        if not zones_doc or not chapters_doc:
            return out
        # Build chapter lookup by row Name.
        chap_by_name = {}
        for r in chapters_doc.rows:
            n = r.get('Name')
            if n:
                chap_by_name[n] = r
        for r in zones_doc.rows:
            zname = r.get('Name', '') or ''
            if 'DarkestDeeps' not in zname:
                continue
            if self._zstate(r) == 'Disabled':
                continue
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            chap_name = self._get(r, 'Chapter')
            if not chap_name or chap_name == 'None':
                continue
            chap_row = chap_by_name.get(chap_name)
            if not chap_row:
                continue
            min_z = self._get(chap_row, 'MinZ')
            prime_z = self._get(chap_row, 'PrimeZ')
            try:
                mn = int(min_z); pz = int(prime_z)
            except (TypeError, ValueError):
                continue
            if mn >= pz:
                out.append(Issue(
                    'error', 'embedded_bottom_needs_headroom', 'zones',
                    f'Zone "{zname}" is embedded-bottom (DarkestDeeps) on '
                    f'chapter "{chap_name}" but chapter has MinZ={mn} >= '
                    f'PrimeZ={pz} — no headroom below PrimeZ for the zone '
                    f'to extend into. Expand the chapter band so MinZ < '
                    f'PrimeZ, or move the zone to a chapter with room '
                    f'below.'))
        return out

    def _check_orphan_added_data(self):
        """Find StringTable entries, landmarks, and chapter rows that the
        user added (not in vanilla) but no longer reference anything Live.
        Loads the .original.json sidecars next to each DT to determine
        which rows are vanilla (always preserved) vs user-added.

        Vanilla content is NEVER flagged — even if currently unreferenced.
        Only items the user introduced that are now orphaned get listed.

        Auto-fix: remove the orphan rows + sync NameMaps. Reversible via
        backup. Fired as a WARNING (not an error) so build can proceed."""
        out = []

        def load_sidecar(stem):
            try:
                import os
                p = os.path.join(os.path.dirname(self.docs[stem].json_path)
                                 if stem in self.docs and self.docs[stem].json_path
                                 else '',
                                 f'{stem.split("/")[-1]}.original.json')
                # Fallback: try direct neighbour
                if not os.path.exists(p):
                    return None
                import json
                return json.load(open(p, encoding='utf-8'))
            except Exception:
                return None

        # Determine sidecar paths from each loaded doc
        def sidecar_for(doc_key):
            doc = self.docs.get(doc_key)
            if not doc or not getattr(doc, 'json_path', None):
                return None
            sp = str(doc.json_path).replace('.json', '.original.json')
            try:
                import os, json
                if not os.path.exists(sp):
                    return None
                return json.load(open(sp, encoding='utf-8'))
            except Exception:
                return None

        W_van = sidecar_for('strings')
        lm_van = sidecar_for('landmarks')
        ch_van = sidecar_for('chapters')

        # Collect referenced text keys across every loaded DT
        referenced_text = set()
        def walk_text(obj):
            if isinstance(obj, dict):
                if ('TextPropertyData' in str(obj.get('$type', ''))
                        and obj.get('HistoryType') == 'StringTableEntry'):
                    v = obj.get('Value')
                    if v: referenced_text.add(str(v))
                for vv in obj.values(): walk_text(vv)
            elif isinstance(obj, list):
                for it in obj: walk_text(it)
        for k in ('zones', 'landmarks', 'chapters', 'biomes',
                  'connections', 'decks', 'filters', 'templates'):
            d = self.docs.get(k)
            if d and d.data is not None: walk_text(d.data)

        orphan_strings = set()
        if W_van and self.docs.get('strings') and self.docs['strings'].data:
            cur_keys = {e[0] for e in
                self.docs['strings'].data['Exports'][0]['Table']['Value']
                if isinstance(e, list) and len(e) >= 1}
            van_keys = {e[0] for e in
                W_van['Exports'][0]['Table']['Value']
                if isinstance(e, list) and len(e) >= 1}
            orphan_strings = (cur_keys - van_keys) - referenced_text

        # Landmark orphans
        orphan_lm = set()
        if lm_van and self.docs.get('landmarks') and self.docs['landmarks'].data:
            cur_lm = {r['Name']
                      for r in self.docs['landmarks'].data['Exports'][0]['Table']['Data']}
            van_lm = {r['Name'] for r in lm_van['Exports'][0]['Table']['Data']}
            added_lm = cur_lm - van_lm
            referenced_lm = set()
            zd = self.docs.get('zones')
            if zd and zd.data:
                for r in zd.data['Exports'][0]['Table']['Data']:
                    if self._zstate(r) == 'Disabled': continue
                    lh = self._fp(r.get('Value', []), 'LandmarkHandles')
                    for e in (lh.get('Value') or []) if lh else []:
                        for sub in e.get('Value') or []:
                            if isinstance(sub, dict) and sub.get('Name') == 'Landmark':
                                lv = sub.get('Value')
                                if isinstance(lv, list):
                                    for it in lv:
                                        if isinstance(it, dict) and it.get('Name') == 'RowName':
                                            rn = it.get('Value')
                                            if rn: referenced_lm.add(rn)
            cd = self.docs.get('connections')
            if cd and cd.data:
                for r in cd.data['Exports'][0]['Table']['Data']:
                    if self._zstate(r) == 'Disabled': continue
                    for fname in ('OriginLandmark', 'DestinationLandmark'):
                        rn = self._get(r, fname)
                        if rn: referenced_lm.add(rn)
            ld = self.docs.get('landmarks')
            if ld and ld.data:
                for r in ld.data['Exports'][0]['Table']['Data']:
                    if self._zstate(r) == 'Disabled': continue
                    gc = self._fp(r.get('Value', []), 'GuaranteedConnections')
                    for entry in (gc.get('Value') or []) if gc else []:
                        for sub in entry.get('Value') or []:
                            if isinstance(sub, dict) and sub.get('Name') == 'TagName':
                                tn = sub.get('Value', '').replace('World.Landmark.', '')
                                if tn: referenced_lm.add(tn)
            orphan_lm = added_lm - referenced_lm

        # Chapter row orphans
        orphan_ch = set()
        if ch_van and self.docs.get('chapters') and self.docs['chapters'].data:
            cur_ch = {r['Name']
                      for r in self.docs['chapters'].data['Exports'][0]['Table']['Data']}
            van_ch = {r['Name'] for r in ch_van['Exports'][0]['Table']['Data']}
            added_ch = cur_ch - van_ch
            referenced_ch = set()
            zd = self.docs.get('zones')
            if zd and zd.data:
                for r in zd.data['Exports'][0]['Table']['Data']:
                    if self._zstate(r) == 'Disabled': continue
                    rn = self._get(r, 'Chapter')
                    if rn: referenced_ch.add(rn)
                    ac = self._fp(r.get('Value', []), 'AdditionalChapters')
                    for entry in (ac.get('Value') or []) if ac else []:
                        if isinstance(entry, dict):
                            for sub in entry.get('Value') or []:
                                if isinstance(sub, dict) and sub.get('Name') == 'RowName':
                                    rn = sub.get('Value')
                                    if rn: referenced_ch.add(rn)
            # Exclude SandboxSmall LEVEL IDENTITY rows from orphan detection.
            # Pattern: 'SandboxSmall-Chapter##.LevelN' or '...DeepN' (post-rename)
            # plus the legacy 'SandboxSmall-chapter-N' lowercase form. These
            # rows are NEVER referenced by zones — zones use anchored sibling
            # rows (Chapter##.<ZoneName>) sharing the level row's CID. The
            # engine consumes level rows implicitly via the layer system, so
            # they look orphan to ref-counting but removing them breaks
            # generation on that layer (Lv-N stops rendering).
            def _is_level_identity(name):
                if name.startswith('SandboxSmall-chapter-'):
                    return True
                if name.startswith('SandboxSmall-Chapter') and '.' in name:
                    tail = name.split('.', 1)[1]
                    return tail.startswith('Level') or tail.startswith('Deep')
                return False
            orphan_ch = (added_ch - referenced_ch) - {
                n for n in added_ch if _is_level_identity(n)
            }

        total = len(orphan_strings) + len(orphan_lm) + len(orphan_ch)
        if total == 0:
            return out

        def fix_orphans(_strs=orphan_strings, _lms=orphan_lm, _chs=orphan_ch):
            # Strip from World StringTable
            wd = self.docs.get('strings')
            if wd and wd.data and _strs:
                wd.data['Exports'][0]['Table']['Value'] = [
                    e for e in wd.data['Exports'][0]['Table']['Value']
                    if not (isinstance(e, list) and len(e) >= 1 and e[0] in _strs)
                ]
                # Sync NameMap
                nm = wd.data.get('NameMap', [])
                wd.data['NameMap'] = [n for n in nm if n not in _strs]
            # Strip orphan landmarks
            ld = self.docs.get('landmarks')
            if ld and ld.data and _lms:
                ld.data['Exports'][0]['Table']['Data'] = [
                    r for r in ld.data['Exports'][0]['Table']['Data']
                    if r['Name'] not in _lms
                ]
                nm = ld.data.get('NameMap', [])
                ld.data['NameMap'] = [n for n in nm if n not in _lms]
            # Strip orphan chapter rows
            cd = self.docs.get('chapters')
            if cd and cd.data and _chs:
                cd.data['Exports'][0]['Table']['Data'] = [
                    r for r in cd.data['Exports'][0]['Table']['Data']
                    if r['Name'] not in _chs
                ]
                nm = cd.data.get('NameMap', [])
                cd.data['NameMap'] = [n for n in nm if n not in _chs]

        out.append(Issue(
            'warning', 'orphan_added_data', 'cleanup',
            f'{total} user-added items are orphan: '
            f'{len(orphan_strings)} StringTable entries, '
            f'{len(orphan_lm)} landmarks, '
            f'{len(orphan_ch)} chapter rows. '
            f'Vanilla content excluded. Auto-fix removes them and syncs '
            f'NameMaps.',
            fixer=fix_orphans,
            fixer_label=f'Remove {total} orphan items + sync NameMaps'))
        return out

    def _check_live_to_disabled(self):
        """Live zones referencing chapters/landmarks/decks that are Disabled.
        Auto-fix: re-enable referenced rows (best-effort — may not match
        user intent, so this is a warning + suggestion, not silent fix)."""
        out = []
        zones_doc = self.docs.get('zones')
        chapters_doc = self.docs.get('chapters')
        landmarks_doc = self.docs.get('landmarks')
        decks_doc = self.docs.get('decks')
        if not zones_doc:
            return out

        disabled_chap = self._rowset(chapters_doc) - self._live_rowset(chapters_doc)
        disabled_lm = self._rowset(landmarks_doc) - self._live_rowset(landmarks_doc)
        disabled_deck = self._rowset(decks_doc) - self._live_rowset(decks_doc)
        # Disabled zone set — for catching Live zone -> Disabled zone refs
        # via ParentZone / SlideToZone. Crashes the parcelizer at
        # MorLayoutParcelizer.cpp:213 (TMergeSort lambda) with a null
        # FZoneDefinition* dereference.
        disabled_zone = self._rowset(zones_doc) - self._live_rowset(zones_doc)

        risky_chap = set()
        risky_lm = set()
        risky_deck = set()
        risky_zone_refs = []  # (live_zone_name, field, disabled_zone_target)
        risky_lines = []
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            n = r.get('Name')
            c = self._get(r, 'Chapter')
            if c in disabled_chap:
                risky_chap.add(c)
                risky_lines.append((n, 'Chapter', c))
            for fld in ('BubbleDeck', 'PassageDeck'):
                d = self._get(r, fld)
                if d in disabled_deck:
                    risky_deck.add(d)
                    risky_lines.append((n, fld, d))
            for fld in ('ParentZone', 'SlideToZone'):
                d = self._get(r, fld)
                if d and d != 'None' and d in disabled_zone:
                    risky_zone_refs.append((n, fld, d))
                    risky_lines.append((n, fld, d))
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if lh:
                for e in (lh.get('Value') or []):
                    inner = e.get('Value') if isinstance(e, dict) else None
                    if isinstance(inner, list):
                        for sub in inner:
                            if isinstance(sub, dict) and sub.get('Name') == 'Landmark':
                                for it in (sub.get('Value') or []):
                                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                                        v = it.get('Value', '')
                                        if v in disabled_lm:
                                            risky_lm.add(v)
                                            risky_lines.append((n, 'Landmark', v))

        if risky_lines:
            def fix():
                # Clear Live zone -> Disabled zone refs to None (safer than
                # re-enabling the disabled zone, which may have been disabled
                # intentionally).
                for live_zone_name, fld, _disabled_target in risky_zone_refs:
                    for r in zones_doc.rows:
                        if r.get('Name') != live_zone_name: continue
                        p = self._fp(r.get('Value', []), fld)
                        if not p: continue
                        v = p.get('Value')
                        if isinstance(v, list):
                            for it in v:
                                if isinstance(it, dict) and it.get('Name') == 'RowName':
                                    it['Value'] = 'None'
                        break
                # Make sure 'None' is in NameMap (in case it wasn't)
                nm = zones_doc.data.get('NameMap', [])
                if 'None' not in nm:
                    nm.append('None')
                    zones_doc.data['NamesReferencedFromExportDataCount'] = len(nm)
                    g = zones_doc.data.get('Generations') or []
                    if g and isinstance(g[0], dict): g[0]['NameCount'] = len(nm)
                # Re-enable every row that's referenced by a Live zone
                for doc, names in ((chapters_doc, risky_chap),
                                   (landmarks_doc, risky_lm),
                                   (decks_doc, risky_deck)):
                    if not (doc and names):
                        continue
                    for r in doc.rows:
                        if r.get('Name') not in names:
                            continue
                        p = self._fp(r.get('Value', []), 'EnabledState')
                        if p and p.get('Value') == 'ERowEnabledState::Disabled':
                            p['Value'] = 'ERowEnabledState::Live'
                    # Make sure ::Live is in NameMap
                    nm = doc.data.get('NameMap', [])
                    if 'ERowEnabledState::Live' not in nm:
                        nm.append('ERowEnabledState::Live')
                        n = len(nm)
                        doc.data['NamesReferencedFromExportDataCount'] = n
                        g = doc.data.get('Generations') or []
                        if g and isinstance(g[0], dict):
                            g[0]['NameCount'] = n

            preview = '; '.join(
                f'{n}.{f}->{v}' for n,f,v in risky_zone_refs[:3])
            zone_msg = (f' ZONE refs to Disabled (parcelizer-crash-class): '
                        f'{len(risky_zone_refs)}'
                        + (f' [{preview}]' if risky_zone_refs else ''))
            out.append(Issue(
                'error' if risky_zone_refs else 'warning', 'live_to_disabled', 'zones',
                f'{len(risky_lines)} Live zone ref(s) target Disabled rows '
                f'(chap={len(risky_chap)}, lm={len(risky_lm)}, '
                f'deck={len(risky_deck)}, zone={len(risky_zone_refs)}).'
                + zone_msg,
                fixer=fix,
                fixer_label='Clear zone refs to None + re-enable other Disabled targets'))
        return out

    # ----- Stair / chapter / landmark health checks (added 2026-04) -----
    # These checks were added during the 14-floor stair build. They catch
    # mistakes specific to constructing multi-floor stair architectures:
    # bubbles that would extend past Z=29, chapter Layer gaps, dead chapters
    # with no zones, and Live landmarks without a host zone.

    def _check_stair_bubble_z_oob(self):
        """Each stair zone (Live SS, bPositionFromLandmarks=true, exactly one
        LandmarkHandle with bExtendedConnectivityLandmark=true) extends UP
        from its landmark BP.Z by TargetSize.Z. The bubble Z range is
        [BP.Z .. BP.Z + TS.Z - 1]; if it leaves [0..29], engine null-derefs
        while routing past the world edge.

        Auto-fix: shrink TargetSize.Z so bubble top == Z_MAX (29).
        """
        out = []
        zones_doc = self.docs.get('zones')
        landmarks_doc = self.docs.get('landmarks')
        if not (zones_doc and landmarks_doc):
            return out
        zmin, zmax = self.Z_MIN, self.Z_MAX

        # Build landmark BP.Z lookup
        lm_bp = {}
        for r in landmarks_doc.rows:
            n = r.get('Name')
            if not n:
                continue
            bp_p = self._fp(r.get('Value', []), 'BasePosition')
            if not bp_p: continue
            v = bp_p.get('Value')
            if isinstance(v, list) and v:
                inner = v[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    lm_bp[n] = inner['Value'].get('Z')

        violations = []
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs_p = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs_p or str(zs_p.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            pflm = self._fp(r.get('Value', []), 'bPositionFromLandmarks')
            if not (pflm and pflm.get('Value') is True):
                continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh:
                continue
            lh_entries = lh.get('Value') or []
            if len(lh_entries) != 1:
                continue
            entry = lh_entries[0]
            inner = entry.get('Value') if isinstance(entry, dict) else None
            if not isinstance(inner, list):
                continue
            ext_p = self._fp(inner, 'bExtendedConnectivityLandmark')
            if not (ext_p and ext_p.get('Value') is True):
                continue
            # Find Landmark.RowName
            lm_p = self._fp(inner, 'Landmark')
            if not lm_p:
                continue
            lm_name = None
            sub = lm_p.get('Value')
            if isinstance(sub, list):
                for s in sub:
                    if isinstance(s, dict) and s.get('Name') == 'RowName':
                        lm_name = s.get('Value'); break
            if not lm_name or lm_name in self.NULL_FNAMES:
                continue
            bp_z = lm_bp.get(lm_name)
            ts_p = self._fp(r.get('Value', []), 'TargetSize')
            ts_z = None
            if ts_p:
                tv = ts_p.get('Value')
                if isinstance(tv, list) and tv:
                    tin = tv[0]
                    if isinstance(tin, dict) and isinstance(tin.get('Value'), dict):
                        ts_z = tin['Value'].get('Z')
            if not (isinstance(bp_z, int) and isinstance(ts_z, int) and ts_z > 0):
                continue
            top = bp_z + ts_z - 1
            if bp_z < zmin or top > zmax:
                violations.append((r.get('Name'), lm_name, bp_z, ts_z, top))

        if violations:
            def fix(_v=violations, _doc=zones_doc, _zmax=zmax):
                for zname, lname, bp_z, ts_z, top in _v:
                    for r in _doc.rows:
                        if r.get('Name') != zname: continue
                        ts_p = self._fp(r.get('Value', []), 'TargetSize')
                        if ts_p:
                            tv = ts_p.get('Value')
                            if isinstance(tv, list) and tv:
                                tin = tv[0]
                                if isinstance(tin, dict) and isinstance(tin.get('Value'), dict):
                                    new_sz = max(1, _zmax - bp_z + 1)
                                    tin['Value']['Z'] = new_sz
            preview = violations[:3]
            out.append(Issue(
                'error', 'stair_bubble_z_oob', 'zones',
                f'{len(violations)} stair zone(s) have bubble Z extending past '
                f'world bounds [{zmin},{zmax}]: {preview}. Engine null-derefs '
                f'while routing across world edges.',
                fixer=fix,
                fixer_label=f'Shrink TargetSize.Z so bubble top fits in [{zmin},{zmax}]'))
        return out

    def _check_chapter_layer_continuity(self):
        """Live SS chapter Layer values should be sequential (no gaps) for
        clean traversal. Engine handles gaps but they often signal a missing
        floor. WARNING."""
        out = []
        chapters_doc = self.docs.get('chapters')
        if not chapters_doc:
            return out
        layers = []
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.lower().startswith('sandboxsmall-chapter'):
                continue
            if self._zstate(r) == 'Disabled':
                continue
            layer = self._get(r, 'Layer')
            if isinstance(layer, int):
                layers.append((layer, n))
        if not layers:
            return out
        layer_set = {l for l, _ in layers}
        lo, hi = min(layer_set), max(layer_set)
        missing = [l for l in range(lo, hi + 1) if l not in layer_set]
        if missing:
            out.append(Issue(
                'warning', 'chapter_layer_continuity', 'chapters',
                f'Live SS chapter Layer sequence has gap(s): missing {missing} '
                f'in range [{lo}..{hi}]. {len(layers)} chapters present.'))
        return out

    def _check_chapter_has_at_least_one_zone(self):
        """Each Live SS chapter row should be referenced by at least one
        Live SS zone via Chapter or AdditionalChapters. Otherwise the chapter
        is dead weight — won't render. WARNING."""
        out = []
        chapters_doc = self.docs.get('chapters')
        zones_doc = self.docs.get('zones')
        if not (chapters_doc and zones_doc):
            return out
        # Live SS chapter names
        live_chaps = set()
        for r in chapters_doc.rows:
            n = r.get('Name', '')
            if not n.lower().startswith('sandboxsmall-chapter'):
                continue
            if self._zstate(r) == 'Disabled':
                continue
            live_chaps.add(n)
        # Build referenced set
        referenced = set()
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs_p = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs_p or str(zs_p.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            chap_p = self._fp(r.get('Value', []), 'Chapter')
            if chap_p:
                cv = chap_p.get('Value')
                if isinstance(cv, list):
                    for s in cv:
                        if isinstance(s, dict) and s.get('Name') == 'RowName':
                            v = s.get('Value')
                            if v and v not in self.NULL_FNAMES:
                                referenced.add(v)
            achap_p = self._fp(r.get('Value', []), 'AdditionalChapters')
            if achap_p:
                for e in (achap_p.get('Value') or []):
                    inner = e.get('Value') if isinstance(e, dict) else None
                    if isinstance(inner, list):
                        for s in inner:
                            if isinstance(s, dict) and s.get('Name') == 'RowName':
                                v = s.get('Value')
                                if v and v not in self.NULL_FNAMES:
                                    referenced.add(v)
        unused = sorted(live_chaps - referenced)
        if unused:
            out.append(Issue(
                'warning', 'chapter_has_at_least_one_zone', 'chapters',
                f'{len(unused)} Live SS chapter(s) have no Live SS zone '
                f'referencing them: {unused[:5]}. They are dead weight — '
                f'won\'t render.'))
        return out

    def _check_live_landmark_has_host(self):
        """Each Live Sandbox-namespaced landmark should be referenced by at
        least one Live SS zone's LandmarkHandles. Engine still loads orphans
        but they have no physical presence in the world. WARNING."""
        out = []
        landmarks_doc = self.docs.get('landmarks')
        zones_doc = self.docs.get('zones')
        if not (landmarks_doc and zones_doc):
            return out
        live_sandbox_lm = set()
        for r in landmarks_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('Sandbox.'):
                continue
            if self._zstate(r) == 'Disabled':
                continue
            live_sandbox_lm.add(n)
        # Build referenced set
        referenced = set()
        for r in zones_doc.rows:
            if self._zstate(r) == 'Disabled':
                continue
            zs_p = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs_p or str(zs_p.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh: continue
            for e in (lh.get('Value') or []):
                inner = e.get('Value') if isinstance(e, dict) else None
                if not isinstance(inner, list): continue
                lm_p = self._fp(inner, 'Landmark')
                if not lm_p: continue
                sub = lm_p.get('Value')
                if isinstance(sub, list):
                    for s in sub:
                        if isinstance(s, dict) and s.get('Name') == 'RowName':
                            v = s.get('Value')
                            if v and v not in self.NULL_FNAMES:
                                referenced.add(v)
        orphans = sorted(live_sandbox_lm - referenced)
        if orphans:
            out.append(Issue(
                'warning', 'live_landmark_has_host', 'landmarks',
                f'{len(orphans)} Live Sandbox.* landmark(s) have no host '
                f'Live SS zone via LandmarkHandles: {orphans[:5]}. Engine '
                f'still loads them but they have no physical presence.'))
        return out

    # --- registry + run -------------------------------------------------

    @property
    def CHECKS(self):
        # Order matters: counter_sync runs LAST so prior auto-fixes
        # (which grow NameMap) get reflected in counters.
        return [
            self._check_namemap_completeness,
            self._check_namemap_dups,
            self._check_empty_struct_arrays,
            self._check_dup_rows,
            self._check_enabled_state_values,
            self._check_cross_dt_refs,
            self._check_z_bounds,
            self._check_unanchored_zones,
            self._check_connection_null_endpoints,
            self._check_connection_endpoints_live,
            self._check_landmark_zband_alignment,
            self._check_landmark_minz_anchor,
            self._check_chapterid_uniqueness,
            self._check_chapter_displayname_resolves,
            self._check_extended_connectivity_neighbours,
            self._check_extended_connectivity_z_bounds,
            self._check_chapter_stair_uniqueness,
            self._check_stair_xy_collision,
            self._check_embedded_bottom_needs_headroom,
            self._check_orphan_added_data,
            self._check_live_to_disabled,
            self._check_counter_sync,
            # New ERROR checks added 2026-04 from Z-shift discoveries:
            self._check_zone_preferred_z_in_band,
            self._check_nested_subcell_z_in_band,
            self._check_ss_landmark_bp_in_band,
            # New checks added 2026-04 from 14-floor stair build:
            self._check_stair_bubble_z_oob,
            self._check_chapter_layer_continuity,
            self._check_chapter_has_at_least_one_zone,
            self._check_live_landmark_has_host,
        ]

    def run(self, progress=None):
        """Run every check. Returns list[Issue].

        progress (optional callable): invoked as progress(i, total, name)
        before each check fires so a UI can show a status bar / label.
        Useful because some checks scan multi-MB JSONs and take a couple
        seconds each, which would otherwise look like a hang."""
        all_issues = []
        checks = self.CHECKS
        total = len(checks)
        for i, check in enumerate(checks):
            if progress:
                try: progress(i, total, check.__name__)
                except Exception: pass
            try:
                all_issues.extend(check() or [])
            except Exception as e:
                all_issues.append(Issue(
                    'error', f'check_crash:{check.__name__}', None,
                    f'Validator crashed: {e}'))
        if progress:
            try: progress(total, total, 'done')
            except Exception: pass
        return all_issues

    def auto_fix(self, issues):
        """Run every fixer attached to an issue, then re-run the validator.
        Returns (fixed_count, remaining_issues)."""
        fixed = 0
        for it in issues:
            if it.fixer:
                try:
                    it.fixer()
                    fixed += 1
                except Exception:
                    pass
        # One additional counter-sync pass — auto-fixes may have grown NameMaps
        for k, doc in self.docs.items():
            n = len(doc.data.get('NameMap', []))
            doc.data['NamesReferencedFromExportDataCount'] = n
            g = doc.data.get('Generations') or []
            if g and isinstance(g[0], dict):
                g[0]['NameCount'] = n
        remaining = self.run()
        return fixed, remaining


# -----------------------------------------------------------------------------
# Pre-build validation dialog (new UX)
# -----------------------------------------------------------------------------

class _ValidationDialog(tk.Toplevel):
    """Custom Toplevel for pre-build validation results.

    Shows each Issue with a plain-English title + explanation, a checkbox
    for selecting which auto-fixes to apply, and three actions:
      - Apply selected auto-fixes  (runs fixers, re-validates in place)
      - Build anyway               (proceeds even if errors remain)
      - Cancel                     (abort build)

    After construction `self.result` is one of: 'skip' (proceed) or
    'cancel' (abort). Auto-fixes (if applied) are persisted by saving
    every dirty doc before returning 'skip'.
    """

    # Check IDs that vanilla SandboxSmall ships with — flagging them is
    # informational only, not crash-indicative. Hidden by default in the UI;
    # toggle "Show vanilla-tolerated" to inspect them.
    VANILLA_TOLERATED_CHECKS = frozenset({
        'connection_null_endpoints',
        'unanchored_zone',
        'namemap_completeness',
        'landmark_zband_misalign',
        'landmark_not_at_minz',
        'extended_connectivity_no_neighbour',
    })

    def __init__(self, app, issues, validator):
        # Standard withdraw -> build -> deiconify pattern (avoids the
        # empty-grey-rectangle race the user has hit in this codebase).
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.validator = validator
        self.issues = list(issues)
        self.result = 'cancel'  # default if user closes the window
        self._fix_vars = []  # parallel to self.issues; tk.BooleanVar each
        # Default: hide vanilla-tolerated warnings (they ship with vanilla
        # SandboxSmall and don't crash anything). User can toggle to show.
        self._show_vanilla_tolerated = tk.BooleanVar(value=False)
        self.title('Pre-build validation')
        self.transient(app)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)

        # Geometry: ~700x500 centered on parent
        w, h = 760, 540
        try:
            px = app.winfo_rootx()
            py = app.winfo_rooty()
            pw = app.winfo_width() or w
            ph = app.winfo_height() or h
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
            self.geometry(f'{w}x{h}+{x}+{y}')
        except Exception:
            self.geometry(f'{w}x{h}')

        self._build_ui()
        self._populate(self.issues)

        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass
        self.wait_window(self)

    # --- UI scaffold -------------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill='both', expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # Header
        self._header = ttk.Label(outer, text='', font=('TkDefaultFont', 11, 'bold'))
        self._header.grid(row=0, column=0, sticky='ew', pady=(0, 6))

        # Optional all-clear banner (shown after fixes resolve everything)
        self._banner = ttk.Label(outer, text='', foreground='#0a7a2e')
        # Not gridded by default; _populate manages it.

        # Scrollable list area
        list_frame = ttk.Frame(outer)
        list_frame.grid(row=1, column=0, sticky='nsew')
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(list_frame, highlightthickness=0,
                                 borderwidth=0)
        self._canvas.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(list_frame, orient='vertical',
                           command=self._canvas.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self._canvas.configure(yscrollcommand=sb.set)

        self._inner = ttk.Frame(self._canvas)
        self._inner_id = self._canvas.create_window(
            (0, 0), window=self._inner, anchor='nw')

        def _on_inner_config(_e):
            self._canvas.configure(scrollregion=self._canvas.bbox('all'))
        self._inner.bind('<Configure>', _on_inner_config)

        def _on_canvas_config(e):
            # Make inner frame match canvas width so wraplength works.
            self._canvas.itemconfigure(self._inner_id, width=e.width)
        self._canvas.bind('<Configure>', _on_canvas_config)

        # Mouse wheel scrolling
        def _on_wheel(e):
            delta = -1 if e.delta > 0 else 1
            self._canvas.yview_scroll(delta, 'units')
        self._canvas.bind_all('<MouseWheel>', _on_wheel)

        # Bottom button row
        btn_row = ttk.Frame(outer)
        btn_row.grid(row=2, column=0, sticky='ew', pady=(8, 0))

        self._apply_btn = ttk.Button(
            btn_row, text='Apply selected auto-fixes',
            command=self._on_apply_fixes)
        self._apply_btn.pack(side='left')

        # Copy-to-clipboard so users can paste into a bug report or
        # search the codebase. Bundles every issue's title + explanation
        # + raw detail into one plain-text block.
        ttk.Button(btn_row, text='Copy text',
                   command=self._on_copy_text).pack(side='left', padx=(8, 0))

        # Vanilla-tolerated toggle — let user reveal warnings that vanilla
        # SandboxSmall ships with (suppressed by default to keep dialog clean).
        ttk.Checkbutton(btn_row,
                        text='Show vanilla-tolerated',
                        variable=self._show_vanilla_tolerated,
                        command=self._on_toggle_vanilla
                        ).pack(side='left', padx=(12, 0))

        ttk.Button(btn_row, text='Cancel',
                   command=self._on_cancel).pack(side='right')
        self._build_btn = ttk.Button(
            btn_row, text='Build anyway', command=self._on_build)
        self._build_btn.pack(side='right', padx=(0, 6))

    # --- (re)populate ------------------------------------------------

    def _on_toggle_vanilla(self):
        """User clicked the 'Show vanilla-tolerated' checkbox — re-render
        with the current issues list and the new filter setting."""
        self._populate(self.issues)

    def _filter_issues(self, issues):
        """Hide vanilla-tolerated warnings unless the toggle is checked."""
        if self._show_vanilla_tolerated.get():
            return list(issues)
        return [i for i in issues
                if i.check not in self.VANILLA_TOLERATED_CHECKS
                or i.severity == 'error']  # always show if somehow elevated to error

    def _populate(self, issues):
        # Clear existing rows
        for child in self._inner.winfo_children():
            child.destroy()
        self._fix_vars = []

        # Apply the vanilla-tolerated filter
        visible_issues = self._filter_issues(issues)
        hidden_count = len(issues) - len(visible_issues)

        n_err = sum(1 for i in visible_issues if i.severity == 'error')
        n_warn = sum(1 for i in visible_issues if i.severity == 'warning')
        n_info = sum(1 for i in visible_issues if i.severity == 'info')
        bits = []
        if n_err: bits.append(f'{n_err} error(s)')
        if n_warn: bits.append(f'{n_warn} warning(s)')
        if n_info: bits.append(f'{n_info} info')
        head = ('Pre-build validation: ' +
                (', '.join(bits) if bits else 'no issues found'))
        if hidden_count > 0:
            head += f'   ({hidden_count} vanilla-tolerated hidden)'
        self._header.configure(text=head)
        # Use visible_issues for the rest of the populate path
        issues = visible_issues

        # Banner: green "all clear" only if zero issues
        if not issues:
            self._banner.configure(
                text='All clear — no validation issues remaining.')
            self._banner.grid(row=0, column=0, sticky='ew', pady=(28, 4))
            self._header.grid_remove()
            self._apply_btn.configure(state='disabled')
            self._build_btn.configure(text='Build now', default='active')
            try:
                self._build_btn.focus_set()
            except tk.TclError:
                pass
            return
        else:
            self._banner.grid_remove()
            self._header.grid()

        # Build per-issue rows
        SEV_TAG = {'error': '[ERROR]  ',
                   'warning': '[WARN]   ',
                   'info': '[INFO]   '}
        SEV_COLOR = {'error': '#a4262c',
                     'warning': '#a06800',
                     'info': '#1a4f8a'}

        n_fixable = 0
        for idx, issue in enumerate(issues):
            title, explanation = humanize(issue)
            sev = issue.severity or 'info'

            row = ttk.Frame(self._inner, padding=(2, 4))
            row.pack(fill='x', expand=True)

            tag = ttk.Label(row, text=SEV_TAG.get(sev, '[?]    '),
                            foreground=SEV_COLOR.get(sev, '#333'),
                            font=('TkDefaultFont', 9, 'bold'))
            tag.grid(row=0, column=0, sticky='nw', padx=(0, 6))

            ttl = ttk.Label(row, text=title,
                            font=('TkDefaultFont', 10, 'bold'),
                            wraplength=620, justify='left')
            ttl.grid(row=0, column=1, sticky='w')

            exp = ttk.Label(row, text=explanation,
                            wraplength=620, justify='left')
            exp.grid(row=1, column=1, sticky='w', pady=(2, 0))

            # Show the raw detail collapsed underneath in muted text —
            # users who want the technical specifics can still read it.
            if issue.detail and issue.detail.strip() != (explanation or '').strip():
                det = ttk.Label(row, text=issue.detail,
                                wraplength=620, justify='left',
                                foreground='#555')
                det.grid(row=2, column=1, sticky='w', pady=(2, 0))

            # Auto-fix checkbox (if available)
            if issue.fixer:
                n_fixable += 1
                # Default: errors checked, warnings unchecked.
                default_on = (sev == 'error')
                var = tk.BooleanVar(value=default_on)
                lbl = ('Auto-fix available'
                       if not issue.fixer_label
                       else f'Auto-fix: {issue.fixer_label}')
                cb = ttk.Checkbutton(row, text=lbl, variable=var)
                cb.grid(row=3, column=1, sticky='w', pady=(4, 0))
                self._fix_vars.append((var, issue))
            else:
                self._fix_vars.append((None, issue))

            # Subtle separator
            sep = ttk.Separator(self._inner, orient='horizontal')
            sep.pack(fill='x', pady=(4, 0))

            row.columnconfigure(1, weight=1)

        # Update button states
        self._apply_btn.configure(
            state='normal' if n_fixable > 0 else 'disabled')
        # Always allow "Build anyway"; warn user via the header text.
        self._build_btn.configure(state='normal', text='Build anyway')

        # Reset scroll to top
        try:
            self._canvas.yview_moveto(0.0)
        except tk.TclError:
            pass

    # --- actions -----------------------------------------------------

    def _on_apply_fixes(self):
        """Run every checked fixer, save dirty docs, then re-validate
        and refresh this dialog in place."""
        selected = [iss for var, iss in self._fix_vars
                    if var is not None and var.get() and iss.fixer]
        if not selected:
            return

        applied = 0
        for iss in selected:
            try:
                iss.fixer()
                applied += 1
            except Exception:
                pass

        # Counter-sync sweep (mirrors BuildValidator.auto_fix tail).
        for _k, doc in self.validator.docs.items():
            try:
                n = len(doc.data.get('NameMap', []))
                doc.data['NamesReferencedFromExportDataCount'] = n
                g = doc.data.get('Generations') or []
                if g and isinstance(g[0], dict):
                    g[0]['NameCount'] = n
            except Exception:
                pass

        # Persist the fixes — same behaviour as the legacy 'fix' path.
        for doc in self.validator.docs.values():
            if doc.data is not None:
                try:
                    doc.save()
                except Exception:
                    pass

        # Re-validate
        try:
            new_issues = self.validator.run()
        except Exception:
            new_issues = []
        self.issues = new_issues
        self._populate(self.issues)

        # Tell the user what just happened in the header.
        if not new_issues:
            self._banner.configure(
                text=f'Auto-fix applied: {applied} issue(s) resolved. '
                     f'All clear — ready to build.')
            self._banner.grid(row=0, column=0, sticky='ew', pady=(28, 4))
        else:
            self._header.configure(
                text=self._header.cget('text') +
                     f'   (auto-fix applied: {applied})')

    def _on_copy_text(self):
        """Bundle every issue's text into a plain-text block and put it
        on the clipboard so the user can paste elsewhere."""
        lines = [self._header.cget('text'), '']
        for iss in (self.issues or []):
            try:
                title, explanation = humanize(iss)
            except Exception:
                title = (iss.check or '?')
                explanation = iss.detail or ''
            sev = (iss.severity or 'info').upper()
            lines.append(f'[{sev}] {title}')
            if explanation:
                lines.append(f'  {explanation}')
            if iss.detail and iss.detail.strip() != (explanation or '').strip():
                lines.append(f'  Raw: {iss.detail}')
            if iss.fixer and getattr(iss, 'fixer_label', None):
                lines.append(f'  Auto-fix: {iss.fixer_label}')
            lines.append('')
        text = '\n'.join(lines).rstrip() + '\n'
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()  # keep clipboard alive after dialog closes
        except tk.TclError:
            pass
        # Brief visual feedback in the header
        try:
            cur = self._header.cget('text')
            self._header.configure(text=cur + '   (copied to clipboard)')
            self.after(1500, lambda: self._header.configure(text=cur))
        except Exception:
            pass

    def _on_build(self):
        self.result = 'skip'
        self._teardown()

    def _on_cancel(self):
        self.result = 'cancel'
        self._teardown()

    def _teardown(self):
        try:
            self._canvas.unbind_all('<MouseWheel>')
        except tk.TclError:
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# -----------------------------------------------------------------------------
# Row views
# -----------------------------------------------------------------------------

class ZoneView:
    def __init__(self, row):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])

    @property
    def zone_set(self): return get_enum(find_prop(self._values, 'ZoneSet'))
    @property
    def chapter(self): return get_rowname(find_prop(self._values, 'Chapter'))
    @property
    def additional_chapters(self):
        """Return list of chapter RowNames in AdditionalChapters (vanilla
        elevator pattern uses this to declare bridge-floor membership)."""
        out = []
        ac = find_prop(self._values, 'AdditionalChapters')
        if not ac: return out
        for entry in (ac.get('Value') or []):
            if not isinstance(entry, dict): continue
            ev = entry.get('Value')
            if isinstance(ev, list):
                for it in ev:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        v = it.get('Value')
                        if v: out.append(v)
        return out
    @property
    def biome(self): return get_tagname(find_prop(self._values, 'Biome'))
    @property
    def visual_map_style(self): return get_enum(find_prop(self._values, 'VisualMapStyle'))
    @property
    def zone_lighting_behavior(self): return get_enum(find_prop(self._values, 'ZoneLightingBehavior'))
    @property
    def toast_appearance(self): return get_enum(find_prop(self._values, 'ToastAppearance'))
    @property
    def position(self): return get_intvec(find_prop(self._values, 'Position'))
    @property
    def target_size(self): return get_intvec(find_prop(self._values, 'TargetSize'))
    @property
    def zone_temperature(self): return float(get_scalar(find_prop(self._values, 'ZoneTemperature'), 0.0))
    @property
    def water_prevalence(self): return float(get_scalar(find_prop(self._values, 'WaterPrevalence'), 0.0))
    @property
    def light_prevalence(self): return float(get_scalar(find_prop(self._values, 'LightPrevalence'), 0.0))
    @property
    def lighting_curve(self): return int(get_scalar(find_prop(self._values, 'LightingCurve'), 0))
    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def landmark_entries(self):
        """Return list of dicts: {landmark, placement, extended, _prop}."""
        prop = find_prop(self._values, 'LandmarkHandles')
        if prop is None:
            return []
        out = []
        for entry in prop.get('Value', []) or []:
            if not isinstance(entry, dict):
                continue
            lm = ''
            placement = ''
            extended = False
            for sub in entry.get('Value', []) or []:
                if not isinstance(sub, dict):
                    continue
                if sub.get('Name') == 'Landmark':
                    lm = get_rowname(sub)
                elif sub.get('Name') == 'Placement':
                    placement = get_enum(sub)
                elif sub.get('Name') == 'bExtendedConnectivityLandmark':
                    extended = bool(sub.get('Value', False))
            out.append({'landmark': lm, 'placement': placement,
                        'extended': extended, '_entry': entry})
        return out

    @property
    def bubble_deck(self): return get_rowname(find_prop(self._values, 'BubbleDeck'))
    @property
    def passage_deck(self): return get_rowname(find_prop(self._values, 'PassageDeck'))

    def set_bubble_deck(self, v): set_rowname(find_prop(self._values, 'BubbleDeck'), v)
    def set_passage_deck(self, v): set_rowname(find_prop(self._values, 'PassageDeck'), v)

    def set_chapter(self, v): set_rowname(find_prop(self._values, 'Chapter'), v)
    def set_biome(self, v): set_tagname(find_prop(self._values, 'Biome'), v)
    def set_visual_map_style(self, v): set_enum(find_prop(self._values, 'VisualMapStyle'), v)
    def set_zone_lighting_behavior(self, v): set_enum(find_prop(self._values, 'ZoneLightingBehavior'), v)
    def set_toast_appearance(self, v): set_enum(find_prop(self._values, 'ToastAppearance'), v)
    def set_position(self, x, y, z): set_intvec(find_prop(self._values, 'Position'), x, y, z)
    def set_target_size(self, x, y, z): set_intvec(find_prop(self._values, 'TargetSize'), x, y, z)
    def set_zone_temperature(self, v): set_scalar(find_prop(self._values, 'ZoneTemperature'), float(v))
    def set_water_prevalence(self, v): set_scalar(find_prop(self._values, 'WaterPrevalence'), float(v))
    def set_light_prevalence(self, v): set_scalar(find_prop(self._values, 'LightPrevalence'), float(v))
    def set_lighting_curve(self, v): set_scalar(find_prop(self._values, 'LightingCurve'), int(v))
    def set_enabled(self, enabled):
        p = find_prop(self._values, 'EnabledState')
        if p is not None:
            set_enum(p, 'Live' if enabled else 'Disabled')

    def landmark_handles_prop(self):
        return find_prop(self._values, 'LandmarkHandles')

    def add_landmark_entry(self, landmark_rowname, placement='Fixed', extended=False):
        prop = self.landmark_handles_prop()
        if prop is None:
            return False
        existing = prop.get('Value', [])

        template = None
        if existing:
            # Best template: clone an existing entry (it has all sub-fields)
            template = copy.deepcopy(existing[0])
        elif ('DummyStruct' in prop and isinstance(prop['DummyStruct'], dict)
              and prop['DummyStruct'].get('Value')):
            # Non-empty pristine DummyStruct — rare, but use it if it has fields
            template = copy.deepcopy(prop['DummyStruct'])

        if template is None:
            # Empty DummyStruct (UAssetGUI emits Value=[] for pristine empty
            # struct arrays). Build a full MorZoneLandmarkEntry from scratch.
            template = self._build_landmark_entry_template(
                landmark_rowname, placement, extended)
        else:
            # Template came from a real entry — rewrite its sub-fields.
            for sub in template.get('Value', []):
                if not isinstance(sub, dict):
                    continue
                n = sub.get('Name')
                if n == 'Landmark':
                    set_rowname(sub, landmark_rowname)
                elif n == 'Placement':
                    set_enum(sub, placement)
                elif n == 'bExtendedConnectivityLandmark':
                    sub['Value'] = bool(extended)

        existing.append(template)
        prop['Value'] = existing
        # DummyStruct is harmless once real entries exist; leave it alone.
        return True

    def _build_landmark_entry_template(self, landmark_rowname, placement, extended):
        """Fallback template if zone has no existing landmark entries to clone."""
        return {
            '$type': 'UAssetAPI.PropertyTypes.Structs.StructPropertyData, UAssetAPI',
            'StructType': 'MorZoneLandmarkEntry',
            'SerializeNone': True,
            'StructGUID': '{00000000-0000-0000-0000-000000000000}',
            'SerializationControl': 'NoExtension',
            'Operation': 'None',
            'Name': 'LandmarkHandles',
            'ArrayIndex': 0,
            'IsZero': False,
            'PropertyTagFlags': 'None',
            'PropertyTagExtensions': 'NoExtension',
            'Value': [
                {
                    '$type': 'UAssetAPI.PropertyTypes.Structs.StructPropertyData, UAssetAPI',
                    'StructType': 'MorLandmarkRowHandle',
                    'SerializeNone': True,
                    'StructGUID': '{00000000-0000-0000-0000-000000000000}',
                    'SerializationControl': 'NoExtension',
                    'Operation': 'None',
                    'Name': 'Landmark',
                    'ArrayIndex': 0,
                    'IsZero': False,
                    'PropertyTagFlags': 'None',
                    'PropertyTagExtensions': 'NoExtension',
                    'Value': [{
                        '$type': 'UAssetAPI.PropertyTypes.Objects.NamePropertyData, UAssetAPI',
                        'Name': 'RowName',
                        'ArrayIndex': 0, 'IsZero': False,
                        'PropertyTagFlags': 'None',
                        'PropertyTagExtensions': 'NoExtension',
                        'Value': landmark_rowname,
                    }]
                },
                {
                    '$type': 'UAssetAPI.PropertyTypes.Objects.EnumPropertyData, UAssetAPI',
                    'EnumType': 'EZoneBubblePlacement',
                    'InnerType': None,
                    'Name': 'Placement',
                    'ArrayIndex': 0, 'IsZero': False,
                    'PropertyTagFlags': 'None',
                    'PropertyTagExtensions': 'NoExtension',
                    'Value': f'EZoneBubblePlacement::{placement}',
                },
                {
                    '$type': 'UAssetAPI.PropertyTypes.Objects.BoolPropertyData, UAssetAPI',
                    'Name': 'bExtendedConnectivityLandmark',
                    'ArrayIndex': 0, 'IsZero': False,
                    'PropertyTagFlags': 'None',
                    'PropertyTagExtensions': 'NoExtension',
                    'Value': bool(extended),
                },
            ],
        }

    def remove_landmark_entry(self, index):
        prop = self.landmark_handles_prop()
        if prop is None:
            return False
        val = prop.get('Value', [])
        if 0 <= index < len(val):
            removed = val.pop(index)
            # If the array is now empty, we MUST preserve a DummyStruct on the
            # ArrayProperty so UAssetGUI fromjson can reconstruct the binary.
            # Empty StructProperty arrays without DummyStruct throw:
            #   "Unable to reconstruct DummyStruct within empty StructProperty array"
            if not val and 'DummyStruct' not in prop:
                prop['DummyStruct'] = copy.deepcopy(removed)
            prop['Value'] = val
            return True
        return False

    def update_landmark_entry(self, index, landmark=None, placement=None, extended=None):
        prop = self.landmark_handles_prop()
        if prop is None:
            return False
        val = prop.get('Value', [])
        if not (0 <= index < len(val)):
            return False
        entry = val[index]
        for sub in entry.get('Value', []):
            if not isinstance(sub, dict):
                continue
            n = sub.get('Name')
            if n == 'Landmark' and landmark is not None:
                set_rowname(sub, landmark)
            elif n == 'Placement' and placement is not None:
                set_enum(sub, placement)
            elif n == 'bExtendedConnectivityLandmark' and extended is not None:
                sub['Value'] = bool(extended)
        return True


class ChapterView:
    def __init__(self, row):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])

    @property
    def zone_set(self): return get_enum(find_prop(self._values, 'ZoneSet'))
    @property
    def chapter_id(self): return int(get_scalar(find_prop(self._values, 'ChapterID'), 0))
    @property
    def display_name(self):
        p = find_prop(self._values, 'DisplayName')
        if p is None: return ''
        return str(p.get('Value', ''))
    @property
    def layer(self): return int(get_scalar(find_prop(self._values, 'Layer'), 0))
    @property
    def enemy_scaling(self): return int(get_scalar(find_prop(self._values, 'EnemyScalingLevel'), 0))
    @property
    def min_z(self): return int(get_scalar(find_prop(self._values, 'MinZ'), 0))
    @property
    def max_z(self): return int(get_scalar(find_prop(self._values, 'MaxZ'), 0))
    @property
    def prime_z(self): return int(get_scalar(find_prop(self._values, 'PrimeZ'), 0))
    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def set_zone_set(self, v): set_enum(find_prop(self._values, 'ZoneSet'), v)
    def set_chapter_id(self, v): set_scalar(find_prop(self._values, 'ChapterID'), int(v))
    def set_display_name(self, v): set_scalar(find_prop(self._values, 'DisplayName'), str(v))
    def set_layer(self, v): set_scalar(find_prop(self._values, 'Layer'), int(v))
    def set_enemy_scaling(self, v): set_scalar(find_prop(self._values, 'EnemyScalingLevel'), int(v))
    def set_min_z(self, v): set_scalar(find_prop(self._values, 'MinZ'), int(v))
    def set_max_z(self, v): set_scalar(find_prop(self._values, 'MaxZ'), int(v))
    def set_prime_z(self, v): set_scalar(find_prop(self._values, 'PrimeZ'), int(v))
    def set_enabled(self, enabled):
        p = find_prop(self._values, 'EnabledState')
        if p is not None:
            set_enum(p, 'Live' if enabled else 'Disabled')


class BiomeView:
    def __init__(self, row, dt_data):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])
        self._dt = dt_data

    @property
    def display_name(self):
        p = find_prop(self._values, 'DisplayName')
        if p is None: return ''
        return str(p.get('Value', ''))
    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def object_ref_fields(self):
        out = []
        for p in self._values:
            if not isinstance(p, dict):
                continue
            t = p.get('$type', '')
            if 'ObjectPropertyData' in t:
                idx = p.get('Value', 0)
                out.append((p.get('Name', '?'), idx, resolve_object_ref(self._dt, idx)))
        return out

    def set_display_name(self, v): set_scalar(find_prop(self._values, 'DisplayName'), str(v))
    def set_enabled(self, enabled):
        p = find_prop(self._values, 'EnabledState')
        if p is not None:
            set_enum(p, 'Live' if enabled else 'Disabled')


class DeckView:
    def __init__(self, row):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])

    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def deck_prop(self): return find_prop(self._values, 'DeckEntries')

    def entries(self):
        prop = self.deck_prop()
        out = []
        if prop is None: return out
        for entry in prop.get('Value', []) or []:
            if not isinstance(entry, dict): continue
            bubble = appearances = ''
            zone_entrance = False
            for sub in entry.get('Value', []) or []:
                if not isinstance(sub, dict): continue
                n = sub.get('Name')
                if n == 'Bubble':
                    bubble = str(sub.get('Value', ''))
                elif n == 'Appearances':
                    appearances = get_enum(sub)
                elif n == 'bZoneEntrance':
                    zone_entrance = bool(sub.get('Value', False))
            out.append({'bubble': bubble, 'appearances': appearances,
                        'zone_entrance': zone_entrance})
        return out

    def add_entry(self, bubble, appearances='Single', zone_entrance=False):
        prop = self.deck_prop()
        if prop is None: return False
        existing = prop.get('Value', [])
        if existing:
            template = copy.deepcopy(existing[0])
            for sub in template.get('Value', []):
                if not isinstance(sub, dict): continue
                n = sub.get('Name')
                if n == 'Bubble':
                    sub['Value'] = bubble
                elif n == 'Appearances':
                    set_enum(sub, appearances)
                elif n == 'bZoneEntrance':
                    sub['Value'] = bool(zone_entrance)
        else:
            template = {
                '$type': 'UAssetAPI.PropertyTypes.Structs.StructPropertyData, UAssetAPI',
                'StructType': 'MorZoneDeckEntry',
                'SerializeNone': True,
                'StructGUID': '{00000000-0000-0000-0000-000000000000}',
                'SerializationControl': 'NoExtension',
                'Operation': 'None',
                'Name': 'DeckEntries',
                'ArrayIndex': 0, 'IsZero': False,
                'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                'Value': [
                    {'$type': 'UAssetAPI.PropertyTypes.Objects.NamePropertyData, UAssetAPI',
                     'Name': 'Bubble', 'ArrayIndex': 0, 'IsZero': False,
                     'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                     'Value': bubble},
                    {'$type': 'UAssetAPI.PropertyTypes.Objects.EnumPropertyData, UAssetAPI',
                     'EnumType': 'EZoneDeckAppearances', 'InnerType': None,
                     'Name': 'Appearances', 'ArrayIndex': 0, 'IsZero': False,
                     'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                     'Value': f'EZoneDeckAppearances::{appearances}'},
                    {'$type': 'UAssetAPI.PropertyTypes.Objects.BoolPropertyData, UAssetAPI',
                     'Name': 'bZoneEntrance', 'ArrayIndex': 0, 'IsZero': False,
                     'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                     'Value': bool(zone_entrance)},
                ],
            }
        existing.append(template)
        prop['Value'] = existing
        return True

    def remove_entry(self, index):
        prop = self.deck_prop()
        if prop is None: return False
        val = prop.get('Value', [])
        if 0 <= index < len(val):
            removed = val.pop(index)
            if not val and 'DummyStruct' not in prop:
                prop['DummyStruct'] = copy.deepcopy(removed)
            prop['Value'] = val
            return True
        return False

    def update_entry(self, index, bubble=None, appearances=None, zone_entrance=None):
        prop = self.deck_prop()
        if prop is None: return False
        val = prop.get('Value', [])
        if not (0 <= index < len(val)): return False
        entry = val[index]
        for sub in entry.get('Value', []):
            if not isinstance(sub, dict): continue
            n = sub.get('Name')
            if n == 'Bubble' and bubble is not None:
                sub['Value'] = bubble
            elif n == 'Appearances' and appearances is not None:
                set_enum(sub, appearances)
            elif n == 'bZoneEntrance' and zone_entrance is not None:
                sub['Value'] = bool(zone_entrance)
        return True


class FilterView:
    def __init__(self, row):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])

    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def _list_prop(self, name):
        return find_prop(self._values, name)

    def _list(self, name):
        prop = self._list_prop(name)
        if prop is None: return []
        out = []
        for item in prop.get('Value', []) or []:
            if isinstance(item, dict):
                out.append(str(item.get('Value', '')))
        return out

    def whitelist(self): return self._list('Whitelist')
    def blacklist(self): return self._list('Blacklist')

    def _set_list(self, name, names):
        prop = self._list_prop(name)
        if prop is None:
            return False
        new_items = []
        for bb in names:
            new_items.append({
                '$type': 'UAssetAPI.PropertyTypes.Objects.NamePropertyData, UAssetAPI',
                'Name': name,
                'ArrayIndex': 0, 'IsZero': False,
                'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                'Value': bb,
            })
        prop['Value'] = new_items
        return True

    def set_whitelist(self, items): self._set_list('Whitelist', items)
    def set_blacklist(self, items): self._set_list('Blacklist', items)

    def set_enabled(self, enabled):
        p = find_prop(self._values, 'EnabledState')
        if p is not None:
            set_enum(p, 'Live' if enabled else 'Disabled')


class LandmarkView:
    def __init__(self, row):
        self.row = row
        self.name = row.get('Name', '?')
        self._values = row.get('Value', [])

    @property
    def placement(self): return get_enum(find_prop(self._values, 'Placement'))
    @property
    def base_bubble_name(self): return str(get_scalar(find_prop(self._values, 'BaseBubbleName'), ''))
    @property
    def display_name(self):
        p = find_prop(self._values, 'DisplayName')
        if p is None: return ''
        return str(p.get('Value', ''))
    @property
    def player_start(self): return bool(get_scalar(find_prop(self._values, 'bPlayerStartLocation'), False))
    @property
    def challenge_rating(self): return int(get_scalar(find_prop(self._values, 'ChallengeRating'), 0))
    @property
    def enabled_state(self):
        p = find_prop(self._values, 'EnabledState')
        return get_enum(p) if p is not None else 'Live'
    @property
    def is_enabled(self): return self.enabled_state == 'Live'

    def connections_prop(self): return find_prop(self._values, 'GuaranteedConnections')

    def connections(self):
        """Return list of landmark RowNames parsed from 'World.Landmark.<name>' tags."""
        prop = self.connections_prop()
        if prop is None: return []
        out = []
        for item in prop.get('Value', []) or []:
            if not isinstance(item, dict):
                continue
            tag = ''
            # Could be GameplayTagContainerPropertyData OR a struct with TagName field
            inner = item.get('Value')
            if isinstance(inner, str):
                tag = inner
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and sub.get('Name') == 'TagName':
                        tag = str(sub.get('Value', ''))
                        break
            # strip the "World.Landmark." prefix for display
            if tag.startswith('World.Landmark.'):
                out.append(tag[len('World.Landmark.'):])
            else:
                out.append(tag)
        return out

    def _connection_item_template(self, existing):
        """Build a new connection entry matching the existing style."""
        if existing:
            return copy.deepcopy(existing[0])
        # Fallback — build a GameplayTag struct with TagName NameProperty inside
        return {
            '$type': 'UAssetAPI.PropertyTypes.Structs.StructPropertyData, UAssetAPI',
            'StructType': 'GameplayTag',
            'SerializeNone': True,
            'StructGUID': '{00000000-0000-0000-0000-000000000000}',
            'SerializationControl': 'NoExtension',
            'Operation': 'None',
            'Name': 'GuaranteedConnections',
            'ArrayIndex': 0, 'IsZero': False,
            'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
            'Value': [{
                '$type': 'UAssetAPI.PropertyTypes.Objects.NamePropertyData, UAssetAPI',
                'Name': 'TagName',
                'ArrayIndex': 0, 'IsZero': False,
                'PropertyTagFlags': 'None', 'PropertyTagExtensions': 'NoExtension',
                'Value': '',
            }],
        }

    def set_connections(self, landmark_rownames):
        prop = self.connections_prop()
        if prop is None: return False
        existing = prop.get('Value', [])
        new_items = []
        for rn in landmark_rownames:
            tag = f'World.Landmark.{rn}' if not rn.startswith('World.Landmark.') else rn
            item = self._connection_item_template(existing)
            # Write TagName
            inner = item.get('Value')
            if isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and sub.get('Name') == 'TagName':
                        sub['Value'] = tag
                        break
            new_items.append(item)
        prop['Value'] = new_items
        return True

    def set_placement(self, v): set_enum(find_prop(self._values, 'Placement'), v)
    def set_base_bubble(self, v): set_scalar(find_prop(self._values, 'BaseBubbleName'), str(v))
    def set_player_start(self, v): set_scalar(find_prop(self._values, 'bPlayerStartLocation'), bool(v))
    def set_challenge(self, v): set_scalar(find_prop(self._values, 'ChallengeRating'), int(v))
    def set_enabled(self, enabled):
        p = find_prop(self._values, 'EnabledState')
        if p is not None:
            set_enum(p, 'Live' if enabled else 'Disabled')


# -----------------------------------------------------------------------------
# Base tab
# -----------------------------------------------------------------------------

class BaseTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app

    def make_tree(self, parent, columns, heading_specs, height=14,
                   settings_key=None):
        """Create a Treeview wired up to persistent sort state.
        If settings_key is provided, the tree's current sort order is loaded
        from SETTINGS on creation and saved whenever the user clicks a column
        header. It's also reapplied automatically every time the tab calls
        apply_sort(tree) after a repopulate."""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height)
        for key, label, width, stretch in heading_specs:
            tree.heading(key, text=label,
                         command=lambda k=key, t=tree: self._sort_tree(t, k))
            tree.column(key, width=width, anchor=tk.W, stretch=stretch)
        vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        tree._settings_key = settings_key
        if settings_key:
            col, rev = SETTINGS.get_sort(settings_key)
            tree._sort_state = (col, rev) if col else ('', False)
        else:
            tree._sort_state = ('', False)
        return tree

    def _sort_tree(self, tree, col):
        cur, rev = getattr(tree, '_sort_state', ('', False))
        rev = not rev if cur == col else False
        tree._sort_state = (col, rev)
        if getattr(tree, '_settings_key', None):
            SETTINGS.set_sort(tree._settings_key, col, rev)
        self._apply_sort_items(tree)

    @staticmethod
    def _natural_key(s):
        """Compatibility shim — module-level natural_key() is the canonical one."""
        return natural_key(s)

    def _apply_sort_items(self, tree):
        col, rev = getattr(tree, '_sort_state', ('', False))
        if not col:
            return
        items = [(tree.set(k, col), k) for k in tree.get_children('')]
        def keyfn(v):
            s = v[0]
            # Pure numeric or tuple-leading-number: sort numerically
            try:
                return (0, float(s.strip('()').split(',')[0])) if ',' in s else (0, float(s))
            except Exception:
                # Fallback: natural sort (numbers-within-text compared numerically)
                return (1, BaseTab._natural_key(s))
        items.sort(key=keyfn, reverse=rev)
        for i, (_, k) in enumerate(items):
            tree.move(k, '', i)

    def apply_sort(self, tree):
        """Tab-facing hook: call after (re)populating `tree` to re-apply the
        last-saved sort order so the user doesn't have to re-click the header."""
        self._apply_sort_items(tree)


# -----------------------------------------------------------------------------
# STRINGS TAB — edit the World.uasset StringTable (Chapter/Biome/Landmark
# display-name keys and any other localization strings in that table)
# -----------------------------------------------------------------------------

class StringsTab(BaseTab):
    """Key/value editor for a StringTable doc.

    Doc's `.rows` is a list of [key, value] pairs. Editing rewrites the list
    in place; saving writes back to the JSON and the build pipeline picks it
    up automatically via `differs_from_original()`.
    """

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._build_ui()
        self._current_idx = None

    def _build_ui(self):
        # Top filter bar
        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top, text='Filter:').pack(side=tk.LEFT)
        self.v_filter = tk.StringVar()
        self.v_filter.trace_add('write', lambda *_: self._populate())
        e = ttk.Entry(top, textvariable=self.v_filter, width=40)
        e.pack(side=tk.LEFT, padx=(4, 12))
        self.v_count = tk.StringVar(value='0 entries')
        ttk.Label(top, textvariable=self.v_count,
                  foreground=self.app.COLOR_MUTED).pack(side=tk.LEFT)

        # Split: treeview on top, editor below
        self.tree = self.make_tree(self,
            columns=('key', 'value'),
            heading_specs=[
                ('key', 'Key',    380, False),
                ('value', 'Value', 520, True),
            ],
            height=20,
            settings_key='strings_tree')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # Edit panel
        ed = ttk.LabelFrame(self, text='Entry', padding=8)
        ed.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(ed, text='Key:').grid(row=0, column=0, sticky='w')
        self.v_key = tk.StringVar()
        ttk.Entry(ed, textvariable=self.v_key, width=60).grid(
            row=0, column=1, sticky='we', padx=(4, 0))
        ttk.Label(ed, text='Value:').grid(row=1, column=0, sticky='nw', pady=(6, 0))
        self.t_value = tk.Text(ed, height=4, width=60, wrap='word')
        self.t_value.grid(row=1, column=1, sticky='we', padx=(4, 0), pady=(6, 0))
        ed.columnconfigure(1, weight=1)

        btns = ttk.Frame(ed)
        btns.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
        ttk.Button(btns, text='Add as New',   command=self._on_add).pack(side=tk.LEFT)
        ttk.Button(btns, text='Copy',         command=self._on_copy).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text='Update',       command=self._on_update).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text='Delete',       command=self._on_delete).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text='Clear',        command=self._on_clear).pack(side=tk.LEFT, padx=(6, 0))

    # ---- data helpers ----
    def _doc(self):
        return self.app.docs.get('strings')

    def refresh_from_doc(self):
        self._populate()

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        doc = self._doc()
        if not doc or not doc.data:
            self.v_count.set('(no strings doc loaded)')
            return
        flt = self.v_filter.get().strip().lower()
        shown = 0
        for i, entry in enumerate(doc.rows):
            try:
                k, v = entry[0], entry[1]
            except (IndexError, TypeError):
                continue
            if flt and flt not in str(k).lower() and flt not in str(v).lower():
                continue
            self.tree.insert('', 'end', iid=str(i), values=(k, v))
            shown += 1
        self.v_count.set(f'{shown} of {len(doc.rows)} entries')
        self.apply_sort(self.tree)

    def _on_select(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        doc = self._doc()
        if not doc or idx >= len(doc.rows):
            return
        k, v = doc.rows[idx][0], doc.rows[idx][1]
        self._current_idx = idx
        self.v_key.set(k)
        self.t_value.delete('1.0', 'end')
        self.t_value.insert('1.0', v)

    def _on_add(self):
        doc = self._doc()
        if not doc:
            return
        k = self.v_key.get().strip()
        v = self.t_value.get('1.0', 'end-1c')
        if not k:
            messagebox.showwarning('Missing key', 'Key is required.')
            return
        # Duplicate check
        for entry in doc.rows:
            if len(entry) >= 1 and entry[0] == k:
                messagebox.showwarning('Duplicate key',
                    f'Key "{k}" already exists. Use Update to change its value.')
                return
        doc.rows.append([k, v])
        self._populate()
        self.app.refresh_status()

    def _on_update(self):
        doc = self._doc()
        if not doc:
            return
        if self._current_idx is None:
            messagebox.showwarning('Nothing selected', 'Select an entry to update.')
            return
        k = self.v_key.get().strip()
        v = self.t_value.get('1.0', 'end-1c')
        if not k:
            messagebox.showwarning('Missing key', 'Key is required.')
            return
        # Re-check duplicate on key change
        for i, entry in enumerate(doc.rows):
            if i != self._current_idx and len(entry) >= 1 and entry[0] == k:
                messagebox.showwarning('Duplicate key',
                    f'Key "{k}" already exists on another entry.')
                return
        doc.rows[self._current_idx] = [k, v]
        self._populate()
        self.app.refresh_status()

    def _on_delete(self):
        doc = self._doc()
        if not doc or self._current_idx is None:
            return
        if not messagebox.askyesno('Delete entry',
                f'Delete "{self.v_key.get()}"?'):
            return
        del doc.rows[self._current_idx]
        self._current_idx = None
        self._on_clear()
        self._populate()
        self.app.refresh_status()

    def _on_copy(self):
        doc = self._doc()
        if not doc:
            return
        if self._current_idx is None:
            messagebox.showwarning('Nothing selected', 'Select an entry to copy.')
            return
        try:
            src = doc.rows[self._current_idx]
            src_key, src_val = src[0], src[1]
        except (IndexError, TypeError):
            return
        new_key = simpledialog.askstring(
            'Copy entry', f'New key (copying from "{src_key}"):',
            initialvalue=f'{src_key}_copy', parent=self)
        if not new_key:
            return
        new_key = new_key.strip()
        if not new_key:
            return
        for entry in doc.rows:
            if len(entry) >= 1 and entry[0] == new_key:
                messagebox.showwarning('Duplicate key',
                    f'Key "{new_key}" already exists.')
                return
        doc.rows.append([new_key, copy.deepcopy(src_val)])
        self._populate()
        self.app.refresh_status()

    def _on_clear(self):
        self._current_idx = None
        self.v_key.set('')
        self.t_value.delete('1.0', 'end')


# -----------------------------------------------------------------------------
# ZONE TAB
# -----------------------------------------------------------------------------

class ZoneTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs['zones']
        self.zones = []
        self.modified = set()
        self.current = None
        self.snapshots = {}
        self._updating = False
        # Which ZoneSets to display. SandboxSmall is always on; campaign and
        # expedition are togglable so the tab can also act as a read/edit view
        # of Moria chapter + Expedition content.
        self._show_sandbox = tk.BooleanVar(value=True)
        self._show_campaign = tk.BooleanVar(value=False)
        self._show_expedition = tk.BooleanVar(value=False)
        # Hide zones whose biome is one of the Moria outdoor biomes
        # (Outdoor.DurinTower, Outdoor.TradingPost, Outdoor.DimrillDale,
        # Outdoor.ExpeditionStart). These are scripted campaign exteriors and
        # rarely useful when editing the indoor sandbox layout.
        self._hide_outdoor = tk.BooleanVar(value=True)
        # Hide zones whose primary chapter is not a Live SandboxSmall chapter
        # (orphans, outdoor/bridge chapters, disabled chapters). Default off —
        # show everything.
        self._hide_unassigned = tk.BooleanVar(value=False)
        self._build()

    def refresh_from_doc(self):
        allowed = set()
        if self._show_sandbox.get():
            allowed.update({'SandboxSmall', 'SandboxMedium'})
        if self._show_campaign.get():
            allowed.add('Moria')
        if self._show_expedition.get():
            allowed.add('Expedition')
        hide_outdoor = self._hide_outdoor.get()
        zones = []
        for r in self.doc.rows:
            zv = ZoneView(r)
            if zv.zone_set not in allowed:
                continue
            # Filter out zones whose biome is a Moria outdoor biome.
            # The biome property serializes as a GameplayTag whose TagName is
            # like 'World.Biome.Outdoor.DimrillDale'. We match on that prefix.
            if hide_outdoor:
                bt = (zv.biome or '')
                if bt.startswith('World.Biome.Outdoor.'):
                    continue
            zones.append(zv)
        self.zones = zones
        self.snapshots = {z.name: copy.deepcopy(z.row) for z in self.zones}
        self.modified.clear()
        self._populate_dropdowns()
        self._populate_tree()
        self.app.refresh_status()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Zone…',
                   command=self._add_zone).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Zone…',
                   command=self._copy_zone).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Rename Zone…',
                   command=self._rename_zone).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Zone',
                   command=self._delete_zone).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(toolbar, text='Revert Zone',
                   command=self.revert_current).pack(side=tk.LEFT)

        # ZoneSet filters
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT,
                                                       fill='y', padx=10)
        ttk.Label(toolbar, text='Show:').pack(side=tk.LEFT, padx=(0, 4))
        ttk.Checkbutton(toolbar, text='Sandbox',
                        variable=self._show_sandbox,
                        command=self.refresh_from_doc
                        ).pack(side=tk.LEFT)
        ttk.Checkbutton(toolbar, text='Campaign (Moria)',
                        variable=self._show_campaign,
                        command=self.refresh_from_doc
                        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Checkbutton(toolbar, text='Expedition',
                        variable=self._show_expedition,
                        command=self.refresh_from_doc
                        ).pack(side=tk.LEFT, padx=(6, 0))

        # Hide outdoor biomes (DimrillDale, DurinTower, TradingPost,
        # ExpeditionStart) so the list focuses on indoor sandbox zones.
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT,
                                                       fill='y', padx=10)
        cb = ttk.Checkbutton(toolbar, text='Hide outdoor biomes',
                              variable=self._hide_outdoor,
                              command=self.refresh_from_doc)
        cb.pack(side=tk.LEFT)
        try:
            attach_tooltip(cb, 'hide_outdoor_biomes')
        except Exception:
            pass

        # Hide zones whose primary chapter is not a Live SandboxSmall chapter.
        # Composes with the other filters via AND.
        cb_l0 = ttk.Checkbutton(toolbar, text='Hide unassigned zones',
                                 variable=self._hide_unassigned,
                                 command=self._populate_tree)
        cb_l0.pack(side=tk.LEFT, padx=(6, 0))
        try:
            attach_tooltip(cb_l0, 'hide_unassigned_zones')
        except Exception:
            pass

        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.LEFT, padx=12)

        headings = [
            ('name', 'Zone Name', 340, True),
            ('layer', 'Level', 50, False),
            ('chapter', 'Chapter', 180, False),
            ('biome', 'Biome', 220, False),
            ('style', 'Style', 90, False),
            ('pos', 'Position', 110, False),
            ('size', 'Size', 100, False),
            ('temp', 'Temp', 60, False),
            ('water', 'Water', 60, False),
            ('light', 'Light', 60, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(self, [h[0] for h in headings], headings,
                                     settings_key='zones')
        for ch, c in CHAPTER_COLORS.items():
            self.tree.tag_configure(ch, background=c)
        self.tree.tag_configure(MODIFIED_TAG, background='#fff7b0')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # Chapter-grouped tree rendering. Used by both drag-drop and the
        # right-click move flow — the grouped view is independently useful.
        if ENABLE_ZONE_CHAPTER_GROUPING:
            try:
                # Switch to tree+headings mode so chapter parent rows are
                # visible. The first column ('#0') becomes the chapter group
                # heading; existing data columns stay aligned with headings.
                self.tree.configure(show='tree headings')
                self.tree.heading('#0', text='Chapter group')
                self.tree.column('#0', width=240, stretch=False, anchor=tk.W)
                self.tree.tag_configure(ZONE_DRAG_TAG,
                                        background='#b6e7a0')
                self.tree.tag_configure('zone-chapter-parent',
                                        background='#eef1f6',
                                        font=self.app.FONT_HEADING)
            except Exception:
                pass

        # Drag-drop: wire mouse event handlers. Disabled by default
        # (see ENABLE_ZONE_DRAG_DROP at top of file). All state and
        # handlers are left in place for easy re-enable.
        if ENABLE_ZONE_DRAG_DROP:
            # Drag state. _dnd_active flips True on Button-1 over a zone row.
            self._dnd_active = False
            self._dnd_zone_iid = None
            self._dnd_press_x = 0
            self._dnd_press_y = 0
            self._dnd_last_target = None  # iid of last highlighted chapter row
            self._dnd_tooltip = None      # Toplevel created lazily during drag
            self._dnd_scroll_after = None # after-id for edge auto-scroll repeat
            self._dnd_scroll_dir = 0      # -1 up, +1 down, 0 not scrolling
            self._dnd_scroll_speed = 1    # units per tick (adaptive 1 or 3)
            self.tree.bind('<ButtonPress-1>', self._dnd_press, add='+')
            self.tree.bind('<B1-Motion>', self._dnd_motion, add='+')
            self.tree.bind('<ButtonRelease-1>', self._dnd_release, add='+')

        # Right-click "Move to chapter..." menu. Replaces drag-drop as the
        # primary way to reparent a zone.
        if ENABLE_ZONE_RIGHT_CLICK_MOVE:
            self._zone_context_menu = None  # built lazily on first popup
            self._zone_context_target = None  # zone iid the menu was opened on
            self.tree.bind('<Button-3>', self._on_zone_right_click, add='+')

        self._build_detail()

    def _build_detail(self):
        d = ttk.LabelFrame(self, text='Zone Detail', padding=8)
        d.pack(fill=tk.X, pady=(6, 0))
        g = ttk.Frame(d); g.pack(fill=tk.X)

        self.v_name = tk.StringVar(value='(no zone selected)')
        ttk.Label(g, text='Zone:', width=10).grid(row=0, column=0, sticky='w')
        ttk.Label(g, textvariable=self.v_name,
                  font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, columnspan=5, sticky='w')
        self.v_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text='Enabled', variable=self.v_enabled,
                        command=self._apply_enabled).grid(row=0, column=6, sticky='e', padx=10)

        lbl = ttk.Label(g, text='Chapter:')
        lbl.grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.v_chapter = tk.StringVar()
        self.cmb_chapter = ttk.Combobox(g, textvariable=self.v_chapter, width=50, state='readonly')
        self.cmb_chapter.grid(row=1, column=1, sticky='w', pady=(6, 0))
        self.cmb_chapter.bind(
            '<<ComboboxSelected>>',
            lambda e: self._apply('chapter',
                                  self._chapter_label_to_rowname(self.v_chapter.get())))

        lbl = ttk.Label(g, text='Biome:')
        lbl.grid(row=1, column=2, sticky='w', padx=(16, 0), pady=(6, 0))
        self.v_biome = tk.StringVar()
        self.cmb_biome = ttk.Combobox(g, textvariable=self.v_biome, width=30, state='readonly')
        self.cmb_biome.grid(row=1, column=3, sticky='w', pady=(6, 0))
        self.cmb_biome.bind('<<ComboboxSelected>>',
                            lambda e: self._apply('biome', self.v_biome.get()))

        lbl_style = ttk.Label(g, text='Style:')
        lbl_style.grid(row=1, column=4, sticky='w', padx=(16, 0), pady=(6, 0))
        attach_tooltip(lbl_style, 'visual_map_style')
        self.v_style = tk.StringVar()
        self.cmb_style = ttk.Combobox(g, textvariable=self.v_style, width=14, state='readonly')
        self.cmb_style.grid(row=1, column=5, sticky='w', pady=(6, 0))
        self.cmb_style.bind('<<ComboboxSelected>>',
                            lambda e: self._apply('style', self.v_style.get()))
        attach_tooltip(self.cmb_style, 'visual_map_style')

        ttk.Label(g, text='Position:').grid(row=2, column=0, sticky='w', pady=6)
        pf = ttk.Frame(g); pf.grid(row=2, column=1, sticky='w')
        self.v_px, self.v_py, self.v_pz = tk.IntVar(), tk.IntVar(), tk.IntVar()
        for i, (lbl, var) in enumerate([('X', self.v_px), ('Y', self.v_py), ('Z', self.v_pz)]):
            ttk.Label(pf, text=lbl).pack(side=tk.LEFT, padx=(0 if i == 0 else 8, 2))
            sp = ttk.Spinbox(pf, from_=-20, to=30, width=5, textvariable=var,
                             command=self._apply_pos)
            sp.pack(side=tk.LEFT); sp.bind('<FocusOut>', lambda e: self._apply_pos())

        lbl_size = ttk.Label(g, text='Size:')
        lbl_size.grid(row=2, column=2, sticky='w', padx=(16, 0))
        attach_tooltip(lbl_size, 'target_size')
        sf = ttk.Frame(g); sf.grid(row=2, column=3, sticky='w')
        self.v_sx, self.v_sy, self.v_sz = tk.IntVar(), tk.IntVar(), tk.IntVar()
        for i, (lbl, var) in enumerate([('X', self.v_sx), ('Y', self.v_sy), ('Z', self.v_sz)]):
            ttk.Label(sf, text=lbl).pack(side=tk.LEFT, padx=(0 if i == 0 else 8, 2))
            sp = ttk.Spinbox(sf, from_=1, to=30, width=5, textvariable=var,
                             command=self._apply_size)
            sp.pack(side=tk.LEFT); sp.bind('<FocusOut>', lambda e: self._apply_size())

        ttk.Label(g, text='Temp:').grid(row=3, column=0, sticky='w', pady=6)
        self.v_temp = tk.DoubleVar()
        sp = ttk.Spinbox(g, from_=0.0, to=100.0, increment=1.0, width=8,
                         textvariable=self.v_temp,
                         command=lambda: self._apply_float('temp', self.v_temp))
        sp.grid(row=3, column=1, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply_float('temp', self.v_temp))

        ttk.Label(g, text='Water:').grid(row=3, column=2, sticky='w', padx=(16, 0))
        self.v_water = tk.DoubleVar()
        sp = ttk.Spinbox(g, from_=0.0, to=10.0, increment=0.1, width=8,
                         textvariable=self.v_water,
                         command=lambda: self._apply_float('water', self.v_water))
        sp.grid(row=3, column=3, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply_float('water', self.v_water))

        ttk.Label(g, text='Light:').grid(row=3, column=4, sticky='w', padx=(16, 0))
        self.v_light = tk.DoubleVar()
        sp = ttk.Spinbox(g, from_=0.0, to=10.0, increment=0.1, width=8,
                         textvariable=self.v_light,
                         command=lambda: self._apply_float('light', self.v_light))
        sp.grid(row=3, column=5, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply_float('light', self.v_light))

        ttk.Label(g, text='LightCurve:').grid(row=3, column=6, sticky='w', padx=(16, 0))
        self.v_curve = tk.IntVar()
        sp = ttk.Spinbox(g, from_=-20, to=20, width=6, textvariable=self.v_curve,
                         command=lambda: self._apply_int('curve', self.v_curve))
        sp.grid(row=3, column=7, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply_int('curve', self.v_curve))

        ttk.Label(g, text='Lighting:').grid(row=4, column=0, sticky='w', pady=6)
        self.v_lighting = tk.StringVar()
        self.cmb_lighting = ttk.Combobox(g, textvariable=self.v_lighting, width=18, state='readonly')
        self.cmb_lighting.grid(row=4, column=1, sticky='w')
        self.cmb_lighting.bind('<<ComboboxSelected>>',
                               lambda e: self._apply('lighting', self.v_lighting.get()))

        ttk.Label(g, text='Toast:').grid(row=4, column=2, sticky='w', padx=(16, 0))
        self.v_toast = tk.StringVar()
        self.cmb_toast = ttk.Combobox(g, textvariable=self.v_toast, width=18, state='readonly')
        self.cmb_toast.grid(row=4, column=3, sticky='w')
        self.cmb_toast.bind('<<ComboboxSelected>>',
                            lambda e: self._apply('toast', self.v_toast.get()))

        # ---- Bubble / Passage deck references ----
        deckf = ttk.LabelFrame(self, text='Bubble Sources', padding=6)
        deckf.pack(fill=tk.X, pady=(6, 0))
        dg = ttk.Frame(deckf); dg.pack(fill=tk.X)

        ttk.Label(dg, text='BubbleDeck:').grid(row=0, column=0, sticky='w', padx=(0, 4))
        self.v_bdeck = tk.StringVar()
        self.cmb_bdeck = ttk.Combobox(dg, textvariable=self.v_bdeck, width=36, state='readonly')
        self.cmb_bdeck.grid(row=0, column=1, sticky='w')
        self.cmb_bdeck.bind('<<ComboboxSelected>>', lambda e: self._apply('bdeck', self.v_bdeck.get()))

        ttk.Label(dg, text='PassageDeck:').grid(row=0, column=2, sticky='w', padx=(16, 4))
        self.v_pdeck = tk.StringVar()
        self.cmb_pdeck = ttk.Combobox(dg, textvariable=self.v_pdeck, width=36, state='readonly')
        self.cmb_pdeck.grid(row=0, column=3, sticky='w')
        self.cmb_pdeck.bind('<<ComboboxSelected>>', lambda e: self._apply('pdeck', self.v_pdeck.get()))

        ttk.Button(dg, text='Edit BubbleDeck →',
                   command=lambda: self._jump_to_deck(self.v_bdeck.get())
                   ).grid(row=0, column=4, sticky='w', padx=(10, 0))
        ttk.Button(dg, text='Edit PassageDeck →',
                   command=lambda: self._jump_to_deck(self.v_pdeck.get())
                   ).grid(row=0, column=5, sticky='w', padx=(6, 0))

        ttk.Label(deckf, text='Bubbles that this zone can spawn:',
                  foreground='#555').pack(anchor='w', pady=(6, 2))
        self.bubble_preview = tk.Listbox(deckf, height=4)
        self.bubble_preview.pack(fill=tk.X)

        # ---- Generation Tuning (per-zone aggression dials) ----
        tunef = ttk.LabelFrame(self, text='Generation Tuning', padding=6)
        tunef.pack(fill=tk.X, pady=(6, 0))
        tg = ttk.Frame(tunef); tg.pack(fill=tk.X)

        # NewBubbleChance: 0.0..1.0
        lbl = ttk.Label(tg, text='NewBubbleChance:')
        lbl.grid(row=0, column=0, sticky='w', pady=(0, 4))
        attach_tooltip(lbl,
            'Chance the engine grows an extra bubble while filling this zone. '
            'Range 0..1. Higher = denser interior. 0.5 is the vanilla default.')
        self.v_new_bubble = tk.DoubleVar()
        sp = ttk.Spinbox(tg, from_=0.0, to=1.0, increment=0.05, width=8,
                         textvariable=self.v_new_bubble,
                         command=lambda: self._apply_float('new_bubble', self.v_new_bubble))
        sp.grid(row=0, column=1, sticky='w', padx=(4, 16))
        sp.bind('<FocusOut>', lambda e: self._apply_float('new_bubble', self.v_new_bubble))

        # AdditionalOpeningChance: 0.0..1.0
        lbl = ttk.Label(tg, text='AdditionalOpeningChance:')
        lbl.grid(row=0, column=2, sticky='w', pady=(0, 4))
        attach_tooltip(lbl,
            'Chance the engine carves an extra opening between bubbles. '
            'Range 0..1. Higher = more interconnections, less linear paths. '
            '0.75 is most-zones default; 1.0 = always.')
        self.v_add_opening = tk.DoubleVar()
        sp = ttk.Spinbox(tg, from_=0.0, to=1.0, increment=0.05, width=8,
                         textvariable=self.v_add_opening,
                         command=lambda: self._apply_float('add_opening', self.v_add_opening))
        sp.grid(row=0, column=3, sticky='w', padx=(4, 16))
        sp.bind('<FocusOut>', lambda e: self._apply_float('add_opening', self.v_add_opening))

        # GenerationPriority: integer (typically 1..120)
        lbl = ttk.Label(tg, text='GenerationPriority:')
        lbl.grid(row=0, column=4, sticky='w', pady=(0, 4))
        attach_tooltip(lbl,
            'Order in which the engine attempts to place this zone. '
            'Higher = placed earlier (better real estate). Elevators/anchors '
            'are 1-5; rooms 90-120; rare/filler 40-60.')
        self.v_gen_priority = tk.IntVar()
        sp = ttk.Spinbox(tg, from_=0, to=200, width=8,
                         textvariable=self.v_gen_priority,
                         command=lambda: self._apply_int('gen_priority', self.v_gen_priority))
        sp.grid(row=0, column=5, sticky='w', padx=(4, 16))
        sp.bind('<FocusOut>', lambda e: self._apply_int('gen_priority', self.v_gen_priority))

        # bExtendFootprint
        self.v_extend_fp = tk.BooleanVar()
        chk = ttk.Checkbutton(tg, text='Extend footprint',
                               variable=self.v_extend_fp,
                               command=self._apply_extend_fp)
        chk.grid(row=0, column=6, sticky='w', padx=(4, 0))
        attach_tooltip(chk,
            'When True, the zone may grow past its TargetSize during '
            'generation if the engine needs more room. Most rooms = True; '
            'elevators/special = False (rigid footprint).')

        # ---- Editable Landmarks ----
        lmf = ttk.LabelFrame(self, text='Landmarks', padding=6)
        lmf.pack(fill=tk.X, pady=(6, 0))
        lm_bar = ttk.Frame(lmf); lm_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(lm_bar, text='Add Landmark…',
                   command=self._add_landmark_dialog).pack(side=tk.LEFT)
        ttk.Button(lm_bar, text='Remove Selected',
                   command=self._remove_landmark).pack(side=tk.LEFT, padx=6)
        ttk.Button(lm_bar, text='Edit Selected…',
                   command=self._edit_landmark_dialog).pack(side=tk.LEFT)

        cols = ('lm', 'placement', 'ext')
        self.lm_tree = ttk.Treeview(lmf, columns=cols, show='headings', height=4)
        self.lm_tree.heading('lm', text='Landmark')
        self.lm_tree.heading('placement', text='Placement')
        self.lm_tree.heading('ext', text='Extended Connectivity')
        self.lm_tree.column('lm', width=320)
        self.lm_tree.column('placement', width=120)
        self.lm_tree.column('ext', width=150)
        self.lm_tree.pack(fill=tk.X)

    # ---- dropdowns / table ----
    def _populate_dropdowns(self):
        all_views = [ZoneView(r) for r in self.doc.rows]
        chapters = {v.chapter for v in all_views if v.chapter}
        for x in EXTRA_CHAPTER_OPTIONS:
            chapters.add(x)
        chap_doc = self.app.docs.get('chapters')
        if chap_doc:
            for r in chap_doc.rows:
                nm = r.get('Name', '')
                if nm: chapters.add(nm)

        # Build pretty labels: "Ground — chapter-1", "1st Floor — chapter-2",
        # "7th Deep — chapter-8", etc. The dropdown shows the label and we
        # translate label <-> chapter row name on select / load.
        # Sorted by Layer descending (top floor first), unknown layers last.
        self._chapter_label_to_name = {}
        self._chapter_name_to_label = {}
        labelled = []
        for cn in chapters:
            label = self._chapter_display_label(cn)
            self._chapter_label_to_name[label] = cn
            self._chapter_name_to_label[cn] = label
            layer = self._chapter_layer_int(cn)
            # Sort key: layer (desc), unknowns last
            sort_key = (0 if layer is not None else 1,
                        -(layer if layer is not None else 0),
                        natural_key(cn))
            labelled.append((sort_key, label))
        labelled.sort(key=lambda x: x[0])
        self.cmb_chapter['values'] = [lbl for _, lbl in labelled]

        biomes = sorted({v.biome for v in all_views if v.biome}, key=natural_key)
        self.cmb_biome['values'] = biomes

        styles = set(VISUAL_MAP_STYLES)
        styles.update({v.visual_map_style for v in all_views if v.visual_map_style})
        self.cmb_style['values'] = sorted(styles, key=natural_key)

        lightings = sorted({v.zone_lighting_behavior for v in all_views
                            if v.zone_lighting_behavior}, key=natural_key)
        self.cmb_lighting['values'] = lightings or ['Normal']

        toasts = sorted({v.toast_appearance for v in all_views if v.toast_appearance}, key=natural_key)
        self.cmb_toast['values'] = toasts or ['Automatic']

        # Deck dropdowns — source from DT_Moria_ZoneDeck
        deck_doc = self.app.docs.get('decks')
        deck_names = sorted((r.get('Name', '') for r in (deck_doc.rows if deck_doc else [])
                             if r.get('Name')), key=natural_key)
        self.cmb_bdeck['values'] = deck_names
        self.cmb_pdeck['values'] = deck_names

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        # "Hide unassigned zones" filter: hide zones whose primary Chapter is
        # not a Live SandboxSmall chapter (empty chapter, missing row, non-SS
        # ZoneSet, or Disabled). The corresponding chapter parent row is also
        # omitted in grouped mode.
        hide_unassigned = bool(getattr(self, '_hide_unassigned',
                                        tk.BooleanVar(value=False)).get())

        def _zone_hidden_by_unassigned(z):
            if not hide_unassigned:
                return False
            return not self._chapter_is_live_ss(z.chapter)

        if ENABLE_ZONE_CHAPTER_GROUPING:
            # Build chapter parent rows in Layer-descending order (top floor
            # first). Group zones under their Chapter rowname. Zones with an
            # unknown chapter (e.g. legacy 'None' / 'EXTRA' rows) go under a
            # synthetic '(no chapter)' parent so they remain visible.
            chapter_iids = {}  # chapter_rowname -> tree iid
            # Build sort order from the dropdown labels we already computed
            # in _populate_dropdowns: Layer desc, sandbox before unknown.
            order = []
            seen_chapters = {z.chapter for z in self.zones}
            # Always include all known chapters that have any zones AND every
            # chapter in the chapters DT (so empty chapters still accept drops).
            chap_doc = self.app.docs.get('chapters')
            all_chap_names = set(seen_chapters)
            if chap_doc:
                for r in chap_doc.rows:
                    nm = r.get('Name')
                    if nm:
                        all_chap_names.add(nm)
            for cn in all_chap_names:
                if not cn:
                    continue
                layer = self._chapter_layer_int(cn)
                # Skip chapter parents whose underlying row is not a Live SS
                # chapter (non-SS ZoneSet, Disabled, or missing) when filter on.
                if hide_unassigned and not self._chapter_is_live_ss(cn):
                    continue
                sort_key = (0 if layer is not None else 1,
                            -(layer if layer is not None else 0),
                            natural_key(cn))
                order.append((sort_key, cn))
            order.sort(key=lambda x: x[0])

            for _, cn in order:
                iid = ZONE_CHAPTER_IID_PREFIX + cn
                label = self._chapter_display_label(cn)
                tag = chapter_color_tag(cn) or ''
                tags = ('zone-chapter-parent',)
                if tag:
                    tags = tags + (tag,)
                # Show the chapter label in the '#0' tree column. Other
                # columns are blank for parents.
                self.tree.insert('', 'end', iid=iid, text=label,
                                 values=('',) * len(self.tree['columns']),
                                 open=True, tags=tags)
                chapter_iids[cn] = iid

            # Synthetic catch-all for zones whose chapter doesn't resolve
            no_chap_iid = ZONE_CHAPTER_IID_PREFIX + '(no chapter)'
            no_chap_inserted = False
            for z in self.zones:
                if _zone_hidden_by_unassigned(z):
                    continue
                parent = chapter_iids.get(z.chapter)
                if parent is None:
                    if not no_chap_inserted:
                        self.tree.insert('', 'end', iid=no_chap_iid,
                                         text='(no chapter)',
                                         values=('',) * len(self.tree['columns']),
                                         open=True,
                                         tags=('zone-chapter-parent',))
                        no_chap_inserted = True
                    parent = no_chap_iid
                self._insert_row(z, parent=parent)
        else:
            for z in self.zones:
                if _zone_hidden_by_unassigned(z):
                    continue
                self._insert_row(z)
        self._refresh_count()
        self.apply_sort(self.tree)

    def _row_tags(self, z):
        tag = chapter_color_tag(z.chapter)
        tags = [tag] if tag else []
        if z.name in self.modified: tags.append(MODIFIED_TAG)
        if not z.is_enabled: tags.append(DISABLED_TAG)
        return tuple(tags)

    def _chapter_layer_int(self, chapter_name):
        """Return chapter Layer as int, or None if unknown."""
        if not chapter_name:
            return None
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return None
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name:
                p = find_prop(r.get('Value', []), 'Layer')
                if p is not None:
                    v = p.get('Value')
                    if v is not None:
                        try: return int(v)
                        except (TypeError, ValueError): return None
        return None

    def _chapter_is_live_ss(self, chapter_name):
        """True iff chapter_name resolves to a chapter row whose ZoneSet is
        SandboxSmall and EnabledState is not Disabled. Used by the
        'Hide unassigned zones' filter to drop orphans, non-SS chapters,
        and disabled chapters."""
        if not chapter_name:
            return False
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return False
        for r in chap_doc.rows:
            if r.get('Name') != chapter_name:
                continue
            zs_p = find_prop(r.get('Value', []), 'ZoneSet')
            zs = get_enum(zs_p) if zs_p is not None else ''
            if zs != 'SandboxSmall':
                return False
            es_p = find_prop(r.get('Value', []), 'EnabledState')
            es = get_enum(es_p) if es_p is not None else 'Live'
            if es == 'Disabled':
                return False
            return True
        # No matching row in DT_Moria_Chapters
        return False

    @staticmethod
    def _ordinal(n):
        """1 -> '1st', 2 -> '2nd', 3 -> '3rd', else 'Nth'. Used for floor/deep labels."""
        if 10 <= (n % 100) <= 20:
            return f'{n}th'
        return f'{n}{["th","st","nd","rd","th","th","th","th","th","th"][n % 10]}'

    def _chapter_display_label(self, chapter_name):
        """Pretty label for a chapter, like 'Ground - chapter-1' or
        '7th Deep - chapter-8'. Falls back to the bare row name when Layer
        is unavailable (e.g. campaign chapters with Layer=0 but no sandbox role)."""
        if not chapter_name:
            return ''
        L = self._chapter_layer_int(chapter_name)
        # SandboxSmall sandbox chapters get the friendly Lv/Deep prefix.
        # Campaign chapters at Layer 0 are also labelled as 'Ground' but their
        # row name (e.g. 'Moria-DurinTower') makes the context obvious.
        is_sandbox = chapter_name.startswith('SandboxSmall-')
        if L is None:
            return chapter_name
        if L == 0:
            prefix = 'Ground' if is_sandbox else 'Layer 0'
        elif L > 0:
            prefix = f'{self._ordinal(L)} Floor' if is_sandbox else f'Layer +{L}'
        else:
            prefix = f'{self._ordinal(-L)} Deep' if is_sandbox else f'Layer {L}'
        return f'{prefix} - {chapter_name}'

    def _chapter_label_to_rowname(self, label):
        """Reverse lookup. Accepts either a pretty label or a raw chapter name
        (for backward compatibility)."""
        if not label:
            return ''
        if label in getattr(self, '_chapter_label_to_name', {}):
            return self._chapter_label_to_name[label]
        # Fallback: maybe the user pasted a raw name
        return label

    def _chapter_layer(self, chapter_name):
        """Return the LEVEL label for the Zones-tab "Layer" column.

        Maps raw Layer ints to human-readable level numbers:
            Layer  0 -> 1   (ground floor = Lv-1)
            Layer +1 -> 2  (Lv-2)
            Layer +6 -> 7  (Lv-7)
            Layer -1 -> -1 (D-1)
            Layer -7 -> -7 (D-7)

        Negative Layers display as the negative integer (matching D-N labels
        used elsewhere). Non-negative Layers shift by +1 so ground floor never
        displays as 0 (the player thinks of it as 'first floor').
        """
        if not chapter_name:
            return ''
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return ''
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name:
                p = find_prop(r.get('Value', []), 'Layer')
                if p is not None:
                    v = p.get('Value')
                    if v is None:
                        continue
                    try:
                        L = int(v)
                    except (TypeError, ValueError):
                        return str(v)
                    # Map: ground+ => +1 offset; deeps => raw negative
                    return str(L + 1) if L >= 0 else str(L)
        return ''

    def _row_values(self, z):
        px, py, pz = z.position; sx, sy, sz = z.target_size
        return (z.name, self._chapter_layer(z.chapter), z.chapter, z.biome, z.visual_map_style,
                f'({px},{py},{pz})', f'({sx},{sy},{sz})',
                f'{z.zone_temperature:g}', f'{z.water_prevalence:g}',
                f'{z.light_prevalence:g}', 'Yes' if z.is_enabled else 'No')

    def _insert_row(self, z, parent=''):
        self.tree.insert(parent, 'end', iid=z.name, values=self._row_values(z),
                         tags=self._row_tags(z))

    def _refresh_row(self, z):
        if not self.tree.exists(z.name):
            # Pick the right parent for grouped mode
            parent = ''
            if ENABLE_ZONE_CHAPTER_GROUPING and z.chapter:
                cand = ZONE_CHAPTER_IID_PREFIX + z.chapter
                if self.tree.exists(cand):
                    parent = cand
            self._insert_row(z, parent=parent); return
        self.tree.item(z.name, values=self._row_values(z), tags=self._row_tags(z))
        # If grouped and this zone's chapter changed, reparent it.
        if ENABLE_ZONE_CHAPTER_GROUPING and z.chapter:
            want_parent = ZONE_CHAPTER_IID_PREFIX + z.chapter
            if self.tree.exists(want_parent):
                cur_parent = self.tree.parent(z.name)
                if cur_parent != want_parent:
                    try:
                        self.tree.move(z.name, want_parent, 'end')
                    except Exception:
                        pass

    def _refresh_count(self):
        self.status_lbl.config(
            text=self._count_label_text())

    def _count_label_text(self):
        # Group the currently-visible zones by ZoneSet for a clear count
        from collections import Counter
        zs = Counter(z.zone_set or '(unset)' for z in self.zones)
        parts = [f'{v} {k}' for k, v in sorted(zs.items(), key=lambda kv: natural_key(kv[0]))]
        summary = ', '.join(parts) if parts else 'no zones'
        return f'{summary}  |  {len(self.modified)} modified'

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        # Ignore chapter parent rows (grouped-tree mode)
        if sel[0].startswith(ZONE_CHAPTER_IID_PREFIX):
            return
        z = next((zz for zz in self.zones if zz.name == sel[0]), None)
        if z: self._populate_detail(z)

    def _populate_detail(self, z):
        self.current = z
        self._updating = True
        try:
            self.v_name.set(z.name)
            self.v_enabled.set(z.is_enabled)
            # Show pretty label in dropdown when we have one mapped, else raw name
            self.v_chapter.set(getattr(self, '_chapter_name_to_label', {}).get(z.chapter, z.chapter))
            self.v_biome.set(z.biome)
            self.v_style.set(z.visual_map_style)
            px, py, pz = z.position
            self.v_px.set(px); self.v_py.set(py); self.v_pz.set(pz)
            sx, sy, sz = z.target_size
            self.v_sx.set(sx); self.v_sy.set(sy); self.v_sz.set(sz)
            self.v_temp.set(z.zone_temperature)
            self.v_water.set(z.water_prevalence)
            self.v_light.set(z.light_prevalence)
            self.v_curve.set(z.lighting_curve)
            self.v_lighting.set(z.zone_lighting_behavior)
            self.v_toast.set(z.toast_appearance)
            self.v_bdeck.set(z.bubble_deck)
            self.v_pdeck.set(z.passage_deck)
            # Generation tuning
            def _read_num(name, default):
                for p in z.row.get('Value', []):
                    if isinstance(p, dict) and p.get('Name') == name:
                        v = p.get('Value')
                        if v is None: return default
                        try: return type(default)(v)
                        except (TypeError, ValueError): return default
                return default
            self.v_new_bubble.set(_read_num('NewBubbleChance', 0.5))
            self.v_add_opening.set(_read_num('AdditionalOpeningChance', 0.75))
            self.v_gen_priority.set(_read_num('GenerationPriority', 100))
            self.v_extend_fp.set(bool(_read_num('bExtendFootprint', False)))
            self._refresh_bubble_preview(z)
            self._refresh_landmarks()
        finally:
            self._updating = False

    def _refresh_bubble_preview(self, z):
        self.bubble_preview.delete(0, tk.END)
        deck_doc = self.app.docs.get('decks')
        if deck_doc is None:
            return
        wanted = {z.bubble_deck, z.passage_deck} - {'', 'None'}
        for deck_row in deck_doc.rows:
            if deck_row.get('Name') not in wanted:
                continue
            which = deck_row['Name']
            label = 'bubble' if which == z.bubble_deck else 'passage'
            for entry in DeckView(deck_row).entries():
                tag = ''
                if entry['appearances']:
                    tag = f"[{entry['appearances']}]"
                entrance = ' (entrance)' if entry['zone_entrance'] else ''
                self.bubble_preview.insert(
                    tk.END,
                    f"  {label}: {entry['bubble']} {tag}{entrance}")
        if self.bubble_preview.size() == 0:
            self.bubble_preview.insert(tk.END, '  (deck empty or not found)')

    def _refresh_landmarks(self):
        self.lm_tree.delete(*self.lm_tree.get_children())
        if self.current is None:
            return
        for i, e in enumerate(self.current.landmark_entries()):
            self.lm_tree.insert('', 'end', iid=str(i),
                                values=(e['landmark'], e['placement'] or '—',
                                        'Yes' if e['extended'] else ''))

    def _apply(self, field, value):
        if self._updating or self.current is None: return
        z = self.current
        if field == 'chapter':
            z.set_chapter(value)
            self._auto_align_z_to_chapter(z, value)
        elif field == 'biome': z.set_biome(value)
        elif field == 'style': z.set_visual_map_style(value)
        elif field == 'lighting': z.set_zone_lighting_behavior(value)
        elif field == 'toast': z.set_toast_appearance(value)
        elif field == 'bdeck':
            z.set_bubble_deck(value); self._refresh_bubble_preview(z)
        elif field == 'pdeck':
            z.set_passage_deck(value); self._refresh_bubble_preview(z)
        self._mark(z)

    def _jump_to_deck(self, deck_name):
        if not deck_name or deck_name == 'None':
            return
        if hasattr(self.app, 'nb') and hasattr(self.app, 'bubble_tab'):
            # Pass the zone context so the Bubbles tab can show both decks
            # as quick-toggle buttons at the top (Bubbles vs Passages).
            bt = self.app.bubble_tab
            if self.current is not None:
                bt.set_zone_context(self.current.name,
                                     self.current.bubble_deck,
                                     self.current.passage_deck,
                                     selected=deck_name)
            self.app.nb.select(bt)
            if bt.tree.exists(deck_name):
                bt.tree.selection_set(deck_name)
                bt.tree.see(deck_name)
                bt._on_select()

    def _auto_align_z_to_chapter(self, zone, chapter_name):
        """When a zone's chapter changes, shift Pos.Z to the new chapter's
        MinZ so the zone falls inside the target chapter's Z-band. Warns via
        messagebox if the zone's Size.Z exceeds the chapter's height (zone
        will stick out the top of the chapter)."""
        if not chapter_name:
            return
        chap_doc = self.app.docs.get('chapters')
        if chap_doc is None:
            return
        # Find the chapter row matching chapter_name
        target = None
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name:
                target = r; break
        if target is None:
            return
        minz_prop = find_prop(target.get('Value', []), 'MinZ')
        maxz_prop = find_prop(target.get('Value', []), 'MaxZ')
        if minz_prop is None or maxz_prop is None:
            return
        try:
            minz = int(minz_prop.get('Value', 0))
            maxz = int(maxz_prop.get('Value', 0))
        except (TypeError, ValueError):
            return
        chap_h = maxz - minz + 1
        # Current zone position/size
        px, py, pz = zone.position
        sx, sy, sz = zone.target_size
        if pz != minz:
            zone.set_position(px, py, minz)
            # Mirror into the UI spinbox without triggering _apply_pos recursion
            prev = self._updating
            self._updating = True
            try:
                self.v_pz.set(minz)
            except tk.TclError:
                pass
            self._updating = prev
        if sz > chap_h:
            try:
                messagebox.showwarning(
                    'Zone taller than chapter',
                    f'Zone "{zone.name}" has Size.Z={sz} but chapter '
                    f'"{chapter_name}" only spans {chap_h} Z-cells '
                    f'(Z={minz}..{maxz}).\n\n'
                    f'The zone will extend above the chapter\'s top. '
                    f'Either grow the chapter (raise MaxZ) or shrink the zone.')
            except Exception:
                pass

    def _apply_pos(self):
        if self._updating or self.current is None: return
        try:
            self.current.set_position(self.v_px.get(), self.v_py.get(), self.v_pz.get())
        except tk.TclError: return
        self._mark(self.current)

    def _apply_size(self):
        if self._updating or self.current is None: return
        try:
            self.current.set_target_size(self.v_sx.get(), self.v_sy.get(), self.v_sz.get())
        except tk.TclError: return
        self._mark(self.current)

    def _apply_float(self, which, var):
        if self._updating or self.current is None: return
        try: v = float(var.get())
        except (tk.TclError, ValueError): return
        z = self.current
        if which == 'temp': z.set_zone_temperature(v)
        elif which == 'water': z.set_water_prevalence(v)
        elif which == 'light': z.set_light_prevalence(v)
        elif which == 'new_bubble': self._set_zone_prop_float(z, 'NewBubbleChance', v)
        elif which == 'add_opening': self._set_zone_prop_float(z, 'AdditionalOpeningChance', v)
        self._mark(z)

    def _apply_int(self, which, var):
        if self._updating or self.current is None: return
        try: v = int(var.get())
        except (tk.TclError, ValueError): return
        z = self.current
        if which == 'curve': z.set_lighting_curve(v)
        elif which == 'gen_priority':
            self._set_zone_prop_int(z, 'GenerationPriority', v)
        self._mark(z)

    def _apply_extend_fp(self):
        if self._updating or self.current is None: return
        z = self.current
        self._set_zone_prop_bool(z, 'bExtendFootprint', bool(self.v_extend_fp.get()))
        self._mark(z)

    @staticmethod
    def _set_zone_prop_float(z, name, value):
        for p in z.row.get('Value', []):
            if isinstance(p, dict) and p.get('Name') == name:
                p['Value'] = float(value)
                return

    @staticmethod
    def _set_zone_prop_int(z, name, value):
        for p in z.row.get('Value', []):
            if isinstance(p, dict) and p.get('Name') == name:
                p['Value'] = int(value)
                return

    @staticmethod
    def _set_zone_prop_bool(z, name, value):
        for p in z.row.get('Value', []):
            if isinstance(p, dict) and p.get('Name') == name:
                p['Value'] = bool(value)
                return

    def _apply_enabled(self):
        if self._updating or self.current is None: return
        self.current.set_enabled(self.v_enabled.get())
        self._mark(self.current)

    def _mark(self, z):
        snap = self.snapshots.get(z.name)
        if snap is not None and json.dumps(z.row, sort_keys=True) != json.dumps(
                snap, sort_keys=True):
            self.modified.add(z.name)
        else:
            self.modified.discard(z.name)
        self._refresh_row(z)
        self._refresh_count()
        self.app.refresh_status()

    def revert_current(self):
        if self.current is None: return
        z = self.current
        snap = self.snapshots.get(z.name)
        if snap is None: return
        z.row.clear(); z.row.update(copy.deepcopy(snap))
        z._values = z.row.get('Value', [])
        self.modified.discard(z.name)
        self._refresh_row(z)
        self._populate_detail(z)
        self._refresh_count()
        self.app.refresh_status()

    # ---- Row CRUD (Add / Copy / Delete) ----
    def _add_zone(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add Zone', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Zone "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_zone(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a zone to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Zone', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Zone "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_zone(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a zone to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete zone',
                f'Delete zone "{name}"?\n\nThis cannot be undone (until you reload).'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        self.app.refresh_status()

    def _rename_zone(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Rename', 'Select a zone first.'); return
        self.app.rename_row('zones', sel[0], parent=self)

    # ---- Landmark editing ----
    def _landmark_picker(self, title, default_lm='', default_place='Fixed',
                         default_ext=False):
        """Open modal dialog; return (lm_rowname, placement, extended) or None."""
        lm_doc = self.app.docs.get('landmarks')
        if lm_doc is None or not lm_doc.rows:
            messagebox.showerror('No landmarks loaded',
                                 'DT_Moria_Landmarks.json not loaded.')
            return None
        names = sorted((r.get('Name', '') for r in lm_doc.rows if r.get('Name')),
                        key=natural_key)

        dlg = tk.Toplevel(self); dlg.title(title)
        dlg.transient(self.winfo_toplevel()); dlg.grab_set()
        dlg.minsize(640, 220); dlg.geometry('680x240')

        # Pin OK/Cancel at the bottom so the window can't clip them.
        result = {'ok': False}
        btns = ttk.Frame(dlg, padding=(10, 8, 10, 10))
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        def do_ok(): result['ok'] = True; dlg.destroy()
        ttk.Button(btns, text='OK', style='Accent.TButton',
                   command=do_ok).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text='Cancel',
                   command=dlg.destroy).pack(side=tk.RIGHT)

        body = ttk.Frame(dlg, padding=(10, 10, 10, 4))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        ttk.Label(body, text='Landmark:').grid(row=0, column=0, sticky='w', pady=(0, 2))
        v_lm = tk.StringVar(value=default_lm or (names[0] if names else ''))
        cb = ttk.Combobox(body, textvariable=v_lm, width=46, state='readonly', values=names)
        cb.grid(row=0, column=1, sticky='w', pady=(0, 2))

        lbl_pl = ttk.Label(body, text='Placement:')
        lbl_pl.grid(row=1, column=0, sticky='w', pady=2)
        attach_tooltip(lbl_pl, 'landmark_placement')
        v_pl = tk.StringVar(value=default_place)
        cmb_pl = ttk.Combobox(body, textvariable=v_pl, width=20, state='readonly',
                     values=LANDMARK_PLACEMENTS)
        cmb_pl.grid(row=1, column=1, sticky='w', pady=2)
        attach_tooltip(cmb_pl, 'landmark_placement')

        v_ext = tk.BooleanVar(value=default_ext)
        chk_ext = ttk.Checkbutton(body, text='Extended connectivity landmark   (is this an elevator / stair between floors?  y/n)',
                        variable=v_ext)
        chk_ext.grid(row=2, column=1, sticky='w', pady=(4, 2))
        attach_tooltip(chk_ext, 'extended_connectivity')

        dlg.wait_window()
        if not result['ok']:
            return None
        return (v_lm.get(), v_pl.get(), v_ext.get())

    def _add_landmark_dialog(self):
        if self.current is None:
            return
        res = self._landmark_picker('Add Landmark')
        if res is None: return
        lm, pl, ext = res
        self.current.add_landmark_entry(lm, pl, ext)
        self._refresh_landmarks()
        self._mark(self.current)

    def _remove_landmark(self):
        if self.current is None: return
        sel = self.lm_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.current.remove_landmark_entry(idx)
        self._refresh_landmarks()
        self._mark(self.current)

    def _edit_landmark_dialog(self):
        if self.current is None: return
        sel = self.lm_tree.selection()
        if not sel: return
        idx = int(sel[0])
        entries = self.current.landmark_entries()
        if not (0 <= idx < len(entries)): return
        cur = entries[idx]
        res = self._landmark_picker('Edit Landmark',
                                     default_lm=cur['landmark'],
                                     default_place=cur['placement'] or 'Fixed',
                                     default_ext=cur['extended'])
        if res is None: return
        lm, pl, ext = res
        self.current.update_landmark_entry(idx, lm, pl, ext)
        self._refresh_landmarks()
        self._mark(self.current)

    # ---- Sort override for grouped tree ----
    def _apply_sort_items(self, tree):
        """Sort children within their chapter parent rather than at the root.
        Falls back to the BaseTab implementation when grouping is disabled."""
        if not ENABLE_ZONE_DRAG_DROP:
            return super()._apply_sort_items(tree)
        col, rev = getattr(tree, '_sort_state', ('', False))
        if not col:
            return
        def keyfn(v):
            s = v[0]
            try:
                return (0, float(s.strip('()').split(',')[0])) if ',' in s else (0, float(s))
            except Exception:
                return (1, BaseTab._natural_key(s))
        for parent in tree.get_children(''):
            if not parent.startswith(ZONE_CHAPTER_IID_PREFIX):
                continue
            items = [(tree.set(k, col), k) for k in tree.get_children(parent)]
            items.sort(key=keyfn, reverse=rev)
            for i, (_, k) in enumerate(items):
                tree.move(k, parent, i)

    # ---- Drag-drop: zone -> chapter ----
    def _dnd_press(self, event):
        """Begin a potential drag. Only zone child rows are draggable."""
        if not ENABLE_ZONE_DRAG_DROP:
            return
        iid = self.tree.identify_row(event.y)
        if not iid or iid.startswith(ZONE_CHAPTER_IID_PREFIX):
            self._dnd_active = False
            return
        # Don't start drag on heading row
        if self.tree.identify_region(event.x, event.y) == 'heading':
            self._dnd_active = False
            return
        self._dnd_active = True
        self._dnd_zone_iid = iid
        self._dnd_press_x = event.x
        self._dnd_press_y = event.y
        self._dnd_last_target = None

    def _dnd_motion(self, event):
        if not ENABLE_ZONE_DRAG_DROP or not self._dnd_active:
            return
        # Require a small drag threshold to disambiguate from click+select
        if (abs(event.x - self._dnd_press_x) < 4
                and abs(event.y - self._dnd_press_y) < 4):
            return
        target = self.tree.identify_row(event.y)
        # Resolve the chapter parent: if we're hovering a zone child, use
        # its parent. If on a chapter parent row directly, use that.
        if target and not target.startswith(ZONE_CHAPTER_IID_PREFIX):
            target = self.tree.parent(target)
        # Update highlight
        if target != self._dnd_last_target:
            self._clear_drag_highlight()
            self._dnd_last_target = target
            if target and target.startswith(ZONE_CHAPTER_IID_PREFIX):
                cur_tags = list(self.tree.item(target, 'tags') or ())
                if ZONE_DRAG_TAG not in cur_tags:
                    cur_tags.append(ZONE_DRAG_TAG)
                    self.tree.item(target, tags=tuple(cur_tags))
        # Tooltip: "Drop to move {zone} to {chapter_label}"
        if target and target.startswith(ZONE_CHAPTER_IID_PREFIX):
            chap_name = target[len(ZONE_CHAPTER_IID_PREFIX):]
            label = self._chapter_display_label(chap_name)
            self._show_drag_tooltip(event, f'Drop to move "{self._dnd_zone_iid}" to {label}')
        else:
            self._hide_drag_tooltip()
        # Visual cursor hint
        try:
            self.tree.config(cursor='exchange')
        except Exception:
            pass
        # Edge auto-scroll: scroll the Treeview when cursor is near top/bottom
        self._update_edge_scroll(event.y)

    # ---- Edge auto-scroll while dragging ----
    EDGE_ZONE_PX = 30
    EDGE_FAST_PX = 10

    def _update_edge_scroll(self, y):
        """Start/stop/adjust the auto-scroll based on cursor y in the tree."""
        try:
            h = self.tree.winfo_height()
        except Exception:
            return
        # If cursor has left the widget vertically, stop scrolling.
        if y < 0 or y > h:
            self._stop_edge_scroll()
            return
        direction = 0
        speed = 1
        if y < self.EDGE_ZONE_PX:
            direction = -1
            speed = 3 if y < self.EDGE_FAST_PX else 1
        elif y > h - self.EDGE_ZONE_PX:
            direction = 1
            speed = 3 if y > h - self.EDGE_FAST_PX else 1
        if direction == 0:
            self._stop_edge_scroll()
            return
        self._dnd_scroll_dir = direction
        self._dnd_scroll_speed = speed
        if self._dnd_scroll_after is None:
            self._edge_scroll_tick()

    def _edge_scroll_tick(self):
        """Recurring tick: scroll one step then reschedule."""
        self._dnd_scroll_after = None
        if not self._dnd_active or self._dnd_scroll_dir == 0:
            return
        try:
            self.tree.yview_scroll(
                self._dnd_scroll_dir * self._dnd_scroll_speed, 'units')
        except Exception:
            return
        try:
            self._dnd_scroll_after = self.tree.after(
                50, self._edge_scroll_tick)
        except Exception:
            self._dnd_scroll_after = None

    def _stop_edge_scroll(self):
        self._dnd_scroll_dir = 0
        if self._dnd_scroll_after is not None:
            try:
                self.tree.after_cancel(self._dnd_scroll_after)
            except Exception:
                pass
            self._dnd_scroll_after = None

    def _dnd_release(self, event):
        if not ENABLE_ZONE_DRAG_DROP or not self._dnd_active:
            self._stop_edge_scroll()
            return
        try:
            self.tree.config(cursor='')
        except Exception:
            pass
        self._stop_edge_scroll()
        self._hide_drag_tooltip()
        self._clear_drag_highlight()
        if self._dnd_last_target is None:
            self._dnd_active = False
            return
        target = self._dnd_last_target
        zone_iid = self._dnd_zone_iid
        self._dnd_active = False
        self._dnd_last_target = None
        if not target or not target.startswith(ZONE_CHAPTER_IID_PREFIX):
            return
        dest_chapter = target[len(ZONE_CHAPTER_IID_PREFIX):]
        if dest_chapter == '(no chapter)':
            return
        # Source chapter
        zv = next((z for z in self.zones if z.name == zone_iid), None)
        if zv is None:
            return
        if zv.chapter == dest_chapter:
            return  # no-op
        # Confirm before firing the pipeline
        if not messagebox.askyesno(
                'Move zone',
                f'Move zone "{zone_iid}" from\n  {zv.chapter or "(no chapter)"}\n'
                f'to\n  {dest_chapter}?\n\nThis will create snapshot backups, '
                f're-anchor positions/landmarks/connections, and run validation.',
                parent=self):
            return
        try:
            mover = ZoneMover(self.app)
            result = mover.move(zone_iid, dest_chapter, parent=self.winfo_toplevel())
        except Exception as e:
            messagebox.showerror('Zone move failed',
                                 f'Pipeline crashed: {e}', parent=self)
            return
        if result is None:
            return  # user cancelled in conflict dialog
        # Show result dialog with roll-back option
        ZoneMoveResultDialog(self.winfo_toplevel(), self.app, result)
        # Refresh tab regardless (changes already applied or rolled back)
        self.app.load_all_after_zone_move()

    def _clear_drag_highlight(self):
        self._stop_edge_scroll()
        if self._dnd_last_target and self.tree.exists(self._dnd_last_target):
            tags = [t for t in (self.tree.item(self._dnd_last_target, 'tags') or ())
                    if t != ZONE_DRAG_TAG]
            try:
                self.tree.item(self._dnd_last_target, tags=tuple(tags))
            except Exception:
                pass

    def _show_drag_tooltip(self, event, text):
        try:
            if self._dnd_tooltip is None:
                tw = tk.Toplevel(self.tree)
                tw.wm_overrideredirect(True)
                lbl = tk.Label(tw, text=text, background='#fffacd',
                               relief='solid', borderwidth=1,
                               font=('Segoe UI', 9), padx=6, pady=2)
                lbl.pack()
                tw._lbl = lbl
                self._dnd_tooltip = tw
            else:
                self._dnd_tooltip._lbl.config(text=text)
            x = self.tree.winfo_rootx() + event.x + 16
            y = self.tree.winfo_rooty() + event.y + 16
            self._dnd_tooltip.geometry(f'+{x}+{y}')
            self._dnd_tooltip.deiconify()
        except Exception:
            pass

    def _hide_drag_tooltip(self):
        try:
            if self._dnd_tooltip is not None:
                self._dnd_tooltip.withdraw()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Right-click "Move to chapter..." flow.
    # ------------------------------------------------------------------
    def _on_zone_right_click(self, event):
        """Show a context menu for the row under the pointer.

        - If the row is a chapter parent (iid starts with the chapter
          prefix) or empty space, do nothing. Move-to-chapter only makes
          sense for an actual zone row.
        - If the row is a zone, identify it via the row's iid (which is
          the zone row name in grouped mode, or also the row name in
          flat mode) and pop the menu.
        """
        if not ENABLE_ZONE_RIGHT_CLICK_MOVE:
            return
        try:
            iid = self.tree.identify_row(event.y)
        except Exception:
            iid = ''
        if not iid:
            return
        # Skip chapter parent rows in grouped mode.
        if iid.startswith(ZONE_CHAPTER_IID_PREFIX):
            return
        # Confirm this iid corresponds to a known zone.
        zv = next((z for z in self.zones if z.name == iid), None)
        if zv is None:
            return
        self._zone_context_target = iid
        # Select the row so the user has visual feedback.
        try:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        except Exception:
            pass
        menu = self._build_zone_context_menu()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _build_zone_context_menu(self):
        """Construct (or reuse) the right-click menu. Currently only one
        item — kept as a separate builder so future additions land in
        one place. Note: Zones tab had no prior right-click menu, so
        nothing pre-existing to merge in here."""
        if self._zone_context_menu is None:
            m = tk.Menu(self.tree, tearoff=0)
            m.add_command(label='Move to chapter…',
                          command=self._move_zone_via_picker)
            m.add_separator()
            # Placeholder for future entries; preserves the spec's
            # "(Whatever the existing right-click context menu items
            # already are - preserve them)" pattern even though the
            # Zones tab had no prior right-click handler.
            self._zone_context_menu = m
        return self._zone_context_menu

    def _move_zone_via_picker(self):
        """Pop the chapter picker, then run the existing ZoneMover
        pipeline on the chosen destination. No-ops if no zone is
        targeted, the destination matches the source, or the user
        cancels in either modal."""
        zone_name = self._zone_context_target
        if not zone_name:
            return
        zv = next((z for z in self.zones if z.name == zone_name), None)
        if zv is None:
            return

        picker = ZoneMoveChapterPicker(self.winfo_toplevel(), self.app, zv)
        try:
            self.wait_window(picker)
        except Exception:
            pass
        dest_chapter = picker.result
        if not dest_chapter:
            return
        if dest_chapter == zv.chapter:
            return  # no-op
        try:
            mover = ZoneMover(self.app)
            result = mover.move(zone_name, dest_chapter,
                                parent=self.winfo_toplevel())
        except Exception as e:
            messagebox.showerror('Zone move failed',
                                 f'Pipeline crashed: {e}', parent=self)
            return
        if result is None:
            return  # user cancelled in conflict dialog
        ZoneMoveResultDialog(self.winfo_toplevel(), self.app, result)
        self.app.load_all_after_zone_move()


# -----------------------------------------------------------------------------
# CHAPTERS TAB
# -----------------------------------------------------------------------------

class ChapterTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs['chapters']
        self.chapters = []
        self.current = None
        self._updating = False
        self._build()

    def refresh_from_doc(self):
        self.chapters = [ChapterView(r) for r in self.doc.rows]
        self._populate_tree()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Chapter',
                   command=self.add_chapter_dialog).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Chapter…',
                   command=self._copy_chapter).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Rename Chapter…',
                   command=self._rename_chapter).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Chapter',
                   command=self._delete_chapter).pack(side=tk.LEFT, padx=(4, 0))
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.LEFT, padx=12)

        headings = [
            ('name', 'Chapter Name', 260, True),
            ('zoneset', 'ZoneSet', 130, False),
            ('cid', 'ChapterID', 80, False),
            ('display', 'DisplayName', 200, False),
            ('layer', 'Layer', 60, False),
            ('scale', 'EnemyScale', 80, False),
            ('minz', 'MinZ', 60, False),
            ('maxz', 'MaxZ', 60, False),
            ('primez', 'PrimeZ', 60, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(self, [h[0] for h in headings], headings,
                                     settings_key='chapters')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        self._build_detail()

    def _build_detail(self):
        d = ttk.LabelFrame(self, text='Chapter Detail', padding=8)
        d.pack(fill=tk.X, pady=(6, 0))
        g = ttk.Frame(d); g.pack(fill=tk.X)

        self.v_name = tk.StringVar(value='(no chapter selected)')
        ttk.Label(g, text='Chapter:', width=10).grid(row=0, column=0, sticky='w')
        ttk.Label(g, textvariable=self.v_name,
                  font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, columnspan=3, sticky='w')
        self.v_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text='Enabled', variable=self.v_enabled,
                        command=self._apply_enabled).grid(row=0, column=4, sticky='e', padx=10)

        lbl_zs = ttk.Label(g, text='ZoneSet:')
        lbl_zs.grid(row=1, column=0, sticky='w', pady=(6, 0))
        attach_tooltip(lbl_zs, 'zone_set')
        self.v_zoneset = tk.StringVar()
        self.cmb_zs = ttk.Combobox(g, textvariable=self.v_zoneset, width=20,
                                    values=['Moria', 'SandboxSmall', 'SandboxMedium', 'Expedition'],
                                    state='readonly')
        self.cmb_zs.grid(row=1, column=1, sticky='w', pady=(6, 0))
        self.cmb_zs.bind('<<ComboboxSelected>>',
                         lambda e: self._apply('zoneset', self.v_zoneset.get()))
        attach_tooltip(self.cmb_zs, 'zone_set')

        lbl_cid = ttk.Label(g, text='ChapterID:')
        lbl_cid.grid(row=1, column=2, sticky='w', padx=(16, 0), pady=(6, 0))
        attach_tooltip(lbl_cid, 'chapter_id')
        self.v_cid = tk.IntVar()
        sp = ttk.Spinbox(g, from_=0, to=100, width=6, textvariable=self.v_cid,
                         command=lambda: self._apply('cid', self.v_cid.get()))
        sp.grid(row=1, column=3, sticky='w', pady=(6, 0))
        sp.bind('<FocusOut>', lambda e: self._apply('cid', self.v_cid.get()))
        attach_tooltip(sp, 'chapter_id')

        ttk.Label(g, text='DisplayName:').grid(row=2, column=0, sticky='w', pady=6)
        self.v_disp = tk.StringVar()
        e = ttk.Entry(g, textvariable=self.v_disp, width=30)
        e.grid(row=2, column=1, columnspan=2, sticky='w')
        e.bind('<FocusOut>', lambda e: self._apply('display', self.v_disp.get()))

        lbl_layer = ttk.Label(g, text='Layer:')
        lbl_layer.grid(row=3, column=0, sticky='w', pady=6)
        attach_tooltip(lbl_layer, 'layer')
        self.v_layer = tk.IntVar()
        sp = ttk.Spinbox(g, from_=-20, to=20, width=6, textvariable=self.v_layer,
                         command=lambda: self._apply('layer', self.v_layer.get()))
        sp.grid(row=3, column=1, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply('layer', self.v_layer.get()))
        attach_tooltip(sp, 'layer')

        ttk.Label(g, text='EnemyScale:').grid(row=3, column=2, sticky='w', padx=(16, 0))
        self.v_scale = tk.IntVar()
        sp = ttk.Spinbox(g, from_=0, to=20, width=6, textvariable=self.v_scale,
                         command=lambda: self._apply('scale', self.v_scale.get()))
        sp.grid(row=3, column=3, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply('scale', self.v_scale.get()))

        lbl_min = ttk.Label(g, text='MinZ:')
        lbl_min.grid(row=4, column=0, sticky='w', pady=6)
        attach_tooltip(lbl_min, 'min_z')
        self.v_minz = tk.IntVar()
        sp = ttk.Spinbox(g, from_=-1000, to=1000, width=8, textvariable=self.v_minz,
                         command=lambda: self._apply('minz', self.v_minz.get()))
        sp.grid(row=4, column=1, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply('minz', self.v_minz.get()))
        attach_tooltip(sp, 'min_z')

        lbl_max = ttk.Label(g, text='MaxZ:')
        lbl_max.grid(row=4, column=2, sticky='w', padx=(16, 0))
        attach_tooltip(lbl_max, 'max_z')
        self.v_maxz = tk.IntVar()
        sp = ttk.Spinbox(g, from_=-1000, to=1000, width=8, textvariable=self.v_maxz,
                         command=lambda: self._apply('maxz', self.v_maxz.get()))
        sp.grid(row=4, column=3, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply('maxz', self.v_maxz.get()))
        attach_tooltip(sp, 'max_z')

        lbl_pz = ttk.Label(g, text='PrimeZ:')
        lbl_pz.grid(row=4, column=4, sticky='w', padx=(16, 0))
        attach_tooltip(lbl_pz, 'prime_z')
        self.v_primez = tk.IntVar()
        sp = ttk.Spinbox(g, from_=-1000, to=1000, width=8, textvariable=self.v_primez,
                         command=lambda: self._apply('primez', self.v_primez.get()))
        sp.grid(row=4, column=5, sticky='w')
        sp.bind('<FocusOut>', lambda e: self._apply('primez', self.v_primez.get()))

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for c in self.chapters:
            self._insert_row(c)
        self.status_lbl.config(text=f'{len(self.chapters)} chapters')
        self.apply_sort(self.tree)

    def _insert_row(self, c):
        tags = []
        if not c.is_enabled: tags.append(DISABLED_TAG)
        self.tree.insert('', 'end', iid=c.name,
                         values=(c.name, c.zone_set, c.chapter_id, c.display_name,
                                 c.layer, c.enemy_scaling, c.min_z, c.max_z,
                                 c.prime_z, 'Yes' if c.is_enabled else 'No'),
                         tags=tuple(tags))

    def _refresh_row(self, c):
        if self.tree.exists(c.name):
            tags = []
            if not c.is_enabled: tags.append(DISABLED_TAG)
            self.tree.item(c.name,
                           values=(c.name, c.zone_set, c.chapter_id, c.display_name,
                                   c.layer, c.enemy_scaling, c.min_z, c.max_z,
                                   c.prime_z, 'Yes' if c.is_enabled else 'No'),
                           tags=tuple(tags))

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        c = next((cc for cc in self.chapters if cc.name == sel[0]), None)
        if c: self._populate_detail(c)

    def _populate_detail(self, c):
        self.current = c
        self._updating = True
        try:
            self.v_name.set(c.name)
            self.v_enabled.set(c.is_enabled)
            self.v_zoneset.set(c.zone_set)
            self.v_cid.set(c.chapter_id)
            self.v_disp.set(c.display_name)
            self.v_layer.set(c.layer)
            self.v_scale.set(c.enemy_scaling)
            self.v_minz.set(c.min_z)
            self.v_maxz.set(c.max_z)
            self.v_primez.set(c.prime_z)
        finally:
            self._updating = False

    def _apply(self, field, value):
        if self._updating or self.current is None: return
        c = self.current
        try:
            if field == 'zoneset': c.set_zone_set(value)
            elif field == 'cid': c.set_chapter_id(int(value))
            elif field == 'display': c.set_display_name(str(value))
            elif field == 'layer': c.set_layer(int(value))
            elif field == 'scale': c.set_enemy_scaling(int(value))
            elif field == 'minz': c.set_min_z(int(value))
            elif field == 'maxz': c.set_max_z(int(value))
            elif field == 'primez': c.set_prime_z(int(value))
        except (ValueError, tk.TclError):
            return
        self._refresh_row(c)
        self.app.refresh_status()

    def _apply_enabled(self):
        if self._updating or self.current is None: return
        self.current.set_enabled(self.v_enabled.get())
        self._refresh_row(self.current)
        self.app.refresh_status()

    def add_chapter_dialog(self):
        dlg = tk.Toplevel(self); dlg.title('Add Chapter'); dlg.resizable(False, False)
        dlg.transient(self.winfo_toplevel()); dlg.grab_set()
        ttk.Label(dlg, text='Row name:').grid(row=0, column=0, padx=8, pady=(8, 2), sticky='w')
        e_name = ttk.Entry(dlg, width=40); e_name.grid(row=1, column=0, padx=8, sticky='w')
        ttk.Label(dlg, text='ChapterID:').grid(row=2, column=0, padx=8, pady=(8, 2), sticky='w')
        e_cid = ttk.Entry(dlg, width=10); e_cid.grid(row=3, column=0, padx=8, sticky='w')
        ttk.Label(dlg, text='ZoneSet:').grid(row=4, column=0, padx=8, pady=(8, 2), sticky='w')
        v_zs = tk.StringVar(value='SandboxSmall')
        ttk.Combobox(dlg, textvariable=v_zs, width=18, state='readonly',
                     values=['Moria', 'SandboxSmall', 'SandboxMedium', 'Expedition']
                     ).grid(row=5, column=0, padx=8, sticky='w')
        ttk.Label(dlg, text='DisplayName:').grid(row=6, column=0, padx=8, pady=(8, 2), sticky='w')
        e_disp = ttk.Entry(dlg, width=40); e_disp.grid(row=7, column=0, padx=8, sticky='w')

        def do_add():
            name = e_name.get().strip()
            if not name:
                messagebox.showerror('Error', 'Row name required.'); return
            if any(c.name == name for c in self.chapters):
                messagebox.showerror('Error', f'Row {name} already exists.'); return
            try: cid = int(e_cid.get().strip() or '0')
            except ValueError:
                messagebox.showerror('Error', 'ChapterID must be an integer.'); return
            self._append_chapter(name, cid, v_zs.get(), e_disp.get().strip() or f'{name}.Name')
            dlg.destroy()

        bt = ttk.Frame(dlg); bt.grid(row=8, column=0, pady=10, padx=8, sticky='e')
        ttk.Button(bt, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bt, text='Add', command=do_add).pack(side=tk.RIGHT, padx=4)
        e_name.focus()

    def add_book_chapters(self):
        existing = {c.name for c in self.chapters}
        to_add = [(n, cid, dn) for (n, cid, dn, _) in BOOK_ACCURATE_CHAPTERS
                  if n not in existing]
        if not to_add:
            messagebox.showinfo('Already added',
                                'All book-accurate chapters (9–15) already exist.')
            return
        preview = '\n'.join(f'  • {n} (ID {cid}) — {dn}' for n, cid, dn in to_add)
        if not messagebox.askyesno('Add book-accurate chapters',
                f'Add the following to DT_Moria_Chapters?\n\n{preview}'):
            return
        for name, cid, disp in to_add:
            self._append_chapter(name, cid, 'SandboxSmall', disp)
        messagebox.showinfo('Added',
            f'Added {len(to_add)} chapter rows.\n\n'
            'They now appear in the Zones tab Chapter dropdown.')

    def _append_chapter(self, name, cid, zone_set, display_name):
        if not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        template = copy.deepcopy(self.doc.rows[0])
        template['Name'] = name
        for p in template.get('Value', []):
            if not isinstance(p, dict): continue
            n = p.get('Name')
            if n == 'ZoneSet': set_enum(p, zone_set)
            elif n == 'ChapterID': p['Value'] = int(cid)
            elif n == 'DisplayName': p['Value'] = display_name
            elif n in ('Layer', 'EnemyScalingLevel', 'MinZ', 'MaxZ', 'PrimeZ'):
                p['Value'] = 0
            elif n == 'EnabledState': set_enum(p, 'Live')
        self.doc.rows.append(template)
        self.doc.reconcile_namemap()
        self.chapters.append(ChapterView(template))
        self._insert_row(self.chapters[-1])
        self.status_lbl.config(text=f'{len(self.chapters)} chapters')
        if hasattr(self.app, 'zone_tab'):
            self.app.zone_tab._populate_dropdowns()
        self.app.refresh_status()

    def _copy_chapter(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a chapter to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Chapter', f'New row name (copying from "{src_name}"):\n'
            'Note: ChapterID is not auto-assigned — edit it after copying.',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Chapter "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.chapters.append(ChapterView(new_row))
        self._insert_row(self.chapters[-1])
        self.status_lbl.config(text=f'{len(self.chapters)} chapters')
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        if hasattr(self.app, 'zone_tab'):
            self.app.zone_tab._populate_dropdowns()
        self.app.refresh_status()

    def _delete_chapter(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a chapter to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete chapter',
                f'Delete chapter "{name}"?\n\nZones referencing it will be orphaned.'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        if hasattr(self.app, 'zone_tab'):
            self.app.zone_tab._populate_dropdowns()
        self.app.refresh_status()

    def _rename_chapter(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Rename', 'Select a chapter first.'); return
        self.app.rename_row('chapters', sel[0], parent=self)


# -----------------------------------------------------------------------------
# BIOMES TAB
# -----------------------------------------------------------------------------

class BiomeTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs['biomes']
        self.biomes = []
        self.current = None
        self._updating = False
        self._build()

    def refresh_from_doc(self):
        self.biomes = [BiomeView(r, self.doc.data) for r in self.doc.rows]
        self._populate_tree()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Biome…',
                   command=self._add_biome).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Biome…',
                   command=self._copy_biome).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Biome',
                   command=self._delete_biome).pack(side=tk.LEFT, padx=(4, 8))
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.LEFT)

        headings = [
            ('name', 'Biome Name', 240, True),
            ('disp', 'DisplayName', 280, False),
            ('audio', 'AudioConfig', 260, False),
            ('deco', 'DecoConfig', 260, False),
            ('rock', 'RockConfig', 240, False),
            ('atmos', 'AtmosphericsConfig', 240, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(self, [h[0] for h in headings], headings,
                                     settings_key='biomes')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        self._build_detail()

    def _build_detail(self):
        d = ttk.LabelFrame(self, text='Biome Detail', padding=8)
        d.pack(fill=tk.X, pady=(6, 0))
        top = ttk.Frame(d); top.pack(fill=tk.X)
        self.v_name = tk.StringVar(value='(no biome selected)')
        ttk.Label(top, text='Biome:', width=10).grid(row=0, column=0, sticky='w')
        ttk.Label(top, textvariable=self.v_name,
                  font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, columnspan=3, sticky='w')
        self.v_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='Enabled', variable=self.v_enabled,
                        command=self._apply_enabled).grid(row=0, column=4, sticky='e', padx=10)

        ttk.Label(top, text='DisplayName:').grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.v_disp = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.v_disp, width=50)
        e.grid(row=1, column=1, columnspan=3, sticky='w', pady=(6, 0))
        e.bind('<FocusOut>', lambda _e: self._apply_display())

        ttk.Label(d, text='Object references (read-only):',
                  foreground='#555').pack(anchor='w', pady=(8, 2))
        rf = ttk.Frame(d); rf.pack(fill=tk.BOTH, expand=False)
        cols = ('field', 'value')
        self.ref_tree = ttk.Treeview(rf, columns=cols, show='headings', height=6)
        self.ref_tree.heading('field', text='Field')
        self.ref_tree.heading('value', text='References asset')
        self.ref_tree.column('field', width=220, stretch=False)
        self.ref_tree.column('value', width=800, stretch=True)
        vsb = ttk.Scrollbar(rf, orient='vertical', command=self.ref_tree.yview)
        self.ref_tree.configure(yscrollcommand=vsb.set)
        self.ref_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for b in self.biomes:
            refs = dict(((n, txt) for n, _i, txt in b.object_ref_fields()))
            tags = [] if b.is_enabled else [DISABLED_TAG]
            self.tree.insert('', 'end', iid=b.name,
                             values=(b.name, b.display_name,
                                     refs.get('AudioConfig', ''),
                                     refs.get('DecoConfig', ''),
                                     refs.get('RockConfig', ''),
                                     refs.get('AtmosphericsConfig', ''),
                                     'Yes' if b.is_enabled else 'No'),
                             tags=tuple(tags))
        self.status_lbl.config(text=f'{len(self.biomes)} biomes')
        self.apply_sort(self.tree)

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        b = next((bb for bb in self.biomes if bb.name == sel[0]), None)
        if b: self._populate_detail(b)

    def _populate_detail(self, b):
        self.current = b
        self._updating = True
        try:
            self.v_name.set(b.name)
            self.v_enabled.set(b.is_enabled)
            self.v_disp.set(b.display_name)
            self.ref_tree.delete(*self.ref_tree.get_children())
            for fname, ref, txt in b.object_ref_fields():
                self.ref_tree.insert('', 'end', values=(fname, f'{txt}  [ref={ref}]'))
        finally:
            self._updating = False

    def _apply_display(self):
        if self._updating or self.current is None: return
        self.current.set_display_name(self.v_disp.get())
        row_id = self.current.name
        if self.tree.exists(row_id):
            vals = list(self.tree.item(row_id, 'values'))
            vals[1] = self.current.display_name
            self.tree.item(row_id, values=tuple(vals))
        self.app.refresh_status()

    def _apply_enabled(self):
        if self._updating or self.current is None: return
        self.current.set_enabled(self.v_enabled.get())
        row_id = self.current.name
        if self.tree.exists(row_id):
            vals = list(self.tree.item(row_id, 'values'))
            vals[-1] = 'Yes' if self.current.is_enabled else 'No'
            tags = [] if self.current.is_enabled else [DISABLED_TAG]
            self.tree.item(row_id, values=tuple(vals), tags=tuple(tags))
        self.app.refresh_status()

    # ---- Row CRUD ----
    def _add_biome(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add Biome', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Biome "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_biome(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a biome to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Biome', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Biome "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_biome(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a biome to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete biome',
                f'Delete biome "{name}"?\n\nZones referencing it will be orphaned.'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        self.app.refresh_status()


# -----------------------------------------------------------------------------
# BUBBLES (ZONEDECK) TAB — editable
# -----------------------------------------------------------------------------

class BubbleTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs['decks']
        self.decks = []
        self.current = None
        self._build()

    def refresh_from_doc(self):
        self.decks = [DeckView(r) for r in self.doc.rows]
        self._populate_tree()

    def all_bubbles(self):
        """Gather known bubble names from every source, so the picker lists
        EVERY bubble the generator could legally use — not just bubbles that
        already happen to appear in a deck entry.

        Sources scanned (in order, deduped):
          1. DeckEntries across all ZoneDeck rows
          2. BubbleFilter Whitelist/Blacklist entries
          3. Landmark BaseBubbleName (covers bubbles used only as landmark anchors)
          4. BC_GameWorldCatalog (authoritative master list — strips 'BF_' prefix
             from each registered /Game/.../BF_BB_*.* path)
        """
        names = set()
        # 1. DeckEntries
        for d in self.decks:
            for e in d.entries():
                if e['bubble']:
                    names.add(e['bubble'])
        # 2. Filter whitelist/blacklist
        fdoc = self.app.docs.get('filters')
        if fdoc:
            for r in fdoc.rows:
                for p in r.get('Value', []):
                    if p.get('Name') in ('Whitelist', 'Blacklist'):
                        for item in p.get('Value', []) or []:
                            if isinstance(item, dict):
                                v = item.get('Value', '')
                                if v:
                                    names.add(str(v))
        # 3. Landmark BaseBubbleName (new — covers landmark-only bubbles)
        lm_doc = self.app.docs.get('landmarks')
        if lm_doc:
            for r in lm_doc.rows:
                for p in r.get('Value', []) or []:
                    if isinstance(p, dict) and p.get('Name') == 'BaseBubbleName':
                        v = p.get('Value', '')
                        if v:
                            names.add(str(v))
        # 4. BC_GameWorldCatalog — authoritative master list (new)
        try:
            cat_path = WGR_DIR / 'BC_GameWorldCatalog.json'
            if cat_path.exists():
                with open(cat_path, 'r', encoding='utf-8') as f:
                    cat = json.load(f)
                # The catalog stores paths like
                # '/Game/Tech/Data/BubbleDefs/GameWorldCatalog/BF_BB_Chapter2_*'
                # Strip 'BF_' to recover the 'BB_*' bubble name.
                def walk(node):
                    if isinstance(node, str):
                        if '/BF_BB_' in node:
                            tail = node.rsplit('/', 1)[-1]
                            if tail.startswith('BF_'):
                                names.add(tail[3:])  # drop 'BF_'
                    elif isinstance(node, dict):
                        for v in node.values(): walk(v)
                    elif isinstance(node, list):
                        for v in node: walk(v)
                walk(cat)
        except Exception:
            pass  # best-effort — never block the picker on a catalog read failure
        return sorted(names, key=natural_key)

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Deck…',
                   command=self._add_deck).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Deck…',
                   command=self._copy_deck).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Deck',
                   command=self._delete_deck).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(toolbar, text='ZoneDecks feed bubbles into zones',
                  foreground='#555').pack(side=tk.LEFT)
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)

        # Context strip — when the user jumps here from a zone, this shows the
        # zone's BubbleDeck and PassageDeck side-by-side as one-click toggles.
        # Stays empty otherwise.
        self._ctx_frame = ttk.LabelFrame(
            self, text='Zone context (from Zones tab)', padding=6)
        # Not packed yet — set_zone_context() packs it when needed.
        self._ctx_row = ttk.Frame(self._ctx_frame)
        self._ctx_row.pack(fill=tk.X)
        self._ctx_zone_lbl = ttk.Label(self._ctx_row, text='',
                                         font=('Segoe UI', 10, 'bold'))
        self._ctx_zone_lbl.pack(side=tk.LEFT, padx=(0, 12))
        self._ctx_bubble_btn = ttk.Button(self._ctx_row, text='',
                                           command=self._ctx_go_bubble)
        self._ctx_bubble_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._ctx_passage_btn = ttk.Button(self._ctx_row, text='',
                                            command=self._ctx_go_passage)
        self._ctx_passage_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(self._ctx_row, text='Clear',
                   command=self.clear_zone_context).pack(side=tk.RIGHT)
        self._ctx_zone = None
        self._ctx_bubble_deck = None
        self._ctx_passage_deck = None

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        self._paned = paned  # anchor used when inserting the context strip

        left = ttk.Frame(paned); paned.add(left, weight=1)
        headings = [
            ('name', 'ZoneDeck Name', 280, True),
            ('count', '# Bubbles', 90, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(left, [h[0] for h in headings], headings,
                                     settings_key='bubbles')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = ttk.Frame(paned); paned.add(right, weight=2)
        rtb = ttk.Frame(right); rtb.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(rtb, text='Deck Entries:').pack(side=tk.LEFT)
        ttk.Button(rtb, text='Add…', command=self._add_entry_dialog).pack(side=tk.RIGHT)
        ttk.Button(rtb, text='Edit…', command=self._edit_entry_dialog).pack(side=tk.RIGHT, padx=6)
        ttk.Button(rtb, text='Remove', command=self._remove_entry).pack(side=tk.RIGHT)

        cols = ('bubble', 'appearances', 'entrance')
        self.entry_tree = ttk.Treeview(right, columns=cols, show='headings', height=20)
        self.entry_tree.heading('bubble', text='Bubble')
        self.entry_tree.heading('appearances', text='Appearances')
        self.entry_tree.heading('entrance', text='ZoneEntrance')
        self.entry_tree.column('bubble', width=380)
        self.entry_tree.column('appearances', width=140)
        self.entry_tree.column('entrance', width=110)
        vsb = ttk.Scrollbar(right, orient='vertical', command=self.entry_tree.yview)
        self.entry_tree.configure(yscrollcommand=vsb.set)
        self.entry_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for d in self.decks:
            tags = [] if d.is_enabled else [DISABLED_TAG]
            self.tree.insert('', 'end', iid=d.name,
                             values=(d.name, len(d.entries()),
                                     'Yes' if d.is_enabled else 'No'),
                             tags=tuple(tags))
        self.status_lbl.config(text=f'{len(self.decks)} zone decks')
        self.apply_sort(self.tree)

    def set_zone_context(self, zone_name, bubble_deck, passage_deck,
                          selected=None):
        """Called from Zone tab when jumping here. Shows a header strip with
        one-click toggle buttons for the zone's BubbleDeck vs PassageDeck."""
        self._ctx_zone = zone_name
        self._ctx_bubble_deck = bubble_deck or ''
        self._ctx_passage_deck = passage_deck or ''
        self._ctx_zone_lbl.config(text=f'Zone:  {zone_name}')

        bd_label = f'BubbleDeck: {bubble_deck}' if bubble_deck else 'BubbleDeck: (none)'
        pd_label = f'PassageDeck: {passage_deck}' if passage_deck else 'PassageDeck: (none)'
        self._ctx_bubble_btn.config(
            text=bd_label,
            state=(tk.NORMAL if bubble_deck else tk.DISABLED),
            style=('Accent.TButton' if selected == bubble_deck else 'TButton'))
        self._ctx_passage_btn.config(
            text=pd_label,
            state=(tk.NORMAL if passage_deck else tk.DISABLED),
            style=('Accent.TButton' if selected == passage_deck else 'TButton'))

        if not self._ctx_frame.winfo_ismapped():
            # Slot the context strip between toolbar and paned view
            self._ctx_frame.pack(fill=tk.X, pady=(0, 4), before=self._paned)

    def clear_zone_context(self):
        self._ctx_zone = None
        self._ctx_bubble_deck = None
        self._ctx_passage_deck = None
        if self._ctx_frame.winfo_ismapped():
            self._ctx_frame.pack_forget()

    def _ctx_go_bubble(self):
        if not self._ctx_bubble_deck: return
        self._ctx_select(self._ctx_bubble_deck)

    def _ctx_go_passage(self):
        if not self._ctx_passage_deck: return
        self._ctx_select(self._ctx_passage_deck)

    def _ctx_select(self, deck_name):
        if self.tree.exists(deck_name):
            self.tree.selection_set(deck_name)
            self.tree.see(deck_name)
            self._on_select()
        # Update which button is highlighted
        self.set_zone_context(self._ctx_zone, self._ctx_bubble_deck,
                               self._ctx_passage_deck, selected=deck_name)

    def _refresh_right(self):
        self.entry_tree.delete(*self.entry_tree.get_children())
        if self.current is None: return
        for i, e in enumerate(self.current.entries()):
            self.entry_tree.insert('', 'end', iid=str(i),
                                   values=(e['bubble'], e['appearances'],
                                           'Yes' if e['zone_entrance'] else ''))

    def _refresh_row(self, d):
        if self.tree.exists(d.name):
            tags = [] if d.is_enabled else [DISABLED_TAG]
            self.tree.item(d.name,
                           values=(d.name, len(d.entries()),
                                   'Yes' if d.is_enabled else 'No'),
                           tags=tuple(tags))

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        self.current = next((dd for dd in self.decks if dd.name == sel[0]), None)
        self._refresh_right()

    def _entry_dialog(self, title, bubble='', appearances='Single',
                       zone_entrance=False):
        dlg = tk.Toplevel(self); dlg.title(title); dlg.resizable(False, False)
        dlg.transient(self.winfo_toplevel()); dlg.grab_set()
        ttk.Label(dlg, text='Bubble (BB_* name):').grid(row=0, column=0,
                                                         padx=8, pady=(8, 2), sticky='w')
        v_bubble = tk.StringVar(value=bubble)
        cb = ttk.Combobox(dlg, textvariable=v_bubble, width=44,
                           values=self.all_bubbles())
        cb.grid(row=1, column=0, padx=8, sticky='w')
        lbl_app = ttk.Label(dlg, text='Appearances:')
        lbl_app.grid(row=2, column=0, padx=8, pady=(8, 2), sticky='w')
        attach_tooltip(lbl_app, 'deck_appearance')
        v_ap = tk.StringVar(value=appearances)
        cmb_ap = ttk.Combobox(dlg, textvariable=v_ap, width=16, state='readonly',
                     values=DECK_APPEARANCES)
        cmb_ap.grid(row=3, column=0, padx=8, sticky='w')
        attach_tooltip(cmb_ap, 'deck_appearance')
        v_ze = tk.BooleanVar(value=zone_entrance)
        chk_ze = ttk.Checkbutton(dlg, text='Zone entrance', variable=v_ze)
        chk_ze.grid(row=4, column=0, padx=8, pady=(8, 2), sticky='w')
        attach_tooltip(chk_ze, 'zone_entrance')
        result = {'ok': False}
        def do_ok(): result['ok'] = True; dlg.destroy()
        bf = ttk.Frame(dlg); bf.grid(row=5, column=0, pady=10, padx=8, sticky='e')
        ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bf, text='OK', command=do_ok).pack(side=tk.RIGHT, padx=4)
        cb.focus()
        dlg.wait_window()
        if not result['ok']: return None
        return (v_bubble.get().strip(), v_ap.get(), v_ze.get())

    def _add_entry_dialog(self):
        if self.current is None: return
        res = self._entry_dialog('Add Deck Entry')
        if res is None: return
        bubble, ap, ze = res
        if not bubble:
            messagebox.showerror('Error', 'Bubble name required.'); return
        self.current.add_entry(bubble, ap, ze)
        self._refresh_right(); self._refresh_row(self.current)
        self.app.refresh_status()

    def _edit_entry_dialog(self):
        if self.current is None: return
        sel = self.entry_tree.selection()
        if not sel: return
        idx = int(sel[0])
        entries = self.current.entries()
        if not (0 <= idx < len(entries)): return
        cur = entries[idx]
        res = self._entry_dialog('Edit Deck Entry',
                                  cur['bubble'], cur['appearances'] or 'Single',
                                  cur['zone_entrance'])
        if res is None: return
        bubble, ap, ze = res
        self.current.update_entry(idx, bubble, ap, ze)
        self._refresh_right(); self._refresh_row(self.current)
        self.app.refresh_status()

    def _remove_entry(self):
        if self.current is None: return
        sel = self.entry_tree.selection()
        if not sel: return
        idx = int(sel[0])
        self.current.remove_entry(idx)
        self._refresh_right(); self._refresh_row(self.current)
        self.app.refresh_status()

    # ---- Row CRUD (Deck list) ----
    def _add_deck(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add ZoneDeck', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Deck "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_deck(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a deck to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy ZoneDeck', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Deck "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_deck(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a deck to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete ZoneDeck',
                f'Delete deck "{name}"?\n\nZones referencing it will be orphaned.'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        self.app.refresh_status()


# -----------------------------------------------------------------------------
# FILTERS (ZoneBubbleFilters) TAB
# -----------------------------------------------------------------------------

class FilterTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs.get('filters')
        self.filters = []
        self.current = None
        self._build()

    def refresh_from_doc(self):
        self.filters = [FilterView(r) for r in (self.doc.rows if self.doc else [])]
        self._populate_tree()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Filter…',
                   command=self._add_filter).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Filter…',
                   command=self._copy_filter).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Filter',
                   command=self._delete_filter).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(toolbar, text='ZoneBubbleFilters: whitelist/blacklist bubbles per filter row',
                  foreground='#555').pack(side=tk.LEFT)
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned); paned.add(left, weight=1)
        headings = [
            ('name', 'Filter Name', 220, True),
            ('wl', '# Whitelist', 100, False),
            ('bl', '# Blacklist', 100, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(left, [h[0] for h in headings], headings,
                                     settings_key='filters')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = ttk.Frame(paned); paned.add(right, weight=2)
        # Whitelist
        wlf = ttk.LabelFrame(right, text='Whitelist (bubbles allowed)', padding=6)
        wlf.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        wlbar = ttk.Frame(wlf); wlbar.pack(fill=tk.X)
        ttk.Button(wlbar, text='Add…', command=lambda: self._add_to('wl')).pack(side=tk.LEFT)
        ttk.Button(wlbar, text='Remove', command=lambda: self._remove_from('wl')).pack(side=tk.LEFT, padx=6)
        self.wl_list = tk.Listbox(wlf, height=8)
        self.wl_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        blf = ttk.LabelFrame(right, text='Blacklist (bubbles forbidden)', padding=6)
        blf.pack(fill=tk.BOTH, expand=True)
        blbar = ttk.Frame(blf); blbar.pack(fill=tk.X)
        ttk.Button(blbar, text='Add…', command=lambda: self._add_to('bl')).pack(side=tk.LEFT)
        ttk.Button(blbar, text='Remove', command=lambda: self._remove_from('bl')).pack(side=tk.LEFT, padx=6)
        self.bl_list = tk.Listbox(blf, height=8)
        self.bl_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for f in self.filters:
            tags = [] if f.is_enabled else [DISABLED_TAG]
            self.tree.insert('', 'end', iid=f.name,
                             values=(f.name, len(f.whitelist()),
                                     len(f.blacklist()),
                                     'Yes' if f.is_enabled else 'No'),
                             tags=tuple(tags))
        self.status_lbl.config(text=f'{len(self.filters)} filter rows')
        self.apply_sort(self.tree)

    def _refresh_row(self, f):
        if self.tree.exists(f.name):
            tags = [] if f.is_enabled else [DISABLED_TAG]
            self.tree.item(f.name,
                           values=(f.name, len(f.whitelist()), len(f.blacklist()),
                                   'Yes' if f.is_enabled else 'No'),
                           tags=tuple(tags))

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        self.current = next((ff for ff in self.filters if ff.name == sel[0]), None)
        self._refresh_lists()

    def _refresh_lists(self):
        self.wl_list.delete(0, tk.END)
        self.bl_list.delete(0, tk.END)
        if self.current is None: return
        for bb in self.current.whitelist():
            self.wl_list.insert(tk.END, bb)
        for bb in self.current.blacklist():
            self.bl_list.insert(tk.END, bb)

    def _known_bubbles(self):
        bt = self.app.bubble_tab if hasattr(self.app, 'bubble_tab') else None
        return bt.all_bubbles() if bt else []

    def _add_to(self, which):
        if self.current is None: return
        dlg = tk.Toplevel(self); dlg.title('Add bubble'); dlg.resizable(False, False)
        dlg.transient(self.winfo_toplevel()); dlg.grab_set()
        ttk.Label(dlg, text='Bubble (BB_* name):').grid(row=0, column=0, padx=8, pady=(8, 2), sticky='w')
        v = tk.StringVar()
        cb = ttk.Combobox(dlg, textvariable=v, width=44, values=self._known_bubbles())
        cb.grid(row=1, column=0, padx=8, sticky='w')
        ok = {'v': False}
        def do(): ok['v'] = True; dlg.destroy()
        bf = ttk.Frame(dlg); bf.grid(row=2, column=0, pady=10, padx=8, sticky='e')
        ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bf, text='OK', command=do).pack(side=tk.RIGHT, padx=4)
        cb.focus(); dlg.wait_window()
        if not ok['v']: return
        bb = v.get().strip()
        if not bb: return
        if which == 'wl':
            items = self.current.whitelist(); items.append(bb)
            self.current.set_whitelist(items)
        else:
            items = self.current.blacklist(); items.append(bb)
            self.current.set_blacklist(items)
        self._refresh_lists(); self._refresh_row(self.current)
        self.app.refresh_status()

    def _remove_from(self, which):
        if self.current is None: return
        lst = self.wl_list if which == 'wl' else self.bl_list
        sel = lst.curselection()
        if not sel: return
        idx = sel[0]
        items = self.current.whitelist() if which == 'wl' else self.current.blacklist()
        if 0 <= idx < len(items):
            items.pop(idx)
            if which == 'wl': self.current.set_whitelist(items)
            else: self.current.set_blacklist(items)
        self._refresh_lists(); self._refresh_row(self.current)
        self.app.refresh_status()

    # ---- Row CRUD ----
    def _add_filter(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add Filter', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Filter "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_filter(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a filter to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Filter', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Filter "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_filter(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a filter to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete filter', f'Delete filter "{name}"?'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        self.app.refresh_status()


# -----------------------------------------------------------------------------
# LANDMARKS TAB
# -----------------------------------------------------------------------------

class LandmarkTab(BaseTab):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs.get('landmarks')
        self.landmarks = []
        self.current = None
        self._updating = False
        self._build()

    def refresh_from_doc(self):
        self.landmarks = [LandmarkView(r) for r in (self.doc.rows if self.doc else [])]
        self._populate_tree()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(toolbar, text='Add Landmark…',
                   command=self._add_landmark_row).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Copy Landmark…',
                   command=self._copy_landmark_row).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Rename Landmark…',
                   command=self._rename_landmark).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(toolbar, text='Delete Landmark',
                   command=self._delete_landmark_row).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(toolbar, text='Refresh',
                   command=self.refresh_from_doc).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(toolbar, text='Landmarks anchor bubbles + drive zone connectivity',
                  foreground='#555').pack(side=tk.LEFT)
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned); paned.add(left, weight=1)
        headings = [
            ('name', 'Landmark', 280, True),
            ('bubble', 'BaseBubbleName', 260, False),
            ('placement', 'Placement', 110, False),
            ('conns', '# Connections', 100, False),
            ('start', 'PlayerStart', 90, False),
            ('enabled', 'Enabled', 70, False),
        ]
        self.tree = self.make_tree(left, [h[0] for h in headings], headings,
                                     settings_key='landmarks')
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        # Orphan landmarks (no Live zone hosts them via LandmarkHandles):
        # red text only — no background fill (was hard to read).
        self.tree.tag_configure('orphan', foreground='#c0392b')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        # Hover tooltip for orphan reasons.
        self._orphan_reasons = {}  # name -> reason string
        self.tree.bind('<Motion>', self._on_tree_motion)
        self.tree.bind('<Leave>', lambda _e: self._hide_orphan_tip())
        self._tip_window = None
        self._tip_for_row = None

        right = ttk.Frame(paned); paned.add(right, weight=1)
        self._build_detail(right)

    def _build_detail(self, parent):
        d = ttk.LabelFrame(parent, text='Landmark Detail', padding=8)
        d.pack(fill=tk.BOTH, expand=True)
        g = ttk.Frame(d); g.pack(fill=tk.X)

        self.v_name = tk.StringVar(value='(no landmark selected)')
        ttk.Label(g, text='Landmark:', width=10).grid(row=0, column=0, sticky='w')
        ttk.Label(g, textvariable=self.v_name,
                  font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, columnspan=3, sticky='w')
        self.v_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text='Enabled', variable=self.v_enabled,
                        command=self._apply_enabled).grid(row=0, column=4, sticky='e', padx=10)

        ttk.Label(g, text='BaseBubbleName:').grid(row=1, column=0, sticky='w', pady=(6, 0))
        self.v_bubble = tk.StringVar()
        e = ttk.Entry(g, textvariable=self.v_bubble, width=40)
        e.grid(row=1, column=1, columnspan=3, sticky='w', pady=(6, 0))
        e.bind('<FocusOut>', lambda _e: self._apply_bubble())

        ttk.Label(g, text='Placement:').grid(row=2, column=0, sticky='w', pady=6)
        self.v_pl = tk.StringVar()
        self.cmb_pl = ttk.Combobox(g, textvariable=self.v_pl, width=20, state='readonly',
                                    values=['Fixed', 'Free', 'Sidequest', 'Unspecified'])
        self.cmb_pl.grid(row=2, column=1, sticky='w')
        self.cmb_pl.bind('<<ComboboxSelected>>', lambda _e: self._apply_placement())

        ttk.Label(g, text='Challenge:').grid(row=2, column=2, sticky='w', padx=(16, 0))
        self.v_ch = tk.IntVar()
        sp = ttk.Spinbox(g, from_=0, to=20, width=6, textvariable=self.v_ch,
                         command=self._apply_challenge)
        sp.grid(row=2, column=3, sticky='w')
        sp.bind('<FocusOut>', lambda _e: self._apply_challenge())

        self.v_start = tk.BooleanVar()
        ttk.Checkbutton(g, text='Player start location',
                        variable=self.v_start,
                        command=self._apply_start).grid(row=3, column=1, sticky='w', pady=(6, 0))

        cf = ttk.LabelFrame(d, text='Guaranteed Connections', padding=6)
        cf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        cbar = ttk.Frame(cf); cbar.pack(fill=tk.X)
        ttk.Button(cbar, text='Add…', command=self._add_connection).pack(side=tk.LEFT)
        ttk.Button(cbar, text='Remove', command=self._remove_connection).pack(side=tk.LEFT, padx=6)
        self.conn_list = tk.Listbox(cf, height=8)
        self.conn_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    # ---- orphan detection ----
    def _compute_orphan_reasons(self):
        """Walk every Live zone in DT_Moria_Zones, collect every landmark
        referenced via LandmarkHandles. Any Live landmark NOT in that
        referenced set is an orphan. Returns dict: landmark_name -> reason.
        Disabled landmarks are not flagged (they're intentionally inactive)."""
        reasons = {}
        zone_doc = self.app.docs.get('zones')
        if zone_doc is None:
            return reasons
        # Build set of landmark RowNames referenced by any Live zone
        referenced = set()
        for r in zone_doc.rows:
            es = find_prop(r.get('Value', []), 'EnabledState')
            if es and 'Disabled' in str(es.get('Value', '')):
                continue
            lh = find_prop(r.get('Value', []), 'LandmarkHandles')
            for e in (lh.get('Value') or []) if lh else []:
                for sub in e.get('Value') or []:
                    if isinstance(sub, dict) and sub.get('Name') == 'Landmark':
                        v = sub.get('Value')
                        if isinstance(v, list):
                            for it in v:
                                if isinstance(it, dict) and it.get('Name') == 'RowName':
                                    rn = it.get('Value')
                                    if rn:
                                        referenced.add(rn)
        # Each Live landmark not referenced = orphan
        for lm in self.landmarks:
            if not lm.is_enabled:
                continue
            if lm.name not in referenced:
                reasons[lm.name] = (
                    f'No Live zone hosts "{lm.name}" via LandmarkHandles. '
                    'A landmark must be referenced by at least one Live zone '
                    'to anchor its bubble in the world. Add this landmark to '
                    'a zone\'s LandmarkHandles, or disable the landmark if '
                    'it is intentionally unused.'
                )
        return reasons

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._orphan_reasons = self._compute_orphan_reasons()
        n_orphan = 0
        for lm in self.landmarks:
            tags = [] if lm.is_enabled else [DISABLED_TAG]
            if lm.name in self._orphan_reasons:
                tags.append('orphan'); n_orphan += 1
            self.tree.insert('', 'end', iid=lm.name,
                             values=(lm.name, lm.base_bubble_name, lm.placement,
                                     len(lm.connections()),
                                     'Yes' if lm.player_start else '',
                                     'Yes' if lm.is_enabled else 'No'),
                             tags=tuple(tags))
        self.status_lbl.config(
            text=f'{len(self.landmarks)} landmarks  ·  {n_orphan} orphan(s)')
        self.apply_sort(self.tree)

    def _refresh_row(self, lm):
        if self.tree.exists(lm.name):
            tags = [] if lm.is_enabled else [DISABLED_TAG]
            if lm.name in self._orphan_reasons:
                tags.append('orphan')
            self.tree.item(lm.name,
                           values=(lm.name, lm.base_bubble_name, lm.placement,
                                   len(lm.connections()),
                                   'Yes' if lm.player_start else '',
                                   'Yes' if lm.is_enabled else 'No'),
                           tags=tuple(tags))

    # ---- orphan tooltip ----
    def _on_tree_motion(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id or row_id not in self._orphan_reasons:
            self._hide_orphan_tip()
            self._tip_for_row = None
            return
        if row_id == self._tip_for_row:
            return
        self._tip_for_row = row_id
        self._show_orphan_tip(event, self._orphan_reasons[row_id])

    def _show_orphan_tip(self, event, text):
        self._hide_orphan_tip()
        x = self.tree.winfo_rootx() + event.x + 16
        y = self.tree.winfo_rooty() + event.y + 14
        tw = tk.Toplevel(self.tree)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        tw.configure(background='#202020')
        tk.Label(tw, text=text, justify='left', wraplength=420,
                 background='#fffacd', foreground='#1a1a1a',
                 borderwidth=1, relief='solid',
                 font=('Segoe UI', 9), padx=8, pady=6).pack()
        self._tip_window = tw

    def _hide_orphan_tip(self):
        if self._tip_window is not None:
            try: self._tip_window.destroy()
            except Exception: pass
            self._tip_window = None

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel: return
        lm = next((x for x in self.landmarks if x.name == sel[0]), None)
        if lm: self._populate_detail(lm)

    def _populate_detail(self, lm):
        self.current = lm
        self._updating = True
        try:
            self.v_name.set(lm.name)
            self.v_enabled.set(lm.is_enabled)
            self.v_bubble.set(lm.base_bubble_name)
            self.v_pl.set(lm.placement)
            self.v_ch.set(lm.challenge_rating)
            self.v_start.set(lm.player_start)
            self._refresh_conn_list()
        finally:
            self._updating = False

    def _refresh_conn_list(self):
        self.conn_list.delete(0, tk.END)
        if self.current is None: return
        for c in self.current.connections():
            self.conn_list.insert(tk.END, c)

    def _apply_bubble(self):
        if self._updating or self.current is None: return
        self.current.set_base_bubble(self.v_bubble.get().strip())
        self._refresh_row(self.current); self.app.refresh_status()

    def _apply_placement(self):
        if self._updating or self.current is None: return
        self.current.set_placement(self.v_pl.get())
        self._refresh_row(self.current); self.app.refresh_status()

    def _apply_challenge(self):
        if self._updating or self.current is None: return
        try: v = int(self.v_ch.get())
        except (tk.TclError, ValueError): return
        self.current.set_challenge(v); self.app.refresh_status()

    def _apply_start(self):
        if self._updating or self.current is None: return
        self.current.set_player_start(self.v_start.get())
        self._refresh_row(self.current); self.app.refresh_status()

    def _apply_enabled(self):
        if self._updating or self.current is None: return
        self.current.set_enabled(self.v_enabled.get())
        self._refresh_row(self.current); self.app.refresh_status()

    def _add_connection(self):
        if self.current is None: return
        # Pick from OTHER landmarks
        dlg = tk.Toplevel(self); dlg.title('Add Guaranteed Connection')
        dlg.resizable(False, False); dlg.transient(self.winfo_toplevel()); dlg.grab_set()
        ttk.Label(dlg, text='Connect this landmark to:').grid(
            row=0, column=0, padx=8, pady=(8, 2), sticky='w')
        names = [lm.name for lm in self.landmarks if lm.name != self.current.name]
        v = tk.StringVar(value=names[0] if names else '')
        cb = ttk.Combobox(dlg, textvariable=v, width=44, state='readonly', values=names)
        cb.grid(row=1, column=0, padx=8, sticky='w')
        ok = {'v': False}
        def do(): ok['v'] = True; dlg.destroy()
        bf = ttk.Frame(dlg); bf.grid(row=2, column=0, pady=10, padx=8, sticky='e')
        ttk.Button(bf, text='Cancel', command=dlg.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bf, text='OK', command=do).pack(side=tk.RIGHT, padx=4)
        cb.focus(); dlg.wait_window()
        if not ok['v']: return
        target = v.get().strip()
        if not target: return
        conns = self.current.connections()
        if target in conns:
            messagebox.showinfo('Already connected',
                                f'{self.current.name} already connects to {target}.')
            return
        conns.append(target)
        self.current.set_connections(conns)
        self._refresh_conn_list(); self._refresh_row(self.current)
        self.app.refresh_status()

    def _remove_connection(self):
        if self.current is None: return
        sel = self.conn_list.curselection()
        if not sel: return
        idx = sel[0]
        conns = self.current.connections()
        if 0 <= idx < len(conns):
            conns.pop(idx)
            self.current.set_connections(conns)
            self._refresh_conn_list(); self._refresh_row(self.current)
            self.app.refresh_status()

    # ---- Row CRUD (Landmark list) ----
    def _add_landmark_row(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add Landmark', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Landmark "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_landmark_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a landmark to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Landmark', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Landmark "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_landmark_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a landmark to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete landmark',
                f'Delete landmark "{name}"?\n\n'
                'Zones and LayoutConnections referencing it will be orphaned.'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current = None
        self.refresh_from_doc()
        self.app.refresh_status()

    def _rename_landmark(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Rename', 'Select a landmark first.'); return
        self.app.rename_row('landmarks', sel[0], parent=self)


# -----------------------------------------------------------------------------
# MAPPINGS TAB — the one-glance relationship view
# -----------------------------------------------------------------------------

class MappingsTab(BaseTab):
    """Read-only grid showing Zone → Chapter/Biome/BubbleDeck/PassageDeck/Landmarks.
    Double-click any cell to jump to that item in the right tab."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._show_all = tk.BooleanVar(value=False)
        self._build()

    def refresh_from_doc(self):
        self._populate_tree()

    def _build(self):
        hdr = ttk.Frame(self); hdr.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(hdr, text='Zone relationships',
                  font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT)
        ttk.Checkbutton(hdr, text='Include campaign zones',
                        variable=self._show_all,
                        command=self._populate_tree).pack(side=tk.LEFT, padx=(12, 0))
        self.status_lbl = ttk.Label(hdr, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)
        ttk.Label(self, text='Double-click any Chapter / Biome / Deck / Landmark '
                             'cell to jump to its editor tab.',
                  foreground='#555').pack(anchor='w', pady=(0, 4))

        headings = [
            ('name', 'Zone', 300, True),
            ('layer', 'Layer', 50, False),
            ('chapter', 'Chapter', 180, False),
            ('biome', 'Biome', 220, False),
            ('bdeck', 'BubbleDeck', 180, False),
            ('pdeck', 'PassageDeck', 180, False),
            ('lms', 'Landmarks', 300, False),
            ('ch_pos', 'Pos', 100, False),
        ]
        self.tree = self.make_tree(self, [h[0] for h in headings], headings,
                                     height=20, settings_key='mappings')
        for ch, c in CHAPTER_COLORS.items():
            self.tree.tag_configure(ch, background=c)
        self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
        self.tree.bind('<Double-1>', self._on_double_click)

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        zone_doc = self.app.docs.get('zones')
        if zone_doc is None: return
        zones = [ZoneView(r) for r in zone_doc.rows]
        if not self._show_all.get():
            zones = [z for z in zones if z.zone_set == 'SandboxSmall']
        # Build chapter -> Layer cache once for this populate
        chap_doc = self.app.docs.get('chapters')
        layer_by_chap = {}
        if chap_doc:
            for cr in chap_doc.rows:
                p = find_prop(cr.get('Value', []), 'Layer')
                if p is not None:
                    layer_by_chap[cr.get('Name', '')] = p.get('Value')
        for z in zones:
            lms = ', '.join(e['landmark'] for e in z.landmark_entries()
                            if e['landmark']) or '—'
            px, py, pz = z.position
            tag = chapter_color_tag(z.chapter)
            tags = [tag] if tag else []
            if not z.is_enabled: tags.append(DISABLED_TAG)
            layer_val = layer_by_chap.get(z.chapter, '')
            layer_str = str(layer_val) if layer_val is not None and layer_val != '' else ''
            self.tree.insert('', 'end', iid=z.name,
                             values=(z.name, layer_str, z.chapter or '—', z.biome or '—',
                                     z.bubble_deck or '—', z.passage_deck or '—',
                                     lms, f'({px},{py},{pz})'),
                             tags=tuple(tags))
        self.status_lbl.config(text=f'{len(zones)} zones')
        self.apply_sort(self.tree)

    def _on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell':
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)   # '#1'...'#7'
        if not row_id or not col_id: return

        col_idx = int(col_id.replace('#', '')) - 1
        values = self.tree.item(row_id, 'values')
        if col_idx >= len(values): return
        cell = values[col_idx]

        # column index -> target tab
        if col_idx == 0:   # Zone name -> Zones tab
            self._jump_to_zones(row_id)
        elif col_idx == 1: # Chapter -> Chapters tab
            self._jump_to('chapter_tab', cell)
        elif col_idx == 2:
            # Biome -> Biomes tab (biome tag lookup)
            # Biome in zone is a gameplay tag; biome table keyed by row name
            # Try to find a matching row; just open the Biomes tab
            self._jump_to('biome_tab', None)
        elif col_idx == 3 or col_idx == 4:
            self._jump_to('bubble_tab', cell)
        elif col_idx == 5:
            # Landmarks cell — pick first one
            first = cell.split(',')[0].strip()
            if first and first != '—':
                self._jump_to('landmark_tab', first)

    def _jump_to_zones(self, zone_name):
        if hasattr(self.app, 'zone_tab'):
            self.app.nb.select(self.app.zone_tab)
            if self.app.zone_tab.tree.exists(zone_name):
                self.app.zone_tab.tree.selection_set(zone_name)
                self.app.zone_tab.tree.see(zone_name)

    def _jump_to(self, tab_attr, target):
        tab = getattr(self.app, tab_attr, None)
        if tab is None: return
        self.app.nb.select(tab)
        if target and hasattr(tab, 'tree') and tab.tree.exists(target):
            tab.tree.selection_set(target)
            tab.tree.see(target)
            if hasattr(tab, '_on_select'):
                tab._on_select()


# -----------------------------------------------------------------------------
# HISTORY TAB — every change vs the pristine game files
# -----------------------------------------------------------------------------

class HistoryTab(BaseTab):
    """Read-only view that diffs every loaded DataTable against its pristine
    sidecar (.original.json) and shows added / removed / modified rows with
    field-level detail."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self._build()

    def refresh_from_doc(self):
        self._populate()

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(toolbar,
                  text='Change history — every difference from the pristine '
                       'game files (.original.json sidecars)',
                  font=('Segoe UI', 11, 'bold')).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Refresh',
                   command=self._populate).pack(side=tk.LEFT, padx=12)
        ttk.Button(toolbar, text='Export to file…',
                   command=self._export).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Expand all',
                   command=self._expand_all).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(toolbar, text='Collapse all',
                   command=self._collapse_all).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT,
                                                        fill='y', padx=10)
        ttk.Button(toolbar, text='Revert Selected',
                   command=self._revert_selected).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Revert ALL changes',
                   command=self._revert_all).pack(side=tk.LEFT, padx=6)
        self.status_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)

        ttk.Label(self,
                  text='+ added   − removed   ~ modified   '
                       'Select any node and click Revert Selected (or right-click) '
                       'to undo that change.',
                  foreground='#555').pack(anchor='w', padx=2, pady=(0, 6))

        # Track what each tree node represents so we can revert it.
        # Maps item id -> ('table', doc_key) | ('row', doc_key, row_name) |
        # ('field', doc_key, row_name, field_name)
        self._node_kind = {}

        body = ttk.Frame(self); body.pack(fill=tk.BOTH, expand=True)
        cols = ('lock', 'what', 'was', 'now')
        self.tree = ttk.Treeview(body, columns=cols, show='tree headings')
        self.tree.heading('#0', text='')
        self.tree.heading('lock', text='Lock')
        self.tree.heading('what', text='Change')
        self.tree.heading('was', text='Was (pristine)')
        self.tree.heading('now', text='Now (current)')
        self.tree.column('#0', width=16, stretch=False)
        self.tree.column('lock', width=50, stretch=False, anchor='center')
        self.tree.column('what', width=340, stretch=False)
        self.tree.column('was', width=300, stretch=True)
        self.tree.column('now', width=300, stretch=True)
        self.tree.tag_configure('added', foreground='#2e8b57')      # sea green
        self.tree.tag_configure('removed', foreground='#b22222')    # firebrick
        self.tree.tag_configure('modified', foreground='#c67c00')   # amber
        self.tree.tag_configure('table', font=('Segoe UI', 10, 'bold'))
        # Locked rows get a muted green to signal "protected"
        self.tree.tag_configure('locked', foreground='#1a7f37')

        vsb = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Right-click on any node offers revert
        self.tree.bind('<Button-3>', self._on_right_click)
        # Left-click on the Lock column toggles the lock checkbox
        self.tree.bind('<Button-1>', self._on_left_click)

    # ---- lock helpers ----
    LOCK_CHECKED = '\u2611'   # ☑
    LOCK_EMPTY = '\u2610'     # ☐

    @staticmethod
    def _lock_key(doc_key, row_name, field_name=None):
        """Stable key under [locks] in the INI. Field=None means whole-row lock."""
        return f'{doc_key}|{row_name}|{field_name or "*"}'

    def _is_row_locked(self, doc_key, row_name):
        return SETTINGS.is_locked(self._lock_key(doc_key, row_name))

    def _is_field_locked(self, doc_key, row_name, field_name):
        return SETTINGS.is_locked(self._lock_key(doc_key, row_name, field_name))

    def _is_change_protected(self, kind_tuple):
        """Is this node (or any of its ancestors/descendants) currently locked?"""
        if not kind_tuple: return False
        kind = kind_tuple[0]
        if kind == 'row':
            _, dk, rn = kind_tuple
            if self._is_row_locked(dk, rn): return True
            # Any locked field under this row also protects the row
            for key in SETTINGS.locked_keys():
                parts = key.split('|')
                if len(parts) == 3 and parts[0] == dk and parts[1] == rn and parts[2] != '*':
                    return True
            return False
        if kind == 'field':
            _, dk, rn, fn = kind_tuple
            return (self._is_row_locked(dk, rn)
                    or self._is_field_locked(dk, rn, fn))
        return False

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        self._node_kind.clear()

        # Collect every changed row across every doc
        changes = []  # list of (doc_key, doc_label, kind, row_name)
        totals = {'added': 0, 'removed': 0, 'modified': 0}
        for doc in self.app.docs.values():
            if doc.data is None:
                continue
            added, removed, modified = doc.change_summary()
            for name in added:
                changes.append((doc.key, doc.label, 'added', name))
                totals['added'] += 1
            for name in removed:
                changes.append((doc.key, doc.label, 'removed', name))
                totals['removed'] += 1
            for name in modified:
                changes.append((doc.key, doc.label, 'modified', name))
                totals['modified'] += 1

        if not changes:
            self.tree.insert('', 'end',
                              values=('', 'No changes vs pristine baseline',
                                      '', ''),
                              tags=('table',))
            self.status_lbl.config(text='No changes')
            return

        # Sort: zones first (users spend 99% of their time here), then other
        # tables in their natural editor order. Within each table, sort by
        # chapter color (if the row is a zone) else by row name.
        doc_order = {'zones': 0, 'chapters': 1, 'landmarks': 2,
                     'decks': 3, 'filters': 4, 'biomes': 5}
        # Build per-zone chapter map so we can group by chapter for aesthetic
        zone_chapter = {}
        z_doc = self.app.docs.get('zones')
        if z_doc is not None:
            for r in z_doc.rows:
                if not isinstance(r, dict): continue
                nm = r.get('Name', '')
                chap = ''
                for p in r.get('Value', []) or []:
                    if isinstance(p, dict) and p.get('Name') == 'Chapter':
                        for it in p.get('Value', []) or []:
                            if isinstance(it, dict) and it.get('Name') == 'RowName':
                                chap = str(it.get('Value', ''))
                zone_chapter[nm] = chap

        def sort_key(c):
            _, _, _, row_name = c
            doc_key = c[0]
            if doc_key == 'zones':
                return (0, zone_chapter.get(row_name, ''), row_name)
            return (doc_order.get(doc_key, 99), row_name)
        changes.sort(key=sort_key)

        # Heading row with running totals
        hdr = (f'{totals["added"]} added   '
               f'{totals["removed"]} removed   '
               f'{totals["modified"]} modified')
        self.tree.insert('', 'end', values=('', hdr, '', ''), tags=('table',))

        kind_prefix = {'added': '+', 'removed': '-', 'modified': '~'}
        for doc_key, doc_label, kind, row_name in changes:
            prefix = kind_prefix[kind]
            chap = zone_chapter.get(row_name, '') if doc_key == 'zones' else ''
            row_tags = [kind]
            ctag = chapter_color_tag(chap)
            if ctag:
                row_tags.append(ctag)

            top_label = f'{prefix} {row_name}'
            context = doc_label
            if doc_key == 'zones' and chap:
                context = f'{doc_label} · {chap}'

            row_lock = self._is_row_locked(doc_key, row_name)
            row_lock_cell = self.LOCK_CHECKED if row_lock else self.LOCK_EMPTY
            if row_lock:
                row_tags.append('locked')

            if kind == 'added':
                row_node = self.tree.insert(
                    '', 'end',
                    values=(row_lock_cell, top_label,
                            '(did not exist)', f'ADDED in {context}'),
                    tags=tuple(row_tags))
                self._node_kind[row_node] = ('row', doc_key, row_name)
            elif kind == 'removed':
                row_node = self.tree.insert(
                    '', 'end',
                    values=(row_lock_cell, top_label,
                            f'was in {context}', '(deleted)'),
                    tags=tuple(row_tags))
                self._node_kind[row_node] = ('row', doc_key, row_name)
            else:   # modified
                doc = self.app.docs[doc_key]
                field_diffs = doc.row_field_diffs(row_name)
                row_node = self.tree.insert(
                    '', 'end',
                    values=(row_lock_cell, top_label, context,
                            f'{len(field_diffs)} field(s) changed'),
                    tags=tuple(row_tags), open=True)
                self._node_kind[row_node] = ('row', doc_key, row_name)
                for field, old_s, new_s in field_diffs:
                    f_lock = self._is_field_locked(doc_key, row_name, field)
                    ftags = ['locked'] if f_lock else []
                    fid = self.tree.insert(
                        row_node, 'end',
                        values=(self.LOCK_CHECKED if f_lock else self.LOCK_EMPTY,
                                f'      {field}', old_s, new_s),
                        tags=tuple(ftags))
                    self._node_kind[fid] = ('field', doc_key, row_name, field)

        # Colour-tag the chapter backgrounds so zone entries from different
        # chapters are visually grouped.
        for ch, col in CHAPTER_COLORS.items():
            self.tree.tag_configure(ch, background=col)

        self.status_lbl.config(
            text=f'+{totals["added"]}  '
                 f'-{totals["removed"]}  '
                 f'~{totals["modified"]} total changes')

    def _expand_all(self):
        def walk(item=''):
            for k in self.tree.get_children(item):
                self.tree.item(k, open=True)
                walk(k)
        walk()

    def _collapse_all(self):
        def walk(item=''):
            for k in self.tree.get_children(item):
                self.tree.item(k, open=False)
                walk(k)
        walk()

    # ---- lock click handler ----
    def _on_left_click(self, event):
        """Toggle the lock checkbox when the user clicks in the Lock column."""
        col = self.tree.identify_column(event.x)
        if col != '#1':          # first displayed data column = 'lock'
            return   # fall through to the default selection behaviour
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        kind_tuple = self._node_kind.get(iid)
        if not kind_tuple:
            return
        if kind_tuple[0] == 'row':
            _, dk, rn = kind_tuple
            new_state = not self._is_row_locked(dk, rn)
            SETTINGS.set_lock(self._lock_key(dk, rn), new_state)
        elif kind_tuple[0] == 'field':
            _, dk, rn, fn = kind_tuple
            new_state = not self._is_field_locked(dk, rn, fn)
            SETTINGS.set_lock(self._lock_key(dk, rn, fn), new_state)
        else:
            return
        self._populate()
        # Consume the click — don't let the tree flip selection mid-toggle
        return 'break'

    # ---- revert operations ----
    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        kind_tuple = self._node_kind.get(iid)
        if not kind_tuple:
            return
        menu = tk.Menu(self.tree, tearoff=0)
        menu.add_command(label='Revert to original',
                         command=lambda: self._revert_node(iid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _revert_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Nothing selected',
                'Select a DataTable, row, or field in the tree first.')
            return
        self._revert_node(sel[0])

    def _revert_node(self, iid):
        kind_tuple = self._node_kind.get(iid)
        if not kind_tuple:
            return
        # Block revert if this change (or anything under it) is locked, unless
        # the user explicitly opts in to unlocking first.
        if self._is_change_protected(kind_tuple):
            if not messagebox.askyesno(
                    'Locked change',
                    'This change is marked as WORKING & TESTED (locked).\n\n'
                    'Reverting it will also clear the lock.\n\nProceed?'):
                return
            self._unlock_tree(kind_tuple)

        kind = kind_tuple[0]
        if kind == 'table':
            _, doc_key = kind_tuple
            doc = self.app.docs.get(doc_key)
            if doc is None:
                return
            if not messagebox.askyesno(
                    'Revert whole DataTable',
                    f'Revert every change to {doc.label} back to the pristine '
                    'game state? This will undo all adds, removes, and edits '
                    'in this table.'):
                return
            msg = doc.revert_all()
        elif kind == 'row':
            _, doc_key, row_name = kind_tuple
            doc = self.app.docs.get(doc_key)
            if doc is None:
                return
            if not messagebox.askyesno(
                    'Revert row',
                    f'Revert {row_name} in {doc.label} back to the pristine '
                    'game state?'):
                return
            msg = doc.revert_row(row_name)
        elif kind == 'field':
            _, doc_key, row_name, field_name = kind_tuple
            doc = self.app.docs.get(doc_key)
            if doc is None:
                return
            if not messagebox.askyesno(
                    'Revert field',
                    f'Revert {row_name}.{field_name} in {doc.label} back to '
                    'the pristine game state?'):
                return
            msg = doc.revert_field(row_name, field_name)
        else:
            return

        # Refresh every tab so the change is visible everywhere, and the
        # history view itself updates to reflect the new state.
        for tab in (self.app.zone_tab, self.app.chapter_tab, self.app.biome_tab,
                    self.app.bubble_tab, self.app.filter_tab,
                    self.app.landmark_tab, self.app.mappings_tab,
                    self.app.history_tab, self.app.map_tab):
            tab.refresh_from_doc()
        self.app.refresh_status()
        if msg:
            self.status_lbl.config(text=msg)

    def _unlock_tree(self, kind_tuple):
        """Remove any lock that covers this change (row or field)."""
        kind = kind_tuple[0]
        if kind == 'row':
            _, dk, rn = kind_tuple
            SETTINGS.set_lock(self._lock_key(dk, rn), False)
            # Also clear any per-field locks beneath it
            for key in list(SETTINGS.locked_keys()):
                parts = key.split('|')
                if len(parts) == 3 and parts[0] == dk and parts[1] == rn:
                    SETTINGS.set_lock(key, False)
        elif kind == 'field':
            _, dk, rn, fn = kind_tuple
            SETTINGS.set_lock(self._lock_key(dk, rn, fn), False)

    def _revert_all(self):
        """Revert every unlocked change. Locked changes are preserved."""
        dirty_docs = [d for d in self.app.docs.values()
                      if d.data is not None and d.differs_from_original()]
        if not dirty_docs:
            messagebox.showinfo('Nothing to revert',
                'No DataTables differ from the pristine baseline.')
            return

        # Count locked vs unlocked so we can tell the user what's protected
        locked_count = 0
        revert_plan = []  # list of (doc, kind, row, field_or_None)
        for doc in dirty_docs:
            added, removed, modified = doc.change_summary()
            for nm in added + removed:
                if self._is_row_locked(doc.key, nm):
                    locked_count += 1
                    continue
                revert_plan.append((doc, 'row', nm, None))
            for nm in modified:
                if self._is_row_locked(doc.key, nm):
                    locked_count += 1
                    continue
                # Revert each field individually so per-field locks are honored
                for field, _old, _new in doc.row_field_diffs(nm):
                    if self._is_field_locked(doc.key, nm, field):
                        locked_count += 1
                        continue
                    revert_plan.append((doc, 'field', nm, field))

        if not revert_plan:
            messagebox.showinfo(
                'All changes locked',
                f'Every current change is marked as WORKING & TESTED '
                f'({locked_count} locked). Nothing to revert.\n\n'
                'Uncheck a lock first if you want to revert something.')
            return

        msg = (f'Revert {len(revert_plan)} unlocked change(s) back to the '
               'pristine game state?')
        if locked_count:
            msg += f'\n\n{locked_count} locked change(s) will be PRESERVED.'
        msg += '\n\nThis only changes in-memory data — Ctrl+S afterwards to save.'
        if not messagebox.askyesno('Revert unlocked changes', msg):
            return

        reverted_docs = set()
        for doc, kind, row_name, field_name in revert_plan:
            if kind == 'row':
                doc.revert_row(row_name)
            else:
                doc.revert_field(row_name, field_name)
            reverted_docs.add(doc.label)

        for tab in (self.app.zone_tab, self.app.chapter_tab, self.app.biome_tab,
                    self.app.bubble_tab, self.app.filter_tab,
                    self.app.landmark_tab, self.app.mappings_tab,
                    self.app.history_tab, self.app.map_tab):
            tab.refresh_from_doc()
        self.app.refresh_status()
        summary = f'Reverted {len(revert_plan)} change(s) in {", ".join(sorted(reverted_docs, key=natural_key))}'
        if locked_count:
            summary += f'  |  {locked_count} locked preserved'
        self.status_lbl.config(text=summary)

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            initialfile='worldgen_changes.txt',
            filetypes=[('Text', '*.txt'), ('All files', '*.*')])
        if not path:
            return
        lines = []
        for doc in self.app.docs.values():
            if doc.data is None:
                continue
            added, removed, modified = doc.change_summary()
            if not (added or removed or modified):
                lines.append(f'{doc.label}: no changes\n')
                continue
            lines.append(
                f'{doc.label}  +{len(added)}  -{len(removed)}  ~{len(modified)}')
            for n in added:
                lines.append(f'  + added   {n}')
            for n in removed:
                lines.append(f'  - removed {n}')
            for n in modified:
                lines.append(f'  ~ modified {n}')
                for field, old_s, new_s in doc.row_field_diffs(n):
                    lines.append(f'      {field}:  {old_s}  ->  {new_s}')
            lines.append('')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            messagebox.showinfo('Exported', f'Written to {path}')
        except Exception as e:
            messagebox.showerror('Export failed', str(e))


# -----------------------------------------------------------------------------
# LAYOUT CONNECTIONS TAB — view + edit DT_Moria_LayoutConnections rows
# -----------------------------------------------------------------------------

class LayoutConnectionsTab(BaseTab):
    """View + edit rows in DT_Moria_LayoutConnections.

    Each row wires two endpoints (Origin / Destination) — each endpoint
    is either a Landmark (via LandmarkInterface) or a Zone (via
    ZoneInterface). Together with ZoneRule, EnabledState, bRequired and
    bExclusive, they tell the runtime A* router how to wire chapters
    together at world-gen time.

    Orphan rows — Live connections whose Origin or Destination landmark
    has no Live SS zone holding it via LandmarkHandles[] — are the
    classic crash class for `FMorLayoutConnectionInstance::GetZone`
    (offset 0x1a1). This tab highlights them in red.
    """

    # Enum option lists — provided as plain strings (the JSON stores them
    # with the leading enum prefix, which we add/strip on read/write).
    ENABLED_STATE_OPTS = ['Live', 'Disabled', 'Test']
    ZONE_SET_OPTS = ['Moria', 'SandboxSmall', 'SandboxMedium', 'Expedition',
                     'ExpeditionRescue', 'ExpeditionGrendel', 'ExpeditionForge',
                     'EZoneSet_MAX', 'All']
    ZONE_RULE_OPTS = ['Shared', 'Chapter', 'BelongsToOrigin', 'BelongsToDestination']
    ENDPOINT_KIND_OPTS = ['LandmarkInterface', 'ZoneInterface']
    FAVOR_OPTS = ['Any', 'Origin', 'Destination']

    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.doc = app.docs.get('connections')
        self.zones_doc = app.docs.get('zones')
        self.landmarks_doc = app.docs.get('landmarks')
        self.current_row = None
        self._updating = False
        self._build()

    def refresh_from_doc(self):
        self._populate_tree()

    # ---------- helpers ----------
    @staticmethod
    def _fp(v, n):
        for p in v or []:
            if isinstance(p, dict) and p.get('Name') == n:
                return p
        return None

    @classmethod
    def _get_rowname(cls, r, k):
        p = cls._fp(r['Value'], k)
        if not p: return None
        v = p.get('Value')
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get('Name') == 'RowName':
                    return it.get('Value', '')
        return v

    @classmethod
    def _get_enum_short(cls, r, k):
        p = cls._fp(r['Value'], k)
        if not p: return ''
        v = p.get('Value')
        if isinstance(v, str) and '::' in v:
            return v.split('::', 1)[1]
        return str(v) if v else ''

    @classmethod
    def _get_bool(cls, r, k):
        p = cls._fp(r['Value'], k)
        return bool(p.get('Value')) if p else False

    @classmethod
    def _row_orphan_status(cls, row, holders_by_set):
        """Return tuple (is_orphan, reason).

        A connection is an orphan if its Origin or Destination landmark
        is not held by any Live zone in a ZoneSet that the connection
        APPLIES to:
          - ZoneSet=SandboxSmall → must be held by a Live SS zone
          - ZoneSet=Moria        → must be held by a Live Moria zone
          - ZoneSet=All          → must be held by SOME Live zone in any set
          - other ZoneSets       → not flagged here (out of scope)
        """
        es = cls._get_enum_short(row, 'EnabledState')
        if es != 'Live': return (False, '')
        zs = cls._get_enum_short(row, 'ZoneSet')

        # Build the candidate-holder set for this connection's ZoneSet
        if zs == 'All':
            # Held by any Live zone in any set
            relevant = set()
            for s in holders_by_set.values(): relevant |= s
        elif zs in holders_by_set:
            relevant = holders_by_set[zs]
        else:
            # Unknown / unsupported zoneset — don't flag
            return (False, '')

        for fld in ('OriginLandmark', 'DestinationLandmark'):
            v = cls._get_rowname(row, fld)
            if v and v != 'None' and v not in relevant:
                scope = 'any Live zone' if zs == 'All' else f'any Live {zs} zone'
                return (True, f'{fld}={v} not held by {scope}')
        return (False, '')

    def _build_landmark_holders(self):
        """Map of {ZoneSet -> set of landmark names} held by Live zones in
        that set via LandmarkHandles. Lets the orphan check evaluate
        connections against the right scope."""
        holders = {}  # {zoneset: {landmark_name, ...}}
        if not self.zones_doc: return holders
        for r in self.zones_doc.rows:
            zs_p = self._fp(r.get('Value', []), 'ZoneSet')
            zs = (zs_p.get('Value', '').split('::', 1)[-1] if zs_p else '')
            if not zs: continue
            es = self._get_enum_short(r, 'EnabledState')
            if es == 'Disabled': continue
            lh = self._fp(r.get('Value', []), 'LandmarkHandles')
            if not lh: continue
            bucket = holders.setdefault(zs, set())
            for e in (lh.get('Value') or []):
                if not isinstance(e, dict): continue
                inner = e.get('Value')
                if not isinstance(inner, list): continue
                lhprop = self._fp(inner, 'Landmark')
                if not lhprop: continue
                lv = lhprop.get('Value')
                if isinstance(lv, list):
                    for it in lv:
                        if isinstance(it, dict) and it.get('Name') == 'RowName':
                            nm = it.get('Value', '')
                            if nm and nm != 'None':
                                bucket.add(nm)
        return holders

    def _all_zone_names(self):
        if not self.zones_doc: return []
        return ['None'] + sorted({r['Name'] for r in self.zones_doc.rows
                                  if isinstance(r, dict) and r.get('Name')})

    def _all_landmark_names(self):
        if not self.landmarks_doc: return []
        return ['None'] + sorted({r['Name'] for r in self.landmarks_doc.rows
                                   if isinstance(r, dict) and r.get('Name')})

    # ---------- UI ----------
    def _build(self):
        # Toolbar with filters
        bar = ttk.Frame(self); bar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(bar, text='Add…',
                   command=self._add_connection_row).pack(side=tk.LEFT)
        ttk.Button(bar, text='Copy…',
                   command=self._copy_connection_row).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bar, text='Rename…',
                   command=self._rename_connection).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bar, text='Delete',
                   command=self._delete_connection_row).pack(side=tk.LEFT, padx=(4, 8))

        lbl_fzs = ttk.Label(bar, text='ZoneSet:')
        lbl_fzs.pack(side=tk.LEFT, padx=(0, 4))
        attach_tooltip(lbl_fzs, 'conn_filter_zoneset')
        # Default = '(All)' bypass so every row is visible. Renamed the
        # bypass option to '(any)' to avoid confusion with the literal
        # EZoneSet::All enum value (which is one of the real ZoneSets a
        # connection can be tagged with).
        # Filter values persist across editor sessions in the .ini's
        # [filters] section, just like sort state in [sort].
        saved_zs = SETTINGS.get_filter('connections_zoneset', '(any)') or '(any)'
        valid_zs = ['(any)'] + self.ZONE_SET_OPTS
        if saved_zs not in valid_zs: saved_zs = '(any)'
        self.v_filter_zoneset = tk.StringVar(value=saved_zs)
        cb_fzs = ttk.Combobox(bar, textvariable=self.v_filter_zoneset, width=14,
                     state='readonly', values=valid_zs)
        cb_fzs.pack(side=tk.LEFT, padx=(0, 12))
        attach_tooltip(cb_fzs, 'conn_filter_zoneset')

        def _on_zs_change(*_):
            SETTINGS.set_filter('connections_zoneset',
                                 self.v_filter_zoneset.get())
            self._populate_tree()
        self.v_filter_zoneset.trace_add('write', _on_zs_change)

        lbl_fst = ttk.Label(bar, text='State:')
        lbl_fst.pack(side=tk.LEFT, padx=(0, 4))
        attach_tooltip(lbl_fst, 'conn_filter_state')
        saved_st = SETTINGS.get_filter('connections_state', '(any)') or '(any)'
        valid_st = ['(any)', 'Live', 'Disabled', 'Test']
        if saved_st not in valid_st: saved_st = '(any)'
        self.v_filter_state = tk.StringVar(value=saved_st)
        cb_fst = ttk.Combobox(bar, textvariable=self.v_filter_state, width=10,
                     state='readonly', values=valid_st)
        cb_fst.pack(side=tk.LEFT, padx=(0, 12))
        attach_tooltip(cb_fst, 'conn_filter_state')

        def _on_st_change(*_):
            SETTINGS.set_filter('connections_state',
                                 self.v_filter_state.get())
            self._populate_tree()
        self.v_filter_state.trace_add('write', _on_st_change)

        saved_orph = SETTINGS.get_filter_bool('connections_orphans_only', False)
        self.v_orphans_only = tk.BooleanVar(value=saved_orph)
        def _on_orph_change():
            SETTINGS.set_filter('connections_orphans_only',
                                 self.v_orphans_only.get())
            self._populate_tree()
        chk_orph = ttk.Checkbutton(bar, text='Orphans only',
                        variable=self.v_orphans_only,
                        command=_on_orph_change)
        chk_orph.pack(side=tk.LEFT, padx=(0, 12))
        attach_tooltip(chk_orph, 'conn_orphans_only')

        self.status_lbl = ttk.Label(bar, text='', foreground='#555')
        self.status_lbl.pack(side=tk.RIGHT)

        # Paned: list left, detail right
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned); paned.add(left, weight=2)
        cols = ('name','zoneset','state','olm','oz','dlm','dz','rule','flags')
        specs = [
            ('name',   'Connection',           240, True),
            ('zoneset','ZoneSet',               90, False),
            ('state',  'State',                 75, False),
            ('olm',    'OriginLandmark',       170, True),
            ('oz',     'OriginZone',           170, True),
            ('dlm',    'DestLandmark',         170, True),
            ('dz',     'DestZone',             170, True),
            ('rule',   'ZoneRule',             100, False),
            ('flags',  'Req/Exc',               70, False),
        ]
        self.tree = self.make_tree(left, cols, specs, settings_key='connections')
        try:
            self.tree.tag_configure(DISABLED_TAG, foreground='#999999')
            # Red text only — no dark background (was hard to read).
            self.tree.tag_configure('orphan', foreground='#c0392b')
        except Exception: pass
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = ttk.Frame(paned); paned.add(right, weight=1)
        self._build_detail(right)

    def _build_detail(self, parent):
        d = ttk.LabelFrame(parent, text='Connection Detail', padding=8)
        d.pack(fill=tk.BOTH, expand=True)

        g = ttk.Frame(d); g.pack(fill=tk.X)
        self.v_name = tk.StringVar(value='(no connection selected)')
        ttk.Label(g, text='Row:', width=10).grid(row=0, column=0, sticky='w')
        ttk.Label(g, textvariable=self.v_name,
                  font=('Segoe UI', 10, 'bold')).grid(row=0, column=1, columnspan=3, sticky='w')

        # ZoneSet + EnabledState
        lbl_zs = ttk.Label(g, text='ZoneSet:')
        lbl_zs.grid(row=1, column=0, sticky='w', pady=(8, 0))
        attach_tooltip(lbl_zs, 'conn_zoneset')
        self.v_zoneset = tk.StringVar()
        cb_zs = ttk.Combobox(g, textvariable=self.v_zoneset, width=20,
                              state='readonly', values=self.ZONE_SET_OPTS)
        cb_zs.grid(row=1, column=1, sticky='w', pady=(8, 0))
        cb_zs.bind('<<ComboboxSelected>>',
                   lambda _e: self._apply_enum('ZoneSet', 'EZoneSet::',
                                                self.v_zoneset.get()))
        attach_tooltip(cb_zs, 'conn_zoneset')

        lbl_st = ttk.Label(g, text='State:')
        lbl_st.grid(row=1, column=2, sticky='w', padx=(16, 0), pady=(8, 0))
        attach_tooltip(lbl_st, 'conn_state')
        self.v_state = tk.StringVar()
        cb_st = ttk.Combobox(g, textvariable=self.v_state, width=12,
                              state='readonly', values=self.ENABLED_STATE_OPTS)
        cb_st.grid(row=1, column=3, sticky='w', pady=(8, 0))
        cb_st.bind('<<ComboboxSelected>>',
                   lambda _e: self._apply_enum('EnabledState',
                                                'ERowEnabledState::',
                                                self.v_state.get()))
        attach_tooltip(cb_st, 'conn_state')

        # Required / Exclusive / LeafZoneRoute
        flags = ttk.Frame(d); flags.pack(fill=tk.X, pady=(8, 0))
        self.v_required = tk.BooleanVar()
        chk_req = ttk.Checkbutton(flags, text='bRequired', variable=self.v_required,
                        command=lambda: self._apply_bool('bRequired',
                                                          self.v_required.get()))
        chk_req.pack(side=tk.LEFT)
        attach_tooltip(chk_req, 'conn_required')
        self.v_exclusive = tk.BooleanVar()
        chk_exc = ttk.Checkbutton(flags, text='bExclusive', variable=self.v_exclusive,
                        command=lambda: self._apply_bool('bExclusive',
                                                          self.v_exclusive.get()))
        chk_exc.pack(side=tk.LEFT, padx=12)
        attach_tooltip(chk_exc, 'conn_exclusive')
        self.v_leaf = tk.BooleanVar()
        chk_leaf = ttk.Checkbutton(flags, text='bLeafZoneRoute', variable=self.v_leaf,
                        command=lambda: self._apply_bool('bLeafZoneRoute',
                                                          self.v_leaf.get()))
        chk_leaf.pack(side=tk.LEFT)
        attach_tooltip(chk_leaf, 'conn_leaf')

        # ZoneRule
        rl = ttk.Frame(d); rl.pack(fill=tk.X, pady=(8, 0))
        lbl_rl = ttk.Label(rl, text='ZoneRule:', width=10)
        lbl_rl.pack(side=tk.LEFT)
        attach_tooltip(lbl_rl, 'conn_zonerule')
        self.v_rule = tk.StringVar()
        cb_rl = ttk.Combobox(rl, textvariable=self.v_rule, width=24,
                              state='readonly', values=self.ZONE_RULE_OPTS)
        cb_rl.pack(side=tk.LEFT)
        cb_rl.bind('<<ComboboxSelected>>',
                   lambda _e: self._apply_enum('ZoneRule',
                                                'EConnectionZoneRule::',
                                                self.v_rule.get()))
        attach_tooltip(cb_rl, 'conn_zonerule')

        # ORIGIN endpoint
        of = ttk.LabelFrame(d, text='Origin', padding=6)
        of.pack(fill=tk.X, pady=(8, 0))
        lbl_okind = ttk.Label(of, text='Kind:', width=10)
        lbl_okind.grid(row=0, column=0, sticky='w')
        attach_tooltip(lbl_okind, 'conn_origin_kind')
        self.v_okind = tk.StringVar()
        cb_ok = ttk.Combobox(of, textvariable=self.v_okind, width=24,
                              state='readonly', values=self.ENDPOINT_KIND_OPTS)
        cb_ok.grid(row=0, column=1, sticky='w')
        cb_ok.bind('<<ComboboxSelected>>',
                   lambda _e: self._apply_enum('OriginKind',
                                                'EConnectionEndpointKind::',
                                                self.v_okind.get()))
        attach_tooltip(cb_ok, 'conn_origin_kind')
        lbl_olm = ttk.Label(of, text='Landmark:')
        lbl_olm.grid(row=1, column=0, sticky='w', pady=(4,0))
        attach_tooltip(lbl_olm, 'conn_origin_landmark')
        self.v_olm = tk.StringVar()
        self.cb_olm = ttk.Combobox(of, textvariable=self.v_olm, width=40)
        self.cb_olm.grid(row=1, column=1, sticky='w', pady=(4,0))
        self.cb_olm.bind('<<ComboboxSelected>>',
                          lambda _e: self._apply_rowhandle('OriginLandmark',
                                                            self.v_olm.get()))
        self.cb_olm.bind('<FocusOut>',
                          lambda _e: self._apply_rowhandle('OriginLandmark',
                                                            self.v_olm.get()))
        attach_tooltip(self.cb_olm, 'conn_origin_landmark')
        lbl_oz = ttk.Label(of, text='Zone:')
        lbl_oz.grid(row=2, column=0, sticky='w', pady=(4,0))
        attach_tooltip(lbl_oz, 'conn_origin_zone')
        self.v_oz = tk.StringVar()
        self.cb_oz = ttk.Combobox(of, textvariable=self.v_oz, width=40)
        self.cb_oz.grid(row=2, column=1, sticky='w', pady=(4,0))
        self.cb_oz.bind('<<ComboboxSelected>>',
                         lambda _e: self._apply_rowhandle('OriginZone',
                                                           self.v_oz.get()))
        self.cb_oz.bind('<FocusOut>',
                         lambda _e: self._apply_rowhandle('OriginZone',
                                                           self.v_oz.get()))
        attach_tooltip(self.cb_oz, 'conn_origin_zone')

        # DESTINATION endpoint
        df = ttk.LabelFrame(d, text='Destination', padding=6)
        df.pack(fill=tk.X, pady=(8, 0))
        lbl_dkind = ttk.Label(df, text='Kind:', width=10)
        lbl_dkind.grid(row=0, column=0, sticky='w')
        attach_tooltip(lbl_dkind, 'conn_dest_kind')
        self.v_dkind = tk.StringVar()
        cb_dk = ttk.Combobox(df, textvariable=self.v_dkind, width=24,
                              state='readonly', values=self.ENDPOINT_KIND_OPTS)
        cb_dk.grid(row=0, column=1, sticky='w')
        cb_dk.bind('<<ComboboxSelected>>',
                   lambda _e: self._apply_enum('DestinationKind',
                                                'EConnectionEndpointKind::',
                                                self.v_dkind.get()))
        attach_tooltip(cb_dk, 'conn_dest_kind')
        lbl_dlm = ttk.Label(df, text='Landmark:')
        lbl_dlm.grid(row=1, column=0, sticky='w', pady=(4,0))
        attach_tooltip(lbl_dlm, 'conn_dest_landmark')
        self.v_dlm = tk.StringVar()
        self.cb_dlm = ttk.Combobox(df, textvariable=self.v_dlm, width=40)
        self.cb_dlm.grid(row=1, column=1, sticky='w', pady=(4,0))
        self.cb_dlm.bind('<<ComboboxSelected>>',
                          lambda _e: self._apply_rowhandle('DestinationLandmark',
                                                            self.v_dlm.get()))
        self.cb_dlm.bind('<FocusOut>',
                          lambda _e: self._apply_rowhandle('DestinationLandmark',
                                                            self.v_dlm.get()))
        attach_tooltip(self.cb_dlm, 'conn_dest_landmark')
        lbl_dz = ttk.Label(df, text='Zone:')
        lbl_dz.grid(row=2, column=0, sticky='w', pady=(4,0))
        attach_tooltip(lbl_dz, 'conn_dest_zone')
        self.v_dz = tk.StringVar()
        self.cb_dz = ttk.Combobox(df, textvariable=self.v_dz, width=40)
        self.cb_dz.grid(row=2, column=1, sticky='w', pady=(4,0))
        self.cb_dz.bind('<<ComboboxSelected>>',
                         lambda _e: self._apply_rowhandle('DestinationZone',
                                                           self.v_dz.get()))
        self.cb_dz.bind('<FocusOut>',
                         lambda _e: self._apply_rowhandle('DestinationZone',
                                                           self.v_dz.get()))
        attach_tooltip(self.cb_dz, 'conn_dest_zone')

        # Routing coordinates — Subcell, OriginCoord, DestinationCoord.
        # These IntVector fields are READ AND USED by the engine's A* router
        # at world-gen time. The Z component must be inside a Live chapter's
        # MinZ..MaxZ band, otherwise the router calls GetZone() on an empty
        # cell → null deref → crash. Editing these here lets the user keep
        # them aligned when chapters move.
        coords_frame = ttk.LabelFrame(d, text='Routing coords (engine A*)', padding=6)
        coords_frame.pack(fill=tk.X, pady=(8, 0))
        self._coord_vars = {}  # field_name -> {'x':IntVar,'y':IntVar,'z':IntVar}

        def _make_coord_row(parent, row, fld, tooltip):
            ttk.Label(parent, text=fld + ':', width=20).grid(row=row, column=0, sticky='w')
            xs = tk.IntVar(); ys = tk.IntVar(); zs = tk.IntVar()
            self._coord_vars[fld] = {'x': xs, 'y': ys, 'z': zs}
            for col, (lbl, var) in enumerate([('X', xs), ('Y', ys), ('Z', zs)], start=1):
                ttk.Label(parent, text=lbl).grid(row=row, column=col*2 - 1, sticky='e',
                                                  padx=(8 if col > 1 else 4, 2))
                sp = ttk.Spinbox(parent, from_=-99, to=99, width=5, textvariable=var,
                                  command=lambda f=fld: self._apply_coord_vec(f))
                sp.grid(row=row, column=col*2, sticky='w')
                sp.bind('<FocusOut>', lambda _e, f=fld: self._apply_coord_vec(f))

        _make_coord_row(coords_frame, 0, 'Subcell',
                        'Subcell coordinate the connection occupies. Z must be '
                        'inside a Live chapter band or the router crashes.')
        _make_coord_row(coords_frame, 1, 'OriginCoord',
                        'Origin endpoint world coordinate (often X/Y only, Z=0).')
        _make_coord_row(coords_frame, 2, 'DestinationCoord',
                        'Destination endpoint world coordinate (often X/Y only, Z=0).')

        # Orphan / status feedback
        self.v_status = tk.StringVar(value='')
        ttk.Label(d, textvariable=self.v_status,
                  foreground=self.app.COLOR_MUTED, justify=tk.LEFT,
                  wraplength=520).pack(fill=tk.X, pady=(8, 0), anchor='w')

    # ---------- tree population ----------
    def _populate_tree(self):
        if not self.doc:
            return
        for iid in self.tree.get_children(''):
            self.tree.delete(iid)

        lm_holders = self._build_landmark_holders()

        # Refresh combobox option lists in case zones / landmarks changed
        try:
            self.cb_olm['values'] = self._all_landmark_names()
            self.cb_dlm['values'] = self._all_landmark_names()
            self.cb_oz['values'] = self._all_zone_names()
            self.cb_dz['values'] = self._all_zone_names()
        except Exception: pass

        filt_zs = self.v_filter_zoneset.get()
        filt_state = self.v_filter_state.get()
        orphans_only = self.v_orphans_only.get()

        n_total = 0; n_visible = 0; n_orphan = 0
        for r in self.doc.rows:
            n_total += 1
            zs = self._get_enum_short(r, 'ZoneSet')
            es = self._get_enum_short(r, 'EnabledState')
            is_orphan, reason = self._row_orphan_status(r, lm_holders)
            if is_orphan: n_orphan += 1

            if filt_zs not in ('(any)', '') and zs != filt_zs: continue
            if filt_state not in ('(any)', '') and es != filt_state: continue
            if orphans_only and not is_orphan: continue

            req = self._get_bool(r, 'bRequired')
            exc = self._get_bool(r, 'bExclusive')
            flags_str = ('R' if req else '-') + ('X' if exc else '-')

            tags = []
            if es == 'Disabled':
                tags.append(DISABLED_TAG)
            if is_orphan:
                tags.append('orphan')

            self.tree.insert('', 'end', iid=r['Name'], values=(
                r['Name'],
                zs,
                es,
                self._get_rowname(r, 'OriginLandmark') or '',
                self._get_rowname(r, 'OriginZone') or '',
                self._get_rowname(r, 'DestinationLandmark') or '',
                self._get_rowname(r, 'DestinationZone') or '',
                self._get_enum_short(r, 'ZoneRule'),
                flags_str,
            ), tags=tuple(tags))
            n_visible += 1

        self.status_lbl.config(
            text=f'{n_visible}/{n_total} rows  ·  {n_orphan} orphan(s)')
        self.apply_sort(self.tree)

    # ---------- selection ----------
    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            self.current_row = None
            return
        name = sel[0]
        if not self.doc: return
        for r in self.doc.rows:
            if r.get('Name') == name:
                self.current_row = r
                self._populate_detail(r)
                return

    def _populate_detail(self, r):
        self._updating = True
        try:
            self.v_name.set(r.get('Name', '?'))
            self.v_zoneset.set(self._get_enum_short(r, 'ZoneSet'))
            self.v_state.set(self._get_enum_short(r, 'EnabledState') or 'Live')
            self.v_required.set(self._get_bool(r, 'bRequired'))
            self.v_exclusive.set(self._get_bool(r, 'bExclusive'))
            self.v_leaf.set(self._get_bool(r, 'bLeafZoneRoute'))
            self.v_rule.set(self._get_enum_short(r, 'ZoneRule'))
            self.v_okind.set(self._get_enum_short(r, 'OriginKind'))
            self.v_dkind.set(self._get_enum_short(r, 'DestinationKind'))
            self.v_olm.set(self._get_rowname(r, 'OriginLandmark') or 'None')
            self.v_oz.set(self._get_rowname(r, 'OriginZone') or 'None')
            self.v_dlm.set(self._get_rowname(r, 'DestinationLandmark') or 'None')
            self.v_dz.set(self._get_rowname(r, 'DestinationZone') or 'None')
            # Routing coords — read each IntVector and populate spinboxes
            for fld in ('Subcell', 'OriginCoord', 'DestinationCoord'):
                vec = self._read_intvec(r, fld)
                if fld in self._coord_vars:
                    self._coord_vars[fld]['x'].set(vec[0] if vec else 0)
                    self._coord_vars[fld]['y'].set(vec[1] if vec else 0)
                    self._coord_vars[fld]['z'].set(vec[2] if vec else 0)
            # status / orphan reason
            lm_holders = self._build_landmark_holders()
            is_orphan, reason = self._row_orphan_status(r, lm_holders)
            if is_orphan:
                self.v_status.set('⚠ Orphan: ' + reason +
                                   '  — this connection will null-deref the '
                                   'router. Disable it OR clear the bad '
                                   'endpoint OR attach the landmark to a '
                                   'Live SS zone.')
            else:
                self.v_status.set('')
        finally:
            self._updating = False

    # ---------- write helpers ----------
    def _ensure_namemap(self, *values):
        nm = self.doc.data.get('NameMap', [])
        added = False
        for v in values:
            if v and v not in nm:
                nm.append(v); added = True
        if added:
            n = len(nm)
            self.doc.data['NamesReferencedFromExportDataCount'] = n
            g = self.doc.data.get('Generations') or []
            if g and isinstance(g[0], dict):
                g[0]['NameCount'] = n

    def _apply_enum(self, fld, prefix, short_value):
        if self._updating or self.current_row is None: return
        if not short_value: return
        full = prefix + short_value
        p = self._fp(self.current_row['Value'], fld)
        if p is None: return
        p['Value'] = full
        self._ensure_namemap(full, prefix.rstrip(':'))
        self._refresh_row()
        self.app.refresh_status()

    def _apply_bool(self, fld, value):
        if self._updating or self.current_row is None: return
        p = self._fp(self.current_row['Value'], fld)
        if p is None: return
        p['Value'] = bool(value)
        self._refresh_row()
        self.app.refresh_status()

    def _read_intvec(self, r, fld):
        """Return (X, Y, Z) tuple for an IntVector property field on row r,
        or None if the field doesn't exist or is malformed."""
        p = self._fp(r['Value'], fld)
        if not p: return None
        v = p.get('Value')
        if isinstance(v, list) and v:
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if isinstance(d, dict):
                return (d.get('X', 0), d.get('Y', 0), d.get('Z', 0))
        return None

    def _apply_coord_vec(self, fld):
        """Write back the (X, Y, Z) values from the spinboxes to the row's
        IntVector field. Wired to Subcell, OriginCoord, DestinationCoord."""
        if self._updating or self.current_row is None: return
        if fld not in self._coord_vars: return
        try:
            x = int(self._coord_vars[fld]['x'].get())
            y = int(self._coord_vars[fld]['y'].get())
            z = int(self._coord_vars[fld]['z'].get())
        except (tk.TclError, ValueError):
            return  # ignore partial input
        p = self._fp(self.current_row['Value'], fld)
        if not p: return
        v = p.get('Value')
        if isinstance(v, list) and v:
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if isinstance(d, dict):
                d['X'] = x; d['Y'] = y; d['Z'] = z
                self.app.refresh_status()

    def _apply_rowhandle(self, fld, new_target):
        if self._updating or self.current_row is None: return
        p = self._fp(self.current_row['Value'], fld)
        if p is None: return
        v = p.get('Value')
        target = new_target.strip() if new_target else 'None'
        if not target: target = 'None'
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get('Name') == 'RowName':
                    it['Value'] = target
                    break
        # NameMap completeness — make sure target is registered
        if target and target != 'None':
            self._ensure_namemap(target)
        self._refresh_row()
        # Refresh the orphan status indicator on the detail panel
        lm_holders = self._build_landmark_holders()
        is_orphan, reason = self._row_orphan_status(self.current_row, lm_holders)
        if is_orphan:
            self.v_status.set('⚠ Orphan: ' + reason)
        else:
            self.v_status.set('')
        self.app.refresh_status()

    def _refresh_row(self):
        if not self.current_row: return
        name = self.current_row.get('Name')
        if not name or not self.tree.exists(name): return
        zs = self._get_enum_short(self.current_row, 'ZoneSet')
        es = self._get_enum_short(self.current_row, 'EnabledState')
        req = self._get_bool(self.current_row, 'bRequired')
        exc = self._get_bool(self.current_row, 'bExclusive')
        lm_holders = self._build_landmark_holders()
        is_orphan, _ = self._row_orphan_status(self.current_row, lm_holders)
        flags_str = ('R' if req else '-') + ('X' if exc else '-')
        tags = []
        if es == 'Disabled': tags.append(DISABLED_TAG)
        if is_orphan: tags.append('orphan')
        self.tree.item(name, values=(
            name, zs, es,
            self._get_rowname(self.current_row, 'OriginLandmark') or '',
            self._get_rowname(self.current_row, 'OriginZone') or '',
            self._get_rowname(self.current_row, 'DestinationLandmark') or '',
            self._get_rowname(self.current_row, 'DestinationZone') or '',
            self._get_enum_short(self.current_row, 'ZoneRule'),
            flags_str,
        ), tags=tuple(tags))

    # ---- Row CRUD ----
    def _add_connection_row(self):
        if not self.doc or not self.doc.rows:
            messagebox.showerror('Error', 'No template row available.'); return
        name = simpledialog.askstring('Add Connection', 'Row name:', parent=self)
        if not name: return
        name = name.strip()
        if not name: return
        if any(r.get('Name') == name for r in self.doc.rows):
            messagebox.showerror('Error', f'Connection "{name}" already exists.'); return
        new_row = copy.deepcopy(self.doc.rows[0])
        new_row['Name'] = name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(name):
            self.tree.selection_set(name); self.tree.see(name)
        self.app.refresh_status()

    def _copy_connection_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a connection to copy.'); return
        src_name = sel[0]
        src = next((r for r in self.doc.rows if r.get('Name') == src_name), None)
        if src is None: return
        new_name = simpledialog.askstring(
            'Copy Connection', f'New row name (copying from "{src_name}"):',
            initialvalue=f'{src_name}_copy', parent=self)
        if not new_name: return
        new_name = new_name.strip()
        if not new_name: return
        if any(r.get('Name') == new_name for r in self.doc.rows):
            messagebox.showerror('Error', f'Connection "{new_name}" already exists.'); return
        new_row = copy.deepcopy(src)
        new_row['Name'] = new_name
        self.doc.rows.append(new_row)
        self.doc.reconcile_namemap()
        self.refresh_from_doc()
        if self.tree.exists(new_name):
            self.tree.selection_set(new_name); self.tree.see(new_name)
        self.app.refresh_status()

    def _delete_connection_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning('Nothing selected', 'Select a connection to delete.'); return
        name = sel[0]
        if not messagebox.askyesno('Delete connection', f'Delete connection "{name}"?'):
            return
        self.doc.rows[:] = [r for r in self.doc.rows if r.get('Name') != name]
        self.doc.reconcile_namemap()
        self.current_row = None
        self.refresh_from_doc()
        self.app.refresh_status()

    def _rename_connection(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Rename', 'Select a connection first.'); return
        self.app.rename_row('connections', sel[0], parent=self)


# -----------------------------------------------------------------------------
# LEVELS TAB — top-to-bottom chart of the SandboxSmall stack
# -----------------------------------------------------------------------------

class LevelsTab(BaseTab):
    """Read-only chart showing the SandboxSmall stack from top floor to
    bottom deep. One row per Live SandboxSmall chapter, sorted by Layer
    descending. Columns mirror the level-list skill output PLUS an
    EnemyScale column (chapter EnemyScalingLevel)."""

    def __init__(self, parent, app):
        super().__init__(parent, app)
        # toolbar
        bar = ttk.Frame(self); bar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text='Refresh', command=self._populate).pack(side=tk.LEFT)
        ttk.Label(bar, text='  Top → Bottom (sorted by Layer descending). '
                  'Live SandboxSmall chapters only.',
                  foreground=app.COLOR_MUTED).pack(side=tk.LEFT)

        cols = ('lv', 'chapter', 'layer', 'chapid', 'enemy', 'h',
                'minz', 'maxz', 'primez', 'live')
        specs = [
            ('lv',      'Lv',         60,  False),
            ('chapter', 'Chapter',    240, True),
            ('layer',   'Layer',      60,  False),
            ('chapid',  'ChapID',     60,  False),
            ('enemy',   'EnemyScale', 80,  False),
            ('h',       'h',          40,  False),
            ('minz',    'MinZ',       50,  False),
            ('maxz',    'MaxZ',       50,  False),
            ('primez',  'PrimeZ',     60,  False),
            ('live',    'Live',       50,  False),
        ]
        self.tree = self.make_tree(self, cols, specs, height=18,
                                   settings_key='levels_chart')

        # footer label for totals + warnings
        self.v_summary = tk.StringVar(value='')
        ttk.Label(self, textvariable=self.v_summary,
                  foreground=app.COLOR_MUTED, justify=tk.LEFT).pack(
            anchor='w', pady=(6, 0))

    def refresh_from_doc(self):
        # Called by app.load_all / save_all. Defer to _populate.
        try:
            self._populate()
        except Exception:
            pass

    def _populate(self):
        for iid in self.tree.get_children(''):
            self.tree.delete(iid)
        self.v_summary.set('')

        ch = self.app.docs.get('chapters')
        z = self.app.docs.get('zones')
        if not ch or not z:
            self.v_summary.set('Chapters / Zones doc not loaded.')
            return

        def fp(v, n):
            for p in v or []:
                if isinstance(p, dict) and p.get('Name') == n: return p
            return None
        def get_field(r, k):
            p = fp(r['Value'], k)
            if not p: return None
            v = p.get('Value')
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        return it.get('Value', '')
            return v
        def zoneset(r):
            p = fp(r.get('Value', []), 'ZoneSet')
            return str(p.get('Value', '')).split('::')[-1] if p else None
        def zstate(r):
            p = fp(r.get('Value', []), 'EnabledState')
            return str(p.get('Value', '')).split('::')[-1] if p else None

        # Count Live SandboxSmall zones per chapter
        counts = {}
        total_zones = 0
        for r in z.rows:
            if zoneset(r) != 'SandboxSmall' or zstate(r) == 'Disabled':
                continue
            chap = get_field(r, 'Chapter') or '(none)'
            counts[chap] = counts.get(chap, 0) + 1
            total_zones += 1

        # Live SandboxSmall LEVEL chapters only (skip zone/landmark-anchored
        # rows that share the SandboxSmall-Chapter## prefix). A LEVEL row's
        # name suffix is 'LevelN' or 'DeepN'. Also accept the legacy
        # lowercase 'SandboxSmall-chapter-N' pattern in case of old data.
        def _is_level_row(name):
            if name.startswith('SandboxSmall-chapter-'):
                return True
            if name.startswith('SandboxSmall-Chapter'):
                tail = name.split('.', 1)[1] if '.' in name else ''
                return tail.startswith('Level') or tail.startswith('Deep')
            return False
        ss = [r for r in ch.rows
              if _is_level_row(r.get('Name', ''))
              and zstate(r) != 'Disabled']
        def lkey(r):
            L = get_field(r, 'Layer')
            return -(L if L is not None else 0)
        ss.sort(key=lkey)

        # Bridge chapters: any non-SS chapter row that hosts Live SS zones.
        # Vanilla pattern — SandboxSmall worlds reach the DLC outdoor areas
        # (DimrillDale, DurinsTower, TradingPost) via zones whose ZoneSet
        # is SandboxSmall but whose Chapter is a Moria-* row.
        # Bridge = any chapter referenced by Live SS zones that ISN'T a
        # SS level row. Treat the new SandboxSmall-Chapter##.<X> non-Level/
        # Deep rows (zone/landmark-anchored) as level-equivalent so they
        # don't fall into the bridge bucket either.
        def _is_ss_chap(name):
            return (name.startswith('SandboxSmall-chapter-') or
                    name.startswith('SandboxSmall-Chapter'))
        bridge_chap_names = sorted(
            n for n in counts
            if not _is_ss_chap(n) and n != '(none)')
        bridge_rows = []
        for r in ch.rows:
            if r.get('Name') in bridge_chap_names and zstate(r) != 'Disabled':
                bridge_rows.append(r)

        total_h = 0
        zmin = None; zmax = None
        chap_ids = []
        ground_inserted = False
        for r in ss:
            L = get_field(r, 'Layer')
            mn = get_field(r, 'MinZ')
            mx = get_field(r, 'MaxZ')
            pz = get_field(r, 'PrimeZ')
            cid = get_field(r, 'ChapterID')
            es = get_field(r, 'EnemyScalingLevel')
            try:
                h = int(mx) - int(mn) + 1
            except (TypeError, ValueError):
                h = 0
            total_h += h
            if mn is not None:
                zmin = mn if zmin is None else min(zmin, mn)
            if mx is not None:
                zmax = mx if zmax is None else max(zmax, mx)
            if cid is not None:
                chap_ids.append(cid)
            cnt = counts.get(r['Name'], 0)

            if L == 0: lv = 'Lv-1'
            elif (L or 0) > 0: lv = f'Lv-{L+1}'
            else: lv = f'D-{-L}'

            tags = []
            if cnt == 0: tags.append('empty')
            if (L or 0) == 0: tags.append('ground')
            self.tree.insert('', tk.END, values=(
                lv, r['Name'],
                f'{L:+d}' if isinstance(L, int) else str(L),
                str(cid) if cid is not None else '-',
                str(es) if es is not None else '-',
                h,
                mn if mn is not None else '-',
                mx if mx is not None else '-',
                pz if pz is not None else '-',
                cnt,
            ), tags=tuple(tags))

        # Append bridge chapters (outdoor / cross-ZoneSet) at the bottom of
        # the chart so the user sees every chapter that hosts Live SS zones.
        for r in bridge_rows:
            L = get_field(r, 'Layer')
            mn = get_field(r, 'MinZ'); mx = get_field(r, 'MaxZ'); pz = get_field(r, 'PrimeZ')
            cid = get_field(r, 'ChapterID')
            es = get_field(r, 'EnemyScalingLevel')
            try:
                h = int(mx) - int(mn) + 1
            except (TypeError, ValueError):
                h = 0
            cnt = counts.get(r['Name'], 0)
            self.tree.insert('', tk.END, values=(
                'OUT',  # bridge / outdoor marker in Lv column
                r['Name'],
                f'{L:+d}' if isinstance(L, int) else str(L),
                str(cid) if cid is not None else '-',
                str(es) if es is not None else '-',
                h,
                mn if mn is not None else '-',
                mx if mx is not None else '-',
                pz if pz is not None else '-',
                cnt,
            ), tags=('bridge',))

        # Subtle visual markers — no heavy backgrounds. The user reported
        # the previous styling made rows hard to read.
        try:
            # Ground row: faint italic-style emphasis via slightly muted text
            self.tree.tag_configure('ground', foreground='#3d6cb9')
            # Empty chapter: orange (warning, not error)
            self.tree.tag_configure('empty', foreground='#d68910')
            # Outdoor / bridge: muted grey-green
            self.tree.tag_configure('bridge', foreground='#7a9a7a')
        except Exception:
            pass

        # Footer summary
        warns = []
        if zmin is not None and zmin < 0:
            warns.append(f'Z BOUNDS: min={zmin} (<0)')
        if zmax is not None and zmax > 29:
            warns.append(f'Z BOUNDS: max={zmax} (>29)')
        if total_h > 30:
            warns.append(f'Stack budget exceeded: {total_h}/30')
        dups = sorted({x for x in chap_ids if chap_ids.count(x) > 1})
        if dups:
            warns.append(f'Duplicate ChapterID(s): {dups} '
                         '(map UI buckets by ChapterID — collapses on travel-stone screen)')
        empties = [self.tree.set(iid, 'chapter')
                   for iid in self.tree.get_children('')
                   if 'empty' in self.tree.item(iid, 'tags')]
        if empties:
            warns.append(f'EMPTY Live chapter(s): {", ".join(empties)}')

        s = (f'Stack height: {total_h}/30   Range: Z={zmin}..{zmax}   '
             f'Live SandboxSmall zones: {total_zones}')
        if warns:
            s += '\n!! ' + '\n!! '.join(warns)
        self.v_summary.set(s)
        self.apply_sort(self.tree)


# -----------------------------------------------------------------------------
# MAP TAB — isometric visualizer
# -----------------------------------------------------------------------------

class MapTab(BaseTab):
    """3D-rotatable canvas showing zone positions + sizes + connectivity.

    Layout modes:
      - Grid: each zone placed in its own cell (no overlap, guaranteed separation)
      - True positions: use the zone's real Position(X,Y,Z) field
    Filtering: pick a single chapter or show all chapters.
    Controls:
      - Left-drag       : pan
      - Right-drag      : rotate (yaw=horizontal, pitch=vertical)
      - Mouse wheel     : zoom
      - Q / E           : yaw -/+ 10°
      - W / S           : pitch -/+ 10°
      - R               : reset view
    """
    SCALE = 24.0
    GRID_PAD = 2      # bubble-units of padding between cells in grid mode

    def __init__(self, parent, app):
        super().__init__(parent, app)
        # Persisted view state — pulled from settings.ini at startup so the
        # tab opens to the same view, layout, filter, etc. the user used last.
        try:
            yaw_def = float(SETTINGS.get_filter('map_yaw', '45') or 45)
            pitch_def = float(SETTINGS.get_filter('map_pitch', '35.26') or 35.26)
            scale_def = float(SETTINGS.get_filter('map_scale', '24') or 24)
            ox_def = float(SETTINGS.get_filter('map_offset_x', '500') or 500)
            oy_def = float(SETTINGS.get_filter('map_offset_y', '350') or 350)
        except (ValueError, TypeError):
            yaw_def, pitch_def, scale_def = 45.0, 35.26, 24.0
            ox_def, oy_def = 500.0, 350.0
        self._offset = [ox_def, oy_def]
        self._scale = scale_def
        self._yaw = yaw_def
        self._pitch = pitch_def
        self._pan_dragging = False
        self._rot_dragging = False
        self._zone_drag = None  # {name, press_xy, orig_pos, started, valid}
        self._drag_last = None
        self._box_refs = []
        self._show_connections = tk.BooleanVar(
            value=SETTINGS.get_filter_bool('map_show_connections', True))
        self._show_labels = tk.BooleanVar(
            value=SETTINGS.get_filter_bool('map_show_labels', True))
        self._show_landmarks = tk.BooleanVar(
            value=SETTINGS.get_filter_bool('map_show_landmarks', True))
        self._hide_unpinned = tk.BooleanVar(
            value=SETTINGS.get_filter_bool('map_hide_unpinned', False))
        self._layout_mode = tk.StringVar(
            value=SETTINGS.get_filter('map_layout_mode',
                                       'Grid (one per cell)')
                  or 'Grid (one per cell)')
        self._chapter_filter = tk.StringVar(
            value=SETTINGS.get_filter('map_chapter_filter',
                                       '(first chapter)') or '(first chapter)')
        # Persist on change. Each var write calls _persist_view() — which
        # stamps every setting at once (cheap; small dict).
        for var in (self._show_connections, self._show_labels,
                    self._show_landmarks, self._hide_unpinned,
                    self._layout_mode, self._chapter_filter):
            var.trace_add('write', lambda *_: self._persist_view())
        self._projected = {}
        self._build()

    def refresh_from_doc(self):
        self.redraw()

    def _persist_view(self):
        """Stamp all map view settings to settings.ini so they survive
        between sessions. Called on any var write or view-state change."""
        try:
            SETTINGS.set_filter('map_show_connections', self._show_connections.get())
            SETTINGS.set_filter('map_show_labels', self._show_labels.get())
            SETTINGS.set_filter('map_show_landmarks', self._show_landmarks.get())
            SETTINGS.set_filter('map_hide_unpinned', self._hide_unpinned.get())
            SETTINGS.set_filter('map_layout_mode', self._layout_mode.get())
            SETTINGS.set_filter('map_chapter_filter', self._chapter_filter.get())
            SETTINGS.set_filter('map_yaw', f'{self._yaw:.4f}')
            SETTINGS.set_filter('map_pitch', f'{self._pitch:.4f}')
            SETTINGS.set_filter('map_scale', f'{self._scale:.4f}')
            SETTINGS.set_filter('map_offset_x', f'{self._offset[0]:.2f}')
            SETTINGS.set_filter('map_offset_y', f'{self._offset[1]:.2f}')
        except Exception:
            pass

    def _build(self):
        toolbar = ttk.Frame(self); toolbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(toolbar, text='Chapter:').pack(side=tk.LEFT)
        self.cmb_chapter = ttk.Combobox(toolbar, textvariable=self._chapter_filter,
                                          width=30, state='readonly')
        self.cmb_chapter.pack(side=tk.LEFT, padx=(4, 12))
        self.cmb_chapter.bind('<<ComboboxSelected>>', lambda e: self.redraw())

        ttk.Label(toolbar, text='Layout:').pack(side=tk.LEFT)
        ttk.Combobox(toolbar, textvariable=self._layout_mode, width=22,
                     state='readonly',
                     values=['Grid (one per cell)',
                             'True positions (X,Y,Z)']
                     ).pack(side=tk.LEFT, padx=(4, 12))
        self._layout_mode.trace_add('write', lambda *_: self.redraw())

        ttk.Button(toolbar, text='Fit view', command=self.fit_view).pack(side=tk.LEFT)
        ttk.Button(toolbar, text='Reset', command=self.reset_view).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(toolbar, text='Connections',
                        variable=self._show_connections,
                        command=self.redraw).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(toolbar, text='Show labels',
                        variable=self._show_labels,
                        command=self.redraw).pack(side=tk.LEFT)
        ttk.Checkbutton(toolbar, text='Landmarks',
                        variable=self._show_landmarks,
                        command=self.redraw).pack(side=tk.LEFT, padx=(10, 0))
        # In True-positions mode, hide auto-placed zones (Pos=(0,0,0))
        ttk.Checkbutton(toolbar, text='Hide variable placement',
                        variable=self._hide_unpinned,
                        command=self.redraw).pack(side=tk.LEFT, padx=(10, 0))

        # View-preset buttons so the user isn't dependent on drag math
        ttk.Separator(toolbar, orient='vertical').pack(side=tk.LEFT,
                                                       fill='y', padx=10)
        ttk.Label(toolbar, text='View:').pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(toolbar, text='Top',
                   command=lambda: self._set_view(0, 90)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Iso',
                   command=lambda: self._set_view(45, 35.26)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Front',
                   command=lambda: self._set_view(0, 0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Side',
                   command=lambda: self._set_view(90, 0)).pack(side=tk.LEFT, padx=2)

        self.angle_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.angle_lbl.pack(side=tk.RIGHT, padx=(10, 0))
        self.info_lbl = ttk.Label(toolbar, text='', foreground='#555')
        self.info_lbl.pack(side=tk.RIGHT)

        # Initialise readouts
        self._update_angle_label()

        # Split: left side hierarchical zone list, right side the 3D canvas.
        paned = ttk.PanedWindow(self, orient='horizontal')
        paned.pack(fill=tk.BOTH, expand=True)

        # ----- Left pane: Level/Zone tree, chapter-colour-coded -----
        left = ttk.Frame(paned, width=260)
        paned.add(left, weight=0)
        ttk.Label(left, text='Levels & zones',
                  foreground=self.app.COLOR_MUTED).pack(anchor='w', padx=4, pady=(2, 2))
        tree_box = ttk.Frame(left); tree_box.pack(fill=tk.BOTH, expand=True)
        self.zone_tree = ttk.Treeview(tree_box, show='tree', selectmode='browse')
        ysb = ttk.Scrollbar(tree_box, orient='vertical', command=self.zone_tree.yview)
        self.zone_tree.configure(yscrollcommand=ysb.set)
        self.zone_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        # Same chapter colour scheme as the Zones tab.
        for ch, c in CHAPTER_COLORS.items():
            self.zone_tree.tag_configure(ch, background=c)
        self.zone_tree.bind('<<TreeviewSelect>>', self._on_zone_tree_select)

        # ----- Right pane: 3D canvas -----
        canvas_frame = ttk.Frame(paned)
        paned.add(canvas_frame, weight=1)
        self.canvas = tk.Canvas(canvas_frame, background='#202028',
                                 highlightthickness=0, takefocus=1)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # Left mouse = pan
        self.canvas.bind('<ButtonPress-1>', self._on_pan_press)
        self.canvas.bind('<B1-Motion>', self._on_pan_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_pan_release)
        # Right mouse = rotate (yaw + pitch)
        self.canvas.bind('<ButtonPress-3>', self._on_rot_press)
        self.canvas.bind('<B3-Motion>', self._on_rot_drag)
        self.canvas.bind('<ButtonRelease-3>', self._on_rot_release)
        # Middle mouse also rotates (for trackpad users)
        self.canvas.bind('<ButtonPress-2>', self._on_rot_press)
        self.canvas.bind('<B2-Motion>', self._on_rot_drag)
        self.canvas.bind('<ButtonRelease-2>', self._on_rot_release)
        # Zoom
        self.canvas.bind('<MouseWheel>', self._on_wheel)
        # Keyboard — requires canvas to have focus
        self.canvas.bind('<Enter>', lambda e: self.canvas.focus_set())
        self.canvas.bind('<Key-q>', lambda e: self._nudge_yaw(-10))
        self.canvas.bind('<Key-Q>', lambda e: self._nudge_yaw(-10))
        self.canvas.bind('<Key-e>', lambda e: self._nudge_yaw(+10))
        self.canvas.bind('<Key-E>', lambda e: self._nudge_yaw(+10))
        self.canvas.bind('<Key-w>', lambda e: self._nudge_pitch(+10))
        self.canvas.bind('<Key-W>', lambda e: self._nudge_pitch(+10))
        self.canvas.bind('<Key-s>', lambda e: self._nudge_pitch(-10))
        self.canvas.bind('<Key-S>', lambda e: self._nudge_pitch(-10))
        self.canvas.bind('<Key-r>', lambda e: self.reset_view())
        self.canvas.bind('<Key-R>', lambda e: self.reset_view())
        self.canvas.bind('<Configure>', lambda e: self.redraw())

    # ---- projection ----
    # Axis convention: Z is the VERTICAL / level axis (matches game data).
    # Position.Z = which floor. Size.Z = how many floors the zone spans.
    # X and Y are the horizontal plane.
    def _project(self, x, y, z):
        """3D->2D projection. Screen Y comes from world Z (vertical)."""
        rx, ry, rz = self._rotate(x, y, z)
        sx = rx * self._scale     # screen-horizontal from rotated-X
        sy = -ry * self._scale    # screen-vertical from rotated-Y (flipped)
        return self._offset[0] + sx, self._offset[1] + sy

    def _rotate(self, x, y, z):
        """Yaw around world Z (vertical), then pitch tilts camera down."""
        yaw = math.radians(self._yaw)
        pitch = math.radians(self._pitch)
        # Yaw around world Z axis — rotates X, Y on the horizontal plane
        cx = x * math.cos(yaw) - y * math.sin(yaw)
        cy = x * math.sin(yaw) + y * math.cos(yaw)
        cz = z
        # Pitch tilts around the camera's X axis — rotates Y and Z
        py = cy * math.cos(pitch) - cz * math.sin(pitch)
        pz = cy * math.sin(pitch) + cz * math.cos(pitch)
        return cx, py, pz

    def _iso_box(self, x, y, z, sx, sy, sz):
        """Return (top, left, right, front) polygon points.
        Z is vertical in world space — lid is at max-Z, sides are at X and Y extremes.
        Callers use front, right, top (in that draw order)."""
        def pt(dx, dy, dz):
            return self._project(x + dx, y + dy, z + dz)

        # 8 corners of the box [x..x+sx, y..y+sy, z..z+sz]
        c000 = pt(0,  0,  0)
        c100 = pt(sx, 0,  0)
        c010 = pt(0,  sy, 0)
        c110 = pt(sx, sy, 0)
        c001 = pt(0,  0,  sz)
        c101 = pt(sx, 0,  sz)
        c011 = pt(0,  sy, sz)
        c111 = pt(sx, sy, sz)

        # Top face — the lid at max-Z (seen from above)
        top   = [c001, c101, c111, c011]
        # Side face at max-X (visible at default yaw=45)
        right = [c100, c110, c111, c101]
        # Side face at min-Y / front (visible at default yaw=45)
        front = [c000, c100, c101, c001]
        # Left side face at min-X (opposite side of right)
        left  = [c000, c010, c011, c001]
        return top, left, right, front

    # ---- data ----
    def refresh_from_doc(self):
        self._populate_chapter_dropdown()
        self._populate_zone_tree()
        self.redraw()

    def _populate_zone_tree(self):
        """Build the left-side level/zone hierarchy. Top-level rows are the
        14 SandboxSmall level chapters (Level1..Level7 + Deep1..Deep7), sorted
        top-to-bottom by Layer descending. Nested under each level: every Live
        SandboxSmall zone whose Chapter shares that floor's chapter # (covers
        zone/landmark-anchored rows that share the level's CID). Each row is
        colour-coded with the same CHAPTER_COLORS scheme used on the Zones tab.
        Selecting a level switches the canvas filter; selecting a zone selects
        its level and (best-effort) highlights it on the redraw."""
        tr = self.zone_tree
        # remember selection across rebuilds
        prev_sel = tr.selection()[0] if tr.selection() else None
        for iid in tr.get_children(''):
            tr.delete(iid)

        chap_doc = self.app.docs.get('chapters')
        zone_doc = self.app.docs.get('zones')
        if chap_doc is None or zone_doc is None:
            return

        def fp(v, n):
            for p in v or []:
                if isinstance(p, dict) and p.get('Name') == n:
                    return p
            return None
        def get(r, k):
            p = fp(r['Value'], k)
            if not p: return None
            v = p.get('Value')
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        return it.get('Value', '')
            return v
        def state(r):
            p = fp(r.get('Value', []), 'EnabledState')
            return str(p.get('Value', '')).split('::')[-1] if p else None

        def is_level(name):
            tail = name.split('.', 1)[1] if '.' in name else ''
            return (name.startswith('SandboxSmall-chapter-') or
                    (name.startswith('SandboxSmall-Chapter') and
                     (tail.startswith('Level') or tail.startswith('Deep'))))

        # Build level rows sorted by Layer descending (top floor first).
        level_rows = [r for r in chap_doc.rows
                      if is_level(r.get('Name', '')) and state(r) != 'Disabled']
        def lkey(r):
            L = get(r, 'Layer')
            return -(L if isinstance(L, int) else 0)
        level_rows.sort(key=lkey)

        # Index zones by chapter # so we can group efficiently.
        zones_by_tag = {}
        for r in zone_doc.rows:
            if state(r) == 'Disabled': continue
            zs = fp(r.get('Value', []), 'ZoneSet')
            if zs is None: continue
            if str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            chap = get(r, 'Chapter') or ''
            tag = chapter_color_tag(chap)
            if not tag: continue
            zones_by_tag.setdefault(tag, []).append(r['Name'])

        # Insert level + nested zone rows.
        for r in level_rows:
            name = r['Name']
            L = get(r, 'Layer')
            tag = chapter_color_tag(name)
            if isinstance(L, int):
                lv = 'Lv-1' if L == 0 else (f'Lv-{L+1}' if L > 0 else f'D-{-L}')
            else:
                lv = '?'
            # Strip the SandboxSmall- prefix on the level label for compactness.
            short = name.replace('SandboxSmall-', '')
            label = f'{lv}  {short}'
            level_iid = tr.insert('', tk.END, iid=name, text=label,
                                  tags=(tag,) if tag else ())
            # Nested zones — sort alphabetically.
            for zname in sorted(zones_by_tag.get(tag, [])):
                tr.insert(level_iid, tk.END, iid=zname, text=zname,
                          tags=(tag,) if tag else ())
            tr.item(level_iid, open=True)

        # Restore previous selection if still present.
        if prev_sel and tr.exists(prev_sel):
            tr.selection_set(prev_sel)
            tr.see(prev_sel)

    def _on_zone_tree_select(self, _e=None):
        """Tree selection: a level row sets the chapter filter to that level;
        a zone row sets the filter to the zone's level (so its containing
        floor is shown), and remembers the zone name so redraw can highlight
        it (best-effort outline; noop if zone has no rendered cell yet)."""
        sel = self.zone_tree.selection()
        if not sel: return
        iid = sel[0]
        chap_doc = self.app.docs.get('chapters')
        if chap_doc is None: return

        # Level row: iid is the level's chapter row name.
        is_level_iid = iid.startswith('SandboxSmall-chapter-') or (
            iid.startswith('SandboxSmall-Chapter')
            and '.' in iid
            and (iid.split('.', 1)[1].startswith('Level')
                 or iid.split('.', 1)[1].startswith('Deep')))
        self._highlight_zone = None
        if is_level_iid:
            if self._chapter_filter.get() != iid:
                self._chapter_filter.set(iid)
            self.redraw()
            return

        # Zone row: iid is the zone Name. Resolve its chapter -> level row.
        zone_doc = self.app.docs.get('zones')
        if zone_doc is None: return
        target = next((r for r in zone_doc.rows if r.get('Name') == iid), None)
        if target is None: return

        def fp(v, n):
            for p in v or []:
                if isinstance(p, dict) and p.get('Name') == n: return p
            return None
        def get(r, k):
            p = fp(r['Value'], k)
            if not p: return None
            v = p.get('Value')
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        return it.get('Value', '')
            return v

        chap = get(target, 'Chapter') or ''
        tag = chapter_color_tag(chap)
        # Find the level row whose name has the same chapter # tag.
        level_match = None
        for r in chap_doc.rows:
            n = r.get('Name', '')
            if chapter_color_tag(n) == tag:
                tail = n.split('.', 1)[1] if '.' in n else ''
                if tail.startswith('Level') or tail.startswith('Deep') \
                        or n.startswith('SandboxSmall-chapter-'):
                    level_match = n; break
        if level_match and self._chapter_filter.get() != level_match:
            self._chapter_filter.set(level_match)
        self._highlight_zone = iid
        self.redraw()

    def _populate_chapter_dropdown(self):
        zone_doc = self.app.docs.get('zones')
        chap_doc = self.app.docs.get('chapters')
        if zone_doc is None:
            return
        # Build a list of LEVEL chapters only (not zone/landmark-anchored
        # rows). A LEVEL row's name suffix is 'LevelN' or 'DeepN', or the
        # row uses the legacy 'SandboxSmall-chapter-N' pattern. Match
        # chapter rows -> their chapter # so the dropdown is one entry per
        # actual floor.
        level_rows = []
        if chap_doc:
            for cr in chap_doc.rows:
                name = cr.get('Name', '') or ''
                tail = name.split('.', 1)[1] if '.' in name else ''
                is_level = (
                    name.startswith('SandboxSmall-chapter-') or
                    (name.startswith('SandboxSmall-Chapter') and
                     (tail.startswith('Level') or tail.startswith('Deep')))
                )
                if is_level:
                    level_rows.append(name)

        # Sort by chapter #
        def lvl_key(c):
            tag = chapter_color_tag(c) or ''
            try:
                return (0, int(tag.rsplit('-', 1)[-1]) if tag else 99)
            except Exception:
                return (1, c)
        level_rows.sort(key=lvl_key)

        options = ['All chapters'] + level_rows + ['All (incl. campaign zones)']
        self.cmb_chapter['values'] = options
        cur = self._chapter_filter.get()
        if cur not in options:
            self._chapter_filter.set(level_rows[0] if level_rows else 'All chapters')

    def _visible_zones(self):
        """Return the list of zones to render. When a single level chapter
        is selected, also include 'ghost' zones from other levels whose Z
        range bleeds into the selected level's Z band — these draw greyed
        out so the user can see what's already occupying overhead/below
        cells while positioning current-level zones in Front view."""
        zones = [ZoneView(r) for r in self.app.docs['zones'].rows]
        choice = self._chapter_filter.get()
        self._ghost_zones = set()

        if choice == 'All (incl. campaign zones)':
            result = zones
        elif choice == 'All chapters':
            result = [z for z in zones if z.zone_set == 'SandboxSmall']
        else:
            # Specific level chapter selected: match all zones whose chapter
            # shares the same chapter # (i.e., on the same floor — covers
            # both the level row and any zone/landmark-anchored rows).
            target_tag = chapter_color_tag(choice)
            if target_tag is None:
                result = [z for z in zones
                          if z.chapter == choice
                          or choice in z.additional_chapters]
            else:
                # Match either the primary Chapter OR any AdditionalChapters
                # entry — vanilla elevators register bridge-floor membership
                # via AdditionalChapters, so a stair zone whose primary chapter
                # is Lv-1 but which lists Lv-2 in AdditionalChapters should
                # render when the user views Lv-2.
                def _matches_target(z):
                    if z.zone_set != 'SandboxSmall': return False
                    if chapter_color_tag(z.chapter) == target_tag: return True
                    for ac in z.additional_chapters:
                        if chapter_color_tag(ac) == target_tag:
                            return True
                    return False
                result = [z for z in zones if _matches_target(z)]

            # Add ghost zones: any other SandboxSmall zone whose Z extent
            # overlaps the selected level's Z band. Useful in Front view
            # when positioning since the user can see "what else is there
            # at this Z layer" without losing focus on the current level.
            sel_zmin, sel_zmax = self._chapter_z_band(choice)
            if sel_zmin is not None:
                primary_names = {z.name for z in result}
                for z in zones:
                    if z.name in primary_names: continue
                    if z.zone_set != 'SandboxSmall': continue
                    pos = z.position
                    sz = z.target_size
                    if not pos or not sz: continue
                    if pos == (0, 0, 0): continue  # auto-place — skip
                    z_lo = pos[2]
                    z_hi = pos[2] + max(sz[2], 1) - 1
                    # overlap test
                    if z_hi >= sel_zmin and z_lo <= sel_zmax:
                        result.append(z)
                        self._ghost_zones.add(z.name)

        # Hide-variable-placement: drop zones at the auto-place sentinel
        # (Pos=(0,0,0)). Only meaningful in True-positions mode (in Grid
        # mode, positions are computed regardless of the data Pos).
        if self._hide_unpinned.get() and self._layout_mode.get().startswith('True'):
            result = [z for z in result if z.position != (0, 0, 0)]
        return result

    def _chapter_z_band(self, chapter_name):
        """Return (MinZ, MaxZ) for the selected chapter row, or (None, None)
        if not resolvable. Falls back to scanning chapter rows that share
        the same chapter # (so anchored rows also work)."""
        chap_doc = self.app.docs.get('chapters')
        if chap_doc is None:
            return (None, None)
        target_tag = chapter_color_tag(chapter_name)
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name or chapter_color_tag(r.get('Name','')) == target_tag:
                mn = find_prop(r.get('Value', []), 'MinZ')
                mx = find_prop(r.get('Value', []), 'MaxZ')
                mn_v = mn.get('Value') if mn else None
                mx_v = mx.get('Value') if mx else None
                if mn_v is not None and mx_v is not None:
                    return (int(mn_v), int(mx_v))
        return (None, None)

    def _landmark_to_zones(self):
        """Map each landmark RowName to list of zone_names that reference it."""
        out = {}
        for z in self._visible_zones():
            for e in z.landmark_entries():
                lm = e['landmark']
                if lm:
                    out.setdefault(lm, []).append(z.name)
        return out

    def _landmark_connections(self):
        lm_doc = self.app.docs.get('landmarks')
        if lm_doc is None: return {}
        out = {}
        for r in lm_doc.rows:
            lm = LandmarkView(r)
            cs = lm.connections()
            if cs:
                out[lm.name] = cs
        return out

    # ---- layout ----
    def _layout_positions(self, zones):
        """Return dict: zone.name -> (origin_x, origin_y, origin_z) in bubble units.
        Grid mode lays zones out in a row grid, Z grouped by chapter."""
        mode = self._layout_mode.get()
        result = {}
        if mode.startswith('True'):
            for z in zones:
                result[z.name] = z.position
            return result

        # Grid mode — arrange zones left→right, wrapping when the row gets wide.
        # Each zone occupies (size_x + PAD) by (size_y + PAD) in bubble units.
        # Group by chapter: each chapter gets its own Z-layer so multiple
        # chapters stack vertically in 'All chapters' view, else all on z=0.
        pad = self.GRID_PAD
        choice = self._chapter_filter.get()
        single_chapter = choice not in ('All chapters',
                                         'All (incl. campaign zones)')

        # Group zones by chapter # (so anchored chapter rows sharing the
        # same level # collapse into one stack layer).
        groups = {}
        group_keys = {}  # tag -> a representative chapter row name (for sorting)
        for z in zones:
            tag = chapter_color_tag(z.chapter) or (z.chapter or '(none)')
            groups.setdefault(tag, []).append(z)
            group_keys.setdefault(tag, z.chapter or '(none)')
        # Sort by chapter # via the tag
        def ch_key(t):
            try:
                return (0, int(t.rsplit('-', 1)[-1]))
            except Exception:
                return (1, t)
        ordered_chapters = sorted(groups.keys(), key=ch_key)

        # Determine target columns — aim for roughly sqrt(n) per chapter
        z_layer = 0
        for ch in ordered_chapters:
            group = groups[ch]
            # Wrap when row width exceeds ~28 bubble-units
            row_width_target = 28
            cursor_x = 0
            cursor_y = 0
            row_max_h = 0
            for z in group:
                sx, sy, sz = z.target_size
                if cursor_x > 0 and cursor_x + sx > row_width_target:
                    cursor_x = 0
                    cursor_y += row_max_h + pad
                    row_max_h = 0
                # When stacking chapters, raise z_layer between chapters
                result[z.name] = (cursor_x, cursor_y, z_layer)
                cursor_x += sx + pad
                row_max_h = max(row_max_h, sy)
            # Next chapter starts a new row OR new z-layer when stacking
            if not single_chapter:
                z_layer += max(6, 0)  # rise by 6 bubble-units per chapter
                # Actually stack visually by offsetting Y instead of Z so
                # isometric view shows chapters as distinct horizontal strips
                # (Z==0 keeps things readable). Use Y strip offset:
                # We already advanced cursor_y within the chapter; jump further.
                z_layer = 0
        return result

    # ---- drawing ----
    def redraw(self):
        c = self.canvas
        c.delete('all')
        self._box_refs.clear()
        self._projected.clear()

        zones = self._visible_zones()
        # Cache the current level's Z band so zone drawing can flag zones
        # whose Size.Z extends past the level's MinZ/MaxZ.
        choice = self._chapter_filter.get()
        if choice in ('All chapters', 'All (incl. campaign zones)') or not choice:
            self._sel_z_band = (None, None)
        else:
            self._sel_z_band = self._chapter_z_band(choice)

        if not zones:
            c.create_text(20, 20, anchor='nw', fill='#aaa',
                          text='No zones loaded.')
            self.info_lbl.config(text='')
            return

        positions = self._layout_positions(zones)
        # Store for click / connection math
        self._positions = positions

        # For stable drawing order: sort back-to-front using the rotated
        # depth (post-rotation z component). Boxes farther from the camera
        # draw first so nearer boxes correctly overlap them.
        def draw_key(z):
            ox, oy, oz = positions[z.name]
            sx, sy, sz = z.target_size
            # Use rotated z at box centre as the depth key; higher z after
            # rotation means closer to viewer, so sort ascending → far first.
            _, _, rz = self._rotate(ox + sx/2, oy + sy/2, oz + sz/2)
            return -rz
        zones_sorted = sorted(zones, key=draw_key)

        self._draw_grid(c, zones, positions)

        # Connections
        if self._show_connections.get():
            lm_to_zones = self._landmark_to_zones()
            connections = self._landmark_connections()
            zone_centers = {}
            for z in zones:
                ox, oy, oz = positions[z.name]
                sx, sy, sz = z.target_size
                zone_centers[z.name] = self._project(
                    ox + sx/2, oy + sy/2, oz + sz/2)
            seen_pairs = set()
            for lm_a, neighbors in connections.items():
                zones_a = lm_to_zones.get(lm_a, [])
                for lm_b in neighbors:
                    zones_b = lm_to_zones.get(lm_b, [])
                    for za in zones_a:
                        for zb in zones_b:
                            if za == zb: continue
                            key = tuple(sorted([za, zb]))
                            if key in seen_pairs: continue
                            seen_pairs.add(key)
                            if za in zone_centers and zb in zone_centers:
                                x1, y1 = zone_centers[za]
                                x2, y2 = zone_centers[zb]
                                c.create_line(x1, y1, x2, y2,
                                              fill='#5aff5a', width=2, dash=(4, 3))

        for z in zones_sorted:
            self._draw_zone(c, z, positions[z.name])

        n = len(zones)
        mode = self._layout_mode.get()
        ch = self._chapter_filter.get()
        self.info_lbl.config(
            text=f'{n} zones in “{ch}” · {mode} · scale={self._scale:.1f}px/unit')

    def _draw_grid(self, c, zones, positions):
        # Z is vertical, so "ground" is the X-Y plane at Z=0.
        # Always draw the full world grid 0..29 x 0..29 so the user can see
        # how much of the playable area is filled vs. empty regardless of
        # which zones are currently visible.
        x0, x1 = 0, 29
        y0, y1 = 0, 29
        # Slightly brighter outer border at the world edges (0 and 29) so
        # the playable boundary is unmistakable.
        for xv in range(x0, x1 + 1):
            p1 = self._project(xv, y0, 0)
            p2 = self._project(xv, y1, 0)
            colour = '#5a6680' if xv in (x0, x1) else '#303040'
            c.create_line(p1[0], p1[1], p2[0], p2[1], fill=colour)
        for yv in range(y0, y1 + 1):
            p1 = self._project(x0, yv, 0)
            p2 = self._project(x1, yv, 0)
            colour = '#5a6680' if yv in (y0, y1) else '#303040'
            c.create_line(p1[0], p1[1], p2[0], p2[1], fill=colour)

    def _chapter_color(self, chapter):
        tag = chapter_color_tag(chapter)
        return CHAPTER_COLORS.get(tag, '#909090') if tag else '#909090'

    def _draw_zone(self, c, z, origin):
        ox, oy, oz = origin
        sx, sy, sz = z.target_size
        top, left, right, front = self._iso_box(ox, oy, oz, sx, sy, max(sz, 1))
        is_ghost = z.name in getattr(self, '_ghost_zones', set())
        if is_ghost:
            # Greyed-out: show that this zone occupies space at this Z layer
            # but is owned by a different level. Light grey + stippled to
            # reduce visual weight versus the in-focus level zones.
            col = '#888888'
            stipple = 'gray50'
        else:
            col = self._chapter_color(z.chapter)
            stipple = '' if z.is_enabled else 'gray50'
        top_col = col
        right_col = self._darken(col, 0.75)
        front_col = self._darken(col, 0.6)

        items = []
        items.append(c.create_polygon(front, fill=front_col, outline='#000',
                                       stipple=stipple, tags=('zone', z.name)))
        items.append(c.create_polygon(right, fill=right_col, outline='#000',
                                       stipple=stipple, tags=('zone', z.name)))
        items.append(c.create_polygon(top, fill=top_col, outline='#000',
                                       stipple=stipple, tags=('zone', z.name)))

        # Draw an "extends" marker on any zone whose Z range crosses past
        # the currently selected level's Z band. ↑ = extends above, ↓ =
        # extends below, ↕ = both. Only meaningful when a single level is
        # selected (sel_z_band is set).
        sel_lo, sel_hi = getattr(self, '_sel_z_band', (None, None))
        if sel_lo is not None and sel_hi is not None:
            world_pos = z.position if hasattr(z, 'position') else (0, 0, 0)
            world_sz_z = z.target_size[2] if z.target_size else 1
            if world_pos and world_pos != (0, 0, 0):
                z_lo = world_pos[2]
                z_hi = world_pos[2] + max(world_sz_z, 1) - 1
                up_extend = z_hi > sel_hi
                dn_extend = z_lo < sel_lo
                if up_extend or dn_extend:
                    if up_extend and dn_extend:
                        glyph = '↕'
                    elif up_extend:
                        glyph = '↑'
                    else:
                        glyph = '↓'
                    cx_m, cy_m = self._project(ox + sx - 0.5, oy + 0.5, oz + sz)
                    items += self._halo_text(c, cx_m, cy_m, glyph,
                                              fill='#ff3b30',
                                              font=('Segoe UI', 14, 'bold'),
                                              tag_name=z.name)

        if self._show_labels.get():
            # Compute how much screen space the zone's top face actually
            # occupies, then size text so it fits inside the zone box.
            top_xs = [p[0] for p in top]; top_ys = [p[1] for p in top]
            box_w = max(top_xs) - min(top_xs)
            box_h = max(top_ys) - min(top_ys)
            # Heuristic font size: scale with zone footprint, clamp 7..11.
            # Ghost zones use a slightly smaller font to keep the
            # current-level labels visually dominant.
            base_size = max(7, min(11, int(box_h / 6)))
            font_size = max(6, base_size - (1 if is_ghost else 0))
            size_font_size = max(6, font_size - 1)
            cx, cy = self._project(ox + sx/2, oy + sy/2, oz + sz)
            short = z.name.replace('Sandbox_Small_', '').replace('Sandbox_', '')
            # Ghost zones use muted colours instead of the bright neon so
            # they don't compete for attention with focus-level zones.
            if is_ghost:
                name_fill = '#cccccc'
                size_fill = '#a8a8a8'
            else:
                name_fill = '#ff9500'
                size_fill = '#ffeb3b'
            tiny = box_w < 40 or box_h < 28
            very_tiny = box_w < 28 or box_h < 18
            if not very_tiny:
                line_gap = font_size + 2
                items += self._halo_text(c, cx, cy - line_gap // 2, short,
                                          fill=name_fill,
                                          font=('Segoe UI', font_size, 'bold'),
                                          tag_name=z.name)
                if not tiny:
                    items += self._halo_text(c, cx, cy + line_gap // 2,
                                              f'{sx}x{sy}x{sz}',
                                              fill=size_fill,
                                              font=('Segoe UI', size_font_size, 'bold'),
                                              tag_name=z.name)

        # Landmark markers on top face (Z-as-vertical: top face at Z + SZ)
        if self._show_landmarks.get():
            items += self._draw_zone_landmarks(c, z, ox, oy, oz + max(sz, 1),
                                                sx, sy)

        for iid in items:
            # Press starts a (possible) zone-drag; release decides click-vs-drag.
            c.tag_bind(iid, '<ButtonPress-1>',
                       lambda e, zn=z.name: self._zone_press(e, zn))
        self._box_refs.append((z.name, items))

    def _draw_zone_landmarks(self, c, z, ox, oy, top_z, sx, sy):
        """Draw landmarks as coloured markers on the zone's top face.
        Z is vertical, so the top face is at world-Z = top_z and spans
        the X-Y horizontal plane. Colour coding:
          - Extended connectivity (transit hub): orange diamond
          - PlayerStart landmark: green star
          - Everything else: cyan circle
        Returns the list of canvas item IDs created."""
        entries = z.landmark_entries()
        if not entries:
            return []

        # Resolve extra info (player start flag) from DT_Moria_Landmarks
        lm_doc = self.app.docs.get('landmarks')
        lm_info = {}
        if lm_doc is not None:
            for r in lm_doc.rows:
                nm = r.get('Name', '')
                if not nm: continue
                ps = False
                for p in r.get('Value', []):
                    if p.get('Name') == 'bPlayerStartLocation':
                        ps = bool(p.get('Value', False))
                        break
                lm_info[nm] = ps

        items = []
        n = len(entries)
        import math as _m
        cols = int(_m.ceil(_m.sqrt(n)))
        if cols < 1:
            cols = 1
        rows = int(_m.ceil(n / cols))
        inset = 0.35
        grid_w = max(sx - 2 * inset, 0.01)
        grid_h = max(sy - 2 * inset, 0.01)

        for i, e in enumerate(entries):
            lm_name = e['landmark'] or ''
            row = i // cols
            col = i % cols
            fx = inset + (col + 0.5) * (grid_w / cols)
            fy = inset + (row + 0.5) * (grid_h / rows)
            cx, cy = self._project(ox + fx, oy + fy, top_z)

            # Classification
            is_ext = bool(e.get('extended'))
            is_start = lm_info.get(lm_name, False)
            if is_start:
                color = '#39ff14'   # neon green for player start
                shape = 'star'
            elif is_ext:
                color = '#ff9500'   # safety orange for transit landmarks
                shape = 'diamond'
            else:
                color = '#00e5ff'   # neon cyan for normal landmarks
                shape = 'circle'

            items += self._draw_marker(c, cx, cy, color, shape, tag=('landmark', z.name))

            # Short label under the marker
            short = lm_name.replace('Chapter', 'Ch').replace('Sandbox.', 'SB.')
            # Limit to the last '.' fragment if still long
            if '.' in short and len(short) > 22:
                short = short.split('.', 1)[-1]
            items += self._halo_text(c, cx, cy + 11, short,
                                      fill=color,
                                      font=('Segoe UI', 7, 'bold'),
                                      tag_name=z.name)
        return items

    @staticmethod
    def _draw_marker(canvas, x, y, color, shape, tag):
        """Draw a small marker at screen coords (x, y). Returns canvas items."""
        r = 5
        items = []
        if shape == 'circle':
            items.append(canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color, outline='black', width=1.5, tags=tag))
        elif shape == 'diamond':
            items.append(canvas.create_polygon(
                x, y - r - 1, x + r + 1, y, x, y + r + 1, x - r - 1, y,
                fill=color, outline='black', width=1.5, tags=tag))
        elif shape == 'star':
            # 5-point star
            import math as _m
            pts = []
            for k in range(10):
                ang = -_m.pi / 2 + k * _m.pi / 5
                rr = r + 2 if k % 2 == 0 else r / 2.2
                pts.append(x + rr * _m.cos(ang))
                pts.append(y + rr * _m.sin(ang))
            items.append(canvas.create_polygon(
                pts, fill=color, outline='black', width=1.5, tags=tag))
        return items

    @staticmethod
    def _halo_text(canvas, x, y, text, fill, font, tag_name):
        """Draw `text` with a 4-direction black halo for guaranteed legibility.
        Returns the list of canvas item IDs (4 outline + 1 fill = 5 items)."""
        items = []
        # Outline pass: draw in black at 1px offsets in 8 directions (or 4 diag
        # to keep draw count down) so the fill text stands out on any backdrop.
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -2), (0, 2)):
            items.append(canvas.create_text(
                x + dx, y + dy, text=text, fill='#000000', font=font,
                tags=('label', tag_name)))
        # Fill pass
        items.append(canvas.create_text(
            x, y, text=text, fill=fill, font=font,
            tags=('label', tag_name)))
        return items

    @staticmethod
    def _darken(hex_color, factor):
        try:
            r = int(hex_color[1:3], 16); g = int(hex_color[3:5], 16); b = int(hex_color[5:7], 16)
            r = int(r * factor); g = int(g * factor); b = int(b * factor)
            return f'#{r:02x}{g:02x}{b:02x}'
        except Exception:
            return hex_color

    def _click_zone(self, zone_name):
        # Jump to Zones tab and select the zone
        if hasattr(self.app, 'nb') and hasattr(self.app, 'zone_tab'):
            self.app.nb.select(self.app.zone_tab)
            if self.app.zone_tab.tree.exists(zone_name):
                self.app.zone_tab.tree.selection_set(zone_name)
                self.app.zone_tab.tree.see(zone_name)

    # ---- pan / rotate / zoom ----
    def _is_top_down(self):
        """True when the camera is roughly aligned for a top-down (X/Y) view.
        In this projection, yaw=0 + pitch=0 maps world X -> screen X and
        world Y -> screen -Y, which is what we want for drag editing.
        Allow ±15° tolerance so minor rotational drift doesn't disable drag."""
        yaw_norm = self._yaw % 360
        return ((yaw_norm < 15 or yaw_norm > 345)
                and abs(self._pitch) < 15)

    def _zone_press(self, event, zone_name):
        """Press on a zone polygon. In top-down view, prep a drag — works
        for both current-level zones and ghost zones (so you can align
        zones from neighbouring levels too). In other views, the press
        falls through to a click on release (jumps to Zones tab)."""
        self.canvas.focus_set()
        # Find the zone's current world position (X, Y, Z)
        zone_doc = self.app.docs.get('zones')
        orig = (0, 0, 0)
        if zone_doc:
            for r in zone_doc.rows:
                if r.get('Name') == zone_name:
                    z = ZoneView(r)
                    orig = z.position
                    break
        self._zone_drag = {
            'name': zone_name,
            'press_xy': (event.x, event.y),
            'orig_pos': orig,
            'started': False,
            'top_down': self._is_top_down(),
        }
        # Suppress pan when we have a zone press
        self._pan_dragging = False
        return 'break'

    def _on_pan_press(self, event):
        # If a zone press already captured this event, do nothing.
        if self._zone_drag is not None:
            return
        self._pan_dragging = True; self._drag_last = (event.x, event.y)
        self.canvas.focus_set()

    def _on_pan_drag(self, event):
        # Zone drag (top-down only) takes precedence over pan.
        if self._zone_drag is not None and self._zone_drag.get('top_down'):
            self._zone_drag_motion(event)
            return
        if not self._pan_dragging: return
        dx = event.x - self._drag_last[0]
        dy = event.y - self._drag_last[1]
        self._offset[0] += dx; self._offset[1] += dy
        self._drag_last = (event.x, event.y)
        self.redraw()

    def _on_pan_release(self, event):
        # Zone drag end → commit or click.
        if self._zone_drag is not None:
            self._zone_drag_release(event)
            return
        if self._pan_dragging:
            self._pan_dragging = False; self._drag_last = None
            self._persist_view()
        else:
            self._pan_dragging = False; self._drag_last = None

    def _zone_drag_motion(self, event):
        """Translate screen pixel delta into world cell delta (top-down).
        Clamp the new position so the zone's full X/Y footprint stays
        inside the world grid (0..29) — accounts for the zone's size,
        not just the origin cell."""
        d = self._zone_drag
        dx_px = event.x - d['press_xy'][0]
        dy_px = event.y - d['press_xy'][1]
        # Scale: pixels per world unit. Top-down → screen X = world X * scale,
        # screen Y = -world Y * scale. Snap to integer cells.
        scale = self._scale or 1
        dx_cell = int(round(dx_px / scale))
        dy_cell = int(round(-dy_px / scale))
        if dx_cell == 0 and dy_cell == 0 and not d['started']:
            return
        d['started'] = True
        ox, oy, oz = d['orig_pos']
        # Look up the zone's size so we can clamp the FAR edge to <= 29
        zone_doc = self.app.docs.get('zones')
        sx = sy = 1
        if zone_doc:
            for r in zone_doc.rows:
                if r.get('Name') == d['name']:
                    z = ZoneView(r)
                    sx, sy, _ = z.target_size
                    break
        # Each axis: 0 <= origin <= 29 - (size-1)
        max_x = max(0, 29 - max(sx, 1) + 1)
        max_y = max(0, 29 - max(sy, 1) + 1)
        new_x = max(0, min(max_x, ox + dx_cell))
        new_y = max(0, min(max_y, oy + dy_cell))
        self._set_zone_pos_in_doc(d['name'], new_x, new_y, oz)
        self.redraw()

    def _zone_drag_release(self, event):
        d = self._zone_drag
        self._zone_drag = None
        if not d['started']:
            # No motion: treat as a click (jump to Zones tab).
            self._click_zone(d['name'])
            return
        # Motion happened — Pos already updated in the doc. Mark dirty +
        # refresh the Zones tab so the new position is visible there.
        if hasattr(self.app, '_set_dirty'):
            self.app._set_dirty('zones', True)
        elif hasattr(self.app, 'mark_dirty'):
            self.app.mark_dirty('zones')
        if hasattr(self.app, 'zone_tab'):
            try: self.app.zone_tab.refresh_from_doc()
            except Exception: pass
        if hasattr(self.app, 'refresh_status'):
            self.app.refresh_status()

    def _set_zone_pos_in_doc(self, zone_name, new_x, new_y, new_z):
        """Update Position.X/Y on the named zone row directly in the doc."""
        zone_doc = self.app.docs.get('zones')
        if not zone_doc: return
        for r in zone_doc.rows:
            if r.get('Name') != zone_name: continue
            for prop in r.get('Value', []):
                if not isinstance(prop, dict): continue
                if prop.get('Name') != 'Position': continue
                val = prop.get('Value')
                if not isinstance(val, list) or not val: continue
                inner = val[0]
                if isinstance(inner, dict) and isinstance(inner.get('Value'), dict):
                    inner['Value']['X'] = int(new_x)
                    inner['Value']['Y'] = int(new_y)
                    inner['Value']['Z'] = int(new_z)
            return

    def _on_rot_press(self, event):
        self._rot_dragging = True; self._drag_last = (event.x, event.y)
        self.canvas.focus_set()

    def _on_rot_drag(self, event):
        if not self._rot_dragging: return
        dx = event.x - self._drag_last[0]
        dy = event.y - self._drag_last[1]
        # 0.4° per pixel is a sensitive but readable rate
        self._yaw = (self._yaw + dx * 0.4) % 360
        self._pitch = max(-89, min(89, self._pitch - dy * 0.4))
        self._drag_last = (event.x, event.y)
        self._update_angle_label()
        self.redraw()

    def _on_rot_release(self, _event):
        self._rot_dragging = False; self._drag_last = None
        self._persist_view()

    def _nudge_yaw(self, delta):
        self._yaw = (self._yaw + delta) % 360
        self._update_angle_label(); self.redraw(); self._persist_view()

    def _nudge_pitch(self, delta):
        self._pitch = max(-89, min(89, self._pitch + delta))
        self._update_angle_label(); self.redraw(); self._persist_view()

    def _update_angle_label(self):
        if hasattr(self, 'angle_lbl'):
            self.angle_lbl.config(
                text=f'yaw {self._yaw:5.1f}°  pitch {self._pitch:5.1f}°')

    def _on_wheel(self, event):
        delta = 1.1 if event.delta > 0 else (1 / 1.1)
        # Zoom around cursor
        cx, cy = event.x, event.y
        # adjust offset so the point under cursor stays fixed
        self._offset[0] = cx - (cx - self._offset[0]) * delta
        self._offset[1] = cy - (cy - self._offset[1]) * delta
        self._scale *= delta
        self.redraw(); self._persist_view()

    def fit_view(self):
        zones = self._visible_zones()
        if not zones: return
        positions = self._layout_positions(zones)
        xs, ys = [], []
        for z in zones:
            ox, oy, oz = positions[z.name]
            sx, sy, sz = z.target_size
            for dx in (0, sx):
                for dy in (0, sy):
                    xs.append(ox + dx); ys.append(oy + dy)
        self._offset = [0, 0]
        pts = [self._project(x, y, 0) for x, y in zip(xs, ys)]
        sxs = [p[0] for p in pts]; sys_ = [p[1] for p in pts]
        w = self.canvas.winfo_width() or 1000
        h = self.canvas.winfo_height() or 700
        bx = (max(sxs) - min(sxs)) or 1
        by = (max(sys_) - min(sys_)) or 1
        fit_scale = 0.85 * min(w / bx, h / by)
        self._scale *= fit_scale
        self._offset = [w / 2 - (min(sxs) + bx/2) * fit_scale,
                        h / 2 - (min(sys_) + by/2) * fit_scale]
        self.redraw()

    def reset_view(self):
        self._offset = [500, 350]
        self._scale = 24.0
        self._yaw = 45.0
        self._pitch = 35.26
        self._update_angle_label()
        self.redraw()

    def _set_view(self, yaw, pitch):
        """Snap to a preset orientation."""
        self._yaw = yaw % 360
        self._pitch = max(-89, min(89, pitch))
        self._update_angle_label()
        self.fit_view()


# -----------------------------------------------------------------------------
# ZONE MOVER — drag-and-drop zone-to-chapter pipeline
# -----------------------------------------------------------------------------
#
# Three classes, plus the wire-up in ZoneTab._dnd_*:
#
#   ZoneMover            Encapsulates the move pipeline:
#                          1. snapshot-backup all 4 modified DTs
#                          2. pre-flight (Z-bleed, AdditionalChapters drift,
#                             count of LayoutConnection refs, BP.Z list)
#                          3. show ZoneMoveDialog if conflicts -> A/B/C choice
#                          4. apply changes (Chapter, Position.Z, landmark
#                             BasePosition.Z, nested Subcell.Z, AdditionalChapters,
#                             PreferredZOverride) and sync NameMap counters
#                          5. run BuildValidator + deep-Z audit
#                          6. return a ZoneMoveResult
#
#   ZoneMoveDialog       Modal A/B/C choice dialog (block / expand / shrink)
#
#   ZoneMoveResultDialog Post-move summary popup with roll-back button
#
# These are PURE Tk + stdlib. They reuse the existing fp/find_prop helpers
# defined further up in this file.
# -----------------------------------------------------------------------------


class ZoneMoveResult:
    """Result object returned from ZoneMover.move().

    Attributes:
        zone_name           The zone we moved
        src_chapter         Chapter we came from
        dest_chapter        Chapter we moved to
        choice              'A' (block, never returns this), 'B' (expand),
                            'C' (shrink), or 'NONE' (no conflict)
        snapshot_paths      dict: doc_key -> Path to *.before_zonemove_*.json
        change_log          list[str] of human-readable mutations applied
        validator_errors    list[Issue] severity=='error' from BuildValidator
        validator_warnings  list[Issue] severity=='warning'
        deep_audit_lines    list[str] from the deep-Z audit (mirrors _deep_verify)
        cancelled           True if user cancelled before any change applied
    """

    def __init__(self, zone_name, src_chapter, dest_chapter):
        self.zone_name = zone_name
        self.src_chapter = src_chapter
        self.dest_chapter = dest_chapter
        self.choice = 'NONE'
        self.snapshot_paths = {}
        self.change_log = []
        self.validator_errors = []
        self.validator_warnings = []
        self.deep_audit_lines = []
        self.cancelled = False


class ZoneMover:
    """Encapsulates the drag-drop "move zone to chapter" pipeline.

    Public API: ``move(zone_name, dest_chapter_name, parent=None)``
    Returns a ZoneMoveResult, or None if the user cancelled in the conflict
    dialog before any change was applied.

    The mover only mutates ``app.docs``. Files are NOT written to disk —
    the user still has to hit Save All to persist changes (consistent with
    every other editor action). The snapshot backups ARE written to disk,
    however, so a roll-back can recover even if the user immediately saves.
    """

    # Doc keys we may snapshot/mutate during a move.
    AFFECTED_DOCS = ('zones', 'chapters', 'landmarks', 'connections')

    def __init__(self, app):
        self.app = app

    # ---------- helpers (mirror module-level fp/gv from _deep_verify) ----------
    @staticmethod
    def _fp(values, name):
        for p in values or []:
            if isinstance(p, dict) and p.get('Name') == name:
                return p
        return None

    @staticmethod
    def _intvec(prop):
        v = prop.get('Value') if prop else None
        if isinstance(v, list) and v:
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if isinstance(d, dict):
                return (d.get('X', 0), d.get('Y', 0), d.get('Z', 0))
        return None

    @staticmethod
    def _set_intvec_z(prop, new_z):
        """Mutate the IntVector struct's Z component in place. Returns True
        if mutation succeeded."""
        v = prop.get('Value') if prop else None
        if isinstance(v, list) and v:
            d = v[0].get('Value') if isinstance(v[0], dict) else None
            if isinstance(d, dict):
                d['Z'] = int(new_z)
                return True
        return False

    @staticmethod
    def _zstate(row):
        p = ZoneMover._fp(row.get('Value', []), 'EnabledState')
        return str(p.get('Value', '')).split('::')[-1] if p else ''

    @staticmethod
    def _rowname_field(row, field):
        """Read RowName from a structhandle field (Chapter, OriginLandmark…)"""
        p = ZoneMover._fp(row.get('Value', []), field)
        if not p:
            return None
        v = p.get('Value')
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and it.get('Name') == 'RowName':
                    return it.get('Value', '')
        return v

    @staticmethod
    def _scalar(row, field):
        p = ZoneMover._fp(row.get('Value', []), field)
        if not p:
            return None
        return p.get('Value')

    # ---------- public API ----------
    def move(self, zone_name, dest_chapter_name, parent=None):
        """Run the full pipeline. Returns a ZoneMoveResult or None on cancel."""
        zones_doc = self.app.docs.get('zones')
        chap_doc = self.app.docs.get('chapters')
        lm_doc = self.app.docs.get('landmarks')
        conn_doc = self.app.docs.get('connections')
        if not (zones_doc and chap_doc):
            raise RuntimeError('zones/chapters DT not loaded')

        zone_row = next((r for r in zones_doc.rows if r.get('Name') == zone_name), None)
        if zone_row is None:
            raise RuntimeError(f'Zone {zone_name!r} not in DT_Moria_Zones')
        dest_chap_row = next((r for r in chap_doc.rows
                              if r.get('Name') == dest_chapter_name), None)
        if dest_chap_row is None:
            raise RuntimeError(f'Chapter {dest_chapter_name!r} not in DT_Moria_Chapters')

        src_chapter = self._rowname_field(zone_row, 'Chapter') or ''
        result = ZoneMoveResult(zone_name, src_chapter, dest_chapter_name)

        # Pre-flight ----------------------------------------------------------
        preflight = self._preflight(zone_row, dest_chap_row, lm_doc, conn_doc, chap_doc)
        # If anomalies present, ask user
        choice = 'NONE'
        if preflight['has_conflict']:
            dlg = ZoneMoveDialog(parent or self.app, preflight)
            choice = dlg.result  # 'A', 'B', 'C', or None
            if choice is None or choice == 'A':
                result.cancelled = True
                return None if choice is None else result
        result.choice = choice

        # Snapshot backup -----------------------------------------------------
        result.snapshot_paths = self._snapshot(zone_name, dest_chapter_name)

        # Apply changes -------------------------------------------------------
        self._apply(zone_row, dest_chap_row, preflight, choice,
                    chap_doc, lm_doc, conn_doc, result.change_log)

        # Sync NameMap counters in every modified doc
        for k in self.AFFECTED_DOCS:
            d = self.app.docs.get(k)
            if d is not None:
                d.reconcile_namemap()

        # Validate ------------------------------------------------------------
        try:
            issues = BuildValidator(self.app.docs).run()
            result.validator_errors = [i for i in issues if i.severity == 'error']
            result.validator_warnings = [i for i in issues if i.severity == 'warning']
        except Exception as e:
            result.deep_audit_lines.append(f'(validator crashed: {e})')
        result.deep_audit_lines.extend(self._deep_audit(preflight['ss_cells_after']))

        return result

    # ---------- pre-flight ----------
    def _preflight(self, zone_row, dest_chap_row, lm_doc, conn_doc, chap_doc):
        """Compute everything we need to make conflict-resolution decisions."""
        zv = ZoneView(zone_row)
        dest_min = int(self._scalar(dest_chap_row, 'MinZ') or 0)
        dest_max = int(self._scalar(dest_chap_row, 'MaxZ') or 0)
        dest_prime = int(self._scalar(dest_chap_row, 'PrimeZ') or dest_min)
        dest_layer = int(self._scalar(dest_chap_row, 'Layer') or 0)
        src_chap = zv.chapter
        src_chap_row = next((r for r in chap_doc.rows
                             if r.get('Name') == src_chap), None)
        src_min = src_max = src_prime = None
        if src_chap_row is not None:
            src_min = int(self._scalar(src_chap_row, 'MinZ') or 0)
            src_max = int(self._scalar(src_chap_row, 'MaxZ') or 0)
            src_prime = int(self._scalar(src_chap_row, 'PrimeZ') or 0)

        pos = zv.position or (0, 0, 0)
        size = zv.target_size or (1, 1, 1)
        size_z = int(size[2] or 1)

        # Z-bleed: new top = dest_prime + size_z - 1; >= dest_max?
        new_top = dest_prime + size_z - 1
        z_bleed = max(0, new_top - dest_max)

        # bPositionFromLandmarks
        pfl_p = self._fp(zone_row.get('Value', []), 'bPositionFromLandmarks')
        from_landmarks = bool(pfl_p.get('Value')) if pfl_p else False

        # AdditionalChapters drift detection — list those that are no longer
        # adjacent to the destination chapter (heuristic: anything that
        # references the OLD chapter or its immediate siblings is suspect).
        addl = list(zv.additional_chapters or [])
        # Anything that == source chapter or starts with the same Layer/numeric
        # neighbour is flagged. Simplest rule: flag any addl whose name doesn't
        # match dest_chapter and isn't already in dest_chap's natural neighbours.
        # We don't have the neighbour-detection logic here; just flag every
        # entry so the user can decide.
        addl_drift = [c for c in addl if c and c != dest_chap_row.get('Name')]

        # PreferredZOverride
        pzo_p = self._fp(zone_row.get('Value', []), 'PreferredZOverride')
        pzo_val = None
        if pzo_p:
            v = pzo_p.get('Value')
            if isinstance(v, int):
                pzo_val = v

        # Landmarks hosted by this zone, with their current BP.Z
        lh_entries = self._zone_landmark_handles(zone_row)
        lm_bps = []  # [(landmark_name, (x,y,z))]
        if lm_doc:
            for ln in lh_entries:
                if not ln or ln == 'None':
                    continue
                lm_row = next((r for r in lm_doc.rows if r.get('Name') == ln), None)
                if lm_row is None:
                    continue
                bp = self._fp(lm_row.get('Value', []), 'BasePosition')
                bp_xyz = self._intvec(bp) if bp else None
                lm_bps.append((ln, bp_xyz))

        # LayoutConnections referencing any of these landmarks
        conn_refs = []  # [(conn_row_name, field, landmark)]
        if conn_doc and lh_entries:
            lh_set = set(lh_entries)
            for r in conn_doc.rows:
                for fld in ('OriginLandmark', 'DestinationLandmark'):
                    ln = self._rowname_field(r, fld)
                    if ln and ln in lh_set:
                        conn_refs.append((r.get('Name', '?'), fld, ln))

        # Cascade-shift planning for option B: every chapter with Layer >
        # dest_layer needs MinZ/MaxZ/PrimeZ shifted UP by z_bleed cells.
        cascade_chapters = []
        if z_bleed > 0:
            for r in chap_doc.rows:
                if r is dest_chap_row:
                    continue
                L = self._scalar(r, 'Layer')
                if not isinstance(L, int):
                    continue
                if L > dest_layer:
                    cascade_chapters.append(r.get('Name', '?'))

        # ss_cells_after: live SS chapter Z bands assuming option B is applied
        ss_cells_after = self._ss_cells_after(chap_doc, dest_chap_row, z_bleed,
                                              cascade_chapters)

        has_conflict = bool(z_bleed) or bool(addl_drift)
        return {
            'has_conflict': has_conflict,
            'zone_name': zone_row.get('Name'),
            'src_chapter': src_chap,
            'src_min_z': src_min, 'src_max_z': src_max, 'src_prime_z': src_prime,
            'dest_chapter': dest_chap_row.get('Name'),
            'dest_min_z': dest_min, 'dest_max_z': dest_max,
            'dest_prime_z': dest_prime, 'dest_layer': dest_layer,
            'pos': pos, 'size': size, 'size_z': size_z,
            'z_bleed': z_bleed,
            'from_landmarks': from_landmarks,
            'addl_drift': addl_drift,
            'lm_bps': lm_bps,
            'conn_refs': conn_refs,
            'cascade_chapters': cascade_chapters,
            'pzo_val': pzo_val,
            'ss_cells_after': ss_cells_after,
        }

    @staticmethod
    def _zone_landmark_handles(zone_row):
        """Return list of landmark RowNames this zone hosts via LandmarkHandles."""
        out = []
        lh = ZoneMover._fp(zone_row.get('Value', []), 'LandmarkHandles')
        if not lh:
            return out
        for entry in (lh.get('Value') or []):
            if not isinstance(entry, dict):
                continue
            inner = entry.get('Value')
            if not isinstance(inner, list):
                continue
            lhprop = ZoneMover._fp(inner, 'Landmark')
            if not lhprop:
                continue
            lv = lhprop.get('Value')
            if isinstance(lv, list):
                for it in lv:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        nm = it.get('Value', '')
                        if nm and nm != 'None':
                            out.append(nm)
        return out

    @staticmethod
    def _ss_cells_after(chap_doc, dest_chap_row, z_bleed, cascade_names):
        """Compute the set of SS Z cells assuming option B's expand+cascade
        shift would apply. Used by the deep audit AFTER changes are applied
        (so it always reflects the post-state)."""
        cells = set()
        for r in chap_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-'):
                continue
            mn = ZoneMover._scalar(r, 'MinZ')
            mx = ZoneMover._scalar(r, 'MaxZ')
            if isinstance(mn, int) and isinstance(mx, int):
                for cz in range(mn, mx + 1):
                    cells.add(cz)
        return cells

    # ---------- snapshot ----------
    def _snapshot(self, zone_name, dest_chapter):
        """Write *.before_zonemove_<zone>_<dest>_<ts>.json sidecars next to
        each modified DT. The pattern matches the gitignore rule
        ``*.before_*.json`` so commits are clean."""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        # Sanitize for filesystem. Chapter rownames may contain dots / dashes.
        sane_zone = re.sub(r'[^A-Za-z0-9_.-]', '_', zone_name)
        sane_chap = re.sub(r'[^A-Za-z0-9_.-]', '_', dest_chapter)
        suffix = f'.before_zonemove_{sane_zone}_{sane_chap}_{ts}.json'
        paths = {}
        for k in self.AFFECTED_DOCS:
            d = self.app.docs.get(k)
            if d is None or d.data is None:
                continue
            sp = d.json_path.with_name(d.json_path.stem + suffix)
            try:
                with open(sp, 'w', encoding='utf-8') as f:
                    json.dump(d.data, f, indent=2)
                paths[k] = sp
            except Exception:
                pass
        return paths

    # ---------- apply ----------
    def _apply(self, zone_row, dest_chap_row, pf, choice,
               chap_doc, lm_doc, conn_doc, log):
        """Mutate docs in place. ``choice`` is 'B', 'C', or 'NONE'."""
        dest_name = dest_chap_row.get('Name')
        # 1. Zone Chapter rowname
        cp = self._fp(zone_row.get('Value', []), 'Chapter')
        if cp is not None:
            v = cp.get('Value')
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        old = it.get('Value', '')
                        it['Value'] = dest_name
                        log.append(f'Zone.Chapter: {old} -> {dest_name}')
            else:
                cp['Value'] = dest_name
                log.append(f'Zone.Chapter: -> {dest_name}')

        # 2. Option B cascade: expand dest MaxZ by z_bleed and shift higher chapters
        if choice == 'B' and pf['z_bleed'] > 0:
            shift = pf['z_bleed']
            # Expand dest MaxZ by shift
            mx_prop = self._fp(dest_chap_row.get('Value', []), 'MaxZ')
            if mx_prop is not None:
                mx_prop['Value'] = pf['dest_max_z'] + shift
                log.append(f'Chapter {dest_name}.MaxZ: '
                           f'{pf["dest_max_z"]} -> {pf["dest_max_z"] + shift}')
            # Cascade-shift everything with Layer > dest_layer
            for nm in pf['cascade_chapters']:
                row = next((r for r in chap_doc.rows if r.get('Name') == nm), None)
                if row is None:
                    continue
                for fld in ('MinZ', 'MaxZ', 'PrimeZ'):
                    p = self._fp(row.get('Value', []), fld)
                    if p is not None and isinstance(p.get('Value'), int):
                        p['Value'] = p['Value'] + shift
                log.append(f'Chapter {nm}: MinZ/MaxZ/PrimeZ +{shift}')

        # 3. Option C: shrink zone TargetSize.Z to fit
        if choice == 'C' and pf['z_bleed'] > 0:
            ts_prop = self._fp(zone_row.get('Value', []), 'TargetSize')
            if ts_prop is not None:
                new_sz = max(1, pf['size_z'] - pf['z_bleed'])
                v = ts_prop.get('Value')
                if isinstance(v, list) and v:
                    d = v[0].get('Value') if isinstance(v[0], dict) else None
                    if isinstance(d, dict):
                        d['Z'] = new_sz
                        log.append(f'Zone.TargetSize.Z: {pf["size_z"]} -> {new_sz}')

        # 4. Update Position.Z to dest PrimeZ (preserve X/Y)
        pos_prop = self._fp(zone_row.get('Value', []), 'Position')
        if pos_prop is not None:
            v = pos_prop.get('Value')
            if isinstance(v, list) and v:
                d = v[0].get('Value') if isinstance(v[0], dict) else None
                if isinstance(d, dict):
                    old_z = d.get('Z', 0)
                    # Use NEW dest_prime (after option B shift, dest_prime
                    # itself doesn't move; we expanded MaxZ above it).
                    d['Z'] = pf['dest_prime_z']
                    log.append(f'Zone.Position.Z: {old_z} -> {pf["dest_prime_z"]}')

        # 5. Update PreferredZOverride if present
        if pf['pzo_val'] is not None:
            pzo = self._fp(zone_row.get('Value', []), 'PreferredZOverride')
            if pzo is not None:
                pzo['Value'] = pf['dest_prime_z']
                log.append(f'Zone.PreferredZOverride: {pf["pzo_val"]} -> {pf["dest_prime_z"]}')

        # 6. Landmark BasePosition.Z update
        if lm_doc:
            for ln, bp_xyz in pf['lm_bps']:
                if not bp_xyz:
                    continue
                # Skip sentinel (0,0,*) landmarks — those are unset markers
                if bp_xyz[0] == 0 and bp_xyz[1] == 0:
                    log.append(f'Landmark {ln}.BasePosition: skipped (sentinel 0,0)')
                    continue
                lm_row = next((r for r in lm_doc.rows if r.get('Name') == ln), None)
                if lm_row is None:
                    continue
                bp = self._fp(lm_row.get('Value', []), 'BasePosition')
                if bp is None:
                    continue
                old_z = bp_xyz[2]
                if self._set_intvec_z(bp, pf['dest_prime_z']):
                    log.append(f'Landmark {ln}.BasePosition.Z: '
                               f'{old_z} -> {pf["dest_prime_z"]}')

        # 7. AdditionalChapters drift — clear flagged entries (safest default)
        if pf['addl_drift']:
            ac = self._fp(zone_row.get('Value', []), 'AdditionalChapters')
            if ac is not None:
                # Remove any entry whose RowName is in addl_drift
                vals = ac.get('Value') or []
                kept = []
                cleared = []
                for entry in vals:
                    if not isinstance(entry, dict):
                        kept.append(entry); continue
                    inner = entry.get('Value')
                    drop = False
                    if isinstance(inner, list):
                        for it in inner:
                            if (isinstance(it, dict)
                                    and it.get('Name') == 'RowName'
                                    and it.get('Value') in pf['addl_drift']):
                                drop = True
                                cleared.append(it.get('Value'))
                                break
                    if drop:
                        continue
                    kept.append(entry)
                if cleared:
                    ac['Value'] = kept
                    log.append(f'Zone.AdditionalChapters: cleared '
                               f'{len(cleared)} drifted entry(ies): {cleared}')

        # 8. LayoutConnections nested Subcell.Z update for each conn whose
        #    endpoint landmark is hosted by this zone.
        if conn_doc and pf['conn_refs']:
            touched = set()
            for cname, fld, _ln in pf['conn_refs']:
                touched.add(cname)
            for cname in touched:
                row = next((r for r in conn_doc.rows
                            if r.get('Name') == cname), None)
                if row is None:
                    continue
                for iface_field in ('OriginInterface', 'DestinationInterface'):
                    iface = self._fp(row.get('Value', []), iface_field)
                    if not iface:
                        continue
                    for inner in (iface.get('Value') or []):
                        if (isinstance(inner, dict)
                                and inner.get('Name') == 'Subcell'):
                            old = self._intvec(inner)
                            if self._set_intvec_z(inner, pf['dest_prime_z']):
                                log.append(
                                    f'LayoutConnection {cname}.'
                                    f'{iface_field}.Subcell.Z: '
                                    f'{old[2] if old else "?"} '
                                    f'-> {pf["dest_prime_z"]}')
                # Top-level Subcell on the row (rare in vanilla, but spec
                # says update it if present).
                top_sc = self._fp(row.get('Value', []), 'Subcell')
                if top_sc:
                    old = self._intvec(top_sc)
                    if self._set_intvec_z(top_sc, pf['dest_prime_z']):
                        log.append(f'LayoutConnection {cname}.Subcell.Z: '
                                   f'{old[2] if old else "?"} '
                                   f'-> {pf["dest_prime_z"]}')

    # ---------- deep audit (mirror experiments/.../_deep_verify.py) ----------
    def _deep_audit(self, ss_cells):
        """Lightweight in-process version of _deep_verify.py — surfaces the
        same checks the user would run from the command line. Returns a list
        of human-readable lines describing each finding."""
        out = []
        z_doc = self.app.docs.get('zones')
        ch_doc = self.app.docs.get('chapters')
        lm_doc = self.app.docs.get('landmarks')
        lc_doc = self.app.docs.get('connections')
        if not (z_doc and ch_doc):
            return out

        # Recompute SS cells from current state
        ss_cells = set()
        for r in ch_doc.rows:
            n = r.get('Name', '')
            if not n.startswith('SandboxSmall-'):
                continue
            if self._zstate(r) == 'Disabled':
                continue
            mn = self._scalar(r, 'MinZ'); mx = self._scalar(r, 'MaxZ')
            if isinstance(mn, int) and isinstance(mx, int):
                for cz in range(mn, mx + 1):
                    ss_cells.add(cz)

        # Zone Position.Z in cell?
        bad_zpos = []
        for r in z_doc.rows:
            zs = self._fp(r.get('Value', []), 'ZoneSet')
            if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                continue
            if self._zstate(r) == 'Disabled':
                continue
            pos = self._intvec(self._fp(r['Value'], 'Position'))
            if not pos or pos == (0, 0, 0):
                continue
            if pos[2] not in ss_cells:
                bad_zpos.append((r['Name'], pos))
        out.append(f'Zone Position.Z out of band: {len(bad_zpos)}')
        for n, p in bad_zpos[:5]:
            out.append(f'  {n} {p}')

        # Nested Subcell.Z in band?
        if lc_doc:
            bad_sc = []
            for r in lc_doc.rows:
                zs = self._fp(r.get('Value', []), 'ZoneSet')
                if not zs or str(zs.get('Value', '')).split('::')[-1] != 'SandboxSmall':
                    continue
                if self._zstate(r) == 'Disabled':
                    continue
                for fld in ('OriginInterface', 'DestinationInterface'):
                    prop = self._fp(r['Value'], fld)
                    if not prop:
                        continue
                    for inner in (prop.get('Value') or []):
                        if (isinstance(inner, dict)
                                and inner.get('Name') == 'Subcell'):
                            sc = self._intvec(inner)
                            if sc and sc[2] != 0 and sc[2] not in ss_cells:
                                bad_sc.append((r['Name'], fld, sc[2]))
            out.append(f'Nested Subcell.Z out of band: {len(bad_sc)}')
            for n, f, z in bad_sc[:5]:
                out.append(f'  {n}.{f} Z={z}')

        # NameMap counter sync
        for k in self.AFFECTED_DOCS:
            d = self.app.docs.get(k)
            if d is None or d.data is None:
                continue
            nm = len(d.data.get('NameMap', []))
            nrf = d.data.get('NamesReferencedFromExportDataCount')
            g = d.data.get('Generations') or []
            g_nc = g[0].get('NameCount') if g else None
            ok = (nrf == nm and (g_nc is None or g_nc == nm))
            out.append(f'{d.json_path.name}: NameMap={nm} NRef={nrf} '
                       f'Gen.NC={g_nc} {"OK" if ok else "MISMATCH"}')
        return out


class ZoneMoveDialog(tk.Toplevel):
    """Modal conflict-resolution dialog with three radio choices.

    Returns one of 'A' (block), 'B' (auto-expand+cascade), 'C' (auto-shrink),
    or None (cancelled). Read ``self.result`` after ``wait_window()``.
    """

    def __init__(self, parent, preflight):
        super().__init__(parent)
        self.title('Zone move — conflicts detected')
        self.transient(parent)
        self.grab_set()
        self.minsize(640, 500)
        self.result = None
        pf = preflight

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=f'Move zone "{pf["zone_name"]}" '
                  f'to {pf["dest_chapter"]}',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w')

        # Summary
        sf = ttk.LabelFrame(body, text='Move summary', padding=8)
        sf.pack(fill=tk.X, pady=(8, 4))
        rows = [
            f'Source chapter:  {pf["src_chapter"]}  '
            f'(MinZ={pf["src_min_z"]} MaxZ={pf["src_max_z"]} '
            f'PrimeZ={pf["src_prime_z"]})',
            f'Destination:     {pf["dest_chapter"]}  '
            f'(MinZ={pf["dest_min_z"]} MaxZ={pf["dest_max_z"]} '
            f'PrimeZ={pf["dest_prime_z"]})',
            f'Zone Position:   {pf["pos"]}  TargetSize: {pf["size"]}',
            f'bPositionFromLandmarks: {pf["from_landmarks"]}',
            f'Hosted landmarks: {len(pf["lm_bps"])}',
            f'Connection refs: {len(pf["conn_refs"])}',
        ]
        for r in rows:
            ttk.Label(sf, text=r, justify='left').pack(anchor='w')

        # Issues
        isf = ttk.LabelFrame(body, text='Issues detected', padding=8)
        isf.pack(fill=tk.X, pady=4)
        if pf['z_bleed'] > 0:
            ttk.Label(isf, justify='left', wraplength=560,
                      text=(f'Z-bleed: zone top would land at Z='
                            f'{pf["dest_prime_z"] + pf["size_z"] - 1}, '
                            f'destination MaxZ={pf["dest_max_z"]}. '
                            f'Bleed = {pf["z_bleed"]} cell(s).')
                      ).pack(anchor='w')
        if pf['addl_drift']:
            ttk.Label(isf, justify='left', wraplength=560,
                      text=(f'AdditionalChapters drift: '
                            f'{len(pf["addl_drift"])} entry(ies) point at '
                            f'old neighbours: {pf["addl_drift"]}')
                      ).pack(anchor='w')

        # Choices
        cf = ttk.LabelFrame(body, text='Choose how to proceed', padding=8)
        cf.pack(fill=tk.X, pady=(8, 4))
        self.v_choice = tk.StringVar(value='B' if pf['z_bleed'] > 0 else 'B')
        ttk.Radiobutton(cf, variable=self.v_choice, value='A',
                        text='A — Block the move (cancel everything)'
                        ).pack(anchor='w')
        b_text = (f'B — Auto-expand destination chapter MaxZ by '
                  f'{pf["z_bleed"]} and cascade-shift '
                  f'{len(pf["cascade_chapters"])} higher chapter(s) up')
        rb_b = ttk.Radiobutton(cf, variable=self.v_choice, value='B',
                               text=b_text)
        rb_b.pack(anchor='w')
        if pf['z_bleed'] <= 0:
            rb_b.state(['disabled'])
        c_text = (f'C — Auto-shrink zone TargetSize.Z from {pf["size_z"]} '
                  f'to {max(1, pf["size_z"] - pf["z_bleed"])}')
        rb_c = ttk.Radiobutton(cf, variable=self.v_choice, value='C',
                               text=c_text)
        rb_c.pack(anchor='w')
        if pf['z_bleed'] <= 0:
            rb_c.state(['disabled'])

        # Buttons
        btns = ttk.Frame(body); btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text='Cancel',
                   command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text='Apply', style='Accent.TButton',
                   command=self._apply).pack(side=tk.RIGHT, padx=(0, 6))

        self.protocol('WM_DELETE_WINDOW', self._cancel)
        self.wait_window()

    def _apply(self):
        self.result = self.v_choice.get()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class ZoneMoveResultDialog(tk.Toplevel):
    """Post-move summary popup. Shows the change-log + validator results +
    a roll-back button (which restores the .before_zonemove_* sidecars)."""

    def __init__(self, parent, app, result):
        super().__init__(parent)
        self.app = app
        self.result = result
        dest_label = result.dest_chapter
        self.title(f'Zone moved: {result.zone_name} -> {dest_label}')
        self.transient(parent)
        self.grab_set()
        self.minsize(720, 540)

        body = ttk.Frame(self, padding=10)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body,
                  text=f'Zone "{result.zone_name}" moved '
                       f'{result.src_chapter or "(none)"}'
                       f' -> {result.dest_chapter}',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w')

        # Summary tabs (Notebook)
        nb = ttk.Notebook(body); nb.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Changes tab
        cf = ttk.Frame(nb)
        nb.add(cf, text=f'Changes ({len(result.change_log)})')
        cl_text = tk.Text(cf, wrap='word', height=14)
        cl_text.pack(fill=tk.BOTH, expand=True)
        cl_text.insert('1.0', '\n'.join(result.change_log)
                       if result.change_log else '(no changes recorded)')
        cl_text.config(state='disabled')

        # Validation tab
        vf = ttk.Frame(nb)
        nb.add(vf, text=f'Validation ({len(result.validator_errors)} errors / '
                        f'{len(result.validator_warnings)} warnings)')
        v_text = tk.Text(vf, wrap='word', height=14)
        v_text.pack(fill=tk.BOTH, expand=True)
        lines = []
        if result.validator_errors:
            lines.append('=== ERRORS ===')
            for it in result.validator_errors:
                lines.append(f'[error] {it.check}: {it.detail}')
        if result.validator_warnings:
            lines.append('')
            lines.append('=== WARNINGS ===')
            for it in result.validator_warnings[:50]:
                lines.append(f'[warn]  {it.check}: {it.detail}')
        if not result.validator_errors and not result.validator_warnings:
            lines.append('No errors or warnings.')
        v_text.insert('1.0', '\n'.join(lines))
        v_text.config(state='disabled')

        # Deep audit tab
        df = ttk.Frame(nb)
        nb.add(df, text='Deep audit')
        d_text = tk.Text(df, wrap='word', height=14)
        d_text.pack(fill=tk.BOTH, expand=True)
        d_text.insert('1.0', '\n'.join(result.deep_audit_lines)
                       if result.deep_audit_lines else '(no audit output)')
        d_text.config(state='disabled')

        # Snapshot listing tab
        sf = ttk.Frame(nb)
        nb.add(sf, text=f'Snapshots ({len(result.snapshot_paths)})')
        s_text = tk.Text(sf, wrap='word', height=8)
        s_text.pack(fill=tk.BOTH, expand=True)
        slines = []
        for k, p in result.snapshot_paths.items():
            slines.append(f'{k}: {p}')
        s_text.insert('1.0', '\n'.join(slines)
                      if slines else '(no snapshots written)')
        s_text.config(state='disabled')

        # Buttons
        bf = ttk.Frame(body); bf.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(bf, text='Roll back',
                   command=self._rollback).pack(side=tk.LEFT)
        ttk.Button(bf, text='OK', style='Accent.TButton',
                   command=self.destroy).pack(side=tk.RIGHT)

    def _rollback(self):
        if not self.result.snapshot_paths:
            messagebox.showerror('Roll back',
                                 'No snapshots available to restore.',
                                 parent=self)
            return
        if not messagebox.askyesno('Roll back',
                                   'Restore all 4 DataTables from the '
                                   '.before_zonemove_* snapshots?',
                                   parent=self):
            return
        restored = []
        failed = []
        for k, sp in self.result.snapshot_paths.items():
            d = self.app.docs.get(k)
            if d is None or not Path(sp).exists():
                failed.append(k); continue
            try:
                with open(sp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                d.data = data
                exports = data.get('Exports', [])
                table = exports[0].get('Table', {}) if exports else {}
                if 'Data' in table:
                    d.rows = table['Data']
                else:
                    d.rows = table.get('Value', []) or []
                restored.append(k)
            except Exception as e:
                failed.append(f'{k}: {e}')
        # Refresh all tabs after roll-back
        try:
            self.app.load_all_after_zone_move()
        except Exception:
            pass
        msg = f'Restored: {restored}'
        if failed:
            msg += f'\nFailed: {failed}'
        messagebox.showinfo('Roll back', msg, parent=self)
        self.destroy()


# -----------------------------------------------------------------------------
# ZONE MOVE — chapter picker (right-click flow)
# -----------------------------------------------------------------------------
#
# Modal Toplevel that replaces the drag-drop trigger. Listing all valid
# destination chapters in Layer-descending order (Lv-7 at top, D-7 at
# bottom) makes off-screen targets a non-issue. The user picks a chapter
# and the host caller invokes ZoneMover.move() exactly the way the
# drag-drop release handler did.
#
# Columns mirror the level-list skill output:
#   Lv-N | chapter-row-name | DisplayName key | PrimeZ | live zone count
#
# The source chapter is filtered out (can't move a zone to itself).
# -----------------------------------------------------------------------------


class ZoneMoveChapterPicker(tk.Toplevel):
    """Modal chapter-picker for the right-click "Move to chapter…" flow.

    Public API:
        picker = ZoneMoveChapterPicker(parent, app, zone_view)
        parent.wait_window(picker)
        if picker.result:  # chapter row name, or None on cancel
            ZoneMover(app).move(zone_view.name, picker.result, parent=...)
    """

    def __init__(self, parent, app, zone_view):
        super().__init__(parent)
        self.app = app
        self.zone = zone_view
        self.result = None
        self._chapter_names = []  # parallel list to listbox rows

        self.title(f'Move zone: {zone_view.name}')
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass
        self.resizable(True, True)
        self.minsize(640, 420)
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self.bind('<Escape>', lambda _e: self._on_cancel())
        self.bind('<Return>', lambda _e: self._on_ok())

        self._build_ui()
        self._populate_chapters()
        # Center on parent
        try:
            self.update_idletasks()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width() or 1200
            ph = parent.winfo_height() or 800
            w = self.winfo_width()
            h = self.winfo_height()
            self.geometry(f'+{px + (pw - w) // 2}+{py + (ph - h) // 2}')
        except Exception:
            pass

    # ---------- helpers ----------
    @staticmethod
    def _layer_label(layer):
        """Format a Layer int the same way the level-list skill does:
        Lv-N for non-negative (0->Lv-1, 1->Lv-2, ...), D-N for negative."""
        if layer is None:
            return '?'
        try:
            L = int(layer)
        except (TypeError, ValueError):
            return '?'
        if L >= 0:
            return f'Lv-{L + 1}'
        return f'D-{-L}'

    def _chapter_layer(self, chapter_name):
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return None
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name:
                p = find_prop(r.get('Value', []), 'Layer')
                if p is None:
                    return None
                v = p.get('Value')
                if v is None:
                    return None
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return None
        return None

    def _chapter_field(self, chapter_name, field_name):
        """Return the scalar value of a chapter field, or None."""
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return None
        for r in chap_doc.rows:
            if r.get('Name') == chapter_name:
                p = find_prop(r.get('Value', []), field_name)
                if p is None:
                    return None
                return p.get('Value')
        return None

    def _live_zone_count(self, chapter_name):
        """Count Live zones whose Chapter rowname == chapter_name."""
        zones_doc = self.app.docs.get('zones')
        if not zones_doc:
            return 0
        n = 0
        for r in zones_doc.rows:
            ch_p = find_prop(r.get('Value', []), 'Chapter')
            ch = get_rowname(ch_p) if ch_p is not None else ''
            if ch != chapter_name:
                continue
            es_p = find_prop(r.get('Value', []), 'EnabledState')
            es = get_enum(es_p) if es_p is not None else 'Live'
            if es == 'Live':
                n += 1
        return n

    # ---------- UI ----------
    def _build_ui(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        # --- Top: read-only summary -------------------------------------
        summary = ttk.LabelFrame(outer, text='Zone', padding=8)
        summary.pack(fill=tk.X, pady=(0, 8))

        z = self.zone
        src_chapter = z.chapter or '(none)'
        src_layer = self._chapter_layer(src_chapter) if z.chapter else None
        src_label = (f'{src_chapter}  ({self._layer_label(src_layer)})'
                     if z.chapter else src_chapter)
        pos = z.position or (0, 0, 0)
        size = z.target_size or (0, 0, 0)

        rows = [
            ('Zone:', z.name),
            ('Current chapter:', src_label),
            ('Current Position:', f'X={pos[0]}, Y={pos[1]}, Z={pos[2]}'),
            ('Current TargetSize:', f'X={size[0]}, Y={size[1]}, Z={size[2]}'),
        ]
        for i, (k, v) in enumerate(rows):
            ttk.Label(summary, text=k, width=18).grid(
                row=i, column=0, sticky='w', pady=1)
            ttk.Label(summary, text=str(v),
                      font=('Segoe UI', 9, 'bold')).grid(
                row=i, column=1, sticky='w', pady=1)

        # --- Middle: scrollable list of destination chapters ------------
        mid = ttk.LabelFrame(outer, text='Destination chapter', padding=4)
        mid.pack(fill=tk.BOTH, expand=True)

        cols = ('layer', 'chapter', 'display', 'primez', 'count')
        tv = ttk.Treeview(mid, columns=cols, show='headings',
                          selectmode='browse', height=14)
        tv.heading('layer', text='Layer')
        tv.heading('chapter', text='Chapter row name')
        tv.heading('display', text='DisplayName')
        tv.heading('primez', text='PrimeZ')
        tv.heading('count', text='Live zones')
        tv.column('layer', width=70, stretch=False, anchor='w')
        tv.column('chapter', width=210, stretch=False, anchor='w')
        tv.column('display', width=240, stretch=True, anchor='w')
        tv.column('primez', width=70, stretch=False, anchor='center')
        tv.column('count', width=80, stretch=False, anchor='center')

        vsb = ttk.Scrollbar(mid, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        tv.bind('<Double-1>', lambda _e: self._on_ok())
        self.tree = tv

        # --- Bottom: OK / Cancel ---------------------------------------
        btns = ttk.Frame(outer)
        btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btns, text='Cancel',
                   command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(btns, text='OK',
                   command=self._on_ok).pack(side=tk.RIGHT, padx=(0, 6))

    def _populate_chapters(self):
        chap_doc = self.app.docs.get('chapters')
        if not chap_doc:
            return
        src_chapter = self.zone.chapter or ''
        rows = []
        for r in chap_doc.rows:
            cn = r.get('Name')
            if not cn or cn == src_chapter:
                continue
            layer = self._chapter_layer(cn)
            display = self._chapter_field(cn, 'DisplayName') or ''
            primez = self._chapter_field(cn, 'PrimeZ')
            primez_s = '' if primez is None else str(primez)
            count = self._live_zone_count(cn)
            rows.append((layer, cn, display, primez_s, count))

        # Sort by Layer descending; unknowns last; then natural row name
        def sort_key(row):
            layer, cn, *_ = row
            return (
                0 if layer is not None else 1,
                -(layer if layer is not None else 0),
                natural_key(cn),
            )

        rows.sort(key=sort_key)

        self._chapter_names = []
        for layer, cn, display, primez_s, count in rows:
            iid = f'chap-{len(self._chapter_names)}'
            self.tree.insert('', 'end', iid=iid,
                             values=(self._layer_label(layer),
                                     cn, display, primez_s, count))
            self._chapter_names.append(cn)

        if self._chapter_names:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.tree.see(first)

    # ---------- actions ----------
    def _on_ok(self):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0].split('-', 1)[1])
        except (ValueError, IndexError):
            return
        if 0 <= idx < len(self._chapter_names):
            self.result = self._chapter_names[idx]
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


# -----------------------------------------------------------------------------
# MAIN APPLICATION
# -----------------------------------------------------------------------------

class WorldGenApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Moria WorldGen Editor — {MOD_NAME} v{SETTINGS.get_mod_version()}')
        self.geometry('1600x1000')
        self.minsize(1200, 700)
        self._apply_styles()
        self.docs = {
            k: DataTableDoc(k, WGR_DIR / fname, stem, label)
            for k, (fname, stem, label) in DATATABLES.items()
        }
        self.last_build = 'never'
        self._build_menu()
        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()
        self.after(60, self.load_all)

    def _apply_styles(self):
        """Consistent, calm, card-oriented look across all tabs."""
        style = ttk.Style(self)
        # Use 'clam' — cleanest on Windows, allows full theming
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        # Typography — Segoe UI across the board
        base_font = ('Segoe UI', 10)
        heading_font = ('Segoe UI', 10, 'bold')
        self.option_add('*Font', base_font)
        self.option_add('*TCombobox*Listbox.font', base_font)

        # Colors — quiet neutral surface with subtle accent
        surface = '#f3f4f7'
        border = '#d4d7dd'
        text = '#2b2e36'
        muted = '#6a6f78'
        accent = '#3a6dd1'
        accent_hover = '#2f5bb3'

        self.configure(background=surface)

        style.configure('.', background=surface, foreground=text,
                        fieldbackground='white', bordercolor=border,
                        focuscolor=accent, lightcolor=border, darkcolor=border)
        style.configure('TFrame', background=surface)
        style.configure('TLabel', background=surface, foreground=text)
        style.configure('TLabelframe', background=surface, borderwidth=1,
                        relief='solid', bordercolor=border)
        style.configure('TLabelframe.Label', background=surface, foreground=text,
                        font=heading_font)

        # Buttons — flat with accent hover
        style.configure('TButton', padding=(10, 4), relief='flat',
                        background='#ffffff', foreground=text, borderwidth=1)
        style.map('TButton',
                  background=[('active', '#eef2fa'), ('pressed', '#e1e8f5')],
                  bordercolor=[('active', accent), ('focus', accent)])

        # Primary action button (accent-filled)
        style.configure('Accent.TButton', padding=(14, 6), relief='flat',
                        background=accent, foreground='white', borderwidth=0)
        style.map('Accent.TButton',
                  background=[('active', accent_hover), ('pressed', accent_hover)])

        # Treeview — roomier rows + clean stripes
        style.configure('Treeview', rowheight=26, borderwidth=0,
                        background='white', fieldbackground='white', foreground=text)
        style.configure('Treeview.Heading', font=heading_font, padding=(6, 6),
                        background='#eef1f6', foreground=text, relief='flat',
                        borderwidth=0)
        # Selection highlight: light grey so the row's chapter colour
        # background remains visible underneath.
        style.map('Treeview',
                  background=[('selected', '#d3d3d3')],
                  foreground=[('selected', text)])

        # Notebook — bigger tabs, easier to click
        style.configure('TNotebook', background=surface, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(18, 8), font=base_font,
                        background='#e4e7ee', foreground=muted)
        style.map('TNotebook.Tab',
                  background=[('selected', 'white'), ('active', '#edf0f5')],
                  foreground=[('selected', text), ('active', text)])

        # Entry / Combobox / Spinbox consistency
        for widget in ('TEntry', 'TCombobox', 'TSpinbox'):
            style.configure(widget, padding=4, bordercolor=border)

        # Checkbutton — minor padding so it doesn't touch labels
        style.configure('TCheckbutton', background=surface, padding=(4, 2))

        # Scrollbar less chunky
        style.configure('Vertical.TScrollbar', background=surface,
                        troughcolor=surface, borderwidth=0, arrowsize=12)

        # Save handles for tabs to consume if they want them
        self.FONT_HEADING = heading_font
        self.COLOR_MUTED = muted
        self.COLOR_ACCENT = accent

    def _build_menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label='Reload all JSON', command=self.load_all)
        fm.add_command(label='Save all', command=self.save_all, accelerator='Ctrl+S')
        fm.add_separator()
        fm.add_command(label='Exit', command=self.on_exit)
        m.add_cascade(label='File', menu=fm)

        bm = tk.Menu(m, tearoff=0)
        bm.add_command(label='Preview build manifest…',
                       command=self.preview_build_manifest)
        bm.add_separator()
        bm.add_command(label='Build Mod Pak…', command=self.build_pak)
        m.add_cascade(label='Build', menu=bm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label='About', command=self.show_about)
        m.add_cascade(label='Help', menu=hm)
        self.config(menu=m)
        self.bind_all('<Control-s>', lambda e: self.save_all())

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(12, 10))
        bar.pack(side=tk.TOP, fill=tk.X)
        title = ttk.Label(bar, text='Moria WorldGen Editor',
                          font=('Segoe UI', 13, 'bold'))
        title.pack(side=tk.LEFT)
        sub = ttk.Label(bar, text=f'  •  {WGR_DIR}',
                        foreground=self.COLOR_MUTED)
        sub.pack(side=tk.LEFT)
        ttk.Button(bar, text='Build Mod Pak', style='Accent.TButton',
                   command=self.build_pak).pack(side=tk.RIGHT)
        ttk.Button(bar, text='Save all',
                   command=self.save_all).pack(side=tk.RIGHT, padx=8)
        ttk.Button(bar, text='Reload',
                   command=self.load_all).pack(side=tk.RIGHT)
        ttk.Separator(self, orient='horizontal').pack(fill=tk.X)

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        self.zone_tab = ZoneTab(self.nb, self)
        self.chapter_tab = ChapterTab(self.nb, self)
        self.biome_tab = BiomeTab(self.nb, self)
        self.bubble_tab = BubbleTab(self.nb, self)
        self.filter_tab = FilterTab(self.nb, self)
        self.landmark_tab = LandmarkTab(self.nb, self)
        self.strings_tab = StringsTab(self.nb, self)
        self.mappings_tab = MappingsTab(self.nb, self)
        self.history_tab = HistoryTab(self.nb, self)
        self.connections_tab = LayoutConnectionsTab(self.nb, self)
        self.levels_tab = LevelsTab(self.nb, self)
        self.map_tab = MapTab(self.nb, self)

        # Tab order (left → right):
        # Map, Zones, Landmarks, Bubbles, Connections, Chapters, Levels,
        # Filters, Strings, Mappings, Biomes, History
        # (Biomes parked left of History per "forgotten → left of History" rule.)
        self.nb.add(self.map_tab, text='  Map  ')
        self.nb.add(self.zone_tab, text='  Zones  ')
        self.nb.add(self.landmark_tab, text='  Landmarks  ')
        self.nb.add(self.bubble_tab, text='  Bubbles  ')
        self.nb.add(self.connections_tab, text='  Connections  ')
        self.nb.add(self.chapter_tab, text='  Chapters  ')
        self.nb.add(self.levels_tab, text='  Levels  ')
        self.nb.add(self.filter_tab, text='  Filters  ')
        self.nb.add(self.strings_tab, text='  Strings  ')
        self.nb.add(self.mappings_tab, text='  Mappings  ')
        self.nb.add(self.biome_tab, text='  Biomes  ')
        self.nb.add(self.history_tab, text='  History  ')

        # Redraw map when switching to it
        self.nb.bind('<<NotebookTabChanged>>', self._on_tab_change)

    def _on_tab_change(self, _):
        sel = self.nb.select()
        if sel == str(self.map_tab):
            self.map_tab.redraw()
        elif sel == str(self.history_tab):
            self.history_tab._populate()
        elif sel == str(self.levels_tab):
            self.levels_tab._populate()
        elif sel == str(self.connections_tab):
            self.connections_tab._populate_tree()

    def _build_statusbar(self):
        ttk.Separator(self, orient='horizontal').pack(side=tk.BOTTOM, fill=tk.X)
        self.status = ttk.Frame(self, padding=(12, 6))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.v_status = tk.StringVar(value='Loading…')
        ttk.Label(self.status, textvariable=self.v_status,
                  foreground=self.COLOR_MUTED).pack(side=tk.LEFT)

    def refresh_status(self):
        dirty = [doc.label for doc in self.docs.values() if doc.is_dirty()]
        txt = 'unsaved: ' + ', '.join(dirty) if dirty else 'no unsaved changes'
        self.v_status.set(f'{txt} | last build: {self.last_build}')

    def load_all_after_zone_move(self):
        """Refresh every tab from the in-memory docs (no disk reload). Used
        by the drag-drop ZoneMover pipeline after a successful move (or
        roll-back) so all tabs see the new state."""
        for tab in (self.zone_tab, self.chapter_tab, self.biome_tab,
                    self.bubble_tab, self.filter_tab, self.landmark_tab,
                    self.strings_tab, self.mappings_tab, self.history_tab,
                    self.connections_tab, self.levels_tab, self.map_tab):
            try:
                tab.refresh_from_doc()
            except Exception:
                pass
        self.refresh_status()

    def load_all(self):
        missing = []
        for doc in self.docs.values():
            if not doc.load():
                missing.append(doc.json_path.name)
        if missing:
            messagebox.showwarning('Missing JSON',
                'Not found:\n  ' + '\n  '.join(missing)
                + f'\n\nExpected in: {WGR_DIR}\nDecompile with UAssetGUI tojson.')
        for tab in (self.zone_tab, self.chapter_tab, self.biome_tab,
                    self.bubble_tab, self.filter_tab, self.landmark_tab,
                    self.strings_tab, self.mappings_tab, self.history_tab,
                    self.connections_tab, self.levels_tab, self.map_tab):
            tab.refresh_from_doc()
        self.refresh_status()

    def save_all(self):
        saved = False
        for doc in self.docs.values():
            if doc.data is not None and doc.is_dirty():
                try:
                    doc.save(); saved = True
                except Exception as e:
                    messagebox.showerror('Save failed', f'{doc.json_path.name}: {e}')
                    return
        for tab in (self.zone_tab, self.chapter_tab, self.biome_tab,
                    self.bubble_tab, self.filter_tab, self.landmark_tab,
                    self.strings_tab, self.mappings_tab, self.history_tab,
                    self.connections_tab, self.levels_tab, self.map_tab):
            tab.refresh_from_doc()
        self.refresh_status()
        if saved:
            messagebox.showinfo('Saved', 'All modified JSONs saved.')
        else:
            messagebox.showinfo('Nothing to save', 'No unsaved changes.')

    # -------------------------------------------------------------------
    # Row rename + cross-reference fixup
    # -------------------------------------------------------------------
    # Renames a row across one or more loaded DataTable docs and updates
    # every engine-coupled reference to that row name in other docs.
    # Categories supported: 'zones', 'chapters', 'landmarks', 'connections'.
    # Tabs invoke this via thin _rename_*() wrappers.

    @staticmethod
    def _find_prop(values, name):
        """Local copy of the standard property lookup helper used by tabs."""
        for p in values or []:
            if isinstance(p, dict) and p.get('Name') == name:
                return p
        return None

    def _walk_rename_refs(self, category, old_name, new_name, apply=False):
        """Walk every loaded doc and update or count cross-references for
        a renamed row. Returns (refs_count, set_of_doc_keys_touched).
        When apply=False, just counts. When True, mutates in place.

        See category table in docstring above for what each category touches.
        """
        refs = 0
        touched = set()
        zones_doc = self.docs.get('zones')
        chap_doc = self.docs.get('chapters')
        lm_doc = self.docs.get('landmarks')
        conn_doc = self.docs.get('connections')

        def _set_rowname(prop, expected, replacement):
            """If a property's nested RowName == expected, replace it.
            Returns True if mutated."""
            if not prop:
                return False
            v = prop.get('Value')
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict) and it.get('Name') == 'RowName':
                        if it.get('Value') == expected:
                            if apply:
                                it['Value'] = replacement
                            return True
            return False

        if category == 'chapters':
            # (a) Zones: Chapter.RowName
            if zones_doc and zones_doc.data:
                hit = False
                for r in zones_doc.rows:
                    if not isinstance(r, dict):
                        continue
                    p = self._find_prop(r.get('Value', []), 'Chapter')
                    if _set_rowname(p, old_name, new_name):
                        refs += 1; hit = True
                if hit:
                    touched.add('zones')
            # (b) Chapters: AdditionalChapters[*].RowName
            if chap_doc and chap_doc.data:
                hit = False
                for r in chap_doc.rows:
                    if not isinstance(r, dict):
                        continue
                    ac = self._find_prop(r.get('Value', []), 'AdditionalChapters')
                    if not ac:
                        continue
                    for entry in (ac.get('Value') or []):
                        # Two shapes: handle struct (with RowName inside Value list)
                        # or a direct dict with RowName.
                        if isinstance(entry, dict):
                            inner = entry.get('Value')
                            if isinstance(inner, list):
                                for sub in inner:
                                    if (isinstance(sub, dict)
                                            and sub.get('Name') == 'RowName'
                                            and sub.get('Value') == old_name):
                                        if apply:
                                            sub['Value'] = new_name
                                        refs += 1; hit = True
                            elif (entry.get('Name') == 'RowName'
                                    and entry.get('Value') == old_name):
                                if apply:
                                    entry['Value'] = new_name
                                refs += 1; hit = True
                if hit:
                    touched.add('chapters')

        elif category == 'landmarks':
            old_tag = f'World.Landmark.{old_name}'
            new_tag = f'World.Landmark.{new_name}'
            # (a) Zones: LandmarkHandles[*].Landmark.RowName
            if zones_doc and zones_doc.data:
                hit = False
                for r in zones_doc.rows:
                    if not isinstance(r, dict):
                        continue
                    lh = self._find_prop(r.get('Value', []), 'LandmarkHandles')
                    if not lh:
                        continue
                    for entry in (lh.get('Value') or []):
                        if not isinstance(entry, dict):
                            continue
                        inner = entry.get('Value')
                        if not isinstance(inner, list):
                            continue
                        lp = self._find_prop(inner, 'Landmark')
                        if _set_rowname(lp, old_name, new_name):
                            refs += 1; hit = True
                if hit:
                    touched.add('zones')
            # (b) Connections: Origin/Destination Landmark RowName
            if conn_doc and conn_doc.data:
                hit = False
                for r in conn_doc.rows:
                    if not isinstance(r, dict):
                        continue
                    for fld in ('OriginLandmark', 'DestinationLandmark'):
                        p = self._find_prop(r.get('Value', []), fld)
                        if _set_rowname(p, old_name, new_name):
                            refs += 1; hit = True
                if hit:
                    touched.add('connections')
            # (c) Landmarks: other rows' GuaranteedConnections TagName
            # (d) The renamed landmark's OWN InternalId TagName + own
            #     GuaranteedConnections (in case it self-refers). Tag-walk
            #     handles both — we just don't skip the renamed row.
            if lm_doc and lm_doc.data:
                hit = False
                for r in lm_doc.rows:
                    if not isinstance(r, dict):
                        continue
                    # Walk every TagName-bearing property on the row
                    def _walk(obj):
                        nonlocal refs, hit
                        if isinstance(obj, dict):
                            if obj.get('Name') == 'TagName' and obj.get('Value') == old_tag:
                                if apply:
                                    obj['Value'] = new_tag
                                refs += 1; hit = True
                            for v in obj.values():
                                _walk(v)
                        elif isinstance(obj, list):
                            for it in obj:
                                _walk(it)
                    _walk(r.get('Value', []))
                if hit:
                    touched.add('landmarks')

        # zones / connections: leaf rows, no cross-refs
        return refs, touched

    def rename_row(self, category, old_name, parent=None):
        """Open a Toplevel rename dialog and, on OK, rename the row + every
        cross-reference, sync NameMaps, refresh tabs."""
        doc_key_for_cat = {'zones': 'zones', 'chapters': 'chapters',
                           'landmarks': 'landmarks', 'connections': 'connections'}
        doc_key = doc_key_for_cat.get(category)
        if doc_key is None:
            messagebox.showerror('Rename', f'Unknown category: {category}')
            return
        doc = self.docs.get(doc_key)
        if doc is None or doc.data is None:
            messagebox.showerror('Rename', f'{doc_key} doc not loaded.')
            return
        row = next((r for r in doc.rows
                    if isinstance(r, dict) and r.get('Name') == old_name), None)
        if row is None:
            messagebox.showerror('Rename', f'Row "{old_name}" not found.')
            return

        existing_names = {r.get('Name') for r in doc.rows
                          if isinstance(r, dict) and r.get('Name')}

        # ----- dialog -----
        dlg = tk.Toplevel(parent or self)
        dlg.title('Rename row')
        dlg.transient((parent or self).winfo_toplevel())
        dlg.grab_set()
        dlg.minsize(460, 200)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f'Rename {category} row',
                  font=('Segoe UI', 11, 'bold')).grid(
                      row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(body, text=f'Old name: {old_name}',
                  foreground='#555').grid(row=1, column=0, columnspan=2,
                                          sticky='w', pady=(6, 0))
        ttk.Label(body, text=f'Category: {category}',
                  foreground='#555').grid(row=2, column=0, columnspan=2,
                                          sticky='w')
        ttk.Label(body, text='New name:').grid(row=3, column=0, sticky='w',
                                                pady=(10, 2))
        v_new = tk.StringVar(value=old_name)
        entry = ttk.Entry(body, textvariable=v_new, width=44)
        entry.grid(row=3, column=1, sticky='we', pady=(10, 2))
        body.columnconfigure(1, weight=1)
        v_msg = tk.StringVar(value='')
        msg_lbl = ttk.Label(body, textvariable=v_msg, foreground='#555')
        msg_lbl.grid(row=4, column=0, columnspan=2, sticky='w', pady=(8, 0))

        btns = ttk.Frame(dlg, padding=(12, 0, 12, 12))
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        result = {'ok': False, 'new_name': None}
        ok_btn = ttk.Button(btns, text='OK', style='Accent.TButton')
        ok_btn.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text='Cancel',
                   command=dlg.destroy).pack(side=tk.RIGHT)

        def validate(*_):
            n = v_new.get().strip()
            if not n:
                v_msg.set('Name cannot be empty')
                ok_btn.state(['disabled']); return
            if n == old_name:
                v_msg.set('Enter a different name')
                ok_btn.state(['disabled']); return
            if n in existing_names:
                v_msg.set('A row with this name already exists')
                ok_btn.state(['disabled']); return
            refs, touched = self._walk_rename_refs(category, old_name, n,
                                                    apply=False)
            v_msg.set(f'Will update {refs} reference(s) across '
                      f'{len(touched) + 1} doc(s)')
            ok_btn.state(['!disabled'])
        v_new.trace_add('write', validate)

        def do_ok():
            n = v_new.get().strip()
            if not n or n == old_name or n in existing_names:
                return
            result['ok'] = True
            result['new_name'] = n
            dlg.destroy()
        ok_btn.configure(command=do_ok)
        dlg.bind('<Return>', lambda _e: do_ok())
        dlg.bind('<Escape>', lambda _e: dlg.destroy())
        entry.focus_set()
        entry.select_range(0, tk.END)
        validate()
        dlg.wait_window()

        if not result['ok']:
            return
        new_name = result['new_name']

        # ----- atomic apply -----
        # 1. Update the row's Name
        row['Name'] = new_name
        # 2. Self-references (e.g. landmarks' InternalId TagName referencing
        #    'World.Landmark.{old_name}') — handled by the landmark branch of
        #    _walk_rename_refs below (it scans the renamed row too).
        # 3. Cross-reference walk + apply
        refs, touched = self._walk_rename_refs(category, old_name, new_name,
                                                apply=True)
        # The primary doc is always touched (we changed the row Name)
        touched.add(doc_key)

        # 4. NameMap sync on every touched doc.
        # We append new tokens but do NOT remove old ones — leaving stale
        # entries is harmless and safer if any reference was missed.
        for k in touched:
            d = self.docs.get(k)
            if d is None or d.data is None:
                continue
            nm = d.data.setdefault('NameMap', [])
            present = set(nm)
            extras = [new_name]
            if k == 'landmarks' or category == 'landmarks':
                extras.append(f'World.Landmark.{new_name}')
            for tok in extras:
                if tok and tok not in present:
                    nm.append(tok); present.add(tok)
            n = len(nm)
            if d.data.get('NamesReferencedFromExportDataCount', 0) < n:
                d.data['NamesReferencedFromExportDataCount'] = n
            gens = d.data.get('Generations') or []
            if gens and isinstance(gens[0], dict):
                if gens[0].get('NameCount', 0) < n:
                    gens[0]['NameCount'] = n
            # Also let the doc's own reconciler add anything else that became
            # newly referenced (e.g. RowName tokens in nested struct values).
            try:
                d.reconcile_namemap()
            except Exception:
                pass

        # 5/6. Doc dirty marking + history happen automatically: is_dirty()
        # diffs against last-saved snapshot, and the History tab diffs against
        # the pristine sidecar — both pick up the rename without an explicit
        # event log.

        # 7. Refresh every tab.
        for tab in (getattr(self, 'zone_tab', None),
                    getattr(self, 'chapter_tab', None),
                    getattr(self, 'biome_tab', None),
                    getattr(self, 'bubble_tab', None),
                    getattr(self, 'filter_tab', None),
                    getattr(self, 'landmark_tab', None),
                    getattr(self, 'strings_tab', None),
                    getattr(self, 'mappings_tab', None),
                    getattr(self, 'history_tab', None),
                    getattr(self, 'connections_tab', None),
                    getattr(self, 'levels_tab', None),
                    getattr(self, 'map_tab', None)):
            if tab is not None:
                try:
                    tab.refresh_from_doc()
                except Exception:
                    pass
        self.refresh_status()

        # 8. Confirmation
        messagebox.showinfo(
            'Rename complete',
            f'Renamed "{old_name}" -> "{new_name}". '
            f'{refs} reference(s) updated across {len(touched)} doc(s).')

    def _show_validation_dialog(self, issues, fixable, unfixable_errors,
                                 has_errors, validator=None):
        """Dispatch to the new Toplevel dialog or the legacy messagebox.

        Returns 'fix' | 'skip' | 'cancel'. When `validator` is supplied
        and the new UI is enabled, auto-fixes are applied INSIDE the
        dialog (in place, with re-validation), so the caller does not
        need to call validator.auto_fix() again on a 'fix' result —
        instead the result is 'skip' (proceed) or 'cancel'.
        """
        if USE_NEW_VALIDATOR_UI and validator is not None:
            return self._show_validation_dialog_new(issues, validator)
        return self._show_validation_dialog_legacy(
            issues, fixable, unfixable_errors, has_errors)

    def _show_validation_dialog_new(self, issues, validator):
        """Custom Toplevel: scrollable list, per-issue checkboxes for
        auto-fix selection, Apply/Build/Cancel row. Re-validates in
        place when fixes are applied. Returns 'skip' | 'cancel'
        (fixes are already applied + saved before returning 'skip')."""
        dlg = _ValidationDialog(self, issues, validator)
        return dlg.result  # 'skip' or 'cancel'

    def _show_validation_dialog_legacy(self, issues, fixable,
                                        unfixable_errors, has_errors):
        """Legacy messagebox flow — kept behind USE_NEW_VALIDATOR_UI as
        a fallback. Returns 'fix' | 'skip' | 'cancel'.
        """
        # Compose plain-English summary
        n_err = sum(1 for i in issues if i.severity == 'error')
        n_warn = sum(1 for i in issues if i.severity == 'warning')
        lines = []
        title_bits = []
        if n_err: title_bits.append(f'{n_err} error(s)')
        if n_warn: title_bits.append(f'{n_warn} warning(s)')
        lines.append('Pre-build validation: ' + ', '.join(title_bits))
        lines.append('')

        errors_list = [i for i in issues if i.severity == 'error']
        warnings_list = [i for i in issues if i.severity == 'warning']

        if errors_list:
            lines.append(f'ERRORS ({len(errors_list)}):')
            for it in errors_list:
                lines.append(f'  [{it.check}] ({it.doc_key or "-"})')
                lines.append(f'    {it.detail}')
                if it.fixer:
                    lines.append(f'    Auto-fix: {it.fixer_label}')
                lines.append('')

        if warnings_list:
            lines.append(f'WARNINGS ({len(warnings_list)}):')
            for it in warnings_list:
                lines.append(f'  [{it.check}] ({it.doc_key or "-"})')
                lines.append(f'    {it.detail}')
                if it.fixer:
                    lines.append(f'    Auto-fix: {it.fixer_label}')
                lines.append('')

        if fixable:
            lines.append(f'{len(fixable)} of these have an auto-fix available.')

        msg = '\n'.join(lines)
        if len(msg) > 3500:
            msg = msg[:3400] + '\n\n…(truncated; full report in console)'

        if fixable and not unfixable_errors:
            ans = messagebox.askyesnocancel(
                'Pre-build validation',
                msg + '\n\nApply auto-fixes now?\n'
                      '  Yes  — fix and proceed with build\n'
                      '  No   — proceed WITHOUT fixing (build may fail)\n'
                      '  Cancel — abort build')
            if ans is None: return 'cancel'
            return 'fix' if ans else 'skip'

        if unfixable_errors:
            ans = messagebox.askyesno(
                'Pre-build validation: errors',
                msg + '\n\nProceed with build anyway? (Pak may crash.)')
            return 'skip' if ans else 'cancel'

        ans = messagebox.askyesno(
            'Pre-build validation: warnings',
            msg + '\n\nProceed with build?')
        return 'skip' if ans else 'cancel'

    def _run_pre_build_validation(self):
        """Run BuildValidator and let the user respond. Returns True iff the
        build should proceed."""
        validator = BuildValidator(self.docs)

        # Validator runs ~50ms with the iterative-walk fix — no progress
        # dialog needed. Console-print progress so a frozen UI is at least
        # diagnosable from the launching shell.
        import time as _t
        print('Pre-build validation: starting…', flush=True)
        _t0 = _t.perf_counter()
        issues = validator.run()
        print(f'Pre-build validation: completed in '
              f'{(_t.perf_counter()-_t0)*1000:.0f}ms with '
              f'{len(issues)} issue(s)', flush=True)

        errors = [i for i in issues if i.severity == 'error']
        warnings = [i for i in issues if i.severity == 'warning']

        if not issues:
            print('Pre-build validation: 0 issues.')
            return True

        # Stdout summary always
        print(f'Pre-build validation: {len(errors)} error(s), {len(warnings)} warning(s)')
        for it in issues:
            tag = {'error': 'ERR', 'warning': 'WARN', 'info': 'INFO'}[it.severity]
            doc = it.doc_key or '-'
            print(f'  [{tag}] {it.check} ({doc}): {it.detail}')

        fixable = [i for i in issues if i.fixer]
        unfixable_errors = [i for i in errors if not i.fixer]

        choice = self._show_validation_dialog(
            issues, fixable, unfixable_errors, has_errors=bool(errors),
            validator=validator)

        if choice == 'cancel':
            return False
        if choice == 'fix':
            fixed, remaining = validator.auto_fix(issues)
            for doc in self.docs.values():
                if doc.data is not None:
                    try: doc.save()
                    except Exception: pass
            rem_errors = [i for i in remaining if i.severity == 'error']
            if rem_errors:
                if not messagebox.askyesno(
                        'Validation: residual errors',
                        f'Auto-fix resolved {fixed} issue(s) but '
                        f'{len(rem_errors)} error(s) remain. Proceed with '
                        'build anyway? (Pak may crash.)'):
                    return False
            else:
                print(f'Auto-fix applied: {fixed} issue(s) resolved.')
            return True
        # choice == 'skip'
        return True

    def build_pak(self):
        # Pre-build validation pipeline. Runs every known check and shows the
        # user the report; offers to auto-fix any issues with a registered
        # fixer. Catches the defect classes that have crashed past builds:
        #   - NameMap incompleteness, duplicates, stale counters
        #   - Empty StructProperty arrays without DummyStruct
        #   - Unresolved cross-DT row references
        #   - Live rows referencing Disabled targets
        # Implementation: scripts/SandboxZoneEditor.py BuildValidator class.
        # New checks are added by appending to BuildValidator.CHECKS.
        if not self._run_pre_build_validation():
            return  # user cancelled or unfixable errors

        dirty = [doc for doc in self.docs.values() if doc.data is not None and doc.is_dirty()]
        if dirty:
            if not messagebox.askyesno('Unsaved changes',
                    f'Save {len(dirty)} modified JSON file(s) first?'):
                return
            self.save_all()

        # Only bundle DataTables that actually differ from their pristine
        # sidecar (.original.json). Round-tripping untouched tables through
        # UAssetGUI can lose fidelity on complex nested types and cause
        # async-loader crashes (observed: FAsyncLoadingThread crash on
        # v0.4.0 when all six tables were bundled).
        include = [doc for doc in self.docs.values() if doc.differs_from_original()]
        if not include:
            # No changes — but let the user FORCE-build all tables anyway.
            # Useful for diagnosing whether the pipeline itself corrupts
            # data (vanilla-in vs vanilla-out byte comparison).
            answer = messagebox.askyesnocancel(
                'No changes detected',
                'No DataTables differ from the pristine baseline.\n\n'
                'Yes  = FORCE BUILD with ALL DataTables bundled (diagnostic — '
                'tests whether the build pipeline itself round-trips cleanly).\n\n'
                'No   = Cancel (default — only bundle modified tables).\n\n'
                'Cancel = same as No.')
            if not answer:  # No or Cancel
                return
            # Force-build path: include every loaded doc that has data.
            include = [doc for doc in self.docs.values()
                       if doc.data is not None]
            if not include:
                messagebox.showerror('Nothing to build',
                    'No DataTables loaded. Cannot force-build.')
                return

        # Show a manifest dialog so the user can review what's about to be
        # packaged and cancel if it doesn't match their intent.
        if not self._confirm_build_manifest(include):
            return
        for lbl, exe in [('UAssetGUI.exe', UASSETGUI_EXE), ('retoc.exe', RETOC_EXE)]:
            if not exe.exists():
                messagebox.showerror('Missing tool', f'{lbl} not found at:\n{exe}'); return

        work = PROJECT_ROOT / 'experiments' / 'sandbox_zone_mod'
        out_dir = work / 'out'
        out_dir.mkdir(parents=True, exist_ok=True)

        # Per-doc staging path. Most docs share STAGED_REL_DIR (GameWorld);
        # strings (World.uasset) lives in a different subtree.
        def staging_dir_for(doc):
            sub = STAGED_DIR_OVERRIDES.get(doc.key, STAGED_REL_DIR)
            return work / 'staging' / sub

        # Ensure each included doc's staging dir exists
        for doc in include:
            staging_dir_for(doc).mkdir(parents=True, exist_ok=True)

        # Version-stamped pak basename. Bumped only AFTER a successful zip.
        mod_ver = SETTINGS.get_mod_version()
        pak_name = f'{MOD_NAME}_v{mod_ver}_P'

        # Clean prior pak triplets (any version) from out_dir so stale builds
        # don't linger alongside the current one.
        for f in out_dir.glob(f'{MOD_NAME}_v*_P.*'):
            try: f.unlink()
            except OSError: pass
        for f in out_dir.glob(f'{MOD_NAME}_P.*'):  # legacy unversioned
            try: f.unlink()
            except OSError: pass

        # Wipe the ENTIRE staging tree before each build. retoc to-zen packs
        # every file in `work/staging/`, so stale uassets from prior sessions
        # — for tables that are no longer in `include` (e.g. reverted to
        # pristine) — would otherwise ship in the pak as ghost edits.
        # This cost us ~a session of debugging when reverted-to-pristine
        # JSONs were producing pak crashes because old modified uassets
        # for World.uasset / DT_Moria_Biomes / DT_Moria_ZoneBubbleFilters
        # were still in staging from days-old runs. Clean slate every time.
        staging_root = work / 'staging'
        if staging_root.exists():
            import shutil as _sh
            for child in staging_root.iterdir():
                try:
                    if child.is_dir():
                        _sh.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    pass
        # Re-create the per-doc staging dirs after the wipe.
        for doc in include:
            staging_dir_for(doc).mkdir(parents=True, exist_ok=True)

        for doc in include:
            target = staging_dir_for(doc) / f'{doc.uasset_stem}.uasset'
            cmd = [str(UASSETGUI_EXE), 'fromjson',
                   str(doc.json_path), str(target), UE_VERSION]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            except Exception as e:
                messagebox.showerror('Build failed', f'UAssetGUI {doc.label}: {e}'); return
            if r.returncode != 0 or not target.exists():
                messagebox.showerror('Build failed',
                    f'UAssetGUI fromjson {doc.label} exit={r.returncode}\n'
                    f'{r.stdout}\n{r.stderr}')
                return

        # NOTE: retoc's <OUTPUT> argument is the .utoc filename itself, not
        # a base name. Pass it WITH the .utoc extension — retoc writes the
        # .utoc at exactly that path, and creates matching .pak / .ucas
        # alongside it. Passing a bare base name causes the .utoc to be
        # written without an extension and the mod will silently fail to load.
        cmd = [str(RETOC_EXE), 'to-zen', '-v', '--version', RETOC_VERSION,
               str(work / 'staging'), str(out_dir / f'{pak_name}.utoc')]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception as e:
            messagebox.showerror('Build failed', f'retoc: {e}'); return
        if r.returncode != 0:
            messagebox.showerror('Build failed',
                f'retoc to-zen exit={r.returncode}\n{r.stdout}\n{r.stderr}')
            return

        zip_name = f'{MOD_NAME}_v{mod_ver}.zip'
        zip_path = DOWNLOADS_DIR / zip_name
        triplet = [out_dir / f'{pak_name}{ext}' for ext in ('.pak', '.ucas', '.utoc')]
        triplet = [p for p in triplet if p.exists()]
        if not triplet:
            messagebox.showerror('Build failed', 'retoc produced no pak files.'); return
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in triplet:
                    zf.write(p, arcname=p.name)
        except Exception as e:
            messagebox.showerror('Build failed', f'Zip error: {e}'); return

        # Success — bump patch version so the NEXT build gets the next number.
        next_ver = SETTINGS.bump_mod_version()

        self.last_build = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.refresh_status()
        # Update window title so the bumped version is visible immediately.
        try:
            self.title(f'Moria WorldGen Editor — {MOD_NAME} v{next_ver}')
        except Exception:
            pass
        bundled = ', '.join(doc.label for doc in include)
        messagebox.showinfo('Build succeeded',
            f'{zip_name}\n→ {zip_path}\n\n'
            f'Bundled: {bundled}\n\n'
            f'Next build will be v{next_ver}.\n\n'
            'Extract to the game\'s Paks/~mods folder to install.')

    def preview_build_manifest(self):
        """Menu command — show the build manifest without building."""
        include = [doc for doc in self.docs.values() if doc.differs_from_original()]
        if not include:
            messagebox.showinfo('Nothing to bundle',
                'No DataTables differ from the pristine baseline.\n\n'
                'The pak would be empty.')
            return
        self._show_manifest_dialog(include, confirm=False)

    def _confirm_build_manifest(self, include):
        """Show modal manifest dialog with OK/Cancel. Returns True if OK."""
        return self._show_manifest_dialog(include, confirm=True)

    def _show_manifest_dialog(self, include, confirm):
        """Dialog that lists every DataTable that will be bundled and shows
        row-level added/removed/modified counts for each. Used both by the
        'Preview build manifest' menu item and by Build confirmation."""
        dlg = tk.Toplevel(self)
        dlg.title('Build manifest — what will be packaged')
        dlg.transient(self); dlg.grab_set()
        dlg.geometry('780x620')
        dlg.minsize(640, 420)

        # Pack buttons FIRST from the bottom so they're always pinned and
        # visible regardless of window resizing or content overflow.
        result = {'ok': False}
        btns = ttk.Frame(dlg, padding=(12, 8, 12, 12))
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        if confirm:
            def do_build(): result['ok'] = True; dlg.destroy()
            ttk.Button(btns, text='Build now', style='Accent.TButton',
                       command=do_build).pack(side=tk.RIGHT, padx=(6, 0))
            ttk.Button(btns, text='Cancel',
                       command=dlg.destroy).pack(side=tk.RIGHT)
        else:
            ttk.Button(btns, text='Close',
                       command=dlg.destroy).pack(side=tk.RIGHT)

        # Build-target summary — pinned above the buttons so it's never cut off.
        summary = ttk.LabelFrame(dlg, text='Build target', padding=8)
        summary.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(4, 4))
        _ver = SETTINGS.get_mod_version()
        ttk.Label(summary,
                  text=f'Pak name:  {MOD_NAME}_v{_ver}_P').pack(anchor='w')
        ttk.Label(summary,
                  text=f'Output:    ~/Downloads/{MOD_NAME}_v{_ver}.zip').pack(
            anchor='w')
        ttk.Label(summary,
                  text='Install:    extract the three files into '
                       '<Game>/Moria/Content/Paks/~mods/',
                  foreground=self.COLOR_MUTED).pack(anchor='w')

        # Header at top
        ttk.Label(dlg, text='The mod pak will bundle these DataTables:',
                  font=('Segoe UI', 11, 'bold'),
                  padding=(12, 12, 12, 2)).pack(side=tk.TOP, anchor='w')
        ttk.Label(dlg,
                  text='Only tables that differ from the pristine baseline are '
                       'included. Untouched tables are skipped to avoid\n'
                       'UAssetGUI round-trip fidelity loss.',
                  foreground=self.COLOR_MUTED,
                  padding=(12, 0, 12, 8)).pack(side=tk.TOP, anchor='w')

        # Body (treeview) expands to fill whatever space is left between the
        # header above and the summary+buttons pinned below.
        body = ttk.Frame(dlg, padding=(12, 0, 12, 0))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ('label', 'added', 'removed', 'modified')
        tv = ttk.Treeview(body, columns=cols, show='tree headings')
        tv.heading('#0', text='')
        tv.heading('label', text='DataTable')
        tv.heading('added', text='+ Added rows')
        tv.heading('removed', text='- Removed rows')
        tv.heading('modified', text='~ Modified rows')
        tv.column('#0', width=16, stretch=False)
        tv.column('label', width=220, stretch=True)
        tv.column('added', width=110, stretch=False)
        tv.column('removed', width=110, stretch=False)
        tv.column('modified', width=110, stretch=False)
        vsb = ttk.Scrollbar(body, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for doc in include:
            added, removed, modified = doc.change_summary()
            parent = tv.insert('', 'end', iid=doc.key,
                               values=(doc.label, len(added),
                                       len(removed), len(modified)),
                               open=True)
            for name in added:
                tv.insert(parent, 'end', values=(f'  + {name}', '', '', ''))
            for name in removed:
                tv.insert(parent, 'end', values=(f'  - {name}', '', '', ''))
            for name in modified:
                tv.insert(parent, 'end', values=(f'  ~ {name}', '', '', ''))

        dlg.wait_window()
        return result['ok']

    def show_about(self):
        messagebox.showinfo('About',
            f'{MOD_NAME} v{SETTINGS.get_mod_version()} — Moria WorldGen Editor\n\n'
            'Tabs:\n'
            '  • Zones — 44 SandboxSmall zones, fields + landmarks + decks\n'
            '  • Chapters — edit and add new chapters\n'
            '  • Biomes — view + basic edit biome rows and refs\n'
            '  • Bubbles — edit ZoneDeck DeckEntries (add/remove bubbles)\n'
            '  • Filters — edit ZoneBubbleFilters whitelist/blacklist\n'
            '  • Landmarks — edit BaseBubbleName + GuaranteedConnections\n'
            '  • Mappings — relationship view; double-click to jump\n'
            '  • Map — isometric visualizer with connectivity overlay\n\n'
            'Edits DataTable JSONs via UAssetGUI fromjson and packages\n'
            'into an IoStore mod pak (retoc to-zen). Uninstalling the\n'
            'pak restores original behavior.')

    def on_exit(self):
        dirty = [doc.label for doc in self.docs.values()
                 if doc.data is not None and doc.is_dirty()]
        if dirty:
            if not messagebox.askyesno('Unsaved changes',
                    'Unsaved in: ' + ', '.join(dirty) + '\nQuit anyway?'):
                return
        self.destroy()


def main():
    for _, (fname, _stem, _label) in DATATABLES.items():
        p = WGR_DIR / fname
        if not p.exists():
            print(f'WARNING: {p} not found.', file=sys.stderr)
    app = WorldGenApp()
    app.protocol('WM_DELETE_WINDOW', app.on_exit)
    app.mainloop()


if __name__ == '__main__':
    main()
