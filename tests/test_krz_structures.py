"""Tests for KRZ data structures: hash, header, sample, envelope, program."""

import io
import struct
import unittest

from ..krz.hash import KHash
from ..krz.header import KrzHeader
from ..krz.sample import (
    KSample, Soundfilehead, Envelope, swap_bytes, create_sample_from_wav
)
from ..krz.keymap import create_instrument_keymap, KKeymap
from ..krz.program import (
    KProgram, Segment, create_program, create_multi_layer_program
)
from ..wav.parser import WavFile, SampleInfo


class TestKHash(unittest.TestCase):
    def test_generate_sample(self):
        h = KHash.generate(200, KHash.T_SAMPLE)
        # (38 << 10) + 200 = 39112
        self.assertEqual(h, 38 * 1024 + 200)

    def test_generate_keymap(self):
        h = KHash.generate(100, KHash.T_KEYMAP)
        self.assertEqual(h, 37 * 1024 + 100)

    def test_generate_program(self):
        h = KHash.generate(50, KHash.T_PROGRAM)
        self.assertEqual(h, 36 * 1024 + 50)

    def test_get_type(self):
        h = KHash.generate(200, KHash.T_SAMPLE)
        self.assertEqual(KHash.get_type(h), KHash.T_SAMPLE)

    def test_get_id(self):
        h = KHash.generate(200, KHash.T_SAMPLE)
        self.assertEqual(KHash.get_id(h), 200)

    def test_get_id_roundtrip(self):
        for t in (KHash.T_SAMPLE, KHash.T_KEYMAP, KHash.T_PROGRAM):
            for obj_id in (0, 1, 100, 200, 999):
                h = KHash.generate(obj_id, t)
                self.assertEqual(KHash.get_id(h), obj_id,
                                 f"Failed for type={t}, id={obj_id}")

    def test_min_max_sample_ids(self):
        # ID 0 and ID 1023 are the min/max for 10-bit IDs
        self.assertEqual(KHash.get_id(KHash.generate(0, KHash.T_SAMPLE)), 0)
        self.assertEqual(KHash.get_id(KHash.generate(1023, KHash.T_SAMPLE)), 1023)


class TestKrzHeader(unittest.TestCase):
    def test_default_magic(self):
        h = KrzHeader()
        self.assertEqual(h.magic, b'PRAM')

    def test_software_version(self):
        h = KrzHeader()
        self.assertEqual(h.rest[2], 353)

    def test_write_size(self):
        h = KrzHeader()
        buf = io.BytesIO()
        h.write(buf)
        self.assertEqual(buf.tell(), 32)

    def test_write_read_roundtrip(self):
        h = KrzHeader(osize=1000)
        buf = io.BytesIO()
        h.write(buf)

        buf.seek(0)
        h2 = KrzHeader.read(buf)
        self.assertEqual(h2.magic, b'PRAM')
        self.assertEqual(h2.osize, 1000)
        self.assertEqual(h2.rest[2], 353)
        self.assertTrue(h2.is_valid())

    def test_invalid_magic(self):
        h = KrzHeader(magic=b'XXXX')
        self.assertFalse(h.is_valid())


class TestEnvelope(unittest.TestCase):
    def test_size(self):
        self.assertEqual(Envelope.size(), 12)

    def test_write_size(self):
        env = Envelope()
        buf = io.BytesIO()
        env.write(buf)
        self.assertEqual(buf.tell(), 12)


class TestSoundfilehead(unittest.TestCase):
    def test_size(self):
        self.assertEqual(Soundfilehead.size(), 32)

    def test_write_size(self):
        sh = Soundfilehead()
        buf = io.BytesIO()
        sh.write(buf)
        self.assertEqual(buf.tell(), 32)

    def test_default_root_key(self):
        sh = Soundfilehead()
        self.assertEqual(sh.rootkey, 60)

    def test_set_root_key(self):
        sh = Soundfilehead()
        sh.set_root_key(48)
        self.assertEqual(sh.rootkey, 48)

    def test_needs_load_flag(self):
        sh = Soundfilehead()
        sh.flags = 0x70
        self.assertTrue(sh.needs_load())
        sh.flags = 0x00
        self.assertFalse(sh.needs_load())

    def test_loop_off_flag(self):
        sh = Soundfilehead()
        sh.flags = 0xF0  # loop off
        self.assertFalse(sh.is_looped())
        sh.flags = 0x70  # loop on
        self.assertTrue(sh.is_looped())


class TestSwapBytes(unittest.TestCase):
    def test_basic_swap(self):
        data = bytes([0x01, 0x02, 0x03, 0x04])
        swapped = swap_bytes(data)
        self.assertEqual(swapped, bytes([0x02, 0x01, 0x04, 0x03]))

    def test_roundtrip(self):
        data = bytes([0xAB, 0xCD, 0xEF, 0x01])
        self.assertEqual(swap_bytes(swap_bytes(data)), data)

    def test_empty(self):
        self.assertEqual(swap_bytes(b''), b'')


