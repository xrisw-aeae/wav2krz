"""Main converter orchestration for wav2krz."""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .wav.parser import parse_wav, WavFile
from .krz.writer import KrzWriter
from .krz.sample import create_sample_from_wav, KSample
from .krz.keymap import create_instrument_keymap, create_drumset_keymap, KKeymap
from .krz.program import create_program, KProgram
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


def read_wav_list(list_file: Path) -> List[Tuple[Path, Optional[int]]]:
    """
    Read list of WAV file paths and optional root keys from a text file.

    File format (one entry per line):
        filename.wav           # Uses default root key (60) or WAV metadata
        filename.wav 48        # Specifies root key as MIDI number
        filename.wav C4        # Specifies root key as note name
        # This is a comment

    Args:
        list_file: Path to text file

    Returns:
        List of (Path, root_key) tuples. root_key is None if not specified.
    """
    entries = []
    with open(list_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Split line into parts (filename and optional root key)
            parts = line.split()
            if not parts:
                continue

            wav_path = Path(parts[0])
            # If relative path, make it relative to list file's directory
            if not wav_path.is_absolute():
                wav_path = list_file.parent / wav_path

            root_key = None
            if len(parts) >= 2:
                root_key = parse_note_name(parts[1])
                if root_key is None:
                    raise Wav2KrzError(
                        f"Invalid root key '{parts[1]}' on line {line_num}. "
                        f"Use MIDI number (0-127) or note name (C4, F#3, etc.)"
                    )

            entries.append((wav_path, root_key))

    return entries


def convert_wavs_to_krz(
    wav_files: List[Path],
    output_path: Path,
    mode: str = ConversionMode.SAMPLES,
    start_key: int = 36,
    start_id: int = 200,
    name: Optional[str] = None,
    root_key: Optional[int] = None,
    root_keys: Optional[List[Optional[int]]] = None
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

    Raises:
        Wav2KrzError: On conversion errors
    """
    if not wav_files:
        raise Wav2KrzError("No WAV files to convert")

    writer = KrzWriter()
    samples: List[KSample] = []

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
            # Global override
            sample_root_key = root_key
        elif root_keys is not None and i < len(root_keys) and root_keys[i] is not None:
            # Per-sample root key from list file
            sample_root_key = root_keys[i]
        elif mode == ConversionMode.DRUMSET:
            # For drumset mode, set root key to the assigned MIDI key
            sample_root_key = start_key + len(samples)
            if sample_root_key > 127:
                sample_root_key = 127
        else:
            # Default: 60 (C4), or from WAV smpl chunk (handled in create_sample_from_wav)
            sample_root_key = 60

        sample = create_sample_from_wav(wav_data, sample_name, sample_id, sample_root_key)
        samples.append(sample)
        writer.add_sample(sample)
        sample_id += 1

    # Create keymap and program based on mode
    if mode in (ConversionMode.INSTRUMENT, ConversionMode.DRUMSET):
        base_name = name if name else output_path.stem[:16]
        keymap_id = start_id
        program_id = start_id

        # Check if any samples are stereo
        has_stereo = any(s.is_stereo() for s in samples)

        if mode == ConversionMode.INSTRUMENT:
            # Use all samples for instrument mode - each placed at its root key
            keymap = create_instrument_keymap(samples, keymap_id, base_name)
        else:
            # Drumset: map each sample to consecutive keys
            keymap = create_drumset_keymap(samples, keymap_id, base_name, start_key)

        writer.add_keymap(keymap)

        # Create program referencing the keymap
        program = create_program(keymap, program_id, base_name, has_stereo)
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
        list_file: Path to text file with WAV paths and optional root keys
        output_path: Output .krz file path
        mode: Conversion mode
        start_key: Starting MIDI key for drumset mode
        start_id: Starting object ID
        name: Base name for keymap/program
        root_key: Global root key override (overrides per-sample keys from file)
    """
    entries = read_wav_list(list_file)
    wav_files = [path for path, _ in entries]
    root_keys = [rk for _, rk in entries]

    convert_wavs_to_krz(
        wav_files, output_path, mode, start_key, start_id, name,
        root_key=root_key, root_keys=root_keys
    )
