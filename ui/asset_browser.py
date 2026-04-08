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
        self._lookup  = None
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
        # Real extensions from hashes.txt — populated after hashes load
        for t in ['.model', '.texture', '.zone', '.animclip', '.material',
                  '.actor', '.config', '.visualeffect', '.level', '.soundbank']:
            self._type_filter.addItem(t)
        self._type_filter.setFixedWidth(100)
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

    def load_entries_grouped(self, entries, groups: list, lookup=None):
        """Fast path: groups already computed on background thread."""
        import time
        t0 = time.perf_counter()
        self._entries = entries
        self._lookup  = lookup
        self._tree.clear()
        t1 = time.perf_counter()
        self._tree.setUpdatesEnabled(False)
        for arc_idx, idx_arr in groups:
            self._add_group_item(arc_idx, idx_arr, entries)
        t2 = time.perf_counter()
        self._tree.setUpdatesEnabled(True)
        t3 = time.perf_counter()
        try:
            self._tree.itemExpanded.disconnect(self._on_group_expanded)
        except Exception:
            pass
        self._tree.itemExpanded.connect(self._on_group_expanded)
        n_named = len(lookup) if lookup and lookup.is_loaded() else 0
        suffix = f"  ·  {n_named:,} named" if n_named else ""
        elapsed = time.perf_counter() - t0
        print(f"[browser] clear:{t1-t0:.3f}s  add_groups:{t2-t1:.3f}s  "
              f"enable:{t3-t2:.3f}s  total:{elapsed:.3f}s  groups:{len(groups)}")
        self._status.setText(
            f"{len(entries):,} assets loaded{suffix}  "
            f"[browser:{elapsed:.2f}s]"
        )

    def set_lookup(self, lookup):
        """Update the hash lookup and refresh visible tree items."""
        self._lookup = lookup
        # Refresh already-expanded groups
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            if group.childCount() > 0:
                first_child = group.child(0)
                if first_child.text(0) != "  Loading…":
                    # Already expanded — refresh names
                    for j in range(group.childCount()):
                        child = group.child(j)
                        entry = child.data(0, Qt.ItemDataRole.UserRole)
                        if entry:
                            name = lookup.name(entry.asset_id)
                            child.setText(0, name)

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
        entries, idx_arr = payload
        group_item.takeChildren()
        self._tree.setUpdatesEnabled(False)
        # idx_arr may be a numpy array or a plain list
        for idx in idx_arr:
            entry = entries[int(idx)]
            child = QTreeWidgetItem(group_item)

            if self._lookup and self._lookup.is_loaded():
                display = self._lookup.name(entry.asset_id)
                full    = self._lookup.full_path(entry.asset_id)
            else:
                display = f"{entry.asset_id:016X}"
                full    = display

            child.setText(0, display)
            child.setData(0, Qt.ItemDataRole.UserRole, entry)
            child.setToolTip(0, (
                f"ID:      {entry.asset_id:#018x}\n"
                f"Path:    {full}\n"
                f"Archive: {entry.archive}\n"
                f"Offset:  {entry.offset:#010x}\n"
                f"Size:    {entry.size:,} bytes"
                + (f"\nHeader:  yes (36B)" if entry.header else "")
            ))
        self._tree.setUpdatesEnabled(True)
        group_item.setData(0, Qt.ItemDataRole.UserRole + 1, None)

    def _apply_filter(self):
        text     = self._search.text().lower().strip()
        ext_flt  = self._type_filter.currentText()
        if ext_flt == "All Types":
            ext_flt = None

        from core.archive import _LazyEntryList
        import numpy as np

        # Fast path: numpy search for hex ID match + extension filter
        if isinstance(self._entries, _LazyEntryList) and self._lookup and self._lookup.is_loaded():
            if not text and not ext_flt:
                # Reset to full grouped view
                self.load_entries_grouped(
                    self._entries,
                    self._compute_groups(self._entries),
                    self._lookup
                )
                return

            # Search by hex ID exact match first
            matched_indices = []
            if text:
                # Try hex ID match
                try:
                    search_id = int(text, 16)
                    hex_matches = np.where(self._entries._ids == search_id)[0].tolist()
                    matched_indices = hex_matches
                except ValueError:
                    pass

                # If no hex match, search by name/path substring
                if not matched_indices:
                    ids_arr = self._entries._ids[:len(self._entries)]
                    for i in range(len(self._entries)):
                        path = self._lookup.full_path(int(ids_arr[i]))
                        if text in path.lower():
                            matched_indices.append(i)
            else:
                matched_indices = list(range(len(self._entries)))

            # Apply extension filter
            if ext_flt:
                ids_arr = self._entries._ids[:len(self._entries)]
                matched_indices = [
                    i for i in matched_indices
                    if self._lookup.full_path(int(ids_arr[i])).endswith(ext_flt)
                ]

            # Build filtered list
            filtered = [self._entries[i] for i in matched_indices]
            self._build_filtered_tree(filtered)
            self._status.setText(f"{len(filtered):,} of {len(self._entries):,} assets")

        else:
            # Fallback: plain list filter by hex ID
            if not text and not ext_flt:
                self._rebuild_tree(self._entries)
                self._status.setText(f"{len(self._entries):,} assets loaded")
                return
            filtered = []
            for e in self._entries:
                if text and text not in f"{e.asset_id:016x}":
                    continue
                filtered.append(e)
            self._build_filtered_tree(filtered)
            self._status.setText(f"{len(filtered):,} of {len(self._entries):,} assets")

    def _compute_groups(self, entries):
        """Re-compute archive groups for the given entries."""
        from core.archive import _LazyEntryList
        import numpy as np
        if isinstance(entries, _LazyEntryList):
            arc_col     = entries._sizes['archive'][:len(entries)].astype(np.int32)
            sort_idx    = np.argsort(arc_col, kind='stable')
            sorted_arcs = arc_col[sort_idx]
            if len(sorted_arcs) == 0:
                return []
            boundaries  = np.where(np.diff(sorted_arcs))[0] + 1
            starts = np.concatenate([[0], boundaries])
            ends   = np.concatenate([boundaries, [len(sort_idx)]])
            return [(int(sorted_arcs[s]), sort_idx[s:e].tolist())
                    for s, e in zip(starts, ends)]
        else:
            groups = {}
            for i, e in enumerate(entries):
                groups.setdefault(e.archive, []).append(i)
            return sorted(groups.items())

    def _build_filtered_tree(self, entries: list):
        """Build a flat tree for filtered results (not grouped by archive)."""
        self._tree.clear()
        self._tree.setUpdatesEnabled(False)

        # Group by extension for filtered results
        groups = {}
        for e in entries:
            if self._lookup and self._lookup.is_loaded():
                path = self._lookup.full_path(e.asset_id)
                ext  = '.' + path.rsplit('.', 1)[-1] if '.' in path else 'unknown'
            else:
                ext = 'unknown'
            groups.setdefault(ext, []).append(e)

        for ext in sorted(groups.keys()):
            items = groups[ext]
            group_item = QTreeWidgetItem(self._tree)
            group_item.setText(0, f"{ext}  ({len(items):,})")
            group_item.setForeground(0, QColor('#5dade2'))
            f = group_item.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            f.setPointSize(9)
            group_item.setFont(0, f)
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group_item.setExpanded(True)

            for entry in items:
                child = QTreeWidgetItem(group_item)
                if self._lookup and self._lookup.is_loaded():
                    display = self._lookup.name(entry.asset_id)
                    full    = self._lookup.full_path(entry.asset_id)
                else:
                    display = f"{entry.asset_id:016X}"
                    full    = display
                child.setText(0, display)
                child.setData(0, Qt.ItemDataRole.UserRole, entry)
                child.setToolTip(0, (
                    f"ID:      {entry.asset_id:#018x}\n"
                    f"Path:    {full}\n"
                    f"Archive: {entry.archive}\n"
                    f"Offset:  {entry.offset:#010x}\n"
                    f"Size:    {entry.size:,} bytes"
                ))

        self._tree.setUpdatesEnabled(True)

    def _on_double_click(self, item: QTreeWidgetItem, col: int):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry:
            self.asset_activated.emit(entry)
