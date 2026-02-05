"""Generate a test file and dump its structure."""

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..converter import convert_wavs_to_krz, ConversionMode
from .helpers import make_wav


def dump_file(filepath):
    """Dump the structure of a krz file."""
    result = []
    with open(filepath, 'rb') as f:
        magic = f.read(4)
        osize = struct.unpack('>I', f.read(4))[0]
        result.append(f'Magic: {magic}')
        result.append(f'osize (sample data offset): {osize}')
        result.append(f'File size: {Path(filepath).stat().st_size}')
        f.seek(32)  # Skip rest of header

        obj_num = 0
        while True:
            pos = f.tell()
            if pos >= osize:
                result.append(f'Reached sample data at {pos}')
                break
            block_size = struct.unpack('>i', f.read(4))[0]
            if block_size == 0:
                result.append(f'Terminator at {pos}')
                break

            hash_val = struct.unpack('>H', f.read(2))[0]
            obj_type = hash_val >> 10
            obj_id = hash_val & 0x3FF

            type_names = {36: 'PROGRAM', 37: 'KEYMAP', 38: 'SAMPLE'}
            type_name = type_names.get(obj_type, f'TYPE_{obj_type}')

            # Read name (2-byte size + chars)
            name_size = struct.unpack('>H', f.read(2))[0]
            name = f.read(name_size).decode('latin-1', errors='replace')

            result.append(f'Object {obj_num}: {type_name} id={obj_id} hash={hash_val} name="{name}" pos={pos} block_size={block_size}')

            # Seek to next block
            f.seek(pos - block_size)
            obj_num += 1
    return result


class TestDumpFile(unittest.TestCase):
    def test_dump_all_formats(self):
        with TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            wav = tmpdir / 'test.wav'
            make_wav(wav)

            for ext in ['.krz', '.k25', '.k26']:
                out = tmpdir / f'test{ext}'
                convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
                print(f'\n=== {ext} ===')
                for line in dump_file(out):
                    print(line)
