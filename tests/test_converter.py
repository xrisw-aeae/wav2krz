"""Tests for converter, list file parsing, and end-to-end conversion."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ..converter import (
    parse_note_name, parse_velocity_range, read_wav_list,
    convert_wavs_to_krz, convert_from_list_file, ConversionMode, WavEntry,
)
from ..exceptions import Wav2KrzError
from .helpers import make_wav


class TestParseNoteName(unittest.TestCase):
    def test_midi_numbers(self):
        self.assertEqual(parse_note_name('0'), 0)
        self.assertEqual(parse_note_name('60'), 60)
        self.assertEqual(parse_note_name('127'), 127)

    def test_out_of_range(self):
        self.assertIsNone(parse_note_name('128'))
        self.assertIsNone(parse_note_name('-1'))

    def test_note_names(self):
        self.assertEqual(parse_note_name('C4'), 60)
        self.assertEqual(parse_note_name('A4'), 69)
        self.assertEqual(parse_note_name('C-1'), 0)
        self.assertEqual(parse_note_name('G9'), 127)

    def test_sharps(self):
        self.assertEqual(parse_note_name('C#4'), 61)
        self.assertEqual(parse_note_name('F#3'), 54)

    def test_flats(self):
        self.assertEqual(parse_note_name('Bb4'), 70)

    def test_case_insensitive(self):
        self.assertEqual(parse_note_name('c4'), 60)
        self.assertEqual(parse_note_name('C4'), 60)

    def test_invalid(self):
        self.assertIsNone(parse_note_name('X4'))
        self.assertIsNone(parse_note_name('hello'))
        self.assertIsNone(parse_note_name(''))


class TestParseVelocityRange(unittest.TestCase):
    def test_numeric_range(self):
        self.assertEqual(parse_velocity_range('v1-3'), (0, 2))
        self.assertEqual(parse_velocity_range('v4-8'), (3, 7))
        self.assertEqual(parse_velocity_range('v1-8'), (0, 7))

    def test_numeric_single(self):
        self.assertEqual(parse_velocity_range('v1'), (0, 0))
        self.assertEqual(parse_velocity_range('v8'), (7, 7))

    def test_named_range(self):
        self.assertEqual(parse_velocity_range('ppp-p'), (0, 2))
        self.assertEqual(parse_velocity_range('mp-fff'), (3, 7))
        self.assertEqual(parse_velocity_range('ff-fff'), (6, 7))

    def test_named_single(self):
        self.assertEqual(parse_velocity_range('mf'), (4, 4))
        self.assertEqual(parse_velocity_range('ppp'), (0, 0))

    def test_case_insensitive(self):
        self.assertEqual(parse_velocity_range('V1-3'), (0, 2))
        self.assertEqual(parse_velocity_range('PPP-P'), (0, 2))
        self.assertEqual(parse_velocity_range('Mf'), (4, 4))

    def test_invalid(self):
        self.assertIsNone(parse_velocity_range('v0'))
        self.assertIsNone(parse_velocity_range('v9'))
        self.assertIsNone(parse_velocity_range('v3-1'))
        self.assertIsNone(parse_velocity_range('garbage'))
        self.assertIsNone(parse_velocity_range(''))


class TestReadWavList(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_simple_list(self):
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav\nb.wav\n')
        entries = read_wav_list(listfile)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].path.name, 'a.wav')
        self.assertIsNone(entries[0].root_key)
        self.assertIsNone(entries[0].vel_range)

    def test_with_root_keys(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)

    def test_with_velocity(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 ppp-p\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)
        self.assertEqual(entries[0].vel_range, (0, 2))

    def test_velocity_without_root_key(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav v1-4\n')
        entries = read_wav_list(listfile)
        self.assertIsNone(entries[0].root_key)
        self.assertEqual(entries[0].vel_range, (0, 3))

    def test_comments_and_blanks(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('# comment\n\na.wav\n\n# another\n')
        entries = read_wav_list(listfile)
        self.assertEqual(len(entries), 1)

    def test_invalid_parameter(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav INVALID\n')
        with self.assertRaises(Wav2KrzError):
            read_wav_list(listfile)

    def test_relative_paths(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav\n')
        entries = read_wav_list(listfile)
        self.assertTrue(entries[0].path.is_absolute())


class TestConvertWavsToKrz(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_samples_mode(self):
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        convert_wavs_to_krz([self.dir / 'a.wav'], out, mode=ConversionMode.SAMPLES)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_instrument_mode(self):
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        convert_wavs_to_krz([self.dir / 'a.wav'], out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_drumset_mode(self):
        for i in range(3):
            make_wav(self.dir / f's{i}.wav')
        out = self.dir / 'out.krz'
        wavs = [self.dir / f's{i}.wav' for i in range(3)]
        convert_wavs_to_krz(wavs, out, mode=ConversionMode.DRUMSET, start_key=36)
        self.assertTrue(out.exists())

    def test_instrument_multi_sample(self):
        for i in range(3):
            make_wav(self.dir / f's{i}.wav')
        out = self.dir / 'out.krz'
        wavs = [self.dir / f's{i}.wav' for i in range(3)]
        root_keys = [36, 60, 84]
        convert_wavs_to_krz(wavs, out, mode=ConversionMode.INSTRUMENT,
                            root_keys=root_keys)
        self.assertTrue(out.exists())

    def test_velocity_layers(self):
        for name in ['soft', 'loud']:
            make_wav(self.dir / f'{name}.wav')
        out = self.dir / 'out.krz'
        wavs = [self.dir / 'soft.wav', self.dir / 'loud.wav']
        vel_ranges = [(0, 3), (4, 7)]
        convert_wavs_to_krz(wavs, out, mode=ConversionMode.INSTRUMENT,
                            vel_ranges=vel_ranges)
        self.assertTrue(out.exists())

    def test_missing_wav(self):
        out = self.dir / 'out.krz'
        with self.assertRaises(Wav2KrzError):
            convert_wavs_to_krz([self.dir / 'missing.wav'], out)

    def test_empty_list(self):
        out = self.dir / 'out.krz'
        with self.assertRaises(Wav2KrzError):
            convert_wavs_to_krz([], out)

    def test_pram_header(self):
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        convert_wavs_to_krz([self.dir / 'a.wav'], out)
        with open(out, 'rb') as f:
            magic = f.read(4)
        self.assertEqual(magic, b'PRAM')


class TestConvertFromListFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic(self):
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav\n')
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_with_root_keys_and_velocity(self):
        make_wav(self.dir / 'soft.wav')
        make_wav(self.dir / 'loud.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('soft.wav C4 ppp-p\nloud.wav C4 mf-fff\n')
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_drumset_explicit_keys_with_velocity(self):
        for name in ['kick_soft', 'kick_loud', 'snare_soft', 'snare_loud']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            'kick_soft.wav C2 ppp-mp\n'
            'kick_loud.wav C2 mf-fff\n'
            'snare_soft.wav D2 ppp-mp\n'
            'snare_loud.wav D2 mf-fff\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET)
        self.assertTrue(out.exists())

    def test_drumset_explicit_keys_no_velocity(self):
        for name in ['kick', 'snare', 'hihat']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            'kick.wav C2\n'
            'snare.wav D2\n'
            'hihat.wav F#2\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET)
        self.assertTrue(out.exists())


if __name__ == '__main__':
    unittest.main()
