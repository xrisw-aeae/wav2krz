"""Main converter orchestration for wav2krz."""

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .exceptions import Wav2KrzError
from .krz.for_writer import ForWriter
from .krz.keymap import create_drumset_keymap, create_instrument_keymap
from .krz.program import create_multi_layer_program, create_program
from .krz.sample import KSample, create_sample_from_wav
from .krz.writer import KrzWriter
from .wav.parser import parse_wav

# Output extension to program mode mapping
FORMAT_MODES = {
    '.krz': 2,  # K2000
    '.k25': 3,  # K2500
    '.k26': 4,  # K2600
    '.for': 4,  # Forte/PC3 (uses K2600 program segments internally)
}

# Note names for MIDI-to-note conversion
_NOTE_LETTERS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

# Velocity zone index to name
_VEL_ZONE_NAMES = ['ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff']


def _midi_to_note(midi: int) -> str:
    """Convert MIDI note number to note name. 60 -> 'C4', 36 -> 'C2'."""
    octave = (midi // 12) - 1
    note = _NOTE_LETTERS[midi % 12]
    return f"{note}{octave}"


def _vel_zone_name(zone: tuple) -> str:
    """Convert velocity zone tuple to display string. (0,3) -> 'ppp-mp', (4,4) -> 'mf'."""
    lo, hi = zone
    if lo == hi:
        return _VEL_ZONE_NAMES[lo]
    return f"{_VEL_ZONE_NAMES[lo]}-{_VEL_ZONE_NAMES[hi]}"


class ConversionMode:
    """Conversion mode constants."""
    SAMPLES = "samples"
    INSTRUMENT = "instrument"
    INSTRUMENT_MULTI = "instrument-multi"
    DRUMSET = "drumset"
    DRUMSET_MULTI = "drumset-multi"


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
    keymap_name: Optional[str] = None  # Per-entry keymap name (from @keymap in @group)
    fine_tune: Optional[int] = None  # Fine tuning in cents (-50 to +50)


@dataclass
class DrumGroup:
    """A group of samples sharing the same root key for drumset-multi mode."""
    root_key: int
    lo_key: int  # Layer lower bound
    hi_key: int  # Layer upper bound
    sample_indices: List[int]  # Indices into the original entries list
    vel_layer_map: Optional[dict] = None  # {(start, end): [local_indices...]}
    keymap_name: Optional[str] = None  # Per-group keymap name (from @keymap)


@dataclass
class InstrumentLayer:
    """A velocity layer in instrument-multi mode."""
    vel_zone: Tuple[int, int]  # (lo_zone, hi_zone) 0-based 0-7
    sample_indices: List[int]  # Indices into the section's entries list
    keymap_name: Optional[str] = None  # Per-layer keymap name


@dataclass
class ProgramSection:
    """A parsed @program section from a list file."""
    name: Optional[str] = None
    mode: Optional[str] = None
    keymap_name: Optional[str] = None  # Section-level default keymap name
    entries: List[WavEntry] = field(default_factory=list)


def _build_drum_groups(entries: List[WavEntry]) -> List[DrumGroup]:
    """
    Group WavEntries by root_key for drumset-multi mode.

    Each group of entries sharing the same root_key becomes one layer.
    Layer key range comes from lo_key/hi_key if present, otherwise lo=hi=root_key.

    Args:
        entries: List of WavEntry (all must have root_key set)

    Returns:
        List of DrumGroup sorted by root_key

    Raises:
        Wav2KrzError: If entries lack root_key or exceed 32 groups
    """
    # Group entries by root_key
    groups_by_key = {}  # root_key -> list of (original_index, entry)
    for i, entry in enumerate(entries):
        if entry.root_key is None:
            raise Wav2KrzError(
                f"All samples in drumset-multi mode must have a root key. "
                f"Sample '{entry.path.name}' (index {i}) has no root key."
            )
        groups_by_key.setdefault(entry.root_key, []).append((i, entry))

    if len(groups_by_key) > 32:
        raise Wav2KrzError(
            f"Too many drum groups ({len(groups_by_key)}). "
            f"Kurzweil supports a maximum of 32 layers per program."
        )

    groups = []
    for root_key in sorted(groups_by_key.keys()):
        members = groups_by_key[root_key]
        sample_indices = [idx for idx, _ in members]

        # Derive layer key range
        first_entry = members[0][1]
        if first_entry.lo_key is not None and first_entry.hi_key is not None:
            lo_key = first_entry.lo_key
            hi_key = first_entry.hi_key
        else:
            lo_key = root_key
            hi_key = root_key

        # Build velocity layer map if any entries have velocity ranges
        has_vel = any(e.vel_range is not None for _, e in members)
        vel_layer_map = None
        if has_vel:
            vel_layer_map = {}
            for local_idx, (_, entry) in enumerate(members):
                vr = entry.vel_range if entry.vel_range is not None else (0, 7)
                vel_layer_map.setdefault(vr, []).append(local_idx)

        # Pick up keymap_name from first entry in the group
        group_keymap_name = members[0][1].keymap_name

        groups.append(DrumGroup(
            root_key=root_key,
            lo_key=lo_key,
            hi_key=hi_key,
            sample_indices=sample_indices,
            vel_layer_map=vel_layer_map,
            keymap_name=group_keymap_name,
        ))

    return groups


def _parse_layer_vel_header(parts: List[str], line_num: int) -> Tuple[int, int]:
    """Parse @layer velocity zone header for instrument-multi mode.

    Formats:
        @layer ppp mp       Two separate zone names
        @layer ppp-mp       Hyphenated range
        @layer mf           Single zone (lo == hi)

    Args:
        parts: Split line parts (after @layer keyword)
        line_num: Line number for error messages

    Returns:
        (lo_zone, hi_zone) as 0-based indices (0-7)
    """
    if not parts:
        raise Wav2KrzError(
            f"@layer requires velocity zone(s) on line {line_num}. "
            f"Expected: @layer lo_vel [hi_vel] (e.g., @layer ppp mp)"
        )

    if len(parts) == 1:
        # Could be "ppp-mp" (hyphenated) or "mf" (single zone)
        result = parse_velocity_range(parts[0])
        if result is None:
            raise Wav2KrzError(
                f"Invalid velocity zone '{parts[0]}' in @layer on line {line_num}. "
                f"Expected zone name (ppp, pp, p, mp, mf, f, ff, fff) or range (ppp-mp)."
            )
        return result
    elif len(parts) == 2:
        lo_idx = VELOCITY_NAMES.get(parts[0].upper())
        hi_idx = VELOCITY_NAMES.get(parts[1].upper())
        if lo_idx is None:
            raise Wav2KrzError(
                f"Invalid velocity zone '{parts[0]}' in @layer on line {line_num}."
            )
        if hi_idx is None:
            raise Wav2KrzError(
                f"Invalid velocity zone '{parts[1]}' in @layer on line {line_num}."
            )
        if lo_idx > hi_idx:
            raise Wav2KrzError(
                f"lo_vel ({parts[0]}) must be <= hi_vel ({parts[1]}) "
                f"in @layer on line {line_num}."
            )
        return (lo_idx, hi_idx)
    else:
        raise Wav2KrzError(
            f"Too many parameters in @layer on line {line_num}. "
            f"Expected: @layer lo_vel [hi_vel]"
        )


def _build_instrument_multi_layers(entries: List[WavEntry]) -> List[InstrumentLayer]:
    """Group WavEntries by vel_range for instrument-multi mode.

    Each unique vel_range becomes one layer. All entries must have vel_range set
    (from an @layer directive).

    Args:
        entries: List of WavEntry (all must have vel_range set)

    Returns:
        List of InstrumentLayer sorted by vel_zone (ascending)

    Raises:
        Wav2KrzError: If entries lack vel_range or exceed 32 layers
    """
    layers_by_vel = {}  # vel_range -> list of (original_index, entry)
    for i, entry in enumerate(entries):
        if entry.vel_range is None:
            raise Wav2KrzError(
                f"All samples in instrument-multi mode must be under a @layer "
                f"directive. Sample '{entry.path.name}' (index {i}) has no "
                f"velocity zone."
            )
        layers_by_vel.setdefault(entry.vel_range, []).append((i, entry))

    if len(layers_by_vel) > 32:
        raise Wav2KrzError(
            f"Too many layers ({len(layers_by_vel)}). "
            f"Kurzweil supports a maximum of 32 layers per program."
        )

    layers = []
    for vel_range in sorted(layers_by_vel.keys()):
        members = layers_by_vel[vel_range]
        sample_indices = [idx for idx, _ in members]
        layer_keymap_name = members[0][1].keymap_name
        layers.append(InstrumentLayer(
            vel_zone=vel_range,
            sample_indices=sample_indices,
            keymap_name=layer_keymap_name,
        ))

    return layers


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


def parse_fine_tune(spec: str) -> Optional[int]:
    """Parse a fine-tune specification like 'tune=25' or 'tune=-10'.

    Returns cents value (-50 to +50) or None if not a tune= token.
    Raises Wav2KrzError if format matches but value is out of range.
    """
    match = re.match(r'^tune=(-?\d+)$', spec, re.IGNORECASE)
    if not match:
        return None
    val = int(match.group(1))
    if not (-50 <= val <= 50):
        raise Wav2KrzError(
            f"tune={val} out of range. Must be -50 to +50 cents."
        )
    return val


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


def _parse_group_header(parts: List[str], line_num: int) -> dict:
    """
    Parse a @group header line.

    Format: @group root_key [lo_key hi_key]

    Args:
        parts: Split line parts (after @group)
        line_num: Line number for error messages

    Returns:
        Dict with root_key, lo_key, hi_key
    """
    if not parts:
        raise Wav2KrzError(
            f"@group requires at least a root_key on line {line_num}."
        )

    rk = parse_note_name(parts[0])
    if rk is None:
        raise Wav2KrzError(
            f"Invalid root key '{parts[0]}' in @group on line {line_num}."
        )

    result = {'root_key': rk, 'lo_key': None, 'hi_key': None}

    if len(parts) == 1:
        pass  # root_key only
    elif len(parts) == 3:
        lo = parse_note_name(parts[1])
        hi = parse_note_name(parts[2])
        if lo is None:
            raise Wav2KrzError(
                f"Invalid lokey '{parts[1]}' in @group on line {line_num}."
            )
        if hi is None:
            raise Wav2KrzError(
                f"Invalid hikey '{parts[2]}' in @group on line {line_num}."
            )
        if lo > hi:
            raise Wav2KrzError(
                f"lokey ({parts[1]}) must be <= hikey ({parts[2]}) in @group on line {line_num}."
            )
        result['lo_key'] = lo
        result['hi_key'] = hi
    elif len(parts) == 2:
        raise Wav2KrzError(
            f"@group has lokey but missing hikey on line {line_num}. "
            f"Expected: @group root_key [lokey hikey]"
        )
    else:
        raise Wav2KrzError(
            f"Too many parameters in @group on line {line_num}. "
            f"Expected: @group root_key [lokey hikey]"
        )

    return result


def _parse_sample_line(parts: List[str], line_num: int, list_file: Path,
                       group: dict = None) -> WavEntry:
    """
    Parse a single sample line.

    When inside a @group, sample lines are: filename [velocity]
    When outside a @group, sample lines are: filename [root_key] [lokey hikey] [velocity]

    Args:
        parts: Split line parts
        line_num: Line number for error messages
        list_file: Path to list file (for resolving relative paths)
        group: Current group context, or None

    Returns:
        WavEntry
    """
    wav_path = Path(parts[0])
    if not wav_path.is_absolute():
        wav_path = list_file.parent / wav_path

    entry = WavEntry(path=wav_path)

    # Extract tune=N token from parts (can appear anywhere after filename)
    filtered_parts = []
    for p in parts[1:]:
        ft = parse_fine_tune(p)
        if ft is not None:
            if entry.fine_tune is not None:
                raise Wav2KrzError(
                    f"Duplicate tune= on line {line_num}."
                )
            entry.fine_tune = ft
        else:
            filtered_parts.append(p)
    parts = [parts[0]] + filtered_parts

    if group is not None:
        # Inside a @group: inherit root_key/lo_key/hi_key, only velocity is optional
        entry.root_key = group['root_key']
        entry.lo_key = group['lo_key']
        entry.hi_key = group['hi_key']

        remaining = parts[1:]
        if remaining and parse_velocity_range(remaining[-1]) is not None:
            entry.vel_range = parse_velocity_range(remaining[-1])
            remaining = remaining[:-1]

        if remaining:
            raise Wav2KrzError(
                f"Unexpected parameter '{remaining[0]}' on line {line_num}. "
                f"Inside @group, sample lines should be: filename [velocity]"
            )
    else:
        # Outside any @group: full column format
        remaining = parts[1:]
        if remaining and parse_velocity_range(remaining[-1]) is not None:
            entry.vel_range = parse_velocity_range(remaining[-1])
            remaining = remaining[:-1]

        if len(remaining) == 0:
            pass
        elif len(remaining) == 1:
            rk = parse_note_name(remaining[0])
            if rk is None:
                raise Wav2KrzError(
                    f"Invalid root key '{remaining[0]}' on line {line_num}. "
                    f"Expected note name (C4) or MIDI number (60)."
                )
            entry.root_key = rk
        elif len(remaining) == 3:
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
            rk = parse_note_name(remaining[0])
            n2 = parse_note_name(remaining[1])
            if rk is None:
                raise Wav2KrzError(
                    f"Invalid root key '{remaining[0]}' on line {line_num}."
                )
            if n2 is None:
                raise Wav2KrzError(
                    f"Unknown parameter '{remaining[1]}' on line {line_num}. "
                    f"Expected note name (C4) or MIDI number (60)."
                )
            entry.root_key = rk
            if n2 >= rk:
                entry.lo_key = rk
                entry.hi_key = n2
            else:
                entry.lo_key = n2
                entry.hi_key = rk
        else:
            raise Wav2KrzError(
                f"Too many parameters on line {line_num}. "
                f"Expected: filename [root_key] [lokey hikey] [velocity]"
            )

    return entry


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

    Group headers reduce repetition:
        @group C2 A#1 C2               # Set root=C2, lo=A#1, hi=C2
        filename.wav f-fff              # Inherits root/lo/hi from group
        filename.wav ppp-p              # Same group

        @group C#2                      # Set root=C#2 only (no lo/hi)
        filename.wav                    # Inherits root from group

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
    current_group = None

    with open(list_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            try:
                parts = shlex.split(line)
            except ValueError as e:
                raise Wav2KrzError(f"Parse error on line {line_num}: {e}")
            if not parts:
                continue

            # Check for @group/@layer directive
            if parts[0].lower() in ('@group', '@layer'):
                current_group = _parse_group_header(parts[1:], line_num)
                continue

            entry = _parse_sample_line(parts, line_num, list_file, current_group)
            entries.append(entry)

    return entries


# Valid modes for @program directive
_VALID_MODES = {
    ConversionMode.SAMPLES,
    ConversionMode.INSTRUMENT,
    ConversionMode.INSTRUMENT_MULTI,
    ConversionMode.DRUMSET,
    ConversionMode.DRUMSET_MULTI,
}


def _parse_directive_name(line: str, directive: str, line_num: int) -> List[str]:
    """
    Parse a @program or @keymap line using shlex for quoted name support.

    Returns list of tokens after the directive keyword.
    """
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        raise Wav2KrzError(
            f"Invalid quoting in {directive} on line {line_num}: {e}"
        )
    # tokens[0] is the directive itself
    return tokens[1:]


def read_program_list(list_file: Path, cli_mode: str = None) -> List[ProgramSection]:
    """
    Read a list file with optional @program and @keymap directives.

    Returns a list of ProgramSection objects. Files without @program
    produce a single section with name=None and mode=None.

    Directives:
        @program "Name" [mode]  — start a new program section
        @keymap "Name"          — name the keymap (section-level or per-group/layer)
        @group root [lo hi]     — start a drum group (resets per-group keymap)
        @layer ...              — alias for @group; in instrument-multi mode,
                                  takes velocity zones: @layer lo_vel [hi_vel]

    Args:
        list_file: Path to text file

    Returns:
        List of ProgramSection objects
    """
    sections = []
    current_section = ProgramSection(mode=cli_mode)
    current_group = None  # Key-based group context (drumset-multi, etc.)
    current_layer_vel = None  # Velocity layer context (instrument-multi)
    group_keymap_name = None

    with open(list_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            try:
                parts = shlex.split(line)
            except ValueError as e:
                raise Wav2KrzError(f"Parse error on line {line_num}: {e}")
            if not parts:
                continue

            keyword = parts[0].lower()

            if keyword == '@program':
                # Finalize current section if it has entries
                if current_section.entries or current_section.name is not None:
                    sections.append(current_section)
                # Parse @program "Name" [mode]
                args = _parse_directive_name(line, '@program', line_num)
                if not args:
                    raise Wav2KrzError(
                        f"@program requires a name on line {line_num}."
                    )
                prog_name = args[0]
                prog_mode = None
                if len(args) >= 2:
                    prog_mode = args[1].lower()
                    if prog_mode not in _VALID_MODES:
                        raise Wav2KrzError(
                            f"Invalid mode '{args[1]}' in @program on line {line_num}. "
                            f"Valid modes: {', '.join(sorted(_VALID_MODES))}"
                        )
                if len(args) > 2:
                    raise Wav2KrzError(
                        f"Too many parameters in @program on line {line_num}. "
                        f"Expected: @program \"Name\" [mode]"
                    )
                current_section = ProgramSection(name=prog_name, mode=prog_mode)
                current_group = None
                current_layer_vel = None
                group_keymap_name = None
                continue

            if keyword == '@keymap':
                args = _parse_directive_name(line, '@keymap', line_num)
                if not args:
                    raise Wav2KrzError(
                        f"@keymap requires a name on line {line_num}."
                    )
                if len(args) > 1:
                    raise Wav2KrzError(
                        f"Too many parameters in @keymap on line {line_num}. "
                        f"Expected: @keymap \"Name\""
                    )
                km_name = args[0]
                if current_group is not None or current_layer_vel is not None:
                    # Inside a @group/@layer: set per-group/layer keymap name
                    group_keymap_name = km_name
                else:
                    # Section level: set default keymap name
                    current_section.keymap_name = km_name
                continue

            if keyword in ('@group', '@layer'):
                if current_section.mode == ConversionMode.INSTRUMENT_MULTI:
                    # In instrument-multi: parse velocity zone args
                    current_layer_vel = _parse_layer_vel_header(parts[1:], line_num)
                    current_group = None
                else:
                    # In other modes: parse note name args (existing behavior)
                    current_group = _parse_group_header(parts[1:], line_num)
                    current_layer_vel = None
                group_keymap_name = None
                continue

            # Sample line
            if current_layer_vel is not None:
                # Inside @layer in instrument-multi: parse as ungrouped,
                # then apply velocity zone from @layer
                entry = _parse_sample_line(parts, line_num, list_file, group=None)
                entry.vel_range = current_layer_vel
            else:
                entry = _parse_sample_line(parts, line_num, list_file, current_group)
            if group_keymap_name is not None:
                entry.keymap_name = group_keymap_name
            current_section.entries.append(entry)

    # Finalize last section
    if current_section.entries or current_section.name is not None:
        sections.append(current_section)

    return sections


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
    key_ranges: Optional[List[Optional[Tuple[int, int]]]] = None,
    fine_tunes: Optional[List[Optional[int]]] = None,
    entries: Optional[List[WavEntry]] = None,
    verbose: bool = True
) -> None:
    """
    Convert WAV files to Kurzweil .krz format.

    Args:
        wav_files: List of paths to WAV files
        output_path: Output .krz file path
        mode: Conversion mode (samples, instrument, drumset, drumset-multi)
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
        fine_tunes: Per-sample fine tuning in cents (-50 to +50).
                    Applied to Soundfilehead max_pitch.
        entries: Original WavEntry list (used for drumset-multi grouping)

    Raises:
        Wav2KrzError: On conversion errors
    """
    if not wav_files:
        raise Wav2KrzError("No WAV files to convert")

    if output_path.suffix.lower() == '.for':
        writer = ForWriter()
    else:
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

        # Determine root key: global override > per-sample > drumset > default
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

        # Apply fine tuning to max_pitch in all soundfile headers
        ft = fine_tunes[i] if fine_tunes is not None and i < len(fine_tunes) else None
        if ft:
            for sh in sample.headers:
                sh.max_pitch -= ft

        samples.append(sample)
        writer.add_sample(sample)
        sample_id += 1

        if verbose:
            stereo_str = "stereo" if sample.is_stereo() else "mono"
            depth_str = " 24→16-bit" if wav_data.bits_per_sample == 24 else ""
            tune_str = f", tune={ft}" if ft else ""
            print(f"  Sample: {sample_name} ({_midi_to_note(sample_root_key)}, {stereo_str}{depth_str}{tune_str})")

        # Track velocity range for this sample
        vr = None
        if vel_ranges is not None and i < len(vel_ranges):
            vr = vel_ranges[i]
        sample_vel_ranges.append(vr)

    num_keymaps = 0
    num_programs = 0

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
        num_keymaps = 1

        if verbose:
            print(f"  Keymap: {base_name} ({len(samples)} samples)")

        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)
        program = create_program(keymap, program_id, base_name, has_stereo, mode=pgm_mode)
        writer.add_program(program)
        num_programs = 1

    elif mode == ConversionMode.DRUMSET_MULTI:
        # Build entries if not provided (construct from flat lists)
        if entries is None:
            entries = []
            for i, wf in enumerate(wav_files):
                e = WavEntry(path=wf)
                if root_keys and i < len(root_keys):
                    e.root_key = root_keys[i]
                if vel_ranges and i < len(vel_ranges):
                    e.vel_range = vel_ranges[i]
                if key_ranges and i < len(key_ranges) and key_ranges[i] is not None:
                    e.lo_key, e.hi_key = key_ranges[i]
                entries.append(e)

        groups = _build_drum_groups(entries)
        base_name = name if name else output_path.stem[:16]
        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)

        keymaps = []
        stereo_flags = []
        layer_key_ranges = []

        for group_idx, group in enumerate(groups):
            group_samples = [samples[i] for i in group.sample_indices]
            keymap_id = start_id + group_idx

            has_group_stereo = any(s.is_stereo() for s in group_samples)
            stereo_flags.append(has_group_stereo)
            layer_key_ranges.append((group.lo_key, group.hi_key))

            km = create_instrument_keymap(
                group_samples, keymap_id, base_name,
                vel_layer_map=group.vel_layer_map)
            keymaps.append(km)
            writer.add_keymap(km)

            if verbose:
                print(f"  Keymap: {base_name} ({len(group_samples)} samples)")

        num_keymaps = len(keymaps)

        program_id = start_id
        program = create_multi_layer_program(
            keymaps, program_id, base_name,
            stereo_flags=stereo_flags,
            key_ranges=layer_key_ranges,
            mode=pgm_mode)
        writer.add_program(program)
        num_programs = 1

        if verbose:
            for layer_num, group in enumerate(groups, 1):
                lo_note = _midi_to_note(group.lo_key)
                hi_note = _midi_to_note(group.hi_key)
                print(f"  Layer {layer_num}: {base_name}, keys {lo_note}..{hi_note}")

    # Write the .krz file
    writer.write(output_path)

    if verbose:
        parts = [f"{len(samples)} samples"]
        if num_keymaps:
            parts.append(f"{num_keymaps} keymaps")
        if num_programs:
            parts.append(f"{num_programs} programs")
        print(f"Created {output_path} ({', '.join(parts)})")


