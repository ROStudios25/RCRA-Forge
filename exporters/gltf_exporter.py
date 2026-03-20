"""
exporters/gltf_exporter.py
Export ModelAsset → glTF 2.0 (.glb) or Wavefront .obj

Updated to use the rewritten ModelAsset/MeshDefinition from core/mesh.py
which accurately reflects the ALERT/RCRA format.
"""

import json
import os
import struct
import numpy as np
from typing import Optional

from core.mesh import ModelAsset, MeshDefinition, mesh_to_numpy

# glTF component type constants
GLTF_FLOAT          = 5126
GLTF_UNSIGNED_SHORT = 5123
GLTF_UNSIGNED_INT   = 5125
GLTF_ARRAY_BUFFER   = 34962
GLTF_ELEMENT_ARRAY  = 34963

GLB_MAGIC      = 0x46546C67
GLB_JSON_CHUNK = 0x4E4F534A
GLB_BIN_CHUNK  = 0x004E4942


class GltfExporter:
    def __init__(self, model: ModelAsset, name: str = "model"):
        self.model = model
        self.name  = name
        self._bin  = bytearray()
        self._views: list[dict] = []
        self._accessors: list[dict] = []
        self._meshes: list[dict] = []
        self._nodes: list[dict] = []
        self._materials: list[dict] = []

    def export_glb(self, path: str):
        doc = self._build()
        json_bytes = json.dumps(doc, separators=(',',':')).encode('utf-8')
        while len(json_bytes) % 4: json_bytes += b' '
        while len(self._bin) % 4:  self._bin  += b'\x00'
        total = 12 + 8 + len(json_bytes) + 8 + len(self._bin)
        with open(path, 'wb') as f:
            f.write(struct.pack('<III', GLB_MAGIC, 2, total))
            f.write(struct.pack('<II', len(json_bytes), GLB_JSON_CHUNK))
            f.write(json_bytes)
            f.write(struct.pack('<II', len(self._bin), GLB_BIN_CHUNK))
            f.write(self._bin)

    def export_gltf(self, path: str):
        bin_path = os.path.splitext(path)[0] + '.bin'
        doc = self._build(bin_uri=os.path.basename(bin_path))
        with open(path, 'w') as f: json.dump(doc, f, indent=2)
        with open(bin_path, 'wb') as f: f.write(self._bin)

    def _build(self, bin_uri: Optional[str] = None) -> dict:
        self._bin.clear()
        self._views.clear(); self._accessors.clear()
        self._meshes.clear(); self._nodes.clear(); self._materials.clear()

        self._materials.append({
            "name": "default",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                "metallicFactor": 0.0, "roughnessFactor": 0.8,
            }
        })

        primitives = []
        for mesh in self.model.meshes:
            prim = self._build_primitive(mesh)
            if prim: primitives.append(prim)

        self._meshes.append({"name": self.name, "primitives": primitives})
        self._nodes.append({"name": self.name, "mesh": 0})

        buf: dict = {"byteLength": len(self._bin)}
        if bin_uri: buf["uri"] = bin_uri

        return {
            "asset": {"version": "2.0", "generator": "RCRA Forge"},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(self._nodes)))}],
            "nodes": self._nodes,
            "meshes": self._meshes,
            "materials": self._materials,
            "accessors": self._accessors,
            "bufferViews": self._views,
            "buffers": [buf],
        }

    def _build_primitive(self, mesh: MeshDefinition) -> Optional[dict]:
        positions, normals, uvs, indices = mesh_to_numpy(self.model, mesh)
        if positions is None or indices is None or len(positions) == 0:
            return None

        attribs: dict = {}
        attribs["POSITION"] = self._add_accessor(positions, "VEC3", GLTF_FLOAT, GLTF_ARRAY_BUFFER, minmax=True)
        if normals is not None:
            attribs["NORMAL"] = self._add_accessor(normals, "VEC3", GLTF_FLOAT, GLTF_ARRAY_BUFFER)
        if uvs is not None:
            attribs["TEXCOORD_0"] = self._add_accessor(uvs, "VEC2", GLTF_FLOAT, GLTF_ARRAY_BUFFER)

        if indices.max() < 65536:
            idx16 = indices.astype(np.uint16)
            idx_acc = self._add_accessor(idx16.reshape(-1,1), "SCALAR", GLTF_UNSIGNED_SHORT, GLTF_ELEMENT_ARRAY, is_scalar=True)
        else:
            idx_acc = self._add_accessor(indices.astype(np.uint32).reshape(-1,1), "SCALAR", GLTF_UNSIGNED_INT, GLTF_ELEMENT_ARRAY, is_scalar=True)

        mat_idx = min(mesh.material_index, len(self._materials) - 1)
        return {"attributes": attribs, "indices": idx_acc, "material": mat_idx, "mode": 4}

    def _add_accessor(self, arr: np.ndarray, acc_type: str, component_type: int,
                      target: int, is_scalar: bool = False, minmax: bool = False) -> int:
        if component_type == GLTF_FLOAT:
            raw = arr.astype(np.float32).tobytes()
            align = 4
        elif component_type == GLTF_UNSIGNED_SHORT:
            raw = arr.astype(np.uint16).tobytes()
            align = 2
        elif component_type == GLTF_UNSIGNED_INT:
            raw = arr.astype(np.uint32).tobytes()
            align = 4
        else:
            raw = arr.astype(np.uint8).tobytes()
            align = 1

        while len(self._bin) % align: self._bin += b'\x00'
        byte_offset = len(self._bin)
        self._bin += raw

        view_idx = len(self._views)
        self._views.append({"buffer": 0, "byteOffset": byte_offset, "byteLength": len(raw), "target": target})

        n = arr.size if is_scalar else arr.shape[0]
        acc: dict = {"bufferView": view_idx, "byteOffset": 0, "componentType": component_type, "count": n, "type": acc_type}
        if minmax and acc_type == "VEC3" and component_type == GLTF_FLOAT:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()

        idx = len(self._accessors)
        self._accessors.append(acc)
        return idx


