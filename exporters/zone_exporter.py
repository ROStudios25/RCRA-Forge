"""
exporters/zone_exporter.py
GLB export for assembled RCRA zones.

Moved from core/level_assembler.py so that all GLB writers live together
in exporters/ and share the same GLB constants from gltf_exporter.py.

Public interface
----------------
export_zone_glb(assembled, out_path, lod=0) -> int
    Write the assembled zone to a .glb file.
    Returns the number of scene nodes written.

zone_short(name) -> str
    Convert a full zone path to a short display name.
    e.g. 'levels/megalopolis/art_zone.zone' -> 'art_zone'
"""

from __future__ import annotations

import json
import struct
from typing import TYPE_CHECKING

import numpy as np

from core.mesh import mesh_to_numpy
from exporters.gltf_exporter import (
    GLB_MAGIC,
    GLB_JSON_CHUNK,
    GLB_BIN_CHUNK,
)

if TYPE_CHECKING:
    from core.level_assembler import AssembledZone


def zone_short(name: str) -> str:
    """'levels/foo/art.zone'  →  'art'"""
    return name.split('/')[-1].replace('.zone', '')


def export_zone_glb(assembled: 'AssembledZone', out_path: str, lod: int = 0) -> int:
    """
    Export an assembled zone as a single GLB file.

    Each resolved scene node becomes a named mesh node carrying its
    world transform matrix.  Geometry for repeated model IDs is written
    to the buffer only once (mesh instancing via shared mesh indices).

    Parameters
    ----------
    assembled : AssembledZone  — output of LevelAssembler.assemble_zone()
    out_path  : str            — destination .glb path
    lod       : int            — LOD level to export (default 0)

    Returns
    -------
    int  — number of scene nodes written
    """
    class _SafeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.floating, np.float32, np.float64)):
                return float(obj)
            if isinstance(obj, np.integer):
                return int(obj)
            return super().default(obj)

    gltf = {
        "asset": {"version": "2.0", "generator": "RCRA Forge",
                  "extras": {"unitScale": 0.01}},
        "scene": 0,
        "scenes": [{"name": zone_short(assembled.zone.name), "nodes": []}],
        "nodes":       [],
        "meshes":      [],
        "accessors":   [],
        "bufferViews": [],
        "buffers":     [{"byteLength": 0}],
    }

    buffer_data = bytearray()
    scene_nodes = gltf["scenes"][0]["nodes"]

    # mesh_cache: model_asset_id → [mesh_idx, ...]
    # Each unique model's geometry is written to the buffer only once.
    mesh_cache: dict[int, list[int]] = {}

    for an in assembled.nodes:
        model    = an.model
        asset_id = an.model_asset_id

        if asset_id and asset_id in mesh_cache:
            cached_mesh_indices = mesh_cache[asset_id]
        else:
            sub_meshes = [m for m in model.meshes
                          if m.look_index == 0 and m.lod_level == lod]
            if not sub_meshes:
                sub_meshes = [m for m in model.meshes if m.look_index == 0]

            cached_mesh_indices = []
            model_name = an.model_path.split('/')[-1]

            for mesh in sub_meshes:
                pos, nrm, uvs, idx = mesh_to_numpy(model, mesh)
                if pos is None or idx is None:
                    continue

                _, pos_acc = _write_accessor(
                    gltf, buffer_data, pos.tobytes(),
                    'VEC3', 5126, len(pos),
                    mins=[float(v) for v in pos.min(0).tolist()],
                    maxs=[float(v) for v in pos.max(0).tolist()],
                )

                idx_flat = idx.flatten().astype('uint32')
                _, idx_acc = _write_accessor(
                    gltf, buffer_data, idx_flat.tobytes(),
                    'SCALAR', 5125, len(idx_flat),
                )

                prim: dict = {"attributes": {"POSITION": pos_acc}, "indices": idx_acc}

                if uvs is not None:
                    _, uv_acc = _write_accessor(
                        gltf, buffer_data, uvs.tobytes(),
                        'VEC2', 5126, len(uvs),
                    )
                    prim["attributes"]["TEXCOORD_0"] = uv_acc

                mesh_idx = len(gltf["meshes"])
                gltf["meshes"].append({"name": model_name, "primitives": [prim]})
                cached_mesh_indices.append(mesh_idx)

            if asset_id:
                mesh_cache[asset_id] = cached_mesh_indices

        if not cached_mesh_indices:
            continue

        short  = an.entry.name or an.model_path.split('/')[-1].replace('.model', '')
        matrix = [float(v) for v in an.world_matrix]

        if len(cached_mesh_indices) == 1:
            parent_idx = len(gltf["nodes"])
            gltf["nodes"].append({
                "name": short,
                "mesh": cached_mesh_indices[0],
                "matrix": matrix,
            })
            scene_nodes.append(parent_idx)
        else:
            child_indices = []
            for mesh_idx in cached_mesh_indices:
                child_idx = len(gltf["nodes"])
                gltf["nodes"].append({"mesh": mesh_idx})
                child_indices.append(child_idx)

            parent_idx = len(gltf["nodes"])
            gltf["nodes"].append({
                "name": short,
                "matrix": matrix,
                "children": child_indices,
            })
            scene_nodes.append(parent_idx)

    # Finalise buffer length
    gltf["buffers"][0]["byteLength"] = len(buffer_data)

    # Serialise and write GLB
    json_bytes = json.dumps(gltf, separators=(',', ':'), cls=_SafeEncoder).encode('utf-8')
    while len(json_bytes) % 4:
        json_bytes += b' '
    while len(buffer_data) % 4:
        buffer_data += b'\x00'

    total = 12 + 8 + len(json_bytes) + 8 + len(buffer_data)
    with open(out_path, 'wb') as f:
        f.write(struct.pack('<III', GLB_MAGIC, 2, total))
        f.write(struct.pack('<II',  len(json_bytes),  GLB_JSON_CHUNK))
        f.write(json_bytes)
        f.write(struct.pack('<II',  len(buffer_data), GLB_BIN_CHUNK))
        f.write(buffer_data)

    return len(assembled.nodes)


# ── Internal helper ───────────────────────────────────────────────────────────

def _write_accessor(gltf: dict, buf: bytearray, data: bytes,
                    acc_type: str, component_type: int, count: int,
                    mins=None, maxs=None) -> tuple[int, int]:
    """
    Append *data* to *buf*, add a bufferView and an accessor to *gltf*.

    Returns (bufferView_index, accessor_index).
    """
    # Align to 4-byte boundary
    while len(buf) % 4:
        buf += b'\x00'
    bv_offset = len(buf)
    buf += data

    bv_idx = len(gltf["bufferViews"])
    gltf["bufferViews"].append({
        "buffer": 0, "byteOffset": bv_offset, "byteLength": len(data),
    })

    acc: dict = {
        "bufferView":    bv_idx,
        "byteOffset":    0,
        "componentType": component_type,
        "count":         count,
        "type":          acc_type,
    }
    if mins is not None:
        acc["min"] = mins
        acc["max"] = maxs

    acc_idx = len(gltf["accessors"])
    gltf["accessors"].append(acc)
    return bv_idx, acc_idx
