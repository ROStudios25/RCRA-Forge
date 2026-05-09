"""
exporters/ascii_exporter.py
ALERT-compatible ASCII model export for RCRA Forge.

Output format matches ALERT's model_to_ascii.py exactly so that
ascii_to_model.py can round-trip the file back into a .model asset.

Format spec (line by line):
    <bone_count>
    for each bone:
        <name>
        <parent_index>          (-1 = root)
        <x y z qx qy qz qw>    world-space position + quaternion

    <mesh_count>
    for each mesh:
        <mesh_name>             e.g. "sm00_materials/ratchet/body"
        <uv_layers>             always 1
        <texture_count>         always 0
        <vertex_count>
        for each vertex:
            <x y z>
            <nx ny nz>
            <r g b a>           always "0 0 0 0"
            <u v>
            [<bone_indices>]    space-separated, only if model has bones
            [<bone_weights>]    space-separated, only if model has bones
        <face_count>
        for each face:
            <i2 i1 i0>          reversed winding (ALERT convention)
"""

import math
from typing import Optional

from core.mesh import ModelAsset, MeshDefinition


# ── Number formatting (matches ALERT's pretty_format) ────────────────────────

def _fmt(n: float) -> str:
    """Format a float the same way ALERT does: trim trailing zeros, no sci-notation."""
    s = f"{n:.6f}"
    dot = s.index(".")
    j = len(s)
    while j > dot:
        if s[j - 1] == "0":
            j -= 1
        else:
            break
    if j == dot + 1:
        result = s[:dot]
    else:
        result = s[:j]
    return "0" if result == "-0" else result


# ── World-space joint transform accumulation ──────────────────────────────────

def _qxq(q, p):
    aq, bq, cq, dq = q
    ap, bp, cp, dp = p
    return (
        dq * ap + aq * dp + bq * cp - cq * bp,
        dq * bp - aq * cp + bq * dp + cq * ap,
        dq * cp + aq * bp - bq * ap + cq * dp,
        dq * dp - aq * ap - bq * bp - cq * cp,
    )

def _qxv(q, v):
    ax, ay, az, aw = q
    bx, by, bz = v
    return (
        aw * bx + ay * bz - az * by,
        aw * by - ax * bz + az * bx,
        aw * bz + ax * by - ay * bx,
        -ax * bx - ay * by - az * bz,
    )

def _inv_q(q):
    x, y, z, w = q
    return (-x, -y, -z, w)

def _rot_v(v, q):
    t = _qxv(q, v)
    t = _qxq(t, _inv_q(q))
    return t[:3]

