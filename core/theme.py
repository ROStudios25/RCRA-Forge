"""
core/theme.py
Theme / colour-scheme system for RCRA Forge.

Colour slots are named semantic roles (e.g. BG_BASE, ACCENT) rather than
raw hex values.  A Theme is a dict mapping slot → "#rrggbb".

Two built-in presets are provided (Dark, Light).  The user can customise any
slot via the Preferences dialog; overrides are saved to config.json.

Usage
-----
    from core.theme import theme_manager
    qss = theme_manager.stylesheet()   # call after load_config()
    widget.setStyleSheet(qss)
"""

import json, os, copy
from typing import Dict

# ── Slot definitions ─────────────────────────────────────────────────────────

SLOTS: Dict[str, str] = {
    # key             : human label
    "BG_BASE"         : "Base background",
    "BG_PANEL"        : "Panel background",
    "BG_DEEP"         : "Deep background",
    "BG_SURFACE"      : "Surface / widget background",
    "BG_ALT"          : "Alternate row background",
    "BG_HOVER"        : "Hover background",
    "BG_SELECT"       : "Selection background",
    "BG_SELECT_DEEP"  : "Selection background (deep)",
    "BORDER"          : "Border",
    "BORDER_FOCUS"    : "Focus border",
    "BORDER_STRONG"   : "Strong border",
    "TEXT_PRIMARY"    : "Primary text",
    "TEXT_SECONDARY"  : "Secondary text",
    "TEXT_DIM"        : "Dimmed text",
    "TEXT_MUTED"      : "Muted / label text",
    "TEXT_SELECT"     : "Selected text",
    "TEXT_MONO"       : "Monospace / value text",
    "ACCENT"          : "Accent (buttons, links)",
    "ACCENT_HOVER"    : "Accent hover",
    "ACCENT_PRESS"    : "Accent pressed",
    "ACCENT_LIGHT"    : "Accent light (text on dark)",
    "ACCENT_BRIGHT"   : "Accent bright highlight",
    "WARN"            : "Warning / groups toggle",
    "WARN_HOVER"      : "Warning hover",
    "WARN_BG"         : "Warning background",
    "SCROLLBAR"       : "Scrollbar handle",
    "SCROLLBAR_HOVER" : "Scrollbar handle hover",
    "EXPORT_BG"       : "Export button background",
    "EXPORT_BORDER"   : "Export button border",
    "EXPORT_TEXT"     : "Export button text",
}

# ── Built-in presets ─────────────────────────────────────────────────────────

