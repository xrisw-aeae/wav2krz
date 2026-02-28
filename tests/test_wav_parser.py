"""Tests for WAV file parser."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wav2krz.exceptions import UnsupportedWavFormat, WavParseError
from wav2krz.wav.parser import parse_wav

from .helpers import make_wav


class TestParseWav16BitMono(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_properties(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav'))
        self.assertEqual(wav.channels, 1)
        self.assertEqual(wav.sample_rate, 44100)
        self.assertEqual(wav.bits_per_sample, 16)
        self.assertTrue(wav.is_mono)
        self.assertFalse(wav.is_stereo)

    def test_sample_count(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', duration=0.5))
        self.assertEqual(wav.num_samples, 22050)

    def test_data_length(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', duration=0.1))
        # 16-bit mono: 2 bytes per sample
        self.assertEqual(len(wav.data), wav.num_samples * 2)

    def test_sample_period(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', sample_rate=44100))
        period = wav.get_sample_period_ns()
        self.assertAlmostEqual(period, 22675, delta=1)

    def test_22050_sample_rate(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', sample_rate=22050))
        self.assertEqual(wav.sample_rate, 22050)
        period = wav.get_sample_period_ns()
        self.assertAlmostEqual(period, 45351, delta=1)


class TestParseWav16BitStereo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_properties(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', channels=2))
        self.assertEqual(wav.channels, 2)
        self.assertTrue(wav.is_stereo)
        self.assertFalse(wav.is_mono)

    def test_sample_count(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', channels=2, duration=0.5))
        # num_samples counts frames, not individual channel samples
        self.assertEqual(wav.num_samples, 22050)

    def test_data_length(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', channels=2, duration=0.1))
        # 16-bit stereo: 4 bytes per frame
        self.assertEqual(len(wav.data), wav.num_samples * 4)


class TestParseWav8BitMono(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_properties(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=8))
        self.assertEqual(wav.bits_per_sample, 8)
        self.assertEqual(wav.channels, 1)

    def test_data_length(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=8, duration=0.1))
        # 8-bit mono: 1 byte per sample
        self.assertEqual(len(wav.data), wav.num_samples)


class TestParseWav24BitMono(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_properties(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=24))
        self.assertEqual(wav.bits_per_sample, 24)
        self.assertEqual(wav.channels, 1)
        self.assertTrue(wav.is_mono)

    def test_data_length(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=24, duration=0.1))
        # 24-bit mono: 3 bytes per sample
        self.assertEqual(len(wav.data), wav.num_samples * 3)

    def test_sample_count(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=24, duration=0.5))
        self.assertEqual(wav.num_samples, 22050)


class TestParseWav24BitStereo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_properties(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=24, channels=2))
        self.assertEqual(wav.bits_per_sample, 24)
        self.assertEqual(wav.channels, 2)
        self.assertTrue(wav.is_stereo)

    def test_data_length(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', bits=24, channels=2, duration=0.1))
        # 24-bit stereo: 6 bytes per frame
        self.assertEqual(len(wav.data), wav.num_samples * 6)


class TestParseWavSmplChunk(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_smpl_chunk(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav'))
        self.assertIsNone(wav.sample_info)

    def test_root_key(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', root_key=48))
        self.assertIsNotNone(wav.sample_info)
        self.assertEqual(wav.sample_info.root_key, 48)

    def test_loop_points(self):
        wav = parse_wav(make_wav(
            self.dir / 'test.wav', loop_start=100, loop_end=4000, root_key=60))
        self.assertTrue(wav.sample_info.is_looped)
        self.assertEqual(wav.sample_info.loop_start, 100)
        self.assertEqual(wav.sample_info.loop_end, 4000)

    def test_no_loop(self):
        wav = parse_wav(make_wav(self.dir / 'test.wav', root_key=60))
        self.assertFalse(wav.sample_info.is_looped)


class TestParseWavErrors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_not_riff(self):
        p = self.dir / 'bad.wav'
        p.write_bytes(b'\x00' * 100)
        with self.assertRaises(WavParseError):
            parse_wav(p)

    def test_too_small(self):
        p = self.dir / 'tiny.wav'
        p.write_bytes(b'RIFF')
        with self.assertRaises(WavParseError):
            parse_wav(p)

    def test_8bit_stereo_unsupported(self):
        with self.assertRaises(UnsupportedWavFormat):
            parse_wav(make_wav(self.dir / 'test.wav', bits=8, channels=2))


if __name__ == '__main__':
    unittest.main()
