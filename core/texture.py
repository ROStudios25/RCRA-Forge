"""
core/texture.py
Ratchet & Clank: Rift Apart PC — texture asset parser.

Written from ALERT dat1lib/types/sections/texture/header.py.

Key facts:
  - Asset is a DAT1 container with unk1 = 0x5C4580B9 ('texture')
  - Single section: TAG 0x4EDE3593 — TextureHeaderSection
  - Section size is 44 bytes for RCRA/MSMR
  - The pixel data itself is NOT in the DAT1 section —
    it is prepended to the asset via the 36-byte AssetEntry.header blob
    (from the TOC's AssetHeadersSection, tag 0x654BDED9)
    OR stored in a separate HD texture archive file

TextureHeaderSection layout (44 bytes for RCRA):
  0x00  4B  sd_len       — byte length of SD (standard def) pixel data
  0x04  4B  hd_len       — byte length of HD pixel data
  0x08  2B  hd_width
  0x0A  2B  hd_height
  0x0C  2B  sd_width
  0x0E  2B  sd_height
  0x10  2B  array_size
  0x12  1B  stex_format  — Insomniac texture format enum
  0x13  1B  planes
  0x14  2B  fmt          — DXGI format enum
  0x16  8B  unk          — uint64
  0x1E  1B  sd_mipmaps
  0x1F  1B  unk2
  0x20  1B  hd_mipmaps
  0x21  1B  unk3
  0x22  2B  unk4         — remaining bytes

fmt field maps to DXGI_FORMAT:
  71 (0x47)  BC1_UNORM
  74 (0x4A)  BC2_UNORM
  77 (0x4D)  BC3_UNORM
  80 (0x50)  BC4_UNORM
  83 (0x53)  BC5_UNORM
  98 (0x62)  BC7_UNORM
  28 (0x1C)  R8G8B8A8_UNORM
  61 (0x3D)  R8_UNORM

Named according to SpiderTex by monax3:
https://github.com/monax3/SpiderTex/blob/main/src/texture_file.rs
"""

import struct
from dataclasses import dataclass
from typing import Optional

from core.archive import DAT1

# ── DAT1 section tag ──────────────────────────────────────────────────────────
TAG_TEXTURE_HEADER = 0x4EDE3593

# ── DXGI format → human-readable name ────────────────────────────────────────
DXGI_FORMAT_NAMES = {
    0x47: 'BC1_UNORM',
    0x4A: 'BC2_UNORM',
    0x4D: 'BC3_UNORM',
    0x50: 'BC4_UNORM',
    0x53: 'BC5_UNORM',
    0x62: 'BC7_UNORM',
    0x1C: 'R8G8B8A8_UNORM',
    0x3D: 'R8_UNORM',
    0x36: 'B8G8R8A8_UNORM',
    0x41: 'BC1_UNORM_SRGB',
    0x4F: 'BC3_UNORM_SRGB',
    0x5B: 'BC5_SNORM',
    0x63: 'BC7_UNORM_SRGB',
}

# DXGI formats that use DXT FourCC in DDS headers
DXGI_DXT1 = 0x47
DXGI_DXT3 = 0x4A
DXGI_DXT5 = 0x4D
DXGI_ATI1 = 0x50
DXGI_ATI2 = 0x53
DXGI_BC7  = 0x62


@dataclass
class TextureAsset:
    # SD = standard definition (always available)
    sd_len:    int
    sd_width:  int
    sd_height: int
    sd_mips:   int
    # HD = high definition (may require separate HD archive)
    hd_len:    int
    hd_width:  int
    hd_height: int
    hd_mips:   int
    # Format
    fmt:       int         # DXGI_FORMAT value
    array_size: int
    planes:    int
    # Raw pixel data (SD resolution, prepended via asset header blob)
    pixel_data: bytes = b''

    @property
    def width(self) -> int:
        return self.sd_width

    @property
    def height(self) -> int:
        return self.sd_height

    @property
    def mips(self) -> int:
        return self.sd_mips

    @property
    def format_name(self) -> str:
        return DXGI_FORMAT_NAMES.get(self.fmt, f'DXGI_{self.fmt:#04x}')

    @property
    def is_block_compressed(self) -> bool:
        return self.fmt in (DXGI_DXT1, DXGI_DXT3, DXGI_DXT5,
                            DXGI_ATI1, DXGI_ATI2, DXGI_BC7)

    def to_dds_bytes(self) -> bytes:
        return _build_dds(self)

    def to_png_bytes(self) -> Optional[bytes]:
        """Decode pixel data to PNG via Pillow if available."""
        if not self.pixel_data:
            return None
        try:
            from PIL import Image
            import io
            dds = self.to_dds_bytes()
            img = Image.open(io.BytesIO(dds))
            out = io.BytesIO()
            img.save(out, format='PNG')
            return out.getvalue()
        except Exception:
            return None


# ── Parser ────────────────────────────────────────────────────────────────────