PRESETS: Dict[str, Dict[str, str]] = {
    "Dark (Default)": {
        "BG_BASE"         : "#1a1c22",
        "BG_PANEL"        : "#13151a",
        "BG_DEEP"         : "#0d0f14",
        "BG_SURFACE"      : "#1e2028",
        "BG_ALT"          : "#1d1f26",
        "BG_HOVER"        : "#22263a",
        "BG_SELECT"       : "#253a5e",
        "BG_SELECT_DEEP"  : "#1f3055",
        "BORDER"          : "#2a2d36",
        "BORDER_FOCUS"    : "#3a6fbf",
        "BORDER_STRONG"   : "#3a3d4a",
        "TEXT_PRIMARY"    : "#d4d8e0",
        "TEXT_SECONDARY"  : "#c0c4cc",
        "TEXT_DIM"        : "#a0a8b8",
        "TEXT_MUTED"      : "#6a7080",
        "TEXT_SELECT"     : "#ffffff",
        "TEXT_MONO"       : "#90b8d8",
        "ACCENT"          : "#3a6fbf",
        "ACCENT_HOVER"    : "#2560af",
        "ACCENT_PRESS"    : "#143a7a",
        "ACCENT_LIGHT"    : "#5ba3f5",
        "ACCENT_BRIGHT"   : "#5dade2",
        "WARN"            : "#f0a500",
        "WARN_HOVER"      : "#f0c040",
        "WARN_BG"         : "#2a2210",
        "SCROLLBAR"       : "#2a3040",
        "SCROLLBAR_HOVER" : "#3a4560",
        "EXPORT_BG"       : "#1f4a8f",
        "EXPORT_BORDER"   : "#3a70cf",
        "EXPORT_TEXT"     : "#e0eaff",
    },
    "Midnight Blue": {
        "BG_BASE"         : "#0e1420",
        "BG_PANEL"        : "#0a0f18",
        "BG_DEEP"         : "#060a10",
        "BG_SURFACE"      : "#141a28",
        "BG_ALT"          : "#111825",
        "BG_HOVER"        : "#1a2540",
        "BG_SELECT"       : "#1e3a6e",
        "BG_SELECT_DEEP"  : "#152d58",
        "BORDER"          : "#1e2a40",
        "BORDER_FOCUS"    : "#4080d0",
        "BORDER_STRONG"   : "#283850",
        "TEXT_PRIMARY"    : "#c8d8f0",
        "TEXT_SECONDARY"  : "#a8b8d8",
        "TEXT_DIM"        : "#8090b0",
        "TEXT_MUTED"      : "#506080",
        "TEXT_SELECT"     : "#e8f0ff",
        "TEXT_MONO"       : "#70a8e0",
        "ACCENT"          : "#4080d0",
        "ACCENT_HOVER"    : "#3070c0",
        "ACCENT_PRESS"    : "#205090",
        "ACCENT_LIGHT"    : "#60a0f0",
        "ACCENT_BRIGHT"   : "#80c0ff",
        "WARN"            : "#e09020",
        "WARN_HOVER"      : "#f0b030",
        "WARN_BG"         : "#201800",
        "SCROLLBAR"       : "#1e2a40",
        "SCROLLBAR_HOVER" : "#2e4060",
        "EXPORT_BG"       : "#1a3a70",
        "EXPORT_BORDER"   : "#3060b0",
        "EXPORT_TEXT"     : "#c8e0ff",
    },
    "Slate": {
        "BG_BASE"         : "#1c1e24",
        "BG_PANEL"        : "#141618",
        "BG_DEEP"         : "#0e1012",
        "BG_SURFACE"      : "#20222a",
        "BG_ALT"          : "#1e2028",
        "BG_HOVER"        : "#282c38",
        "BG_SELECT"       : "#2a3550",
        "BG_SELECT_DEEP"  : "#222c44",
        "BORDER"          : "#2c2f3a",
        "BORDER_FOCUS"    : "#5080c0",
        "BORDER_STRONG"   : "#3a3e4c",
        "TEXT_PRIMARY"    : "#d0d4de",
        "TEXT_SECONDARY"  : "#b0b6c4",
        "TEXT_DIM"        : "#8890a4",
        "TEXT_MUTED"      : "#606878",
        "TEXT_SELECT"     : "#ffffff",
        "TEXT_MONO"       : "#8ab0d0",
        "ACCENT"          : "#5080c0",
        "ACCENT_HOVER"    : "#4070b0",
        "ACCENT_PRESS"    : "#305090",
        "ACCENT_LIGHT"    : "#70a0e0",
        "ACCENT_BRIGHT"   : "#90c0f0",
        "WARN"            : "#d09030",
        "WARN_HOVER"      : "#e0a840",
        "WARN_BG"         : "#241c08",
        "SCROLLBAR"       : "#282c3a",
        "SCROLLBAR_HOVER" : "#384258",
        "EXPORT_BG"       : "#204878",
        "EXPORT_BORDER"   : "#4070b8",
        "EXPORT_TEXT"     : "#d0e8ff",
    },
    "Light": {
        "BG_BASE"         : "#f0f2f5",
        "BG_PANEL"        : "#e4e7ec",
        "BG_DEEP"         : "#d8dce4",
        "BG_SURFACE"      : "#ffffff",
        "BG_ALT"          : "#f5f6f8",
        "BG_HOVER"        : "#dce4f0",
        "BG_SELECT"       : "#c0d4ee",
        "BG_SELECT_DEEP"  : "#b0c8e8",
        "BORDER"          : "#c8cdd8",
        "BORDER_FOCUS"    : "#3a6fbf",
        "BORDER_STRONG"   : "#b0b8c8",
        "TEXT_PRIMARY"    : "#1a1e28",
        "TEXT_SECONDARY"  : "#2a3040",
        "TEXT_DIM"        : "#485060",
        "TEXT_MUTED"      : "#707888",
        "TEXT_SELECT"     : "#0a0e18",
        "TEXT_MONO"       : "#2050a0",
        "ACCENT"          : "#3a6fbf",
        "ACCENT_HOVER"    : "#2a5faf",
        "ACCENT_PRESS"    : "#1a4f9f",
        "ACCENT_LIGHT"    : "#2050a0",
        "ACCENT_BRIGHT"   : "#1a40a0",
        "WARN"            : "#b87000",
        "WARN_HOVER"      : "#c88010",
        "WARN_BG"         : "#fff8e0",
        "SCROLLBAR"       : "#b0b8c8",
        "SCROLLBAR_HOVER" : "#8090a8",
        "EXPORT_BG"       : "#3a6fbf",
        "EXPORT_BORDER"   : "#2a5faf",
        "EXPORT_TEXT"     : "#ffffff",
    },
}

