"""Kurzweil KKeymap, VeloLevel, and KeymapEntry structures."""

import struct
from dataclasses import dataclass, field
from typing import BinaryIO, List

from .hash import KHash
from .sample import KSample


@dataclass
class KeymapEntry:
    """
    Single entry in a keymap velocity level.

    Stores sample reference and tuning for one key.
    """
    tuning: int = 0  # Cents offset from root
    volume_adjust: int = 0
    sample_id: int = 0  # Sample ID (not hash)
    subsample_number: int = 0  # 0 = unused, 1+ = subsample index

    def is_used(self) -> bool:
        """Check if this entry is assigned to a sample."""
        return self.subsample_number != 0

    def write(self, f: BinaryIO, method: int) -> None:
        """
        Write entry to file based on method flags.

        Method bits:
        - 0x10: 2-byte tuning
        - 0x08: 1-byte tuning
        - 0x04: volume adjust
        - 0x02: sample ID
        - 0x01: subsample number
        """
        if method & 0x10:
            f.write(struct.pack('>h', self.tuning))
        elif method & 0x08:
            f.write(struct.pack('>b', self.tuning & 0xFF))

        if method & 0x04:
            f.write(struct.pack('>b', self.volume_adjust))

        if method & 0x02:
            f.write(struct.pack('>h', self.sample_id))

        if method & 0x01:
            f.write(struct.pack('>b', self.subsample_number & 0xFF))


@dataclass
class VeloLevel:
    """
    Velocity level containing keymap entries for each key.

    A keymap can have multiple velocity levels (up to 8).
    """
    method: int = 0
    rang: int = 0
    entries: List[KeymapEntry] = field(default_factory=list)

    def __init__(self, method: int, num_entries: int):
        """
        Create a velocity level with empty entries.

        Args:
            method: Method flags controlling data format
            num_entries: Number of entries (typically 128 for full range)
        """
        self.method = method
        self.rang = 0
        self.entries = [KeymapEntry() for _ in range(num_entries)]

    def set_rang(self, rang: int) -> None:
        """Set the rank/order of this velocity level."""
        self.rang = rang

    def get_rang(self) -> int:
        """Get the rank/order of this velocity level."""
        return self.rang

    def set_method(self, method: int) -> None:
        """Set the method flags."""
        self.method = method

    def get_size(self) -> int:
        """Calculate byte size of this velocity level."""
        return len(self.entries) * method_to_size(self.method)

    def write(self, f: BinaryIO) -> None:
        """Write all entries to file."""
        for entry in self.entries:
            entry.write(f, self.method)

    def set_sample(self, sample: KSample, key: int) -> None:
        """
        Set a sample at a specific key position.

        Args:
            sample: KSample to assign
            key: Key index (0-127)
        """
        self.entries[key].subsample_number = 1
        self.entries[key].sample_id = KHash.get_id(sample.get_hash())
        # Tuning: cents offset from key 48 (C3)
        self.entries[key].tuning = 100 * (48 - key)

    def set_sample_at_root(self, sample: KSample, subsample: int, key: int) -> None:
        """
        Set a sample at a specific key with root-based tuning.

        The sample plays at its original pitch at the assigned key.

        Args:
            sample: KSample to assign
            subsample: Subsample index (0-based)
            key: Key index (0-127)
        """
        self.entries[key].subsample_number = subsample + 1
        self.entries[key].sample_id = KHash.get_id(sample.get_hash())
        # Get root key from sample header and calculate tuning
        root = sample.headers[subsample].rootkey - 12
        self.entries[key].tuning = 100 * (root - key)

    def fill_spaces_between_samples(self) -> None:
        """
        Fill gaps between assigned samples by extending neighboring samples.

        Propagates samples both up and down the keyboard to fill unassigned keys.
        Each pass fills one entry from each sample boundary, alternating directions
        until the entire keyboard is covered.
        """
        fill = True
        while fill:
            fill = False

            # Propagate upward - fill one entry per boundary per pass
            i = 1
            while i < len(self.entries):
                if not self.entries[i].is_used() and self.entries[i-1].is_used():
                    self.entries[i] = KeymapEntry(
                        tuning=self.entries[i-1].tuning,
                        volume_adjust=self.entries[i-1].volume_adjust,
                        sample_id=self.entries[i-1].sample_id,
                        subsample_number=self.entries[i-1].subsample_number
                    )
                    i += 1  # Skip one to only fill one per boundary per pass
                    fill = True
                i += 1

            # Propagate downward - fill one entry per boundary per pass
            i = len(self.entries) - 2
            while i >= 0:
                if not self.entries[i].is_used() and self.entries[i+1].is_used():
                    self.entries[i] = KeymapEntry(
                        tuning=self.entries[i+1].tuning,
                        volume_adjust=self.entries[i+1].volume_adjust,
                        sample_id=self.entries[i+1].sample_id,
                        subsample_number=self.entries[i+1].subsample_number
                    )
                    i -= 1  # Skip one to only fill one per boundary per pass
                    fill = True
                i -= 1


