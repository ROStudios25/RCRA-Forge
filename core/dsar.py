"""
core/dsar.py
DSAR (Rift Apart Streaming Archive) extractor for RCRA Forge.

DSAR is the container format used for zone streaming archives — each zone
file packs thousands of assets (models, textures, materials, DAT1s) into
a single GDeflate-compressed stream with a header index.

Format layout:
    +0x00  "DSAR"           magic (4)
    +0x04  0x00010003       version (4)
    +0x08  entry_count      number of compressed chunks (4)
    +0x0C  header_size      byte offset to start of data region (4)
    +0x10  file_size        total file size (8)
    +0x18  "PADDING*"       padding sentinel (8)
    +0x20  zeros            (16)
    +0x30  entry table      entry_count × 32 bytes

Each entry (32 bytes):
    +0x00  u32  comp_size          compressed size of this chunk
    +0x04  u32  chunk_decomp_size  decompressed size of this chunk
    +0x08  u64  0x5555555555555503 sentinel (padding marker)
    +0x10  u64  data_end_offset    cumulative end offset in data region
    +0x18  u64  decomp_cumulative  cumulative decompressed offset after this chunk

The data region starts at header_size. Each chunk starts at
(data_end_offset - comp_size) from the beginning of the data region.

The decompressed stream is a flat byte array. Individual DAT1 assets are
embedded within it — they must be located by scanning for DAT1 magic or
by using a separate asset index (not yet implemented).

Usage:
    extractor = DsarExtractor("path/to/zone")
    stream = extractor.decompress_all(progress_cb=lambda i, n: None)
    # stream is a bytearray of the fully decompressed zone data

    # Or decompress one chunk at a time:
    for i, chunk_bytes in extractor.iter_chunks():
        ...
"""

import struct
import os
from typing import Callable, Iterator, Optional

DSAR_MAGIC   = b'DSAR'
DSAR_VERSION = 0x00010003
ENTRY_SIZE   = 32
SENTINEL     = 0x5555555555555503


class DsarEntry:
    """One compressed chunk in a DSAR archive."""
    __slots__ = ('comp_size', 'chunk_decomp', 'data_start', 'data_end',
                 'decomp_cumulative')

    def __init__(self, comp_size: int, chunk_decomp: int,
                 data_start: int, data_end: int, decomp_cumulative: int):
        self.comp_size        = comp_size
        self.chunk_decomp     = chunk_decomp       # decompressed size of this chunk
        self.data_start       = data_start         # byte offset in data region
        self.data_end         = data_end           # byte offset in data region (end)
        self.decomp_cumulative = decomp_cumulative  # cumulative decomp offset after chunk