# ── Stylesheet template ───────────────────────────────────────────────────────

def _build_stylesheet(c: Dict[str, str]) -> str:
    """Build the full Qt stylesheet from a colour slot dict."""
    return f"""
        QMainWindow, QWidget {{
            background: {c['BG_BASE']};
            color: {c['TEXT_PRIMARY']};
            font-family: 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', sans-serif;
            font-size: 11px;
        }}
        QMenuBar {{
            background: {c['BG_PANEL']};
            color: {c['TEXT_SECONDARY']};
            border-bottom: 1px solid {c['BORDER']};
            padding: 2px 0;
        }}
        QMenuBar::item:selected {{ background: {c['BORDER']}; }}
        QMenu {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            color: {c['TEXT_PRIMARY']};
        }}
        QMenu::item:selected {{ background: {c['ACCENT']}; color: {c['TEXT_SELECT']}; }}
        QToolBar {{
            background: {c['BG_PANEL']};
            border-bottom: 1px solid {c['BORDER']};
            spacing: 4px;
            padding: 2px 6px;
        }}
        QToolBar QToolButton {{
            background: transparent;
            border: 1px solid transparent;
            border-radius: 4px;
            padding: 3px 8px;
            color: {c['TEXT_SECONDARY']};
        }}
        QToolBar QToolButton:hover    {{ background: {c['BORDER']}; border-color: {c['BORDER_STRONG']}; }}
        QToolBar QToolButton:checked  {{ background: {c['BG_SELECT']}; border-color: {c['ACCENT']}; color: {c['ACCENT_LIGHT']}; }}
        QSplitter::handle {{ background: {c['BORDER']}; width: 4px; height: 4px; }}
        QSplitter::handle:hover   {{ background: {c['ACCENT']}; }}
        QSplitter::handle:pressed {{ background: {c['ACCENT_BRIGHT']}; }}

        #BottomTabs {{
            background: {c['BG_PANEL']};
            border-top: 2px solid {c['BORDER']};
        }}
        #BottomTabs QTabBar::tab {{
            background: {c['BG_BASE']};
            color: {c['TEXT_MUTED']};
            border: none;
            border-bottom: 2px solid transparent;
            padding: 5px 14px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }}
        #BottomTabs QTabBar::tab:hover    {{ color: {c['TEXT_DIM']}; background: {c['BG_SURFACE']}; }}
        #BottomTabs QTabBar::tab:selected {{
            color: {c['ACCENT_LIGHT']};
            border-bottom: 2px solid {c['ACCENT']};
            background: {c['BG_BASE']};
        }}
        #BottomTabs QTabWidget::pane {{ border: none; }}

        #BrowserHeader {{
            background: {c['BG_PANEL']};
            border-bottom: 1px solid {c['BORDER']};
        }}
        #PanelTitle {{
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1.5px;
            color: {c['TEXT_MUTED']};
        }}
        #FilterBar {{ background: {c['BG_DEEP']}; border-bottom: 1px solid {c['BORDER']}; }}
        #SearchBox {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            padding: 3px 6px;
            color: {c['TEXT_PRIMARY']};
        }}
        #SearchBox:focus {{ border-color: {c['BORDER_FOCUS']}; }}
        #TypeFilter {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            color: {c['TEXT_SECONDARY']};
        }}
        #AssetTree {{
            background: {c['BG_BASE']};
            border: none;
            color: {c['TEXT_SECONDARY']};
            alternate-background-color: {c['BG_ALT']};
            selection-background-color: {c['BG_SELECT']};
        }}
        #AssetTree::item {{ padding: 2px 4px; border-radius: 2px; }}
        #AssetTree::item:hover    {{ background: {c['BG_HOVER']}; }}
        #AssetTree::item:selected {{ background: {c['BG_SELECT']}; color: {c['TEXT_SELECT']}; }}
        #StatusLabel {{
            font-size: 10px;
            color: {c['TEXT_MUTED']};
            background: {c['BG_PANEL']};
            border-top: 1px solid {c['BORDER']};
        }}
        #SubPanelLabel {{
            background: {c['BG_DEEP']};
            color: {c['TEXT_MUTED']};
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            border-bottom: 1px solid {c['BORDER']};
            padding-left: 8px;
        }}

        QGroupBox {{
            font-size: 10px;
            font-weight: 600;
            color: {c['TEXT_MUTED']};
            border: 1px solid {c['BORDER']};
            border-radius: 6px;
            margin-top: 12px;
            padding-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 8px;
        }}
        #FieldValue {{
            color: {c['TEXT_MONO']};
            font-family: 'Consolas', 'JetBrains Mono', monospace;
            font-size: 11px;
        }}
        #FmtCombo {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            padding: 2px 6px;
            color: {c['TEXT_SECONDARY']};
        }}
        QCheckBox {{ color: {c['TEXT_DIM']}; }}
        QCheckBox::indicator {{
            width: 13px; height: 13px;
            border: 1px solid {c['BORDER_STRONG']};
            border-radius: 3px;
            background: {c['BG_SURFACE']};
        }}
        QCheckBox::indicator:checked {{ background: {c['ACCENT']}; border-color: {c['ACCENT_LIGHT']}; }}
        #ExportBtn {{
            background: {c['EXPORT_BG']};
            border: 1px solid {c['EXPORT_BORDER']};
            border-radius: 5px;
            padding: 6px 12px;
            color: {c['EXPORT_TEXT']};
            font-weight: 600;
            font-size: 12px;
        }}
        #ExportBtn:hover   {{ background: {c['ACCENT_HOVER']}; }}
        #ExportBtn:pressed {{ background: {c['ACCENT_PRESS']}; }}
        #ExportBtn:disabled {{ background: {c['BG_SURFACE']}; color: {c['TEXT_MUTED']}; border-color: {c['BORDER']}; }}
        #ExportStatus {{ color: {c['ACCENT_LIGHT']}; font-size: 10px; }}

        #GroupsToggleBtn {{
            background: transparent;
            border: 1px solid {c['BORDER_STRONG']};
            border-radius: 4px;
            padding: 2px 8px;
            color: {c['TEXT_DIM']};
            font-size: 10px;
            font-weight: 500;
        }}
        #GroupsToggleBtn:hover   {{ background: {c['WARN_BG']}; border-color: {c['WARN']}; color: {c['WARN_HOVER']}; }}
        #GroupsToggleBtn:checked {{ background: {c['WARN_BG']}; border-color: {c['WARN']}; color: {c['WARN']}; font-weight: 700; }}
        #GroupsToggleBtn:checked:hover {{ background: {c['WARN_BG']}; }}
        #LogBox {{
            background: {c['BG_PANEL']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            color: {c['TEXT_MUTED']};
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 10px;
        }}
        #GamePathLabel {{ color: {c['TEXT_MUTED']}; font-size: 10px; }}

        #ZoomRow {{ background: {c['BG_DEEP']}; border-top: 1px solid {c['BORDER']}; }}
        #TexInfo {{ background: {c['BG_PANEL']}; border-top: 1px solid {c['BORDER']}; }}
        #TexInfoKey {{ color: {c['TEXT_MUTED']}; font-size: 9px; font-weight: 600; letter-spacing: 1px; }}
        #TexInfoVal {{ color: {c['TEXT_MONO']}; font-family: 'Consolas', monospace; font-size: 11px; }}

        #InstTable {{
            background: {c['BG_BASE']};
            alternate-background-color: {c['BG_ALT']};
            border: none;
            color: {c['TEXT_DIM']};
            gridline-color: {c['BORDER']};
            selection-background-color: {c['BG_SELECT']};
        }}
        #InstTable QHeaderView::section {{
            background: {c['BG_PANEL']};
            color: {c['TEXT_MUTED']};
            border: none;
            border-bottom: 1px solid {c['BORDER']};
            padding: 3px 6px;
            font-size: 10px;
            font-weight: 600;
        }}

        QScrollBar:vertical {{
            background: {c['BG_PANEL']};
            width: 10px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {c['SCROLLBAR']};
            border-radius: 4px;
            min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c['SCROLLBAR_HOVER']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{
            background: {c['BG_PANEL']};
            height: 10px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {c['SCROLLBAR']};
            border-radius: 4px;
            min-width: 20px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {c['SCROLLBAR_HOVER']}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

        QSlider::groove:horizontal {{
            background: {c['BORDER']};
            height: 3px;
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {c['ACCENT']};
            width: 12px; height: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }}

        QStatusBar {{
            background: {c['BG_PANEL']};
            border-top: 1px solid {c['BORDER']};
            color: {c['TEXT_MUTED']};
            font-size: 10px;
        }}
        QProgressBar {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 3px;
            height: 16px;
            color: {c['TEXT_PRIMARY']};
            text-align: center;
            font-size: 10px;
        }}
        QProgressBar::chunk {{ background: {c['ACCENT']}; border-radius: 3px; }}

        QPushButton {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            padding: 3px 8px;
            color: {c['TEXT_SECONDARY']};
        }}
        QPushButton:hover   {{ background: {c['BG_HOVER']}; border-color: {c['BORDER_STRONG']}; }}
        QPushButton:pressed {{ background: {c['BG_ALT']}; }}

        /* Fix QScrollArea viewport inheritance */
        QScrollArea > QWidget > QWidget,
        QScrollArea QWidget#PropertiesPanelInner,
        #PropertiesPanelInner {{
            background: {c['BG_BASE']};
            color: {c['TEXT_PRIMARY']};
        }}
        QAbstractScrollArea::viewport {{
            background: {c['BG_BASE']};
        }}

        QDialog {{
            background: {c['BG_BASE']};
            color: {c['TEXT_PRIMARY']};
        }}
        QLabel {{
            color: {c['TEXT_PRIMARY']};
        }}
        QLineEdit {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            padding: 3px 6px;
            color: {c['TEXT_PRIMARY']};
        }}
        QLineEdit:focus {{ border-color: {c['BORDER_FOCUS']}; }}
        QComboBox {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            border-radius: 4px;
            padding: 3px 6px;
            color: {c['TEXT_PRIMARY']};
        }}
        QComboBox QAbstractItemView {{
            background: {c['BG_SURFACE']};
            border: 1px solid {c['BORDER']};
            selection-background-color: {c['BG_SELECT']};
            color: {c['TEXT_PRIMARY']};
        }}
    """


