# RCRA Forge
**Ratchet & Clank: Rift Apart — Asset Browser & Model Viewer**

A standalone Python/PyQt6 desktop tool for browsing, previewing and exporting assets from Ratchet & Clank: Rift Apart (PC) without Ninja Ripper.

---

## Features
- Browse all 340,000+ game assets by name and type
- 3D viewport with HD texture loading (2048×2048 / 4096×4096)
- LOD selector (LOD 0–5) with real-time viewport switching
- Skeleton viewer with bone hierarchy tree
- Group export — export related asset chunks as a single GLB
- Hex inspector with named export
- Smart search with multi-token AND filtering

## Requirements
- Python 3.8+
- PyQt6
- PyOpenGL
- numpy
- Pillow
- imagecodecs (`pip install imagecodecs`)
- Ratchet & Clank: Rift Apart (PC) installed via Steam
- `hashes.txt` from [Overstrike](https://github.com/Tkachov/overstrike)

## Usage
```
python main.py
```
Then click **Open Folder** and point it at your Rift Apart game directory (the folder containing `toc`).

## Notes
- `libdeflate.dll` is bundled and required for HD texture decompression
- HD textures are loaded automatically when a model is selected
- UV scaling is read per-model from the built section for correct texture mapping

## Known Issues
- Stitching textures not yet loading
- Emissive channel not yet applied in viewport shader
- Some sub-meshes (fur, gloves) may appear untextured
- Models with no visible geometry may use bone-space vertices (skinning not yet applied)

## GitHub
https://github.com/ROStudios25/RCRA-Forge
