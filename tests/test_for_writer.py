"""Tests for .for (Forte/PC3) output format."""

import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wav2krz.converter import (
    convert_wavs_to_krz, convert_from_list_file, ConversionMode, WavEntry,
)
from wav2krz.krz.for_templates import (
    PROGRAM_1LAYER, PROGRAM_2LAYER, patch_program_template,
    build_program_data,
    LAYER_COUNT_OFFSET, LAYER_BLOCK_START, LAYER_BLOCK_SIZE,
    L1_LYR_LOKEY_OFFSET, L1_LYR_HIKEY_OFFSET,
    L1_CAL_KMID_OFFSET, L1_CAL_KMID2_OFFSET,
    L2_LYR_LOKEY_OFFSET, L2_LYR_HIKEY_OFFSET,
    L2_CAL_KMID_OFFSET, L2_CAL_KMID2_OFFSET,
    _BLK_LOKEY, _BLK_HIKEY, _BLK_CAL_KMID, _BLK_CAL_KMID2,
    _BLK_LAYER_IDX, _BLK_FINAL_MARKER,
)
from .helpers import make_wav


def parse_for_file(data: bytes) -> dict:
    """Minimal .for parser for test verification."""
    magic = data[:4]
    osize = struct.unpack('>I', data[8:12])[0]
    outer_block_size = struct.unpack('>i', data[32:36])[0]

    objects = []
    pos = 36
    while pos + 20 <= len(data) and pos < osize:
        field0 = struct.unpack('>I', data[pos:pos+4])[0]
        if field0 not in (138, 133, 170):
            break  # null terminator or invalid object
        obj_id = struct.unpack('>I', data[pos+4:pos+8])[0]
        field3 = struct.unpack('>I', data[pos+12:pos+16])[0]
        if field3 == 0:
            break

        # Read name
        name_start = pos + 20
        end = name_start
        while end < len(data) and data[end] != 0:
            end += 1
        name = data[name_start:end].decode('latin-1')

        # Padded name end
        name_with_null_len = end - name_start + 1
        padded_end = name_start + ((name_with_null_len + 3) & ~3)

        obj_data = data[padded_end:pos + field3]
        objects.append({
            'field0': field0, 'id': obj_id, 'name': name,
            'data': obj_data, 'total_size': field3,
        })
        pos += field3

    return {
        'magic': magic, 'osize': osize,
        'outer_block_size': outer_block_size,
        'objects': objects,
        'sample_data': data[osize:] if osize < len(data) else b'',
    }


