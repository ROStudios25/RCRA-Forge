"""
core/zone.py
ZoneDef DAT1 parser for RCRA Forge.

Handles two zone entry formats:
  GP zones  (gameplay tiles): entry_size=0xB0 (176 bytes), section tag 0x06ABCAB2, no header
  ART zones (art/geometry):   entry_size variable (0x140=320 confirmed for megalopolis),
                               section tag 0x06ABCAB2, 32-byte section header.
                               Entry size derived from TAG_MODEL_INDICES count,
                               rounded to nearest multiple of 4.

Confirmed art zone entry field offsets (320-byte / 0x140 entries):
  +0x00 (12): [cosθ, 0, sinθ] — row 0 of Y-axis rotation matrix (Y-axis rotation only confirmed)
  +0x04 (4):  always 0.0 (row 0, element 1)
  +0x10 (12): world position X, Y, Z (3 × f32)
  +0x30 (12): bounding box half-extents or scale X, Y, Z (3 × f32) — NOT a second position
  +0x5C (4):  flags word (0x80000140 typical)
  +0xF0 (4):  model table index (u32) — direct index into TAG_MODEL_ASSETS u64[].
              Value 0xFFFFFFFF = sentinel (no model, non-renderable node).

TAG_MODEL_INDICES: u32[] of byte offsets into scene payload, one per model-bearing entry.
  Used to determine which entries have models (presence set) and to derive entry count.
  The actual model index is stored in the entry at +0xF0, NOT derived from list position.

TAG_MODEL_ASSETS: u64[] of model asset IDs. Indexed directly by +0xF0 value.
  Section size = n*8 + optional trailing padding bytes.

GP zones confirmed field offsets:
  +0x00 (36): rotation matrix (9 × f32)
  +0x30 (12): world position X, Y, Z (3 × f32)
  +0x5C (4):  flags word
  +0x80 (8):  instance_id (u64)

Name/string pool: DAT1-internal, pool_base = 0x10 + section_count*12
Actor/model paths stored as null-terminated strings in pool.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional


ZONE_DEF_TYPE      = 0x1F390AA0

TAG_SCENE_NODES    = 0x06abcab2   # Scene node placement array (both formats)
TAG_ENTRY_INDEX    = 0xdc625b3d   # Name/index table (gp zones: 4 bytes/entry; art: 72 bytes)
TAG_MODEL_INDICES  = 0x6987F172   # Byte offsets (u32[]) into scene payload — one per model-bearing entry.
                                   # Used to derive entry count and presence set.
                                   # Model index is stored in entry at +0xF0, NOT from list position.
TAG_MODEL_ASSETS   = 0xC6A5905E   # Model asset ID table u64[] (art zones only)

GP_ENTRY_SIZE      = 0xB0    # 176 bytes
ART_ENTRY_SIZE     = 0x140   # 320 bytes
ART_SECTION_HEADER = 32      # bytes before first entry in art zone scene section


@dataclass
class SceneNodeEntry:
    index:      int
    asset_id:   int       # instance_id (gp) or 0 (art)
    model_id:   int       # model asset_id (art) or 0 (gp — resolved via actor)
    name:       str
    x:          float
    y:          float
    z:          float
    rot:        tuple
    flags:      int
    raw:        bytes = field(repr=False, default=b'')

    @property
    def position(self):
        return (self.x, self.y, self.z)

    def __repr__(self):
        return (f"SceneNodeEntry(index={self.index}, "
                f"name={self.name!r}, "
                f"pos=({self.x:.2f},{self.y:.2f},{self.z:.2f}))")


@dataclass
class ZoneDef:
    asset_id:    int
    name:        str
    entries:     list
    actor_paths: list  = None
    model_paths: list  = None   # .model paths (art zones — direct model refs)
    model_ids:   list  = None   # model asset_id[] from TAG_MODEL_ASSETS (art zones)
    is_art_zone: bool  = False

    @property
    def entry_count(self):
        return len(self.entries)

    def __repr__(self):
        kind = 'art' if self.is_art_zone else 'gp'
        return f"ZoneDef(name={self.name!r}, entries={self.entry_count}, kind={kind})"


def _read_string(data: bytes, offset: int) -> str:
    if not data or offset < 0 or offset >= len(data):
        return ''
    end = data.find(b'\x00', offset)
    end = end if end != -1 else len(data)
    try:
        return data[offset:end].decode('utf-8', errors='replace')
    except Exception:
        return ''


def _read_all_strings(pool: bytes) -> list:
    strings = []
    i = 0
    while i < len(pool):
        end = pool.find(b'\x00', i)
        if end == -1:
            end = len(pool)
        try:
            text = pool[i:end].decode('utf-8', errors='replace').strip()
            if text:
                strings.append(text)
        except Exception:
            pass
        i = end + 1
    return strings


class ZoneParser:
    def __init__(self, data: bytes, lookup=None):
        self._data   = data
        self._lookup = lookup

    def parse(self, asset_id: int = 0, name: str = '') -> ZoneDef:
        data = self._data

        dat1_off = data.find(b'\x31\x54\x41\x44')
        if dat1_off == -1:
            raise ValueError(f"No DAT1 in zone {name!r}")

        section_count, unknown_count = struct.unpack_from('<HH', data, dat1_off + 12)
        pool_rel  = 0x10 + section_count * 12 + unknown_count * 8
        pool_abs  = dat1_off + pool_rel

        sections  = {}
        first_off = None
        for i in range(section_count):
            base = dat1_off + 0x10 + i * 12
            tag, sec_off, sec_size = struct.unpack_from('<III', data, base)
            abs_off = dat1_off + sec_off
            sections[tag] = data[abs_off:abs_off + sec_size]
            if first_off is None or sec_off < first_off:
                first_off = sec_off

        pool_end    = dat1_off + first_off if first_off else pool_abs
        string_pool = data[pool_abs:pool_end] if pool_abs < pool_end else b''

        # Determine zone type from available sections
        is_art = TAG_MODEL_INDICES in sections or TAG_MODEL_ASSETS in sections

        # Extract actor paths (gp zones) and model paths (art zones)
        all_strings   = _read_all_strings(string_pool) if string_pool else []
        actor_paths   = [s.replace('\\', '/').lower() for s in all_strings
                         if s.lower().endswith('.actor')]
        model_paths   = [s.replace('\\', '/').lower() for s in all_strings
                         if s.lower().endswith('.model')]

        # Parse model asset IDs from art zone table
        model_ids = []
        if TAG_MODEL_ASSETS in sections:
            ma_data = sections[TAG_MODEL_ASSETS]
            n_ids   = len(ma_data) // 8
            model_ids = [struct.unpack_from('<Q', ma_data, i*8)[0] for i in range(n_ids)]

        # Parse scene nodes
        entries = []
        if TAG_SCENE_NODES in sections:
            scene_data   = sections[TAG_SCENE_NODES]
            index_data   = sections.get(TAG_ENTRY_INDEX, b'')
            mi_data      = sections.get(TAG_MODEL_INDICES, b'')
            entries = self._parse_nodes(scene_data, data, dat1_off,
                                        index_data, mi_data, model_ids, is_art)

        return ZoneDef(
            asset_id=asset_id, name=name, entries=entries,
            actor_paths=actor_paths, model_paths=model_paths,
            model_ids=model_ids, is_art_zone=is_art,
        )

    def _parse_nodes(self, scene_data, full_data, dat1_off,
                     index_data, mi_data, model_ids, is_art):
        header  = ART_SECTION_HEADER if is_art else 0
        payload = scene_data[header:]

        if is_art:
            # Derive true entry size from model_indices count.
            # TAG_MODEL_INDICES has exactly one u32 per scene node, so:
            #   entry_size = payload_size / model_indices_count
            # Round to nearest multiple of 4 to handle integer division imprecision
            # (e.g. 2212736 / 6889 = 321.2 → rounds to 320 = 0x140).
            if mi_data:
                mi_count   = len(mi_data) // 4
                raw_size   = len(payload) / mi_count if mi_count > 0 else ART_ENTRY_SIZE
                entry_size = int(round(raw_size / 4)) * 4
            else:
                entry_size = ART_ENTRY_SIZE
            print(f"[zone] art zone entry_size={entry_size:#x} "
                  f"payload={len(payload)} model_indices={len(mi_data)//4 if mi_data else 0}")
        else:
            entry_size = GP_ENTRY_SIZE

        n = len(payload) // entry_size

        # Name offsets from index section
        name_offsets = []
        if is_art:
            # Art zones: index section may be 72 bytes of other data
            # Use string pool positions based on sequential model paths
            name_offsets = [0] * n
        else:
            # GP zones: 4 bytes per entry = pool offset
            for i in range(n):
                if i * 4 + 4 <= len(index_data):
                    name_offsets.append(struct.unpack_from('<I', index_data, i*4)[0])
                else:
                    name_offsets.append(0)

        # TAG_MODEL_INDICES: byte offsets marking model-bearing entries (presence set).
        # Build as a set for O(1) lookup — used only to gate the +0xF0 read.
        model_bearing_offsets = set()
        if is_art and mi_data:
            for k in range(len(mi_data) // 4):
                model_bearing_offsets.add(struct.unpack_from('<I', mi_data, k * 4)[0])

        entries = []
        for i in range(n):
            base = i * entry_size
            raw  = payload[base:base + entry_size]
            if len(raw) < entry_size:
                break

            if is_art:
                # Confirmed field offsets (320-byte art zone entries):
                #   +0x00 (12): [cosθ, 0, sinθ] — row 0 of Y-axis rotation matrix
                #   +0x10 (12): world position X, Y, Z (3 × f32)
                #   +0x5C (4):  flags word
                #   +0xF0 (4):  model table index (u32) → indexes model_ids[].
                #               0xFFFFFFFF = sentinel (no model).
                rot      = struct.unpack_from('<9f', raw, 0x00)
                x, y, z  = struct.unpack_from('<3f', raw, 0x10)
                flags    = struct.unpack_from('<I',  raw, 0x5C)[0] if entry_size > 0x5C else 0
                asset_id = 0
                model_id = 0
                if base in model_bearing_offsets and entry_size >= 0xF4:
                    mi = struct.unpack_from('<I', raw, 0xF0)[0]
                    if mi < len(model_ids):
                        model_id = model_ids[mi]
            else:
                # GP zone confirmed field offsets:
                #   +0x00 (36): rotation matrix (9 × f32)
                #   +0x2C (4):  padding
                #   +0x30 (12): world position X, Y, Z (3 × f32)
                #   +0x5C (4):  flags
                #   +0x80 (8):  instance_id (u64)
                rot      = struct.unpack_from('<9f', raw, 0x00)
                x, y, z  = struct.unpack_from('<3f', raw, 0x30)
                flags    = struct.unpack_from('<I',  raw, 0x5C)[0]
                asset_id = struct.unpack_from('<Q',  raw, 0x80)[0] if len(raw) >= 0x88 else 0
                model_id = 0

            # Name lookup
            if is_art:
                if model_id and self._lookup:
                    full = self._lookup.name(model_id)
                    name = full.split('/')[-1].replace('.model', '') if full else f'node_{i}'
                else:
                    name = f'node_{i}'
            else:
                name_off = name_offsets[i] if i < len(name_offsets) else 0
                name = _read_string(full_data, dat1_off + name_off)

            entries.append(SceneNodeEntry(
                index=i, asset_id=asset_id, model_id=model_id,
                name=name, x=x, y=y, z=z, rot=rot, flags=flags, raw=raw,
            ))

        return entries


def parse_zone_asset(data: bytes, asset_id: int = 0,
                     name: str = '', lookup=None) -> Optional[ZoneDef]:
    dat1_off = data.find(b'\x31\x54\x41\x44')
    if dat1_off == -1:
        return None
    unk1 = struct.unpack_from('<I', data, dat1_off + 4)[0]
    if unk1 != ZONE_DEF_TYPE:
        return None
    try:
        return ZoneParser(data, lookup).parse(asset_id=asset_id, name=name)
    except Exception as ex:
        import traceback
        print(f"[zone] parse failed for {name!r}: {ex}")
        traceback.print_exc()
        return None
