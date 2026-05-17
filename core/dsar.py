"""
core/dsar.py
DSAR (Rift Apart Streaming Archive) extractor for RCRA Forge.

DSAR packs thousands of Oodle-compressed chunks into a single file
with a fixed-size header index. Used for per-level streaming archives
such as d/model_env_megalopolis (447 MB compressed).

File layout
-----------
+0x00  "DSAR"       magic (4)
+0x04  0x00010003   version (4)
+0x08  blocks_count number of chunks (4)
+0x0C  header_end   byte offset where compressed data begins (4)
+0x10  full_size    total decompressed size (8)
+0x18  "PADDING*"   padding sentinel string (8)
+0x20  zeros        (16)
+0x30  block table  blocks_count x 32 bytes

Block entry (32 bytes) -- Rift Apart PC layout (confirmed from live file hex)
-----------------------------------------------------------------------------
+0x00  u32  real_size    decompressed size of this chunk
+0x04  u32  comp_size    compressed size (unpadded)
+0x08  u64  sentinel     0x5555555555555503
+0x10  u64  real_end     cumulative decompressed end offset
+0x18  u64  comp_end     cumulative compressed end offset (absolute from
                         file start, 16-byte aligned)

comp_offset for chunk i = comp_end[i] - align16(comp_size[i])

Compression: Oodle Kraken (type inferred from data; requires
oo2core_9_win64.dll via core/oodle.py). ALERT's dsar_codec.py uses lz4
for PS5 builds -- the PC build uses Oodle instead.
"""

import struct
import os
from typing import Callable, Iterator, Optional

DSAR_MAGIC    = b'DSAR'
DSAR_VERSION  = 0x00010003
ENTRY_SIZE    = 32


class DsarBlock:
    """One compressed chunk in a DSAR archive."""
    __slots__ = ('real_size', 'comp_size', 'real_end', 'comp_end')

    def __init__(self, real_size: int, comp_size: int,
                 real_end: int, comp_end: int):
        self.real_size = real_size   # decompressed size of this chunk
        self.comp_size = comp_size   # compressed size (unpadded)
        self.real_end  = real_end    # cumulative decompressed end offset
        self.comp_end  = comp_end    # cumulative compressed end offset (abs, aligned)

    @property
    def comp_offset(self) -> int:
        """Absolute file offset of compressed data (16-byte aligned start)."""
        aligned = (self.comp_size + 15) & ~15
        return self.comp_end - aligned

    @property
    def real_start(self) -> int:
        """Decompressed start offset of this chunk."""
        return self.real_end - self.real_size


