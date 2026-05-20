"""
core/asset_loader.py
Asset extraction and type-dispatch pipeline for RCRA Forge.

Given an AssetEntry and a TocParser, extracts the raw DAT1 bytes,
determines the asset type, and parses it into the appropriate domain
object. This is pure core logic — no Qt, no signals, no threads.

The Qt threading adapter (AssetLoader QObject) lives in ui/main_window.py
and calls the functions here on a background thread.

Public interface
----------------
load_asset(entry, toc_parser, lookup=None) -> AssetResult
    Extracts and parses one asset. Returns a typed result object.
    Raises on extraction failure; per-type parse errors are caught
    and returned as AssetResult with error set.

load_model_textures(model, entry, toc_parser, lookup) -> dict
    Resolves and decodes all PBR texture slots for a parsed ModelAsset.
    Returns {mat_idx: {role_key: (rgba, width, height, tex_name)}}.
    Never raises — returns {} on total failure.
"""

from __future__ import annotations

import struct
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional, Any


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class AssetResult:
    """Typed result from load_asset()."""
    label:     str               # display name (from lookup or hex fallback)
    raw:       bytes             # raw extracted bytes (always present on success)
    atype:     str               # DAT1 asset type string ('model', 'texture', etc.)
    error:     Optional[str] = None

    # Parsed domain objects — at most one will be set
    model:     Optional[Any] = None   # ModelAsset
    skeleton:  Optional[Any] = None   # Skeleton (companion to model)
    texture:   Optional[Any] = None   # TextureAsset
    zone:      Optional[Any] = None   # ZoneDef
    level:     Optional[Any] = None   # LevelInfo


# ── Main entry point ──────────────────────────────────────────────────────────

def load_asset(entry, toc_parser, lookup=None) -> AssetResult:
    """
    Extract and parse one asset entry.

    Parameters
    ----------
    entry      : AssetEntry from TocParser
    toc_parser : TocParser (already parsed)
    lookup     : HashLookup or None

    Returns
    -------
    AssetResult — always returned, error field set on failure.
    """
    t0 = time.perf_counter()

    print(f"[asset_loader] extracting {entry.asset_id:#018x} "
          f"size={entry.size:,} archive={entry.archive}")

    raw = toc_parser.extract_asset(entry)
    print(f"[asset_loader] extracted {len(raw):,} bytes in {time.perf_counter()-t0:.3f}s")

    # Resolve display label
    if lookup and lookup.is_loaded():
        label = lookup.name(entry.asset_id)
    else:
        label = f'asset_{entry.asset_id:#018x}'

    from core.archive import DAT1, ASSET_TYPE_NAMES
    dat1  = DAT1(raw)
    atype = ASSET_TYPE_NAMES.get(dat1.unk1, '')
    print(f"[asset_loader] DAT1 type={atype} unk1={dat1.unk1:#010x} "
          f"sections={len(dat1.sections)}")

    result = AssetResult(label=label, raw=raw, atype=atype)

    if atype == 'model':
        _parse_model(result, raw, lookup)

    elif atype == 'texture':
        _parse_texture(result, raw)

    elif atype == 'zone':
        _parse_zone(result, raw, entry, label, lookup)

    elif atype == 'level':
        _parse_level(result, raw)

    # Unknown/unhandled types: result.raw is set; callers can inspect raw bytes.

    return result


# ── Per-type parsers ──────────────────────────────────────────────────────────

def _parse_model(result: AssetResult, raw: bytes, lookup) -> None:
    try:
        print("[asset_loader] parsing model…")
        from core.mesh import ModelParser
        from core.skeleton import Skeleton
        model = ModelParser(raw).parse()
        print(f"[asset_loader] model parsed: {len(model.vertexes)} verts, "
              f"{len(model.meshes)} meshes, {len(model.indexes)} indices")
        result.model = model

        skel = Skeleton.from_model(model)
        if skel and skel.bones:
            print(f"[asset_loader] skeleton: {len(skel.bones)} bones")
            result.skeleton = skel

    except Exception as ex:
        result.error = f"model parse failed: {ex}\n{traceback.format_exc()}"
        print(f"[asset_loader] {result.error}")


def _parse_texture(result: AssetResult, raw: bytes) -> None:
    try:
        from core.texture import TextureParser
        result.texture = TextureParser(raw).parse()
    except Exception as ex:
        result.error = f"texture parse failed: {ex}\n{traceback.format_exc()}"
        print(f"[asset_loader] {result.error}")


def _parse_zone(result: AssetResult, raw: bytes, entry, label: str, lookup) -> None:
    try:
        from core.zone import parse_zone_asset
        zone_name = label or f'zone_{entry.asset_id:#018x}'
        zone = parse_zone_asset(raw, entry.asset_id, zone_name, lookup=lookup)
        if zone is not None:
            kind = "art" if zone.is_art_zone else "gp"
            print(f"[asset_loader] zone parsed: {zone.entry_count} scene nodes ({kind} zone)")
            result.zone = zone
        else:
            from core.archive import DAT1, ASSET_TYPE_NAMES
            unk1 = DAT1(raw).unk1
            print(f"[asset_loader] zone parse returned None for unk1={unk1:#010x}")
    except Exception as ex:
        result.error = f"zone parse failed: {ex}\n{traceback.format_exc()}"
        print(f"[asset_loader] {result.error}")


def _parse_level(result: AssetResult, raw: bytes) -> None:
    try:
        from core.level import LevelParser
        result.level = LevelParser(raw).parse_info()
    except Exception as ex:
        result.error = f"level parse failed: {ex}\n{traceback.format_exc()}"
        print(f"[asset_loader] {result.error}")


