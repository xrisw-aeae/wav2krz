"""WAV file parser for wav2krz converter."""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..exceptions import UnsupportedWavFormat, WavParseError


@dataclass
class SampleInfo:
    """Optional sample chunk information from WAV file."""
    root_key: int = 60  # Middle C
    sample_period: int = 0  # nanoseconds per sample (0 = use fmt chunk)
    is_looped: bool = False
    loop_start: int = 0
    loop_end: int = 0


@dataclass
class WavFile:
    """Parsed WAV file data."""
    channels: int
    sample_rate: int
    bits_per_sample: int
    data: bytes
    sample_info: Optional[SampleInfo] = None

    @property
    def num_samples(self) -> int:
        """Number of samples (frames) in the audio data."""
        bytes_per_sample = self.bits_per_sample // 8
        return len(self.data) // (self.channels * bytes_per_sample)

    @property
    def is_stereo(self) -> bool:
        return self.channels == 2

    @property
    def is_mono(self) -> bool:
        return self.channels == 1

    def get_sample_period_ns(self) -> int:
        """Get sample period in nanoseconds."""
        if self.sample_info and self.sample_info.sample_period > 0:
            return self.sample_info.sample_period
        return int(round(1_000_000_000 / self.sample_rate))


def _read_le_int(data: bytes, offset: int) -> int:
    """Read little-endian 32-bit integer."""
    return struct.unpack_from('<I', data, offset)[0]


def _read_le_short(data: bytes, offset: int) -> int:
    """Read little-endian 16-bit integer."""
    return struct.unpack_from('<H', data, offset)[0]


def _parse_fmt_chunk(chunk_data: bytes) -> tuple[int, int, int]:
    """Parse fmt chunk, return (channels, sample_rate, bits_per_sample)."""
    if len(chunk_data) < 16:
        raise WavParseError("fmt chunk too small")

    audio_format = _read_le_short(chunk_data, 0)
    if audio_format != 1:
        raise UnsupportedWavFormat(f"Only PCM format supported, got format {audio_format}")

    channels = _read_le_short(chunk_data, 2)
    sample_rate = _read_le_int(chunk_data, 4)
    # bytes_per_second at offset 8
    # block_align at offset 12
    bits_per_sample = _read_le_short(chunk_data, 14)

    return channels, sample_rate, bits_per_sample


def _parse_smpl_chunk(chunk_data: bytes) -> SampleInfo:
    """Parse smpl (sample) chunk for loop and root key info."""
    info = SampleInfo()

    if len(chunk_data) >= 36:
        # Offset 0-3: manufacturer
        # Offset 4-7: product
        info.sample_period = _read_le_int(chunk_data, 8)
        info.root_key = _read_le_int(chunk_data, 12) & 0x7F
        # Offset 16-19: pitch fraction
        # Offset 20-23: SMPTE format
        # Offset 24-27: SMPTE offset
        num_loops = _read_le_int(chunk_data, 28)
        # Offset 32-35: sampler data size

        if num_loops > 0 and len(chunk_data) >= 60:
            info.is_looped = True
            # Loop structure starts at offset 36
            # Offset 36-39: cue point ID
            # Offset 40-43: type
            info.loop_start = _read_le_int(chunk_data, 44)
            info.loop_end = _read_le_int(chunk_data, 48)
            # Offset 52-55: fraction
            # Offset 56-59: play count

    return info


def parse_wav(filepath: Path | str) -> WavFile:
    """
    Parse a WAV file and return its data.

    Supports:
    - 16-bit mono PCM
    - 16-bit stereo PCM
    - 24-bit mono PCM (downconverted to 16-bit)
    - 24-bit stereo PCM (downconverted to 16-bit)
    - 8-bit mono PCM (upconverted to 16-bit)

    Args:
        filepath: Path to the WAV file

    Returns:
        WavFile with parsed audio data

    Raises:
        WavParseError: If file is not a valid WAV
        UnsupportedWavFormat: If WAV format is not supported
    """
    filepath = Path(filepath)

    with open(filepath, 'rb') as f:
        file_data = f.read()

    if len(file_data) < 44:
        raise WavParseError("File too small to be a valid WAV")

    # Check RIFF header
    if file_data[0:4] != b'RIFF':
        raise WavParseError("Not a RIFF file")

    # file_size = _read_le_int(file_data, 4)

    if file_data[8:12] != b'WAVE':
        raise WavParseError("Not a WAVE file")

    # Parse chunks
    offset = 12
    fmt_data = None
    audio_data = None
    sample_info = None

    while offset < len(file_data) - 8:
        chunk_id = file_data[offset:offset+4]
        chunk_size = _read_le_int(file_data, offset + 4)
        chunk_data = file_data[offset+8:offset+8+chunk_size]

        if chunk_id == b'fmt ':
            fmt_data = chunk_data
        elif chunk_id == b'data':
            audio_data = chunk_data
        elif chunk_id == b'smpl':
            sample_info = _parse_smpl_chunk(chunk_data)

        # Move to next chunk (chunks are word-aligned)
        offset += 8 + chunk_size
        if chunk_size % 2 == 1:
            offset += 1

    if fmt_data is None:
        raise WavParseError("No fmt chunk found")
    if audio_data is None:
        raise WavParseError("No data chunk found")

    channels, sample_rate, bits_per_sample = _parse_fmt_chunk(fmt_data)

    # Validate supported formats
    if channels not in (1, 2):
        raise UnsupportedWavFormat(f"Only mono and stereo supported, got {channels} channels")
    if bits_per_sample not in (8, 16, 24):
        raise UnsupportedWavFormat(f"Only 8-bit, 16-bit, and 24-bit supported, got {bits_per_sample}-bit")
    if channels == 2 and bits_per_sample == 8:
        raise UnsupportedWavFormat("8-bit stereo not supported")

    return WavFile(
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits_per_sample,
        data=audio_data,
        sample_info=sample_info
    )