class ObjExporter:
    def __init__(self, model: ModelAsset, name: str = "model"):
        self.model = model
        self.name  = name

    def export(self, path: str):
        lines = [f"# RCRA Forge OBJ export\no {self.name}\n"]
        vo = no = uo = 1   # global offsets

        for mi, mesh in enumerate(self.model.meshes):
            positions, normals, uvs, indices = mesh_to_numpy(self.model, mesh)
            if positions is None or indices is None: continue

            lines.append(f"g mesh_{mi:03d}")
            lines.append(f"usemtl mat_{mesh.material_index}")
            for v in positions: lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
            if uvs is not None:
                for uv in uvs: lines.append(f"vt {uv[0]:.6f} {1-uv[1]:.6f}")
            if normals is not None:
                for n in normals: lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")

            has_uv = uvs is not None
            has_n  = normals is not None
            for tri in range(0, len(indices) - 2, 3):
                a, b, c = int(indices[tri]), int(indices[tri+1]), int(indices[tri+2])
                def fmt(i):
                    vi = i + vo
                    ti = (f"/{i+uo}" if has_uv else "/")
                    ni = (f"/{i+no}" if has_n else "")
                    return f"{vi}{ti}{ni}"
                lines.append(f"f {fmt(a)} {fmt(b)} {fmt(c)}")

            vo += len(positions)
            if has_uv: uo += len(uvs)
            if has_n:  no += len(normals)

        lines.append("")
        with open(path, 'w') as f: f.write('\n'.join(lines))

        mtl = os.path.splitext(path)[0] + '.mtl'
        mats = {m.material_index for m in self.model.meshes}
        with open(mtl, 'w') as f:
            for mi in sorted(mats):
                f.write(f"newmtl mat_{mi}\nKd 0.8 0.8 0.8\nKa 0 0 0\nKs 0.2 0.2 0.2\nNs 32\n\n")