def _v_plus(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

def _compute_world_transforms(model: ModelAsset) -> list:
    """
    Return a list of (x, y, z, qx, qy, qz, qw) in world space for each joint,
    matching the accumulation logic in ALERT's write_bones().
    """
    joints    = model.joints
    positions = model.joint_positions
    quats     = model.joint_quaternions
    N = len(joints)
    cache = {}

    def get_world(i):
        if i in cache:
            return cache[i]
        x, y, z = positions[i]
        qx, qy, qz, qw = quats[i]
        j = joints[i]
        if j.parent != -1:
            px, py, pz, pqx, pqy, pqz, pqw = get_world(j.parent)
            parent_q = (pqx, pqy, pqz, pqw)
            qx, qy, qz, qw = _qxq(parent_q, (qx, qy, qz, qw))
            x, y, z = _v_plus((px, py, pz), _rot_v((x, y, z), parent_q))
        result = (x, y, z, qx, qy, qz, qw)
        cache[i] = result
        return result

    return [get_world(i) for i in range(N)]


# ── Main exporter ─────────────────────────────────────────────────────────────

class AsciiExporter:
    """
    Export a ModelAsset to ALERT's ASCII format.

    Usage:
        exporter = AsciiExporter(model, lod=0)
        exporter.export("output.ascii")
    """

    MAX_INFLUENCES = 4

    def __init__(self, model: ModelAsset, lod: int = 0):
        self.model = model
        self.lod   = lod

    def export(self, path: str):
        """Write the ASCII file to disk."""
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            self._write(f)

    def export_string(self) -> str:
        """Return the ASCII content as a string (for previewing/testing)."""
        import io
        buf = io.StringIO()
        self._write(buf)
        return buf.getvalue()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write(self, f):
        model = self.model
        has_bones = bool(model.joints and model.joint_positions and model.joint_quaternions)

        # Collect meshes for this LOD (look 0 only, matching viewport behaviour)
        meshes = [
            (i, m) for i, m in enumerate(model.meshes)
            if m.lod_level == self.lod and m.look_index == 0
        ]

        # ── Bones ─────────────────────────────────────────────────────────────
        if has_bones:
            world_xforms = _compute_world_transforms(model)
            f.write(f"{len(model.joints)}\n")
            for i, joint in enumerate(model.joints):
                x, y, z, qx, qy, qz, qw = world_xforms[i]
                f.write(f"{joint.name}\n")
                f.write(f"{joint.parent}\n")
                f.write(
                    f"{_fmt(x)} {_fmt(y)} {_fmt(z)} "
                    f"{_fmt(qx)} {_fmt(qy)} {_fmt(qz)} {_fmt(qw)}\n"
                )
        else:
            f.write("0\n")

        # ── Meshes ────────────────────────────────────────────────────────────
        f.write(f"{len(meshes)}\n")
        for mesh_idx, mesh in meshes:
            self._write_mesh(f, mesh_idx, mesh, has_bones)

    def _mesh_name(self, mesh_idx: int, mesh: MeshDefinition) -> str:
        """Build the mesh name string: sm##_<material_path>"""
        mat_name = ""
        if self.model.material_names and mesh.material_index < len(self.model.material_names):
            mat_name = self.model.material_names[mesh.material_index]
        return f"sm{mesh_idx:02}_{mat_name}"

    def _write_mesh(self, f, mesh_idx: int, mesh: MeshDefinition, has_bones: bool):
        model  = self.model
        fmt    = _fmt

        f.write(f"{self._mesh_name(mesh_idx, mesh)}\n")
        f.write("1\n")   # uv_layers
        f.write("0\n")   # texture_count

        # ── Vertices ──────────────────────────────────────────────────────────

        # Determine which weight array to use — mirrors ALERT's logic:
        # flag 0x100 → use rcra_weights with first_weight_index offset
        # otherwise  → use rcra_weights starting at vertex_start
        use_rcra = bool(mesh.flags & 0x100)
        weight_offset = mesh.first_weight_index if use_rcra else mesh.vertex_start

        # Max influences across this mesh
        max_inf = self.MAX_INFLUENCES
        if has_bones and model.rcra_weights:
            for vi in range(mesh.vertex_start, mesh.vertex_start + mesh.vertex_count):
                wi = vi - mesh.vertex_start + weight_offset
                if wi < len(model.rcra_weights):
                    max_inf = max(max_inf, len(model.rcra_weights[wi]))

        f.write(f"{mesh.vertex_count}\n")
        for vi in range(mesh.vertex_start, mesh.vertex_start + mesh.vertex_count):
            v = model.vertexes[vi]

            f.write(f"{fmt(v.x)} {fmt(v.y)} {fmt(v.z)}\n")
            f.write(f"{fmt(v.nx)} {fmt(v.ny)} {fmt(v.nz)}\n")
            f.write("0 0 0 0\n")   # vertex colour — always zero in ALERT output
            f.write(f"{fmt(v.u)} {fmt(v.v)}\n")

            if has_bones:
                wi = vi - mesh.vertex_start + weight_offset
                groups_str, weights_str = self._format_weights(wi, max_inf)
                f.write(f"{groups_str}\n")
                f.write(f"{weights_str}\n")

        # ── Faces ─────────────────────────────────────────────────────────────
        # ALERT reverses winding: writes i2, i1, i0
        # Indices may be absolute (subtract vertex_start) or relative (flag 0x10)
        indexes  = model.indexes
        vc       = 0 if mesh.indices_are_relative else mesh.vertex_start
        n_faces  = mesh.index_count // 3

        f.write(f"{n_faces}\n")
        for j in range(n_faces):
            base = mesh.index_start + j * 3
            i0 = indexes[base    ] - vc
            i1 = indexes[base + 1] - vc
            i2 = indexes[base + 2] - vc
            f.write(f"{i2} {i1} {i0}\n")

    def _format_weights(self, weight_index: int, max_inf: int) -> tuple[str, str]:
        """Return (bone_indices_str, weights_str) for a vertex."""
        model = self.model
        vertex_weights = []
        if model.rcra_weights and weight_index < len(model.rcra_weights):
            vertex_weights = model.rcra_weights[weight_index]

        groups_parts  = []
        weights_parts = []
        for slot in range(max_inf):
            if slot < len(vertex_weights):
                bone_idx, w = vertex_weights[slot]
                if w == 0:
                    bone_idx = 0
            else:
                bone_idx, w = 0, 0.0
            groups_parts.append(str(bone_idx))
            weights_parts.append(_fmt(w))

        return " ".join(groups_parts), " ".join(weights_parts)


# ── Convenience function ──────────────────────────────────────────────────────

def export_ascii(model: ModelAsset, path: str, lod: int = 0):
    """Export a ModelAsset to an ALERT-compatible .ascii file."""
    AsciiExporter(model, lod=lod).export(path)
