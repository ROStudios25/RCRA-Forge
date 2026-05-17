# RCRA Forge
**Ratchet & Clank: Rift Apart — Asset Browser & Model Exporter**

A standalone Python/PyQt6 desktop tool for browsing, previewing and exporting assets from Ratchet & Clank: Rift Apart (PC) without Ninja Ripper.

---

## Features
- Browse all 340,000+ game assets by name and type
- 3D viewport with HD texture loading (2048×2048 / 4096×4096)
- LOD selector (LOD 0–5) with real-time viewport switching
- Blender-style viewport navigation — LMB orbit, MMB pan, scroll zoom, numpad presets, orthographic toggle
- Skeleton viewer with bone hierarchy tree
- **Export Asset** — export any single model as `.glb`, `.gltf`, `.obj`, `.fbx` or `.ascii` with:
  - Full skeleton with correct inverse bind matrices
  - RCRA skin weights (4 bone influences per vertex)
  - Per sub-mesh named nodes matching the asset browser
  - Correct UV scaling per-model from the built section
  - Named materials (e.g. `hero_Ratchet_Gloves`, `hero_clank_body`)
  - Look 0 / LOD 0 filtering — no bundled props or LOD duplicates
  - **Bundled texture export** — all PBR slots written to a `textures/` subfolder alongside the model (PNG, DDS, or both)
