"""Kurzweil KSample, Soundfilehead, and Envelope structures."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO, Optional

from .hash import KHash

if TYPE_CHECKING:
    from ..wav.parser import WavFile


@dataclass
class Envelope:
    """
    Sample envelope structure (12 bytes = 6 shorts).

    Default values create a simple sustain envelope.
    """
    data: list[int] = field(default_factory=lambda: [-1, 1, 0, 0, -1600, 0])

    def write(self, f: BinaryIO) -> None:
        """Write envelope to file (big-endian)."""
        for val in self.data:
            f.write(struct.pack('>h', val))

    @classmethod
    def size(cls) -> int:
        """Return size in bytes."""
        return 12


@dataclass
class Soundfilehead:
    """
    Sample audio header structure (32 bytes).

    Contains metadata about one audio layer (mono channel or one side of stereo).
    """
    rootkey: int = 60  # MIDI note number
    flags: int = 0x70  # 0x40=needs load, 0x80=loop off
    volume_adjust: int = 0
    alt_volume_adjust: int = 0
    max_pitch: int = 0
    offset_to_name: int = 0
    sample_start: int = 0
    alt_sample_start: int = 0
    sample_loop_start: int = 0
    sample_end: int = 0
    offset_to_envelope: int = 0
    alt_offset_to_envelope: int = 0
    sample_period: int = 22675  # ~44100 Hz in nanoseconds

    sampledata: Optional[bytes] = field(default=None, repr=False)

    def set_root_key(self, key: int) -> None:
        """Set root key and calculate max_pitch."""
        self.rootkey = key
        # maxPitch calculation from Java:
        # Math.ceil(1200.0*Math.log(96000.0/1000000000L*samplePeriod)/Math.log(2.0)) + 100*r - 1200
        if self.sample_period > 0:
            ratio = 96000.0 / 1_000_000_000 * self.sample_period
            if ratio > 0:
                cents = 1200.0 * math.log(ratio) / math.log(2.0)
                self.max_pitch = int(math.ceil(cents) + 100 * key - 1200)

    def needs_load(self) -> bool:
        """Check if sample needs to be loaded (RAM-based)."""
        return (self.flags & 0x40) == 0x40

    def is_looped(self) -> bool:
        """Check if sample is looped (0 = looped, 0x80 = not looped)."""
        return (self.flags & 0x80) == 0

    def get_ram_size(self) -> int:
        """Get RAM size needed for this sample."""
        if self.needs_load():
            return (self.sample_end - self.sample_start + 1) * 2
        return 0

    def write(self, f: BinaryIO) -> None:
        """Write header to file (big-endian)."""
        f.write(struct.pack('>b', self.rootkey))
        f.write(struct.pack('>B', self.flags))
        f.write(struct.pack('>b', self.volume_adjust))
        f.write(struct.pack('>b', self.alt_volume_adjust))
        f.write(struct.pack('>h', self.max_pitch))
        f.write(struct.pack('>h', self.offset_to_name))
        f.write(struct.pack('>i', self.sample_start))
        f.write(struct.pack('>i', self.alt_sample_start))
        f.write(struct.pack('>i', self.sample_loop_start))
        f.write(struct.pack('>i', self.sample_end))
        f.write(struct.pack('>h', self.offset_to_envelope))
        f.write(struct.pack('>h', self.alt_offset_to_envelope))
        f.write(struct.pack('>i', self.sample_period))

    def write_sampledata(self, f: BinaryIO) -> None:
        """Write sample data to file."""
        if self.needs_load() and self.sampledata:
            f.write(self.sampledata)

    def prewrite(self, offset: int) -> int:
        """
        Prepare sample for writing by adjusting offsets.

        Args:
            offset: Current byte offset in sample data section

        Returns:
            New offset after this sample's data
        """
        if self.needs_load():
            # Normalize offsets relative to start
            self.sample_end -= self.sample_start
            self.sample_loop_start -= self.sample_start
            self.alt_sample_start -= self.sample_start
            self.sample_start = 0
            if self.alt_sample_start > self.sample_end:
                self.alt_sample_start = self.sample_start

            # Add current offset (in samples, not bytes)
            sample_offset = offset // 2
            self.sample_end += sample_offset
            self.sample_loop_start += sample_offset
            self.alt_sample_start += sample_offset
            self.sample_start += sample_offset

            # Return new offset (sample_end + 1 samples, 2 bytes each)
            return (self.sample_end + 1) * 2
        return offset

    @classmethod
    def size(cls) -> int:
        """Return size in bytes."""
        return 32


@dataclass
class KSample:
    """
    Kurzweil Sample object.

    Contains one or more Soundfilehead structures plus envelopes.
    """
    name: str = ""
    hash_val: int = 0

    base_id: int = 1
    num_headers: int = -1  # Actual count is num_headers + 1
    headers_ofs: int = 8
    flags: int = 0  # 0 = mono, 1 = stereo
    ks1: int = 0
    copy_id: int = 0
    ks2: int = 0

    headers: list[Soundfilehead] = field(default_factory=list)
    envelopes: list[Envelope] = field(default_factory=list)

    def set_name(self, name: str) -> None:
        """Set sample name (max 16 characters)."""
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

    def insert_header(self, header: Soundfilehead) -> int:
        """
        Add a soundfile header.

        Args:
            header: Soundfilehead to add

        Returns:
            Number of headers after insertion
        """
        self.headers.append(header)
        self.num_headers += 1
        return self.num_headers + 1

    def generate_envelopes(self) -> None:
        """Generate default envelopes (2 required)."""
        self.envelopes = [Envelope(), Envelope()]

    def is_stereo(self) -> bool:
        """Check if this is a stereo sample."""
        return self.flags == 1

    def get_size(self) -> int:
        """Calculate total object size for writing."""
        # Base size: name + padding + size/ofs fields
        name_len = len(self.name)
        name_padded = name_len + (1 if name_len % 2 == 1 else 2)
        base_size = name_padded + 4  # +4 for hash and size fields

        # Object data: 12 bytes metadata + headers + envelopes
        data_size = 12
        data_size += len(self.headers) * Soundfilehead.size()
        data_size += len(self.envelopes) * Envelope.size()

        return base_size + data_size

    def write(self, f: BinaryIO) -> None:
        """Write sample object to file."""
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

        # Write sample metadata
        self.headers_ofs = 8  # Always 8 for safety
        f.write(struct.pack('>h', self.base_id))
        f.write(struct.pack('>h', self.num_headers))
        f.write(struct.pack('>h', self.headers_ofs))
        f.write(struct.pack('>b', self.flags))
        f.write(struct.pack('>b', self.ks1))
        f.write(struct.pack('>h', self.copy_id))
        f.write(struct.pack('>h', self.ks2))

        # Write headers with envelope offset calculation
        # envofs starts at (num_headers * 32 - 32) and decreases by 32 for each header
        envofs = len(self.headers) * 32 - 32
        for header in self.headers:
            header.offset_to_envelope = envofs + 8
            header.alt_offset_to_envelope = envofs + 6
            header.write(f)
            envofs -= 32

        # Write envelopes
        for envelope in self.envelopes:
            envelope.write(f)

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


def swap_bytes(data: bytes) -> bytes:
    """
    Swap bytes for 16-bit samples (little-endian to big-endian).

    WAV files are little-endian, Kurzweil is big-endian.
    """
    result = bytearray(len(data))
    for i in range(0, len(data), 2):
        if i + 1 < len(data):
            result[i] = data[i + 1]
            result[i + 1] = data[i]
        else:
            result[i] = data[i]
    return bytes(result)


def create_sample_from_wav(wav_data: WavFile, name: str, sample_id: int,
                           root_key: int = 60) -> KSample:
    """
    Create a KSample from parsed WAV data.

    Args:
        wav_data: Parsed WAV file
        name: Sample name (max 16 chars)
        sample_id: Sample ID number (0-999)
        root_key: Root key (MIDI note number, default 60 = C4)

    Returns:
        KSample ready for writing
    """

    ks = KSample()
    ks.set_name(name.lower()[:16])
    ks.set_hash(KHash.generate(sample_id, KHash.T_SAMPLE))

    sample_period = wav_data.get_sample_period_ns()

    # Use root key from WAV smpl chunk if available
    if wav_data.sample_info and wav_data.sample_info.root_key != 60:
        root_key = wav_data.sample_info.root_key

    if wav_data.channels == 1 and wav_data.bits_per_sample == 16:
        # 16-bit mono
        num_samples = len(wav_data.data) // 2
        sh = _create_soundfilehead(wav_data, num_samples, sample_period, root_key)
        sh.sampledata = swap_bytes(wav_data.data)
        _truncate_sampledata(sh)
        ks.insert_header(sh)
        ks.flags = 0  # mono

    elif wav_data.channels == 1 and wav_data.bits_per_sample == 24:
        # 24-bit mono - downconvert to 16-bit by dropping LSB, swap LE→BE
        num_samples = len(wav_data.data) // 3
        sh = _create_soundfilehead(wav_data, num_samples, sample_period, root_key)
        converted = bytearray(num_samples * 2)
        for i in range(num_samples):
            converted[i * 2] = wav_data.data[i * 3 + 2]      # MSB → BE high byte
            converted[i * 2 + 1] = wav_data.data[i * 3 + 1]  # MID → BE low byte
        sh.sampledata = bytes(converted)
        _truncate_sampledata(sh)
        ks.insert_header(sh)
        ks.flags = 0  # mono

    elif wav_data.channels == 2 and wav_data.bits_per_sample == 24:
        # 24-bit stereo - downconvert to 16-bit, split into L/R headers
        num_frames = len(wav_data.data) // 6  # 6 bytes per stereo frame
        sh_left = _create_soundfilehead(wav_data, num_frames, sample_period, root_key)
        sh_right = _create_soundfilehead(wav_data, num_frames, sample_period, root_key)
        left_data = bytearray(num_frames * 2)
        right_data = bytearray(num_frames * 2)
        for i in range(num_frames):
            left_data[i * 2] = wav_data.data[i * 6 + 2]      # L MSB → BE high
            left_data[i * 2 + 1] = wav_data.data[i * 6 + 1]  # L MID → BE low
            right_data[i * 2] = wav_data.data[i * 6 + 5]     # R MSB → BE high
            right_data[i * 2 + 1] = wav_data.data[i * 6 + 4] # R MID → BE low
        sh_left.sampledata = bytes(left_data)
        sh_right.sampledata = bytes(right_data)
        _truncate_sampledata(sh_left)
        _truncate_sampledata(sh_right)
        ks.insert_header(sh_left)
        ks.insert_header(sh_right)
        ks.flags = 1  # stereo

    elif wav_data.channels == 1 and wav_data.bits_per_sample == 8:
        # 8-bit mono - convert to 16-bit
        num_samples = len(wav_data.data)
        sh = _create_soundfilehead(wav_data, num_samples, sample_period, root_key)

        # Convert 8-bit unsigned to 16-bit signed big-endian
        converted = bytearray(num_samples * 2)
        for i in range(num_samples):
            # XOR with 0x80 to convert unsigned to signed
            converted[i * 2] = wav_data.data[i] ^ 0x80
            converted[i * 2 + 1] = 0
        sh.sampledata = bytes(converted)
        _truncate_sampledata(sh)
        ks.insert_header(sh)
        ks.flags = 0  # mono

    elif wav_data.channels == 2 and wav_data.bits_per_sample == 16:
        # 16-bit stereo - split into two headers
        swapped = swap_bytes(wav_data.data)
        data_len = len(swapped) // 2  # Each channel gets half
        num_samples = data_len // 2  # Samples per channel

        # Left channel
        sh_left = _create_soundfilehead(wav_data, num_samples, sample_period, root_key)
        left_data = bytearray(data_len)
        for i in range(0, data_len, 2):
            left_data[i] = swapped[i * 2]
            left_data[i + 1] = swapped[i * 2 + 1]
        sh_left.sampledata = bytes(left_data)
        _truncate_sampledata(sh_left)
        ks.insert_header(sh_left)

        # Right channel
        sh_right = _create_soundfilehead(wav_data, num_samples, sample_period, root_key)
        right_data = bytearray(data_len)
        for i in range(0, data_len, 2):
            right_data[i] = swapped[i * 2 + 2]
            right_data[i + 1] = swapped[i * 2 + 3]
        sh_right.sampledata = bytes(right_data)
        _truncate_sampledata(sh_right)
        ks.insert_header(sh_right)

        ks.flags = 1  # stereo

    ks.generate_envelopes()
    ks.base_id = 1
    ks.copy_id = 0
    ks.ks1 = 0
    ks.ks2 = 0

    return ks


def _truncate_sampledata(sh: Soundfilehead) -> None:
    """Truncate sampledata to match sample_end so written size is exact."""
    if sh.sampledata is not None:
        max_bytes = (sh.sample_end + 1) * 2
        if len(sh.sampledata) > max_bytes:
            sh.sampledata = sh.sampledata[:max_bytes]


def _create_soundfilehead(wav_data: WavFile, num_samples: int,
                          sample_period: int, root_key: int) -> Soundfilehead:
    """Create a Soundfilehead with proper settings."""
    sh = Soundfilehead()
    sh.sample_period = sample_period
    sh.set_root_key(root_key)

    sh.sample_start = 0
    sh.alt_sample_start = 0

    # Check for loop info from WAV smpl chunk
    if wav_data.sample_info and wav_data.sample_info.is_looped:
        loop_end = min(wav_data.sample_info.loop_end, num_samples - 1)
        loop_start = min(wav_data.sample_info.loop_start, loop_end)
        if loop_start >= loop_end:
            # Degenerate loop region -- treat as unlooped
            sh.flags = 0xF0
            sh.sample_end = num_samples - 1
            sh.sample_loop_start = sh.sample_end
        else:
            sh.flags = 0x70  # Loop on, needs load, RAM based
            sh.sample_end = loop_end
            sh.sample_loop_start = loop_start
    else:
        sh.flags = 0xF0  # Loop off (0x80), needs load, RAM based
        sh.sample_end = num_samples - 1
        sh.sample_loop_start = sh.sample_end

    return sh
