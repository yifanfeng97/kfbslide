"""
KFBSlide — Pure Python KFB whole-slide image reader.

A minimal, cross-platform reader for KFB (KFBio) whole-slide images
with an OpenSlide-compatible API.

Author: Yifan Feng <evanfeng97@gmail.com>
License: MIT

Drop-in replacement usage:
    import kfbslide as openslide
    slide = openslide.OpenSlide("sample.kfb")
"""

from ._slide import OpenSlide, KfbSlide, open_slide
from ._exceptions import (
    OpenSlideError,
    OpenSlideUnsupportedFormatError,
    KfbError,
    KfbUnsupportedFormatError,
    KfbOpenError,
)

__version__ = "0.2.0"

# Standard OpenSlide property name constants
PROPERTY_NAME_VENDOR = "openslide.vendor"
PROPERTY_NAME_QUICKHASH1 = "openslide.quickhash-1"
PROPERTY_NAME_BACKGROUND_COLOR = "openslide.background-color"
PROPERTY_NAME_OBJECTIVE_POWER = "openslide.objective-power"
PROPERTY_NAME_MPP_X = "openslide.mpp-x"
PROPERTY_NAME_MPP_Y = "openslide.mpp-y"
PROPERTY_NAME_BOUNDS_X = "openslide.bounds-x"
PROPERTY_NAME_BOUNDS_Y = "openslide.bounds-y"
PROPERTY_NAME_BOUNDS_WIDTH = "openslide.bounds-width"
PROPERTY_NAME_BOUNDS_HEIGHT = "openslide.bounds-height"

__all__ = [
    # Primary OpenSlide API
    "OpenSlide",
    "OpenSlideError",
    "OpenSlideUnsupportedFormatError",
    # Property constants
    "PROPERTY_NAME_VENDOR",
    "PROPERTY_NAME_QUICKHASH1",
    "PROPERTY_NAME_BACKGROUND_COLOR",
    "PROPERTY_NAME_OBJECTIVE_POWER",
    "PROPERTY_NAME_MPP_X",
    "PROPERTY_NAME_MPP_Y",
    "PROPERTY_NAME_BOUNDS_X",
    "PROPERTY_NAME_BOUNDS_Y",
    "PROPERTY_NAME_BOUNDS_WIDTH",
    "PROPERTY_NAME_BOUNDS_HEIGHT",
    # Backward compatibility
    "KfbSlide",
    "open_slide",
    "KfbError",
    "KfbUnsupportedFormatError",
    "KfbOpenError",
]
