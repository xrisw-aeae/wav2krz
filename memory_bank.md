# wav2krz Memory Bank

## Project Overview

**wav2krz** is a Python command-line tool that converts WAV audio files into Kurzweil K2000/K2500/K2600 `.krz` soundfile format. It produces binary `.krz` files that can be loaded directly onto Kurzweil hardware samplers via SCSI, floppy, or SmartMedia.

**Primary Purpose:**
- Convert one or more WAV files into a single `.krz` file
- Support four conversion modes: raw samples, pitched instrument, drumset, and multi-layer drumset
- Handle mono/stereo, 8-bit/16-bit PCM WAV input
- Read WAV `smpl` chunk metadata (root key, loop points)
- Support velocity layering via list file syntax

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.12 |
| Dependencies | None (pure stdlib) |
| Test Framework | pytest |
| Build/Run | `python -m wav2krz` |
| License | (not yet specified) |

## Project Structure

```
/wav2krz/
├── __init__.py              # Package init, version "1.0.0"
├── __main__.py              # Entry point: python -m wav2krz
├── cli.py                   # Argument parsing and CLI orchestration
├── converter.py             # Main conversion logic, list file parsing
├── exceptions.py            # Custom exception hierarchy
├── krz/                     # Kurzweil .krz format module
│   ├── __init__.py          # Re-exports all krz types
│   ├── hash.py              # KHash: object ID/type hashing (from KurzFiler)
│   ├── header.py            # KrzHeader: 32-byte PRAM file header
│   ├── keymap.py            # KKeymap, VeloLevel, KeymapEntry + fill algorithm
│   ├── program.py           # KProgram, Segment structures
│   ├── sample.py            # KSample, Soundfilehead, Envelope, byte-swapping
│   └── writer.py            # KrzWriter: assembles complete .krz file
├── wav/                     # WAV parsing module
│   ├── __init__.py          # Re-exports WavFile, parse_wav
│   └── parser.py            # RIFF/WAV chunk parser, smpl chunk support
├── tests/                   # Test suite
│   ├── __init__.py
│   ├── helpers.py           # make_wav() - generates test WAV files
│   ├── test_converter.py    # Converter integration tests
│   ├── test_keymap.py       # Keymap/fill algorithm tests
│   ├── test_krz_structures.py  # KSample, KKeymap, KProgram unit tests
│   └── test_wav_parser.py   # WAV parser tests
├── analyze_for.py           # .for format analysis script (research)
├── analyze_for2.py          # .for format analysis script v2 (research)
├── analyze_for3.py          # .for format analysis script v3 (research)
├── compare_formats.py       # .krz vs .for comparison tool (research)
├── make_control.py          # Generates control_sine.wav for testing
├── control_sine.wav         # Reference WAV file
├── control_sine.krz         # Reference .krz output
└── control_sine.for         # Reference .for file (Forte format)
```

## Architecture

### Data Flow

```
WAV files → parse_wav() → WavFile → create_sample_from_wav() → KSample
                                                                   ↓
List file → read_wav_list() → WavEntry[] → convert_wavs_to_krz() → KrzWriter → .krz file
                                                                   ↑
                                           create_*_keymap() → KKeymap
                                           create_program()  → KProgram
```

### Conversion Modes

| Mode | Description | Objects Created |
|------|-------------|-----------------|
| `samples` | Raw sample data only | KSample(s) |
| `instrument` | Pitched across keyboard, gaps filled | KSample(s) + KKeymap + KProgram |
| `drumset` | One sample per key, consecutive or assigned | KSample(s) + KKeymap + KProgram |
| `drumset-multi` | Each group gets own keymap + layer with key range | KSample(s) + KKeymap(s) + KProgram |

### .krz File Format (Big-Endian)

```
┌─────────────────────────────┐
│ 32-byte header              │  magic "PRAM", osize, version 353
├─────────────────────────────┤
│ Object blocks               │  Each: 4-byte negative size prefix
│   - Samples (type 38)       │        + hash(2) + size(2) + name + data
│   - Keymaps (type 37)       │        + padding to 4-byte boundary
│   - Programs (type 36)      │
│   - Terminator (0x00000000) │
├─────────────────────────────┤
│ Sample audio data           │  16-bit big-endian PCM, concatenated
└─────────────────────────────┘
```

