"""Kurzweil KProgram structure."""

import struct
from dataclasses import dataclass, field
from typing import BinaryIO, List

from .hash import KHash
from .keymap import KKeymap


@dataclass
class Segment:
    """
    Program segment with tag and data.

    Different tags have different data sizes.
    """
    # Segment tag constants
    PGMSEGTAG = 8
    LYRSEGTAG = 9
    FXSEGTAG = 15
    ASRSEGTAG = 16
    LFOSEGTAG = 20
    FUNSEGTAG = 24
    ENCSEGTAG = 32
    ENVSEGTAG = 33
    IMPSEGTAG = 39
    CALSEGTAG = 64
    HOBSEGTAG = 80
    KDFXSEGTAG = 104
    KB3SEGTAG = 120

    tag: int = 0
    data: bytearray = field(default_factory=bytearray)

    def __init__(self, tag: int):
        """Create segment with proper data size for tag."""
        self.tag = tag
        self.data = bytearray(self._get_length(tag))

    @staticmethod
    def _get_length(tag: int) -> int:
        """Get data length for a segment tag."""
        if tag == Segment.PGMSEGTAG:
            return 15
        if tag == Segment.LYRSEGTAG:
            return 15
        if tag == Segment.FXSEGTAG:
            return 7

        tag_base = tag & 0xF8
        if tag_base == Segment.FUNSEGTAG:
            return 3
        if tag_base in (Segment.ASRSEGTAG, Segment.LFOSEGTAG, Segment.KDFXSEGTAG):
            return 7
        if tag_base in (Segment.ENCSEGTAG, Segment.HOBSEGTAG):
            return 15
        if tag_base in (Segment.CALSEGTAG, Segment.KB3SEGTAG):
            return 31

        return 0

    def write(self, f: BinaryIO) -> None:
        """Write segment to file."""
        f.write(struct.pack('>b', self.tag))
        f.write(self.data)


