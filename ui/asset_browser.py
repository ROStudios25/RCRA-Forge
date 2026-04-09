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
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread, QObject
from PyQt6.QtGui import QIcon, QColor, QFont

from core.archive import AssetEntry, ASSET_TYPE_NAMES
from core.grouping import build_groups, filter_groups, AssetGroup


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


class SearchWorker(QObject):
    """Runs asset name scanning on a background thread."""
    results_ready = pyqtSignal(list, str)   # (matched_indices, ext_flt)
    error         = pyqtSignal(str)

    def __init__(self, entries, lookup, tokens, raw_text: str, ext_flt):
        super().__init__()
        self.entries  = entries
        self.lookup   = lookup
        self.tokens   = tokens
        self.raw_text = raw_text
        self.ext_flt  = ext_flt

    def run(self):
        try:
            import numpy as np
            from core.archive import _LazyEntryList

            entries  = self.entries
            lookup   = self.lookup
            tokens   = self.tokens
            raw_text = self.raw_text
            ext_flt  = self.ext_flt

            matched_indices = []

            if raw_text:
                # 1. Exact hex ID match
                hex_matched = False
                if len(tokens) == 1:
                    try:
                        search_id = int(raw_text, 16)
                        ids_arr   = entries._ids[:len(entries)]
                        hits = np.where(ids_arr == search_id)[0].tolist()
                        if hits:
                            matched_indices = hits
                            hex_matched = True
                    except (ValueError, AttributeError):
                        pass

                if not hex_matched:
                    # Multi-token AND match: every token must appear in the path.
                    # Tokens are already correctly built by _apply_filter.
                    ids_arr = entries._ids[:len(entries)] if isinstance(entries, _LazyEntryList) else None

                    n = len(entries)
                    for i in range(n):
                        aid = int(ids_arr[i]) if ids_arr is not None else entries[i].asset_id
                        path = lookup.full_path(aid)
                        if all(tok in path for tok in tokens):
                            matched_indices.append(i)

            else:
                matched_indices = list(range(len(entries)))

            # Extension filter
            if ext_flt:
                ids_arr = entries._ids[:len(entries)] if isinstance(entries, _LazyEntryList) else None
                if ids_arr is not None:
                    matched_indices = [
                        i for i in matched_indices
                        if lookup.full_path(int(ids_arr[i])).endswith(ext_flt)
                    ]
                else:
                    matched_indices = [
                        i for i in matched_indices
                        if lookup.full_path(entries[i].asset_id).endswith(ext_flt)
                    ]

            self.results_ready.emit(matched_indices, ext_flt or '')
        except Exception as ex:
            import traceback
            self.error.emit(f"{ex}\n{traceback.format_exc()}")


