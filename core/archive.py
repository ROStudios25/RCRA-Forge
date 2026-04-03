import os
import struct
import ctypes
from dataclasses import dataclass, field
from typing import Optional, List

# --- Constants from ALERT Source ---
TOC_MAGIC_RCRA = 0x34E89035
DAT1_MAGIC     = 0x44415431
TAG_ARCHIVES   = 0x398ABFF0
TAG_ASSET_IDS  = 0x506D7B8A
TAG_SIZES      = 0x65BCF461

# --- Asset type identifiers (Required by ui/asset_browser.py) ---
ASSET_TYPE_NAMES = {
    0x98906B9F: 'model',
    0x5C4580B9: 'texture',
    0x8A8B1487: 'zone',
    0x2AFE7495: 'level',
}

class HashManager:
    """Matches Hex IDs to real paths using hashes.txt."""
    def __init__(self):
        self.map = {}
        hash_path = "hashes.txt"
        if os.path.exists(hash_path):
            print(f"Loading names from {hash_path}...")
            with open(hash_path, "r", encoding="utf-8") as f:
                lines = f.readlines() 
            for line in lines:
                parts = line.strip().replace(',', ' ').split(maxsplit=1)
                if len(parts) == 2:
                    try:
                        h_int = int(parts[0].lower().replace("0x", ""), 16)
                        self.map[h_int] = parts[1].strip()
                    except ValueError: 
                        continue
            print(f"Successfully mapped {len(self.map)} asset names.")

    def get_name(self, asset_id: int) -> str:
        return self.map.get(asset_id, f"{asset_id:016X}")

@dataclass
class AssetEntry:
    index: int
    asset_id: int
    archive: int
    offset: int
    size: int
    display_name: str
    header: Optional[bytes] = None

    @property
    def name(self) -> str:
        return self.display_name

class _LazyEntryList:
    """CRITICAL: The UI needs this to display the assets and stop the loading hang."""
    def __init__(self, entries: List[AssetEntry]):
        self.entries = entries
    def __len__(self):
        return len(self.entries)
    def __getitem__(self, i):
        return self.entries[i]

class DAT1:
    """Class expected by core/mesh.py and other modules."""
    def __init__(self, data: bytes):
        self.data = data
        self.sections = {}
        self._parse()

    def _parse(self):
        if len(self.data) < 16: return
        magic, _, _, sec_count = struct.unpack_from("<IIII", self.data, 0)
        if magic != DAT1_MAGIC: return
        for i in range(sec_count):
            tag, off, sz = struct.unpack_from("<III", self.data, 16 + i * 12)
            self.sections[tag] = self.data[off : off + sz]

class TocParser:
    def __init__(self, toc_path: str):
        self.toc_path = toc_path
        self.game_root = os.path.dirname(toc_path)
        self.entries = [] 
        self.archives = []
        self.hash_mgr = HashManager()

    def parse(self) -> bool:
        """Fast parser using numpy-style bulk reading."""
        import numpy as np
        if not os.path.exists(self.toc_path): return False

        with open(self.toc_path, "rb") as f:
            magic, size = struct.unpack("<II", f.read(8))
            if magic != TOC_MAGIC_RCRA: return False

            dat1_raw = f.read(size)
            container = DAT1(dat1_raw) 
            sections = container.sections

            if TAG_ARCHIVES in sections:
                raw = sections[TAG_ARCHIVES]
                for i in range(len(raw) // 66):
                    name = raw[i*66 : i*66+40].split(b'\x00')[0].decode('ascii', 'ignore')
                    self.archives.append(name)

            ids = np.frombuffer(sections[TAG_ASSET_IDS], dtype='<u8')
            meta = np.frombuffer(sections[TAG_SIZES], dtype=[
                ('val', '<u4'), ('arc', '<u4'), ('off', '<u4'), ('hdr', '<i4')
            ])

            temp_entries = []
            for i in range(min(len(ids), len(meta))):
                row = meta[i]
                asset_id = int(ids[i])
                temp_entries.append(AssetEntry(
                    index=i, asset_id=asset_id,
                    archive=int(row['arc']), offset=int(row['off']),
                    size=int(row['val']), display_name=self.hash_mgr.get_name(asset_id)
                ))
            
            # MANDATORY: This wraps the data so the UI can finish the Loading bar
            self.entries = _LazyEntryList(temp_entries)
            
        return True