# ── Theme manager ─────────────────────────────────────────────────────────────

class ThemeManager:
    """Singleton that holds the active colour scheme and builds stylesheets."""

    CONFIG_KEY = "theme"

    def __init__(self):
        self._preset_name: str = "Dark (Default)"
        self._overrides:   Dict[str, str] = {}   # user per-slot overrides
        self._user_presets: Dict[str, Dict[str, str]] = {}  # saved user presets

    # ── Resolved colours ──────────────────────────────────────────────────────

    def resolved(self) -> Dict[str, str]:
        """Return the full merged colour dict (preset + overrides)."""
        if self._preset_name in self._user_presets:
            base = copy.deepcopy(self._user_presets[self._preset_name])
        else:
            base = copy.deepcopy(PRESETS.get(self._preset_name, PRESETS["Dark (Default)"]))
        base.update(self._overrides)
        return base

    def stylesheet(self) -> str:
        return _build_stylesheet(self.resolved())

    # ── Preset ────────────────────────────────────────────────────────────────



    def current_preset(self) -> str:
        return self._preset_name

    def set_preset(self, name: str, clear_overrides: bool = False):
        if name in PRESETS:
            self._preset_name = name
            if clear_overrides:
                self._overrides.clear()

    # ── Per-slot override ─────────────────────────────────────────────────────

    def set_slot(self, slot: str, colour: str):
        self._overrides[slot] = colour

    def reset_slot(self, slot: str):
        self._overrides.pop(slot, None)

    def reset_all_overrides(self):
        self._overrides.clear()

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"preset": self._preset_name, "overrides": copy.deepcopy(self._overrides)}

    def from_dict(self, d: dict):
        self._preset_name = d.get("preset", "Dark (Default)")
        self._overrides   = d.get("overrides", {})
        self._user_presets = d.get("user_presets", {})

    # ── User presets ──────────────────────────────────────────────────────────

    def preset_names(self):
        """All preset names: built-ins first, then user presets alphabetically."""
        return list(PRESETS.keys()) + sorted(self._user_presets.keys())

    def save_user_preset(self, name: str, colours: dict):
        self._user_presets[name] = copy.deepcopy(colours)
        # Switch to it immediately
        self._preset_name = name
        self._overrides.clear()

    def delete_user_preset(self, name: str):
        self._user_presets.pop(name, None)

    def to_dict(self) -> dict:
        return {
            "preset"       : self._preset_name,
            "overrides"    : copy.deepcopy(self._overrides),
            "user_presets" : copy.deepcopy(self._user_presets),
        }


# Expose built-in preset names for guard checks in dialog
BUILTIN_PRESETS = set(PRESETS.keys())

# Singleton
theme_manager = ThemeManager()
