"""Kurzweil .krz file header."""

import struct
from dataclasses import dataclass, field
from typing import BinaryIO


@dataclass
class KrzHeader:
    """
    32-byte Kurzweil file header.

    Format (big-endian):
    - 4 bytes: magic "PRAM"
    - 4 bytes: osize (offset to sample data)
    - 24 bytes: rest[6] (metadata, rest[2] = software version)
    """
    magic: bytes = field(default=b'PRAM')
    osize: int = 0
    rest: list[int] = field(default_factory=lambda: [0, 0, 353, 0, 0, 0])

    SOFTWARE_VERSION = 353

    def __post_init__(self):
        """Ensure rest has 6 elements with software version set."""
        if len(self.rest) < 6:
            self.rest = [0, 0, self.SOFTWARE_VERSION, 0, 0, 0]
        else:
            self.rest[2] = self.SOFTWARE_VERSION

    def write(self, f: BinaryIO) -> None:
        """
        Write header to file.

        Args:
            f: Binary file opened for writing
        """
        f.write(self.magic)
        f.write(struct.pack('>I', self.osize))
        for val in self.rest:
            f.write(struct.pack('>I', val))

    @classmethod
    def read(cls, f: BinaryIO) -> 'KrzHeader':
        """
        Read header from file.

        Args:
            f: Binary file opened for reading

        Returns:
            Parsed KrzHeader
        """
        magic = f.read(4)
        osize = struct.unpack('>I', f.read(4))[0]
        rest = [struct.unpack('>I', f.read(4))[0] for _ in range(6)]
        return cls(magic=magic, osize=osize, rest=rest)

    def is_valid(self) -> bool:
        """Check if header has valid magic bytes."""
        return self.magic == b'PRAM'
