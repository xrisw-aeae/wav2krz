"""Tests for converter, list file parsing, and end-to-end conversion."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wav2krz.converter import (
    ConversionMode,
    WavEntry,
    _build_drum_groups,
    convert_from_list_file,
    convert_wavs_to_krz,
    parse_note_name,
    parse_velocity_range,
    read_program_list,
    read_wav_list,
)
from wav2krz.exceptions import Wav2KrzError

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

    def test_with_key_range(self):
        """Parse lokey/hikey from list file."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C3 C5\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)  # C4
        self.assertEqual(entries[0].lo_key, 48)    # C3
        self.assertEqual(entries[0].hi_key, 72)    # C5
        self.assertIsNone(entries[0].vel_range)

    def test_with_key_range_and_velocity(self):
        """Parse lokey/hikey with velocity range."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C3 C5 v1-3\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)
        self.assertEqual(entries[0].lo_key, 48)
        self.assertEqual(entries[0].hi_key, 72)
        self.assertEqual(entries[0].vel_range, (0, 2))

    def test_key_range_midi_numbers(self):
        """Parse lokey/hikey as MIDI numbers."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav 60 36 84\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)
        self.assertEqual(entries[0].lo_key, 36)
        self.assertEqual(entries[0].hi_key, 84)

    def test_key_range_lokey_greater_than_hikey_error(self):
        """Error when lokey > hikey."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C5 C3\n')  # lokey C5 > hikey C3
        with self.assertRaises(Wav2KrzError) as ctx:
            read_wav_list(listfile)
        self.assertIn('lokey', str(ctx.exception).lower())

    def test_two_keys_second_higher_infers_hikey(self):
        """root + higher value: lo=root, hi=second."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 G4\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)  # C4
        self.assertEqual(entries[0].lo_key, 60)    # C4
        self.assertEqual(entries[0].hi_key, 67)    # G4

    def test_two_keys_second_lower_infers_lokey(self):
        """root + lower value: lo=second, hi=root."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C3\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)  # C4
        self.assertEqual(entries[0].lo_key, 48)    # C3
        self.assertEqual(entries[0].hi_key, 60)    # C4

    def test_two_keys_equal_infers_single_key(self):
        """root + equal value: lo=hi=root."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C4\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)
        self.assertEqual(entries[0].lo_key, 60)
        self.assertEqual(entries[0].hi_key, 60)

    def test_quoted_filename_with_spaces(self):
        """Quoted filename with spaces is parsed correctly."""
        make_wav(self.dir / 'my sample.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('"my sample.wav" C4\n')
        entries = read_wav_list(listfile)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path.name, 'my sample.wav')
        self.assertEqual(entries[0].root_key, 60)

    def test_quoted_filename_with_spaces_and_velocity(self):
        """Quoted filename with spaces plus velocity range."""
        make_wav(self.dir / 'kick drum.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('"kick drum.wav" C2 ppp-mp\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].path.name, 'kick drum.wav')
        self.assertEqual(entries[0].root_key, 36)
        self.assertEqual(entries[0].vel_range, (0, 3))

    def test_unclosed_quote_error(self):
        """Unclosed quote in listfile raises Wav2KrzError."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('"unclosed.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_wav_list(listfile)


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


class TestReadWavListWithGroups(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_group_sets_root_key(self):
        """@group sets root_key on subsequent entries."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@group C2\na.wav\nb.wav\n')
        entries = read_wav_list(listfile)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].root_key, 36)  # C2
        self.assertEqual(entries[1].root_key, 36)

    def test_group_sets_key_range(self):
        """@group with lo/hi sets lo_key/hi_key on entries."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@group C2 A#1 C2\na.wav\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 36)  # C2
        self.assertEqual(entries[0].lo_key, 34)    # A#1
        self.assertEqual(entries[0].hi_key, 36)    # C2

    def test_group_with_velocity(self):
        """Sample lines in a group can have velocity ranges."""
        make_wav(self.dir / 'soft.wav')
        make_wav(self.dir / 'loud.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2 A#1 C2\n'
            'soft.wav ppp-p\n'
            'loud.wav f-fff\n'
        )
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].vel_range, (0, 2))
        self.assertEqual(entries[1].vel_range, (5, 7))
        self.assertEqual(entries[0].root_key, 36)
        self.assertEqual(entries[1].root_key, 36)

    def test_multiple_groups(self):
        """Multiple @group directives create separate groups."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2\na.wav\n'
            '@group D2\nb.wav\n'
        )
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 36)  # C2
        self.assertEqual(entries[1].root_key, 38)  # D2

    def test_backward_compat_no_groups(self):
        """Files without @group work as before."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4 C3 C5 v1-3\n')
        entries = read_wav_list(listfile)
        self.assertEqual(entries[0].root_key, 60)
        self.assertEqual(entries[0].lo_key, 48)
        self.assertEqual(entries[0].hi_key, 72)
        self.assertEqual(entries[0].vel_range, (0, 2))

    def test_group_missing_root_key_error(self):
        """@group with no arguments raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@group\na.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_wav_list(listfile)

    def test_group_invalid_root_key_error(self):
        """@group with invalid root key raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@group INVALID\na.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_wav_list(listfile)

    def test_group_sample_with_extra_params_error(self):
        """Inside a group, extra params on sample line raise error."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@group C2\na.wav C4\n')
        with self.assertRaises(Wav2KrzError):
            read_wav_list(listfile)


