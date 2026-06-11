"""LRU cache for decoded KFB tiles."""

from collections import OrderedDict
from typing import Optional

from PIL import Image


class _LRUCache:
    """OrderedDict-backed LRU cache for decoded tiles (O(1) ops)."""

    __slots__ = ("capacity", "_cache")

    def __init__(self, capacity: int):
        self.capacity = max(0, capacity)
        self._cache: OrderedDict[int, Image.Image] = OrderedDict()

    def get(self, key: int) -> Optional[Image.Image]:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: int, value: Image.Image) -> None:
        if self.capacity <= 0:
            return
        if key in self._cache:
            self._cache.move_to_end(key)
        elif len(self._cache) >= self.capacity:
            self._cache.popitem(last=False)
        self._cache[key] = value

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
