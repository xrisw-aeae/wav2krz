"""Tests for keymap creation: instrument, drumset, and velocity layers."""

import unittest

from ..krz.hash import KHash
from ..krz.keymap import (
    KKeymap, VeloLevel, KeymapEntry, method_to_size,
    create_instrument_keymap, create_drumset_keymap,
)
from ..krz.sample import KSample, Soundfilehead, Envelope, create_sample_from_wav
from ..wav.parser import WavFile


def _make_sample(sample_id: int, root_key: int = 60) -> KSample:
    """Create a minimal KSample for testing."""
    wav = WavFile(channels=1, sample_rate=44100, bits_per_sample=16,
                  data=b'\x00\x01' * 100)
    return create_sample_from_wav(wav, f'smp{sample_id}', sample_id, root_key)


class TestMethodToSize(unittest.TestCase):
    def test_method_03(self):
        # sample ID (2) + subsample (1)
        self.assertEqual(method_to_size(0x03), 3)

    def test_method_13(self):
        # 2-byte tuning (2) + sample ID (2) + subsample (1)
        self.assertEqual(method_to_size(0x13), 5)

    def test_method_17(self):
        # 2-byte tuning (2) + volume (1) + sample ID (2) + subsample (1)
        self.assertEqual(method_to_size(0x17), 6)

    def test_method_0(self):
        self.assertEqual(method_to_size(0), 0)


class TestVeloLevel(unittest.TestCase):
    def test_entry_count(self):
        vl = VeloLevel(0x03, 128)
        self.assertEqual(len(vl.entries), 128)

    def test_set_sample(self):
        vl = VeloLevel(0x03, 128)
        ks = _make_sample(200, root_key=60)
        vl.set_sample(ks, 48)
        self.assertTrue(vl.entries[48].is_used())
        self.assertEqual(vl.entries[48].sample_id, 200)

    def test_set_sample_at_root(self):
        vl = VeloLevel(0x13, 128)
        ks = _make_sample(200, root_key=60)
        vl.set_sample_at_root(ks, 0, 48)
        self.assertTrue(vl.entries[48].is_used())
        self.assertEqual(vl.entries[48].sample_id, 200)
        # Tuning should be 100 * (rootkey - 12 - key)
        expected_tuning = 100 * (60 - 12 - 48)
        self.assertEqual(vl.entries[48].tuning, expected_tuning)

    def test_unused_entry(self):
        vl = VeloLevel(0x03, 128)
        self.assertFalse(vl.entries[0].is_used())

    def test_get_size(self):
        vl = VeloLevel(0x03, 128)
        self.assertEqual(vl.get_size(), 128 * 3)


class TestFillSpaces(unittest.TestCase):
    def test_single_sample_fills_all(self):
        vl = VeloLevel(0x03, 128)
        vl.entries[64].subsample_number = 1
        vl.entries[64].sample_id = 100
        vl.fill_spaces_between_samples()
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used(),
                            f"Entry {i} should be used")

    def test_two_samples_split_keyboard(self):
        vl = VeloLevel(0x03, 128)
        vl.entries[32].subsample_number = 1
        vl.entries[32].sample_id = 100
        vl.entries[96].subsample_number = 1
        vl.entries[96].sample_id = 200
        vl.fill_spaces_between_samples()

        # All entries should be filled
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used())

        # Check boundary: samples should meet near the midpoint
        self.assertEqual(vl.entries[0].sample_id, 100)
        self.assertEqual(vl.entries[32].sample_id, 100)
        self.assertEqual(vl.entries[96].sample_id, 200)
        self.assertEqual(vl.entries[127].sample_id, 200)

    def test_three_samples_all_filled(self):
        vl = VeloLevel(0x03, 128)
        for pos, sid in [(24, 100), (48, 200), (72, 300)]:
            vl.entries[pos].subsample_number = 1
            vl.entries[pos].sample_id = sid

        vl.fill_spaces_between_samples()

        for i in range(128):
            self.assertTrue(vl.entries[i].is_used(), f"Entry {i} not filled")

        # Check that all three samples have coverage
        sample_ids = set(vl.entries[i].sample_id for i in range(128))
        self.assertEqual(sample_ids, {100, 200, 300})

        # Middle sample should have more than just its one key
        mid_count = sum(1 for i in range(128)
                        if vl.entries[i].sample_id == 200)
        self.assertGreater(mid_count, 1)


class TestInstrumentKeymap(unittest.TestCase):
    def test_single_sample(self):
        ks = _make_sample(200, root_key=60)
        km = create_instrument_keymap(ks, 200, 'test')
        self.assertEqual(len(km.velocity_levels), 1)
        self.assertEqual(km.method, 0x03)
        self.assertEqual(km.entries_per_vel, 127)

    def test_single_sample_fills_keyboard(self):
        ks = _make_sample(200, root_key=60)
        km = create_instrument_keymap(ks, 200, 'test')
        vl = km.velocity_levels[0]
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used(),
                            f"Key {i} not assigned")

    def test_multiple_samples(self):
        samples = [
            _make_sample(200, root_key=36),
            _make_sample(201, root_key=60),
            _make_sample(202, root_key=84),
        ]
        km = create_instrument_keymap(samples, 200, 'test')
        vl = km.velocity_levels[0]

        # All keys should be filled
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used())

        # All three samples should appear
        sample_ids = set(vl.entries[i].sample_id for i in range(128))
        self.assertEqual(sample_ids, {200, 201, 202})

    def test_hash(self):
        ks = _make_sample(200)
        km = create_instrument_keymap(ks, 150, 'test')
        self.assertEqual(KHash.get_id(km.get_hash()), 150)
        self.assertEqual(KHash.get_type(km.get_hash()), KHash.T_KEYMAP)

    def test_velocity_mapping_single_level(self):
        ks = _make_sample(200)
        km = create_instrument_keymap(ks, 200, 'test')
        # All 8 zones should point to level 0
        self.assertEqual(km.velocity_mapping, [0] * 8)


