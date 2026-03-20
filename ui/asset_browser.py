"""
ui/asset_browser.py
Left-panel asset browser for RCRA Forge.

Shows the TOC hierarchy: WAD files → asset types → individual assets.
Emits signals when the user selects an asset to load/preview.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QPushButton, QLabel, QComboBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QFont

from core.archive import AssetEntry, ASSET_TYPE_NAMES


# Type → accent color (hex) for the tree
TYPE_COLORS = {
    'MESH': '#5dade2',
    'TXTR': '#a9cce3',
    'LEVL': '#a8d8a8',
    'INST': '#c9b8e8',
    'MATL': '#f7dc6f',
    'ANIM': '#f0a500',
    'SKEL': '#e8907a',
    'COLL': '#95a5a6',
}


class AssetBrowser(QWidget):
    # Emitted when user double-clicks an asset
    asset_activated = pyqtSignal(object)   # AssetEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[AssetEntry] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("BrowserHeader")
        header.setFixedHeight(36)
        hlayout = QHBoxLayout(header)
        hlayout.setContentsMargins(8, 4, 8, 4)
        hlayout.setSpacing(4)

        lbl = QLabel("ASSETS")
        lbl.setObjectName("PanelTitle")
        hlayout.addWidget(lbl)
        hlayout.addStretch()

        layout.addWidget(header)

        # ── Filter bar ────────────────────────────────────────────────────────
        filter_frame = QFrame()
        filter_frame.setObjectName("FilterBar")
        flayout = QHBoxLayout(filter_frame)
        flayout.setContentsMargins(6, 4, 6, 4)
        flayout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter assets…")
        self._search.setObjectName("SearchBox")
        self._search.textChanged.connect(self._apply_filter)
        flayout.addWidget(self._search)

        self._type_filter = QComboBox()
        self._type_filter.setObjectName("TypeFilter")
        self._type_filter.addItem("All Types")
        for t in ['MESH', 'TXTR', 'LEVL', 'INST', 'MATL', 'ANIM', 'SKEL', 'COLL']:
            self._type_filter.addItem(t)
        self._type_filter.setFixedWidth(80)
        self._type_filter.currentTextChanged.connect(self._apply_filter)
        flayout.addWidget(self._type_filter)

        layout.addWidget(filter_frame)

        # ── Tree ──────────────────────────────────────────────────────────────
        self._tree = QTreeWidget()
        self._tree.setObjectName("AssetTree")
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QLabel("No archive loaded")
        self._status.setObjectName("StatusLabel")
        self._status.setContentsMargins(8, 4, 8, 4)
        self._status.setFixedHeight(22)
        layout.addWidget(self._status)

    def load_entries(self, entries):
        self._entries = entries
        self._rebuild_tree(entries)
        self._status.setText(f"{len(entries):,} assets loaded")

    def load_entries_grouped(self, entries, groups: list):
        """Fast path: groups already computed on background thread."""
        self._entries = entries
        self._tree.clear()
        self._tree.setUpdatesEnabled(False)
        for arc_idx, idx_list in groups:
            self._add_group_item(arc_idx, idx_list, entries)
        self._tree.setUpdatesEnabled(True)
        try:
            self._tree.itemExpanded.disconnect(self._on_group_expanded)
        except Exception:
            pass
        self._tree.itemExpanded.connect(self._on_group_expanded)
        self._status.setText(f"{len(entries):,} assets loaded")

    def clear(self):
        self._entries = []
        self._tree.clear()
        self._status.setText("No archive loaded")

    # ── Private ───────────────────────────────────────────────────────────────

    def _rebuild_tree(self, entries):
        self._tree.clear()
        self._tree.setUpdatesEnabled(False)

        from core.archive import _LazyEntryList
        import numpy as np

        if isinstance(entries, _LazyEntryList) and len(entries) > 0:
            # Single-pass O(n) grouping using numpy argsort
            arc_col  = entries._sizes['archive'][:len(entries)].astype(np.int32)
            sort_idx = np.argsort(arc_col, kind='stable')
            sorted_arcs = arc_col[sort_idx]
            # Find boundaries between archive groups
            boundaries = np.where(np.diff(sorted_arcs))[0] + 1
            starts = np.concatenate([[0], boundaries])
            ends   = np.concatenate([boundaries, [len(sort_idx)]])

            for start, end in zip(starts, ends):
                arc_idx  = int(sorted_arcs[start])
                idx_list = sort_idx[start:end].tolist()
                self._add_group_item(arc_idx, idx_list, entries)
        else:
            # Fallback for plain lists
            groups: dict[int, list] = {}
            for i, e in enumerate(entries):
                groups.setdefault(e.archive, []).append(i)
            for arc_idx in sorted(groups.keys()):
                self._add_group_item(arc_idx, groups[arc_idx], entries)

        self._tree.setUpdatesEnabled(True)
        try:
            self._tree.itemExpanded.disconnect(self._on_group_expanded)
        except Exception:
            pass
        self._tree.itemExpanded.connect(self._on_group_expanded)

    def _add_group_item(self, arc_idx: int, idx_list: list, entries):
        group_item = QTreeWidgetItem(self._tree)
        group_item.setText(0, f"Archive {arc_idx:03d}  ({len(idx_list):,})")
        group_item.setForeground(0, QColor('#5dade2'))
        f = group_item.font(0)
        f.setWeight(QFont.Weight.DemiBold)
        f.setPointSize(9)
        group_item.setFont(0, f)
        group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        group_item.setData(0, Qt.ItemDataRole.UserRole + 1, (entries, idx_list))
        QTreeWidgetItem(group_item).setText(0, "  Loading…")

    def _on_group_expanded(self, group_item: QTreeWidgetItem):
        """Populate children on first expand — lazy loading."""
        payload = group_item.data(0, Qt.ItemDataRole.UserRole + 1)
        if payload is None:
            return
        entries, idx_list = payload
        group_item.takeChildren()
        self._tree.setUpdatesEnabled(False)
        for idx in idx_list:
            entry = entries[idx]
            child = QTreeWidgetItem(group_item)
            child.setText(0, f"{entry.asset_id:016X}")
            child.setData(0, Qt.ItemDataRole.UserRole, entry)
            child.setToolTip(0, (
                f"ID:      {entry.asset_id:#018x}\n"
                f"Archive: {entry.archive}\n"
                f"Offset:  {entry.offset:#010x}\n"
                f"Size:    {entry.size:,} bytes"
                + (f"\nHeader:  yes (36B)" if entry.header else "")
            ))
        self._tree.setUpdatesEnabled(True)
        group_item.setData(0, Qt.ItemDataRole.UserRole + 1, None)

    def _apply_filter(self):
        text = self._search.text().lower().strip()
        if not text:
            self._rebuild_tree(self._entries)
            self._status.setText(f"{len(self._entries):,} assets loaded")
            return

        # Fast search: convert hex text to int and compare against numpy array
        from core.archive import _LazyEntryList
        import numpy as np
        if isinstance(self._entries, _LazyEntryList):
            try:
                search_id = int(text, 16)
                matches = np.where(self._entries._ids == search_id)[0]
                filtered = [self._entries[int(i)] for i in matches]
            except ValueError:
                # Not a valid hex — no results
                filtered = []
        else:
            filtered = [e for e in self._entries if text in f"{e.asset_id:016x}"]

        self._rebuild_tree(filtered)
        self._status.setText(f"{len(filtered):,} of {len(self._entries):,} assets")

    def _on_double_click(self, item: QTreeWidgetItem, col: int):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry:
            self.asset_activated.emit(entry)
