"""
core/level_assembler.py
Level assembly pipeline for RCRA Forge.

Given a parsed ZoneDef (list of SceneNodes with world transforms),
resolves each node's actor → model → mesh and assembles them into
a combined GLB export with correct world-space placement.

Pipeline per SceneNode:
  1. SceneNode.asset_id  → find actor entry in TOC
  2. Parse actor          → get model path string
  3. Resolve model path   → model asset_id via HashLookup
  4. Find + extract model → parse mesh geometry
  5. Apply world transform (rotation matrix + position)
  6. Combine into GLB scene

Limitations:
  - Actors without a .model reference (nav volumes, triggers) are skipped
  - Models not in hashes.txt cannot be resolved by path (skipped)
  - Very large zones may have hundreds of actors — use max_nodes to limit
"""

import struct
import math
from dataclasses import dataclass, field
from typing import Optional

from core.actor  import parse_actor_asset, ACTOR_TYPE
from core.zone   import ZoneDef, SceneNodeEntry
from core.mesh   import ModelParser


@dataclass
class AssembledNode:
    """One successfully resolved scene node ready for export."""
    entry:       SceneNodeEntry   # original zone entry
    model_path:  str              # resolved .model path
    model_asset_id: int           # TOC asset id of the model
    model:       object           # parsed ModelAsset
    world_matrix: tuple           # 4×4 column-major transform (for GLB)


@dataclass
class AssembledZone:
    """Result of assembling a zone — list of placed model instances."""
    zone:        ZoneDef
    nodes:       list             # list of AssembledNode
    skipped:     list             # list of (SceneNodeEntry, reason) for skipped nodes

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def skip_count(self) -> int:
        return len(self.skipped)


