# RCRA Forge

**Ratchet & Clank: Rift Apart — Level Editor & Asset Exporter (PC)** — v0.2.0

A Python/PyQt6 desktop application for browsing, previewing, and exporting assets from the PC version of Ratchet & Clank: Rift Apart — without needing Ninja Ripper.

> ⚠️ **Early development** — format parsing is based on community reverse engineering. Contributions and corrections welcome!

---

## Features

| Panel | Description |
|---|---|
| **Asset Browser** | Parses the game's `toc` file in ~0.03s, lists all 340,661 assets with real names from `hashes.txt` |
| **3D Viewport** | PyOpenGL viewer — right-drag orbit, middle-drag pan, scroll zoom, wireframe toggle, view presets |
| **Texture Viewer** | Decodes BCn/DDS textures, exports `.dds` |
| **Scene Panel** | Shows DAT1 section info for level/zone assets |
| **Skeleton Viewer** | Bone hierarchy tree with 2D rest-pose projection |
| **Hex Inspector** | Raw DAT1 byte viewer with jump-to-offset |
| **Export** | One-click export to `.glb`, `.gltf`, or `.obj` for Blender |

---

## What's New in v0.2.0

- ✅ **3D viewport working** — models render correctly with per-mesh colors and lighting
- ✅ **Camera controls** — orbit (right-drag), pan (middle-drag), zoom (scroll wheel)
- ✅ **View presets** — Main, Front, Back, Right, Left, Top, Bottom
- ✅ **Wireframe mode** — toggle in toolbar
- ✅ **Frame button** — resets camera to fit loaded model
- ✅ **Asset name lookup** — `hashes.txt` integration shows real filenames (384,260 entries)
- ✅ **TOC loads instantly** — ~0.03 seconds for 340,661 assets
- ✅ **Fixed ModelRcra** — correct magic `0x9D2C0FA9` for Rift Apart models
- ✅ **Fixed DAT1 parsing** — handles header blob offset correctly

---

## Known Issues

### 🐛 Grid not visible in viewport
The floor grid renders but may not be visible depending on scene scale.

### 🐛 Textures show as blank
Rift Apart texture pixel data is split across the DAT1 asset and a separate HD archive. The HD pixel data read is not yet fully implemented.

### 🐛 GDeflate decompression unavailable
Assets in GDeflate-compressed archives (`comp_type=2`) cannot be extracted until the `gdeflate` Python module is available.

---

## Requirements

- Python 3.10+
- PyQt6, PyOpenGL, NumPy (see `requirements.txt`)
- **Ratchet & Clank: Rift Apart** (Steam PC)
- **`hashes.txt`** from [Overstrike/Overdrive](https://github.com/Tkachov/overstrike) — **required** for asset names. Without it, all assets show as raw hex IDs. Place it in the same folder as the game's `toc` file or next to the RCRA Forge executable.

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

1. **File → Open Game Folder** → select your Rift Apart Steam install directory
2. Wait for the TOC to load (~0.03s) — assets appear grouped by archive with real filenames
3. Use the **search box** to find assets by name (e.g. `zurkon`) or hex ID
4. Use the **type filter** to narrow by extension (`.model`, `.texture`, `.zone`, etc.)
5. **Double-click** any asset to load it into the 3D viewer
6. Use **right-drag** to orbit, **middle-drag** to pan, **scroll** to zoom
7. Select export format (GLB/GLTF/OBJ) in the Properties panel and click **Export Asset**

### Known test assets (from [ALERT PR #17](https://github.com/Tkachov/ALERT/pull/17))

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
- **TOC load performance** — profiling `core/archive.py`
- **Vertex format accuracy** — verifying `core/mesh.py`
- **Texture pixel data** — HD archive texture read
- **Level/zone parsing** — `core/level.py` stubs need real section parsers

Please open an issue before a large PR.
