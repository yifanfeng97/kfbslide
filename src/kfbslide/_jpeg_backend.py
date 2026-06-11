"""
JPEG decoding backend abstraction.

Auto-selects the fastest available decoder:
1. TurboJPEG (libjpeg-turbo) — fastest, requires system library.
2. Pillow (libjpeg) — always-available fallback.

Usage:
    from kfbslide._jpeg_backend import decode_jpeg
    img = decode_jpeg(jpeg_bytes)
"""

import io
from typing import Optional

from PIL import Image

_TURBOJPEG: Optional[object] = None
_TURBOJPEG_ERROR: Optional[str] = None


def _init_turbojpeg():
    """Try to initialize TurboJPEG; record failure reason for diagnostics."""
    global _TURBOJPEG, _TURBOJPEG_ERROR
    try:
        from turbojpeg import TurboJPEG

        _TURBOJPEG = TurboJPEG()
        _TURBOJPEG_ERROR = None
    except ImportError as e:
        _TURBOJPEG_ERROR = f"PyTurboJPEG not installed: {e}"
    except RuntimeError as e:
        # Usually: libturbojpeg.so not found on the system.
        _TURBOJPEG_ERROR = f"libjpeg-turbo library missing: {e}"
    except Exception as e:
        _TURBOJPEG_ERROR = f"TurboJPEG init failed: {e}"


_init_turbojpeg()


def has_turbojpeg() -> bool:
    return _TURBOJPEG is not None


def turbojpeg_error() -> Optional[str]:
    return _TURBOJPEG_ERROR


def decode_jpeg(data: bytes) -> Image.Image:
    """Decode JPEG bytes to a PIL RGB image using the best available backend."""
    if _TURBOJPEG is not None:
        try:
            # TurboJPEG.decode returns an (H, W, 3) numpy array in RGB order.
            arr = _TURBOJPEG.decode(data)
            return Image.fromarray(arr, mode="RGB")
        except Exception:
            # Silently fall back to Pillow on any TurboJPEG error.
            pass

    return Image.open(io.BytesIO(data)).convert("RGB")