class TestBuildDrumGroups(unittest.TestCase):
    def test_basic_grouping(self):
        """Entries with same root_key are grouped together."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=36),
            WavEntry(path=Path('b.wav'), root_key=36),
            WavEntry(path=Path('c.wav'), root_key=38),
        ]
        groups = _build_drum_groups(entries)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].root_key, 36)
        self.assertEqual(groups[0].sample_indices, [0, 1])
        self.assertEqual(groups[1].root_key, 38)
        self.assertEqual(groups[1].sample_indices, [2])

    def test_key_range_from_entry(self):
        """Groups derive key range from lo_key/hi_key."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=36, lo_key=34, hi_key=36),
            WavEntry(path=Path('b.wav'), root_key=38, lo_key=38, hi_key=45),
        ]
        groups = _build_drum_groups(entries)
        self.assertEqual(groups[0].lo_key, 34)
        self.assertEqual(groups[0].hi_key, 36)
        self.assertEqual(groups[1].lo_key, 38)
        self.assertEqual(groups[1].hi_key, 45)

    def test_no_key_range_defaults_to_root(self):
        """Without lo_key/hi_key, key range defaults to root_key."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=36),
        ]
        groups = _build_drum_groups(entries)
        self.assertEqual(groups[0].lo_key, 36)
        self.assertEqual(groups[0].hi_key, 36)

    def test_velocity_layer_map(self):
        """Entries with velocity ranges produce vel_layer_map."""
        entries = [
            WavEntry(path=Path('soft.wav'), root_key=36, vel_range=(0, 2)),
            WavEntry(path=Path('loud.wav'), root_key=36, vel_range=(5, 7)),
        ]
        groups = _build_drum_groups(entries)
        self.assertEqual(len(groups), 1)
        self.assertIsNotNone(groups[0].vel_layer_map)
        self.assertEqual(groups[0].vel_layer_map[(0, 2)], [0])
        self.assertEqual(groups[0].vel_layer_map[(5, 7)], [1])

    def test_no_velocity_no_map(self):
        """Without velocity ranges, vel_layer_map is None."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=36),
        ]
        groups = _build_drum_groups(entries)
        self.assertIsNone(groups[0].vel_layer_map)

    def test_missing_root_key_error(self):
        """Entry without root_key raises error."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=None),
        ]
        with self.assertRaises(Wav2KrzError):
            _build_drum_groups(entries)

    def test_sorted_by_root_key(self):
        """Groups are sorted by root_key."""
        entries = [
            WavEntry(path=Path('a.wav'), root_key=60),
            WavEntry(path=Path('b.wav'), root_key=36),
            WavEntry(path=Path('c.wav'), root_key=48),
        ]
        groups = _build_drum_groups(entries)
        self.assertEqual([g.root_key for g in groups], [36, 48, 60])


class TestDrumsetMultiMode(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_drumset_multi(self):
        """End-to-end drumset-multi with 2 groups."""
        for name in ['kick', 'snare']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2\nkick.wav\n'
            '@group D2\nsnare.wav\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET_MULTI)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_drumset_multi_with_velocity(self):
        """Drumset-multi with velocity layers within groups."""
        for name in ['kick_soft', 'kick_loud', 'snare_soft', 'snare_loud']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2\n'
            'kick_soft.wav ppp-p\n'
            'kick_loud.wav f-fff\n'
            '@group D2\n'
            'snare_soft.wav ppp-p\n'
            'snare_loud.wav f-fff\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET_MULTI)
        self.assertTrue(out.exists())

    def test_drumset_multi_with_key_range(self):
        """Drumset-multi with explicit key ranges."""
        for name in ['kick', 'cowbell']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2 A#1 C2\nkick.wav\n'
            '@group D2 D2 A2\ncowbell.wav\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET_MULTI)
        self.assertTrue(out.exists())

    def test_drumset_multi_k26(self):
        """Drumset-multi produces valid .k26 file."""
        for name in ['kick', 'snare']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2\nkick.wav\n'
            '@group D2\nsnare.wav\n'
        )
        out = self.dir / 'out.k26'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET_MULTI)
        self.assertTrue(out.exists())
        # Verify PRAM header
        with open(out, 'rb') as f:
            magic = f.read(4)
        self.assertEqual(magic, b'PRAM')

    def test_drumset_multi_missing_root_key_error(self):
        """drumset-multi without root keys raises error."""
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        with self.assertRaises(Wav2KrzError):
            convert_wavs_to_krz(
                [self.dir / 'a.wav'], out,
                mode=ConversionMode.DRUMSET_MULTI)


class TestReadProgramList(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_program_directive(self):
        """Files without @program produce a single section with name=None."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4\n')
        sections = read_program_list(listfile)
        self.assertEqual(len(sections), 1)
        self.assertIsNone(sections[0].name)
        self.assertIsNone(sections[0].mode)
        self.assertEqual(len(sections[0].entries), 1)

    def test_single_program_with_name_and_mode(self):
        """@program with name and mode."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Grand Piano" instrument\na.wav C4\n')
        sections = read_program_list(listfile)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].name, 'Grand Piano')
        self.assertEqual(sections[0].mode, 'instrument')

    def test_two_program_directives(self):
        """Two @program directives produce two sections."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument\na.wav C4\n'
            '@program "Drums" drumset\nb.wav C2\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].name, 'Piano')
        self.assertEqual(sections[0].mode, 'instrument')
        self.assertEqual(len(sections[0].entries), 1)
        self.assertEqual(sections[1].name, 'Drums')
        self.assertEqual(sections[1].mode, 'drumset')
        self.assertEqual(len(sections[1].entries), 1)

    def test_quoted_names_with_spaces(self):
        """Quoted names with spaces are parsed correctly."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "My Grand Piano" instrument\na.wav\n')
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].name, 'My Grand Piano')

    def test_unquoted_name(self):
        """Unquoted names work for single words."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program Piano instrument\na.wav\n')
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].name, 'Piano')

    def test_keymap_at_section_level(self):
        """@keymap before any @group sets section keymap_name."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument\n'
            '@keymap "Piano Map"\n'
            'a.wav C4\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].keymap_name, 'Piano Map')

    def test_keymap_inside_group(self):
        """@keymap inside @group sets entry.keymap_name."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Drums" drumset-multi\n'
            '@group C2\n'
            '@keymap "Kick"\n'
            'a.wav f-fff\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].entries[0].keymap_name, 'Kick')

    def test_group_resets_per_group_keymap(self):
        """A new @group resets the per-group keymap name."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@group C2\n'
            '@keymap "Kick"\n'
            'a.wav\n'
            '@group D2\n'
            'b.wav\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].entries[0].keymap_name, 'Kick')
        self.assertIsNone(sections[0].entries[1].keymap_name)

    def test_program_resets_group_context(self):
        """@program resets @group and @keymap context."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "A" drumset-multi\n'
            '@group C2\n'
            '@keymap "Kick"\n'
            'a.wav\n'
            '@program "B" instrument\n'
            'b.wav C4\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].entries[0].keymap_name, 'Kick')
        # Second section: no group context, no keymap name on entry
        self.assertIsNone(sections[1].entries[0].keymap_name)

    def test_program_mode_optional(self):
        """@program with name only (mode omitted) falls back to None."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano"\na.wav C4\n')
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].name, 'Piano')
        self.assertIsNone(sections[0].mode)

    def test_error_missing_name(self):
        """@program without a name raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program\na.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_program_list(listfile)

    def test_error_invalid_mode(self):
        """@program with invalid mode raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano" badmode\na.wav\n')
        with self.assertRaises(Wav2KrzError) as ctx:
            read_program_list(listfile)
        self.assertIn('badmode', str(ctx.exception))

    def test_error_too_many_params(self):
        """@program with too many params raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano" instrument extra\na.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_program_list(listfile)

    def test_error_keymap_missing_name(self):
        """@keymap without a name raises error."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('@keymap\na.wav\n')
        with self.assertRaises(Wav2KrzError):
            read_program_list(listfile)

    def test_empty_file_returns_empty(self):
        """Empty file returns empty list."""
        listfile = self.dir / 'list.txt'
        listfile.write_text('')
        sections = read_program_list(listfile)
        self.assertEqual(len(sections), 0)


class TestMultiProgramConversion(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_two_instrument_programs(self):
        """Two instrument programs produce valid .krz."""
        make_wav(self.dir / 'piano.wav')
        make_wav(self.dir / 'strings.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument\npiano.wav C4\n'
            '@program "Strings" instrument\nstrings.wav C4\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_mixed_modes(self):
        """Instrument + drumset-multi produce valid .krz."""
        make_wav(self.dir / 'piano.wav')
        make_wav(self.dir / 'kick.wav')
        make_wav(self.dir / 'snare.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument\npiano.wav C4\n'
            '@program "Drums" drumset-multi\n'
            '@group C2\nkick.wav\n'
            '@group D2\nsnare.wav\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out)
        self.assertTrue(out.exists())

    def test_per_group_keymap_names(self):
        """Per-group keymap names in drumset-multi are applied."""
        make_wav(self.dir / 'kick.wav')
        make_wav(self.dir / 'snare.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Drums" drumset-multi\n'
            '@group C2\n@keymap "Kick"\nkick.wav\n'
            '@group D2\n@keymap "Snare"\nsnare.wav\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out)
        self.assertTrue(out.exists())

    def test_fallback_to_cli_mode(self):
        """@program without mode falls back to CLI mode."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano"\na.wav C4\n')
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_no_id_collisions(self):
        """Multiple programs don't produce ID collisions (file is valid)."""
        for name in ['a', 'b', 'c', 'd']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Inst1" instrument\na.wav C4\nb.wav D4\n'
            '@program "Inst2" instrument\nc.wav C4\nd.wav D4\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_backward_compat_no_program(self):
        """File without @program still works via convert_from_list_file."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4\n')
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())


class TestNamingDefaults(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_keymap_defaults_to_program_name(self):
        """Without @keymap, keymap name falls back to program name."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano" instrument\na.wav C4\n')
        sections = read_program_list(listfile)
        # Section-level keymap_name is None, so _process_section uses program name
        self.assertIsNone(sections[0].keymap_name)
        self.assertEqual(sections[0].name, 'Piano')

    def test_keymap_defaults_to_filename(self):
        """Without @program or @keymap, names default to output filename."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('a.wav C4\n')
        out = self.dir / 'mysynth.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_names_truncated_to_16_chars(self):
        """Long names are handled without error (truncation in krz layer)."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "This Is A Very Long Name" instrument\n'
            '@keymap "Also A Very Long Keymap Name"\n'
            'a.wav C4\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out)
        self.assertTrue(out.exists())

    def test_explicit_keymap_name_used(self):
        """@keymap name is used instead of program name."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument\n'
            '@keymap "Piano Keys"\n'
            'a.wav C4\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].keymap_name, 'Piano Keys')


class TestInstrumentMultiMode(unittest.TestCase):
    """Tests for instrument-multi mode."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_basic_two_layers(self):
        """Two velocity layers, one sample each."""
        make_wav(self.dir / 'soft.wav')
        make_wav(self.dir / 'loud.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp mp\n'
            'soft.wav C4\n'
            '@layer mf fff\n'
            'loud.wav C4\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_multiple_samples_per_layer(self):
        """Each layer has multiple samples across the keyboard."""
        for name in ['soft_lo', 'soft_hi', 'loud_lo', 'loud_hi']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp mp\n'
            'soft_lo.wav C2 C0 E3\n'
            'soft_hi.wav C4 F3 G9\n'
            '@layer mf fff\n'
            'loud_lo.wav C2 C0 E3\n'
            'loud_hi.wav C4 F3 G9\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_four_layers(self):
        """Four velocity layers."""
        for name in ['pp', 'mp', 'f', 'ff']:
            make_wav(self.dir / f'{name}.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp pp\n'
            'pp.wav C4\n'
            '@layer p mp\n'
            'mp.wav C4\n'
            '@layer mf f\n'
            'f.wav C4\n'
            '@layer ff fff\n'
            'ff.wav C4\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)
        self.assertTrue(out.exists())

    def test_keymap_per_layer(self):
        """@keymap inside @layer sets per-layer keymap name."""
        make_wav(self.dir / 'soft.wav')
        make_wav(self.dir / 'loud.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp mp\n'
            '@keymap "Soft"\n'
            'soft.wav C4\n'
            '@layer mf fff\n'
            '@keymap "Loud"\n'
            'loud.wav C4\n'
        )
        sections = read_program_list(listfile)
        # First two entries should have different keymap names
        self.assertEqual(sections[0].entries[0].keymap_name, 'Soft')
        self.assertEqual(sections[0].entries[1].keymap_name, 'Loud')

    def test_vel_zone_in_lyr_segments(self):
        """LYR segment data[5] has correct velocity zone encoding."""
        from wav2krz.krz.hash import KHash
        from wav2krz.krz.keymap import KKeymap
        from wav2krz.krz.program import Segment, create_multi_layer_program

        # Build two dummy keymaps
        km1 = KKeymap()
        km1.set_hash(KHash.generate(200, KHash.T_KEYMAP))
        km2 = KKeymap()
        km2.set_hash(KHash.generate(201, KHash.T_KEYMAP))

        prog = create_multi_layer_program(
            [km1, km2], 200, 'test',
            stereo_flags=[False, False],
            key_ranges=[(0, 127), (0, 127)],
            vel_zones=[(0, 3), (4, 7)])

        lyr_segs = [s for s in prog.segments if s.tag == Segment.LYRSEGTAG]
        self.assertEqual(len(lyr_segs), 2)

        # ppp-mp: (0+1)*8 - (3+1) = 4
        self.assertEqual(lyr_segs[0].data[5], 4)
        # mf-fff: (4+1)*8 - (7+1) = 32
        self.assertEqual(lyr_segs[1].data[5], 32)

    def test_error_missing_layer(self):
        """Samples without @layer in instrument-multi should error."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            'a.wav C4\n'
        )
        out = self.dir / 'out.krz'
        with self.assertRaises(Wav2KrzError):
            convert_from_list_file(listfile, out, mode=ConversionMode.INSTRUMENT)

    def test_single_zone_layer(self):
        """@layer with single zone name (lo == hi)."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer mf\n'
            'a.wav C4\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].entries[0].vel_range, (4, 4))

    def test_hyphenated_zone_layer(self):
        """@layer with hyphenated range."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp-mp\n'
            'a.wav C4\n'
        )
        sections = read_program_list(listfile)
        self.assertEqual(sections[0].entries[0].vel_range, (0, 3))

    def test_layer_alias_in_drumset_multi(self):
        """@layer works as @group alias in drumset-multi mode."""
        make_wav(self.dir / 'kick.wav')
        make_wav(self.dir / 'snare.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@layer C2\nkick.wav\n'
            '@layer D2\nsnare.wav\n'
        )
        out = self.dir / 'out.krz'
        convert_from_list_file(listfile, out, mode=ConversionMode.DRUMSET_MULTI)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 32)

    def test_layer_alias_in_read_wav_list(self):
        """@layer works as @group alias in read_wav_list."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@layer C2\n'
            'a.wav\n'
        )
        entries = read_wav_list(listfile)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].root_key, 36)  # C2