class TestInstrumentKeymapVelocityLayers(unittest.TestCase):
    def test_two_layers(self):
        samples = [
            _make_sample(200, root_key=60),
            _make_sample(201, root_key=60),
        ]
        vel_layer_map = {
            (0, 3): [0],   # ppp-mp -> soft
            (4, 7): [1],   # mf-fff -> loud
        }
        km = create_instrument_keymap(samples, 200, 'test',
                                      vel_layer_map=vel_layer_map)

        self.assertEqual(len(km.velocity_levels), 2)
        # Zones 0-3 -> level 0, zones 4-7 -> level 1
        for z in range(4):
            self.assertEqual(km.velocity_mapping[z], 0)
        for z in range(4, 8):
            self.assertEqual(km.velocity_mapping[z], 1)

    def test_three_layers(self):
        samples = [
            _make_sample(200, root_key=60),
            _make_sample(201, root_key=60),
            _make_sample(202, root_key=60),
        ]
        vel_layer_map = {
            (0, 2): [0],   # ppp-p
            (3, 5): [1],   # mp-f
            (6, 7): [2],   # ff-fff
        }
        km = create_instrument_keymap(samples, 200, 'test',
                                      vel_layer_map=vel_layer_map)

        self.assertEqual(len(km.velocity_levels), 3)
        self.assertEqual(km.velocity_mapping[0], 0)
        self.assertEqual(km.velocity_mapping[2], 0)
        self.assertEqual(km.velocity_mapping[3], 1)
        self.assertEqual(km.velocity_mapping[5], 1)
        self.assertEqual(km.velocity_mapping[6], 2)
        self.assertEqual(km.velocity_mapping[7], 2)

    def test_each_layer_fills_keyboard(self):
        samples = [_make_sample(200, root_key=60), _make_sample(201, root_key=60)]
        vel_layer_map = {(0, 3): [0], (4, 7): [1]}
        km = create_instrument_keymap(samples, 200, 'test',
                                      vel_layer_map=vel_layer_map)

        for vl in km.velocity_levels:
            for i in range(128):
                self.assertTrue(vl.entries[i].is_used(),
                                f"Key {i} not filled in VeloLevel {vl.rang}")


class TestDrumsetKeymap(unittest.TestCase):
    def test_basic(self):
        samples = [_make_sample(200 + i) for i in range(3)]
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36)
        self.assertEqual(km.method, 0x13)
        self.assertEqual(len(km.velocity_levels), 1)

    def test_samples_at_correct_keys(self):
        samples = [_make_sample(200 + i) for i in range(3)]
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36)
        vl = km.velocity_levels[0]
        self.assertEqual(vl.entries[36].sample_id, 200)
        self.assertEqual(vl.entries[37].sample_id, 201)
        self.assertEqual(vl.entries[38].sample_id, 202)

    def test_fills_keyboard(self):
        samples = [_make_sample(200 + i) for i in range(3)]
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36)
        vl = km.velocity_levels[0]
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used())

    def test_velocity_layers(self):
        samples = [
            _make_sample(200),  # kick soft
            _make_sample(201),  # kick loud
            _make_sample(202),  # snare soft
            _make_sample(203),  # snare loud
        ]
        vel_layer_map = {
            (0, 3): [0, 2],   # soft samples
            (4, 7): [1, 3],   # loud samples
        }
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36,
                                   vel_layer_map=vel_layer_map)
        self.assertEqual(len(km.velocity_levels), 2)

    def test_explicit_key_assignments(self):
        samples = [_make_sample(200 + i, root_key=k)
                   for i, k in enumerate([36, 40, 43])]
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36,
                                   key_assignments=[36, 40, 43])
        vl = km.velocity_levels[0]
        self.assertEqual(vl.entries[36].sample_id, 200)
        self.assertEqual(vl.entries[40].sample_id, 201)
        self.assertEqual(vl.entries[43].sample_id, 202)
        # All keys should still be filled
        for i in range(128):
            self.assertTrue(vl.entries[i].is_used())

    def test_explicit_keys_with_velocity_layers(self):
        # kick soft/loud on key 36, snare soft/loud on key 38
        samples = [
            _make_sample(200, root_key=36),  # kick soft
            _make_sample(201, root_key=36),  # kick loud
            _make_sample(202, root_key=38),  # snare soft
            _make_sample(203, root_key=38),  # snare loud
        ]
        vel_layer_map = {
            (0, 3): [0, 2],   # soft: kick, snare
            (4, 7): [1, 3],   # loud: kick, snare
        }
        key_assignments = [36, 36, 38, 38]
        km = create_drumset_keymap(samples, 200, 'drums', start_key=36,
                                   vel_layer_map=vel_layer_map,
                                   key_assignments=key_assignments)
        self.assertEqual(len(km.velocity_levels), 2)

        # Soft layer: kick at 36, snare at 38
        soft_vl = km.velocity_levels[0]
        self.assertEqual(soft_vl.entries[36].sample_id, 200)
        self.assertEqual(soft_vl.entries[38].sample_id, 202)

        # Loud layer: kick at 36, snare at 38
        loud_vl = km.velocity_levels[1]
        self.assertEqual(loud_vl.entries[36].sample_id, 201)
        self.assertEqual(loud_vl.entries[38].sample_id, 203)


if __name__ == '__main__':
    unittest.main()
