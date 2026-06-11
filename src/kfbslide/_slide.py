"""
KFBSlide — OpenSlide-compatible API for KFB files.

Architecture:
- File header parsing: Pure Python (cross-platform)
- Associated images: Pure Python (read JPEG directly from file)
- Tile reading: Pure Python via tile index table
- Tile cache: LRU decoded-tile cache for repeated reads
- JPEG backend: Auto-selects TurboJPEG if available, else Pillow
"""

import io
import struct
from collections.abc import Mapping
from typing import Dict, List, Optional, Tuple

from PIL import Image

from ._cache import _LRUCache
from ._exceptions import OpenSlideError, OpenSlideUnsupportedFormatError
from ._jpeg_backend import decode_jpeg
from ._kfbformat import KfbAssocImage, KfbFileInfo, parse_kfb_file


class _KfbPropertyMap(Mapping):
    """Read-only mapping compatible with OpenSlide's property map."""

    __slots__ = ("_data",)

    def __init__(self, items: Dict[str, str]):
        self._data = dict(items)

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"_KfbPropertyMap({self._data!r})"


class _AssociatedImageMap(Mapping):
    """Lazy read-only mapping for associated images."""

    __slots__ = ("_filename", "_assoc_list", "_names", "_cache")

    def __init__(self, filename: str, assoc_images: List[KfbAssocImage]):
        self._filename = filename
        self._assoc_list = assoc_images
        self._names = [a.name for a in assoc_images]
        self._cache: Dict[str, Image.Image] = {}

    def __getitem__(self, key: str) -> Image.Image:
        if key in self._cache:
            return self._cache[key]
        for assoc in self._assoc_list:
            if assoc.name == key:
                with open(self._filename, "rb") as f:
                    f.seek(assoc.data_offset)
                    jpeg_data = f.read(assoc.data_length)
                img = Image.open(io.BytesIO(jpeg_data)).convert("RGBA")
                self._cache[key] = img
                return img
        raise KeyError(key)

    def __iter__(self):
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def __repr__(self) -> str:
        return f"_AssociatedImageMap({self._names!r})"