def method_to_size(method: int) -> int:
    """
    Calculate entry size in bytes based on method flags.

    Args:
        method: Method flags

    Returns:
        Size in bytes per entry
    """
    size = 0
    if method & 0x10:
        size += 2  # 2-byte tuning
    elif method & 0x08:
        size += 1  # 1-byte tuning
    if method & 0x04:
        size += 1  # volume adjust
    if method & 0x02:
        size += 2  # sample ID
    if method & 0x01:
        size += 1  # subsample number
    return size


@dataclass
class KKeymap:
    """
    Kurzweil Keymap object.

    Maps MIDI keys to samples through velocity levels.
    """
    name: str = ""
    hash_val: int = 0

    sample_id: int = 0  # Default sample ID (when method & 0x02 == 0)
    method: int = 0x13  # Default: 2-byte tuning + sample ID + subsample
    base_pitch: int = 0
    cents_per_entry: int = 100
    entries_per_vel: int = 127  # Stored as count - 1
    entry_size: int = 0

    levels: List[int] = field(default_factory=lambda: [0] * 8)
    velocity_levels: List[VeloLevel] = field(default_factory=list)
    velocity_mapping: List[int] = field(default_factory=lambda: [0] * 8)

    def set_name(self, name: str) -> None:
        """Set keymap name (max 16 characters)."""
        if len(name) > 16:
            self.name = name[:16]
        else:
            self.name = name

    def set_hash(self, hash_val: int) -> None:
        """Set object hash."""
        self.hash_val = hash_val

    def get_hash(self) -> int:
        """Get object hash."""
        return self.hash_val

    def set_method(self, method: int) -> None:
        """Set method flags and update entry size."""
        self.method = method
        self.entry_size = method_to_size(method)
        for vl in self.velocity_levels:
            vl.set_method(method)

    def insert_level(self, vl: VeloLevel) -> None:
        """Add a velocity level."""
        self.velocity_levels.append(vl)
        vl.set_rang(len(self.velocity_levels) - 1)

    def new_level(self) -> VeloLevel:
        """Create and add a new velocity level."""
        vl = VeloLevel(self.method, self.entries_per_vel + 1)
        self.velocity_levels.append(vl)
        vl.set_rang(len(self.velocity_levels) - 1)
        return vl

    def get_size(self) -> int:
        """Calculate total object size for writing."""
        # Base size: name + padding + size/ofs fields
        name_len = len(self.name)
        name_padded = name_len + (1 if name_len % 2 == 1 else 2)
        base_size = name_padded + 4

        # Keymap header: 28 bytes
        data_size = 28

        # Entry data
        data_size += len(self.velocity_levels) * self.entry_size * (self.entries_per_vel + 1)

        return base_size + data_size

    def write(self, f: BinaryIO) -> None:
        """Write keymap object to file."""
        start_pos = f.tell()

        # Write hash
        f.write(struct.pack('>H', self.hash_val & 0xFFFF))

        # Placeholder for size
        size_pos = f.tell()
        f.write(struct.pack('>H', 0))

        # Calculate and write name offset
        name_len = len(self.name)
        if name_len % 2 == 0:
            ofs = name_len + 4
            f.write(struct.pack('>H', ofs))
            f.write(self.name.encode('latin-1'))
            f.write(b'\x00\x00')
        else:
            ofs = name_len + 3
            f.write(struct.pack('>H', ofs))
            f.write(self.name.encode('latin-1'))
            f.write(b'\x00')

        # Write keymap header
        f.write(struct.pack('>h', self.sample_id))
        f.write(struct.pack('>h', self.method))
        f.write(struct.pack('>h', self.base_pitch))
        f.write(struct.pack('>h', self.cents_per_entry))
        f.write(struct.pack('>h', self.entries_per_vel))
        f.write(struct.pack('>h', self.entry_size))

        # Calculate and write level offsets
        # Level offsets point to velocity level data, adjusted during write
        level_offsets = [0] * 8
        for j in range(8):
            level_offsets[j] = (8 - j) * 2
            level_offsets[j] += self.velocity_mapping[j] * self.velocity_levels[self.velocity_mapping[j]].get_size()

        for offset in level_offsets:
            f.write(struct.pack('>h', offset))

        # Write velocity level data
        for vl in self.velocity_levels:
            vl.write(f)

        # Pad to 2-byte boundary
        end_pos = f.tell()
        if (end_pos - start_pos) % 2 != 0:
            f.write(b'\x00')

        # Go back and write actual size
        end_pos = f.tell()
        size = end_pos - size_pos + 2
        f.seek(size_pos)
        f.write(struct.pack('>H', size))
        f.seek(end_pos)