def _process_section(
    section: ProgramSection,
    writer,
    next_sample_id: int,
    next_keymap_id: int,
    next_program_id: int,
    output_path: Path,
    cli_mode: str,
    cli_name: Optional[str],
    cli_root_key: Optional[int],
    cli_start_key: int,
    verbose: bool = True,
) -> Tuple[int, int, int]:
    """
    Process a single ProgramSection, adding objects to the writer.

    Args:
        section: The program section to process
        writer: KrzWriter to add objects to
        next_sample_id: Next available sample ID
        next_keymap_id: Next available keymap ID
        next_program_id: Next available program ID
        output_path: Output file path (for format detection and default naming)
        cli_mode: CLI mode fallback
        cli_name: CLI name fallback
        cli_root_key: CLI root key override
        cli_start_key: Starting key for drumset modes
        verbose: Print verbose output

    Returns:
        Tuple of (next_sample_id, next_keymap_id, next_program_id)
    """
    entries = section.entries
    if not entries:
        return next_sample_id, next_keymap_id, next_program_id

    mode = section.mode or cli_mode
    # Program name: section name > CLI name > output filename
    program_name = section.name or cli_name or output_path.stem[:16]
    # Keymap name: section keymap_name > program name
    default_keymap_name = section.keymap_name or program_name

    if verbose and mode != ConversionMode.SAMPLES:
        print(f'Program "{program_name}" ({mode}):')

    wav_files = [e.path for e in entries]
    root_keys = [e.root_key for e in entries]
    vel_ranges = [e.vel_range for e in entries]
    fine_tunes = [e.fine_tune for e in entries]
    key_ranges = [
        (e.lo_key, e.hi_key) if e.lo_key is not None else None
        for e in entries
    ]

    # Parse all WAV files and create samples
    samples: List[KSample] = []
    sample_vel_ranges: List[Optional[Tuple[int, int]]] = []
    sample_id = next_sample_id

    for i, wav_path in enumerate(wav_files):
        if not wav_path.exists():
            raise Wav2KrzError(f"WAV file not found: {wav_path}")

        try:
            wav_data = parse_wav(wav_path)
        except Exception as e:
            raise Wav2KrzError(f"Error parsing {wav_path}: {e}")

        sample_name = wav_path.stem[:16]

        if cli_root_key is not None:
            sample_root_key = cli_root_key
        elif root_keys[i] is not None:
            sample_root_key = root_keys[i]
        elif mode == ConversionMode.DRUMSET:
            sample_root_key = cli_start_key + len(samples)
            if sample_root_key > 127:
                sample_root_key = 127
        else:
            sample_root_key = 60

        sample = create_sample_from_wav(wav_data, sample_name, sample_id, sample_root_key)

        # Apply fine tuning to max_pitch in all soundfile headers
        ft = fine_tunes[i]
        if ft:
            for sh in sample.headers:
                sh.max_pitch -= ft

        samples.append(sample)
        writer.add_sample(sample)
        sample_id += 1

        if verbose:
            stereo_str = "stereo" if sample.is_stereo() else "mono"
            depth_str = " 24→16-bit" if wav_data.bits_per_sample == 24 else ""
            tune_str = f", tune={ft}" if ft else ""
            print(f"  Sample: {sample_name} ({_midi_to_note(sample_root_key)}, {stereo_str}{depth_str}{tune_str})")

        vr = vel_ranges[i]
        sample_vel_ranges.append(vr)

    next_sample_id = sample_id

    # Create keymap and program based on mode
    if mode in (ConversionMode.INSTRUMENT, ConversionMode.DRUMSET):
        base_name = default_keymap_name
        keymap_id = next_keymap_id
        program_id = next_program_id

        has_stereo = any(s.is_stereo() for s in samples)

        has_vel_layers = any(vr is not None for vr in sample_vel_ranges)
        vel_layer_map = None
        if has_vel_layers:
            vel_layer_map = {}
            for idx, vr in enumerate(sample_vel_ranges):
                if vr is None:
                    vr = (0, 7)
                vel_layer_map.setdefault(vr, []).append(idx)

        if mode == ConversionMode.INSTRUMENT:
            keymap = create_instrument_keymap(
                samples, keymap_id, base_name, vel_layer_map=vel_layer_map,
                key_ranges=key_ranges)
        else:
            drum_key_assignments = None
            if any(rk is not None for rk in root_keys):
                next_auto_key = cli_start_key
                drum_key_assignments = []
                for rk in root_keys:
                    if rk is not None:
                        drum_key_assignments.append(rk)
                    else:
                        drum_key_assignments.append(next_auto_key)
                        next_auto_key += 1

            keymap = create_drumset_keymap(
                samples, keymap_id, base_name, cli_start_key,
                vel_layer_map=vel_layer_map,
                key_assignments=drum_key_assignments,
                key_ranges=key_ranges)

        writer.add_keymap(keymap)
        next_keymap_id = keymap_id + 1

        if verbose:
            print(f"  Keymap: {base_name} ({len(samples)} samples)")

        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)
        program = create_program(
            keymap, program_id, program_name, has_stereo, mode=pgm_mode)
        writer.add_program(program)
        next_program_id = program_id + 1

    elif mode == ConversionMode.DRUMSET_MULTI:
        groups = _build_drum_groups(entries)
        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)

        keymaps = []
        stereo_flags = []
        layer_key_ranges = []

        for group_idx, group in enumerate(groups):
            group_samples = [samples[i] for i in group.sample_indices]
            keymap_id = next_keymap_id + group_idx

            has_group_stereo = any(s.is_stereo() for s in group_samples)
            stereo_flags.append(has_group_stereo)
            layer_key_ranges.append((group.lo_key, group.hi_key))

            # Keymap name: group keymap_name > section keymap_name > program name
            km_name = group.keymap_name or default_keymap_name

            km = create_instrument_keymap(
                group_samples, keymap_id, km_name,
                vel_layer_map=group.vel_layer_map)
            keymaps.append(km)
            writer.add_keymap(km)

            if verbose:
                print(f"  Keymap: {km_name} ({len(group_samples)} samples)")

        next_keymap_id += len(groups)

        program_id = next_program_id
        program = create_multi_layer_program(
            keymaps, program_id, program_name,
            stereo_flags=stereo_flags,
            key_ranges=layer_key_ranges,
            mode=pgm_mode)
        writer.add_program(program)
        next_program_id = program_id + 1

        if verbose:
            for layer_num, group in enumerate(groups, 1):
                lo_note = _midi_to_note(group.lo_key)
                hi_note = _midi_to_note(group.hi_key)
                km = group.keymap_name or default_keymap_name
                print(f"  Layer {layer_num}: {km}, keys {lo_note}..{hi_note}")

    elif mode == ConversionMode.INSTRUMENT_MULTI:
        layers = _build_instrument_multi_layers(entries)
        pgm_mode = FORMAT_MODES.get(output_path.suffix.lower(), 2)

        keymaps = []
        stereo_flags = []
        layer_key_ranges = []
        layer_vel_zones = []

        for layer_idx, layer in enumerate(layers):
            layer_samples = [samples[i] for i in layer.sample_indices]
            keymap_id = next_keymap_id + layer_idx

            has_layer_stereo = any(s.is_stereo() for s in layer_samples)
            stereo_flags.append(has_layer_stereo)
            layer_key_ranges.append((0, 127))  # Full keyboard per layer
            layer_vel_zones.append(layer.vel_zone)

            # Per-sample key ranges within this layer
            layer_sample_key_ranges = [
                (entries[i].lo_key, entries[i].hi_key)
                if entries[i].lo_key is not None else None
                for i in layer.sample_indices
            ]

            km_name = layer.keymap_name or default_keymap_name

            km = create_instrument_keymap(
                layer_samples, keymap_id, km_name,
                key_ranges=layer_sample_key_ranges)
            keymaps.append(km)
            writer.add_keymap(km)

            if verbose:
                print(f"  Keymap: {km_name} ({len(layer_samples)} samples)")

        next_keymap_id += len(layers)

        program_id = next_program_id
        program = create_multi_layer_program(
            keymaps, program_id, program_name,
            stereo_flags=stereo_flags,
            key_ranges=layer_key_ranges,
            vel_zones=layer_vel_zones,
            mode=pgm_mode)
        writer.add_program(program)
        next_program_id = program_id + 1

        if verbose:
            for layer_num, layer in enumerate(layers, 1):
                km_name = layer.keymap_name or default_keymap_name
                lo_note = _midi_to_note(layer_key_ranges[layer_num - 1][0])
                hi_note = _midi_to_note(layer_key_ranges[layer_num - 1][1])
                vel_str = _vel_zone_name(layer.vel_zone)
                print(f"  Layer {layer_num}: {km_name}, keys {lo_note}..{hi_note}, vel {vel_str}")

    return next_sample_id, next_keymap_id, next_program_id