@dataclass
class KProgram:
    """
    Kurzweil Program object.

    Contains layers that reference keymaps.
    """
    name: str = ""
    hash_val: int = 0
    segments: List[Segment] = field(default_factory=list)

    def set_name(self, name: str) -> None:
        """Set program name (max 16 characters)."""
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

    def make_pgm_block(self, mode: int = 2) -> None:
        """Create the main program segment.

        Args:
            mode: Program mode (2=K2000, 3=K2500, 4=K2600)
        """
        s = Segment(Segment.PGMSEGTAG)
        s.data[0] = mode
        s.data[1] = 0  # numLayers
        s.data[3] = 0x37  # Bend range
        s.data[4] = 64  # Portamento
        self.segments.append(s)

    def add_layer(self, keymap: KKeymap, stereo: bool = False) -> None:
        """
        Add a layer referencing a keymap.

        Args:
            keymap: KKeymap to reference
            stereo: Whether the samples are stereo
        """
        # Get keymap ID
        keymap_id = KHash.get_id(keymap.get_hash())

        if stereo:
            # Layer segment for stereo
            s = Segment(Segment.LYRSEGTAG)
            s.data[0] = 0
            s.data[1] = 0x18
            s.data[2] = 0
            s.data[3] = 0  # Lower bound
            s.data[4] = 0x7F  # Upper bound
            s.data[5] = 0
            s.data[6] = 0x7F
            s.data[7] = 0
            s.data[8] = 0x24  # Enable: Normal Stereo = 0x20
            s.data[9] = 0
            s.data[10] = 0
            s.data[11] = 0
            s.data[12] = 0
            s.data[13] = 0
            s.data[14] = 0
            self.segments.append(s)

            # ENC segment
            s = Segment(Segment.ENCSEGTAG)
            # All zeros for default
            self.segments.append(s)

            # ENV segment (Amp Envelope)
            s = Segment(Segment.ENVSEGTAG)
            s.data[0] = 0
            s.data[1] = 100
            s.data[7] = 100
            self.segments.append(s)

            # CAL segment (references keymap)
            s = Segment(Segment.CALSEGTAG)
            s.data[0] = 0x7F
            s.data[1] = 0  # Keymap transpose
            s.data[3] = 0x2B
            s.data[29] = 1
            s.data[7] = (keymap_id >> 8) & 0xFF
            s.data[8] = keymap_id & 0xFF
            s.data[11] = (keymap_id >> 8) & 0xFF
            s.data[12] = keymap_id & 0xFF
            self.segments.append(s)

            # HOB segments
            s = Segment(0x50)
            s.data[0] = 62
            self.segments.append(s)

            s = Segment(0x51)
            s.data[0] = 60
            self.segments.append(s)

            s = Segment(0x52)
            s.data[0] = 60
            self.segments.append(s)

            s = Segment(0x53)
            s.data[0] = 1
            s.data[2] = 0x70
            s.data[13] = 4
            s.data[14] = 0x90  # Panning Fixed
            self.segments.append(s)
        else:
            # Layer segment for mono
            s = Segment(Segment.LYRSEGTAG)
            s.data[0] = 0
            s.data[1] = 0x18
            s.data[2] = 0
            s.data[3] = 0  # Lower bound
            s.data[4] = 0x7F  # Upper bound
            s.data[5] = 0
            s.data[6] = 0x7F
            s.data[7] = 0
            s.data[8] = 0x04  # Enable: Mono
            s.data[9] = 0
            s.data[10] = 0
            s.data[11] = 0
            s.data[12] = 0
            s.data[13] = 0
            s.data[14] = 0
            self.segments.append(s)

            # ENC segment
            s = Segment(Segment.ENCSEGTAG)
            self.segments.append(s)

            # ENV segment (Amp Envelope)
            s = Segment(Segment.ENVSEGTAG)
            s.data[0] = 0
            s.data[1] = 100
            s.data[7] = 100
            self.segments.append(s)

            # CAL segment (references keymap)
            s = Segment(Segment.CALSEGTAG)
            s.data[0] = 0x7F
            s.data[1] = 0  # Keymap transpose
            s.data[3] = 0x2B
            s.data[29] = 1
            s.data[7] = (keymap_id >> 8) & 0xFF
            s.data[8] = keymap_id & 0xFF
            s.data[11] = (keymap_id >> 8) & 0xFF
            s.data[12] = keymap_id & 0xFF
            self.segments.append(s)

            # HOB segments
            s = Segment(0x50)
            s.data[0] = 62
            self.segments.append(s)

            s = Segment(0x51)
            s.data[0] = 60
            self.segments.append(s)

            s = Segment(0x52)
            s.data[0] = 60
            self.segments.append(s)

            s = Segment(0x53)
            s.data[0] = 1
            s.data[2] = 0x70
            s.data[13] = 4
            s.data[14] = 0x00  # Panning center
            self.segments.append(s)

        # Increment layer count in PGM segment
        for seg in self.segments:
            if seg.tag == Segment.PGMSEGTAG:
                seg.data[1] += 1
                break

    def get_size(self) -> int:
        """Calculate total object size for writing."""
        # Base size: name + padding + size/ofs fields
        name_len = len(self.name)
        name_padded = name_len + (1 if name_len % 2 == 1 else 2)
        base_size = name_padded + 4

        # Segment data
        data_size = sum(len(seg.data) + 1 for seg in self.segments)
        data_size += 2  # Trailing null word

        return base_size + data_size

    def write(self, f: BinaryIO) -> None:
        """Write program object to file."""
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

        # Write segments
        for seg in self.segments:
            seg.write(f)

        # Write trailing null word
        f.write(struct.pack('>H', 0))

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


def create_program(keymap: KKeymap, program_id: int, name: str,
                   stereo: bool = False, mode: int = 2) -> KProgram:
    """
    Create a simple program with one layer.

    Args:
        keymap: KKeymap to reference
        program_id: Program ID number
        name: Program name
        stereo: Whether the samples are stereo
        mode: Program mode (2=K2000, 3=K2500, 4=K2600)

    Returns:
        KProgram ready for writing
    """
    prog = KProgram()
    prog.set_name(name.lower()[:16])
    prog.set_hash(KHash.generate(program_id, KHash.T_PROGRAM))

    prog.make_pgm_block(mode)
    prog.add_layer(keymap, stereo)

    return prog
