"""
profile_toc.py
Run this directly against your game's toc file to find the bottleneck.

Usage:
    python profile_toc.py "C:\path\to\Ratchet & Clank - Rift Apart\toc"

This bypasses the UI entirely and times each parsing step individually.
"""

import sys
import os
import time
import struct

sys.path.insert(0, os.path.dirname(__file__))


def profile(path: str):
    print(f"\nProfiling TOC: {path}")
    print(f"File size: {os.path.getsize(path) / 1024 / 1024:.1f} MB\n")

    # ── Step 1: Raw file read ─────────────────────────────────────────────────
    t = time.perf_counter()
    with open(path, 'rb') as f:
        raw = f.read()
    print(f"[{time.perf_counter()-t:.3f}s] Step 1: Read {len(raw):,} bytes from disk")

    # ── Step 2: Magic + DAT1 slice ────────────────────────────────────────────
    t = time.perf_counter()
    from core.archive import TOC_MAGIC_RCRA, TOC_MAGIC_MSMR, DAT1
    import zlib
    magic, size = struct.unpack_from('<II', raw, 0)
    print(f"  magic = {magic:#010x}", end="  ")
    if magic == TOC_MAGIC_RCRA:
        print("(RCRA — not compressed)")
        dat1_data = raw[8:8 + size]
    elif magic == TOC_MAGIC_MSMR:
        print("(MSMR — zlib compressed)")
        dat1_data = zlib.decompress(raw[8:])
    else:
        print(f"UNKNOWN MAGIC — cannot parse")
        return
    print(f"[{time.perf_counter()-t:.3f}s] Step 2: Sliced DAT1 data ({len(dat1_data):,} bytes)")

    # ── Step 3: DAT1 header parse ─────────────────────────────────────────────
    t = time.perf_counter()
    dat1 = DAT1(dat1_data)
    print(f"[{time.perf_counter()-t:.3f}s] Step 3: Parsed DAT1 ({len(dat1.sections)} sections)")
    for tag, data in dat1.sections.items():
        print(f"  section {tag:#010x}: {len(data):,} bytes")

    # ── Step 4: Asset IDs numpy parse ─────────────────────────────────────────
    from core.archive import TAG_ASSET_IDS, TAG_SIZES, TAG_ARCHIVES, TAG_ASSET_HEADERS
    import numpy as np

    t = time.perf_counter()
    ids_data = dat1.get_section(TAG_ASSET_IDS)
    if ids_data is None:
        print("ERROR: No asset IDs section found!")
        return
    ids_arr = np.frombuffer(ids_data, dtype='<u8')
    print(f"[{time.perf_counter()-t:.3f}s] Step 4: Parsed {len(ids_arr):,} asset IDs")

    # ── Step 5: Sizes numpy parse ─────────────────────────────────────────────
    t = time.perf_counter()
    sizes_data = dat1.get_section(TAG_SIZES)
    if sizes_data is None:
        print("ERROR: No sizes section found!")
        return
    sizes_arr = np.frombuffer(sizes_data, dtype=np.dtype([
        ('value',   '<u4'),
        ('archive', '<u4'),
        ('offset',  '<u4'),
        ('hdr_off', '<i4'),
    ]))
    print(f"[{time.perf_counter()-t:.3f}s] Step 5: Parsed {len(sizes_arr):,} size entries")

    # ── Step 6: Header blobs ──────────────────────────────────────────────────
    t = time.perf_counter()
    hdrs_data = dat1.get_section(TAG_ASSET_HEADERS)
    hdr_bytes = bytes(hdrs_data) if hdrs_data else b''
    print(f"[{time.perf_counter()-t:.3f}s] Step 6: Header blobs ({len(hdr_bytes):,} bytes, "
          f"{len(hdr_bytes)//36} entries)")

    # ── Step 7: Archives parse ────────────────────────────────────────────────
    t = time.perf_counter()
    arc_data = dat1.get_section(TAG_ARCHIVES)
    archives = []
    RCRA_ARC_SIZE = 66
    if arc_data:
        for i in range(len(arc_data) // RCRA_ARC_SIZE):
            raw_arc = bytes(arc_data[i * RCRA_ARC_SIZE:(i + 1) * RCRA_ARC_SIZE])
            fn = raw_arc[:40]
            null = fn.find(b'\x00')
            if null >= 0:
                fn = fn[:null]
            archives.append(fn.decode('ascii', errors='replace').replace('\\', '/'))
    print(f"[{time.perf_counter()-t:.3f}s] Step 7: Parsed {len(archives)} archive filenames")
    for i, a in enumerate(archives[:5]):
        print(f"  [{i}] {a}")
    if len(archives) > 5:
        print(f"  ... and {len(archives)-5} more")

    # ── Step 8: LazyEntryList construction ────────────────────────────────────
    t = time.perf_counter()
    from core.archive import _LazyEntryList
    entries = _LazyEntryList(ids_arr, sizes_arr, hdrs_data, min(len(ids_arr), len(sizes_arr)))
    print(f"[{time.perf_counter()-t:.3f}s] Step 8: Built LazyEntryList ({len(entries):,} entries)")

    # ── Step 9: numpy argsort grouping ────────────────────────────────────────
    t = time.perf_counter()
    arc_col     = sizes_arr['archive'][:len(entries)].astype(np.int32)
    sort_idx    = np.argsort(arc_col, kind='stable')
    sorted_arcs = arc_col[sort_idx]
    boundaries  = np.where(np.diff(sorted_arcs))[0] + 1
    starts = np.concatenate([[0], boundaries])
    ends   = np.concatenate([boundaries, [len(sort_idx)]])
    groups_numpy = [(int(sorted_arcs[s]), sort_idx[s:e]) for s, e in zip(starts.tolist(), ends.tolist())]
    print(f"[{time.perf_counter()-t:.3f}s] Step 9: Grouped into {len(groups_numpy)} archives (numpy, no .tolist())")

    # ── Step 10: .tolist() conversion (old slow path) ─────────────────────────
    t = time.perf_counter()
    groups_list = [(arc, idx.tolist()) for arc, idx in groups_numpy]
    print(f"[{time.perf_counter()-t:.3f}s] Step 10: .tolist() conversion (this was the bottleneck!)")

    # ── Step 11: Access a single entry (lazy) ─────────────────────────────────
    t = time.perf_counter()
    e = entries[0]
    print(f"[{time.perf_counter()-t:.3f}s] Step 11: First entry = {e.asset_id:#018x} "
          f"arc={e.archive} off={e.offset:#x} size={e.size:,}")

    print(f"\n{'─'*60}")
    print(f"Total assets: {len(entries):,}")
    print(f"Total archives: {len(archives)}")
    print(f"\nIf any step above took > 0.5s, that's your bottleneck.")
    print(f"Steps 1-9 should all be < 0.1s on NVMe + modern CPU.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Search ALL available drives for the game
        import string
        import ctypes

        # Get all available drive letters on Windows
        drives = []
        if sys.platform == 'win32':
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drives.append(letter)
                bitmask >>= 1
        else:
            drives = list(string.ascii_uppercase)

        print(f"Searching drives: {', '.join(d + ':' for d in drives)}")

        # Common Steam install paths to check on each drive
        steam_paths = [
            r"Steam\steamapps\common\Ratchet & Clank - Rift Apart\toc",
            r"SteamLibrary\steamapps\common\Ratchet & Clank - Rift Apart\toc",
            r"Games\Steam\steamapps\common\Ratchet & Clank - Rift Apart\toc",
            r"Program Files (x86)\Steam\steamapps\common\Ratchet & Clank - Rift Apart\toc",
            r"Program Files\Steam\steamapps\common\Ratchet & Clank - Rift Apart\toc",
        ]

        toc_path = None
        for drive in drives:
            for steam_path in steam_paths:
                candidate = f"{drive}:\\{steam_path}"
                if os.path.exists(candidate):
                    toc_path = candidate
                    break
            if toc_path:
                break

        if toc_path:
            print(f"Auto-detected toc: {toc_path}\n")
            profile(toc_path)
        else:
            print("\nCould not auto-detect toc file on any drive.")
            print("Please provide the path manually:")
            print('  python profile_toc.py "U:\\Steam\\steamapps\\common\\Ratchet & Clank - Rift Apart\\toc"')
    else:
        profile(sys.argv[1])

    input("\nPress Enter to close...")