def convert_from_list_file(
    list_file: Path,
    output_path: Path,
    mode: str = ConversionMode.SAMPLES,
    start_key: int = 36,
    start_id: int = 200,
    name: Optional[str] = None,
    root_key: Optional[int] = None,
    verbose: bool = True
) -> None:
    """
    Convert WAV files listed in a text file to .krz format.

    Supports @program and @keymap directives for multi-program files.
    Files without @program work exactly as before.

    Args:
        list_file: Path to text file with WAV paths, root keys, velocity ranges
        output_path: Output .krz file path
        mode: Conversion mode
        start_key: Starting MIDI key for drumset mode
        start_id: Starting object ID
        name: Base name for keymap/program
        root_key: Global root key override (overrides per-sample keys from file)
        verbose: Print verbose output (default True)
    """
    sections = read_program_list(list_file, cli_mode=mode)

    if not sections:
        raise Wav2KrzError("No entries found in list file")

    if output_path.suffix.lower() == '.for':
        writer = ForWriter()
    else:
        writer = KrzWriter()
    next_sample_id = start_id
    next_keymap_id = start_id
    next_program_id = start_id

    for section in sections:
        next_sample_id, next_keymap_id, next_program_id = _process_section(
            section, writer, next_sample_id, next_keymap_id, next_program_id,
            output_path, cli_mode=mode, cli_name=name,
            cli_root_key=root_key, cli_start_key=start_key,
            verbose=verbose)

    writer.write(output_path)

    if verbose:
        num_samples = next_sample_id - start_id
        num_keymaps = next_keymap_id - start_id
        num_programs = next_program_id - start_id
        parts = [f"{num_samples} samples"]
        if num_keymaps:
            parts.append(f"{num_keymaps} keymaps")
        if num_programs:
            parts.append(f"{num_programs} programs")
        print(f"Created {output_path} ({', '.join(parts)})")
