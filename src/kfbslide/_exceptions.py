"""KFBSlide / OpenSlide-compatible exceptions.

Author: Yifan Feng <evanfeng97@gmail.com>
"""


class OpenSlideError(Exception):
    """Base exception for OpenSlide errors."""

    pass


class OpenSlideUnsupportedFormatError(OpenSlideError):
    """File format not supported or file is corrupted."""

    pass


# Backward compatibility aliases
KfbError = OpenSlideError
KfbUnsupportedFormatError = OpenSlideUnsupportedFormatError
KfbOpenError = OpenSlideError
