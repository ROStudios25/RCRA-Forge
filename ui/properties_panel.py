"""
ui/properties_panel.py
Right-side properties and export panel for RCRA Forge.

Shows info about the currently selected asset and provides one-click
export to .glb, .gltf, .obj, or .dds.
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QFileDialog, QFrame, QProgressBar,
    QComboBox, QCheckBox, QSizePolicy, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject
from PyQt6.QtGui import QFont, QColor

from core.archive import AssetEntry


class ExportWorker(QObject):
    """Run export on a background thread."""
    finished = pyqtSignal(str)      # output path on success
    error    = pyqtSignal(str)      # error message

    def __init__(self, mesh_asset, path: str, fmt: str, lod: int = 0):
        super().__init__()
        self.mesh  = mesh_asset
        self.path  = path
        self.fmt   = fmt
        self.lod   = lod

    def run(self):
        try:
            from exporters.gltf_exporter import GltfExporter, ObjExporter
            name = os.path.splitext(os.path.basename(self.path))[0]
            if self.fmt == 'glb':
                GltfExporter(self.mesh, name, lod=self.lod).export_glb(self.path)
            elif self.fmt == 'gltf':
                GltfExporter(self.mesh, name, lod=self.lod).export_gltf(self.path)
            elif self.fmt == 'obj':
                ObjExporter(self.mesh, name, lod=self.lod).export(self.path)
            self.finished.emit(self.path)
        except Exception as ex:
            import traceback
            self.error.emit(f"{ex}\n{traceback.format_exc()}")


class GroupExportWorker(QObject):
    """
    Load every asset in a group, then combine them into one GLB.
    Runs on a background thread — emits progress per part.
    """
    progress = pyqtSignal(str)    # status message
    finished = pyqtSignal(str)    # output path on success
    error    = pyqtSignal(str)    # error message

    def __init__(self, group, toc_parser, path: str):
        """
        Parameters
        ----------
        group      : AssetGroup  (from core.grouping)
        toc_parser : TocParser   already-parsed TOC (shared, do not re-parse)
        path       : str         output .glb path
        """
        super().__init__()
        self.group      = group
        self.toc_parser = toc_parser
        self.path       = path

    def run(self):
        try:
            from exporters.group_exporter import GroupExporter
            from core.mesh import ModelParser

            exporter = GroupExporter(slug=self.group.slug.rsplit('/', 1)[-1])
            n = len(self.group.entries)

            for i, entry in enumerate(self.group.entries):
                # Derive part name from the asset id / path
                try:
                    from core.hashes import get_lookup
                    lk = get_lookup()
                    if lk and lk.is_loaded():
                        full = lk.full_path(entry.asset_id)
                        part_name = full.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                    else:
                        part_name = f"part_{i:03d}"
                except Exception:
                    part_name = f"part_{i:03d}"

                self.progress.emit(f"Loading part {i+1}/{n}: {part_name}…")

                try:
                    raw = self.toc_parser.extract_asset(entry)
                    model = ModelParser(raw).parse()
                    exporter.add_model(model, part_name)
                except Exception as ex:
                    self.progress.emit(f"  ⚠ Skipped {part_name}: {ex}")
                    continue

            self.progress.emit(f"Writing GLB ({n} parts)…")
            exporter.export_glb(self.path)
            self.finished.emit(self.path)

        except Exception as ex:
            import traceback
            self.error.emit(f"{ex}\n{traceback.format_exc()}")



class PropertiesPanel(QWidget):
    request_export = pyqtSignal(str, str)   # (output_path, format_string)
    lod_changed    = pyqtSignal(int)        # emitted when user picks a different LOD

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry:      AssetEntry  = None
        self._asset_name: str        = None
        self._mesh_asset             = None
        self._group                  = None   # AssetGroup for batch export
        self._archive_path: str      = None   # path to game 'toc' file
        self._toc_parser             = None   # shared TocParser (set after TOC load)
        self._export_thread: QThread = None
        self._export_worker          = None   # keeps ExportWorker alive during thread run
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setObjectName("BrowserHeader")
        hdr.setFixedHeight(36)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel("PROPERTIES")
        lbl.setObjectName("PanelTitle")
        hl.addWidget(lbl)
        layout.addWidget(hdr)

        # ── Info group ─────────────────────────────────────────────────────
        info_group = QGroupBox("Asset Info")
        info_group.setObjectName("PropsGroup")
        form = QFormLayout(info_group)
        form.setSpacing(4)
        form.setContentsMargins(8, 12, 8, 8)

        self._lbl_id   = self._field_label()
        self._lbl_type = self._field_label()
        self._lbl_size = self._field_label()
        self._lbl_wad  = self._field_label()
        self._lbl_off  = self._field_label()

        form.addRow("Asset ID:", self._lbl_id)
        form.addRow("Type:",     self._lbl_type)
        form.addRow("Size:",     self._lbl_size)
        form.addRow("WAD:",      self._lbl_wad)
        form.addRow("Offset:",   self._lbl_off)

        layout.addWidget(info_group)
        layout.addSpacing(4)

        # ── Mesh stats ─────────────────────────────────────────────────────
        self._mesh_group = QGroupBox("Mesh Statistics")
        self._mesh_group.setObjectName("PropsGroup")
        mform = QFormLayout(self._mesh_group)
        mform.setSpacing(4)
        mform.setContentsMargins(8, 12, 8, 8)

        self._lbl_verts  = self._field_label()
        self._lbl_tris   = self._field_label()
        self._lbl_submsh = self._field_label()
        self._lbl_lods   = self._field_label()
        self._lbl_bones  = self._field_label()

        mform.addRow("Vertices:",   self._lbl_verts)
        mform.addRow("Triangles:",  self._lbl_tris)
        mform.addRow("Sub-meshes:", self._lbl_submsh)
        mform.addRow("LOD levels:", self._lbl_lods)
        mform.addRow("Bones:",      self._lbl_bones)

        self._mesh_group.setVisible(False)
        layout.addWidget(self._mesh_group)
        layout.addSpacing(4)

        # ── Export group ────────────────────────────────────────────────────
        exp_group = QGroupBox("Export")
        exp_group.setObjectName("PropsGroup")
        elayout = QVBoxLayout(exp_group)
        elayout.setContentsMargins(8, 12, 8, 8)
        elayout.setSpacing(6)

        # Format selector
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._fmt_combo = QComboBox()
        self._fmt_combo.setObjectName("FmtCombo")
        self._fmt_combo.addItems([
            "GLB (Blender/glTF binary)",
            "GLTF (text + .bin)",
            "OBJ (Wavefront)",
            "FBX (Binary, Maya/3ds Max/Blender)",
        ])
        fmt_row.addWidget(self._fmt_combo)
        elayout.addLayout(fmt_row)

        # LOD selector
        lod_row = QHBoxLayout()
        lod_row.addWidget(QLabel("LOD:"))
        self._lod_combo = QComboBox()
        self._lod_combo.setObjectName("FmtCombo")
        self._lod_combo.addItem("LOD 0  (highest)")
        self._lod_combo.setEnabled(False)
        self._lod_combo.setToolTip("Select which Level of Detail to view and export")
        self._lod_combo.currentIndexChanged.connect(self._on_lod_changed)
        lod_row.addWidget(self._lod_combo)
        elayout.addLayout(lod_row)

        # Export button
        self._btn_export = QPushButton("⬇  Export Asset")
        self._btn_export.setObjectName("ExportBtn")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._do_export)
        elayout.addWidget(self._btn_export)

        # ── Group export ─────────────────────────────────────────────────────
        from PyQt6.QtWidgets import QFrame as _QFrame
        sep = _QFrame()
        sep.setFrameShape(_QFrame.Shape.HLine)
        sep.setFrameShadow(_QFrame.Shadow.Sunken)
        elayout.addWidget(sep)

        self._group_info = QLabel("No group selected")
        self._group_info.setObjectName("FieldValue")
        self._group_info.setWordWrap(True)
        elayout.addWidget(self._group_info)

        self._btn_export_group = QPushButton("⬡  Export Group as GLB")
        self._btn_export_group.setObjectName("ExportBtn")
        self._btn_export_group.setEnabled(False)
        self._btn_export_group.setToolTip(
            "Export all parts of the selected group into a single GLB.\n"
            "Each part becomes a separate named mesh node in Blender."
        )
        self._btn_export_group.clicked.connect(self._do_export_group)
        elayout.addWidget(self._btn_export_group)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        elayout.addWidget(self._progress)

        # Status
        self._export_status = QLabel("")
        self._export_status.setObjectName("ExportStatus")
        self._export_status.setWordWrap(True)
        elayout.addWidget(self._export_status)

        layout.addWidget(exp_group)

        # ── Export List ────────────────────────────────────────────────────────
        list_group = QGroupBox("Export List")
        list_group.setObjectName("PropsGroup")
        llayout = QVBoxLayout(list_group)
        llayout.setContentsMargins(8, 12, 8, 8)
        llayout.setSpacing(4)

        list_hint = QLabel("Right-click any asset to add it here.")
        list_hint.setObjectName("FieldValue")
        list_hint.setWordWrap(True)
        llayout.addWidget(list_hint)

        from PyQt6.QtWidgets import QListWidget
        self._export_list_widget = QListWidget()
        self._export_list_widget.setObjectName("ExportListWidget")
        self._export_list_widget.setMaximumHeight(120)
        self._export_list_widget.setToolTip("Assets queued for combined GLB export.\nRight-click an asset in the browser to add or remove.")
        llayout.addWidget(self._export_list_widget)

        list_btn_row = QHBoxLayout()
        self._btn_clear_list = QPushButton("Clear List")
        self._btn_clear_list.setObjectName("SmallBtn")
        self._btn_clear_list.setEnabled(False)
        self._btn_clear_list.clicked.connect(self._do_clear_export_list)
        list_btn_row.addWidget(self._btn_clear_list)
        list_btn_row.addStretch()
        llayout.addLayout(list_btn_row)

        self._btn_export_list = QPushButton("⬇  Export List as GLB")
        self._btn_export_list.setObjectName("ExportBtn")
        self._btn_export_list.setEnabled(False)
        self._btn_export_list.setToolTip(
            "Export all queued assets into a single GLB.\n"
            "Each asset becomes a named node under a shared root."
        )
        self._btn_export_list.clicked.connect(self._do_export_list)
        llayout.addWidget(self._btn_export_list)

        self._list_status = QLabel("")
        self._list_status.setObjectName("ExportStatus")
        self._list_status.setWordWrap(True)
        llayout.addWidget(self._list_status)

        layout.addWidget(list_group)
        layout.addStretch()

        # ── Log / notes ────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setObjectName("LogBox")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        self._log.setPlaceholderText("Export log…")
        layout.addWidget(self._log)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_entry(self, entry: AssetEntry, name: str = None):
        self._entry = entry
        self._asset_name = name  # display name from lookup (e.g. 'hero_ratchet')
        self._lbl_id.setText(f"{entry.asset_id:016X}")
        self._lbl_type.setText(f"archive {entry.archive}")
        self._lbl_size.setText(f"{entry.size:,} bytes")
        self._lbl_wad.setText(f"archive_{entry.archive:03d}")
        self._lbl_off.setText(f"{entry.offset:#010x}")
        self._mesh_group.setVisible(False)
        self._btn_export.setEnabled(False)

    def set_mesh_asset(self, model_asset):
        self._mesh_asset = model_asset
        if model_asset is None:
            self._mesh_group.setVisible(False)
            self._btn_export.setEnabled(False)
            return

        from core.mesh import mesh_to_numpy
        total_verts = 0
        total_tris  = 0
        for mesh in model_asset.meshes:
            pos, _, _, idx = mesh_to_numpy(model_asset, mesh)
            if pos is not None:
                total_verts += len(pos)
            if idx is not None:
                total_tris += len(idx) // 3

        self._lbl_verts.setText(f"{total_verts:,}")
        self._lbl_tris.setText(f"{total_tris:,}")
        self._lbl_submsh.setText(str(len(model_asset.meshes)))
        self._lbl_lods.setText(str(getattr(model_asset, 'lod_count', 1)))
        self._lbl_bones.setText(str(len(model_asset.joints)))
        self._mesh_group.setVisible(True)
        self._btn_export.setEnabled(True)

        # Populate LOD selector
        self._lod_combo.blockSignals(True)
        self._lod_combo.clear()
        lod_count = getattr(model_asset, 'lod_count', 1)
        for i in range(lod_count):
            label = "highest detail" if i == 0 else f"lower detail"
            self._lod_combo.addItem(f"LOD {i}  ({label})")
        self._lod_combo.setCurrentIndex(0)
        self._lod_combo.setEnabled(lod_count > 1)
        self._lod_combo.blockSignals(False)

    def set_archive_path(self, path: str):
        """Store the loaded toc path (kept for legacy callers)."""
        self._archive_path = path

    def set_toc_parser(self, parser, path: str):
        """Store the shared TocParser and toc path after TOC load."""
        self._toc_parser   = parser
        self._archive_path = path

    def add_to_export_list(self, entry, name: str = None):
        """Add an asset entry to the export list panel."""
        # Check for duplicates
        for i in range(self._export_list_widget.count()):
            item = self._export_list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole).asset_id == entry.asset_id:
                return  # already in list
        from PyQt6.QtWidgets import QListWidgetItem
        display = name or f"{entry.asset_id:016X}"
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setToolTip(f"ID: {entry.asset_id:#018x}")
        self._export_list_widget.addItem(item)
        self._btn_clear_list.setEnabled(True)
        self._btn_export_list.setEnabled(True)
        self._list_status.setText(f"{self._export_list_widget.count()} asset(s) queued")

    def remove_from_export_list(self, entry):
        """Remove an asset entry from the export list panel."""
        for i in range(self._export_list_widget.count()):
            item = self._export_list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole).asset_id == entry.asset_id:
                self._export_list_widget.takeItem(i)
                break
        count = self._export_list_widget.count()
        self._btn_clear_list.setEnabled(count > 0)
        self._btn_export_list.setEnabled(count > 0)
        self._list_status.setText(f"{count} asset(s) queued" if count > 0 else "")

    def get_export_list_entries(self):
        """Return all AssetEntry objects currently in the export list."""
        return [
            self._export_list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._export_list_widget.count())
        ]

    def clear_export_list(self):
        """Clear the export list programmatically."""
        self._export_list_widget.clear()
        self._btn_clear_list.setEnabled(False)
        self._btn_export_list.setEnabled(False)
        self._list_status.setText("")

    def set_group(self, group):
        """Populate the group export section with *group* info."""
        self._group = group
        name = group.slug.rsplit('/', 1)[-1]
        self._group_info.setText(
            f"<b>{name}</b><br>"
            f"<span style='color:#95a5a6'>{group.count} parts · {group.directory or 'root'}</span>"
        )
        self._group_info.setTextFormat(Qt.TextFormat.RichText)
        self._btn_export_group.setEnabled(True)
        self._btn_export_group.setText(f"⬡  Export Group  ({group.count} parts)")

    def log(self, msg: str):
        self._log.append(msg)

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_lod_changed(self, index: int):
        self.lod_changed.emit(index)

    def _do_clear_export_list(self):
        self.clear_export_list()

    def _do_export_list(self):
        entries = self.get_export_list_entries()
        if not entries:
            return
        if not self._archive_path:
            self._list_status.setText("✗ No archive loaded — open a game folder first")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export List as GLB", "export_list.glb",
            "GLB Files (*.glb);;All Files (*.*)"
        )
        if not path:
            return

        self._btn_export_list.setEnabled(False)
        self._btn_clear_list.setEnabled(False)
        self._list_status.setText("Exporting…")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            from core.archive import TocParser
            from core.mesh import ModelParser
            from exporters.group_exporter import GroupExporter

            # Use parse() to properly read the TOC file before extracting assets
            toc = TocParser(self._archive_path)
            toc.parse()

            exporter = GroupExporter()
            for entry in entries:
                try:
                    raw = toc.extract_asset(entry)
                    model = ModelParser(raw).parse()
                    name = f"asset_{entry.asset_id:016X}"
                    # Try to get a display name from the list widget
                    for i in range(self._export_list_widget.count()):
                        item = self._export_list_widget.item(i)
                        if item.data(Qt.ItemDataRole.UserRole).asset_id == entry.asset_id:
                            name = item.text().rsplit('.', 1)[0]
                            break
                    exporter.add_model(model, name)
                except Exception as ex:
                    self._list_status.setText(f"✗ Failed on {entry.asset_id:#018x}: {ex}")
                    self._btn_export_list.setEnabled(True)
                    self._btn_clear_list.setEnabled(True)
                    return

            exporter.export_glb(path)
            import os
            self._list_status.setText(f"✓ Exported → {os.path.basename(path)}")
            self.log(f"[LIST OK] {path}")

        except Exception as ex:
            import traceback
            self._list_status.setText(f"✗ Export failed: {ex}")
            self.log(f"[LIST ERROR] {ex}\n{traceback.format_exc()}")
        finally:
            self._btn_export_list.setEnabled(True)
            self._btn_clear_list.setEnabled(self._export_list_widget.count() > 0)

    def _do_export_group(self):
        if not self._group:
            return
        if not self._archive_path:
            self._export_status.setText("✗ No archive loaded — open a game folder first")
            return

        name = self._group.slug.rsplit('/', 1)[-1]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Group as GLB", name + ".glb", "GLB Files (*.glb);;All Files (*.*)"
        )
        if not path:
            return

        self._btn_export_group.setEnabled(False)
        self._btn_export.setEnabled(False)
        self._progress.setVisible(True)
        self._export_status.setText("Starting group export…")

        self._export_thread = QThread(self)
        worker = GroupExportWorker(self._group, self._toc_parser, path)
        worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(worker.run)
        worker.progress.connect(self._export_status.setText)
        worker.finished.connect(self._on_group_export_done)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._export_thread.quit)
        worker.error.connect(self._export_thread.quit)
        self._export_thread.start()

    def _on_group_export_done(self, path: str):
        self._progress.setVisible(False)
        self._btn_export_group.setEnabled(True)
        self._btn_export.setEnabled(self._mesh_asset is not None)
        self._export_status.setText(f"✓ Group exported → {os.path.basename(path)}")
        self.log(f"[GROUP OK] {path}")

    def _do_export(self):
        if self._mesh_asset is None:
            return

        fmt_map = {0: 'glb', 1: 'gltf', 2: 'obj', 3: 'fbx'}
        fmt = fmt_map[self._fmt_combo.currentIndex()]
        ext = {
            'glb':  '.glb',
            'gltf': '.gltf',
            'obj':  '.obj',
            'fbx':  '.fbx',
        }[fmt]

        # Use asset name from browser if available, fall back to hex ID
        if self._asset_name:
            name = os.path.splitext(self._asset_name)[0]
        elif self._entry:
            name = f"{self._entry.asset_id:016X}"
        else:
            name = "export"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Asset", name + ext,
            f"3D Files (*{ext});;All Files (*.*)"
        )
        if not path:
            return

        self._btn_export.setEnabled(False)
        self._progress.setVisible(True)
        self._export_status.setText("Exporting…")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            from exporters.gltf_exporter import GltfExporter, ObjExporter
            lod  = self._lod_combo.currentIndex()
            stem = os.path.splitext(os.path.basename(path))[0]
            if fmt == 'glb':
                GltfExporter(self._mesh_asset, stem, lod=lod).export_glb(path)
            elif fmt == 'gltf':
                GltfExporter(self._mesh_asset, stem, lod=lod).export_gltf(path)
            elif fmt == 'obj':
                ObjExporter(self._mesh_asset, stem, lod=lod).export(path)
            elif fmt == 'fbx':
                from exporters.fbx_exporter import FbxExporter
                FbxExporter(self._mesh_asset, stem, lod=lod).export(path)
            self._on_export_done(path)
        except Exception as ex:
            import traceback
            self._on_export_error(f"{ex}\n{traceback.format_exc()}")

    def _on_export_done(self, path: str):
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        self._export_status.setText(f"✓ Exported to {os.path.basename(path)}")
        self.log(f"[OK] {path}")

    def _on_export_error(self, msg: str):
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)
        self._export_status.setText(f"✗ Error: {msg}")
        self.log(f"[ERR] {msg}")

    @staticmethod
    def _field_label() -> QLabel:
        lbl = QLabel("—")
        lbl.setObjectName("FieldValue")
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lbl