class TestVerboseOutput(unittest.TestCase):
    """Tests for verbose/quiet output."""

    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _capture(self, func, *args, **kwargs):
        """Run func and capture stdout."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            func(*args, **kwargs)
        return buf.getvalue()

    def test_verbose_samples_mode(self):
        """verbose=True prints sample info even in samples-only mode."""
        make_wav(self.dir / 'kick.wav')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_wavs_to_krz,
            [self.dir / 'kick.wav'], out,
            mode=ConversionMode.SAMPLES, verbose=True)
        self.assertIn('Sample: kick', output)
        self.assertIn('mono', output)
        self.assertIn('Created', output)
        self.assertIn('1 samples', output)

    def test_quiet_no_output(self):
        """verbose=False produces no output."""
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_wavs_to_krz,
            [self.dir / 'a.wav'], out,
            mode=ConversionMode.INSTRUMENT, verbose=False)
        self.assertEqual(output, '')

    def test_verbose_instrument_mode(self):
        """verbose=True prints keymap and program info."""
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_wavs_to_krz,
            [self.dir / 'a.wav'], out,
            mode=ConversionMode.INSTRUMENT, verbose=True)
        self.assertIn('Sample: a', output)
        self.assertIn('Keymap:', output)
        self.assertIn('1 samples', output)
        self.assertIn('1 keymaps', output)
        self.assertIn('1 programs', output)

    def test_verbose_stereo(self):
        """Stereo samples show 'stereo'."""
        make_wav(self.dir / 'stereo.wav', channels=2)
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_wavs_to_krz,
            [self.dir / 'stereo.wav'], out,
            mode=ConversionMode.SAMPLES, verbose=True)
        self.assertIn('stereo', output)

    def test_verbose_list_file_instrument(self):
        """verbose=True with list file prints program header."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano" instrument\na.wav C4\n')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_from_list_file,
            listfile, out, mode=ConversionMode.INSTRUMENT, verbose=True)
        self.assertIn('Program "Piano" (instrument)', output)
        self.assertIn('Sample: a', output)
        self.assertIn('Keymap:', output)
        self.assertIn('Created', output)

    def test_quiet_list_file(self):
        """verbose=False with list file produces no output."""
        make_wav(self.dir / 'a.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text('@program "Piano" instrument\na.wav C4\n')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_from_list_file,
            listfile, out, mode=ConversionMode.INSTRUMENT, verbose=False)
        self.assertEqual(output, '')

    def test_verbose_instrument_multi(self):
        """instrument-multi prints layer info with velocity zones."""
        make_wav(self.dir / 'soft.wav')
        make_wav(self.dir / 'loud.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "Piano" instrument-multi\n'
            '@layer ppp mp\nsoft.wav C4\n'
            '@layer mf fff\nloud.wav C4\n'
        )
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_from_list_file,
            listfile, out, mode=ConversionMode.INSTRUMENT, verbose=True)
        self.assertIn('Program "Piano" (instrument-multi)', output)
        self.assertIn('Layer 1:', output)
        self.assertIn('Layer 2:', output)
        self.assertIn('vel ppp-mp', output)
        self.assertIn('vel mf-fff', output)

    def test_verbose_summary_counts(self):
        """Summary line has correct counts for multi-program file."""
        make_wav(self.dir / 'a.wav')
        make_wav(self.dir / 'b.wav')
        listfile = self.dir / 'list.txt'
        listfile.write_text(
            '@program "P1" instrument\na.wav C4\n'
            '@program "P2" instrument\nb.wav C4\n'
        )
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_from_list_file,
            listfile, out, mode=ConversionMode.INSTRUMENT, verbose=True)
        self.assertIn('2 samples', output)
        self.assertIn('2 keymaps', output)
        self.assertIn('2 programs', output)

    def test_verbose_default_true(self):
        """verbose defaults to True (output is produced without explicit flag)."""
        make_wav(self.dir / 'a.wav')
        out = self.dir / 'out.krz'
        output = self._capture(
            convert_wavs_to_krz,
            [self.dir / 'a.wav'], out,
            mode=ConversionMode.INSTRUMENT)
        self.assertIn('Sample: a', output)
        self.assertIn('Created', output)


if __name__ == '__main__':
    unittest.main()
