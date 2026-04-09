# RCRA Forge

**Ratchet & Clank: Rift Apart — Level Editor & Asset Exporter (PC)** — v0.3.0

A Python/PyQt6 desktop application for browsing, previewing, and exporting assets from the PC version of Ratchet & Clank: Rift Apart — without needing Ninja Ripper.

> ⚠️ **Early development** — format parsing is based on community reverse engineering. Contributions and corrections welcome!

---

## Features

| Panel | Description |
|---|---|
| **Asset Browser** | Parses the game's `toc` file in ~0.03s, lists all 340,661 assets with real names from `hashes.txt` |
| **Groups** | Auto-detects related assets by name prefix — enemy parts, chunk meshes, LODs all collapse into expandable groups |
| **Smart Search** | Multi-word AND search (`enm chunk`), runs on a background thread, auto-groups results by file type |
| **3D Viewport** | PyOpenGL viewer — right-drag orbit, middle-drag pan, scroll zoom, wireframe toggle, view presets |
| **Texture Viewer** | Decodes BCn/DDS textures, exports `.dds` |
| **Scene Panel** | Shows DAT1 section info for level/zone assets |
| **Skeleton Viewer** | Bone hierarchy tree with 2D rest-pose projection |
| **Hex Inspector** | Raw DAT1 byte viewer with jump-to-offset |
| **Export** | One-click export to `.glb`, `.gltf`, or `.obj` for Blender |
| **Group Export** | Export all parts of a character/object as a single `.glb` with named mesh nodes |

---

## What's New in v0.3.0

- ✅ **Group export** — batch export all parts of an enemy/character as one GLB with named mesh nodes, opens cleanly in Blender's outliner
- ✅ **Groups toggle** — amber group rows in the asset browser, assets auto-grouped by shared name prefix (`_chunk_NN`, `_lod`, `_damaged`, `_body`, etc.)
- ✅ **Smart search grouping** — results auto-group by slug when a file format filter is active
- ✅ **Multi-token AND search** — `enm chunk` or `enm_chunk` finds assets containing both words
- ✅ **Debounced background search** — 200ms debounce + QThread worker, no UI freezing while typing
- ✅ **Groups toggle respects search** — toggling on/off keeps active search results
- ✅ **Freely resizable panels** — drag any splitter as wide as needed to read long asset names
- ✅ **Wider splitter handles** — 4px with hover/pressed highlight for easier grabbing

---

## What's New in v0.2.0

- ✅ **3D viewport working** — models render correctly with per-mesh colors and lighting
- ✅ **Camera controls** — orbit (right-drag), pan (middle-drag), zoom (scroll wheel)
- ✅ **View presets** — Main, Front, Back, Right, Left, Top, Bottom
- ✅ **Wireframe mode** — toggle in toolbar
- ✅ **Frame button** — resets camera to fit loaded model
- ✅ **Asset name lookup** — `hashes.txt` integration shows real filenames (384,260 entries)
- ✅ **TOC loads instantly** — ~0.03 seconds for 340,661 assets

---

## Known Issues

### 🐛 Textures show as flat colors
Rift Apart texture pixel data is split across the DAT1 asset and a separate HD archive. The HD pixel data read is not yet fully implemented — models render with flat shading only.

### 🐛 Group export geometry
The group export GLB structure is correct but mesh geometry accuracy depends on the vertex format parsing in `core/mesh.py`. Verify results in Blender and report any issues.

### 🐛 GDeflate decompression unavailable
Assets in GDeflate-compressed archives (`comp_type=2`) cannot be extracted until the `gdeflate` Python module is available.

---

## Requirements

