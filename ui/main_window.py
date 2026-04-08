"""
ui/main_window.py
RCRA Forge — Main Application Window
"""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QMenuBar, QMenu, QToolBar, QStatusBar, QFileDialog,
    QMessageBox, QApplication, QLabel, QFrame, QTabWidget
)
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, QSize
from PyQt6.QtGui import QAction, QKeySequence, QFont, QColor

from ui.asset_browser import AssetBrowser
from ui.properties_panel import PropertiesPanel
from ui.viewport import Viewport3D
from ui.texture_viewer import TextureViewer
from ui.scene_panel import ScenePanel
from ui.hex_inspector import HexInspector
from ui.skeleton_viewer import SkeletonViewer
from core.archive import TocParser, AssetEntry, ASSET_TYPE_NAMES


# ── Background loader ──────────────────────────────────────────────────────────

class TocLoader(QObject):
    finished      = pyqtSignal(object, object, str, list)  # parser, entries, timing, groups
    hashes_ready  = pyqtSignal(object)                     # lookup (after background load)
    progress      = pyqtSignal(str)
    error         = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        import time, struct, zlib
        print(f"[TocLoader] run() started, path={self.path}")
        try:
            t0 = time.time()

            self.progress.emit("Reading toc file from disk…")
            with open(self.path, 'rb') as f:
                raw = f.read()
            t1 = time.time()

            self.progress.emit(f"Parsing DAT1 container ({len(raw)//1024:,} KB)…")
            from core.archive import TOC_MAGIC_RCRA, TOC_MAGIC_MSMR, DAT1, TocParser
            magic, size = struct.unpack_from('<II', raw, 0)
            if magic == TOC_MAGIC_RCRA:
                # Use memoryview — zero copy slice of the 12MB buffer
                dat1_data = memoryview(raw)[8:8 + size]
            elif magic == TOC_MAGIC_MSMR:
                dat1_data = zlib.decompress(raw[8:])
            else:
                raise ValueError(f"Unknown TOC magic {magic:#010x}")
            dat1 = DAT1(bytes(dat1_data))  # DAT1 needs bytes for struct.unpack_from
            del raw  # free the 12MB buffer immediately after slicing
            t2 = time.time()

            self.progress.emit("Building asset index…")
            parser = TocParser(self.path)
            parser._dat1 = dat1
            parser._build_entries()
            t3 = time.time()

            self.progress.emit("Grouping assets by archive…")
            import numpy as np
            print("[TocLoader] grouping...")
            entries     = parser.entries
            arc_col     = entries._sizes['archive'][:len(entries)].astype(np.int32)
            sort_idx    = np.argsort(arc_col, kind='stable')
            sorted_arcs = arc_col[sort_idx]
            boundaries  = np.where(np.diff(sorted_arcs))[0] + 1
            starts = np.concatenate([[0], boundaries])
            ends   = np.concatenate([boundaries, [len(sort_idx)]])
            groups = [
                (int(sorted_arcs[s]), sort_idx[s:e])
                for s, e in zip(starts.tolist(), ends.tolist())
            ]
            print(f"[TocLoader] grouped into {len(groups)} archives")

            # Load hashes.txt asynchronously
            self.progress.emit("TOC ready — loading asset names in background…")
            from core.hashes import get_lookup, try_load_from_game_root
            lookup = get_lookup()
            game_root = os.path.dirname(self.path)
            print(f"[TocLoader] starting hashes thread, game_root={game_root}")

            import threading
            def _load_hashes():
                print("[hashes thread] starting...")
                try_load_from_game_root(game_root)
                print(f"[hashes thread] done, {len(lookup)} entries")
                self.hashes_ready.emit(lookup)

            t = threading.Thread(target=_load_hashes, daemon=True)
            t.start()

            timing = (
                f"disk:{t1-t0:.2f}s  "
                f"dat1:{t2-t1:.2f}s  "
                f"index:{t3-t2:.2f}s  "
                f"total:{time.time()-t0:.2f}s"
            )
            print(f"[TocLoader] emitting finished signal, {len(entries):,} entries")
            self.progress.emit(f"Done — {len(entries):,} assets  (names loading…)")
            self.finished.emit(parser, entries, timing, groups)
        except Exception as ex:
            import traceback
            self.error.emit(f"{ex}\n{traceback.format_exc()}")


