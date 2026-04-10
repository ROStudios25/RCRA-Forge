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

---

## Credits

**ROStudios25** — Project concept, direction, testing and community research

**Claude AI (Anthropic)** — Initial codebase developed with AI assistance, translating the project concept into working Python code

**Tkachov** — [ALERT (Amazing Luna Engine Research Tools)](https://github.com/Tkachov/ALERT) — format documentation, struct definitions and GDeflate decompressor that made this possible

**Fanis** — Community RE research, material channel breakdown (_m = AO + Emission confirmed)

**ilaac** — UV scaling research and tutorial documentation for Rift Apart models in Blender

> **Development note:** This project was conceived and directed by ROStudios25, who had the original idea of building a native PC tool for browsing and exporting Rift Apart assets without relying on Ninja Ripper. The initial codebase was developed with the assistance of Claude AI (Anthropic). All format research and struct definitions are sourced from the ALERT project by Tkachov and community reverse engineering work. The idea, direction, testing, and persistence were human — the Python was AI-assisted.
