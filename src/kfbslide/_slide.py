"""
KFBSlide — OpenSlide-compatible API for KFB files.

Architecture:
- File header parsing: Pure Python (cross-platform)
- Associated images: Pure Python (read JPEG directly from file)
- Tile reading: Pure Python via tile index table
- Tile cache: byte-bounded LRU decoded-tile cache (default 256 MiB per instance)
- JPEG decoding: Pillow (pure Python, no extra dependencies)
- Thread safety: per-thread file handles and a locked LRU cache, so one
  OpenSlide instance can be read concurrently from multiple threads

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import io
import os
import struct
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image

from ._cache import _LRUCache
from ._exceptions import OpenSlideError, OpenSlideUnsupportedFormatError
from ._kfbformat import KfbAssocImage, KfbFileInfo, parse_kfb_file

# Default per-instance tile cache budget. Cached tiles are RGBA, so an
# entry of a tile_size x tile_size tile costs about tile_size**2 * 4 bytes
# (256 px tiles -> ~256 KiB each; 512 px tiles -> ~1 MiB each).
_DEFAULT_CACHE_BYTES = 256 * 1024 * 1024


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
    """Lazy read-only mapping for associated images.

    When a file_handle_getter is provided and returns a valid handle,
    it reuses that handle to avoid reopening the file on every access.
    If the handle is unavailable (e.g. slide closed), falls back to
    opening the file independently.
    """

    __slots__ = ("_filename", "_assoc_list", "_names", "_cache", "_fh_getter")

    def __init__(
        self,
        filename: str,
        assoc_images: List[KfbAssocImage],
        file_handle_getter=None,
    ):
        self._filename = filename
        self._assoc_list = assoc_images
        self._names = [a.name for a in assoc_images]
        self._cache: Dict[str, Image.Image] = {}
        self._fh_getter = file_handle_getter

    def __getitem__(self, key: str) -> Image.Image:
        if key in self._cache:
            return self._cache[key]
        for assoc in self._assoc_list:
            if assoc.name == key:
                # Try to reuse the slide's file handle first.
                fh = self._fh_getter() if self._fh_getter else None
                if fh is not None:
                    fh.seek(assoc.data_offset)
                    jpeg_data = fh.read(assoc.data_length)
                else:
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

        if len(data) < tile_count * 64:
            raise ValueError(
                f"Tile index truncated: expected {tile_count * 64} bytes "
                f"at offset {idx_start}, got {len(data)}"
            )

        # Bulk-parse the 64-byte records with one C-level iter_unpack per
        # entry (fields: x, y, width, height, scale, size) instead of six
        # struct.unpack calls per entry in a Python loop — roughly an order
        # of magnitude faster, which matters for lazy opens in worker
        # threads where the GIL serializes this pure-Python work.
        records = struct.iter_unpack("<4xIIIIf8xI28x", data)
        self.entries: List[Dict] = [
            {"scale": scale, "x": x, "y": y, "width": width, "height": height, "size": size}
            for x, y, width, height, scale, size in records
        ]
        self.lookup: Dict[Tuple[float, int, int], int] = {
            (e["scale"], e["x"], e["y"]): i for i, e in enumerate(self.entries)
        }
        scales = {e["scale"] for e in self.entries}

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
        "_tile_cache",
        "_handles",
        "_open_handles",
        "_handles_lock",
        "_pid",
        "_info",
        "_index",
        "_properties",
        "_associated_images",
    )

    def __init__(self, filename: str):
        self._filename = filename
        self._closed = False
        self._tile_cache = _LRUCache(_DEFAULT_CACHE_BYTES)
        self._handles = threading.local()
        self._open_handles: List[io.BufferedReader] = []
        self._handles_lock = threading.Lock()
        self._pid = os.getpid()

        try:
            self._info = parse_kfb_file(filename)
        except Exception as e:
            raise OpenSlideUnsupportedFormatError(f"Cannot parse KFB file: {e}")

        try:
            self._index = _TileIndex(filename, self._info)
        except Exception as e:
            raise OpenSlideError(f"Failed to build tile index: {e}")

        try:
            self._ensure_open_handle()
        except OpenSlideError:
            raise
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

        # Lazy associated images map (reuses file handle when possible).
        self._associated_images = _AssociatedImageMap(
            filename,
            self._info.assoc_images,
            file_handle_getter=lambda: self._ensure_open_handle()
            if not self._closed
            else None,
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

    def _ensure_open_handle(self) -> io.BufferedReader:
        """Return a file handle private to the calling thread.

        Each thread gets its own handle (kept in a threading.local), so
        concurrent read_region() calls on one shared OpenSlide instance
        never interleave seek/read on the same OS-level file offset.
        close() closes every per-thread handle via the registry.

        A handle inherited through fork is replaced in the child: the
        inherited handle shares the parent's OS-level file offset, so
        concurrent seek/read on it corrupts the byte stream (typically
        surfacing as PIL "cannot identify image file").
        """
        if self._closed:
            raise OpenSlideError("Slide is closed")
        pid = os.getpid()
        fh = getattr(self._handles, "fh", None)
        if fh is not None and pid != self._pid:
            try:
                fh.close()
            except Exception:
                pass
            fh = None
        if fh is None:
            try:
                fh = open(self._filename, "rb")
            except Exception as e:
                raise OpenSlideError(f"Failed to open file handle: {e}")
            self._handles.fh = fh
            with self._handles_lock:
                self._open_handles.append(fh)
            self._pid = pid
        return fh

    def _file_size(self) -> int:
        """Return the size of the underlying file."""
        fh = self._ensure_open_handle()
        return os.fstat(fh.fileno()).st_size

    def _try_heal_tile(self, idx: int, exc: Exception) -> Image.Image:
        """Attempt to recover a tile whose index entry points to invalid JPEG data.

        Some real-world KFB files contain corrupt tile index entries where the
        recorded offset/size does not match the actual JPEG stream. The JPEG
        data itself is intact; only the index is wrong. This method searches a
        small window for a valid JPEG SOI/EOI pair and, if found, updates the
        in-memory index so the tile (and its neighbors) can be read correctly.

        If the JPEG stream itself is corrupt, the tile cannot be recovered, but
        the index is still repaired so that the failure does not poison reads of
        the following tiles.

        If recovery fails, the original exception is re-raised.
        """
        try:
            fh = self._ensure_open_handle()
        except OpenSlideError:
            raise exc

        offset = self._index.offsets[idx]
        recorded_size = self._index.entries[idx]["size"]
        file_size = self._file_size()

        # Limit recovery to a reasonable JPEG size so we never interpret the
        # next tile's data as part of this tile. Typical 256x256 RGB JPEG tiles
        # are a few KB; allow up to 512 KB as a generous upper bound.
        max_tile_jpeg_size = max(512 * 1024, recorded_size * 4)

        # Search a window around the recorded byte range. The SOI must lie within
        # the recorded tile range or shortly before it; otherwise it belongs to a
        # neighboring tile.
        search_start = max(0, offset - 2048)
        search_end = min(offset + max_tile_jpeg_size, file_size)
        if search_end <= search_start:
            raise exc

        fh.seek(search_start)
        window = fh.read(search_end - search_start)
        if len(window) < 4:
            raise exc

        recorded_rel = offset - search_start

        # Locate JPEG SOI markers that could belong to this tile.
        soi_positions = []
        start = 0
        while True:
            pos = window.find(b"\xff\xd8", start)
            if pos == -1:
                break
            # SOI must be at or before the end of the recorded tile data, and
            # not more than 2048 bytes before the recorded offset.
            if recorded_rel - 2048 <= pos <= recorded_rel + recorded_size:
                soi_positions.append(pos)
            start = pos + 2

        if soi_positions:
            # Prefer the SOI closest to the recorded offset.
            soi_positions.sort(key=lambda p: abs(p - recorded_rel))

            for soi_pos in soi_positions:
                soi_abs = search_start + soi_pos
                # EOI must be after the SOI and within the reasonable tile size.
                eoi_search_end = min(len(window), soi_pos + max_tile_jpeg_size)
                eoi_pos = window.find(b"\xff\xd9", soi_pos + 2, eoi_search_end)
                if eoi_pos == -1:
                    continue
                eoi_abs = search_start + eoi_pos + 2  # include EOI marker
                actual_size = eoi_abs - soi_abs
                if actual_size <= 0:
                    continue

                try:
                    fh.seek(soi_abs)
                    data = fh.read(actual_size)
                    tile = Image.open(io.BytesIO(data)).convert("RGBA")
                except Exception:
                    continue

                # Update in-memory index entries. The recovered tile may take
                # bytes from the previous tile or give bytes to the next tile,
                # so adjust neighbors accordingly and recompute cumulative offsets.
                old_offset = offset
                old_size = recorded_size

                self._index.offsets[idx] = soi_abs
                self._index.entries[idx]["size"] = actual_size

                delta_start = soi_abs - old_offset
                delta_end = (soi_abs + actual_size) - (old_offset + old_size)

                if idx > 0 and delta_start != 0:
                    self._index.entries[idx - 1]["size"] += delta_start

                if idx + 1 < len(self._index.entries):
                    self._index.entries[idx + 1]["size"] -= delta_end

                # Recompute cumulative offsets from idx+1 onwards to keep the
                # entire offset table consistent.
                cumulative = self._index.offsets[idx] + self._index.entries[idx]["size"]
                for i in range(idx + 1, len(self._index.offsets)):
                    self._index.offsets[i] = cumulative
                    cumulative += self._index.entries[i]["size"]

                return tile

        # No decodable JPEG found within this tile's recorded range. The JPEG
        # stream itself may be corrupt, or the index size is wrong. Try to
        # locate the next tile boundary (the next SOI after the recorded offset)
        # so that subsequent tiles are not affected by an incorrect size.
        next_soi_pos = window.find(b"\xff\xd8", recorded_rel + 1)
        if next_soi_pos != -1:
            next_soi_abs = search_start + next_soi_pos
            if 0 < next_soi_abs - offset <= max_tile_jpeg_size:
                old_size = recorded_size
                new_size = next_soi_abs - offset

                self._index.entries[idx]["size"] = new_size

                delta_end = new_size - old_size
                if idx + 1 < len(self._index.entries):
                    self._index.entries[idx + 1]["size"] -= delta_end

                # Recompute cumulative offsets from idx+1 onwards.
                cumulative = self._index.offsets[idx] + self._index.entries[idx]["size"]
                for i in range(idx + 1, len(self._index.offsets)):
                    self._index.offsets[i] = cumulative
                    cumulative += self._index.entries[i]["size"]

        raise exc

    def _read_decoded_tile(self, idx: int) -> Image.Image:
        """Read and decode a single tile, with self-healing for index corruption."""
        tile = self._tile_cache.get(idx)
        if tile is not None:
            return tile

        fh = self._ensure_open_handle()

        entry = self._index.entries[idx]
        tw, th = entry["width"], entry["height"]

        offset = self._index.offsets[idx]
        size = entry["size"]
        fh.seek(offset)
        jpeg = fh.read(size)

        try:
            tile = Image.open(io.BytesIO(jpeg)).convert("RGBA")
        except Exception as exc:
            tile = self._try_heal_tile(idx, exc)

        if tile.size != (tw, th):
            tile = tile.resize((tw, th))

        self._tile_cache.put(idx, tile)
        return tile

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._filename!r})"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self) -> None:
        """Close the slide and release resources.

        Closes every per-thread file handle via the registry, so a slide
        read from multiple threads is fully released from any of them. Do
        not call close() while other threads are still inside
        read_region() — those reads will fail with I/O errors.
        """
        self._closed = True
        self._tile_cache.clear()
        with self._handles_lock:
            handles, self._open_handles = self._open_handles, []
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def level_count(self) -> int:
        """Number of pyramid levels."""
        self._check_open()
        return self._index.level_count

    @property
    def dimensions(self) -> Tuple[int, int]:
        """Dimensions of the slide at level 0 (highest resolution)."""
        self._check_open()
        return self._index.level_dimensions()[0]

    @property
    def level_dimensions(self) -> Tuple[Tuple[int, int], ...]:
        """Dimensions of each pyramid level."""
        self._check_open()
        return self._index.level_dimensions()

    @property
    def level_downsamples(self) -> Tuple[float, ...]:
        """Downsample factor for each level."""
        self._check_open()
        return self._index.level_downsamples()

    @property
    def properties(self) -> Mapping[str, str]:
        """Metadata properties as a read-only mapping."""
        self._check_open()
        return self._properties

    @property
    def associated_images(self) -> Mapping[str, Image.Image]:
        """Associated images (macro, label, thumbnail) as a lazy mapping."""
        self._check_open()
        return self._associated_images

    @property
    def color_profile(self) -> Optional[object]:
        """Embedded ICC color profile, or None if not available."""
        self._check_open()
        return None

    # ------------------------------------------------------------------
    # Reading operations
    # ------------------------------------------------------------------

    def get_best_level_for_downsample(self, downsample: float) -> int:
        """Get the best pyramid level for a given downsample factor."""
        self._check_open()
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

        # Ensure a per-process file handle (reopens after fork, e.g. in
        # PyTorch DataLoader worker processes).
        self._ensure_open_handle()

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

                tile = self._read_decoded_tile(idx)

                paste_x = tile_x - x0
                paste_y = tile_y - y0
                crop_x0 = max(0, x0 - tile_x)
                crop_y0 = max(0, y0 - tile_y)
                crop_x1 = min(tw, x0 + w - tile_x)
                crop_y1 = min(th, y0 + h - tile_y)

                if crop_x1 > crop_x0 and crop_y1 > crop_y0:
                    # Tiles are cached as RGBA, so the crop can be pasted
                    # directly without a per-read alpha conversion.
                    out.paste(
                        tile.crop((crop_x0, crop_y0, crop_x1, crop_y1)),
                        (paste_x + crop_x0, paste_y + crop_y0),
                    )

        return out

    def read_regions(
        self,
        regions: Iterable[Tuple[Tuple[int, int], int, Tuple[int, int]]],
        max_workers: Optional[int] = None,
    ) -> List[Image.Image]:
        """Read many regions in parallel and return them in input order.

        Each element of ``regions`` is a ``(location, level, size)`` tuple
        with the same semantics as :meth:`read_region`. The reads run on a
        thread pool against this shared instance, which is safe since
        0.3.4 (per-thread file handles and a locked cache). Scaling is
        bounded by the GIL for pure-Python work, but I/O-bound reads
        (network or slow mounts) and Pillow's GIL-releasing JPEG decode
        pipeline well in practice.

        Args:
            regions: iterable of ``(location, level, size)`` tuples.
            max_workers: thread pool size; ``None`` lets
                ThreadPoolExecutor choose (min(32, cpu_count + 4)).

        Returns:
            List of PIL.Image.Image (RGBA), one per region, in input order.
        """
        self._check_open()
        region_list = list(regions)
        if not region_list:
            return []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            return list(
                executor.map(
                    lambda region: self.read_region(region[0], region[1], region[2]),
                    region_list,
                )
            )

    def get_thumbnail(self, size: Tuple[int, int]) -> Image.Image:
        """Get a thumbnail image."""
        self._check_open()

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
        byte-bounded LRU cache. Accepts the cache argument for API compatibility.
        """
        self._check_open()
        # No-op for now. Could be extended to use a shared cache.
        pass

    def cache_info(self) -> Dict[str, int]:
        """Return tile-cache statistics for monitoring.

        Returns:
            Dict with ``entries`` (cached tiles), ``bytes`` (approximate
            payload bytes currently cached, RGBA) and ``max_bytes`` (the
            per-instance budget, 256 MiB by default). Peak memory of N
            threads sharing one instance is one cache, not N; N separate
            instances hold N caches (N x max_bytes worst case).
        """
        self._check_open()
        return self._tile_cache.cache_info()


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


__all__ = [
    "OpenSlide",
    "KfbSlide",
    "open_slide",
]
