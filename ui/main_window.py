"""
ui/main_window.py
RCRA Forge — Main Application Window
"""

import os
from core.theme import theme_manager
from ui.preferences_dialog import PreferencesDialog
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
from ui.controls_dialog import ControlsDialog
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
    mesh_ready      = pyqtSignal(object)        # ModelAsset
    texture_ready   = pyqtSignal(object)        # TextureAsset
    materials_ready = pyqtSignal(dict)          # {mat_idx: {role: (rgba, w, h, tex_name)}}
    skel_ready      = pyqtSignal(object)        # Skeleton
    zone_ready      = pyqtSignal(object)        # ZoneDef
    level_ready     = pyqtSignal(object, object)
    raw_ready       = pyqtSignal(bytes, str)    # raw bytes, label
    error           = pyqtSignal(str)

    def __init__(self, entry, toc_parser, lookup=None):
        super().__init__()
        self.entry      = entry
        self.toc_parser = toc_parser
        self.lookup     = lookup

    def run(self):
        import time
        t0 = time.perf_counter()
        try:
            print(f"[AssetLoader] extracting {self.entry.asset_id:#018x} "
                  f"size={self.entry.size:,} archive={self.entry.archive}")
            data = self.toc_parser.extract_asset(self.entry)
            print(f"[AssetLoader] extracted {len(data):,} bytes in {time.perf_counter()-t0:.3f}s")

            # Use asset name as label if lookup is available, else hex ID
            if self.lookup and self.lookup.is_loaded():
                label = self.lookup.name(self.entry.asset_id)
            else:
                label = f'asset_{self.entry.asset_id:#018x}'
            self.raw_ready.emit(data, label)

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

                # Load materials and decode albedo textures
                # Always emit materials_ready (even empty) so the thread quits cleanly
                self._load_model_textures(model)

            elif atype == 'texture':
                from core.texture import TextureParser
                tex = TextureParser(data).parse()
                self.texture_ready.emit(tex)

            elif atype == 'zone':
                from core.zone import parse_zone_asset
                zone_name = label or f'zone_{self.entry.asset_id:#018x}'
                zone = parse_zone_asset(data, self.entry.asset_id, zone_name,
                                        lookup=self.lookup)
                if zone is not None:
                    kind = "art" if zone.is_art_zone else "gp"
                    print(f"[AssetLoader] zone parsed: {zone.entry_count} scene nodes ({kind} zone)")
                    self.zone_ready.emit(zone)
                else:
                    print(f"[AssetLoader] zone parse returned None for unk1={dat1.unk1:#010x}")

            elif atype == 'level':
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

    def _load_model_textures(self, model):
        """
        For each unique material_index in LOD0 meshes, find the .material asset,
        parse it, decode all PBR texture slots, and emit materials_ready.
        """
        try:
            from core.material import parse_material_asset
            from core.texture import TextureParser
            from core.hashes import get_lookup
            import struct

            lookup = self.lookup or get_lookup()
            if not lookup or not lookup.is_loaded():
                return

            # Collect unique material indices from look 0 / LOD 0 only
            mat_indices = sorted({m.material_index for m in model.meshes
                                  if m.look_index == 0 and m.lod_level == 0})

            # Read material names from TAG_MATERIALS section (0x3250BB80)
            from core.archive import DAT1
            raw = self.toc_parser.extract_asset(self.entry)
            dat1 = DAT1(raw)
            TAG_MAT = 0x3250BB80
            mat_sec = dat1.sections.get(TAG_MAT)

            # PBR roles to decode (in priority order per slot)
            PBR_SLOTS = ['albedo', 'color', 'normal', 'ao_emission', 'specular_ior']

            result = {}  # {mat_idx: {role: (rgba, w, h, tex_name)}}

            for mat_idx in mat_indices:
                try:
                    mat_name = None
                    if mat_sec is not None:
                        sec = bytes(mat_sec)
                        ENTRY = 16
                        if mat_idx * ENTRY + ENTRY <= len(sec):
                            matfile_off, matname_off = struct.unpack_from('<QQ', sec, mat_idx * ENTRY)
                            mat_name = dat1.get_string(matfile_off)

                    if not mat_name:
                        continue

                    mat_name = mat_name.replace('\\', '/').lower()
                    if not mat_name.endswith('.material'):
                        mat_name += '.material'

                    mat_asset_id = lookup.asset_id(mat_name)
                    if mat_asset_id is None:
                        mat_asset_id = lookup.asset_id(mat_name.lstrip('/'))
                    if mat_asset_id is None:
                        print(f"[texload] mat[{mat_idx}] path not found: {mat_name}")
                        continue
                    mat_entry = self.toc_parser.find_entry(mat_asset_id)
                    if mat_entry is None:
                        continue

                    mat_data = self.toc_parser.extract_asset(mat_entry)
                    mat_asset = parse_material_asset(mat_data)

                    mat_result = {}

                    # Decode every slot whose role is in our export set
                    # This ensures both _g (albedo) and _c (color mask) are captured,
                    # along with normal, ao_emission, and specular_ior.
                    from exporters.texture_exporter import EXPORT_ROLES
                    for slot in mat_asset.slots:
                        if slot.role not in EXPORT_ROLES:
                            continue
                        # Use role+index as key so both _g and _c can coexist
                        role_key = slot.role if slot.role not in mat_result else f"{slot.role}_{slot.index}"
                        try:
                            tex_path = slot.path.replace('\\', '/').lower()
                            tex_id = lookup.asset_id(tex_path)
                            if tex_id is None:
                                tex_id = lookup.asset_id(tex_path.lstrip('/'))

                            tex_entry = None
                            if tex_id is not None:
                                tex_entry = self.toc_parser.find_entry(tex_id)

                            if tex_entry is None and slot.asset_id_lo:
                                tex_entry = self.toc_parser.find_entry_by_id_lo(slot.asset_id_lo)

                            if tex_entry is None:
                                continue

                            tex_data = self.toc_parser.extract_asset(tex_entry)
                            tex = TextureParser(tex_data).parse()

                            # Try to load HD pixel data
                            if tex.hd_len > 0 and tex.hd_width > 0 and tex_id is not None:
                                all_entries = self.toc_parser.find_all_entries(tex_id)
                                hd_candidates = [e for e in all_entries if e.size > tex_entry.size]
                                if hd_candidates:
                                    hd_entry = max(hd_candidates, key=lambda e: e.size)
                                    try:
                                        hd_raw = self.toc_parser.extract_asset(hd_entry)
                                        tex.hd_pixel_data = bytes(hd_raw)
                                        if slot.role == 'albedo':
                                            print(f"[texload] mat[{mat_idx}] HD {tex.hd_width}×{tex.hd_height} loaded ({len(hd_raw):,} bytes)")
                                    except Exception:
                                        pass

                            rgba = tex.decode_to_rgba()
                            if rgba:
                                tex_name = slot.name
                                w = tex.hd_width if tex.hd_pixel_data else tex.width
                                h = tex.hd_height if tex.hd_pixel_data else tex.height
                                mat_result[role_key] = (rgba, w, h, tex_name)
                                if slot.role == 'albedo':
                                    print(f"[texload] mat[{mat_idx}] '{mat_name}' albedo {w}×{h}")
                                else:
                                    print(f"[texload] mat[{mat_idx}] {slot.role} {w}×{h} ({tex_name})")

                        except Exception as ex:
                            print(f"[texload] mat[{mat_idx}] {slot.role} failed: {ex}")

                    if mat_result:
                        result[mat_idx] = mat_result

                except Exception as ex:
                    print(f"[texload] mat[{mat_idx}] failed: {ex}")

            self.materials_ready.emit(result if result else {})

        except Exception as ex:
            import traceback
            print(f"[texload] error: {ex}\n{traceback.format_exc()}")
            self.materials_ready.emit({})
            self.materials_ready.emit({})


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RCRA Forge — Ratchet & Clank: Rift Apart Editor")
        self.resize(1440, 900)
        self._load_thread:   QThread    = None
        self._asset_thread:  QThread    = None
        self._toc_parser:    TocParser  = None
        self._toc_path:      str        = None   # path to loaded 'toc' file
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
        self._browser.setMinimumWidth(180)
        self._browser.asset_activated.connect(self._on_asset_activated)
        self._browser.group_activated.connect(self._on_group_activated)
        self._browser.quick_export_requested.connect(self._on_quick_export)
        self._browser.add_to_list_requested.connect(self._on_add_to_export_list)
        self._browser.remove_from_list_requested.connect(self._on_remove_from_export_list)
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
        self._viewport.setMinimumHeight(200)
        top_splitter.addWidget(self._viewport)

        self._props = PropertiesPanel()
        self._props.set_export_zone_fn(self._on_export_zone_requested)
        self._props.setMinimumWidth(200)
        top_splitter.addWidget(self._props)
        top_splitter.setSizes([900, 280])
        self._props.lod_changed.connect(self._viewport.set_lod)

        # ── Bottom: tabbed panel [Texture | Scene | Skeleton | Hex] ──────────
        self._tab_panel = QTabWidget()
        self._tab_panel.setObjectName("BottomTabs")
        self._tab_panel.setMinimumHeight(312)
        right_splitter.addWidget(self._tab_panel)

        right_splitter.setSizes([560, 220])
        right_splitter.setStretchFactor(0, 1)  # viewport stretches
        right_splitter.setStretchFactor(1, 0)  # bottom panel holds size

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

        # Edit
        edit_m = mb.addMenu("Edit")
        act_prefs = QAction("Preferences…", self)
        act_prefs.setShortcut(QKeySequence("Ctrl+,"))
        act_prefs.triggered.connect(self._open_preferences)
        edit_m.addAction(act_prefs)

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

        view_m.addSeparator()

        act_controls = QAction("Viewport Controls…", self)
        act_controls.setShortcut(QKeySequence("Ctrl+K"))
        act_controls.triggered.connect(self._open_controls_dialog)
        view_m.addAction(act_controls)

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
        self._load_config()

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
        self._toc_path = path
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
        self._props.set_toc_parser(parser, self._toc_path)
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

        # Get display name from lookup for the export filename
        lookup = self._browser._lookup
        asset_name = None
        if lookup and lookup.is_loaded():
            asset_name = lookup.name(entry.asset_id)

        self._props.set_entry(entry, name=asset_name)
        self._current_entry = entry   # cached for texture export mat_names lookup
        self._status_lbl.setText(f"Loading asset {entry.asset_id:#018x}…")

        self._asset_thread = QThread(self)
        self._asset_loader = AssetLoader(entry, self._toc_parser, self._browser._lookup)  # keep reference!
        self._asset_loader.moveToThread(self._asset_thread)
        self._asset_thread.started.connect(self._asset_loader.run)

        self._asset_loader.mesh_ready.connect(self._on_mesh_ready)
        self._asset_loader.texture_ready.connect(self._on_texture_ready)
        self._asset_loader.materials_ready.connect(self._viewport.load_textures)
        self._asset_loader.materials_ready.connect(self._on_materials_ready)
        self._asset_loader.skel_ready.connect(self._on_skel_ready)
        self._asset_loader.zone_ready.connect(self._on_zone_ready)
        self._asset_loader.level_ready.connect(self._on_level_ready)
        self._asset_loader.raw_ready.connect(self._on_raw_ready)
        self._asset_loader.error.connect(self._on_asset_error)

        # Quit thread only after materials_ready (texture loading is last step in run())
        # Do NOT quit on mesh_ready — texture loading happens after it.
        for sig in (self._asset_loader.materials_ready, self._asset_loader.texture_ready,
                    self._asset_loader.level_ready, self._asset_loader.error):
            sig.connect(self._asset_thread.quit)

        self._asset_thread.start()

    def _on_group_activated(self, group):
        """User double-clicked a named group in the Groups tree view."""
        self._props.set_group(group)
        name = group.slug.rsplit('/', 1)[-1]
        self._status_lbl.setText(
            f"Group selected: {name}  ({group.count} parts) — "
            f"click 'Export Group as GLB' in Properties to export"
        )

    def _on_quick_export(self, entry, fmt: str):
        """Handle right-click quick export from asset browser."""
        from PyQt6.QtWidgets import QFileDialog
        import os

        # Get asset name for filename
        lookup = self._browser._lookup
        name = None
        if lookup and lookup.is_loaded():
            name = lookup.name(entry.asset_id)
        stem = (name or f"asset_{entry.asset_id:016X}").rsplit('.', 1)[0]

        ext_map = {'glb': 'GLB Files (*.glb)', 'fbx': 'FBX Files (*.fbx)', 'obj': 'OBJ Files (*.obj)'}
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {fmt.upper()}", f"{stem}.{fmt}",
            f"{ext_map.get(fmt, 'All Files (*.*)')};;All Files (*.*)"
        )
        if not path:
            return

        if not self._toc_path:
            self._status_lbl.setText("✗ No game folder loaded")
            return

        try:
            from core.archive import TocParser
            from core.mesh import ModelParser

            toc = TocParser(self._toc_path)
            toc.parse()
            raw = toc.extract_asset(entry)
            model = ModelParser(raw).parse()

            if fmt == 'glb':
                from exporters.gltf_exporter import GltfExporter
                GltfExporter(model, name=stem).export(path)
            elif fmt == 'fbx':
                from exporters.fbx_exporter import FbxExporter
                FbxExporter(model, name=stem).export(path)
            elif fmt == 'obj':
                from exporters.gltf_exporter import ObjExporter
                ObjExporter(model, name=stem).export(path)

            self._status_lbl.setText(f"✓ Exported {stem}.{fmt}")
        except Exception as ex:
            import traceback
            self._status_lbl.setText(f"✗ Export failed: {ex}")
            print(f"[quick_export] error: {ex}\n{traceback.format_exc()}")

    def _on_add_to_export_list(self, entry):
        """Handle right-click add to export list from asset browser."""
        lookup = self._browser._lookup
        name = None
        if lookup and lookup.is_loaded():
            name = lookup.name(entry.asset_id)
        self._props.add_to_export_list(entry, name=name)
        self._browser.mark_export_list(
            {e.asset_id for e in self._props.get_export_list_entries()}
        )

    def _on_remove_from_export_list(self, entry):
        """Handle right-click remove from export list from asset browser."""
        self._props.remove_from_export_list(entry)
        self._browser.mark_export_list(
            {e.asset_id for e in self._props.get_export_list_entries()}
        )

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

    def _on_materials_ready(self, tex_data: dict):
        """Cache decoded texture data in the properties panel for texture export."""
        if not tex_data:
            return
        # Build mat_names from the current model asset (already parsed, no re-extraction needed)
        mat_names = {}
        try:
            model = getattr(self._viewport, '_current_model', None)
            if model and model.material_names:
                for mat_idx in tex_data:
                    if mat_idx < len(model.material_names):
                        raw = model.material_names[mat_idx]
                        # Use just the filename stem
                        mat_names[mat_idx] = raw.replace('\\', '/').split('/')[-1].replace('.material', '')
        except Exception:
            pass

        self._props._cached_tex_data = tex_data
        self._props._mat_names       = mat_names

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

    def _on_zone_ready(self, zone):
        self._scene_panel.load_zone(zone)
        self._props.set_zone(zone)
        self._tab_panel.setCurrentWidget(self._scene_panel)
        kind = "art" if zone.is_art_zone else "gp"
        n_with_model = sum(1 for e in zone.entries if e.model_id)
        self._status_lbl.setText(
            f"Zone loaded — {zone.entry_count} scene node(s) ({kind})"
        )
        print(f"[zone] {zone.name}: {zone.entry_count} entries, "
              f"is_art={zone.is_art_zone}, "
              f"model_ids={len(zone.model_ids or [])}, "
              f"entries_with_model_id={n_with_model}")

    def _on_export_zone_requested(self, zone):
        """Export all resolved zone actors as a single GLB with world transforms."""
        from PyQt6.QtWidgets import QFileDialog, QProgressDialog
        from PyQt6.QtCore import Qt

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Zone as GLB",
            zone.name.split('/')[-1].replace('.zone', '') + "_assembled.glb",
            "GLB Files (*.glb)"
        )
        if not path:
            return

        # Progress dialog
        progress = QProgressDialog("Assembling zone...", "Cancel", 0, zone.entry_count, self)
        progress.setWindowTitle("Zone Export")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumWidth(360)
        progress.show()
        # Style the dialog's internal progress bar for readability
        from PyQt6.QtWidgets import QProgressBar as _QPB
        _bar = progress.findChild(_QPB)
        if _bar:
            _bar.setStyleSheet(
                "QProgressBar { height: 20px; color: #e0e4ef; text-align: center; "
                "font-size: 11px; background: #1e2028; border: 1px solid #2a2d36; "
                "border-radius: 3px; } "
                "QProgressBar::chunk { background: #3a6fbf; border-radius: 3px; }"
            )

        try:
            from core.level_assembler import LevelAssembler, export_zone_glb

            def on_progress(current, total):
                progress.setValue(current)
                progress.setLabelText(f"Resolving node {current}/{total}…")
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()

            assembler = LevelAssembler(self._toc_parser, self._browser._lookup)
            result    = assembler.assemble_zone(zone, progress_cb=on_progress)

            progress.setLabelText(f"Writing GLB…")
            QApplication.processEvents()

            n = export_zone_glb(result, path)
            progress.close()

            skipped = result.skip_count
            self._status_lbl.setText(
                f"Zone exported — {n} model(s) placed, {skipped} skipped"
            )
            print(f"[zone export] {n} nodes → {path}")
            if skipped:
                print(f"[zone export] {skipped} skipped:")
                for entry, reason in result.skipped:
                    id_str = f"{entry.asset_id:#018x}"
                    label  = entry.name or id_str
                    print(f"  [{entry.index}] {label} ({id_str}): {reason}")

        except Exception as ex:
            import traceback
            progress.close()
            self._status_lbl.setText(f"Zone export failed: {ex}")
            print(f"[zone export] error: {ex}\n{traceback.format_exc()}")

    def _on_raw_ready(self, data: bytes, label: str):
        self._hex_inspector.load_data(data, label)

    def _on_asset_error(self, msg: str):
        self._status_lbl.setText(f"Asset error: {msg}")
        self._props.log(f"[ERR] {msg}")

    def _on_instance_selected(self, entry):
        """Focus viewport camera on the selected scene node's world position."""
        try:
            import numpy as np
            pos = np.array([entry.x, entry.y, entry.z], dtype='float32')
            self._viewport.camera.target = pos
            self._viewport.camera.distance = 20.0
            self._viewport.update()
            self._status_lbl.setText(
                f"Scene node: {entry.name.split(chr(92))[-1] if entry.name else 'unnamed'} "
                f"— pos ({entry.x:.1f}, {entry.y:.1f}, {entry.z:.1f})"
            )
        except Exception:
            pass
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

    def _open_controls_dialog(self):
        dlg = ControlsDialog(self)
        dlg.exec()
        # Always reload so viewport picks up any saved changes
        self._viewport.reload_controls()

    def _open_preferences(self):
        """Open the Preferences dialog (theme / colour customisation)."""
        def apply_fn(qss: str):
            self.setStyleSheet(qss)
        dlg = PreferencesDialog(apply_fn, self)
        if dlg.exec():
            self._save_config()

    def _save_config(self):
        """Persist current theme to config.json next to the executable."""
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            existing = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r') as f:
                    existing = json.load(f)
            existing["theme"] = theme_manager.to_dict()
            with open(cfg_path, 'w') as f:
                json.dump(existing, f, indent=2)
        except Exception as ex:
            print(f"[config] failed to save: {ex}")

    def _load_config(self):
        """Load theme (and other settings) from config.json."""
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r') as f:
                    cfg = json.load(f)
                if "theme" in cfg:
                    theme_manager.from_dict(cfg["theme"])
                    self.setStyleSheet(theme_manager.stylesheet())
        except Exception as ex:
            print(f"[config] failed to load: {ex}")

    def _show_about(self):
        QMessageBox.about(self, "About RCRA Forge",
            "<h3>RCRA Forge v0.5.5</h3>"
            "<p>Ratchet &amp; Clank: Rift Apart level editor and model exporter.</p>"
            "<p>Format reverse engineering credit:<br>"
            "&nbsp;• chaoticgd / <i>ripped_apart</i> (MIT)<br>"
            "&nbsp;• thtrandomlurker (mesh format)<br>"
            "&nbsp;• doesthisusername (lump names)</p>"
            "<p>Built with Python, PyQt6, PyOpenGL, NumPy.</p>")
