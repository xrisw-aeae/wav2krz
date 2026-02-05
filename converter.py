"""Main converter orchestration for wav2krz."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .wav.parser import parse_wav, WavFile
from .krz.writer import KrzWriter
from .krz.sample import create_sample_from_wav, KSample
from .krz.keymap import create_instrument_keymap, create_drumset_keymap, KKeymap
from .krz.program import create_program, KProgram

# Output extension to program mode mapping
FORMAT_MODES = {
    '.krz': 2,  # K2000
    '.k25': 3,  # K2500
    '.k26': 4,  # K2600
}
from .exceptions import Wav2KrzError


class ConversionMode:
    """Conversion mode constants."""
    SAMPLES = "samples"
    INSTRUMENT = "instrument"
    DRUMSET = "drumset"


# Note name to MIDI number mapping
NOTE_NAMES = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11
}

# Velocity zone names to indices (0-7)
VELOCITY_NAMES = {
    'PPP': 0, 'PP': 1, 'P': 2, 'MP': 3,
    'MF': 4, 'F': 5, 'FF': 6, 'FFF': 7
}


@dataclass
class WavEntry:
    """A parsed entry from the wav list file."""
    path: Path
    root_key: Optional[int] = None
    vel_range: Optional[Tuple[int, int]] = None  # (start_zone, end_zone) 0-7
    lo_key: Optional[int] = None  # Explicit low key (0-127)
    hi_key: Optional[int] = None  # Explicit high key (0-127)


def parse_velocity_range(spec: str) -> Optional[Tuple[int, int]]:
    """
    Parse a velocity range specification.

    Supports:
        v1-3        Numeric zones (1-8 mapped to indices 0-7)
        v5          Single numeric zone
        ppp-p       Named range
        mf          Single named zone
        ppp-fff     Full range

    Args:
        spec: Velocity range string

    Returns:
        (start_zone, end_zone) tuple with 0-7 indices, or None if invalid
    """
    spec = spec.strip()

    # Try numeric format: v1-3 or v5
    match = re.match(r'^[vV](\d+)(?:-(\d+))?$', spec)
    if match:
        start = int(match.group(1)) - 1  # Convert 1-8 to 0-7
        end = int(match.group(2)) - 1 if match.group(2) else start
        if 0 <= start <= 7 and 0 <= end <= 7 and start <= end:
            return (start, end)
        return None

    # Try named format: ppp-p or mf
    parts = spec.upper().split('-')
    if len(parts) == 1:
        idx = VELOCITY_NAMES.get(parts[0])
        if idx is not None:
            return (idx, idx)
    elif len(parts) == 2:
        start_idx = VELOCITY_NAMES.get(parts[0])
        end_idx = VELOCITY_NAMES.get(parts[1])
        if start_idx is not None and end_idx is not None and start_idx <= end_idx:
            return (start_idx, end_idx)

    return None


def parse_note_name(note: str) -> Optional[int]:
    """
    Parse a note name (e.g., 'C4', 'F#3', 'Bb5') to MIDI note number.

    Args:
        note: Note name string

    Returns:
        MIDI note number (0-127) or None if invalid
    """
    note = note.strip().upper()

    # Try parsing as integer first
    try:
        midi_num = int(note)
        if 0 <= midi_num <= 127:
            return midi_num
        return None
    except ValueError:
        pass

    # Parse note name like C4, F#3, Bb5
    match = re.match(r'^([A-G])([#B]?)(-?\d+)$', note)
    if not match:
        return None

    letter, accidental, octave_str = match.groups()

    base = NOTE_NAMES.get(letter)
    if base is None:
        return None

    # Apply accidental
    if accidental == '#':
        base += 1
    elif accidental == 'B':  # Flat
        base -= 1

    # Calculate MIDI note: C4 = 60, so octave 4 base C = 60
    # MIDI note = (octave + 1) * 12 + base
    octave = int(octave_str)
    midi_num = (octave + 1) * 12 + base

    if 0 <= midi_num <= 127:
        return midi_num
    return None


def read_wav_list(list_file: Path) -> List[WavEntry]:
    """
    Read list of WAV file paths with optional root keys, key ranges, and velocity ranges.

    File format (one entry per line):
        filename.wav                    # Defaults for everything
        filename.wav C4                 # Root key only
        filename.wav C4 C3 C5           # Root key, lokey, hikey
        filename.wav C4 C3 C5 v1-3      # Root key, lokey, hikey, velocity
        filename.wav 60 36 84           # MIDI numbers work too
        filename.wav C4 v1-3            # Root key + velocity (no key range)
        # This is a comment

    Column order after filename: [root_key] [lokey] [hikey] [velocity]
    - lokey/hikey are optional but must appear together (both or neither)
    - When present, they explicitly set the range; the fill algorithm does NOT extend beyond
    - When absent, fill algorithm works as before

    Args:
        list_file: Path to text file

    Returns:
        List of WavEntry objects.
    """
    entries = []
    with open(list_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Split line into parts
            parts = line.split()
            if not parts:
                continue

            wav_path = Path(parts[0])
            # If relative path, make it relative to list file's directory
            if not wav_path.is_absolute():
                wav_path = list_file.parent / wav_path

            entry = WavEntry(path=wav_path)

            # Parse remaining columns positionally
            # Extract velocity range if last part looks like one
            remaining = parts[1:]
            if remaining and parse_velocity_range(remaining[-1]) is not None:
                entry.vel_range = parse_velocity_range(remaining[-1])
                remaining = remaining[:-1]

            # Now remaining should be: [] or [root_key] or [root_key, lokey, hikey]
            if len(remaining) == 0:
                pass  # No key info
            elif len(remaining) == 1:
                # Just root key
                rk = parse_note_name(remaining[0])
                if rk is None:
                    raise Wav2KrzError(
                        f"Invalid root key '{remaining[0]}' on line {line_num}. "
                        f"Expected note name (C4) or MIDI number (60)."
                    )
                entry.root_key = rk
            elif len(remaining) == 3:
                # root_key, lokey, hikey
                rk = parse_note_name(remaining[0])
                lo = parse_note_name(remaining[1])
                hi = parse_note_name(remaining[2])
                if rk is None:
                    raise Wav2KrzError(
                        f"Invalid root key '{remaining[0]}' on line {line_num}."
                    )
                if lo is None:
                    raise Wav2KrzError(
                        f"Invalid lokey '{remaining[1]}' on line {line_num}."
                    )
                if hi is None:
                    raise Wav2KrzError(
                        f"Invalid hikey '{remaining[2]}' on line {line_num}."
                    )
                if lo > hi:
                    raise Wav2KrzError(
                        f"lokey ({remaining[1]}) must be <= hikey ({remaining[2]}) on line {line_num}."
                    )
                entry.root_key = rk
                entry.lo_key = lo
                entry.hi_key = hi
            elif len(remaining) == 2:
                # Ambiguous: could be root_key + something invalid, or missing hikey
                # Check if both look like notes - if so, error about missing hikey
                n1 = parse_note_name(remaining[0])
                n2 = parse_note_name(remaining[1])
                if n1 is not None and n2 is not None:
                    raise Wav2KrzError(
                        f"Missing hikey on line {line_num}. "
                        f"lokey/hikey must appear together (got root_key and lokey only)."
                    )
                elif n1 is not None:
                    raise Wav2KrzError(
                        f"Unknown parameter '{remaining[1]}' on line {line_num}. "
                        f"Expected velocity range (v1-3, ppp-p) or lokey+hikey pair."
                    )
                else:
                    raise Wav2KrzError(
                        f"Invalid root key '{remaining[0]}' on line {line_num}."
                    )
            else:
                raise Wav2KrzError(
                    f"Too many parameters on line {line_num}. "
                    f"Expected: filename [root_key] [lokey hikey] [velocity]"
                )

            entries.append(entry)

    return entries


def convert_wavs_to_krz(
    wav_files: List[Path],
    output_path: Path,
    mode: str = ConversionMode.SAMPLES,
    start_key: int = 36,
    start_id: int = 200,
    name: Optional[str] = None,
    root_key: Optional[int] = None,
    root_keys: Optional[List[Optional[int]]] = None,
    vel_ranges: Optional[List[Optional[Tuple[int, int]]]] = None,
    key_ranges: Optional[List[Optional[Tuple[int, int]]]] = None
) -> None:
    """
    Convert WAV files to Kurzweil .krz format.

    Args:
        wav_files: List of paths to WAV files
        output_path: Output .krz file path
        mode: Conversion mode (samples, instrument, drumset)
        start_key: Starting MIDI key for drumset mode (default 36 = C1)
        start_id: Starting object ID (default 200)
        name: Base name for keymap/program (default: output filename)
        root_key: Global root key override for all samples (default: None)
        root_keys: Per-sample root keys (default: None). If provided, must match
                   length of wav_files. None entries use default or WAV metadata.
        vel_ranges: Per-sample velocity ranges as (start_zone, end_zone) tuples.
                    Zones are 0-7 mapping to ppp through fff.
        key_ranges: Per-sample key ranges as (lokey, hikey) tuples.
                    When specified, the fill algorithm will not extend
                    a sample beyond these bounds.

    Raises:
        Wav2KrzError: On conversion errors
    """
    if not wav_files:
        raise Wav2KrzError("No WAV files to convert")

    writer = KrzWriter()
    samples: List[KSample] = []
    sample_vel_ranges: List[Optional[Tuple[int, int]]] = []

    # Parse all WAV files and create samples
    sample_id = start_id
    for i, wav_path in enumerate(wav_files):
        if not wav_path.exists():
            raise Wav2KrzError(f"WAV file not found: {wav_path}")

        try:
            wav_data = parse_wav(wav_path)
        except Exception as e:
            raise Wav2KrzError(f"Error parsing {wav_path}: {e}")

        # Use WAV filename (without extension) as sample name
        sample_name = wav_path.stem[:16]

        # Determine root key (priority: global override > per-sample > drumset position > WAV metadata > default)
        if root_key is not None:
            sample_root_key = root_key
        elif root_keys is not None and i < len(root_keys) and root_keys[i] is not None:
            sample_root_key = root_keys[i]
        elif mode == ConversionMode.DRUMSET:
            sample_root_key = start_key + len(samples)
            if sample_root_key > 127:
                sample_root_key = 127
        else:
            sample_root_key = 60

        sample = create_sample_from_wav(wav_data, sample_name, sample_id, sample_root_key)
        samples.append(sample)
        writer.add_sample(sample)
        sample_id += 1

        # Track velocity range for this sample
        vr = None
        if vel_ranges is not None and i < len(vel_ranges):
            vr = vel_ranges[i]
        sample_vel_ranges.append(vr)

    # Create keymap and program based on mode
    if mode in (ConversionMode.INSTRUMENT, ConversionMode.DRUMSET):
        base_name = name if name else output_path.stem[:16]
        keymap_id = start_id
        program_id = start_id

        has_stereo = any(s.is_stereo() for s in samples)

        # Build velocity layer grouping if any velocity ranges are specified
        has_vel_layers = any(vr is not None for vr in sample_vel_ranges)
        vel_layer_map = None

        if has_vel_layers:
            # Group: {(start, end): [sample_indices...]}
            vel_layer_map = {}
            for idx, vr in enumerate(sample_vel_ranges):
                if vr is None:
                    vr = (0, 7)  # Default: all zones
                vel_layer_map.setdefault(vr, []).append(idx)

        if mode == ConversionMode.INSTRUMENT:
            keymap = create_instrument_keymap(
                samples, keymap_id, base_name, vel_layer_map=vel_layer_map,
                key_ranges=key_ranges)
        else:
            # In drumset mode, root_keys from the list file specify key positions.
            # Build key_assignments: explicit key per sample, or None for consecutive.
            drum_key_assignments = None
            if root_keys is not None and any(rk is not None for rk in root_keys):
                # Assign explicit keys where provided, fill gaps with consecutive keys
                next_auto_key = start_key
                drum_key_assignments = []
                for rk in root_keys:
                    if rk is not None:
                        drum_key_assignments.append(rk)
                    else:
                        drum_key_assignments.append(next_auto_key)
                        next_auto_key += 1

            keymap = create_drumset_keymap(
                samples, keymap_id, base_name, start_key,
                vel_layer_map=vel_layer_map,
                key_assignments=drum_key_assignments,
                key_ranges=key_ranges)

        writer.add_keymap(keymap)

        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)
        program = create_program(keymap, program_id, base_name, has_stereo, mode=pgm_mode)
        writer.add_program(program)

    # Write the .krz file
    writer.write(output_path)


def convert_from_list_file(
    list_file: Path,
    output_path: Path,
    mode: str = ConversionMode.SAMPLES,
    start_key: int = 36,
    start_id: int = 200,
    name: Optional[str] = None,
    root_key: Optional[int] = None
) -> None:
    """
    Convert WAV files listed in a text file to .krz format.

    Args:
        list_file: Path to text file with WAV paths, root keys, velocity ranges
        output_path: Output .krz file path
        mode: Conversion mode
        start_key: Starting MIDI key for drumset mode
        start_id: Starting object ID
        name: Base name for keymap/program
        root_key: Global root key override (overrides per-sample keys from file)
    """
    entries = read_wav_list(list_file)
    wav_files = [e.path for e in entries]
    root_keys = [e.root_key for e in entries]
    vel_ranges = [e.vel_range for e in entries]
    key_ranges = [
        (e.lo_key, e.hi_key) if e.lo_key is not None else None
        for e in entries
    ]

    convert_wavs_to_krz(
        wav_files, output_path, mode, start_key, start_id, name,
        root_key=root_key, root_keys=root_keys, vel_ranges=vel_ranges,
        key_ranges=key_ranges
    )
