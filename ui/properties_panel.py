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


class PropertiesPanel(QWidget):
    request_export = pyqtSignal(str, str)   # (output_path, format_string)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry:      AssetEntry  = None
        self._mesh_asset             = None
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

        mform.addRow("Vertices:",   self._lbl_verts)
        mform.addRow("Triangles:",  self._lbl_tris)
        mform.addRow("Sub-meshes:", self._lbl_submsh)
        mform.addRow("LOD levels:", self._lbl_lods)

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

        # Options
        self._chk_lod0 = QCheckBox("LOD 0 only")
        self._chk_lod0.setChecked(True)
        elayout.addWidget(self._chk_lod0)

        # Export button
        self._btn_export = QPushButton("⬇  Export Asset")
        self._btn_export.setObjectName("ExportBtn")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._do_export)
        elayout.addWidget(self._btn_export)

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
        self._lbl_lods.setText(f"{len(model_asset.joints)} bones")
        self._mesh_group.setVisible(True)
        self._btn_export.setEnabled(True)

    def log(self, msg: str):
        self._log.append(msg)

    # ── Private ───────────────────────────────────────────────────────────────

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