class _TileIndex:
    """Parsed KFB tile index table."""

    __slots__ = (
        "filename",
        "tile_size",
        "entries",
        "lookup",
        "offsets",
        "level_scales",
        "scale_to_level",
        "level_count",
        "_level_dimensions",
        "_level_downsamples",
    )

    def __init__(self, filename: str, info: KfbFileInfo):
        self.filename = filename
        self.tile_size = info.header.tile_size

        tile_count = info.header.tile_count
        idx_start = info.header.tile_index_start

        with open(filename, "rb") as f:
            f.seek(idx_start)
            data = f.read(tile_count * 64)

        self.entries: List[Dict] = []
        self.lookup: Dict[Tuple[float, int, int], int] = {}
        scales = set()

        for i in range(tile_count):
            off = i * 64
            e = data[off : off + 64]
            entry = {
                "scale": struct.unpack("<f", e[20:24])[0],
                "x": struct.unpack("<I", e[4:8])[0],
                "y": struct.unpack("<I", e[8:12])[0],
                "width": struct.unpack("<I", e[12:16])[0],
                "height": struct.unpack("<I", e[16:20])[0],
                "size": struct.unpack("<I", e[32:36])[0],
            }
            self.entries.append(entry)
            self.lookup[(entry["scale"], entry["x"], entry["y"])] = i
            scales.add(entry["scale"])

        # Compute file offset for each tile.
        cumulative = info.tile_data_offset
        self.offsets: List[int] = []
        for entry in self.entries:
            self.offsets.append(cumulative)
            cumulative += entry["size"]

        # Build level info from scales (descending: 40.0, 20.0, ...).
        sorted_scales = sorted([s for s in scales if s >= 1.0], reverse=True)
        self.level_scales = {i: s for i, s in enumerate(sorted_scales)}
        self.scale_to_level = {s: i for i, s in self.level_scales.items()}
        self.level_count = len(sorted_scales)

        # Level dimensions derived from base resolution.
        base_w, base_h = info.header.width, info.header.height
        self._level_dimensions: List[Tuple[int, int]] = []
        self._level_downsamples: List[float] = []
        for i in range(self.level_count):
            scale = self.level_scales[i]
            ds = info.header.scan_scale / scale
            self._level_downsamples.append(ds)
            self._level_dimensions.append((int(base_w / ds), int(base_h / ds)))

    def level_dimensions(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(self._level_dimensions)

    def level_downsamples(self) -> Tuple[float, ...]:
        return tuple(self._level_downsamples)

    def get_best_level_for_downsample(self, downsample: float) -> int:
        """Choose the level with downsample closest to but not greater than target."""
        best = 0
        for i in range(self.level_count):
            if self._level_downsamples[i] <= downsample:
                best = i
            else:
                break
        return best


class OpenSlide:
    """
    KFB whole-slide image reader with OpenSlide-compatible API.

    Interface compatible with openslide-python, but completely independent
    and implemented in pure Python with no native library dependencies.
    """

    __slots__ = (
        "_filename",
        "_closed",
        "_error",
        "_tile_cache",
        "_file_handle",
        "_info",
        "_index",
        "_properties",
        "_associated_images",
    )

    def __init__(self, filename: str):
        self._filename = filename
        self._closed = False
        self._error = False
        self._tile_cache = _LRUCache(256)
        self._file_handle: Optional[io.BufferedReader] = None

        try:
            self._info = parse_kfb_file(filename)
        except Exception as e:
            raise OpenSlideUnsupportedFormatError(f"Cannot parse KFB file: {e}")

        try:
            self._index = _TileIndex(filename, self._info)
        except Exception as e:
            raise OpenSlideError(f"Failed to build tile index: {e}")

        try:
            self._file_handle = open(filename, "rb")
        except Exception as e:
            raise OpenSlideError(f"Failed to open file handle: {e}")

        # Pre-build property map.
        header = self._info.header
        props = {
            "openslide.vendor": "kfbio",
            "openslide.quickhash-1": "",
            "openslide.mpp-x": str(header.mpp),
            "openslide.mpp-y": str(header.mpp),
            "openslide.objective-power": str(header.scan_scale),
            "kfbio.vendor": "Kfbio",
            "kfbio.version": str(header.version),
            "kfbio.scan_scale": str(header.scan_scale),
            "kfbio.tile_size": str(header.tile_size),
            "kfbio.tile_count": str(header.tile_count),
            "kfbio.width": str(header.width),
            "kfbio.height": str(header.height),
            "kfbio.spend_time": str(header.spend_time),
            "kfbio.scan_time": str(header.scan_time),
            "kfbio.mpp": str(header.mpp),
        }
        self._properties = _KfbPropertyMap(props)

        # Lazy associated images map.
        self._associated_images = _AssociatedImageMap(
            filename, self._info.assoc_images
        )

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def detect_format(cls, filename: str) -> Optional[str]:
        """Detect whether a file is a KFB format slide.

        Returns:
            "kfbio" if the file is recognized, None otherwise.
        """
        try:
            info = parse_kfb_file(filename)
            if info.header.magic == "KFB":
                return "kfbio"
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_open(self) -> None:
        """Raise if the slide is closed."""
        if self._closed:
            raise OpenSlideError("Slide is closed")

    def _check_error(self) -> None:
        """Raise if an error has occurred (latching semantics)."""
        if self._error:
            raise OpenSlideError("OpenSlide error has occurred")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._filename!r})"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self) -> None:
        """Close the slide and release resources."""
        self._closed = True
        self._tile_cache.clear()
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def level_count(self) -> int:
        """Number of pyramid levels."""
        self._check_open()
        self._check_error()
        return self._index.level_count

    @property
    def dimensions(self) -> Tuple[int, int]:
        """Dimensions of the slide at level 0 (highest resolution)."""
        self._check_open()
        self._check_error()
        return self._index.level_dimensions()[0]

    @property
    def level_dimensions(self) -> Tuple[Tuple[int, int], ...]:
        """Dimensions of each pyramid level."""
        self._check_open()
        self._check_error()
        return self._index.level_dimensions()

    @property
    def level_downsamples(self) -> Tuple[float, ...]:
        """Downsample factor for each level."""
        self._check_open()
        self._check_error()
        return self._index.level_downsamples()

    @property
    def properties(self) -> Mapping[str, str]:
        """Metadata properties as a read-only mapping."""
        self._check_open()
        self._check_error()
        return self._properties

    @property
    def associated_images(self) -> Mapping[str, Image.Image]:
        """Associated images (macro, label, thumbnail) as a lazy mapping."""
        self._check_open()
        self._check_error()
        return self._associated_images

    @property
    def color_profile(self) -> Optional[object]:
        """Embedded ICC color profile, or None if not available."""
        self._check_open()
        self._check_error()
        return None

    # ------------------------------------------------------------------
    # Reading operations
    # ------------------------------------------------------------------

    def get_best_level_for_downsample(self, downsample: float) -> int:
        """Get the best pyramid level for a given downsample factor."""
        self._check_open()
        self._check_error()
        return self._index.get_best_level_for_downsample(downsample)

    def read_region(
        self,
        location: Tuple[int, int],
        level: int,
        size: Tuple[int, int],
    ) -> Image.Image:
        """
        Read a region from the slide.

        Args:
            location: (x, y) top-left coordinates in level 0.
            level: Pyramid level.
            size: (width, height) output size.

        Returns:
            PIL.Image.Image (RGBA).
        """
        self._check_open()
        self._check_error()

        try:
            if level < 0 or level >= self._index.level_count:
                raise OpenSlideError(f"Invalid level {level}")

            scale = self._index.level_scales[level]
            ds = self._info.header.scan_scale / scale

            x0, y0 = int(location[0] / ds), int(location[1] / ds)
            w, h = int(size[0]), int(size[1])

            out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            tile_size = self._index.tile_size

            tx_start = x0 // tile_size
            ty_start = y0 // tile_size
            tx_end = (x0 + w - 1) // tile_size + 1
            ty_end = (y0 + h - 1) // tile_size + 1

            if self._file_handle is None:
                raise OpenSlideError("Slide is closed")
            f = self._file_handle

            for ty in range(ty_start, ty_end):
                for tx in range(tx_start, tx_end):
                    tile_x = tx * tile_size
                    tile_y = ty * tile_size
                    key = (scale, tile_x, tile_y)
                    idx = self._index.lookup.get(key)
                    if idx is None:
                        continue

                    entry = self._index.entries[idx]
                    tw, th = entry["width"], entry["height"]

                    # Try cached decoded tile first.
                    tile = self._tile_cache.get(idx)
                    if tile is None:
                        offset = self._index.offsets[idx]
                        f.seek(offset)
                        jpeg = f.read(entry["size"])
                        tile = decode_jpeg(jpeg)

                        # Tile may be smaller than tile_size at image edges.
                        if tile.size != (tw, th):
                            tile = tile.resize((tw, th))

                        self._tile_cache.put(idx, tile)

                    paste_x = tile_x - x0
                    paste_y = tile_y - y0
                    crop_x0 = max(0, x0 - tile_x)
                    crop_y0 = max(0, y0 - tile_y)
                    crop_x1 = min(tw, x0 + w - tile_x)
                    crop_y1 = min(th, y0 + h - tile_y)

                    if crop_x1 > crop_x0 and crop_y1 > crop_y0:
                        cropped = tile.crop((crop_x0, crop_y0, crop_x1, crop_y1))
                        cropped_rgba = cropped.convert("RGBA")
                        out.paste(
                            cropped_rgba,
                            (paste_x + crop_x0, paste_y + crop_y0),
                        )

            return out
        except Exception:
            self._error = True
            raise

    def get_thumbnail(self, size: Tuple[int, int]) -> Image.Image:
        """Get a thumbnail image."""
        self._check_open()
        self._check_error()

        thumb = None
        try:
            thumb = self._associated_images.get("thumbnail")
        except KeyError:
            pass

        if thumb is not None:
            thumb_copy = thumb.copy()
            thumb_copy.thumbnail(size, Image.LANCZOS)
            return thumb_copy

        # Fallback: read from lowest resolution level.
        level = self.level_count - 1
        dims = self.level_dimensions[level]
        return self.read_region((0, 0), level, dims)

    def set_cache(self, cache) -> None:
        """
        Attach a shared cache to the slide.

        For kfbslide, this is currently a no-op since we use a private
        per-slide LRU cache. Accepts the cache argument for API compatibility.
        """
        self._check_open()
        self._check_error()
        # No-op for now. Could be extended to use a shared cache.
        pass


# Backward compatibility alias
KfbSlide = OpenSlide


def open_slide(filename: str, **kwargs) -> OpenSlide:
    """
    Open a KFB file.

    Args:
        filename: Path to the KFB file.

    Returns:
        OpenSlide instance.
    """
    if kwargs.pop("tile_cache_size", None) is not None:
        import warnings

        warnings.warn(
            "tile_cache_size is deprecated and ignored",
            DeprecationWarning,
            stacklevel=2,
        )
    return OpenSlide(filename)
