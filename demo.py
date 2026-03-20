"""
demo.py
Launch RCRA Forge with pre-loaded synthetic assets.
No game files needed — useful for testing the UI and parsers.

Usage:
    python demo.py
"""

import sys
import os
import struct
import math

sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

from ui.main_window import MainWindow
from core.archive import AssetEntry
from core.mesh import ModelAsset, MeshDefinition, Vertex
from core.texture import TextureAsset
from core.skeleton import Skeleton, Bone


def _make_sphere_model(radius=1.5, rings=20, sectors=32) -> ModelAsset:
    vertexes = []
    for r in range(rings + 1):
        phi = math.pi * r / rings
        for s in range(sectors + 1):
            theta = 2 * math.pi * s / sectors
            x = math.sin(phi) * math.cos(theta) * radius
            y = math.cos(phi) * radius
            z = math.sin(phi) * math.sin(theta) * radius
            nx, ny, nz = x/radius, y/radius, z/radius
            vertexes.append(Vertex(x=x, y=y, z=z, nx=nx, ny=ny, nz=nz,
                                   u=s/sectors, v=r/rings))
    indices = []
    for r in range(rings):
        for s in range(sectors):
            a = r * (sectors + 1) + s
            b, c, d = a + 1, (r+1)*(sectors+1) + s, (r+1)*(sectors+1) + s + 1
            indices += [a, c, b, b, c, d]
    mesh = MeshDefinition(mesh_id=0, vertex_start=0, vertex_count=len(vertexes),
                          index_start=0, index_count=len(indices), flags=0x10,
                          material_index=0, first_skin_batch=0,
                          skin_batches_count=0, first_weight_index=0)
    return ModelAsset(vertexes=vertexes, meshes=[mesh], indexes=indices)


def _make_checkerboard_texture(size=128) -> TextureAsset:
    tile = size // 8
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            if (x // tile + y // tile) % 2 == 0:
                pixels += bytes([200, 80, 20, 255])
            else:
                pixels += bytes([30, 30, 50, 255])
    return TextureAsset(sd_len=len(pixels), sd_width=size, sd_height=size, sd_mips=1,
                        hd_len=0, hd_width=size, hd_height=size, hd_mips=1,
                        fmt=0x1C, array_size=1, planes=1, pixel_data=bytes(pixels))


def _make_demo_skeleton() -> Skeleton:
    bones = [
        Bone(index=0, parent=-1, name="root",  position=(0,0,0),    rotation=(0,0,0,1)),
        Bone(index=1, parent=0,  name="spine", position=(0,1,0),    rotation=(0,0,0,1)),
        Bone(index=2, parent=1,  name="chest", position=(0,1,0),    rotation=(0,0,0,1)),
        Bone(index=3, parent=2,  name="neck",  position=(0,0.5,0),  rotation=(0,0,0,1)),
        Bone(index=4, parent=3,  name="head",  position=(0,0.4,0),  rotation=(0,0,0,1)),
    ]
    return Skeleton(bones=bones)


def load_demo_assets(window: MainWindow):
    demo_entries = [
        AssetEntry(index=i, asset_id=0xDEADBEEF00000000 + i,
                   archive=i % 3, offset=i * 4096, size=4096)
        for i in range(8)
    ]
    window._browser.load_entries(demo_entries)
    window._game_path_lbl.setText("  Demo Mode — no game folder  ")

    sphere = _make_sphere_model()
    window._on_mesh_ready(sphere)
    window._props.set_entry(demo_entries[0])
    window._on_texture_ready(_make_checkerboard_texture(128))
    window._on_skel_ready(_make_demo_skeleton())

    idx_bytes = struct.pack(f'<{min(len(sphere.indexes),2048)}H',
                            *[min(i, 0xFFFF) for i in sphere.indexes[:2048]])
    window._on_raw_ready(idx_bytes, 'demo — sphere index buffer')
    window._tab_panel.setCurrentIndex(0)
    window._status_lbl.setText(
        "Demo mode active. File → Open Game Folder to load real Rift Apart assets.")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RCRA Forge [Demo]")
    window = MainWindow()
    window.setWindowTitle("RCRA Forge v0.1.0 — Demo Mode")
    window.show()
    QTimer.singleShot(250, lambda: load_demo_assets(window))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