class TestForTemplates(unittest.TestCase):
    """Tests for program template integrity and patching."""

    def test_1layer_template_size(self):
        self.assertEqual(len(PROGRAM_1LAYER), 1380)

    def test_2layer_template_size(self):
        self.assertEqual(len(PROGRAM_2LAYER), 1696)

    def test_1layer_template_signatures(self):
        self.assertEqual(PROGRAM_1LAYER[LAYER_COUNT_OFFSET], 1)
        self.assertEqual(PROGRAM_1LAYER[0xE5], 0x09)  # LYR tag
        self.assertEqual(PROGRAM_1LAYER[0x167], 0x40)  # CAL tag

    def test_2layer_template_signatures(self):
        self.assertEqual(PROGRAM_2LAYER[LAYER_COUNT_OFFSET], 2)
        self.assertEqual(PROGRAM_2LAYER[0xE5], 0x09)   # L1 LYR tag
        self.assertEqual(PROGRAM_2LAYER[0x167], 0x40)   # L1 CAL tag
        self.assertEqual(PROGRAM_2LAYER[0x223], 0x09)   # L2 LYR tag
        self.assertEqual(PROGRAM_2LAYER[0x2A5], 0x40)   # L2 CAL tag

    def test_patch_1layer(self):
        patched = patch_program_template(
            PROGRAM_1LAYER, layer_count=1,
            keymap_ids=[2048], key_ranges=[(24, 96)])
        self.assertEqual(patched[LAYER_COUNT_OFFSET], 1)
        self.assertEqual(patched[L1_LYR_LOKEY_OFFSET], 24)
        self.assertEqual(patched[L1_LYR_HIKEY_OFFSET], 96)
        self.assertEqual(struct.unpack('>I', patched[L1_CAL_KMID_OFFSET:L1_CAL_KMID_OFFSET+4])[0], 2048)
        self.assertEqual(struct.unpack('>I', patched[L1_CAL_KMID2_OFFSET:L1_CAL_KMID2_OFFSET+4])[0], 2048)

    def test_patch_2layer(self):
        patched = patch_program_template(
            PROGRAM_2LAYER, layer_count=2,
            keymap_ids=[1024, 1025], key_ranges=[(0, 60), (61, 127)])
        self.assertEqual(patched[LAYER_COUNT_OFFSET], 2)
        self.assertEqual(patched[L1_LYR_LOKEY_OFFSET], 0)
        self.assertEqual(patched[L1_LYR_HIKEY_OFFSET], 60)
        self.assertEqual(struct.unpack('>I', patched[L1_CAL_KMID_OFFSET:L1_CAL_KMID_OFFSET+4])[0], 1024)
        self.assertEqual(patched[L2_LYR_LOKEY_OFFSET], 61)
        self.assertEqual(patched[L2_LYR_HIKEY_OFFSET], 127)
        self.assertEqual(struct.unpack('>I', patched[L2_CAL_KMID_OFFSET:L2_CAL_KMID_OFFSET+4])[0], 1025)

    def test_build_3layer(self):
        data = build_program_data(
            3, keymap_ids=[1024, 1025, 1026],
            key_ranges=[(0, 63), (64, 67), (68, 127)])
        self.assertEqual(data[LAYER_COUNT_OFFSET], 3)
        # Check expected size: prefix(231) + 3*block(318) + suffix(829)
        expected = 231 + 3 * 318 + 829
        self.assertEqual(len(data), expected)
        # Verify each layer's key range
        for i, (lo, hi) in enumerate([(0, 63), (64, 67), (68, 127)]):
            blk = LAYER_BLOCK_START + i * LAYER_BLOCK_SIZE
            self.assertEqual(data[blk + _BLK_LOKEY], lo)
            self.assertEqual(data[blk + _BLK_HIKEY], hi)

    def test_build_3layer_keymap_ids(self):
        data = build_program_data(
            3, keymap_ids=[1024, 1025, 1026],
            key_ranges=[(0, 63), (64, 67), (68, 127)])
        for i, km_id in enumerate([1024, 1025, 1026]):
            blk = LAYER_BLOCK_START + i * LAYER_BLOCK_SIZE
            actual = struct.unpack('>I', data[blk + _BLK_CAL_KMID:blk + _BLK_CAL_KMID + 4])[0]
            self.assertEqual(actual, km_id)

    def test_build_3layer_layer_indices(self):
        data = build_program_data(
            3, keymap_ids=[1024, 1025, 1026],
            key_ranges=[(0, 63), (64, 67), (68, 127)])
        for i in range(3):
            blk = LAYER_BLOCK_START + i * LAYER_BLOCK_SIZE
            self.assertEqual(data[blk + _BLK_LAYER_IDX], i)

    def test_build_3layer_final_markers(self):
        data = build_program_data(
            3, keymap_ids=[1024, 1025, 1026],
            key_ranges=[(0, 63), (64, 67), (68, 127)])
        for i in range(3):
            blk = LAYER_BLOCK_START + i * LAYER_BLOCK_SIZE
            marker = data[blk + _BLK_FINAL_MARKER:blk + _BLK_FINAL_MARKER + 2]
            if i == 2:  # last layer
                self.assertEqual(marker, b'\x00\x01')
            else:
                self.assertEqual(marker, b'\x09\x00')

    def test_build_1layer_matches_template(self):
        data = build_program_data(
            1, keymap_ids=[1024], key_ranges=[(0, 127)])
        patched = patch_program_template(
            PROGRAM_1LAYER, 1, [1024], [(0, 127)])
        self.assertEqual(data, patched)

    def test_build_2layer_matches_template(self):
        data = build_program_data(
            2, keymap_ids=[1024, 1025], key_ranges=[(0, 60), (61, 127)])
        patched = patch_program_template(
            PROGRAM_2LAYER, 2, [1024, 1025], [(0, 60), (61, 127)])
        self.assertEqual(data, patched)