class AssetBrowser(QWidget):
    # Emitted when user double-clicks an asset
    asset_activated = pyqtSignal(object)   # AssetEntry
    # Emitted when user double-clicks a group (for batch export)
    group_activated = pyqtSignal(object)   # AssetGroup

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[AssetEntry] = []
        self._lookup  = None
        self._groups: list[AssetGroup] = []
        self._ungrouped: list = []
        self._groups_mode: bool = False
        self._search_thread: QThread = None
        self._search_worker = None
        # Debounce timer — fires 200 ms after the user stops typing
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._apply_filter)
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

        self._btn_groups = QPushButton("⬡  Groups")
        self._btn_groups.setObjectName("GroupsToggleBtn")
        self._btn_groups.setCheckable(True)
        self._btn_groups.setChecked(False)
        self._btn_groups.setFixedHeight(24)
        self._btn_groups.setToolTip(
            "Group assets by shared name prefix.\n"
            "Enemy/character models are grouped together for easy batch export."
        )
        self._btn_groups.toggled.connect(self._on_groups_toggled)
        hlayout.addWidget(self._btn_groups)

        layout.addWidget(header)

        # ── Filter bar ────────────────────────────────────────────────────────
        filter_frame = QFrame()
        filter_frame.setObjectName("FilterBar")
        flayout = QHBoxLayout(filter_frame)
        flayout.setContentsMargins(6, 4, 6, 4)
        flayout.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search assets… (space = AND, e.g. enm chunk)")
        self._search.setObjectName("SearchBox")
        self._search.textChanged.connect(lambda: self._debounce.start())
        flayout.addWidget(self._search)

        self._type_filter = QComboBox()
        self._type_filter.setObjectName("TypeFilter")
        self._type_filter.addItem("All Types")
        # Real extensions from hashes.txt — populated after hashes load
        for t in ['.model', '.texture', '.zone', '.animclip', '.material',
                  '.actor', '.config', '.visualeffect', '.level', '.soundbank']:
            self._type_filter.addItem(t)
        self._type_filter.setFixedWidth(100)
        self._type_filter.currentTextChanged.connect(lambda: self._debounce.start())
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
        # Rebuild groups now that we have names
        if self._groups_mode and self._entries:
            self._rebuild_groups_tree()
            return
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

    # ── Groups mode ───────────────────────────────────────────────────────────

    def _on_groups_toggled(self, checked: bool):
        self._groups_mode = checked
        if checked:
            self._btn_groups.setText("⬡  Groups  ✓")
            # If there's an active search, re-run it in groups mode
            if self._search.text().strip() or self._type_filter.currentText() != "All Types":
                self._apply_filter()
            else:
                self._rebuild_groups_tree()
        else:
            self._btn_groups.setText("⬡  Groups")
            # If there's an active search, keep showing search results (just ungrouped)
            if self._search.text().strip() or self._type_filter.currentText() != "All Types":
                self._apply_filter()
            else:
                self._rebuild_tree(self._entries)
                self._status.setText(f"{len(self._entries):,} assets loaded")

    def _rebuild_groups_tree(self):
        """Build the name-prefix group tree. Requires a loaded lookup."""
        if not self._lookup or not self._lookup.is_loaded():
            self._status.setText("⚠  Load a game folder first to enable Groups view")
            return

        self._groups, self._ungrouped = build_groups(self._entries, self._lookup)

        self._tree.clear()
        self._tree.setUpdatesEnabled(False)

        # ── Grouped section ───────────────────────────────────────────────
        for group in self._groups:
            self._add_named_group_item(group)

        # ── Ungrouped (singleton) assets ──────────────────────────────────
        if self._ungrouped:
            solo_item = QTreeWidgetItem(self._tree)
            solo_item.setText(0, f"Ungrouped  ({len(self._ungrouped):,})")
            solo_item.setForeground(0, QColor('#95a5a6'))
            f = solo_item.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            f.setPointSize(9)
            solo_item.setFont(0, f)
            solo_item.setFlags(solo_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            # Lazy-load children
            solo_item.setData(0, Qt.ItemDataRole.UserRole + 2, self._ungrouped)
            QTreeWidgetItem(solo_item).setText(0, "  Loading…")

        self._tree.setUpdatesEnabled(True)

        try:
            self._tree.itemExpanded.disconnect(self._on_group_expanded)
        except Exception:
            pass
        try:
            self._tree.itemExpanded.disconnect(self._on_named_group_expanded)
        except Exception:
            pass
        self._tree.itemExpanded.connect(self._on_named_group_expanded)

        n_groups = len(self._groups)
        self._status.setText(
            f"{len(self._entries):,} assets  ·  {n_groups} groups  ·  "
            f"{len(self._ungrouped)} ungrouped"
        )

    def _add_named_group_item(self, group: AssetGroup):
        """Add a collapsible group row for one AssetGroup."""
        item = QTreeWidgetItem(self._tree)
        # Icon + label
        item.setText(0, f"  {group.display_name}  ({group.count})")
        item.setForeground(0, QColor('#f0a500'))   # amber — distinct from archive rows
        f = item.font(0)
        f.setWeight(QFont.Weight.DemiBold)
        f.setPointSize(9)
        item.setFont(0, f)
        item.setToolTip(0,
            f"Group: {group.slug}\n"
            f"Parts:  {group.count} assets\n"
            f"Dir:    {group.directory}\n"
            f"Double-click to batch export all parts as one GLB"
        )
        # Store the group object for expand + double-click
        item.setData(0, Qt.ItemDataRole.UserRole + 3, group)
        # Placeholder child so the expand arrow shows
        QTreeWidgetItem(item).setText(0, "  Loading…")

    def _on_named_group_expanded(self, item: QTreeWidgetItem):
        """Lazy-populate children of a named group or the 'Ungrouped' bucket."""
        # Named group
        group: AssetGroup = item.data(0, Qt.ItemDataRole.UserRole + 3)
        if group is not None:
            item.takeChildren()
            self._tree.setUpdatesEnabled(False)
            for entry in group.entries:
                child = QTreeWidgetItem(item)
                display = self._lookup.name(entry.asset_id) if self._lookup and self._lookup.is_loaded() else f"{entry.asset_id:016X}"
                full    = self._lookup.full_path(entry.asset_id) if self._lookup and self._lookup.is_loaded() else display
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
            item.setData(0, Qt.ItemDataRole.UserRole + 3, None)
            # Keep the group reference in a different role for double-click
            item.setData(0, Qt.ItemDataRole.UserRole + 4, group)
            return

        # Ungrouped bucket
        solo_entries = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if solo_entries is not None:
            item.takeChildren()
            self._tree.setUpdatesEnabled(False)
            for entry in solo_entries:
                child = QTreeWidgetItem(item)
                display = self._lookup.name(entry.asset_id) if self._lookup and self._lookup.is_loaded() else f"{entry.asset_id:016X}"
                full    = self._lookup.full_path(entry.asset_id) if self._lookup and self._lookup.is_loaded() else display
                child.setText(0, display)
                child.setData(0, Qt.ItemDataRole.UserRole, entry)
                child.setToolTip(0, f"ID: {entry.asset_id:#018x}\nPath: {full}")
            self._tree.setUpdatesEnabled(True)
            item.setData(0, Qt.ItemDataRole.UserRole + 2, None)

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
        import re as _re
        raw_text = self._search.text().lower().strip()
        ext_flt  = self._type_filter.currentText()
        if ext_flt == "All Types":
            ext_flt = None

        from core.archive import _LazyEntryList

        # Reset to full view when nothing typed
        if not raw_text and not ext_flt:
            if isinstance(self._entries, _LazyEntryList):
                self.load_entries_grouped(
                    self._entries,
                    self._compute_groups(self._entries),
                    self._lookup
                )
            else:
                self._rebuild_tree(self._entries)
            self._status.setText(f"{len(self._entries):,} assets loaded")
            return

        if not self._lookup or not self._lookup.is_loaded():
            # No names loaded — fall back to hex filter only
            filtered = [e for e in self._entries
                        if not raw_text or raw_text in f"{e.asset_id:016x}"]
            self._build_filtered_tree(filtered, ext_flt=ext_flt)
            self._status.setText(f"{len(filtered):,} of {len(self._entries):,} assets")
            return

        # Tokenise: split on whitespace OR underscore, keep tokens >= 2 chars.
        # The raw_text itself is always kept as an extra token so that a word
        # like "sargasso" is matched even if it contains underscores internally.
        tokens = list({t for t in _re.split(r'[\s_]+', raw_text) if len(t) >= 2})
        if not tokens:
            tokens = [raw_text]
        # Only add raw_text as an extra token when it's a single word with no
        # spaces or underscores — this covers prefix searches like "sar" → "sargasso"
        # but does NOT add "enm_chunk" as a literal token (which would never match
        # since paths use underscores as separators, not as part of adjacent words).
        if '_' not in raw_text and ' ' not in raw_text and raw_text not in tokens:
            tokens.append(raw_text)

        # Show immediate feedback
        self._status.setText("Searching…")

        # Cancel any in-flight search
        if self._search_thread and self._search_thread.isRunning():
            self._search_thread.quit()
            self._search_thread.wait(150)

        self._search_thread = QThread(self)
        self._search_worker = SearchWorker(
            self._entries, self._lookup, tokens, raw_text, ext_flt
        )
        self._search_worker.moveToThread(self._search_thread)
        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.results_ready.connect(self._on_search_done)
        self._search_worker.error.connect(
            lambda msg: self._status.setText(f"Search error: {msg[:80]}")
        )
        self._search_worker.results_ready.connect(self._search_thread.quit)
        self._search_worker.error.connect(self._search_thread.quit)
        self._search_thread.start()

    def _on_search_done(self, matched_indices: list, ext_flt: str):
        """Called on the main thread when the background search finishes."""
        ext_flt  = ext_flt or None
        filtered = [self._entries[i] for i in matched_indices]
        self._build_filtered_tree(filtered, ext_flt=ext_flt)
        from core.grouping import build_groups as _bg
        if ext_flt and filtered:
            grps, ungrp = _bg(filtered, self._lookup)
            if grps:
                self._status.setText(
                    f"{len(filtered):,} results  ·  {len(grps)} groups  ·  {len(ungrp)} other"
                )
                return
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

    def _build_filtered_tree(self, entries: list, ext_flt: str = None):
        """
        Build the filtered-results tree.

        Slug grouping is only applied when a specific file format is selected
        (ext_flt is set) OR Groups mode is explicitly ON — this prevents
        irrelevant file types from appearing inside groups when searching
        across all asset types.
        """
        self._tree.clear()
        self._tree.setUpdatesEnabled(False)

        has_lookup = self._lookup and self._lookup.is_loaded()
        allow_grouping = self._groups_mode or bool(ext_flt)

        # ── Attempt slug-based grouping ───────────────────────────────────────
        if has_lookup and entries and allow_grouping:
            groups, ungrouped = build_groups(entries, self._lookup)
        else:
            groups, ungrouped = [], list(entries)

        use_slug_groups = allow_grouping and len(groups) > 0

        if use_slug_groups:
            # ── Slug-grouped display ──────────────────────────────────────────
            for group in groups:
                self._add_named_group_item(group)

            # Singletons shown flat underneath
            if ungrouped:
                if groups:
                    # Separate header for ungrouped results
                    solo_hdr = QTreeWidgetItem(self._tree)
                    solo_hdr.setText(0, f"Other  ({len(ungrouped):,})")
                    solo_hdr.setForeground(0, QColor('#95a5a6'))
                    f = solo_hdr.font(0)
                    f.setWeight(QFont.Weight.DemiBold)
                    f.setPointSize(9)
                    solo_hdr.setFont(0, f)
                    solo_hdr.setFlags(solo_hdr.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    solo_hdr.setExpanded(True)
                    parent = solo_hdr
                else:
                    parent = self._tree   # no groups at all — use root

                for entry in ungrouped:
                    child = QTreeWidgetItem(parent)
                    display = self._lookup.name(entry.asset_id) if has_lookup else f"{entry.asset_id:016X}"
                    full    = self._lookup.full_path(entry.asset_id) if has_lookup else display
                    child.setText(0, display)
                    child.setData(0, Qt.ItemDataRole.UserRole, entry)
                    child.setToolTip(0, (
                        f"ID:      {entry.asset_id:#018x}\n"
                        f"Path:    {full}\n"
                        f"Archive: {entry.archive}\n"
                        f"Offset:  {entry.offset:#010x}\n"
                        f"Size:    {entry.size:,} bytes"
                    ))

            # Wire expand handler for the named group rows
            try:
                self._tree.itemExpanded.disconnect(self._on_group_expanded)
            except Exception:
                pass
            try:
                self._tree.itemExpanded.disconnect(self._on_named_group_expanded)
            except Exception:
                pass
            self._tree.itemExpanded.connect(self._on_named_group_expanded)

        else:
            # ── Flat extension-bucket fallback (no groups found) ──────────────
            ext_buckets: dict[str, list] = {}
            for e in entries:
                if has_lookup:
                    path = self._lookup.full_path(e.asset_id)
                    ext  = '.' + path.rsplit('.', 1)[-1] if '.' in path else 'unknown'
                else:
                    ext = 'unknown'
                ext_buckets.setdefault(ext, []).append(e)

            for ext in sorted(ext_buckets.keys()):
                items = ext_buckets[ext]
                bucket_item = QTreeWidgetItem(self._tree)
                bucket_item.setText(0, f"{ext}  ({len(items):,})")
                bucket_item.setForeground(0, QColor('#5dade2'))
                f = bucket_item.font(0)
                f.setWeight(QFont.Weight.DemiBold)
                f.setPointSize(9)
                bucket_item.setFont(0, f)
                bucket_item.setFlags(bucket_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                bucket_item.setExpanded(True)

                for entry in items:
                    child = QTreeWidgetItem(bucket_item)
                    display = self._lookup.name(entry.asset_id) if has_lookup else f"{entry.asset_id:016X}"
                    full    = self._lookup.full_path(entry.asset_id) if has_lookup else display
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
            return
        # Double-click on a named group header → batch export
        group = item.data(0, Qt.ItemDataRole.UserRole + 4)
        if group:
            self.group_activated.emit(group)
