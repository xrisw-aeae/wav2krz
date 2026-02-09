"""Kurzweil .for (Forte/PC3) file writer."""

import struct
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, List, Union

from .sample import KSample, Soundfilehead, Envelope
from .keymap import KKeymap, method_to_size
from .program import KProgram, Segment
from .hash import KHash
from .for_templates import build_program_data

# .for object type field0 values
FOR_TYPE_PROGRAM = 138
FOR_TYPE_KEYMAP = 133
FOR_TYPE_SAMPLE = 170

# .for IDs start at 1024
FOR_BASE_ID = 1024

# Extra bytes appended after each 48-byte SFH in .for samples
_SFH_EXTRA = b'\x00\x00\x03\xe0\x00\x00\x00\x00'


class ForWriter:
    """Writer for Kurzweil .for (Forte/PC3) files."""

    def __init__(self):
        self.samples: List[KSample] = []
        self.keymaps: List[KKeymap] = []
        self.programs: List[KProgram] = []

    def add_sample(self, sample: KSample) -> None:
        self.samples.append(sample)

    def add_keymap(self, keymap: KKeymap) -> None:
        self.keymaps.append(keymap)

    def add_program(self, program: KProgram) -> None:
        self.programs.append(program)

    def write(self, filepath: Path | str) -> None:
        filepath = Path(filepath)

        # Build mappings from .krz hashes to .for IDs and indices
        sample_index = {}   # krz sample hash -> 0-based index in write order
        sample_for_id = {}  # krz sample hash -> .for ID
        for i, s in enumerate(self.samples):
            sample_index[s.get_hash()] = i
            sample_for_id[s.get_hash()] = FOR_BASE_ID + i

        keymap_for_id = {}  # krz keymap hash -> .for ID
        for i, km in enumerate(self.keymaps):
            keymap_for_id[km.get_hash()] = FOR_BASE_ID + i

        # Prewrite samples (calculate data offsets)
        self._prewrite_samples()

        with open(filepath, 'wb') as f:
            # Write 32-byte header (magic COOL, osize placeholder at offset 8)
            f.write(b'COOL')
            f.write(b'\x00' * 4)     # bytes 4-7: zeros
            f.write(b'\x00' * 4)     # bytes 8-11: osize placeholder
            f.write(b'\x00' * 20)    # bytes 12-31: zeros

            # Write 4-byte outer block size placeholder
            outer_block_pos = f.tell()
            f.write(struct.pack('>i', 0))

            # Write all objects
            self._write_all_objects(f, sample_index, sample_for_id, keymap_for_id)

            # Write 4-byte null terminator after all objects
            f.write(b'\x00\x00\x00\x00')

            # Record osize (offset to sample audio data)
            osize = f.tell()

            # Calculate and write outer block size: -(osize - 36)
            outer_block_size = -(osize - 36)
            f.seek(outer_block_pos)
            f.write(struct.pack('>i', outer_block_size))
            f.seek(osize)

            # Write sample audio data
            self._write_sample_data(f)

            # Write osize at offset 8
            f.seek(8)
            f.write(struct.pack('>I', osize))

    def _prewrite_samples(self) -> None:
        offset = 0
        for sample in self.samples:
            for header in sample.headers:
                offset = header.prewrite(offset)

    def _write_sample_data(self, f: BinaryIO) -> None:
        for sample in self.samples:
            for header in sample.headers:
                header.write_sampledata(f)

    def _write_all_objects(self, f: BinaryIO, sample_index: dict,
                           sample_for_id: dict, keymap_for_id: dict) -> None:
        """Write .for objects: PGM, first KMP, all SMPs, remaining KMPs."""
        written_keymaps = set()

        for i, prog in enumerate(self.programs):
            for_id = FOR_BASE_ID + i
            self._write_for_program(f, prog, for_id, keymap_for_id)

            # Write the first keymap for this program
            if i < len(self.keymaps):
                km = self.keymaps[i]
                km_for_id = keymap_for_id[km.get_hash()]
                self._write_for_keymap(f, km, km_for_id, sample_index)
                written_keymaps.add(i)

        # Write all samples
        for sample in self.samples:
            for_id = sample_for_id[sample.get_hash()]
            self._write_for_sample(f, sample, for_id)

        # Write remaining keymaps (e.g. layer 2+ keymaps in drumset-multi)
        for i, km in enumerate(self.keymaps):
            if i not in written_keymaps:
                km_for_id = keymap_for_id[km.get_hash()]
                self._write_for_keymap(f, km, km_for_id, sample_index)

    # --- Object framing ---

    def _write_object_header(self, f: BinaryIO, field0: int, obj_id: int,
                             name: str, data_len: int) -> None:
        """Write 20-byte .for object header + padded name.

        field3 = total object size (header + name_padded + data).
        field4 = padded_name_length + 2.
        """
        name_bytes = name.encode('latin-1')[:16]
        # Pad name to 4-byte alignment (including null terminator)
        name_with_null = name_bytes + b'\x00'
        padded_len = (len(name_with_null) + 3) & ~3
        name_padded = name_with_null.ljust(padded_len, b'\x00')

        total_size = 20 + len(name_padded) + data_len
        field4 = padded_len + 2

        f.write(struct.pack('>I', field0))
        f.write(struct.pack('>I', obj_id))
        f.write(struct.pack('>I', 0))           # field2
        f.write(struct.pack('>I', total_size))   # field3
        f.write(struct.pack('>I', field4))       # field4
        f.write(name_padded)

    # --- Sample serialization ---

    def _write_for_sample(self, f: BinaryIO, sample: KSample, for_id: int) -> None:
        # Serialize sample data to buffer first to know size
        buf = BytesIO()
        self._write_sample_data_region(buf, sample)
        data = buf.getvalue()

        self._write_object_header(f, FOR_TYPE_SAMPLE, for_id, sample.name, len(data))
        f.write(data)

    def _write_sample_data_region(self, f: BinaryIO, sample: KSample) -> None:
        """Write sample object data (metadata + SFHs + envelopes)."""
        is_stereo = sample.is_stereo()
        num_headers = len(sample.headers)

        # 16-byte metadata prefix
        f.write(struct.pack('>I', sample.base_id))          # base_id
        f.write(struct.pack('>H', 1))                       # num_subsamples
        f.write(struct.pack('>H', 1 if is_stereo else 0))   # stereo_flag
        f.write(struct.pack('>H', 8))                       # headers_ofs
        f.write(struct.pack('>B', 5 if is_stereo else 4))   # subsample_count byte
        f.write(b'\x00' * 5)                                # padding

        # Write 48-byte SFH for each header + 8-byte extra
        for i, hdr in enumerate(sample.headers):
            self._write_for_sfh(f, hdr, i, num_headers)
            f.write(_SFH_EXTRA)

        # Write envelopes (12 bytes each, same as .krz)
        for env in sample.envelopes:
            env.write(f)

        # Pad to expected size: 120 for mono, 176 for stereo
        current = 16 + num_headers * (48 + 8) + len(sample.envelopes) * 12
        target = 176 if is_stereo else 120
        if current < target:
            f.write(b'\x00' * (target - current))

    def _write_for_sfh(self, f: BinaryIO, hdr: Soundfilehead,
                       header_idx: int, num_headers: int) -> None:
        """Write a 48-byte .for Soundfilehead (widened from 32-byte .krz SFH)."""
        f.write(struct.pack('>b', hdr.rootkey))
        f.write(struct.pack('>B', hdr.flags))
        f.write(struct.pack('>b', hdr.volume_adjust))
        f.write(struct.pack('>b', hdr.alt_volume_adjust))
        f.write(struct.pack('>h', hdr.max_pitch))
        f.write(struct.pack('>h', hdr.offset_to_name))
        # Widen 4-byte sample offsets to 8-byte, multiply by 2 (samples -> bytes)
        f.write(struct.pack('>q', hdr.sample_start * 2))
        f.write(struct.pack('>q', hdr.alt_sample_start * 2))
        f.write(struct.pack('>q', hdr.sample_loop_start * 2))
        f.write(struct.pack('>q', hdr.sample_end * 2))
        # Envelope offsets — distance from the envofs field to envelope data.
        # envofs field is at SFH+40; after each SFH there's an 8-byte extra block,
        # so each SFH+extra is 56 bytes. Envelope data follows the last extra block.
        remaining = num_headers - header_idx - 1
        envofs = remaining * 56 + 16
        f.write(struct.pack('>h', envofs))
        f.write(struct.pack('>h', envofs - 2))
        f.write(struct.pack('>i', hdr.sample_period))

    # --- Keymap serialization ---

    def _write_for_keymap(self, f: BinaryIO, keymap: KKeymap,
                          for_id: int, sample_index: dict) -> None:
        buf = BytesIO()
        self._write_keymap_data_region(buf, keymap, sample_index)
        data = buf.getvalue()

        self._write_object_header(f, FOR_TYPE_KEYMAP, for_id, keymap.name, len(data))
        f.write(data)

    def _write_keymap_data_region(self, f: BinaryIO, keymap: KKeymap,
                                  sample_index: dict) -> None:
        """Write keymap object data with .for encoding."""
        max_sample_idx = self._find_max_sample_index(keymap, sample_index)
        num_distinct = self._count_distinct_samples(keymap, sample_index)

        if num_distinct <= 1:
            self._write_compact_keymap(f, max_sample_idx)
        else:
            self._write_full_keymap(f, keymap, sample_index, max_sample_idx)

    def _write_compact_keymap(self, f: BinaryIO, max_sample_idx: int) -> None:
        """Write compact 32-byte keymap for single-sample keymaps (method=1)."""
        f.write(struct.pack('>BB', 0x04, max_sample_idx))  # prefix
        f.write(struct.pack('>h', 1))         # method=1 (subsample only)
        f.write(struct.pack('>h', 0))         # base_pitch
        f.write(struct.pack('>H', 0x7FFF))    # cents_per_entry
        f.write(struct.pack('>h', 0))         # entries_per_vel (1 entry)
        f.write(struct.pack('>h', 1))         # entry_size
        # Level offsets: 16 14 12 10 8 6 4 2
        for j in range(8):
            f.write(struct.pack('>h', (8 - j) * 2))
        # Single entry: subsample=1, then 3 bytes padding
        f.write(b'\x01\x00\x00\x00')

    def _write_full_keymap(self, f: BinaryIO, keymap: KKeymap,
                           sample_index: dict, max_sample_idx: int) -> None:
        """Write full keymap for multi-sample keymaps."""
        # 2-byte prefix: [0x04][max_sample_index]
        f.write(struct.pack('>BB', 0x04, max_sample_idx))

        # Keymap header (same as .krz but without sample_id field)
        f.write(struct.pack('>h', keymap.method))
        f.write(struct.pack('>h', keymap.base_pitch))
        f.write(struct.pack('>h', keymap.cents_per_entry))
        f.write(struct.pack('>h', keymap.entries_per_vel))
        f.write(struct.pack('>h', keymap.entry_size))

        # Level offsets (same calculation as .krz)
        for j in range(8):
            ofs = (8 - j) * 2
            ofs += keymap.velocity_mapping[j] * keymap.velocity_levels[keymap.velocity_mapping[j]].get_size()
            f.write(struct.pack('>h', ofs))

        # Write velocity level entry data with index-based sample refs
        for vl in keymap.velocity_levels:
            for entry in vl.entries:
                self._write_for_entry(f, entry, vl.method, sample_index)

    def _count_distinct_samples(self, keymap: KKeymap, sample_index: dict) -> int:
        """Count distinct samples referenced by this keymap."""
        seen = set()
        for vl in keymap.velocity_levels:
            for entry in vl.entries:
                if entry.is_used():
                    sample_hash = KHash.generate(entry.sample_id, KHash.T_SAMPLE)
                    idx = sample_index.get(sample_hash, 0)
                    seen.add(idx)
        return len(seen)

    def _write_for_entry(self, f: BinaryIO, entry, method: int,
                         sample_index: dict) -> None:
        """Write a single keymap entry with .for sample index encoding."""
        if method & 0x10:
            f.write(struct.pack('>h', entry.tuning))
        elif method & 0x08:
            f.write(struct.pack('>b', entry.tuning & 0xFF))

        if method & 0x04:
            f.write(struct.pack('>b', entry.volume_adjust))

        if method & 0x02:
            # Convert sample_id to .for index-based format: [0x04][index]
            sample_hash = KHash.generate(entry.sample_id, KHash.T_SAMPLE)
            idx = sample_index.get(sample_hash, 0)
            f.write(struct.pack('>BB', 0x04, idx))

        if method & 0x01:
            f.write(struct.pack('>B', entry.subsample_number & 0xFF))

    def _find_max_sample_index(self, keymap: KKeymap, sample_index: dict) -> int:
        """Find the highest sample index referenced by this keymap."""
        max_idx = 0
        for vl in keymap.velocity_levels:
            for entry in vl.entries:
                if entry.is_used():
                    sample_hash = KHash.generate(entry.sample_id, KHash.T_SAMPLE)
                    idx = sample_index.get(sample_hash, 0)
                    max_idx = max(max_idx, idx)
        return max_idx

    # --- Program serialization (template-based) ---

    def _write_for_program(self, f: BinaryIO, program: KProgram,
                           for_id: int, keymap_for_id: dict) -> None:
        # Count layers and extract layer info from KProgram segments
        layer_count = 0
        keymap_ids = []
        key_ranges = []

        for seg in program.segments:
            if seg.tag == Segment.PGMSEGTAG:
                layer_count = seg.data[1]
            elif seg.tag == Segment.LYRSEGTAG:
                lo = seg.data[3]
                hi = seg.data[4]
                key_ranges.append((lo, hi))
            elif seg.tag == Segment.CALSEGTAG:
                # Extract .krz keymap ID (BE16 at data[7:9])
                krz_km_id = (seg.data[7] << 8) | seg.data[8]
                krz_km_hash = KHash.generate(krz_km_id, KHash.T_KEYMAP)
                for_km_id = keymap_for_id.get(krz_km_hash, FOR_BASE_ID)
                keymap_ids.append(for_km_id)

        if layer_count == 0:
            layer_count = 1
        if not key_ranges:
            key_ranges = [(0, 127)]
        if not keymap_ids:
            keymap_ids = [FOR_BASE_ID]

        data = build_program_data(layer_count, keymap_ids, key_ranges)

        self._write_object_header(f, FOR_TYPE_PROGRAM, for_id, program.name, len(data))
        f.write(data)