class TextureParser:
    """
    Parse a raw texture asset blob into a TextureAsset.

    The asset blob is a DAT1 container.  For RCRA the pixel data is stored
    separately — it arrives prepended in the 36-byte header blob from the TOC
    AssetHeadersSection, or via a separate SD/HD archive read.

    Usage:
        raw = toc.extract_asset(entry)   # already includes header bytes
        tex = TextureParser(raw).parse()
    """

    def __init__(self, data: bytes):
        self.data = data
        self.dat1 = DAT1(data)

    def parse(self) -> TextureAsset:
        sec = self.dat1.get_section(TAG_TEXTURE_HEADER)
        if not sec or len(sec) < 34:
            raise ValueError(f"No valid texture header section found (got {len(sec) if sec else 0} bytes)")

        sd_len, hd_len           = struct.unpack_from('<II', sec, 0)
        hd_w, hd_h               = struct.unpack_from('<HH', sec, 8)
        sd_w, sd_h               = struct.unpack_from('<HH', sec, 12)
        array_size, stex_fmt, pl = struct.unpack_from('<HBB', sec, 16)
        fmt, unk                 = struct.unpack_from('<HQ', sec, 20)
        sd_mips, unk2, hd_mips, unk3 = struct.unpack_from('<BBBB', sec, 30)

        # Pixel data: for RCRA the TOC prepends a 36-byte header blob to the
        # asset bytes; the actual pixel data follows the DAT1 section data.
        # We look for pixel bytes after the end of the DAT1 payload.
        pixel_data = b''
        # The sd_len bytes of pixel data are appended after the DAT1 container
        # (extract_asset() prepends the header blob, so the layout is:
        #   [36-byte header blob] + [DAT1 container]
        # The header blob itself IS the pixel data for the SD texture.
        # We detect this: if data length > DAT1 total_size + 8, leading bytes = pixel data
        dat1_start = 0
        if len(self.data) >= 4:
            # Check if first 4 bytes are DAT1 magic
            magic = struct.unpack_from('<I', self.data, 0)[0]
            if magic != 0x44415431:
                # Data starts with prepended header blob (36 bytes of pixel data header)
                # The actual SD pixel data comes from a separate read; the 36-byte blob
                # is the texture header summary. The pixel bytes follow the DAT1 in the archive.
                dat1_start = 36
                pixel_data = bytes(self.data[:36])  # placeholder — real data from archive

        # If sd_len is set and there's trailing data after the DAT1, that's the pixels
        if dat1_start == 0 and len(self.data) > 0:
            # Find where DAT1 ends
            if len(self.data) >= 12:
                m2 = struct.unpack_from('<I', self.data, 0)[0]
                if m2 == 0x44415431:
                    total = struct.unpack_from('<I', self.data, 8)[0]
                    if total < len(self.data):
                        pixel_data = self.data[total:]

        return TextureAsset(
            sd_len     = sd_len,
            sd_width   = sd_w,
            sd_height  = sd_h,
            sd_mips    = max(1, sd_mips),
            hd_len     = hd_len,
            hd_width   = hd_w,
            hd_height  = hd_h,
            hd_mips    = max(1, hd_mips),
            fmt        = fmt,
            array_size = array_size,
            planes     = pl,
            pixel_data = pixel_data,
        )


# ── DDS container builder ─────────────────────────────────────────────────────

DDS_MAGIC       = b'DDS '
DDS_HDR_SIZE    = 124
DDSD_CAPS       = 0x00000001
DDSD_HEIGHT     = 0x00000002
DDSD_WIDTH      = 0x00000004
DDSD_LINEARSIZE = 0x00080000
DDSD_PIXFMT     = 0x00001000
DDSD_MIPMAP     = 0x00020000
DDSCAPS_TEXTURE = 0x00001000
DDSCAPS_MIPMAP  = 0x00400000
DDSCAPS_COMPLEX = 0x00000008
DDPF_FOURCC     = 0x00000004

# DDS FourCC → DXGI format
_FOURCC_MAP = {
    DXGI_DXT1: b'DXT1',
    DXGI_DXT3: b'DXT3',
    DXGI_DXT5: b'DXT5',
    DXGI_ATI1: b'ATI1',
    DXGI_ATI2: b'ATI2',
    DXGI_BC7:  b'DX10',
}

# DXGI format resource dimension constant
D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3


def _build_dds(tex: TextureAsset) -> bytes:
    import io
    buf = io.BytesIO()

    mip_count = max(1, tex.sd_mips)
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXFMT | DDSD_LINEARSIZE
    if mip_count > 1:
        flags |= DDSD_MIPMAP

    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        caps |= DDSCAPS_MIPMAP | DDSCAPS_COMPLEX

    fourcc = _FOURCC_MAP.get(tex.fmt, b'DX10')
    block_bytes = 8 if tex.fmt == DXGI_DXT1 else 16
    pitch = max(1, (tex.sd_width + 3) // 4) * block_bytes

    # DDS header
    buf.write(DDS_MAGIC)
    buf.write(struct.pack('<I', DDS_HDR_SIZE))
    buf.write(struct.pack('<I', flags))
    buf.write(struct.pack('<I', max(1, tex.sd_height)))
    buf.write(struct.pack('<I', max(1, tex.sd_width)))
    buf.write(struct.pack('<I', pitch))
    buf.write(struct.pack('<I', 1))              # depth
    buf.write(struct.pack('<I', mip_count))
    buf.write(b'\x00' * 44)                      # reserved[11]

    # DDS_PIXELFORMAT (32 bytes)
    buf.write(struct.pack('<I', 32))             # size
    buf.write(struct.pack('<I', DDPF_FOURCC))
    buf.write(fourcc)
    buf.write(b'\x00' * 20)

    buf.write(struct.pack('<I', caps))
    buf.write(b'\x00' * 16)                      # caps2-4 + reserved

    # DX10 extension header (needed for BC7 and others without legacy FourCC)
    if fourcc == b'DX10':
        buf.write(struct.pack('<I', tex.fmt))
        buf.write(struct.pack('<I', D3D10_RESOURCE_DIMENSION_TEXTURE2D))
        buf.write(struct.pack('<I', 0))           # miscFlag
        buf.write(struct.pack('<I', max(1, tex.array_size)))
        buf.write(struct.pack('<I', 0))           # miscFlags2

    if tex.pixel_data:
        buf.write(tex.pixel_data)

    return buf.getvalue()