class AssetLoader(QObject):
    """Load + parse a single asset on a background thread."""
    mesh_ready    = pyqtSignal(object)        # ModelAsset
    texture_ready = pyqtSignal(object)        # TextureAsset
    skel_ready    = pyqtSignal(object)        # Skeleton
    level_ready   = pyqtSignal(object, object)
    raw_ready     = pyqtSignal(bytes, str)    # raw bytes, label
    error         = pyqtSignal(str)

    def __init__(self, entry, toc_parser):
        super().__init__()
        self.entry      = entry
        self.toc_parser = toc_parser

    def run(self):
        import time
        t0 = time.perf_counter()
        try:
            print(f"[AssetLoader] extracting {self.entry.asset_id:#018x} "
                  f"size={self.entry.size:,} archive={self.entry.archive}")
            data = self.toc_parser.extract_asset(self.entry)
            print(f"[AssetLoader] extracted {len(data):,} bytes in {time.perf_counter()-t0:.3f}s")

            self.raw_ready.emit(data, f'asset_{self.entry.asset_id:#018x}')

            from core.archive import DAT1, ASSET_TYPE_NAMES
            dat1 = DAT1(data)
            atype = ASSET_TYPE_NAMES.get(dat1.unk1, '')
            print(f"[AssetLoader] DAT1 type={atype} unk1={dat1.unk1:#010x} "
                  f"sections={len(dat1.sections)}")

            if atype == 'model':
                print("[AssetLoader] parsing model...")
                from core.mesh import ModelParser
                from core.skeleton import Skeleton
                model = ModelParser(data).parse()
                print(f"[AssetLoader] model parsed: {len(model.vertexes)} verts, "
                      f"{len(model.meshes)} meshes, {len(model.indexes)} indices")
                self.mesh_ready.emit(model)
                skel = Skeleton.from_model(model)
                if skel and skel.bones:
                    print(f"[AssetLoader] skeleton: {len(skel.bones)} bones")
                    self.skel_ready.emit(skel)

            elif atype == 'texture':
                from core.texture import TextureParser
                tex = TextureParser(data).parse()
                self.texture_ready.emit(tex)

            elif atype in ('level', 'zone'):
                from core.level import LevelParser
                lp = LevelParser(data)
                info = lp.parse_info()
                self.level_ready.emit(info, None)

            else:
                # Unknown/unhandled — raw bytes already emitted above
                pass

        except Exception as ex:
            import traceback
            self.error.emit(f"{ex}\n{traceback.format_exc()}")


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RCRA Forge — Ratchet & Clank: Rift Apart Editor")
        self.resize(1440, 900)
        self._load_thread:   QThread    = None
        self._asset_thread:  QThread    = None
        self._toc_parser:    TocParser  = None
        self._loader        = None   # keeps TocLoader alive during thread run
        self._asset_loader  = None   # keeps AssetLoader alive during thread run
        self._setup_ui()
        self._setup_menus()
        self._setup_toolbar()
        self._apply_theme()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Outer horizontal split: [Asset Browser | Main Area] ──────────────
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setChildrenCollapsible(False)
        root.addWidget(outer)

        # Left: Asset browser
        self._browser = AssetBrowser()
        self._browser.setMinimumWidth(220)
        self._browser.setMaximumWidth(380)
        self._browser.asset_activated.connect(self._on_asset_activated)
        outer.addWidget(self._browser)

        # ── Right side: vertical split [Viewport top | Tabs bottom] ──────────
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setChildrenCollapsible(False)
        outer.addWidget(right_splitter)

        # ── Top: horizontal split [3D Viewport | Properties] ─────────────────
        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.setChildrenCollapsible(False)
        right_splitter.addWidget(top_splitter)

        self._viewport = Viewport3D()
        self._viewport.setMinimumHeight(300)
        top_splitter.addWidget(self._viewport)

        self._props = PropertiesPanel()
        self._props.setMinimumWidth(220)
        self._props.setMaximumWidth(340)
        top_splitter.addWidget(self._props)
        top_splitter.setSizes([900, 280])

        # ── Bottom: tabbed panel [Texture | Scene | Skeleton | Hex] ──────────
        self._tab_panel = QTabWidget()
        self._tab_panel.setObjectName("BottomTabs")
        self._tab_panel.setMinimumHeight(180)
        self._tab_panel.setMaximumHeight(380)
        right_splitter.addWidget(self._tab_panel)

        right_splitter.setSizes([560, 220])

        # Tab: Texture viewer
        self._tex_viewer = TextureViewer()
        self._tab_panel.addTab(self._tex_viewer, "🖼  Texture")

        # Tab: Scene hierarchy
        self._scene_panel = ScenePanel()
        self._scene_panel.instance_selected.connect(self._on_instance_selected)
        self._tab_panel.addTab(self._scene_panel, "🗺  Scene")

        # Tab: Skeleton
        self._skel_viewer = SkeletonViewer()
        self._tab_panel.addTab(self._skel_viewer, "🦴  Skeleton")

        # Tab: Hex inspector
        self._hex_inspector = HexInspector()
        self._tab_panel.addTab(self._hex_inspector, "🔬  Hex")

        outer.setSizes([260, 1180])

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_lbl = QLabel("Ready — open a game folder to begin")
        self._status.addWidget(self._status_lbl)

        # Loading progress bar (hidden until TOC load starts)
        from PyQt6.QtWidgets import QProgressBar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate spinner
        self._progress.setFixedWidth(120)
        self._progress.setFixedHeight(14)
        self._progress.setVisible(False)
        self._progress.setTextVisible(False)
        self._status.addPermanentWidget(self._progress)

        # Permanent right-side status info
        self._status_right = QLabel("")
        self._status.addPermanentWidget(self._status_right)

    def _setup_menus(self):
        mb = QMenuBar(self)
        self.setMenuBar(mb)

        # File
        file_m = mb.addMenu("File")
        act_open = QAction("Open Game Folder…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open_game_folder)
        file_m.addAction(act_open)

        act_toc = QAction("Open TOC File…", self)
        act_toc.triggered.connect(self._open_toc_file)
        file_m.addAction(act_toc)

        act_hashes = QAction("Load hashes.txt…", self)
        act_hashes.triggered.connect(self._load_hashes_file)
        file_m.addAction(act_hashes)

        file_m.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(QApplication.quit)
        file_m.addAction(act_quit)

        # View
        view_m = mb.addMenu("View")
        self._act_wire = QAction("Wireframe", self)
        self._act_wire.setCheckable(True)
        self._act_wire.triggered.connect(self._toggle_wireframe)
        view_m.addAction(self._act_wire)

        act_frame = QAction("Frame All", self)
        act_frame.setShortcut(QKeySequence("F"))
        act_frame.triggered.connect(self._frame_scene)
        view_m.addAction(act_frame)

        # Help
        help_m = mb.addMenu("Help")
        act_about = QAction("About RCRA Forge", self)
        act_about.triggered.connect(self._show_about)
        help_m.addAction(act_about)

    def _setup_toolbar(self):
        tb = QToolBar("Main Toolbar", self)
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        act_open = QAction("📂 Open Folder", self)
        act_open.triggered.connect(self._open_game_folder)
        tb.addAction(act_open)

        tb.addSeparator()

        self._act_wire_tb = QAction("⬛ Wireframe", self)
        self._act_wire_tb.setCheckable(True)
        self._act_wire_tb.triggered.connect(self._toggle_wireframe)
        tb.addAction(self._act_wire_tb)

        act_frame_tb = QAction("⊞ Frame", self)
        act_frame_tb.triggered.connect(self._frame_scene)
        tb.addAction(act_frame_tb)

        # View preset dropdown
        from PyQt6.QtWidgets import QComboBox
        self._view_preset = QComboBox()
        self._view_preset.setObjectName("ViewPreset")
        self._view_preset.setFixedWidth(72)
        self._view_preset.addItems(["Main", "Front", "Back", "Right", "Left", "Top", "Bottom"])
        self._view_preset.activated.connect(
            lambda _: self._viewport.set_view_preset(self._view_preset.currentText().lower())
        )
        tb.addWidget(self._view_preset)

        tb.addSeparator()

        self._game_path_lbl = QLabel("  No game folder loaded  ")
        self._game_path_lbl.setObjectName("GamePathLabel")
        tb.addWidget(self._game_path_lbl)

    # ── Theming ───────────────────────────────────────────────────────────────

    def _apply_theme(self):
        self.setStyleSheet("""
        QMainWindow, QWidget {
            background: #1a1c22;
            color: #d4d8e0;
            font-family: 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', sans-serif;
            font-size: 11px;
        }
        QMenuBar {
            background: #13151a;
            color: #c0c4cc;
            border-bottom: 1px solid #2a2d36;
            padding: 2px 0;
        }
        QMenuBar::item:selected { background: #2a2d36; }
        QMenu {
            background: #1e2028;
            border: 1px solid #2a2d36;
            color: #d0d4dc;
        }
        QMenu::item:selected { background: #3a6fbf; }
        QToolBar {
            background: #13151a;
            border-bottom: 1px solid #2a2d36;
            spacing: 4px;
            padding: 2px 6px;
        }
        QToolBar QToolButton {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 4px;
            padding: 3px 8px;
            color: #c0c4cc;
        }
        QToolBar QToolButton:hover    { background: #2a2d36; border-color: #3a3d4a; }
        QToolBar QToolButton:checked  { background: #253a5e; border-color: #3a6fbf; color: #5ba3f5; }
        QSplitter::handle { background: #2a2d36; width: 2px; height: 2px; }
        QSplitter::handle:hover { background: #3a6fbf; }

        /* Bottom tab panel */
        #BottomTabs {
            background: #13151a;
            border-top: 2px solid #2a2d36;
        }
        #BottomTabs QTabBar::tab {
            background: #1a1c22;
            color: #606878;
            border: none;
            border-bottom: 2px solid transparent;
            padding: 5px 14px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.5px;
        }
        #BottomTabs QTabBar::tab:hover    { color: #a0b0c8; background: #1e2028; }
        #BottomTabs QTabBar::tab:selected {
            color: #5ba3f5;
            border-bottom: 2px solid #3a6fbf;
            background: #1a1c22;
        }
        #BottomTabs QTabWidget::pane { border: none; }

        /* Browser / panel frames */
        #BrowserHeader {
            background: #13151a;
            border-bottom: 1px solid #2a2d36;
        }
        #PanelTitle {
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1.5px;
            color: #6a7080;
        }
        #FilterBar { background: #16181f; border-bottom: 1px solid #2a2d36; }
        #SearchBox {
            background: #1e2028;
            border: 1px solid #2a2d36;
            border-radius: 4px;
            padding: 3px 6px;
            color: #d0d4dc;
        }
        #SearchBox:focus { border-color: #3a6fbf; }
        #TypeFilter {
            background: #1e2028;
            border: 1px solid #2a2d36;
            border-radius: 4px;
            color: #c0c4cc;
        }
        #AssetTree {
            background: #1a1c22;
            border: none;
            color: #c0c8d8;
            alternate-background-color: #1d1f26;
            selection-background-color: #253a5e;
        }
        #AssetTree::item { padding: 2px 4px; border-radius: 2px; }
        #AssetTree::item:hover    { background: #22263a; }
        #AssetTree::item:selected { background: #253a5e; color: #ffffff; }
        #StatusLabel {
            font-size: 10px;
            color: #606570;
            background: #13151a;
            border-top: 1px solid #2a2d36;
        }
        #SubPanelLabel {
            background: #161820;
            color: #505868;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            border-bottom: 1px solid #2a2d36;
            padding-left: 8px;
        }

        /* Properties panel */
        QGroupBox {
            font-size: 10px;
            font-weight: 600;
            color: #6a7080;
            border: 1px solid #2a2d36;
            border-radius: 6px;
            margin-top: 12px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 6px;
            left: 8px;
        }
        #FieldValue {
            color: #b0bcd0;
            font-family: 'Consolas', 'JetBrains Mono', monospace;
            font-size: 11px;
        }
        #FmtCombo {
            background: #1e2028;
            border: 1px solid #2a2d36;
            border-radius: 4px;
            padding: 2px 6px;
            color: #c0c4cc;
        }
        QCheckBox { color: #a0a8b8; }
        QCheckBox::indicator {
            width: 13px; height: 13px;
            border: 1px solid #3a3d4a;
            border-radius: 3px;
            background: #1e2028;
        }
        QCheckBox::indicator:checked { background: #3a6fbf; border-color: #5a90df; }
        #ExportBtn {
            background: #1f4a8f;
            border: 1px solid #3a70cf;
            border-radius: 5px;
            padding: 6px 12px;
            color: #e0eaff;
            font-weight: 600;
            font-size: 12px;
        }
        #ExportBtn:hover   { background: #2560af; }
        #ExportBtn:pressed { background: #143a7a; }
        #ExportBtn:disabled { background: #1e2028; color: #404550; border-color: #2a2d36; }
        #ExportStatus { color: #80b0e0; font-size: 10px; }
        #LogBox {
            background: #13151a;
            border: 1px solid #2a2d36;
            border-radius: 4px;
            color: #607090;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 10px;
        }
        #GamePathLabel { color: #404858; font-size: 10px; }

        /* Texture viewer */
        #ZoomRow { background: #161820; border-top: 1px solid #2a2d36; }
        #TexInfo { background: #13151a; border-top: 1px solid #2a2d36; }
        #TexInfoKey { color: #405060; font-size: 9px; font-weight: 600; letter-spacing: 1px; }
        #TexInfoVal { color: #90b8d8; font-family: 'Consolas', monospace; font-size: 11px; }

        /* Instance table */
        #InstTable {
            background: #1a1c22;
            alternate-background-color: #1d1f26;
            border: none;
            color: #a0b8c8;
            gridline-color: #2a2d36;
            selection-background-color: #253a5e;
        }
        #InstTable QHeaderView::section {
            background: #13151a;
            color: #506070;
            border: none;
            border-bottom: 1px solid #2a2d36;
            padding: 3px 6px;
            font-size: 10px;
            font-weight: 600;
        }

        /* Scrollbars */
        QScrollBar:vertical {
            background: #13151a;
            width: 10px;
            border: none;
        }
        QScrollBar::handle:vertical {
            background: #2a3040;
            border-radius: 4px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover { background: #3a4560; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal {
            background: #13151a;
            height: 10px;
            border: none;
        }
        QScrollBar::handle:horizontal {
            background: #2a3040;
            border-radius: 4px;
            min-width: 20px;
        }
        QScrollBar::handle:horizontal:hover { background: #3a4560; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

        /* Sliders */
        QSlider::groove:horizontal {
            background: #2a2d36;
            height: 3px;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #3a6fbf;
            width: 12px; height: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }

        /* Status bar */
        QStatusBar {
            background: #13151a;
            border-top: 1px solid #2a2d36;
            color: #606070;
            font-size: 10px;
        }
        QProgressBar {
            background: #1e2028;
            border: 1px solid #2a2d36;
            border-radius: 3px;
            height: 6px;
        }
        QProgressBar::chunk { background: #3a6fbf; border-radius: 3px; }

        QPushButton {
            background: #1e2028;
            border: 1px solid #2a2d36;
            border-radius: 4px;
            padding: 3px 8px;
            color: #c0c4cc;
        }
        QPushButton:hover { background: #252830; border-color: #3a3d4a; }
        QPushButton:pressed { background: #1a1c24; }
        """)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _open_game_folder(self):
        import string, ctypes

        # Get all available drive letters on Windows
        drives = []
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(letter)
                bitmask >>= 1
        except Exception:
            drives = list('CDEFGHIJKLMNOPQRSTUVWXYZ')

        # Search all drives for Steam install
        steam_subpaths = [
            r"Steam\steamapps\common\Ratchet & Clank - Rift Apart",
            r"SteamLibrary\steamapps\common\Ratchet & Clank - Rift Apart",
            r"Games\Steam\steamapps\common\Ratchet & Clank - Rift Apart",
            r"Program Files (x86)\Steam\steamapps\common\Ratchet & Clank - Rift Apart",
            r"Program Files\Steam\steamapps\common\Ratchet & Clank - Rift Apart",
        ]

        default_dir = ""
        for drive in drives:
            for sub in steam_subpaths:
                candidate = f"{drive}:\\{sub}"
                if os.path.exists(candidate):
                    default_dir = candidate
                    break
            if default_dir:
                break

        folder = QFileDialog.getExistingDirectory(
            self, "Select Rift Apart Game Folder", default_dir
        )
        if not folder:
            return

        toc_candidates = [
            os.path.join(folder, 'toc'),
            os.path.join(folder, 'data', 'toc'),
        ]
        toc_path = next((p for p in toc_candidates if os.path.exists(p)), None)

        if not toc_path:
            QMessageBox.warning(self, "TOC Not Found",
                f"Could not find a 'toc' file in:\n{folder}\n\n"
                "Make sure you selected the correct game folder containing the 'toc' file.")
            return

        self._load_toc(toc_path)
        self._game_path_lbl.setText(f"  {os.path.basename(folder)}  ")

    def _load_hashes_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load hashes.txt", "",
            "Hash files (hashes.txt);;Text Files (*.txt);;All Files (*.*)"
        )
        if not path:
            return
        from core.hashes import get_lookup
        lookup = get_lookup()
        count = lookup.load(path)
        self._status_lbl.setText(f"Loaded {count:,} asset names from hashes.txt")
        # Refresh the browser with new names if TOC is already loaded
        if self._toc_parser:
            self._browser.set_lookup(lookup)

    def _open_toc_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TOC File", "", "TOC Files (toc);;All Files (*.*)"
        )
        if path:
            self._load_toc(path)

    def _load_toc(self, path: str):
        import time
        self._toc_load_start = time.time()
        toc_size_mb = os.path.getsize(path) / (1024*1024)
        self._status_lbl.setText(
            f"Loading toc… ({toc_size_mb:.1f} MB)  please wait"
        )
        self._progress.setVisible(True)
        self._browser.clear()

        self._load_thread = QThread(self)
        self._loader = TocLoader(path)          # keep reference on self!
        self._loader.moveToThread(self._load_thread)
        self._load_thread.started.connect(self._loader.run)
        self._loader.progress.connect(self._status_lbl.setText)
        self._loader.finished.connect(self._on_toc_loaded)
        self._loader.hashes_ready.connect(self._on_hashes_ready)
        self._loader.error.connect(self._on_load_error)
        self._loader.finished.connect(self._load_thread.quit)
        self._loader.error.connect(self._load_thread.quit)
        self._load_thread.start()
        print(f"[_load_toc] thread started for {path}")

    def _on_toc_loaded(self, parser, entries, timing, groups):
        import time
        t0 = time.perf_counter()
        elapsed_wall = time.time() - getattr(self, '_toc_load_start', 0)
        self._toc_parser = parser
        self._progress.setVisible(False)
        t1 = time.perf_counter()
        self._browser.load_entries_grouped(entries, groups, None)
        t2 = time.perf_counter()
        print(f"[main] progress_hide:{t1-t0:.3f}s  load_browser:{t2-t1:.3f}s  "
              f"wall:{elapsed_wall:.2f}s")
        self._status_lbl.setText(
            f"Loaded {len(entries):,} assets  ·  "
            f"{len(parser.archives)} archives  ·  "
            f"wall:{elapsed_wall:.1f}s  [{timing}]  — names loading…"
        )

    def _on_hashes_ready(self, lookup):
        """Called when hashes.txt finishes loading in background."""
        self._browser.set_lookup(lookup)
        n = len(lookup) if lookup and lookup.is_loaded() else 0
        current = self._status_lbl.text().replace("— asset names loading…", "")
        self._status_lbl.setText(f"{current.strip()}  ·  {n:,} names")

    def _on_load_error(self, msg: str):
        self._progress.setVisible(False)
        self._status_lbl.setText(f"Error: {msg}")
        QMessageBox.critical(self, "Load Error", msg)

    def _on_asset_activated(self, entry):
        if self._toc_parser is None:
            self._status_lbl.setText("No TOC loaded — open a game folder first")
            return
        self._props.set_entry(entry)
        self._status_lbl.setText(f"Loading asset {entry.asset_id:#018x}…")

        self._asset_thread = QThread(self)
        self._asset_loader = AssetLoader(entry, self._toc_parser)  # keep reference!
        self._asset_loader.moveToThread(self._asset_thread)
        self._asset_thread.started.connect(self._asset_loader.run)

        self._asset_loader.mesh_ready.connect(self._on_mesh_ready)
        self._asset_loader.texture_ready.connect(self._on_texture_ready)
        self._asset_loader.skel_ready.connect(self._on_skel_ready)
        self._asset_loader.level_ready.connect(self._on_level_ready)
        self._asset_loader.raw_ready.connect(self._on_raw_ready)
        self._asset_loader.error.connect(self._on_asset_error)

        for sig in (self._asset_loader.mesh_ready, self._asset_loader.texture_ready,
                    self._asset_loader.skel_ready, self._asset_loader.level_ready,
                    self._asset_loader.error):
            sig.connect(self._asset_thread.quit)

        self._asset_thread.start()

    def _on_mesh_ready(self, model_asset):
        self._viewport.load_mesh(model_asset)
        self._props.set_mesh_asset(model_asset)
        from core.mesh import mesh_to_numpy
        total_verts = 0
        total_tris  = 0
        for mesh in model_asset.meshes:
            pos, _, _, idx = mesh_to_numpy(model_asset, mesh)
            if pos is not None: total_verts += len(pos)
            if idx is not None: total_tris  += len(idx) // 3
        self._status_lbl.setText(
            f"Model loaded — {total_verts:,} vertices, {total_tris:,} triangles, "
            f"{len(model_asset.meshes)} sub-meshes, {len(model_asset.joints)} bones"
        )
        self._status_right.setText(f"Sub-meshes: {len(model_asset.meshes)}")

    def _on_texture_ready(self, tex_asset):
        self._tex_viewer.load_texture(tex_asset)
        self._tab_panel.setCurrentWidget(self._tex_viewer)
        self._status_lbl.setText(
            f"Texture loaded — {tex_asset.width}×{tex_asset.height} {tex_asset.format_name}"
        )

    def _on_skel_ready(self, skel):
        self._skel_viewer.load_skeleton(skel)
        self._tab_panel.setCurrentWidget(self._skel_viewer)
        self._status_lbl.setText(f"Skeleton loaded — {len(skel.bones)} bones")

    def _on_level_ready(self, level_info, inst_table):
        self._tab_panel.setCurrentWidget(self._scene_panel)
        self._status_lbl.setText(
            f"Asset loaded — type: {level_info.asset_type}"
        )
        self._props.log(f"[INFO] {level_info.description}")

    def _on_raw_ready(self, data: bytes, label: str):
        self._hex_inspector.load_data(data, label)

    def _on_asset_error(self, msg: str):
        self._status_lbl.setText(f"Asset error: {msg}")
        self._props.log(f"[ERR] {msg}")

    def _on_instance_selected(self, inst):
        # Focus viewport camera on the instance's world position
        pos = inst.position
        self._viewport.camera.target = pos.astype('float32')
        self._viewport.update()
        self._status_lbl.setText(
            f"Instance {inst.instance_id:#010x} @ "
            f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
        )

    def _toggle_wireframe(self, checked: bool):
        self._viewport.set_wireframe(checked)
        self._act_wire.setChecked(checked)
        self._act_wire_tb.setChecked(checked)

    def _frame_scene(self):
        self._viewport.frame_model()

    def _show_about(self):
        QMessageBox.about(self, "About RCRA Forge",
            "<h3>RCRA Forge v0.2.0</h3>"
            "<p>Ratchet &amp; Clank: Rift Apart level editor and model exporter.</p>"
            "<p>Format reverse engineering credit:<br>"
            "&nbsp;• chaoticgd / <i>ripped_apart</i> (MIT)<br>"
            "&nbsp;• thtrandomlurker (mesh format)<br>"
            "&nbsp;• doesthisusername (lump names)</p>"
            "<p>Built with Python, PyQt6, PyOpenGL, NumPy.</p>")