class DsarExtractor:
    """
    Reads a DSAR zone archive and decompresses it using GDeflate
    (requires libdeflate.dll on Windows via core/gdeflate.py).

    Parameters
    ----------
    path : str
        Path to the zone file.
    """

    def __init__(self, path: str):
        self.path = path
        self._entries: list[DsarEntry] = []
        self._header_size: int = 0
        self._total_decomp: int = 0
        self._parse_header()

    def _parse_header(self):
        with open(self.path, 'rb') as f:
            hdr = f.read(32)

        magic   = hdr[:4]
        version = struct.unpack_from('<I', hdr, 4)[0]
        if magic != DSAR_MAGIC:
            raise ValueError(f"Not a DSAR file — magic={magic!r}")
        if version != DSAR_VERSION:
            raise ValueError(f"Unknown DSAR version: {version:#010x}")

        entry_count  = struct.unpack_from('<I', hdr, 8)[0]
        header_size  = struct.unpack_from('<I', hdr, 12)[0]
        self._header_size = header_size

        # Read full entry table
        with open(self.path, 'rb') as f:
            f.seek(0x30)
            table = f.read(entry_count * ENTRY_SIZE)

        entries = []
        for i in range(entry_count):
            off = i * ENTRY_SIZE
            v   = struct.unpack_from('<8I', table, off)

            comp_size         = v[0]
            chunk_decomp      = v[1]            # per-chunk decompressed size
            # v[2], v[3]      = sentinel 0x5555555555555503
            data_end          = v[4] | (v[5] << 32)  # cumulative comp end offset
            decomp_cumulative = v[6]            # cumulative decomp offset (u32)
            # v[7]            = 0 (padding)
            data_start        = data_end - comp_size

            entries.append(DsarEntry(
                comp_size, chunk_decomp,
                data_start, data_end, decomp_cumulative
            ))

        self._entries      = entries
        self._total_decomp = entries[-1].decomp_cumulative if entries else 0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def total_decompressed_size(self) -> int:
        return self._total_decomp

    @property
    def header_size(self) -> int:
        return self._header_size

    # ── Chunk reading ─────────────────────────────────────────────────────────

    def read_chunk_raw(self, index: int) -> bytes:
        """Read the raw compressed bytes for a single chunk."""
        e = self._entries[index]
        with open(self.path, 'rb') as f:
            f.seek(self._header_size + e.data_start)
            return f.read(e.comp_size)

    def decompress_chunk(self, index: int) -> bytearray:
        """Decompress a single chunk using LZ4 block decompression."""
        try:
            import lz4.block
        except ImportError:
            raise ImportError(
                "lz4 is required for DSAR decompression. "
                "Install it with: pip install lz4"
            )
        e   = self._entries[index]
        raw = self.read_chunk_raw(index)
        return bytearray(lz4.block.decompress(raw, uncompressed_size=e.chunk_decomp))

    def iter_chunks(self) -> Iterator[tuple[int, bytearray]]:
        """Yield (index, decompressed_chunk) for every chunk in order."""
        for i in range(len(self._entries)):
            yield i, self.decompress_chunk(i)

    # ── Full decompression ────────────────────────────────────────────────────

    def decompress_all(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        out_path: Optional[str] = None,
    ) -> bytearray:
        """
        Decompress the entire DSAR archive into a single bytearray.

        Parameters
        ----------
        progress_cb : callable, optional
            Called as progress_cb(chunk_index, total_chunks) after each chunk.
        out_path : str, optional
            If provided, write the decompressed stream to this file instead
            of returning it in memory (useful for 920MB+ archives).

        Returns
        -------
        bytearray
            Full decompressed stream. Empty if out_path was provided.
        """
        n = len(self._entries)

        if out_path:
            with open(out_path, 'wb') as out:
                for i, chunk in self.iter_chunks():
                    out.write(chunk)
                    if progress_cb:
                        progress_cb(i + 1, n)
            return bytearray()
        else:
            result = bytearray(self._total_decomp)
            for i, chunk in self.iter_chunks():
                e   = self._entries[i]
                off = e.decomp_cumulative - e.chunk_decomp
                end = off + len(chunk)
                result[off:end] = chunk
                if progress_cb:
                    progress_cb(i + 1, n)
            return result

    # ── DAT1 scanning ─────────────────────────────────────────────────────────

    def scan_dat1(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> list[tuple[int, int, bytes]]:
        """
        Scan the decompressed stream for DAT1 assets without holding the
        entire stream in memory. Processes chunk by chunk.

        Returns list of (decomp_offset, dat1_size, first_64_bytes) tuples
        for every DAT1 header found.

        Note: DAT1 assets that span chunk boundaries will be reported
        with their start position in the decompressed stream.
        """
        DAT1_MAGIC = b'DAT1'
        results    = []
        n          = len(self._entries)
        # Rolling buffer to catch magic bytes spanning chunk boundaries
        tail       = bytearray()

        for i, chunk in self.iter_chunks():
            e          = self._entries[i]
            base_off   = e.decomp_cumulative - e.chunk_decomp

            # Prepend tail from previous chunk for boundary detection
            search_buf = tail + bytes(chunk)
            tail_len   = len(tail)

            pos = 0
            while True:
                idx = search_buf.find(DAT1_MAGIC, pos)
                if idx == -1:
                    break
                abs_off = base_off - tail_len + idx
                # Read size from DAT1 header if we have enough bytes
                header_slice = search_buf[idx:idx + 64]
                if len(header_slice) >= 8:
                    dat1_size = struct.unpack_from('<I', header_slice, 4)[0]
                    results.append((abs_off, dat1_size, bytes(header_slice)))
                pos = idx + 4

            # Keep last 3 bytes as tail (DAT1 magic is 4 bytes)
            tail = bytearray(chunk[-3:])

            if progress_cb:
                progress_cb(i + 1, n)

        return results

    # ── Info ──────────────────────────────────────────────────────────────────

    def info(self) -> str:
        """Return a human-readable summary of the archive."""
        lines = [
            f"DSAR Archive: {os.path.basename(self.path)}",
            f"  Chunks:      {self.entry_count:,}",
            f"  Header size: {self._header_size:,} bytes",
            f"  Total decomp: {self._total_decomp:,} bytes "
            f"({self._total_decomp / 1024 / 1024:.1f} MB)",
        ]
        if self._entries:
            sizes = [e.comp_size for e in self._entries]
            lines.append(f"  Chunk sizes: min={min(sizes):,}  "
                         f"max={max(sizes):,}  "
                         f"avg={sum(sizes)//len(sizes):,}")
        return '\n'.join(lines)


# ── Convenience function ──────────────────────────────────────────────────────

def extract_dsar(path: str, out_path: str,
                 progress_cb: Optional[Callable[[int, int], None]] = None):
    """
    Extract a DSAR archive to a flat decompressed file.

    Parameters
    ----------
    path     : path to the DSAR zone file
    out_path : path to write the decompressed stream
    progress_cb : optional progress callback (chunk_index, total_chunks)
    """
    extractor = DsarExtractor(path)
    print(extractor.info())
    extractor.decompress_all(progress_cb=progress_cb, out_path=out_path)
    print(f"Written to: {out_path}")
