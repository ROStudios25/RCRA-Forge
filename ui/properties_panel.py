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

    def __init__(self, mesh_asset, path: str, fmt: str):
        super().__init__()
        self.mesh  = mesh_asset
        self.path  = path
        self.fmt   = fmt

    def run(self):
        try:
            from exporters.gltf_exporter import GltfExporter, ObjExporter
            name = os.path.splitext(os.path.basename(self.path))[0]
            if self.fmt == 'glb':
                GltfExporter(self.mesh, name).export_glb(self.path)
            elif self.fmt == 'gltf':
                GltfExporter(self.mesh, name).export_gltf(self.path)
            elif self.fmt == 'obj':
                ObjExporter(self.mesh, name).export(self.path)
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

    def __init__(self, group, archive_path: str, path: str):
        """
        Parameters
        ----------
        group        : AssetGroup  (from core.grouping)
        archive_path : str         path to the game 'toc' file (to open WADs)
        path         : str         output .glb path
        """
        super().__init__()
        self.group        = group
        self.archive_path = archive_path
        self.path         = path

    def run(self):
        try:
            from exporters.group_exporter import GroupExporter
            from core.mesh import parse_model_asset
            from core.archive import TocParser

            exporter = GroupExporter(slug=self.group.slug.rsplit('/', 1)[-1])
            n = len(self.group.entries)

            for i, entry in enumerate(self.group.entries):
                part_name = self.group.entries[i]
                # Derive part name from the asset id / path
                # We'll use a simple index-based name if lookup not available
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
                    # Read raw bytes from the WAD archive
                    parser = TocParser(self.archive_path)
                    raw = parser.read_asset(entry)
                    model = parse_model_asset(raw, entry.header)
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
        self._mesh_asset             = None
        self._group                  = None   # AssetGroup for batch export
        self._archive_path: str      = None   # path to game 'toc' file
        self._export_thread: QThread = None
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
        self._fmt_combo.addItems(["GLB (Blender/glTF binary)", "GLTF (text + .bin)", "OBJ (Wavefront)"])
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
        layout.addStretch()

        # ── Log / notes ────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setObjectName("LogBox")
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        self._log.setPlaceholderText("Export log…")
        layout.addWidget(self._log)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_entry(self, entry: AssetEntry):
        self._entry = entry
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
        """Store the loaded toc path so group export can open WADs."""
        self._archive_path = path

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
        worker = GroupExportWorker(self._group, self._archive_path, path)
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

        fmt_map = {0: 'glb', 1: 'gltf', 2: 'obj'}
        fmt = fmt_map[self._fmt_combo.currentIndex()]
        ext = f".{fmt}" if fmt in ('glb', 'obj') else ".gltf"

        name = f"{self._entry.asset_id:016X}" if self._entry else "export"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Asset", name + ext,
            f"3D Files (*{ext});;All Files (*.*)"
        )
        if not path:
            return

        # Run on background thread
        self._btn_export.setEnabled(False)
        self._progress.setVisible(True)
        self._export_status.setText("Exporting…")

        self._export_thread = QThread(self)
        worker = ExportWorker(self._mesh_asset, path, fmt)
        worker.moveToThread(self._export_thread)
        self._export_thread.started.connect(worker.run)
        worker.finished.connect(self._on_export_done)
        worker.error.connect(self._on_export_error)
        worker.finished.connect(self._export_thread.quit)
        worker.error.connect(self._export_thread.quit)
        self._export_thread.start()

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