# ── Texture loading ───────────────────────────────────────────────────────────

def load_model_textures(model, entry, toc_parser, lookup) -> dict:
    """
    Resolve and decode all PBR texture slots for a parsed ModelAsset.

    For each unique material index in LOD0/look0 meshes:
      1. Reads the material name from the model's DAT1 section
      2. Looks up the .material asset in the TOC
      3. Parses it and iterates texture slots
      4. Decodes each slot's texture (SD + HD if available)

    Returns
    -------
    dict  {mat_idx: {role_key: (rgba_bytes, width, height, tex_name)}}
          Empty dict on total failure; partial results otherwise.

    Never raises.
    """
    try:
        from core.material import parse_material_asset
        from core.texture import TextureParser
        from core.archive import DAT1
        from exporters.texture_exporter import EXPORT_ROLES, _role_in_export_roles

        if not lookup or not lookup.is_loaded():
            return {}

        # Collect unique material indices from look 0 / LOD 0 only
        mat_indices = sorted({m.material_index for m in model.meshes
                              if m.look_index == 0 and m.lod_level == 0})

        # Re-extract the model bytes to read TAG_MATERIALS section
        raw  = toc_parser.extract_asset(entry)
        dat1 = DAT1(raw)
        TAG_MAT = 0x3250BB80
        mat_sec = dat1.sections.get(TAG_MAT)

        result = {}  # {mat_idx: {role_key: (rgba, w, h, tex_name)}}

        for mat_idx in mat_indices:
            try:
                mat_name = _resolve_mat_name(dat1, mat_sec, mat_idx)
                if not mat_name:
                    continue

                mat_asset_id = lookup.asset_id(mat_name)
                if mat_asset_id is None:
                    mat_asset_id = lookup.asset_id(mat_name.lstrip('/'))
                if mat_asset_id is None:
                    print(f"[texload] mat[{mat_idx}] path not found: {mat_name}")
                    continue

                mat_entry = toc_parser.find_entry(mat_asset_id)
                if mat_entry is None:
                    continue

                mat_data  = toc_parser.extract_asset(mat_entry)
                mat_asset = parse_material_asset(mat_data)

                mat_result = {}
                for slot in mat_asset.slots:
                    if not _role_in_export_roles(slot.role):
                        continue
                    role_key = slot.role if slot.role not in mat_result else f"{slot.role}_{slot.index}"
                    _decode_slot(
                        slot, role_key, mat_idx, mat_name,
                        toc_parser, lookup, mat_result,
                    )

                if mat_result:
                    result[mat_idx] = mat_result

            except Exception as ex:
                print(f"[texload] mat[{mat_idx}] failed: {ex}")

        return result

    except Exception as ex:
        print(f"[texload] error: {ex}\n{traceback.format_exc()}")
        return {}


def _resolve_mat_name(dat1, mat_sec, mat_idx: int) -> Optional[str]:
    """Read the material path string for mat_idx from the model DAT1."""
    if mat_sec is None:
        return None
    sec  = bytes(mat_sec)
    ENTRY = 16
    if mat_idx * ENTRY + ENTRY > len(sec):
        return None
    matfile_off, _ = struct.unpack_from('<QQ', sec, mat_idx * ENTRY)
    name = dat1.get_string(matfile_off)
    if not name:
        return None
    name = name.replace('\\', '/').lower()
    if not name.endswith('.material'):
        name += '.material'
    return name


def _decode_slot(slot, role_key: str, mat_idx: int, mat_name: str,
                 toc_parser, lookup, mat_result: dict) -> None:
    """Decode one texture slot and add to mat_result if successful."""
    try:
        from core.texture import TextureParser

        tex_path = slot.path.replace('\\', '/').lower()
        tex_id   = lookup.asset_id(tex_path)
        if tex_id is None:
            tex_id = lookup.asset_id(tex_path.lstrip('/'))

        tex_entry = None
        if tex_id is not None:
            tex_entry = toc_parser.find_entry(tex_id)
        if tex_entry is None and slot.asset_id_lo:
            tex_entry = toc_parser.find_entry_by_id_lo(slot.asset_id_lo)
        if tex_entry is None:
            return

        tex_data = toc_parser.extract_asset(tex_entry)
        tex      = TextureParser(tex_data).parse()

        # Attempt HD pixel data load
        if tex.hd_len > 0 and tex.hd_width > 0 and tex_id is not None:
            all_entries  = toc_parser.find_all_entries(tex_id)
            hd_candidates = [e for e in all_entries if e.size > tex_entry.size]
            if hd_candidates:
                hd_entry = max(hd_candidates, key=lambda e: e.size)
                try:
                    hd_raw = toc_parser.extract_asset(hd_entry)
                    tex.hd_pixel_data = bytes(hd_raw)
                    if slot.role == 'albedo':
                        print(f"[texload] mat[{mat_idx}] HD {tex.hd_width}×{tex.hd_height} "
                              f"loaded ({len(hd_raw):,} bytes)")
                except Exception:
                    pass

        rgba = tex.decode_to_rgba()
        if not rgba:
            return
        tex_name = slot.name
        w = tex.hd_width  if tex.hd_pixel_data else tex.width
        h = tex.hd_height if tex.hd_pixel_data else tex.height
        mat_result[role_key] = (rgba, w, h, tex_name)
        if slot.role == 'albedo':
            print(f"[texload] mat[{mat_idx}] '{mat_name}' albedo {w}×{h}")
        else:
            print(f"[texload] mat[{mat_idx}] {slot.role} {w}×{h} ({tex_name})")

    except Exception as ex:
        print(f"[texload] mat[{mat_idx}] {slot.role} failed: {ex}")
