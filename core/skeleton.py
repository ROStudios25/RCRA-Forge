"""
core/skeleton.py
Skeleton data extracted from a parsed ModelAsset.

The skeleton is NOT a separate asset type in RCRA — it lives inside
the model's DAT1 container in two sections:

  0x15DF9D3B  JointsSection      — bone definitions (16B each)
  0xDCC88A19  xDCC88A19_Section  — bone transforms (3×4 + 4×4 float matrices)

This module wraps the ModelAsset data into a convenient Skeleton object
for the UI skeleton viewer and the glTF exporter's skin building.

JointDef layout (16 bytes, from ALERT joints.py):
  int16   parent        (-1 = root)
  uint16  index
  uint16  unknown1      (child count in hierarchy)
  uint16  unknown2      (flags/type)
  uint32  hash          (crc32 of bone name, case-sensitive)
  uint32  string_offset (offset into DAT1 string table)
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Bone:
    index:    int
    parent:   int          # -1 = root
    name:     str
    position: tuple        # (x, y, z) local rest position
    rotation: tuple        # (x, y, z, w) quaternion

    @property
    def is_root(self) -> bool:
        return self.parent == -1


@dataclass
class Skeleton:
    bones: list[Bone] = field(default_factory=list)

    def root_bones(self) -> list[Bone]:
        return [b for b in self.bones if b.is_root]

    def children_of(self, bone: Bone) -> list[Bone]:
        return [b for b in self.bones if b.parent == bone.index]

    def world_positions(self) -> dict[int, np.ndarray]:
        """
        Compute approximate world-space head positions for display.
        Uses the stored local rest positions composed up the hierarchy.
        """
        local_pos = {b.index: np.array(b.position, dtype=np.float32) for b in self.bones}
        world: dict[int, np.ndarray] = {}

        def visit(bone: Bone, parent_world: np.ndarray):
            world[bone.index] = parent_world + local_pos.get(bone.index, np.zeros(3))
            for child in self.children_of(bone):
                visit(child, world[bone.index])

        for root in self.root_bones():
            visit(root, np.zeros(3, dtype=np.float32))

        return world

    @classmethod
    def from_model(cls, model) -> Optional['Skeleton']:
        """Build a Skeleton from a parsed ModelAsset (core.mesh.ModelAsset)."""
        if not model.joints:
            return None

        bones = []
        for i, jd in enumerate(model.joints):
            pos = model.joint_positions[i] if i < len(model.joint_positions) else (0, 0, 0)
            rot = model.joint_quaternions[i] if i < len(model.joint_quaternions) else (0, 0, 0, 1)
            bones.append(Bone(
                index    = jd.index,
                parent   = jd.parent,
                name     = jd.name,
                position = tuple(pos),
                rotation = tuple(rot),
            ))

        return cls(bones=bones)
