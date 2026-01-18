"""Custom exceptions for wav2krz converter."""


class Wav2KrzError(Exception):
    """Base exception for wav2krz converter."""
    pass


class WavParseError(Wav2KrzError):
    """Error parsing WAV file."""
    pass


class UnsupportedWavFormat(Wav2KrzError):
    """WAV format not supported for conversion."""
    pass


class KrzWriteError(Wav2KrzError):
    """Error writing KRZ file."""
    pass


class InvalidNameError(Wav2KrzError):
    """Object name too long (max 16 characters)."""
    pass