class LevelAssembler:
    """
    Resolves zone scene nodes to model assets and builds world transforms.

    Usage:
        assembler = LevelAssembler(toc_parser, lookup)
        result = assembler.assemble_zone(zone, max_nodes=50)
        # result.nodes → list of AssembledNode ready for GLB export
    """

    def __init__(self, toc_parser, lookup):
        self.toc    = toc_parser
        self.lookup = lookup
        self._model_cache = {}   # asset_id → ModelAsset (avoid re-parsing)
        self._actor_cache = {}   # asset_id → ActorAsset

    def assemble_zone(self, zone: ZoneDef,
                      max_nodes: Optional[int] = None,
                      progress_cb=None) -> AssembledZone:
        """Resolve all scene nodes in a zone to model assets."""
        nodes   = []
        skipped = []
        entries = zone.entries[:max_nodes] if max_nodes else zone.entries
        total   = len(entries)

        if zone.is_art_zone:
            return self._assemble_art_zone(zone, entries, progress_cb)
        else:
            return self._assemble_gp_zone(zone, entries, progress_cb)

    def _assemble_art_zone(self, zone, entries, progress_cb) -> AssembledZone:
        """Art zones: each entry has model_id directly."""
        nodes   = []
        skipped = []
        total   = len(entries)

        print(f"[assembler] art zone: {total} entries, {len(zone.model_ids or [])} model IDs in table")

        for i, entry in enumerate(entries):
            if progress_cb:
                progress_cb(i + 1, total)

            if not entry.model_id:
                skipped.append((entry, "no model_id in entry"))
                continue

            # Load model
            if entry.model_id in self._model_cache:
                model = self._model_cache[entry.model_id]
            else:
                model_entry = self.toc.find_entry(entry.model_id)
                if model_entry is None:
                    skipped.append((entry, f"model not in TOC ({entry.model_id:#018x})"))
                    if i < 5:
                        print(f"[assembler] model not in TOC: {entry.model_id:#018x}")
                    continue
                try:
                    model_data = self.toc.extract_asset(model_entry)
                    model = ModelParser(model_data).parse()
                    self._model_cache[entry.model_id] = model  # cache BEFORE print
                    model_name = self.lookup.name(entry.model_id) if self.lookup else ''
                    id_str = model_name if model_name else f'{entry.model_id:#018x}'
                    print(f"[assembler] loaded model: {id_str}")
                except Exception as ex:
                    import traceback
                    skipped.append((entry, f"model parse failed: {ex}"))
                    if i < 5:
                        print(f"[assembler] model parse failed ({entry.model_id:#018x}): {ex}")
                        traceback.print_exc()
                    continue

            nodes.append(AssembledNode(
                entry=entry,
                model_path=self.lookup.name(entry.model_id) if (self.lookup and entry.model_id) else '',
                model_asset_id=entry.model_id,
                model=model,
                world_matrix=_build_matrix(entry),
            ))

        print(f"[assembler] art zone complete: {len(nodes)} nodes, {len(skipped)} skipped")
        if skipped:
            reasons = {}
            for _, r in skipped:
                reasons[r] = reasons.get(r, 0) + 1
            for r, c in reasons.items():
                print(f"  {c}× {r}")
        return AssembledZone(zone=zone, nodes=nodes, skipped=skipped)

    def _assemble_gp_zone(self, zone, entries, progress_cb) -> AssembledZone:
        """GP zones: resolve actor paths from string pool → model."""
        nodes   = []
        skipped = []
        total   = len(entries)

        # Resolve actor paths to models
        actor_id_map   = {}
        actor_model_map = {}

        if zone.actor_paths and self.lookup and self.lookup.is_loaded():
            for path in zone.actor_paths:
                aid = self.lookup.asset_id(path)
                if aid is None:
                    aid = self.lookup.asset_id(path.lstrip('/'))
                if aid is None:
                    aid = self.lookup.asset_id(path.replace('/', '\\'))
                if aid is not None:
                    actor_id_map[path] = aid
                    print(f"[assembler] resolved actor: {path.split('/')[-1]} → {aid:#018x}")
                else:
                    print(f"[assembler] actor not in hashes.txt: {path}")

        for path, actor_aid in actor_id_map.items():
            if actor_aid in actor_model_map:
                continue
            actor_entry = self.toc.find_entry(actor_aid)
            if actor_entry is None:
                continue
            try:
                actor_data = self.toc.extract_asset(actor_entry)
                actor = parse_actor_asset(actor_data, self.lookup)
                if actor is None or not actor.has_model:
                    print(f"[assembler] actor has no model: {path}")
                    continue
                if not actor.model_asset_id:
                    continue
                model_entry = self.toc.find_entry(actor.model_asset_id)
                if model_entry is None:
                    continue
                model_data = self.toc.extract_asset(model_entry)
                model = ModelParser(model_data).parse()
                actor_model_map[actor_aid] = (actor.model_path, actor.model_asset_id, model)
                print(f"[assembler] loaded model: {actor.model_path.split('/')[-1]}")
            except Exception as ex:
                print(f"[assembler] failed: {path}: {ex}")

        resolved_actors = list(actor_model_map.values())

        for i, entry in enumerate(entries):
            if progress_cb:
                progress_cb(i + 1, total)
            if not resolved_actors:
                skipped.append((entry, "no actors resolved"))
                continue
            actor_idx = i % len(resolved_actors)
            model_path, model_aid, model = resolved_actors[actor_idx]
            nodes.append(AssembledNode(
                entry=entry, model_path=model_path,
                model_asset_id=model_aid, model=model,
                world_matrix=_build_matrix(entry),
            ))

        return AssembledZone(zone=zone, nodes=nodes, skipped=skipped)
        """
        Resolve all scene nodes in a zone to model assets.
        Uses actor paths from the zone string pool to find actor assets.
        """
        nodes   = []
        skipped = []
        entries = zone.entries[:max_nodes] if max_nodes else zone.entries
        total   = len(entries)

        # Pre-resolve all actor paths to asset IDs from hashes.txt
        # The zone string pool has the actor paths; we look them up once
        actor_id_map = {}   # path → asset_id
        if zone.actor_paths and self.lookup and self.lookup.is_loaded():
            for path in zone.actor_paths:
                aid = self.lookup.asset_id(path)
                if aid is None:
                    aid = self.lookup.asset_id(path.lstrip('/'))
                if aid is None:
                    # Try with backslashes
                    aid = self.lookup.asset_id(path.replace('/', '\\'))
                if aid is not None:
                    actor_id_map[path] = aid
                    print(f"[assembler] resolved actor: {path.split('/')[-1]} → {aid:#018x}")
                else:
                    print(f"[assembler] actor not in hashes.txt: {path}")

        # Pre-build actor → model map
        # For each unique actor, parse it to get the model path
        actor_model_map = {}   # actor_asset_id → (model_path, model_asset_id, model)
        for path, actor_aid in actor_id_map.items():
            if actor_aid in actor_model_map:
                continue
            actor_entry = self.toc.find_entry(actor_aid)
            if actor_entry is None:
                print(f"[assembler] actor not in TOC: {path}")
                continue
            try:
                actor_data  = self.toc.extract_asset(actor_entry)
                actor       = parse_actor_asset(actor_data, self.lookup)
                if actor is None or not actor.has_model:
                    print(f"[assembler] actor has no model: {path}")
                    continue
                model_aid = actor.model_asset_id
                if model_aid is None:
                    print(f"[assembler] model not in hashes.txt: {actor.model_path}")
                    continue
                model_entry = self.toc.find_entry(model_aid)
                if model_entry is None:
                    print(f"[assembler] model not in TOC: {actor.model_path}")
                    continue
                model_data = self.toc.extract_asset(model_entry)
                model = ModelParser(model_data).parse()
                actor_model_map[actor_aid] = (actor.model_path, model_aid, model)
                print(f"[assembler] loaded model: {actor.model_path.split('/')[-1]}")
            except Exception as ex:
                print(f"[assembler] failed to load actor {path}: {ex}")

        # Now assign models to entries
        # We match by actor path name stem to the entry name
        # Entries are tagged with checkpoint names, not actor names
        # So we assign all actors equally (one actor type per zone in simple tiles)
        # For zones with multiple actor types, we assign by round-robin or first-match
        
        # Build list of resolved (actor_id, model) pairs
        resolved_actors = list(actor_model_map.values())  # [(model_path, model_aid, model)]

        for i, entry in enumerate(entries):
            if progress_cb:
                progress_cb(i + 1, total)

            if not resolved_actors:
                skipped.append((entry, "no actors resolved for this zone"))
                continue

            # For tiles with one actor type, assign that model to all entries
            # For tiles with multiple, try to match by index modulo
            actor_idx = i % len(resolved_actors)
            model_path, model_aid, model = resolved_actors[actor_idx]

            world_matrix = _build_matrix(entry)
            nodes.append(AssembledNode(
                entry=entry,
                model_path=model_path,
                model_asset_id=model_aid,
                model=model,
                world_matrix=world_matrix,
            ))

        return AssembledZone(zone=zone, nodes=nodes, skipped=skipped)


