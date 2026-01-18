"""Kurzweil .krz file writer."""

import struct
from pathlib import Path
from typing import BinaryIO, List, Union

from .header import KrzHeader
from .sample import KSample
from .keymap import KKeymap
from .program import KProgram
from .hash import KHash


class KrzWriter:
    """
    Writer for Kurzweil .krz files.

    Handles the complete file format including:
    - 32-byte header
    - Object blocks (negative size prefix, 4-byte aligned)
    - Sample data section
    """

    def __init__(self):
        self.samples: List[KSample] = []
        self.keymaps: List[KKeymap] = []
        self.programs: List[KProgram] = []

    def add_sample(self, sample: KSample) -> None:
        """Add a sample to the file."""
        self.samples.append(sample)

    def add_keymap(self, keymap: KKeymap) -> None:
        """Add a keymap to the file."""
        self.keymaps.append(keymap)

    def add_program(self, program: KProgram) -> None:
        """Add a program to the file."""
        self.programs.append(program)

    def _get_all_objects(self) -> List[Union[KSample, KKeymap, KProgram]]:
        """
        Get all objects sorted by hash for writing.

        Order: Samples, Keymaps, Programs (by hash within each type).
        """
        objects = []
        objects.extend(sorted(self.samples, key=lambda x: x.get_hash()))
        objects.extend(sorted(self.keymaps, key=lambda x: x.get_hash()))
        objects.extend(sorted(self.programs, key=lambda x: x.get_hash()))
        return objects

    def _prewrite_samples(self) -> None:
        """Prepare sample data offsets before writing."""
        offset = 0
        for sample in self.samples:
            for header in sample.headers:
                offset = header.prewrite(offset)

    def _write_objects(self, f: BinaryIO) -> None:
        """
        Write all object blocks.

        Each object is written as:
        - 4-byte negative block size (distance to next block)
        - Object data
        - Padding to 4-byte boundary
        """
        objects = self._get_all_objects()

        if not objects:
            # Empty file - just write terminator
            f.write(struct.pack('>i', 0))
            return

        for obj in objects:
            block_start = f.tell()

            # Placeholder for block size
            f.write(struct.pack('>i', 0))

            # Write object
            obj.write(f)

            # Pad to 4-byte boundary
            current_pos = f.tell()
            padding = (4 - (current_pos % 4)) % 4
            if padding:
                f.write(b'\x00' * padding)

            # Go back and write negative block size
            block_end = f.tell()
            block_size = block_start - block_end
            f.seek(block_start)
            f.write(struct.pack('>i', block_size))
            f.seek(block_end)

        # Write terminator (zero)
        f.write(struct.pack('>i', 0))

    def _write_sample_data(self, f: BinaryIO) -> None:
        """Write sample audio data section."""
        for sample in self.samples:
            for header in sample.headers:
                header.write_sampledata(f)

    def write(self, filepath: Path | str) -> None:
        """
        Write the complete .krz file.

        Args:
            filepath: Output file path
        """
        filepath = Path(filepath)

        # Prepare sample offsets
        self._prewrite_samples()

        with open(filepath, 'wb') as f:
            # Write placeholder header
            header = KrzHeader()
            header.write(f)

            # Write objects
            self._write_objects(f)

            # Record osize (offset to sample data)
            osize = f.tell()

            # Write sample data
            self._write_sample_data(f)

            # Go back and update header with correct osize
            f.seek(4)  # After magic bytes
            f.write(struct.pack('>I', osize))
