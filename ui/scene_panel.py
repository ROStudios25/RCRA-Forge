"""
ui/scene_panel.py
Scene / level info panel for RCRA Forge.

Shows information about the currently loaded level/zone asset,
including its DAT1 section tags and asset type.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem,
    QLabel, QFrame, QTextEdit, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont


class ScenePanel(QWidget):
    instance_selected = pyqtSignal(object)   # unused for now, kept for compatibility

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_level(self, level_info):
        """Display info from a core.level.LevelInfo object."""
        self._tree.clear()

        root = QTreeWidgetItem(self._tree)
        root.setText(0, f"📦  {level_info.asset_type}")
        f = root.font(0)
        f.setWeight(QFont.Weight.Bold)
        root.setFont(0, f)
        root.setForeground(0, QColor("#5dade2"))
        root.setExpanded(True)

        self._info.setPlainText(level_info.description)
        self._status.setText(f"Asset type: {level_info.asset_type}")

    def load_instances(self, inst_table):
        """Stub — instance tables not yet decoded."""
        pass

    def clear(self):
        self._tree.clear()
        self._info.clear()
        self._status.setText("No level loaded")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setObjectName("BrowserHeader")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 4, 8, 4)
        title = QLabel("SCENE / LEVEL")
        title.setObjectName("PanelTitle")
        hl.addWidget(title)
        layout.addWidget(hdr)

        # Tree (asset type display)
        self._tree = QTreeWidget()
        self._tree.setObjectName("AssetTree")
        self._tree.setHeaderHidden(True)
        self._tree.setMaximumHeight(80)
        layout.addWidget(self._tree)

        # Info text box — shows section tags and description
        info_lbl = QLabel("  DAT1 sections")
        info_lbl.setObjectName("SubPanelLabel")
        info_lbl.setFixedHeight(22)
        layout.addWidget(info_lbl)

        self._info = QTextEdit()
        self._info.setObjectName("LogBox")
        self._info.setReadOnly(True)
        self._info.setPlaceholderText(
            "Double-click a level or zone asset in the browser to inspect it.\n\n"
            "Full level/zone parsing is coming in a future update — "
            "the Hex Inspector tab shows the raw DAT1 bytes."
        )
        layout.addWidget(self._info, 1)

        # Status
        self._status = QLabel("No level loaded")
        self._status.setObjectName("StatusLabel")
        self._status.setContentsMargins(8, 3, 8, 3)
        self._status.setFixedHeight(20)
        layout.addWidget(self._status)