def _build_matrix(entry: SceneNodeEntry) -> tuple:
    """
    Build a column-major 4×4 transform matrix from a SceneNodeEntry.
    Returns a 16-element tuple of plain Python floats for glTF node.matrix.

    GP zones:  entry.rot = full 9-float row-major 3×3 rotation matrix
    Art zones: entry.rot = only 3 valid rotation floats (row 0); position
               data bleeds into rot[3..8] so we reconstruct from row 0 only.
    """
    x, y, z = float(entry.x), float(entry.y), float(entry.z)

    # Check if rot is a valid 3×3 (all values in [-1, 1] range)
    # Art zone rot[3..8] contain position/scale data (large values) — detect this
    r = [float(v) for v in entry.rot]
    x, y, z = float(entry.x), float(entry.y), float(entry.z)

    if all(abs(v) <= 1.001 for v in r):
        # Full valid 3×3 rotation — GP zone (row-major → column-major 4×4)
        return (
            r[0], r[3], r[6], 0.0,
            r[1], r[4], r[7], 0.0,
            r[2], r[5], r[8], 0.0,
            x,    y,    z,    1.0,
        )
    else:
        # Art zone: only rot[0..2] = [cos θ, 0, sin θ] = row 0 of Y-axis rotation
        # Reconstruct full 3×3 Y-axis rotation matrix:
        #   row0 = [ cos θ,  0, sin θ]
        #   row1 = [   0,    1,   0  ]
        #   row2 = [-sin θ,  0, cos θ]
        import math
        # Normalise row 0 in case of floating point drift
        c = float(r[0])   # cos θ
        s = float(r[2])   # sin θ  (r[1] is always 0 for Y-axis rotation)
        length = math.sqrt(c*c + s*s)
        if length > 1e-6:
            c /= length
            s /= length
        else:
            c, s = 1.0, 0.0   # identity fallback

        # Full Y-axis rotation matrix (row-major):
        #   [c,  0,  s]
        #   [0,  1,  0]
        #   [-s, 0,  c]
        # Convert row-major → column-major for glTF:
        return (
            c,   0.0,  -s,  0.0,   # col 0
            0.0, 1.0,  0.0, 0.0,   # col 1
            s,   0.0,  c,   0.0,   # col 2
            x,   y,    z,   1.0,   # col 3
        )


