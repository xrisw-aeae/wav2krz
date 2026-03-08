# wav2krz

Collect WAV files into Kurzweil soundfile format (.krz, .k25, .k26, and .for) with automatic keymap and program creation.

Generates ready-to-load files for the Kurzweil K2000, K2500, K2600, and Forte series synthesizers. This library would not exist without the excellent (Kurzfiler library)[https://sourceforge.net/projects/kurzfiler/].

I own a Kurzweil Forte 7, which I love, but I find it tedious and error prone to import samples and create and edit keymaps on the unit itself. I wanted a straightforward way to organize samples into keymaps and layers via a simple text file format for instruments and drumsets so can spend less time arranging samples and move on to sound design possibilities with those samples, keymaps, layers, and programs more quickly.

## Installation

Requires Python 3.10+.

```
git clone <repo-url>
cd wav2krz
pip install -e .
```

This installs the `wav2krz` command.

## Quick Start

```sh
# Single WAV to a playable instrument (pitched across the full keyboard)
wav2krz --wav piano_c4.wav piano.krz

# Multiple WAVs as a drumset (each sample on a consecutive key from C1)
wav2krz --wav kick.wav snare.wav hihat.wav drums.krz --mode drumset

# From a list file
wav2krz samples.txt output.krz
```

## Output Formats

The output format is determined by the file extension:

| Extension | Target               |
|-----------|----------------------|
| `.krz`    | K2000 / K2000R       |
| `.k25`    | K2500 / K2500R       |
| `.k26`    | K2600 / K2661        |
| `.for`    | Forte                |

```sh
wav2krz --wav pad.wav pad.krz    # K2000 format
wav2krz --wav pad.wav pad.k26    # K2600 format
wav2krz --wav pad.wav pad.for    # Forte format
```

## Conversion Modes

### `instrument` (default)

Creates a sample, keymap, and program. One or more WAVs are mapped across the keyboard. With a single WAV, it covers all 128 keys pitched from the root key. With multiple WAVs, each sample covers a range of keys determined automatically.

```sh
wav2krz --wav strings.wav strings.krz
wav2krz --wav bass_low.wav bass_mid.wav bass_hi.wav bass.krz --mode instrument
```

### `samples`

Packs WAV files as raw samples with no keymap or program. Useful for loading sample data that you'll map manually on the Kurzweil.

```sh
wav2krz --wav one.wav two.wav three.wav raw.krz --mode samples
```

### `drumset`

Each WAV is placed on a separate key, starting at C1 (MIDI 36) by default. A single keymap and program are created. To start on a different key, use a list file with `@group` directives that specify per-sample root keys.

```sh
wav2krz --wav kick.wav snare.wav hat.wav kit.krz --mode drumset
```

### `drumset-multi`

Creates a multi-layer program where each group of samples gets its own keymap and layer. This allows multiple samples to share the same key (e.g., velocity-switched hits). Requires a list file with `@group` directives (see below).

## CLI Reference

```
wav2krz <input_list> <output> [--mode MODE] [--quiet]
wav2krz --wav file1.wav [file2.wav ...] <output> [--mode MODE] [--quiet]
```

| Option | Description |
|---|---|
| `--wav`, `-w` | WAV files to convert (alternative to list file) |
| `--mode`, `-m` | `samples`, `instrument`, `instrument-multi`, `drumset`, or `drumset-multi` (default: `instrument`) |
| `--quiet`, `-q` | Suppress verbose output (verbose is on by default) |

All per-sample configuration (root keys, key ranges, velocity layers, program names) is specified in the list file.

## List File Format

A list file is a text file with one WAV path per line. Lines starting with `#` are comments. Blank lines are ignored. Paths can be absolute or relative to the list file's location.

### Basic

```
# my_samples.txt
piano_c2.wav
piano_c3.wav
piano_c4.wav
piano_c5.wav
```

### Root Keys

Specify a root key after the filename as a note name or MIDI number. This tells the Kurzweil what pitch the sample was recorded at.

```
piano_c2.wav  C2
piano_c3.wav  C3
piano_c4.wav  C4
piano_c5.wav  C5
```

Note names support sharps (`F#3`) and flats (`Bb4`). MIDI numbers (0-127) also work.

### Key Ranges

Add explicit low and high key boundaries after the root key. Without these, key ranges are filled automatically. With them, each sample is confined to the specified range.

```
bass.wav      C2  C0  B2
piano.wav     C4  C3  B5
```

You can also provide just one boundary alongside the root key. If the second key is higher than the root, it becomes the high key (lo=root). If lower, it becomes the low key (hi=root).

```
piano.wav     C4  C6    # lo=C4, hi=C6
bass.wav      C2  C0    # lo=C0, hi=C2
```

### Filenames with Spaces

Quote filenames that contain spaces:

```
"piano soft.wav"   C4  pp-mp
"kick drum.wav"    C2
```

### Velocity Layers

Append a velocity range to assign samples to velocity zones. Zones are numbered 1-8 or named `ppp`, `pp`, `p`, `mp`, `mf`, `f`, `ff`, `fff`.

```
# Velocity-switched piano
piano_soft.wav   C4  pp-mp
piano_loud.wav   C4  mf-fff

# Numeric zones (equivalent)
piano_soft.wav   C4  v1-4
piano_loud.wav   C4  v5-8
```

### Fine Tuning (`tune=N`)

Append `tune=N` to any sample line to adjust pitch by -50 to +50 cents. This applies per-sample tuning at the hardware level.

```
# Tune individual samples to match a recording
piano_c4.wav  C4  tune=4
piano_e4.wav  E4  tune=-2
```

Works alongside velocity zones and key ranges:

```
kick.wav  C2  ppp-mp  tune=-10
```

Inside groups:

```
@group C4
piano_soft.wav   pp-mp   tune=3
piano_loud.wav   mf-fff  tune=3
```

### Groups (`@group`)

Groups set a shared root key and optional key range for multiple samples. Inside a group, sample lines only need a filename and optional velocity.

```
@group C2 A#1 C#2
kick_soft.wav     pp-mp
kick_hard.wav     mf-fff

@group D2 C#2 D#2
snare_ghost.wav   pp-p
snare_normal.wav  mp-f
snare_rimshot.wav ff-fff
```

This is used with `drumset-multi` mode to create multi-layer programs where each group becomes a separate layer.

### Multi-Program Files (`@program`)

Use `@program` to pack multiple independent programs into a single file. Each program gets its own keymap and program object.

```
@program "Electric Piano" instrument
epiano_c2.wav  C2
epiano_c4.wav  C4
epiano_c6.wav  C6

@program "Drum Kit" drumset
kick.wav
snare.wav
hihat.wav
```

The mode after the program name is optional and falls back to the CLI `--mode`.

### Custom Keymap Names (`@keymap`)

By default, the keymap is named after the program (or the output filename). Use `@keymap` to set a custom name.

```
@program "My Piano"
@keymap "Piano KM"
piano_c4.wav C4
```

Inside `drumset-multi` groups, `@keymap` applies per-group:

```
@program "Drum Kit" drumset-multi

@group C2 A#1 C#2
@keymap "Kick"
kick_soft.wav  pp-mf
kick_hard.wav  mf-fff

@group D2 C#2 D#2
@keymap "Snare"
snare_ghost.wav  pp-p
snare_hard.wav   mp-fff
```

## Supported WAV Formats

- 16-bit PCM mono or stereo
- 24-bit PCM mono or stereo (downconverted to 16-bit automatically)
- 8-bit PCM mono
- Any sample rate (44100, 48000, 22050, etc.)
- Loop points and root key from the WAV `smpl` chunk (if present)

## Running Tests

```
pip install -e .
pytest tests/ -v
```

## Linting

```
ruff check src/ tests/
```

## License

MIT
