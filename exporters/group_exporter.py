"""
exporters/group_exporter.py
Batch-export a group of related ModelAssets as a single GLB.

Each source asset becomes one or more named mesh nodes in the output file so
that Blender's outliner shows:

    npc_grunthor                     ← scene root (empty)
      ├─ npc_grunthor_body           ← mesh node
      ├─ npc_grunthor_arm_l          ← mesh node
      ├─ npc_grunthor_damaged_01     ← mesh node
      └─ ...

Usage
-----
    from exporters.group_exporter import GroupExporter
    exporter = GroupExporter(slug="npc_grunthor")
    exporter.add_model(model_asset, part_name="npc_grunthor_body")
    exporter.add_model(model_asset2, part_name="npc_grunthor_arm_l")
    exporter.export_glb("/path/to/npc_grunthor.glb")
"""

from __future__ import annotations

import json
import os
import struct
from typing import Optional

import numpy as np

from core.mesh import ModelAsset, MeshDefinition, mesh_to_numpy

# glTF constants (same as gltf_exporter.py)
GLTF_FLOAT          = 5126
GLTF_UNSIGNED_SHORT = 5123
GLTF_UNSIGNED_INT   = 5125
GLTF_ARRAY_BUFFER   = 34962
GLTF_ELEMENT_ARRAY  = 34963

GLB_MAGIC      = 0x46546C67
GLB_JSON_CHUNK = 0x4E4F534A
GLB_BIN_CHUNK  = 0x004E4942


class GroupExporter:
    """
    Accumulate multiple ModelAssets and write them as a single GLB where
    each source asset is a named node under a shared root.
    """

    def __init__(self, slug: str = "group"):
        self.slug   = slug
        self._parts: list[tuple[ModelAsset, str]] = []  # (model, part_name)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_model(self, model: ModelAsset, part_name: str):
        """Add one model (= one asset) to the group under *part_name*."""
        self._parts.append((model, part_name))

    def export_glb(self, path: str):
        """Write all accumulated parts to *path* as a single .glb."""
        doc, binary = self._build()
        json_bytes = json.dumps(doc, separators=(',', ':')).encode('utf-8')
        while len(json_bytes) % 4:
            json_bytes += b' '
        while len(binary) % 4:
            binary += b'\x00'
        total = 12 + 8 + len(json_bytes) + 8 + len(binary)
        with open(path, 'wb') as f:
            f.write(struct.pack('<III', GLB_MAGIC, 2, total))
            f.write(struct.pack('<II', len(json_bytes), GLB_JSON_CHUNK))
            f.write(json_bytes)
            f.write(struct.pack('<II', len(binary), GLB_BIN_CHUNK))
            f.write(binary)

    # ── Internal build ────────────────────────────────────────────────────────

    def _build(self) -> tuple[dict, bytearray]:
        binary      = bytearray()
        views:      list[dict] = []
        accessors:  list[dict] = []
        meshes:     list[dict] = []
        nodes:      list[dict] = []
        materials:  list[dict] = []

        # One shared default material
        materials.append({
            "name": "default",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8,
            },
        })

        child_node_indices: list[int] = []

        for model, part_name in self._parts:
            primitives = []
            for mesh_def in model.meshes:
                prim = _build_primitive(mesh_def, model, binary, views, accessors, materials)
                if prim:
                    primitives.append(prim)

            if not primitives:
                continue

            mesh_idx = len(meshes)
            meshes.append({"name": part_name, "primitives": primitives})

            node_idx = len(nodes)
            nodes.append({"name": part_name, "mesh": mesh_idx})
            child_node_indices.append(node_idx)

        # Root empty node
        root_idx = len(nodes)
        nodes.append({"name": self.slug, "children": child_node_indices})

        buf = {"byteLength": len(binary)}

        doc = {
            "asset": {"version": "2.0", "generator": "RCRA Forge — Group Export"},
            "scene": 0,
            "scenes": [{"name": self.slug, "nodes": [root_idx]}],
            "nodes":      nodes,
            "meshes":     meshes,
            "materials":  materials,
            "accessors":  accessors,
            "bufferViews": views,
            "buffers":    [buf],
        }
        return doc, binary


# ── Helpers (module-level so they're reusable) ────────────────────────────────

def _build_primitive(
    mesh: MeshDefinition,
    model: ModelAsset,
    binary: bytearray,
    views: list,
    accessors: list,
    materials: list,
) -> Optional[dict]:
    positions, normals, uvs, indices = mesh_to_numpy(model, mesh)
    if positions is None or indices is None or len(positions) == 0:
        return None

    attribs: dict = {}
    attribs["POSITION"] = _add_accessor(
        positions, "VEC3", GLTF_FLOAT, GLTF_ARRAY_BUFFER, binary, views, accessors, minmax=True
    )
    if normals is not None:
        attribs["NORMAL"] = _add_accessor(
            normals, "VEC3", GLTF_FLOAT, GLTF_ARRAY_BUFFER, binary, views, accessors
        )
    if uvs is not None:
        attribs["TEXCOORD_0"] = _add_accessor(
            uvs, "VEC2", GLTF_FLOAT, GLTF_ARRAY_BUFFER, binary, views, accessors
        )

    if indices.max() < 65536:
        idx_acc = _add_accessor(
            indices.astype(np.uint16).reshape(-1, 1),
            "SCALAR", GLTF_UNSIGNED_SHORT, GLTF_ELEMENT_ARRAY,
            binary, views, accessors, is_scalar=True,
        )
    else:
        idx_acc = _add_accessor(
            indices.astype(np.uint32).reshape(-1, 1),
            "SCALAR", GLTF_UNSIGNED_INT, GLTF_ELEMENT_ARRAY,
            binary, views, accessors, is_scalar=True,
        )

    mat_idx = min(mesh.material_index, len(materials) - 1)
    return {"attributes": attribs, "indices": idx_acc, "material": mat_idx, "mode": 4}


def _add_accessor(
    arr: np.ndarray,
    acc_type: str,
    component_type: int,
    target: int,
    binary: bytearray,
    views: list,
    accessors: list,
    is_scalar: bool = False,
    minmax: bool = False,
) -> int:
    if component_type == GLTF_FLOAT:
        raw   = arr.astype(np.float32).tobytes()
        align = 4
    elif component_type == GLTF_UNSIGNED_SHORT:
        raw   = arr.astype(np.uint16).tobytes()
        align = 2
    elif component_type == GLTF_UNSIGNED_INT:
        raw   = arr.astype(np.uint32).tobytes()
        align = 4
    else:
        raw   = arr.astype(np.uint8).tobytes()
        align = 1

    while len(binary) % align:
        binary += b'\x00'
    byte_offset = len(binary)
    binary += raw

    view_idx = len(views)
    views.append({
        "buffer": 0,
        "byteOffset": byte_offset,
        "byteLength": len(raw),
        "target": target,
    })

    n   = arr.size if is_scalar else arr.shape[0]
    acc: dict = {
        "bufferView":    view_idx,
        "byteOffset":    0,
        "componentType": component_type,
        "count":         n,
        "type":          acc_type,
    }
    if minmax and acc_type == "VEC3" and component_type == GLTF_FLOAT:
        acc["min"] = arr.min(axis=0).tolist()
        acc["max"] = arr.max(axis=0).tolist()

    idx = len(accessors)
    accessors.append(acc)
    return idx
