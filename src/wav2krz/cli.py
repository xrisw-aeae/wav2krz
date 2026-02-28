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
  wav2krz wavlist.txt output.krz --mode samples

  # Create instrument with single sample pitched across keyboard
  wav2krz wavlist.txt output.krz --mode instrument

  # Create drumset from a list file
  wav2krz wavlist.txt output.krz --mode drumset

  # Direct WAV files without list file
  wav2krz --wav kick.wav snare.wav hihat.wav output.krz --mode drumset
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

    # Output file (positional)
    parser.add_argument(
        'output',
        nargs='?',
        type=Path,
        help='Output .krz file path'
    )

    # Mode selection
    parser.add_argument(
        '--mode', '-m',
        choices=['samples', 'instrument', 'instrument-multi', 'drumset', 'drumset-multi'],
        default='instrument',
        help='Conversion mode (default: instrument)'
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

    if not parsed.output:
        parser.error("Output file required")

    output_path = parsed.output

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