class TestKSample(unittest.TestCase):
    def test_name_truncation(self):
        ks = KSample()
        ks.set_name('a' * 20)
        self.assertEqual(len(ks.name), 16)

    def test_stereo_flag(self):
        ks = KSample()
        ks.flags = 0
        self.assertFalse(ks.is_stereo())
        ks.flags = 1
        self.assertTrue(ks.is_stereo())

    def test_insert_header(self):
        ks = KSample()
        self.assertEqual(ks.num_headers, -1)
        ks.insert_header(Soundfilehead())
        self.assertEqual(ks.num_headers, 0)  # count - 1
        ks.insert_header(Soundfilehead())
        self.assertEqual(ks.num_headers, 1)


class TestCreateSampleFromWav(unittest.TestCase):
    def test_16bit_mono(self):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100)
        ks = create_sample_from_wav(wav, 'test', 200, root_key=60)
        self.assertEqual(len(ks.headers), 1)
        self.assertFalse(ks.is_stereo())
        self.assertEqual(ks.headers[0].rootkey, 60)

    def test_16bit_stereo(self):
        wav = WavFile(channels=2, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01\x02\x03' * 100)
        ks = create_sample_from_wav(wav, 'test', 200, root_key=60)
        self.assertEqual(len(ks.headers), 2)
        self.assertTrue(ks.is_stereo())

    def test_8bit_mono(self):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=8,
                      data=bytes([128] * 100))
        ks = create_sample_from_wav(wav, 'test', 200, root_key=60)
        self.assertEqual(len(ks.headers), 1)
        self.assertFalse(ks.is_stereo())
        # Each 8-bit sample becomes 2 bytes
        self.assertEqual(len(ks.headers[0].sampledata), 200)

    def test_loop_off_when_no_loop(self):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100)
        ks = create_sample_from_wav(wav, 'test', 200)
        self.assertFalse(ks.headers[0].is_looped())
        self.assertEqual(ks.headers[0].flags, 0xF0)

    def test_loop_on_with_smpl(self):
        info = SampleInfo(root_key=60, is_looped=True,
                          loop_start=10, loop_end=90)
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100, sample_info=info)
        ks = create_sample_from_wav(wav, 'test', 200)
        self.assertTrue(ks.headers[0].is_looped())
        self.assertEqual(ks.headers[0].flags, 0x70)
        self.assertEqual(ks.headers[0].sample_loop_start, 10)
        self.assertEqual(ks.headers[0].sample_end, 90)

    def test_loop_clamp_end_beyond_data(self):
        """loop_end past actual sample count is clamped."""
        info = SampleInfo(root_key=60, is_looped=True,
                          loop_start=10, loop_end=500)
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100, sample_info=info)  # 100 frames
        ks = create_sample_from_wav(wav, 'test', 200)
        self.assertTrue(ks.headers[0].is_looped())
        self.assertEqual(ks.headers[0].sample_end, 99)
        self.assertEqual(ks.headers[0].sample_loop_start, 10)

    def test_loop_clamp_start_beyond_end(self):
        """loop_start >= loop_end disables the loop."""
        info = SampleInfo(root_key=60, is_looped=True,
                          loop_start=90, loop_end=50)
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100, sample_info=info)
        ks = create_sample_from_wav(wav, 'test', 200)
        self.assertFalse(ks.headers[0].is_looped())
        self.assertEqual(ks.headers[0].sample_end, 99)

    def test_looped_sample_data_truncated(self):
        """sampledata is truncated to match loop_end."""
        info = SampleInfo(root_key=60, is_looped=True,
                          loop_start=10, loop_end=49)
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100, sample_info=info)  # 100 frames
        ks = create_sample_from_wav(wav, 'test', 200)
        # sampledata should be (49 + 1) * 2 = 100 bytes, not 200
        self.assertEqual(len(ks.headers[0].sampledata), 100)

    def test_multiple_looped_samples_offsets(self):
        """Two looped samples with post-loop data get correct prewrite offsets."""
        info_a = SampleInfo(root_key=60, is_looped=True,
                            loop_start=10, loop_end=49)
        wav_a = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                        data=b'\x00\x01' * 100, sample_info=info_a)
        ks_a = create_sample_from_wav(wav_a, 'a', 200)

        info_b = SampleInfo(root_key=60, is_looped=True,
                            loop_start=5, loop_end=29)
        wav_b = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                        data=b'\x00\x01' * 80, sample_info=info_b)
        ks_b = create_sample_from_wav(wav_b, 'b', 201)

        # A: 50 frames of data, B: 30 frames of data
        self.assertEqual(len(ks_a.headers[0].sampledata), 100)  # 50 * 2
        self.assertEqual(len(ks_b.headers[0].sampledata), 60)   # 30 * 2

        # prewrite: A gets offset 0, returns 100; B gets offset 100, returns 160
        offset = ks_a.headers[0].prewrite(0)
        self.assertEqual(offset, 100)
        offset = ks_b.headers[0].prewrite(offset)
        self.assertEqual(offset, 160)

    def test_root_key_from_smpl(self):
        info = SampleInfo(root_key=48)
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100, sample_info=info)
        ks = create_sample_from_wav(wav, 'test', 200, root_key=60)
        # smpl chunk root key overrides the passed root_key
        self.assertEqual(ks.headers[0].rootkey, 48)

    def test_hash(self):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100)
        ks = create_sample_from_wav(wav, 'test', 200)
        self.assertEqual(KHash.get_id(ks.get_hash()), 200)
        self.assertEqual(KHash.get_type(ks.get_hash()), KHash.T_SAMPLE)