**Object hashing:** `hash = (type << 10) + id` for types <= 42.

## Key Files Reference

### CLI & Orchestration

| File | Purpose |
|------|---------|
| `cli.py` | argparse CLI: modes, start-key, root-key, start-id, name, verbose |
| `converter.py` | `convert_wavs_to_krz()`, `convert_from_list_file()`, `read_wav_list()` |
| `exceptions.py` | `Wav2KrzError` hierarchy: `WavParseError`, `UnsupportedWavFormat`, `KrzWriteError`, `InvalidNameError` |

### WAV Parsing

| File | Purpose |
|------|---------|
| `wav/parser.py` | RIFF chunk walker, `fmt` parser (PCM only), `smpl` chunk parser, `SampleInfo` dataclass |

### KRZ Object Model

| File | Key Classes |
|------|-------------|
| `krz/hash.py` | `KHash` - ID/type ↔ hash encoding (matches KurzFiler's `KHash.java`) |
| `krz/header.py` | `KrzHeader` - 32-byte PRAM header with `osize` pointer to sample data |
| `krz/sample.py` | `KSample`, `Soundfilehead` (32-byte audio header), `Envelope` (12 bytes), `swap_bytes()` LE→BE |
| `krz/keymap.py` | `KKeymap`, `VeloLevel`, `KeymapEntry`, `fill_spaces_between_samples()` fill algorithm |
| `krz/program.py` | `KProgram`, `Segment` (tag-based blocks: PGM, LYR, ENV, CAL, HOB, etc.) |
| `krz/writer.py` | `KrzWriter` - assembles header + sorted objects + sample data into complete file |

## Keymap Fill Algorithm

The `fill_spaces_between_samples()` method in `VeloLevel` fills unassigned keys by alternating upward and downward propagation passes. Each pass extends assigned samples by one key in each direction from each boundary, creating balanced splits between neighboring samples. Runs until no gaps remain.

## Velocity Layering

The list file supports per-sample velocity ranges:
- Numeric: `v1-3` (zones 1-8 → indices 0-7)
- Named: `ppp`, `mp`, `mf-fff`
- Zone names: PPP, PP, P, MP, MF, F, FF, FFF (indices 0-7)

Samples sharing the same velocity range go into one `VeloLevel`. The 8 velocity zones (0-7) map to velocity levels via `velocity_mapping[8]`.

## List File Format

```
# Comments start with #
filename.wav                    # Defaults for everything
filename.wav C4                 # Root key (note name or MIDI number)
filename.wav C4 v1-3            # Root key + velocity range
filename.wav 60 ppp-p           # MIDI number + named velocity range
filename.wav C4 C3 C5           # Root key, lokey, hikey (explicit key range)
filename.wav C4 C3 C5 v1-3      # Root key, lokey, hikey, velocity
```

Column order: `filename [root_key] [lokey hikey] [velocity]`

**Program sections** define multiple programs in one file:
```
@program "Grand Piano" instrument    # Start new program section
@keymap "Piano Map"                  # Name the keymap
piano_c4.wav C4
piano_d4.wav D4

@program "Drum Kit" drumset-multi    # Another program with different mode
@group C2 A#1 C2
@keymap "Kick"                       # Per-group keymap name
kick.wav f-fff
```

`@program "Name" [mode]` — starts a new program section
- Name is required (quoted if spaces)
- Mode is optional; falls back to CLI `--mode`
- Resets `@group` and `@keymap` context

`@keymap "Name"` — names the keymap
- At section level: default keymap name for the section
- After `@group` (in drumset-multi): names that group's keymap
- Each new `@group` resets the per-group keymap name
- Falls back to program name, then output filename

Files without `@program` work exactly as before (single implicit section).

**Group headers** reduce repetition (available in all modes):
```
@group C2 A#1 C2           # Set root=C2, lo=A#1, hi=C2 for following samples
filename.wav f-fff          # Inherits root/lo/hi from group
filename.wav ppp-p          # Same group

@group C#2                  # Set root=C#2 only (no lo/hi)
filename.wav                # Inherits root from group
```

`@group` syntax: `@group root_key [lo_key hi_key]`
- Inside a `@group`, sample lines are: `filename [velocity]`
- A new `@group` line starts a new group context
- Lines outside any `@group` use the full column format

Additional rules:
- lokey/hikey are optional but must appear together (both or neither)
- When ALL samples have explicit ranges, each sample fills only within its range
- When any sample lacks explicit range, standard fill algorithm is used for all
- Relative paths resolve from the list file's directory.

## CLI Usage

```bash
# Samples only
python -m wav2krz wavlist.txt output.krz --mode samples

# Instrument (pitched)
python -m wav2krz wavlist.txt output.krz --mode instrument

# Drumset starting at C1
python -m wav2krz wavlist.txt output.krz --mode drumset --start-key 36

# Direct WAV files
python -m wav2krz --wav kick.wav snare.wav --output drums.krz --mode drumset

# Options
#   --start-id 200     Starting object ID (default 200)
#   --root-key 60      Global root key override
#   --name "My Inst"   Base name for keymap/program
#   --verbose           Verbose output
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_converter.py -v
```

Test helper `make_wav()` generates PCM WAV files with configurable frequency, duration, sample rate, bit depth, channels, loop points, and root key.

## Key Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `T_PROGRAM` | 36 | Program object type |
| `T_KEYMAP` | 37 | Keymap object type |
| `T_SAMPLE` | 38 | Sample object type |
| `SOFTWARE_VERSION` | 353 | Written to header rest[2] |
| Magic | `PRAM` | .krz file magic bytes |
| Max name length | 16 | All object names |
| Default start ID | 200 | CLI default for --start-id |
| Default root key | 60 | Middle C (C4) |

## Supported WAV Formats

| Format | Supported |
|--------|-----------|
| 16-bit mono PCM | Yes |
| 16-bit stereo PCM | Yes (split to L/R headers) |
| 8-bit mono PCM | Yes (upconverted to 16-bit) |
| 8-bit stereo PCM | No |
| Non-PCM (compressed) | No |

## Relationship to KurzFiler

wav2krz was developed as a standalone Python reimplementation of parts of the Java KurzFiler project. The `krz/` module's object model (`KHash`, `KSample`, `KKeymap`, `KProgram`, `KrzWriter`) is a Python translation of KurzFiler's `kurzobjects/` and `filemethods/kurzweil/` packages. The hash generation algorithm in `KHash` matches `KHash.java` exactly.

## Future Improvements

1. ~~**Loop points from WAV metadata**~~ - Done. Loop points from `smpl` chunks are validated (bounds-clamped), sampledata is truncated to match `sample_end`, and multi-sample offset alignment is correct.
2. ~~**Per-sample lokey/hikey attributes**~~ - Done. List file now supports optional lokey/hikey columns for explicit keyboard range control. When all samples have explicit ranges, each fills only within its bounds; otherwise standard fill is used.
3. ~~**Multi-layer drumset mode**~~ - Done. `drumset-multi` mode groups samples by root key (using `@group` directives), creates per-group keymaps, and produces a single program with multiple layers. Each layer's LYR segment restricts its key range so only the correct keymap sounds per key. Supports velocity layering within groups and explicit key ranges via `@group lo_key hi_key`. Max 32 groups (Kurzweil layer limit).
4. ~~**Improve naming of programs and keymaps**~~ - Done. `@keymap "Name"` directive names keymaps explicitly. At section level sets default; inside `@group` sets per-group keymap name. Falls back to program name, then output filename.
5. ~~**Multiple programs per .krz file**~~ - Done. `@program "Name" [mode]` directive starts a new program section. Each section gets its own program, keymaps, and samples with separate ID counters. Files without `@program` work exactly as before.
6. **.for format output** (backburner) - Support Kurzweil Forte/PC3 native format (`COOL` magic) as an output option; requires reverse-engineering the object framing
7. **Compact keymap / sample merging** (backburner) - Merge individual samples into a single multi-subsample object, then strip per-entry sample IDs from the keymap (saves ~256 bytes per velocity level). Matches KurzFiler's `compactKeymap()` behavior.
