r"""
core/material.py
Ratchet & Clank: Rift Apart PC — material asset parser.

Reverse-engineered from hex dump of hero_ratchet_head.material (852 bytes).

DAT1 layout (unk1 = 0x88730155 = 'material'):
  Two sections:

  Section 1  tag=0xE1275683  (Material Built File header)
    Offset 0x00: string "Material Built File"
    Offset ~0x20: string "required\materials\basic_normal_gloss_subsurface.materialgraph"
    This describes which material graph/shader this material uses.
    Size: ~40 bytes

  Section 2  tag=0xF526018? (Texture slot table + string table)
    Header (12 bytes):
      uint32  total_size        — byte size of this whole section
      uint32  entry_count       — number of texture slots (e.g. 7)
      uint32  string_table_off  — byte offset of string table from section data start

    Followed by: <entry_count> × float params (at offset header.string_table_off - 0x4C)
    Then:         <entry_count> × 8-byte entries:
                    uint32  string_offset   — byte offset of path in string table
                    uint32  asset_id_lo     — low 32 bits of texture asset CRC64 hash
    Then:         string table — null-separated texture paths

  Texture slot naming convention (corrected by ilaac, May 2026):
    _c   → base_color       (primary base color map)
    id_  → color_id         (color ID — alternative base color when _c absent)
    _g   → specular_color   (specular color — was mislabeled albedo)
    _g_a → specular_ior     (Specular IOR Level, greyscale)
    _m   → mask             (packed: R=emission_mask, G=height, B=AO)
    _n   → normal           (normal map)
    _ao  → ambient_occlusion (dedicated AO — replaces _m when present)
    _sm  → micro_variation   (NPC dinosaur detail variation — Grunthors/Monks only, ~30 textures)
    _v   → detail
    _d   → detail_normal
    _s   → specular

  The asset_id_lo (low 32 bits of CRC64) can be used to look up the full
  asset entry in the TOC via HashLookup, or the path string can be hashed
  directly to find the matching texture asset.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional

from core.archive import DAT1

# ── DAT1 section tags ─────────────────────────────────────────────────────────
TAG_MATERIAL_HEADER  = 0xE1275683   # float params / built data
TAG_TEXTURE_TABLE    = 0xF5260180   # texture slot table + string table (from RE)

# Texture slot role inferred from path suffix.
# Channel breakdowns confirmed from Blender RE by community (Fanis + N7Lombax57):
#
#   _g  → albedo/gloss     (base color + gloss packed)
#   _c  → color            (albedo variant, e.g. skin color layer)
#   _n  → normal map       (tangent-space, BC5/RG)
#   _m  → AO + Emission    R=AmbientOcclusion, G=Emission, B=unused/mix
#                          ↳ mostly blue for non-emissive (Ratchet head)
#                          ↳ red/pink highlights for emissive parts (wrench bolts)
#   _s  → specular
#   _v  → detail variant
#   _d  → detail normal
#   _ao → ambient occlusion (dedicated AO map, rare)
_SUFFIX_ROLES = {
    # Base color
    '_c':         'base_color',      # primary base color map
    '_col':       'base_color',
    '_basecolor': 'base_color',
    '_diffuse':   'base_color',
    '_alb':       'base_color',
    # Specular color (_g was previously mislabeled as albedo — corrected by ilaac)
    '_g':         'specular_color',  # specular color map
    '_g_a':       'specular_ior',    # Specular IOR Level (greyscale)
    # Packed mask (_m channels: R=emission mask, G=height, B=AO — confirmed ilaac)
    '_m':         'mask',            # packed: R=emission_mask G=height B=ao
    # Normal map
    '_n':         'normal',
    '_nrm':       'normal',
    '_nor':       'normal',
    # Dedicated AO (replaces _m when present)
    '_ao':        'ambient_occlusion',
    # Detail maps
    '_v':         'detail',
    '_d':         'detail_normal',
    '_s':         'specular',
    # Micro-variation mask (NPC dinosaurs only — Grunthors, Monks)
    # Used by npc_dinosaur_detail_variation materialgraph (~30 textures total)
    # Makes each individual NPC look slightly unique
    '_sm':        'micro_variation',
    '_e':         'unknown',
}

# Prefix-based roles (checked separately — these are prefixes not suffixes)
_PREFIX_ROLES = {
    'id_':  'color_id',   # color ID map — alternative base color when _c is absent
}


def _infer_role(path: str) -> str:
    """Infer texture slot role from path prefix or suffix before .texture."""
    stem = path.rsplit('.', 1)[0]   # strip .texture
    filename = stem.rsplit('/', 1)[-1]  # just the filename

    # Check prefixes first (e.g. id_something)
    for prefix, role in _PREFIX_ROLES.items():
        if filename.startswith(prefix):
            return role

    # Check suffixes
    for suffix, role in _SUFFIX_ROLES.items():
        if stem.endswith(suffix):
            return role

    return 'unknown'


@dataclass
class TextureSlot:
    """One texture binding in a material."""
    index:       int          # slot index (0-based)
    path:        str          # full asset path, e.g. 'characters/.../head_n.texture'
    asset_id_lo: int          # low 32 bits of CRC64 asset ID
    role:        str          # inferred role: 'albedo', 'normal', 'metallic', etc.

    @property
    def name(self) -> str:
        """Short filename without extension."""
        return self.path.rsplit('/', 1)[-1].rsplit('.', 1)[0]

    def __repr__(self):
        return f"<TextureSlot[{self.index}] {self.role} '{self.name}'>"


@dataclass
class MaterialAsset:
    """Parsed .material asset."""
    graph_path:    str                    # material graph path (shader type)
    slots:         list = field(default_factory=list)  # list[TextureSlot]

    @property
    def albedo_slot(self) -> Optional['TextureSlot']:
        """Primary base color slot — _c first, then color_id fallback."""
        # Priority 1: base_color (_c)
        for s in self.slots:
            if s.role == 'base_color':
                return s
        # Priority 2: color_id (id_ prefix) — used when _c is absent
        for s in self.slots:
            if s.role == 'color_id':
                return s
        # Priority 3: first non-utility slot
        _non_albedo = {'normal', 'mask', 'specular_color', 'specular_ior',
                       'specular', 'detail', 'detail_normal', 'ambient_occlusion', 'unknown'}
        for s in self.slots:
            if s.role not in _non_albedo:
                return s
        # Last resort: slot 0
        return self.slots[0] if self.slots else None

    @property
    def normal_slot(self) -> Optional['TextureSlot']:
        for s in self.slots:
            if s.role == 'normal':
                return s
        return None

    @property
    def mask_slot(self) -> Optional['TextureSlot']:
        """Packed mask map: R=emission_mask, G=height, B=AO (_m suffix)."""
        for s in self.slots:
            if s.role == 'mask':
                return s
        return None

    @property
    def ao_emission_slot(self) -> Optional['TextureSlot']:
        """Backwards-compat alias for mask_slot."""
        return self.mask_slot

    @property
    def specular_color_slot(self) -> Optional['TextureSlot']:
        """Specular color map (_g suffix)."""
        for s in self.slots:
            if s.role == 'specular_color':
                return s
        return None

    @property
    def specular_ior_slot(self) -> Optional['TextureSlot']:
        """Specular IOR Level map (_g_a suffix)."""
        for s in self.slots:
            if s.role == 'specular_ior':
                return s
        return None

    @property
    def color_id_slot(self) -> Optional['TextureSlot']:
        """Color ID map (id_ prefix) — alternative base color."""
        for s in self.slots:
            if s.role == 'color_id':
                return s
        return None

    @property
    def ao_slot(self) -> Optional['TextureSlot']:
        """Dedicated AO map (_ao suffix) — replaces _m when present."""
        for s in self.slots:
            if s.role == 'ambient_occlusion':
                return s
        return None

    def slot_by_role(self, role: str) -> Optional['TextureSlot']:
        """Look up a slot by role string."""
        for s in self.slots:
            if s.role == role:
                return s
        return None

    def __repr__(self):
        return f"<MaterialAsset graph='{self.graph_path}' slots={len(self.slots)}>"


# ── Parser ────────────────────────────────────────────────────────────────────

class MaterialParser:
    """
    Parse a raw .material DAT1 asset blob into a MaterialAsset.

    The format was reverse-engineered from hero_ratchet_head.material (852 bytes).

    Section layout within DAT1:
      Section 1 (tag 0xE1275683, ~40 bytes):
        Null-terminated strings:
          [0] "Material Built File"
          [1] graph path, e.g. 'required\\materials\\basic_normal_gloss_subsurface.materialgraph'

      Section 2 (large section, tag varies):
        uint32  total_size
        uint32  entry_count        (number of texture slots)
        uint32  string_table_off   (byte offset of string table within section data)
        ... (header padding / float params) ...
        entry_count × 8-byte entries:
          uint32  str_off           (byte offset of path string in string table)
          uint32  asset_id_lo       (low 32 bits of CRC64 asset hash)
        string table:
          null-separated path strings

    The entry array begins at (section_data_start + string_table_off - entry_count*8)
    based on the observed layout where string table immediately follows entries.
    """

    def __init__(self, data: bytes):
        self.data = data
        try:
            self.dat1 = DAT1(data)
        except Exception as ex:
            raise ValueError(f"Failed to parse DAT1: {ex}")

    def parse(self) -> MaterialAsset:
        graph_path = self._parse_graph_path()
        slots      = self._parse_texture_slots()
        return MaterialAsset(graph_path=graph_path, slots=slots)

    # ── Private ───────────────────────────────────────────────────────────────

    def _parse_graph_path(self) -> str:
        """
        Extract the material graph path from the DAT1 string pool.

        The string pool sits between the DAT1 header+directory and the first
        section data. Layout:
          uint32  total_pool_size
          uint32  string_data_size
          bytes   null-separated strings:
                    [0] "Material Built File"
                    [1] graph path (e.g. 'required\\materials\\...materialgraph')
        """
        try:
            # DAT1 header = 16 bytes, directory = section_count × 12 bytes
            n_sections = len(self.dat1.sections)
            pool_start = 16 + n_sections * 12
            pool_data  = self.data[pool_start:]

            if len(pool_data) < 8:
                return 'unknown'

            pool_size, str_size = struct.unpack_from('<II', pool_data, 0)
            if str_size == 0 or str_size > len(pool_data) - 8:
                return 'unknown'

            strings_raw = pool_data[8:8 + str_size]
            parts = [p.decode('utf-8', errors='replace')
                     for p in strings_raw.split(b'\x00') if p]

            # Find the materialgraph path
            for p in parts:
                if 'materialgraph' in p:
                    return p.replace('\\', '/')
            # Fall back to last non-empty part
            return parts[-1].replace('\\', '/') if parts else 'unknown'
        except Exception:
            return 'unknown'

    def _parse_texture_slots(self) -> list:
        """
        Parse the texture slot table from section TAG_TEXTURE_TABLE (0xF5260180).

        Confirmed layout (from RE of hero_ratchet_head.material):
          Section data header (32 bytes):
            +0x00  uint32  total_size        = 0x270 (624)
            +0x04  uint32  entry_count       = 7
            +0x08  uint32  unk_offset_a      = 0x60  (sub-table offset)
            +0x0C  uint32  padding           = 0
            +0x10  uint32  unk_b             = 0x7C
            +0x14  uint32  entry_count2      = 7  (same as entry_count)
            +0x18  uint32  unk_c             = 0x7C
            +0x1C  uint32  string_table_off  = 0xB4 (180) ← actual string table offset

          Entry array at (string_table_off - entry_count × 8):
            entry_count × 8-byte entries:
              uint32  string_byte_offset  (into string table)
              uint32  asset_id_lo         (low 32 bits of CRC64 hash)

          String table at string_table_off:
            null-separated texture paths
        """
        slots = []

        # Find the texture table section — largest non-header section
        sec = None
        for k, v in self.dat1.sections.items():
            candidate = bytes(v)
            if k != TAG_MATERIAL_HEADER and len(candidate) > 100:
                sec = candidate
                break
        if sec is None or len(sec) < 32:
            return slots

        try:
            # Read 32-byte header — string_table_off is at +0x1C
            total_size, entry_count = struct.unpack_from('<II', sec, 0)
            string_table_off = struct.unpack_from('<I', sec, 0x1C)[0]

            if entry_count == 0 or entry_count > 64:
                return slots
            if string_table_off >= len(sec):
                return slots

            # Entry array sits immediately before the string table
            entry_array_off = string_table_off - entry_count * 8
            if entry_array_off < 0:
                return slots

            str_table = sec[string_table_off:]

            for i in range(entry_count):
                off = entry_array_off + i * 8
                if off + 8 > len(sec):
                    break
                str_off, id_lo = struct.unpack_from('<II', sec, off)
                path = self._read_string(str_table, str_off)
                if not path:
                    continue
                slots.append(TextureSlot(
                    index       = i,
                    path        = path,
                    asset_id_lo = id_lo,
                    role        = _infer_role(path),
                ))

        except Exception as ex:
            print(f"[MaterialParser] slot parse error: {ex}")

        return slots

    def _find_entry_array(self, sec: bytes, count: int, str_table_off: int):
        """
        Fallback: scan backwards from str_table_off to find the entry array
        by verifying that str_off values point to valid null-terminated strings.
        """
        str_table = sec[str_table_off:]
        entry_size = count * 8
        # Try from (str_table_off - entry_size) backwards by 4 bytes
        for candidate in range(str_table_off - entry_size, max(0, str_table_off - entry_size - 64), -4):
            valid = True
            for i in range(count):
                off = candidate + i * 8
                if off + 8 > len(sec):
                    valid = False
                    break
                str_off, _ = struct.unpack_from('<II', sec, off)
                if str_off >= len(str_table):
                    valid = False
                    break
                # Check that str_table[str_off] is a valid printable ASCII path
                if str_off < len(str_table) and not (0x20 <= str_table[str_off] < 0x7F):
                    valid = False
                    break
            if valid:
                return candidate
        return None

    @staticmethod
    def _read_string(data: bytes, offset: int) -> str:
        """Read a null-terminated UTF-8 string from data at offset."""
        if offset >= len(data):
            return ''
        end = data.index(b'\x00', offset) if b'\x00' in data[offset:] else len(data)
        try:
            return data[offset:end].decode('utf-8', errors='replace')
        except Exception:
            return ''


def parse_material_asset(data: bytes) -> MaterialAsset:
    """Convenience function: parse raw bytes → MaterialAsset."""
    return MaterialParser(data).parse()
