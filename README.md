# RCRA Forge
**Ratchet & Clank: Rift Apart — Asset Browser & Model Exporter**

A standalone Python/PyQt6 desktop tool for browsing, previewing and exporting assets from Ratchet & Clank: Rift Apart (PC) without Ninja Ripper.

---

## Features
- Browse all 340,000+ game assets by name and type
- 3D viewport with HD texture loading (2048×2048 / 4096×4096)
- LOD selector (LOD 0–5) with real-time viewport switching
- Skeleton viewer with bone hierarchy tree
- **Export Asset** — export any single model as `.glb`, `.gltf`, `.obj` or `.fbx` with:
  - Full skeleton with correct inverse bind matrices
  - RCRA skin weights (4 bone influences per vertex)
  - Per sub-mesh named nodes matching the asset browser
  - Correct UV scaling per-model from the built section
  - Named materials (e.g. `hero_Ratchet_Gloves`, `hero_clank_body`)
  - Look 0 / LOD 0 filtering — no bundled props or LOD duplicates
- **Export Group as GLB** — export all parts of a named group into a single GLB, with the same UV, material, skeleton and skin weight correctness as single asset export. Each part appears as a named node under a shared root in Blender's outliner
- Hex inspector with named export
- Smart search with multi-token AND filtering

## Requirements
- [Python 3.10+](https://python.org) — during install, check **"Add Python to PATH"**
- Ratchet & Clank: Rift Apart (PC) installed via Steam
- `hashes.txt` from [Overstrike](https://github.com/Tkachov/overstrike)

Everything else (PyQt6, PyOpenGL, numpy, Pillow, imagecodecs) is installed automatically on first run.

## Usage

**Option 1 — From source (easiest, double-click):**
```
run.bat
```
Automatically finds Python, creates a virtual environment, installs all dependencies, and launches the app. All of this only happens once — subsequent launches are instant.

> **Troubleshooting:** If you see "Python not found" but Python IS installed, open a new terminal and run `py --version`. If that works, Python is installed but not in PATH — the script will handle this automatically on Windows 10/11 via the `py` launcher.

**Option 2 — Standalone exe:**
```
build_windows.bat
```
Builds `dist\RCRA_Forge\RCRA_Forge.exe` using PyInstaller. No Python required on the end user's machine.

Then click **Open Folder** and point it at your Rift Apart game directory (the folder containing `toc`).

- **Single export:** Select any `.model` asset, then click **Export Asset** in the Properties panel. Supported formats: GLB, GLTF, OBJ, FBX.
- **Group export:** Switch to the Groups view in the asset browser, select a group, then click **Export Group as GLB** in the Properties panel.

All exports produce files ready for import into Blender, Maya, 3ds Max or Substance Painter — no addons required.

## Importing
- **GLB/GLTF:** Blender → File → Import → glTF 2.0 (.glb/.gltf)
- **FBX:** Blender → File → Import → FBX (.fbx) — compatible with Blender 4.x and 5.x, Maya, 3ds Max, Substance Painter
- **OBJ:** Blender → File → Import → Wavefront (.obj)

## Notes
- `libdeflate.dll` is bundled and required for HD texture decompression
- HD textures are loaded automatically when a model is selected
- UV scaling is read per-model from the built section (`0x283D0383`) for correct texture mapping
- Composite shell meshes (fur) are included in export — delete in Blender if not needed, or replace with particle hair

## Known Issues
- Stitching textures not yet loading
- Emissive/glow channel not yet applied in viewport shader
- Fur and composite shell materials have no albedo texture by design — delete in Blender after export if not needed
- Models with no visible geometry may use bone-space vertices (skinning not yet applied in viewport)

## GitHub
https://github.com/ROStudios25/RCRA-Forge

---

## Changelog

### v0.5.3
- **Fix:** Group GLB export mesh index remapping — mesh index in nodes was never offset by `mesh_offset`, causing all parts in a group export to reference mesh 0 instead of their own geometry
- **Fix:** `GroupExportWorker` now uses the shared `TocParser` instance instead of re-instantiating one per part — fixes wrong API calls (`read_asset`/`parse_model_asset` → `extract_asset`/`ModelParser`) and removes significant per-part overhead
- **Fix:** Export List now uses the shared `_toc_parser` directly instead of constructing a new `TocParser` on every export
- **Fix:** Stray `part_name = self.group.entries[i]` line removed — was overwriting the resolved display name with a raw entry object on every iteration
- **Fix:** Skeleton root bones no longer added to `scenes[0].nodes` in GLB output — Blender was rendering each unparented bone as a visible Icosphere empty in the scene
- **Fix:** `run.bat` now tries `py` → `python3` → `python` in order — users with Python 3.10+ installed via the Windows Python Launcher (common on 3.12+/3.14) no longer see a false "Python not found" error
- **Fix:** Texture albedo slot fallback — materials with non-standard path suffixes now fall back to `asset_id_lo` stored in the slot for direct TOC lookup, rather than silently showing nothing
- **New:** `rcra_empties_to_collections.py` — Blender utility script to reorganise a group GLB import: converts top-level empties into Collections and moves mesh children in
- **Known issue (WIP):** Some bangle materials (e.g. rebellion boots) still show no texture in viewport — `asset_id_lo` fallback is in place, root cause under investigation

### v0.5.2
- **Viewport texture display fixed** — HD albedo textures (2048×2048) now load and display correctly in the 3D viewport
- Fixed: background thread was killed before texture loading completed (mesh_ready signal was incorrectly quitting the asset loader thread)
- Fixed: OpenGL texture uploads now always happen on the main thread via paintGL
- Fixed: `glGenTextures` return value cast to `int` (PyOpenGL returns numpy array, not integer)
- Fixed: unique texture IDs tracked to prevent double-deletion on model reload
- Fixed: `imagecodecs` added to PyInstaller spec — textures now work correctly in the standalone exe build
- Fixed: `fbx_exporter`, `group_exporter`, `core.material` and `core.hashes` added to PyInstaller spec
- Fixed: pip version check notice suppressed in `run.bat` and `build_windows.bat`
- Added: `run.bat` — double-click launcher that auto-installs requirements on first run
- Added: `requirements.txt`
- Known limitations: emissive/glow textures not yet applied in viewport; fur and composite shell materials have no albedo by design

### v0.5.1
- **FBX binary exporter fixed** — models now import at correct scale and without errors in Blender 4.x and 5.x, Maya, 3ds Max and Substance Painter
- Fixed: `UnitScaleFactor = 100` for meter-scale data (was 1, causing models to import 100× too small)
- Fixed: UIDs written as 64-bit Long (`L` type) as required by Blender's FBX importer
- Fixed: object node names use `Name\x00\x01Class` binary separator
- Fixed: FBX class strings match top-level type (`Geometry`, `Model`, `Deformer`, `SubDeformer`)
- Fixed: `RotationActive` property value written as `INT32` not bool
- Fixed: `Shading` node written as `"Y"` string

### v0.5.0
- **GLB exporter** — full skeleton, skin weights, correct UVs, named materials, look/LOD filtering
- **FBX exporter** (binary 7.4) — initial release
- **Group export** — all parts of a named group into a single GLB with same quality as single export
- Export filename matches asset browser name
- Viewport look 0 filtering — no bundled props in 3D view
- About dialog updated to v0.5.0

---

## Credits

**ROStudios25** — Project concept, direction, testing and community research

**Claude AI (Anthropic)** — Codebase developed with AI assistance, translating the project concept into working Python code

**Tkachov** — [ALERT (Amazing Luna Engine Research Tools)](https://github.com/Tkachov/ALERT) — format documentation, struct definitions and GDeflate decompressor that made this possible

**thtrandomlurker** — [io_mesh_riftapart](https://github.com/thtrandomlurker/io_mesh_riftapart) — Rift Apart Blender importer; material section format, UV scaling, mesh subset layout and bone transform reading confirmed from this source

**Fanis** — Community RE research, material channel breakdown (_m = AO + Emission confirmed)

**ilaac** — UV scaling research and tutorial documentation for Rift Apart models in Blender

> **Development note:** This project was conceived and directed by ROStudios25, who had the original idea of building a native PC tool for browsing and exporting Rift Apart assets without relying on Ninja Ripper. The codebase was developed with the assistance of Claude AI (Anthropic). All format research and struct definitions are sourced from the ALERT project by Tkachov, thtrandomlurker's Blender importer, and community reverse engineering work. The idea, direction, testing, and persistence were human — the Python was AI-assisted.
