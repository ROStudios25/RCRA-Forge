"""
exporters/group_exporter.py
Batch-export a group of related ModelAssets as a single GLB.

Each source asset becomes one or more named mesh nodes under a shared root,
with correct UV scaling, named materials, skeleton and skin weights — using
the same GltfExporter pipeline as single-asset export.

Blender outliner result:
    npc_grunthor                       ← scene root (empty)
      ├─ npc_grunthor_body-subset0-LOD_0
      ├─ npc_grunthor_body-subset1-LOD_0
      ├─ npc_grunthor_arm_l-subset0-LOD_0
      └─ ...
"""

from __future__ import annotations

import json
import os
import struct
from typing import Optional

import numpy as np

from core.mesh import ModelAsset, MeshDefinition, mesh_to_numpy
from exporters.gltf_exporter import (
    GltfExporter,
    GLTF_FLOAT, GLTF_UNSIGNED_BYTE, GLTF_UNSIGNED_SHORT, GLTF_UNSIGNED_INT,
    GLTF_ARRAY_BUFFER, GLTF_ELEMENT_ARRAY,
    GLB_MAGIC, GLB_JSON_CHUNK, GLB_BIN_CHUNK,
    MAX_INFLUENCES,
    _qxq, _rot_v, _quat_to_mat4_colmaj,
)


class GroupExporter:
    """
    Accumulate multiple ModelAssets and write them as a single GLB where
    each source asset is a set of named sub-mesh nodes under a shared root.
    """

    def __init__(self, slug: str = "group"):
        self.slug   = slug
        self._parts: list[tuple[ModelAsset, str]] = []

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

    def _build(self) -> tuple[dict, bytearray]:
        # Use GltfExporter per part, then merge all their outputs
        binary:     bytearray  = bytearray()
        views:      list       = []
        accessors:  list       = []
        meshes:     list       = []
        nodes:      list       = []
        skins:      list       = []
        materials:  list       = []

        child_node_indices: list[int] = []

        for model, part_name in self._parts:
            # Build this part using GltfExporter in isolation
            exp = GltfExporter(model, name=part_name, lod=0)
            part_doc = exp._build()
            part_bin = bytes(exp._bin)

            if not part_doc.get('meshes'):
                continue

            # Offset all buffer view byte offsets by current binary length
            bin_offset = len(binary)
            binary += part_bin

            # Remap accessor indices: offset by current counts
            acc_offset  = len(accessors)
            view_offset = len(views)
            mat_offset  = len(materials)
            node_offset = len(nodes)
            skin_offset = len(skins)

            # Remap buffer views
            for bv in part_doc.get('bufferViews', []):
                bv_copy = dict(bv)
                bv_copy['buffer'] = 0
                bv_copy['byteOffset'] = bv_copy.get('byteOffset', 0) + bin_offset
                views.append(bv_copy)

            # Remap accessors
            for acc in part_doc.get('accessors', []):
                acc_copy = dict(acc)
                acc_copy['bufferView'] = acc_copy['bufferView'] + view_offset
                accessors.append(acc_copy)

            # Remap materials
            for mat in part_doc.get('materials', []):
                materials.append(mat)

            # Remap skins
            part_skin_map = {}  # old skin idx → new skin idx
            for si, skin in enumerate(part_doc.get('skins', [])):
                skin_copy = dict(skin)
                # Remap inverseBindMatrices accessor
                if 'inverseBindMatrices' in skin_copy:
                    skin_copy['inverseBindMatrices'] += acc_offset
                # Joints will be remapped after nodes are added
                new_si = len(skins)
                part_skin_map[si] = new_si
                skins.append(skin_copy)

            # Remap nodes
            part_node_map = {}  # old node idx → new node idx
            for ni, node in enumerate(part_doc.get('nodes', [])):
                node_copy = dict(node)
                new_ni = len(nodes)
                part_node_map[ni] = new_ni
                nodes.append(node_copy)

            # Fix up node children and skin references now that we have the map
            for ni, node in enumerate(part_doc.get('nodes', [])):
                new_ni = part_node_map[ni]
                if 'children' in node:
                    nodes[new_ni]['children'] = [
                        part_node_map[c] for c in node['children']
                    ]
                if 'skin' in node:
                    nodes[new_ni]['skin'] = part_skin_map[node['skin']] if node['skin'] in part_skin_map else node['skin'] + skin_offset

            # Fix skin joint references
            for si, skin in enumerate(part_doc.get('skins', [])):
                new_si = part_skin_map[si]
                skins[new_si]['joints'] = [
                    part_node_map[j] for j in skin.get('joints', [])
                ]

            # Remap meshes (accessor indices in primitives)
            for mesh in part_doc.get('meshes', []):
                mesh_copy = dict(mesh)
                new_prims = []
                for prim in mesh_copy.get('primitives', []):
                    prim_copy = dict(prim)
                    prim_copy['attributes'] = {
                        k: v + acc_offset
                        for k, v in prim['attributes'].items()
                    }
                    prim_copy['indices'] = prim['indices'] + acc_offset
                    if 'material' in prim_copy:
                        prim_copy['material'] = prim_copy['material'] + mat_offset
                    new_prims.append(prim_copy)
                mesh_copy['primitives'] = new_prims
                meshes.append(mesh_copy)

            # Collect the mesh node indices for this part (scene nodes from part)
            scene_node_indices = part_doc.get('scenes', [{}])[0].get('nodes', [])
            for old_ni in scene_node_indices:
                child_node_indices.append(part_node_map[old_ni])

        # Root empty node grouping all parts
        root_idx = len(nodes)
        nodes.append({"name": self.slug, "children": child_node_indices})

        buf = {"byteLength": len(binary)}
        doc = {
            "asset":       {"version": "2.0", "generator": "RCRA Forge — Group Export"},
            "scene":       0,
            "scenes":      [{"name": self.slug, "nodes": [root_idx]}],
            "nodes":       nodes,
            "meshes":      meshes,
            "materials":   materials,
            "accessors":   accessors,
            "bufferViews": views,
            "buffers":     [buf],
        }
        if skins:
            doc["skins"] = skins
        return doc, binary
