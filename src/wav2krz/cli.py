"""Command-line interface for wav2krz converter."""

import argparse
import sys
from pathlib import Path

from .converter import convert_from_list_file, convert_wavs_to_krz
from .exceptions import Wav2KrzError


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog='wav2krz',
        description='Convert WAV files to Kurzweil .krz format',
        epilog='''
Examples:
  # Convert samples only (no keymap/program)
  python -m wav2krz wavlist.txt output.krz --mode samples

  # Create instrument with single sample pitched across keyboard
  python -m wav2krz wavlist.txt output.krz --mode instrument

  # Create drumset with each WAV on consecutive keys starting at C1
  python -m wav2krz wavlist.txt output.krz --mode drumset --start-key 36

  # Direct WAV files without list file
  python -m wav2krz --wav kick.wav snare.wav hihat.wav --output drums.krz --mode drumset
'''
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        'input_list',
        nargs='?',
        type=Path,
        help='Text file with WAV paths (one per line)'
    )
    input_group.add_argument(
        '--wav', '-w',
        nargs='+',
        type=Path,
        dest='wav_files',
        help='WAV files to convert (alternative to list file)'
    )

    # Output file
    parser.add_argument(
        'output',
        nargs='?',
        type=Path,
        help='Output .krz file path'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        dest='output_alt',
        help='Output .krz file path (alternative)'
    )

    # Mode selection
    parser.add_argument(
        '--mode', '-m',
        choices=['samples', 'instrument', 'instrument-multi', 'drumset', 'drumset-multi'],
        default='instrument',
        help='Conversion mode (default: instrument)'
    )

    # Drumset options
    parser.add_argument(
        '--start-key', '-k',
        type=int,
        default=36,
        help='Starting MIDI key for drumset mode (default: 36 = C1)'
    )

    # Root key option
    parser.add_argument(
        '--root-key', '-r',
        type=int,
        default=None,
        help='Root key (MIDI note 0-127) for samples. Overrides WAV metadata. (default: 60 = C4)'
    )

    # ID options
    parser.add_argument(
        '--start-id', '-i',
        type=int,
        default=200,
        help='Starting object ID (default: 200)'
    )

    # Name option
    parser.add_argument(
        '--name', '-n',
        type=str,
        help='Base name for keymap/program (default: output filename)'
    )

    # Quiet mode (suppress verbose output)
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress verbose output'
    )

    return parser


def main(args: list[str] | None = None) -> int:
    """
    Main entry point.

    Args:
        args: Command-line arguments (default: sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    parser = create_parser()
    parsed = parser.parse_args(args)

    # Resolve output path
    output_path = parsed.output or parsed.output_alt
    if not output_path:
        parser.error("Output file required (positional argument or --output)")

    # Ensure .krz extension
    if output_path.suffix.lower() not in ('.krz', '.k25', '.k26', '.for'):
        output_path = output_path.with_suffix('.krz')

    verbose = not parsed.quiet

    try:
        if parsed.wav_files:
            # Direct WAV files
            convert_wavs_to_krz(
                wav_files=parsed.wav_files,
                output_path=output_path,
                mode=parsed.mode,
                start_key=parsed.start_key,
                start_id=parsed.start_id,
                name=parsed.name,
                root_key=parsed.root_key,
                verbose=verbose
            )
        else:
            # List file
            if not parsed.input_list.exists():
                print(f"Error: Input list file not found: {parsed.input_list}",
                      file=sys.stderr)
                return 1

            convert_from_list_file(
                list_file=parsed.input_list,
                output_path=output_path,
                mode=parsed.mode,
                start_key=parsed.start_key,
                start_id=parsed.start_id,
                name=parsed.name,
                root_key=parsed.root_key,
                verbose=verbose
            )

        return 0

    except Wav2KrzError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
