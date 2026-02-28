"""Test helpers for generating WAV files."""

import math
import struct
from pathlib import Path


def make_wav(path: Path, frequency: float = 440.0, duration: float = 0.1,
             sample_rate: int = 44100, bits: int = 16, channels: int = 1,
             amplitude: float = 0.5, loop_start: int = None,
             loop_end: int = None, root_key: int = None) -> Path:
    """
    Generate a PCM WAV file for testing.

    Args:
        path: Output file path
        frequency: Tone frequency in Hz
        duration: Duration in seconds
        sample_rate: Sample rate in Hz
        bits: Bits per sample (8 or 16)
        channels: Number of channels (1 or 2)
        amplitude: Amplitude 0.0-1.0
        loop_start: Loop start sample (adds smpl chunk if set)
        loop_end: Loop end sample (adds smpl chunk if set)
        root_key: Root key MIDI note (adds smpl chunk if set)

    Returns:
        The path written to
    """
    num_frames = int(sample_rate * duration)
    bytes_per_sample = bits // 8
    block_align = channels * bytes_per_sample

    # Generate audio data
    audio = bytearray()
    for i in range(num_frames):
        t = i / sample_rate
        value = amplitude * math.sin(2 * math.pi * frequency * t)
        for _ in range(channels):
            if bits == 16:
                audio.extend(struct.pack('<h', int(value * 32767)))
            elif bits == 24:
                sample = int(value * 8388607)  # 2^23 - 1
                audio.extend(struct.pack('<i', sample)[:3])  # 3 LSBs of LE int32
            else:
                audio.append(int((value + 1.0) * 127.5))

    data_size = len(audio)

    # Build smpl chunk if needed
    smpl_chunk = b''
    has_smpl = (loop_start is not None or root_key is not None)
    if has_smpl:
        rk = root_key if root_key is not None else 60
        has_loop = loop_start is not None and loop_end is not None
        num_loops = 1 if has_loop else 0
        sample_period = int(round(1_000_000_000 / sample_rate))

        smpl_data = struct.pack('<II', 0, 0)               # manufacturer, product
        smpl_data += struct.pack('<I', sample_period)       # sample period
        smpl_data += struct.pack('<I', rk)                  # root key
        smpl_data += struct.pack('<I', 0)                   # pitch fraction
        smpl_data += struct.pack('<I', 0)                   # SMPTE format
        smpl_data += struct.pack('<I', 0)                   # SMPTE offset
        smpl_data += struct.pack('<I', num_loops)           # num loops
        smpl_data += struct.pack('<I', 0)                   # sampler data size

        if has_loop:
            smpl_data += struct.pack('<II', 0, 0)           # cue point ID, type
            smpl_data += struct.pack('<II', loop_start, loop_end)
            smpl_data += struct.pack('<II', 0, 0)           # fraction, play count

        smpl_chunk = b'smpl' + struct.pack('<I', len(smpl_data)) + smpl_data

    # Calculate total file size
    fmt_chunk_size = 24  # 'fmt ' + size + 16 bytes data
    data_chunk_size = 8 + data_size
    riff_size = 4 + fmt_chunk_size + data_chunk_size + len(smpl_chunk)

    with open(path, 'wb') as f:
        # RIFF header
        f.write(b'RIFF')
        f.write(struct.pack('<I', riff_size))
        f.write(b'WAVE')

        # fmt chunk
        f.write(b'fmt ')
        f.write(struct.pack('<I', 16))
        f.write(struct.pack('<H', 1))           # PCM
        f.write(struct.pack('<H', channels))
        f.write(struct.pack('<I', sample_rate))
        f.write(struct.pack('<I', sample_rate * block_align))
        f.write(struct.pack('<H', block_align))
        f.write(struct.pack('<H', bits))

        # data chunk
        f.write(b'data')
        f.write(struct.pack('<I', data_size))
        f.write(audio)

        # smpl chunk
        if smpl_chunk:
            f.write(smpl_chunk)

    return path