class TestForWriterFileStructure(unittest.TestCase):
    """Tests for .for file structural correctness."""

    def test_magic_and_osize(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            self.assertEqual(parsed['magic'], b'COOL')
            self.assertGreater(parsed['osize'], 0)

    def test_outer_block_size(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            # outer_block_size should be negative, abs = osize - 36
            self.assertEqual(abs(parsed['outer_block_size']),
                             parsed['osize'] - 36)

    def test_osize_matches_end_of_objects(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            data = out.read_bytes()
            parsed = parse_for_file(data)
            # Sum of all object sizes + 36 header + 4 terminator should equal osize
            total = 36 + sum(o['total_size'] for o in parsed['objects']) + 4
            self.assertEqual(total, parsed['osize'])


class TestForWriterSample(unittest.TestCase):
    """Tests for .for sample object serialization."""

    def test_mono_sample_size(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            samples = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(samples[0]['data']), 120)

    def test_mono_envelope_offset(self):
        """Verify envofs = 16 for mono sample (distance from envofs field to envelope)."""
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            sd = [o for o in parsed['objects'] if o['field0'] == 170][0]['data']
            # envofs at SFH offset 40 = data offset 56
            envofs = struct.unpack('>h', sd[56:58])[0]
            alt_envofs = struct.unpack('>h', sd[58:60])[0]
            self.assertEqual(envofs, 16)
            self.assertEqual(alt_envofs, 14)

    def test_stereo_envelope_offsets(self):
        """Verify envofs = 72/16 for stereo (two SFH+extra blocks)."""
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav', channels=2)
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            sd = [o for o in parsed['objects'] if o['field0'] == 170][0]['data']
            # SFH1 envofs at data[56]
            env1 = struct.unpack('>h', sd[56:58])[0]
            self.assertEqual(env1, 72)
            # SFH2 envofs at data[72+40=112]
            env2 = struct.unpack('>h', sd[112:114])[0]
            self.assertEqual(env2, 16)

    def test_stereo_sample_size(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav', channels=2)
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            samples = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(samples), 1)
            self.assertEqual(len(samples[0]['data']), 176)

    def test_sample_offsets_are_doubled(self):
        """Verify sample offsets are in bytes (2x .krz sample units)."""
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav', duration=0.1)
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            sd = [o for o in parsed['objects'] if o['field0'] == 170][0]['data']
            # SFH starts at offset 16, sample_end at +32 (8 bytes)
            sample_end = struct.unpack('>q', sd[48:56])[0]
            # sample_end should be even (byte offset = sample_count * 2)
            self.assertEqual(sample_end % 2, 0)
            self.assertGreater(sample_end, 0)

    def test_sample_metadata(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            sd = [o for o in parsed['objects'] if o['field0'] == 170][0]['data']
            base_id = struct.unpack('>I', sd[0:4])[0]
            stereo = struct.unpack('>H', sd[6:8])[0]
            headers_ofs = struct.unpack('>H', sd[8:10])[0]
            self.assertEqual(base_id, 1)
            self.assertEqual(stereo, 0)
            self.assertEqual(headers_ofs, 8)

    def test_stereo_flag(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav', channels=2)
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            sd = [o for o in parsed['objects'] if o['field0'] == 170][0]['data']
            stereo = struct.unpack('>H', sd[6:8])[0]
            self.assertEqual(stereo, 1)

    def test_sample_audio_data_preserved(self):
        """Verify sample audio data section is byte-for-byte correct."""
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav', duration=0.01)
            out_for = Path(tmpdir) / 'test.for'
            out_krz = Path(tmpdir) / 'test.krz'
            convert_wavs_to_krz([wav], out_for, mode=ConversionMode.SAMPLES)
            convert_wavs_to_krz([wav], out_krz, mode=ConversionMode.SAMPLES)

            for_data = out_for.read_bytes()
            krz_data = out_krz.read_bytes()

            for_osize = struct.unpack('>I', for_data[8:12])[0]
            krz_osize = struct.unpack('>I', krz_data[4:8])[0]

            for_samples = for_data[for_osize:]
            krz_samples = krz_data[krz_osize:]

            self.assertEqual(for_samples, krz_samples)


class TestForWriterKeymap(unittest.TestCase):
    """Tests for .for keymap object serialization."""

    def test_keymap_prefix(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            km = [o for o in parsed['objects'] if o['field0'] == 133][0]
            # Prefix should be [0x04][sample_index]
            self.assertEqual(km['data'][0], 0x04)
            self.assertEqual(km['data'][1], 0)  # Only one sample, index 0

    def test_multi_sample_keymap_prefix(self):
        with TemporaryDirectory() as tmpdir:
            wavs = [
                make_wav(Path(tmpdir) / 'a.wav', root_key=48),
                make_wav(Path(tmpdir) / 'b.wav', root_key=60),
                make_wav(Path(tmpdir) / 'c.wav', root_key=72),
            ]
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz(wavs, out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            km = [o for o in parsed['objects'] if o['field0'] == 133][0]
            # Max sample index should be 2 (three samples: 0, 1, 2)
            self.assertEqual(km['data'][0], 0x04)
            self.assertEqual(km['data'][1], 2)

    def test_keymap_entry_format(self):
        """Verify keymap entries use 0x04+index for sample references."""
        with TemporaryDirectory() as tmpdir:
            wavs = [
                make_wav(Path(tmpdir) / 'lo.wav', root_key=48),
                make_wav(Path(tmpdir) / 'hi.wav', root_key=72),
            ]
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz(wavs, out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            km = [o for o in parsed['objects'] if o['field0'] == 133][0]
            kd = km['data']
            method = struct.unpack('>h', kd[2:4])[0]
            entries_per_vel = struct.unpack('>h', kd[8:10])[0]
            entry_size = struct.unpack('>h', kd[10:12])[0]

            # method=0x03: sample_ref(2) + subsample(1) = 3 bytes
            self.assertEqual(method, 0x03)
            self.assertEqual(entry_size, 3)

            # Check entries: all should have 0x04 as first byte of sample ref
            entry_start = 28
            for k in range(entries_per_vel + 1):
                off = entry_start + k * entry_size
                sample_marker = kd[off]
                self.assertEqual(sample_marker, 0x04,
                                 f"Key {k}: expected 0x04 marker, got 0x{sample_marker:02x}")


class TestForWriterProgram(unittest.TestCase):
    """Tests for .for program object serialization."""

    def test_single_layer_program(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            progs = [o for o in parsed['objects'] if o['field0'] == 138]
            self.assertEqual(len(progs), 1)
            pd = progs[0]['data']
            self.assertEqual(len(pd), 1380)
            self.assertEqual(pd[LAYER_COUNT_OFFSET], 1)

    def test_program_keymap_reference(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            pd = [o for o in parsed['objects'] if o['field0'] == 138][0]['data']
            km_id = struct.unpack('>I', pd[L1_CAL_KMID_OFFSET:L1_CAL_KMID_OFFSET+4])[0]
            km_id2 = struct.unpack('>I', pd[L1_CAL_KMID2_OFFSET:L1_CAL_KMID2_OFFSET+4])[0]
            # Both should reference keymap 1024
            self.assertEqual(km_id, 1024)
            self.assertEqual(km_id2, 1024)


class TestForWriterEndToEnd(unittest.TestCase):
    """End-to-end tests for .for output."""

    def test_samples_only_mode(self):
        with TemporaryDirectory() as tmpdir:
            wavs = [
                make_wav(Path(tmpdir) / 'a.wav'),
                make_wav(Path(tmpdir) / 'b.wav'),
            ]
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz(wavs, out, mode=ConversionMode.SAMPLES)
            parsed = parse_for_file(out.read_bytes())
            self.assertEqual(parsed['magic'], b'COOL')
            # Samples only: no programs or keymaps
            progs = [o for o in parsed['objects'] if o['field0'] == 138]
            kms = [o for o in parsed['objects'] if o['field0'] == 133]
            samps = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(progs), 0)
            self.assertEqual(len(kms), 0)
            self.assertEqual(len(samps), 2)

    def test_instrument_mode(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            progs = [o for o in parsed['objects'] if o['field0'] == 138]
            kms = [o for o in parsed['objects'] if o['field0'] == 133]
            samps = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(progs), 1)
            self.assertEqual(len(kms), 1)
            self.assertEqual(len(samps), 1)

    def test_drumset_mode(self):
        with TemporaryDirectory() as tmpdir:
            wavs = [
                make_wav(Path(tmpdir) / 'kick.wav'),
                make_wav(Path(tmpdir) / 'snare.wav'),
                make_wav(Path(tmpdir) / 'hat.wav'),
            ]
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz(wavs, out, mode=ConversionMode.DRUMSET)
            parsed = parse_for_file(out.read_bytes())
            progs = [o for o in parsed['objects'] if o['field0'] == 138]
            kms = [o for o in parsed['objects'] if o['field0'] == 133]
            samps = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(progs), 1)
            self.assertEqual(len(kms), 1)
            self.assertEqual(len(samps), 3)

    def test_drumset_multi_mode(self):
        with TemporaryDirectory() as tmpdir:
            wavs = [
                make_wav(Path(tmpdir) / 'kick.wav'),
                make_wav(Path(tmpdir) / 'snare.wav'),
            ]
            entries = [
                WavEntry(path=wavs[0], root_key=36, lo_key=34, hi_key=36),
                WavEntry(path=wavs[1], root_key=38, lo_key=37, hi_key=39),
            ]
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz(
                wavs, out, mode=ConversionMode.DRUMSET_MULTI,
                root_keys=[36, 38], key_ranges=[(34, 36), (37, 39)],
                entries=entries)
            parsed = parse_for_file(out.read_bytes())
            progs = [o for o in parsed['objects'] if o['field0'] == 138]
            kms = [o for o in parsed['objects'] if o['field0'] == 133]
            samps = [o for o in parsed['objects'] if o['field0'] == 170]
            self.assertEqual(len(progs), 1)
            self.assertEqual(len(kms), 2)
            self.assertEqual(len(samps), 2)
            # Program should have 2 layers
            pd = progs[0]['data']
            self.assertEqual(pd[LAYER_COUNT_OFFSET], 2)

    def test_object_ids_start_at_1024(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            for obj in parsed['objects']:
                self.assertGreaterEqual(obj['id'], 1024)

    def test_object_names_preserved(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'mysample.wav')
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT,
                                name='My Inst')
            parsed = parse_for_file(out.read_bytes())
            prog = [o for o in parsed['objects'] if o['field0'] == 138][0]
            km = [o for o in parsed['objects'] if o['field0'] == 133][0]
            samp = [o for o in parsed['objects'] if o['field0'] == 170][0]
            self.assertEqual(prog['name'], 'my inst')
            self.assertEqual(km['name'], 'my inst')
            self.assertEqual(samp['name'], 'mysample')

    def test_list_file_for_output(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            listfile = Path(tmpdir) / 'list.txt'
            listfile.write_text(f'{wav}\n')
            out = Path(tmpdir) / 'output.for'
            convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            self.assertEqual(parsed['magic'], b'COOL')
            self.assertEqual(len(parsed['objects']), 3)

    def test_stereo_instrument(self):
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'stereo.wav', channels=2)
            out = Path(tmpdir) / 'test.for'
            convert_wavs_to_krz([wav], out, mode=ConversionMode.INSTRUMENT)
            parsed = parse_for_file(out.read_bytes())
            samp = [o for o in parsed['objects'] if o['field0'] == 170][0]
            stereo = struct.unpack('>H', samp['data'][6:8])[0]
            self.assertEqual(stereo, 1)
            self.assertEqual(len(samp['data']), 176)


class TestForWriterCLI(unittest.TestCase):
    """Tests for CLI .for extension handling."""

    def test_cli_accepts_for_extension(self):
        from wav2krz.cli import main
        with TemporaryDirectory() as tmpdir:
            wav = make_wav(Path(tmpdir) / 'test.wav')
            listfile = Path(tmpdir) / 'list.txt'
            listfile.write_text(f'{wav}\n')
            out = Path(tmpdir) / 'output.for'
            ret = main([str(listfile), str(out), '--mode', 'instrument'])
            self.assertEqual(ret, 0)
            self.assertTrue(out.exists())
            data = out.read_bytes()
            self.assertEqual(data[:4], b'COOL')


if __name__ == '__main__':
    unittest.main()