- **Export Group as GLB** — export all parts of a named group into a single GLB, with the same UV, material, skeleton and skin weight correctness as single asset export
- **Export Zone as GLB** — export full art zones with all scene node instances at correct world-space positions and rotations. Each unique model is instanced for compact file sizes
- **ASCII export** — ALERT-compatible `.ascii` format for modding re-import via `ascii_to_model.py`, also importable in Blender 5 via the [XNALara extension](https://extensions.blender.org/add-ons/io-xnalara/)
- **Theme system** — full per-slot colour customisation (Edit → Preferences / Ctrl+,) with built-in presets, live preview, and save/load of custom presets
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

- **Single export:** Select any `.model` asset → click **Export Asset** in the Properties panel.
- **Group export:** Switch to Groups view → select a group → click **Export Group as GLB**.
- **Zone export:** Select any `.zone` asset → click **Export Zone as GLB**. Set Blender's Clip End to 100,000 before importing (zones span several km).

All exports produce files ready for import into Blender, Maya, 3ds Max or Substance Painter — no addons required for GLB/FBX/OBJ.

## Viewport Controls
| Input | Action |
|---|---|
| LMB drag | Orbit |
| MMB drag | Pan |
| Ctrl + MMB drag | Zoom |
| Scroll wheel | Zoom |
| Numpad 1 / 3 / 7 / 9 | Front / Right / Top / Back |
| Numpad 5 | Toggle orthographic |
| F | Frame model |

Customise invert axes and zoom speed via **View → Viewport Controls…** (Ctrl+K).

## Importing
- **GLB/GLTF:** Blender → File → Import → glTF 2.0 (.glb/.gltf)
- **FBX:** Blender → File → Import → FBX (.fbx) — compatible with Blender 4.x and 5.x, Maya, 3ds Max, Substance Painter
- **OBJ:** Blender → File → Import → Wavefront (.obj)
- **ASCII:** Blender 5 → [XNALara extension](https://extensions.blender.org/add-ons/io-xnalara/) → Import → XNALara/XPS (.ascii)
- **Zone GLB:** Set Clip End to 100,000 in Blender before importing — zones are several kilometres across

## Notes
- `libdeflate.dll` is bundled and required for HD texture decompression
- HD textures are loaded automatically when a model is selected
- UV scaling is read per-model from the built section (`0x283D0383`) for correct texture mapping
- Composite shell meshes (fur) are included in export — delete `compositeshell` and `head_fur` meshes by name in Blender after export
- Exported textures are written to a `textures/` subfolder alongside the model file — PNG is universally compatible, DDS preserves BCn compression
- Theme settings are saved to `config.json` in the application folder and reloaded on next launch

## Known Issues
- Stitching textures not yet loading
- Emissive/glow channel not yet applied in viewport shader
- Fur and composite shell meshes have no albedo texture by design — delete in Blender after export if not needed
- Models with no visible geometry may use bone-space vertices (bind pose not yet applied in viewport)
- Some bangle assets appear very dark in viewport — this is correct; the albedo texture is near-black by design
- Zone export: Y-axis rotation only confirmed — some props may have incorrect orientation pending full rotation matrix support
- Streaming archive assets (e.g. certain zone models) are not in the main TOC and will be skipped during zone export

## GitHub
https://github.com/ROStudios25/RCRA-Forge

---

## Changelog

### v0.5.7
- **New: Zone art export fixed** — all scene nodes in art zones now resolve correctly. Root cause: model index is stored at `+0xF0` in each 320-byte entry as a direct u32 index into the model table. Sentinel `0xFFFFFFFF` = no model (non-renderable node). TAG_MODEL_INDICES is a presence set only, not a model index table
- **Fix: Entry size rounding** — zones where payload/count is non-integer now round to nearest multiple of 4 correctly
- **New: Theme system** — full per-slot colour customisation via **Edit → Preferences** (Ctrl+,):
  - 31 named colour slots grouped by category
  - 4 built-in presets: Dark (Default), Midnight Blue, Slate, Light
  - Click any swatch to open the colour picker — changes apply live to the whole UI
  - Save current colours as a named user preset; delete user presets
  - Theme persists to `config.json` and reloads on next launch
- **Fix: Properties panel** now scrolls when too short — no more clipping or button overlap on resize
- **Fix: Progress dialog** minimum width and readable percentage text
- **Fix: Texture viewer** minimum height — zoom slider and info strip no longer overlap canvas
- **Fix: Bottom panel** minimum height stops tab panel collapsing below usable size
- **Fix: Viewport clear color** matches UI background on startup
- **New files:** `core/theme.py`, `ui/preferences_dialog.py`

### v0.5.6
- **New: Zone export** — export full art zones as GLB with all scene node instances at correct world-space positions and Y-axis rotations. Confirmed working for i29 Megalopolis: 15,835 walkway nodes, 1,623 + 6,889 architecture nodes
- **New: Mesh instancing** — each unique model appears once in the GLB; all nodes reference it by index for compact file sizes
- **New: `Export Zone as GLB`** and **`Dump Zone Entry Data`** buttons in Properties panel
- **Confirmed art zone binary format** (320-byte entries): `+0x00` Y-axis rotation row 0, `+0x10` world XYZ, `+0xF0` model table index
- **New files:** `core/zone.py`, `core/level_assembler.py`

### v0.5.5
- **New: Bundled texture export** — PNG/DDS alongside model exports, all PBR slots, shared textures deduplicated
- **Fix: Material slot roles** corrected (credit: ilaac): `_c` base color, `_g` specular, `_m` R=emission/G=height/B=AO, `_ao` dedicated AO, `_sm` NPC micro-variation
- **Fix: Viewport texture priority** — strictly base_color → color_id only
- **Fix: Spurious model re-extraction** in `_on_materials_ready` eliminated
- **New files:** `exporters/texture_exporter.py`

### v0.5.4
- **New: Blender-style viewport controls** — LMB orbit, MMB pan, Ctrl+MMB zoom, scroll zoom, numpad presets, ortho toggle, F to frame
- **New: Viewport Controls dialog** (Ctrl+K) — invert axes, zoom speed, persisted settings
- **New: ASCII export** — ALERT-compatible, importable in Blender 5 via XNALara extension
- **Fix: Bangle/accessory LOD0** — all meshes now render (look parser reversed)
- **Fix: Improved viewport lighting** — front fill, two-sided NdL, raised ambient

### v0.5.3
- **Fix:** Group GLB mesh index remapping — all parts reference correct geometry
- **Fix:** No more Icosphere empties on GLB import in Blender
- **Fix:** `run.bat` `py` → `python3` → `python` fallback chain
- **New:** `rcra_empties_to_collections.py` Blender utility script

### v0.5.2
- **Fix: Viewport texture display** — HD albedo loads and displays correctly
- **Fix:** OpenGL uploads on main thread, `glGenTextures` int cast, unique texture ID tracking
- **Fix:** `imagecodecs` in PyInstaller spec — textures work in standalone exe
- **New:** `run.bat`, `requirements.txt`

### v0.5.1
- **Fix: FBX binary exporter** — correct scale/UID/node names, compatible with Blender 4.x and 5.x

### v0.5.0
- **New: GLB exporter** — full skeleton, skin weights, UV, named materials, look/LOD filtering
- **New: FBX exporter** (binary 7.4)
- **New: Group export** — all parts of a named group into a single GLB

---

## Credits

**ROStudios25** — Project concept, direction, testing and community research

**Claude AI (Anthropic)** — Codebase developed with AI assistance, translating the project concept into working Python code

**Tkachov** — [ALERT (Amazing Luna Engine Research Tools)](https://github.com/Tkachov/ALERT) — format documentation, struct definitions and GDeflate decompressor that made this possible

**thtrandomlurker** — [io_mesh_riftapart](https://github.com/thtrandomlurker/io_mesh_riftapart) — Rift Apart Blender importer; material section format, UV scaling, mesh subset layout and bone transform reading confirmed from this source

**Fanis** — Community RE research, initial `_m` channel investigation

**ilaac** — Material slot role corrections (May 2026): `_c` base color, `_g` specular, `_m` packed channels, `_ao` dedicated AO, `id_` color ID, `_sm` NPC micro-variation. UV scaling research and Blender import documentation

**neptuwunium** — [rivet](https://github.com/neptuwunium/rivet) and [rivet_hook](https://github.com/neptuwunium/rivet_hook) — reverse-engineered the complete Rift Apart DDL schema (8,211 type definitions, 863 enums). Essential reference for level/zone parsing

**macton (Mike Acton)** — [DDLParser](https://github.com/macton/DDLParser) — Insomniac Games' original DDL schema parser

> **Development note:** This project was conceived and directed by ROStudios25, who had the original idea of building a native PC tool for browsing and exporting Rift Apart assets without relying on Ninja Ripper. The codebase was developed with the assistance of Claude AI (Anthropic). All format research and struct definitions are sourced from the ALERT project by Tkachov, thtrandomlurker's Blender importer, and community reverse engineering work. The idea, direction, testing, and persistence were human — the Python was AI-assisted.
