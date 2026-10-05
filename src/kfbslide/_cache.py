"""LRU cache for decoded KFB tiles.

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

from PIL import Image


def _weight(value: Any) -> int:
    """Approximate in-memory size of a cached tile in bytes.

    Cached values are PIL RGBA tiles, so width * height * 4 is exact for
    them; anything else falls back to a small constant.
    """
    width = getattr(value, "width", 0)
    height = getattr(value, "height", 0)
    if width and height:
        return width * height * 4
    return 1024


class _LRUCache:
    """Byte-bounded LRU cache for decoded tiles (O(1) ops).

    Capacity is measured in approximate payload bytes rather than entry
    count, so memory stays predictable across tile sizes. All operations
    take a lock, so one cache shared by multiple threads (i.e. one
    OpenSlide instance read concurrently) stays consistent.
    """

    __slots__ = ("max_bytes", "_cache", "_bytes", "_lock")

    def __init__(self, max_bytes: int):
        self.max_bytes = max(0, int(max_bytes))
        self._cache: OrderedDict[int, Image.Image] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: int) -> Optional[Image.Image]:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def put(self, key: int, value: Image.Image) -> None:
        if self.max_bytes <= 0:
            return
        weight = _weight(value)
        with self._lock:
            if key in self._cache:
                self._bytes -= _weight(self._cache.pop(key))
            # Always keep the newest entry even if it alone exceeds the cap.
            while self._cache and self._bytes + weight > self.max_bytes:
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= _weight(evicted)
            self._cache[key] = value
            self._bytes += weight

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._bytes = 0

    def cache_info(self) -> Dict[str, int]:
        """Return {"entries", "bytes", "max_bytes"} snapshot for monitoring."""
        with self._lock:
            return {
                "entries": len(self._cache),
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
