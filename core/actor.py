"""
core/actor.py
Actor DAT1 parser for RCRA Forge.

Actor assets (.actor) are thin wrappers that reference a model asset by path.
The model path is stored as a plain null-terminated string in the DAT1 string
pool, always as the second string after "Actor Built File".

Confirmed from binary analysis of:
  - modifynavvolume.actor       (no model — nav volume only)
  - meg_prop_chair_01_bk_chunk_01_kick.actor  (has model path in pool)

Actor DAT1 layout:
  unk1 = 0x944BD3AD  (shared by all actor types)
  String pool: "Actor Built File\\0" + optional model path + other strings
  Model path: first string ending in ".model" in the pool
"""

import struct
from dataclasses import dataclass
from typing import Optional


ACTOR_TYPE = 0x944BD3AD


@dataclass
class ActorAsset:
    """Parsed actor — primarily just the model path reference."""
    model_path:  Optional[str]   # e.g. "environment\\...\\chair.model"
    model_asset_id: Optional[int]  # resolved via HashLookup (None if not in hashes.txt)
    all_strings: list            # all strings found in pool (for debugging)

    @property
    def has_model(self) -> bool:
        return self.model_path is not None

    def __repr__(self):
        return f"ActorAsset(model={self.model_path!r})"


def parse_actor_asset(data: bytes,
                      lookup=None) -> Optional[ActorAsset]:
    """
    Parse an actor DAT1 asset.

    Parameters
    ----------
    data   : raw extracted bytes
    lookup : HashLookup instance (optional) — used to resolve model path → asset_id

    Returns ActorAsset or None if not an actor.
    """
    # Find DAT1 magic
    dat1_off = data.find(b'\x31\x54\x41\x44')
    if dat1_off == -1:
        return None

    unk1 = struct.unpack_from('<I', data, dat1_off + 4)[0]
    if unk1 != ACTOR_TYPE:
        return None

    # Parse section count (u16 + u16 unknown_count)
    section_count, unknown_count = struct.unpack_from('<HH', data, dat1_off + 12)

    # String pool base
    pool_rel = 0x10 + section_count * 12 + unknown_count * 8
    pool_abs = dat1_off + pool_rel

    # Find first section offset to bound the pool
    first_off = None
    for i in range(section_count):
        base = dat1_off + 0x10 + i * 12
        _, sec_off, _ = struct.unpack_from('<III', data, base)
        if first_off is None or sec_off < first_off:
            first_off = sec_off

    pool_end = dat1_off + first_off if first_off else len(data)
    pool     = data[pool_abs:pool_end]

    # Extract all null-terminated strings from pool
    all_strings = _read_all_strings(pool)

    # Find model path — first string ending with '.model'
    model_path = None
    for s in all_strings:
        if s.lower().endswith('.model') and s:
            model_path = s.replace('\\', '/').lower()
            break

    # Resolve model path to asset ID via HashLookup
    model_asset_id = None
    if model_path and lookup and lookup.is_loaded():
        # Try with and without leading slash
        model_asset_id = lookup.asset_id(model_path)
        if model_asset_id is None:
            model_asset_id = lookup.asset_id(model_path.lstrip('/'))
        if model_asset_id is None:
            # Try with .model extension variants
            model_asset_id = lookup.asset_id(model_path.replace('/', '\\'))

    return ActorAsset(
        model_path=model_path,
        model_asset_id=model_asset_id,
        all_strings=all_strings,
    )


def _read_all_strings(pool: bytes) -> list:
    """Extract all null-terminated strings from a byte pool."""
    strings = []
    i = 0
    while i < len(pool):
        end = pool.find(b'\x00', i)
        if end == -1:
            end = len(pool)
        s = pool[i:end]
        try:
            text = s.decode('utf-8', errors='replace').strip()
            if text:
                strings.append(text)
        except Exception:
            pass
        i = end + 1
    return strings