class TestAddLayerKeyRange(unittest.TestCase):
    def _make_keymap(self, km_id=200):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100)
        sample = create_sample_from_wav(wav, 'test', km_id, root_key=60)
        return create_instrument_keymap(sample, km_id, 'test')

    def test_default_full_range(self):
        """Default layer covers full keyboard (0-127)."""
        km = self._make_keymap()
        prog = KProgram()
        prog.make_pgm_block()
        prog.add_layer(km, stereo=False)
        # Find LYR segment
        lyr = [s for s in prog.segments if s.tag == Segment.LYRSEGTAG][0]
        self.assertEqual(lyr.data[3], 0)
        self.assertEqual(lyr.data[4], 127)

    def test_custom_key_range(self):
        """Custom lo_key/hi_key set on LYR segment."""
        km = self._make_keymap()
        prog = KProgram()
        prog.make_pgm_block()
        prog.add_layer(km, stereo=False, lo_key=36, hi_key=48)
        lyr = [s for s in prog.segments if s.tag == Segment.LYRSEGTAG][0]
        self.assertEqual(lyr.data[3], 36)
        self.assertEqual(lyr.data[4], 48)

    def test_stereo_custom_key_range(self):
        """Custom key range works for stereo layers too."""
        wav = WavFile(channels=2, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01\x02\x03' * 100)
        sample = create_sample_from_wav(wav, 'test', 200, root_key=60)
        km = create_instrument_keymap(sample, 200, 'test')
        prog = KProgram()
        prog.make_pgm_block()
        prog.add_layer(km, stereo=True, lo_key=24, hi_key=60)
        lyr = [s for s in prog.segments if s.tag == Segment.LYRSEGTAG][0]
        self.assertEqual(lyr.data[3], 24)
        self.assertEqual(lyr.data[4], 60)


class TestMultiLayerProgram(unittest.TestCase):
    def _make_keymap(self, km_id):
        wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                      data=b'\x00\x01' * 100)
        sample = create_sample_from_wav(wav, f'test{km_id}', km_id, root_key=60)
        return create_instrument_keymap(sample, km_id, f'test{km_id}')

    def test_two_layers(self):
        """Multi-layer program has correct layer count."""
        km1 = self._make_keymap(200)
        km2 = self._make_keymap(201)
        prog = create_multi_layer_program(
            [km1, km2], 200, 'test',
            stereo_flags=[False, False],
            key_ranges=[(36, 36), (38, 38)])
        # PGM segment should show 2 layers
        pgm = [s for s in prog.segments if s.tag == Segment.PGMSEGTAG][0]
        self.assertEqual(pgm.data[1], 2)

    def test_layer_key_ranges(self):
        """Each layer has its own key range."""
        km1 = self._make_keymap(200)
        km2 = self._make_keymap(201)
        prog = create_multi_layer_program(
            [km1, km2], 200, 'test',
            stereo_flags=[False, False],
            key_ranges=[(36, 36), (38, 45)])
        lyrs = [s for s in prog.segments if s.tag == Segment.LYRSEGTAG]
        self.assertEqual(len(lyrs), 2)
        self.assertEqual(lyrs[0].data[3], 36)
        self.assertEqual(lyrs[0].data[4], 36)
        self.assertEqual(lyrs[1].data[3], 38)
        self.assertEqual(lyrs[1].data[4], 45)

    def test_layer_keymap_refs(self):
        """Each layer references the correct keymap."""
        km1 = self._make_keymap(200)
        km2 = self._make_keymap(201)
        prog = create_multi_layer_program(
            [km1, km2], 200, 'test',
            stereo_flags=[False, False],
            key_ranges=[(36, 36), (38, 38)])
        cals = [s for s in prog.segments if s.tag == Segment.CALSEGTAG]
        self.assertEqual(len(cals), 2)
        # First CAL references keymap 200
        km1_id = KHash.get_id(km1.get_hash())
        self.assertEqual((cals[0].data[7] << 8) | cals[0].data[8], km1_id)
        # Second CAL references keymap 201
        km2_id = KHash.get_id(km2.get_hash())
        self.assertEqual((cals[1].data[7] << 8) | cals[1].data[8], km2_id)

    def test_write_roundtrip(self):
        """Multi-layer program can be written to a buffer."""
        km1 = self._make_keymap(200)
        km2 = self._make_keymap(201)
        prog = create_multi_layer_program(
            [km1, km2], 200, 'test',
            stereo_flags=[False, False],
            key_ranges=[(36, 36), (38, 38)])
        buf = io.BytesIO()
        prog.write(buf)
        self.assertGreater(buf.tell(), 0)


if __name__ == '__main__':
    unittest.main()