class DsarExtractor:
    """
    Reads a DSAR streaming archive and decompresses chunks using Oodle
    via core/oodle.py (requires oo2core_9_win64.dll).

    Parameters
    ----------
    path : str
        Path to the DSAR file (e.g. ``d/model_env_megalopolis``).
    """

    def __init__(self, path: str):
        self.path = path
        self._blocks:       list[DsarBlock] = []
        self._header_end:   int = 0
        self._total_decomp: int = 0
        self._parse_header()

    def _parse_header(self):
        with open(self.path, 'rb') as f:
            hdr = f.read(32)

        magic   = hdr[:4]
        version = struct.unpack_from('<I', hdr, 4)[0]
        if magic != DSAR_MAGIC:
            raise ValueError(f"Not a DSAR file -- magic={magic!r}")
        if version != DSAR_VERSION:
            raise ValueError(f"Unknown DSAR version: {version:#010x}")

        blocks_count     = struct.unpack_from('<I', hdr,  8)[0]
        self._header_end = struct.unpack_from('<I', hdr, 12)[0]

        with open(self.path, 'rb') as f:
            f.seek(0x30)
            table = f.read(blocks_count * ENTRY_SIZE)

        blocks = []
        for i in range(blocks_count):
            o         = i * ENTRY_SIZE
            real_size = struct.unpack_from('<I', table, o)[0]
            comp_size = struct.unpack_from('<I', table, o + 4)[0]
            # +0x08: u64 sentinel 0x5555555555555503 (skipped)
            real_end  = struct.unpack_from('<Q', table, o + 16)[0]
            comp_end  = struct.unpack_from('<Q', table, o + 24)[0]
            blocks.append(DsarBlock(real_size, comp_size, real_end, comp_end))

        self._blocks       = blocks
        self._total_decomp = blocks[-1].real_end if blocks else 0

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    @property
    def total_decompressed_size(self) -> int:
        return self._total_decomp

    @property
    def header_end(self) -> int:
        return self._header_end

    def read_block_raw(self, index: int) -> bytes:
        """Read the raw compressed bytes for a single block."""
        b = self._blocks[index]
        with open(self.path, 'rb') as f:
            f.seek(b.comp_offset)
            return f.read(b.comp_size)

    def decompress_block(self, index: int) -> bytearray:
        """Decompress a single block using Oodle (requires oo2core_9_win64.dll)."""
        from core import oodle
        b   = self._blocks[index]
        raw = self.read_block_raw(index)
        return bytearray(oodle.decompress(raw, b.real_size))

    def iter_blocks(self) -> Iterator[tuple[int, bytearray]]:
        """Yield (index, decompressed_block) for every block in order."""
        for i in range(len(self._blocks)):
            yield i, self.decompress_block(i)

    def decompress_all(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        out_path: Optional[str] = None,
    ) -> bytearray:
        """
        Decompress the entire DSAR archive.

        Parameters
        ----------
        progress_cb : callable, optional
            Called as progress_cb(block_index, total_blocks) after each block.
        out_path : str, optional
            Write decompressed stream to disk instead of returning in memory.
            Recommended for large archives (400MB+).

        Returns
        -------
        bytearray -- empty if out_path was given.
        """
        n = len(self._blocks)
        if out_path:
            with open(out_path, 'wb') as out:
                for i, chunk in self.iter_blocks():
                    out.write(chunk)
                    if progress_cb:
                        progress_cb(i + 1, n)
            return bytearray()
        else:
            result = bytearray(self._total_decomp)
            for i, chunk in self.iter_blocks():
                b = self._blocks[i]
                result[b.real_start:b.real_end] = chunk
                if progress_cb:
                    progress_cb(i + 1, n)
            return result

    def scan_dat1(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> list[tuple[int, int, bytes]]:
        """
        Scan the decompressed stream block-by-block for embedded DAT1 assets.

        Processes one block at a time -- the full archive is never in RAM.
        A 3-byte rolling tail catches magic bytes spanning block boundaries.

        Returns
        -------
        list of (decomp_offset, dat1_size, header_bytes) tuples.
        """
        DAT1_MAGIC = b'DAT1'
        results    = []
        n          = len(self._blocks)
        tail       = bytearray()

        for i, chunk in self.iter_blocks():
            b        = self._blocks[i]
            base_off = b.real_start
            search   = tail + bytes(chunk)
            tail_len = len(tail)

            pos = 0
            while True:
                idx = search.find(DAT1_MAGIC, pos)
                if idx == -1:
                    break
                abs_off = base_off - tail_len + idx
                hdr     = search[idx:idx + 64]
                if len(hdr) >= 8:
                    dat1_size = struct.unpack_from('<I', hdr, 4)[0]
                    results.append((abs_off, dat1_size, bytes(hdr)))
                pos = idx + 4

            tail = bytearray(chunk[-3:])
            if progress_cb:
                progress_cb(i + 1, n)

        return results

    def info(self) -> str:
        lines = [
            f"DSAR Archive: {os.path.basename(self.path)}",
            f"  Blocks:       {self.block_count:,}",
            f"  Header end:   {self._header_end:#x} ({self._header_end:,} bytes)",
            f"  Total decomp: {self._total_decomp:,} bytes "
            f"({self._total_decomp / 1024 / 1024:.1f} MB)",
        ]
        if self._blocks:
            csizes = [b.comp_size for b in self._blocks if b.comp_size > 0]
            if csizes:
                lines.append(
                    f"  Comp sizes:   min={min(csizes):,}  "
                    f"max={max(csizes):,}  avg={sum(csizes)//len(csizes):,}"
                )
        return '\n'.join(lines)


def extract_dsar(
    path: str,
    out_path: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
):
    """Extract a DSAR archive to a flat decompressed file."""
    extractor = DsarExtractor(path)
    print(extractor.info())
    extractor.decompress_all(progress_cb=progress_cb, out_path=out_path)
    print(f"Written: {out_path}")