def create_instrument_keymap(samples: List[KSample] | KSample, keymap_id: int, name: str) -> KKeymap:
    """
    Create a keymap for instrument mode (samples pitched across keyboard).

    Each sample is placed at its root key position, then gaps are filled
    by extending neighboring samples across the keyboard.

    Args:
        samples: KSample or list of KSamples to map
        keymap_id: Keymap ID number
        name: Keymap name

    Returns:
        KKeymap configured for instrument use
    """
    # Handle single sample for backwards compatibility
    if isinstance(samples, KSample):
        samples = [samples]

    km = KKeymap()
    km.set_name(name.lower()[:16])
    km.set_hash(KHash.generate(keymap_id, KHash.T_KEYMAP))

    # Method: 0x03 = sample ID (0x02) + subsample (0x01) = 3 bytes/entry
    # Tuning is calculated automatically by Kurzweil using centsPerEntry
    km.method = 0x03
    km.entry_size = method_to_size(km.method)
    km.entries_per_vel = 127
    km.cents_per_entry = 100

    # Create single velocity level
    vl = km.new_level()

    # Place each sample at its root key position (root key - 12 as in Java code)
    for sample in samples:
        # Iterate over all headers (for stereo, headers 0 and 1 share same root)
        for header_idx in range(len(sample.headers)):
            # For stereo samples, only use even indices (0, 2, 4...)
            # since odd indices are the right channel of the same sample
            if sample.is_stereo() and header_idx % 2 == 1:
                continue

            root_key = sample.headers[header_idx].rootkey
            key_position = root_key - 12
            if key_position < 0:
                key_position = 0
            if key_position > 127:
                key_position = 127

            vl.set_sample_at_root(sample, header_idx, key_position)

    # Fill entire keyboard by extending samples to cover gaps
    vl.fill_spaces_between_samples()

    # All velocities use the same level
    km.velocity_mapping = [0] * 8

    return km


def create_drumset_keymap(samples: List[KSample], keymap_id: int, name: str,
                          start_key: int = 36) -> KKeymap:
    """
    Create a keymap for drumset mode (each sample on a different key).

    Args:
        samples: List of KSamples to map
        keymap_id: Keymap ID number
        name: Keymap name
        start_key: First MIDI key to use (default 36 = C1)

    Returns:
        KKeymap configured for drumset use
    """
    km = KKeymap()
    km.set_name(name.lower()[:16])
    km.set_hash(KHash.generate(keymap_id, KHash.T_KEYMAP))

    # Method: 0x13 = 2-byte tuning (0x10) + sample ID (0x02) + subsample (0x01)
    km.method = 0x13
    km.entry_size = method_to_size(km.method)
    km.entries_per_vel = 127
    km.cents_per_entry = 100

    # Create single velocity level
    vl = km.new_level()

    # Map each sample to consecutive keys
    for i, sample in enumerate(samples):
        key = start_key + i
        if key < 128:
            # Use set_sample_at_root so each sample plays at its original pitch
            vl.set_sample_at_root(sample, 0, key)

    # Fill gaps
    vl.fill_spaces_between_samples()

    # All velocities use the same level
    km.velocity_mapping = [0] * 8

    return km