- Python 3.8+
- PyQt6, PyOpenGL, NumPy (see `requirements.txt`)
- **Ratchet & Clank: Rift Apart** (Steam PC)
- **`hashes.txt`** from [Overstrike/Overdrive](https://github.com/Tkachov/overstrike) — **required** for asset names and grouping. Without it, all assets show as raw hex IDs and Groups mode is unavailable. Place it in the same folder as the game's `toc` file or next to the RCRA Forge executable.

---

## Installation (from source)

```bash
git clone https://github.com/ROStudios25/RCRA-Forge
cd RCRA-Forge
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
python main.py
```

## Building the EXE (Windows)

```bash
build_windows.bat
# Output: dist\RCRA_Forge\RCRA_Forge.exe
```

## Running the demo (no game needed)

```bash
python demo.py
```

---

## Usage

### Basic browsing
1. **File → Open Game Folder** → select your Rift Apart Steam install directory
2. Wait for the TOC to load (~0.03s) and names to populate (~1-2s)
3. **Double-click** any asset to load it into the 3D viewer
4. Use **right-drag** to orbit, **middle-drag** to pan, **scroll** to zoom

### Searching
- Type in the search box to filter by name — results appear after a 200ms pause
- Use **spaces or underscores** to AND multiple terms: `enm chunk` finds assets containing both `enm` and `chunk`
- Select a file type (`.model`, `.texture`, etc.) from the dropdown to filter by extension
- When a file type is selected, results automatically group by shared name prefix

### Groups
- Click the **⬡ Groups** button to switch the browser into grouped view
- Assets are auto-grouped by shared name prefix — enemy parts, chunk meshes, LOD levels all collapse together
- **Expand** a group to see all its parts
- **Double-click** a group header to select it for batch export

### Exporting
- **Single asset** — double-click an asset, choose format (GLB/GLTF/OBJ), click **Export Asset**
- **Group export** — double-click a group header, click **⬡ Export Group (N parts)** in the Properties panel
  - All parts export as one `.glb` with named mesh nodes
  - In Blender: **File → Import → glTF 2.0** — each part appears as a separate object under a shared root

### Known test assets

| Asset ID | Path |
|---|---|
| `94A4B69B67D5CC42` | `characters/npc/npc_zurkon_jr/npc_zurkon_jr.model` |
| `8D98795E786B0206` | `characters/npc/npc_civ_robot_01/npc_civ_robot_01.model` |

---

## Format Notes

### TOC structure (RCRA)
```
magic:  0x34E89035  (NOT zlib compressed, unlike Spider-Man/MSMR)
size:   uint32
data:   DAT1 container (magic 0x44415431)
```

### TOC DAT1 sections
| Tag | Description | Entry size |
|---|---|---|
| `0x398ABFF0` | Archive filenames | 66B |
| `0x506D7B8A` | Asset IDs (uint64 CRC64) | 8B |
| `0x65BCF461` | Asset metadata | 16B |
| `0x654BDED9` | Asset header blobs | 36B |

### Model DAT1 sections
| Tag | Description |
|---|---|
| `0xA98BE69B` | Vertices — 16B: `<4h I 2h>` (xyz, packed normal, uv) |
| `0x0859863D` | Indices — uint16, NOT delta-encoded for RCRA |
| `0x78D9CBDE` | Mesh definitions — 64B each |
| `0x15DF9D3B` | Joint definitions — 16B each |
| `0xDCC88A19` | Joint transforms — 3×4 + 4×4 float matrices |
| `0xCCBAFF15` | RCRA skin weights — 8B each |

---

## Credits

- **[ALERT](https://github.com/Tkachov/ALERT)** by Tkachov (GPL) — primary format reference
- **[ripped_apart](https://github.com/chaoticgd/ripped_apart)** by chaoticgd (MIT)
- **thtrandomlurker** — RCRA mesh format RE
- **CRC64** based on [InsomniacArchive](https://github.com/team-waldo/InsomniacArchive) by akintos

---

## Contributing

Contributions welcome, especially for:
- **Texture pixel data** — HD archive texture read (`core/texture.py`)
- **Level/zone parsing** — `core/level.py` stubs need real section parsers
- **Group export accuracy** — verifying GLB output against known-good models
- **Vertex format accuracy** — verifying the 16-byte vertex decode in `core/mesh.py`

Please open an issue before a large PR.