# ── GLB export ────────────────────────────────────────────────────────────────

def export_zone_glb(assembled: AssembledZone, out_path: str,
                    lod: int = 0) -> int:
    """
    Export an assembled zone as a single GLB file.
    Each resolved node becomes a named mesh node with its world transform.

    Returns the number of nodes written.
    """
    import json, struct as st
    import numpy as np

    class _SafeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.floating, np.float32, np.float64)):
                return float(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            return super().default(obj)

    from exporters.gltf_exporter import GltfExporter
    from core.mesh import mesh_to_numpy

    # Build glTF structure
    gltf = {
        "asset": {"version": "2.0", "generator": "RCRA Forge", "extras": {"unitScale": 0.01}},
        "scene": 0,
        "scenes": [{"name": zone_short(assembled.zone.name), "nodes": []}],
        "nodes": [],
        "meshes": [],
        "accessors": [],
        "bufferViews": [],
        "buffers": [{"byteLength": 0}],
    }

    buffer_data = bytearray()
    scene_nodes = gltf["scenes"][0]["nodes"]

    # mesh_cache: model_asset_id -> list of mesh_idx (one per sub-mesh primitive)
    # Each unique model's geometry is written to the buffer only once.
    mesh_cache: dict = {}

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

                pos_bv, pos_acc = _write_accessor(
                    gltf, buffer_data, pos.tobytes(),
                    'VEC3', 5126, len(pos),
                    mins=[float(v) for v in pos.min(0).tolist()],
                    maxs=[float(v) for v in pos.max(0).tolist()]
                )

                idx_flat = idx.flatten().astype('uint32')
                idx_bv, idx_acc = _write_accessor(
                    gltf, buffer_data, idx_flat.tobytes(),
                    'SCALAR', 5125, len(idx_flat)
                )

                prim = {"attributes": {"POSITION": pos_acc}, "indices": idx_acc}

                if uvs is not None:
                    uv_bv, uv_acc = _write_accessor(
                        gltf, buffer_data, uvs.tobytes(),
                        'VEC2', 5126, len(uvs)
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

    # Finalise buffer
    gltf["buffers"][0]["byteLength"] = len(buffer_data)

    # Write GLB
    json_bytes = json.dumps(gltf, separators=(',', ':'), cls=_SafeEncoder).encode('utf-8')
    # Pad to 4-byte boundary
    while len(json_bytes) % 4:
        json_bytes += b' '

    glb_data = (
        st.pack('<III', 0x46546C67, 2, 12 + 8 + len(json_bytes) + 8 + len(buffer_data))
        + st.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
        + st.pack('<II', len(buffer_data), 0x004E4942) + bytes(buffer_data)
    )

    with open(out_path, 'wb') as f:
        f.write(glb_data)

    return len(assembled.nodes)


def _write_accessor(gltf, buf, data, acc_type, component_type, count,
                    mins=None, maxs=None):
    """Append data to buffer and add bufferView + accessor."""
    # Align to 4 bytes
    while len(buf) % 4:
        buf += b'\x00'
    bv_offset = len(buf)
    buf += data

    bv_idx = len(gltf["bufferViews"])
    gltf["bufferViews"].append({
        "buffer": 0, "byteOffset": bv_offset, "byteLength": len(data)
    })

    acc = {
        "bufferView": bv_idx,
        "byteOffset": 0,
        "componentType": component_type,
        "count": count,
        "type": acc_type,
    }
    if mins is not None:
        acc["min"] = mins
        acc["max"] = maxs

    acc_idx = len(gltf["accessors"])
    gltf["accessors"].append(acc)
    return bv_idx, acc_idx


def zone_short(name: str) -> str:
    return name.split('/')[-1].replace('.zone', '')
