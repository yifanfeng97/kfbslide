"""Basic tests for KFBSlide.

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import collections.abc
import os

import pytest
from PIL import Image

import kfbslide
from kfbslide import (
    OpenSlide,
    OpenSlideError,
    OpenSlideUnsupportedFormatError,
    KfbSlide,
    open_slide,
    KfbError,
    PROPERTY_NAME_VENDOR,
    PROPERTY_NAME_MPP_X,
    PROPERTY_NAME_MPP_Y,
)

# Directory for test output images (gitignored)
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")


def _ensure_cache_dir() -> str:
    """Create and return the cache directory path."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return _CACHE_DIR


# ---------------------------------------------------------------------------
# Drop-in compatibility tests (no KFB file needed)
# ---------------------------------------------------------------------------


def test_module_exports():
    """Ensure all expected names are exported."""
    assert hasattr(kfbslide, "OpenSlide")
    assert hasattr(kfbslide, "OpenSlideError")
    assert hasattr(kfbslide, "OpenSlideUnsupportedFormatError")
    assert hasattr(kfbslide, "PROPERTY_NAME_VENDOR")
    assert hasattr(kfbslide, "PROPERTY_NAME_MPP_X")
    assert hasattr(kfbslide, "PROPERTY_NAME_MPP_Y")


def test_detect_format_none(tmp_path):
    """detect_format returns None for non-KFB files."""
    bad = tmp_path / "not_a_kfb.txt"
    bad.write_text("hello")
    assert OpenSlide.detect_format(str(bad)) is None


def test_open_invalid_file(tmp_path):
    """Opening a non-KFB file raises OpenSlideUnsupportedFormatError."""
    bad = tmp_path / "not_a_kfb.txt"
    bad.write_text("hello")
    with pytest.raises(OpenSlideUnsupportedFormatError):
        OpenSlide(str(bad))


def test_backward_compatibility_aliases():
    """Old names still point to the new ones."""
    assert KfbSlide is OpenSlide
    assert issubclass(KfbError, Exception)
    assert issubclass(OpenSlideError, Exception)


def test_open_slide_factory(tmp_path):
    """open_slide() factory still works and warns on deprecated arg."""
    bad = tmp_path / "not_a_kfb.txt"
    bad.write_text("hello")
    with pytest.warns(DeprecationWarning, match="tile_cache_size"):
        with pytest.raises(OpenSlideUnsupportedFormatError):
            open_slide(str(bad), tile_cache_size=128)


# ---------------------------------------------------------------------------
# Full API tests (require a real KFB file)
# ---------------------------------------------------------------------------


def _get_sample_path():
    """Return the path to a KFB test file if available."""
    # Prefer local sample.kfb (symlink)
    local = os.path.join(os.path.dirname(__file__), "sample.kfb")
    if os.path.exists(local):
        return local
    # Fallback to environment variable
    env = os.environ.get("KFB_TEST_FILE")
    if env and os.path.exists(env):
        return env
    return None


def test_openslide_api_with_sample():
    """Comprehensive OpenSlide API test using a real KFB file."""
    path = _get_sample_path()
    if not path:
        pytest.skip("No KFB test file available (tests/sample.kfb or KFB_TEST_FILE)")

    # Constructor
    slide = OpenSlide(path)

    # Context manager
    with slide:
        # --- Basic properties ---
        assert slide.level_count > 0
        assert slide.dimensions == slide.level_dimensions[0]
        assert len(slide.level_dimensions) == slide.level_count
        assert len(slide.level_downsamples) == slide.level_count

        # --- detect_format ---
        assert OpenSlide.detect_format(path) == "kfbio"

        # --- Properties (read-only mapping) ---
        props = slide.properties
        assert isinstance(props, collections.abc.Mapping)
        assert props[PROPERTY_NAME_VENDOR] == "kfbio"
        assert PROPERTY_NAME_MPP_X in props
        assert PROPERTY_NAME_MPP_Y in props
        # Read-only
        with pytest.raises(TypeError):
            props["foo"] = "bar"

        cache = _ensure_cache_dir()

        # --- Associated images (lazy mapping) ---
        assoc = slide.associated_images
        assert isinstance(assoc, collections.abc.Mapping)
        names = list(assoc.keys())
        assert len(names) >= 0
        # Lazy: accessing a key triggers read; save to cache/
        for name in names:
            img = assoc[name]
            assert isinstance(img, Image.Image)
            assert img.mode == "RGBA"
            img.save(os.path.join(cache, f"assoc_{name}.png"))

        # --- read_region returns RGBA ---
        w0, h0 = slide.dimensions

        # 1) 左上角 (已有)
        region = slide.read_region((0, 0), 0, (256, 256))
        assert isinstance(region, Image.Image)
        assert region.mode == "RGBA"
        assert region.size == (256, 256)
        region.save(os.path.join(cache, "region_0_0_256x256.png"))

        # 2) 中心偏左区域（通常有组织）
        cx, cy = w0 // 3, h0 // 3
        region = slide.read_region((cx, cy), 0, (256, 256))
        assert region.mode == "RGBA"
        region.save(os.path.join(cache, f"region_{cx}_{cy}_256x256.png"))

        # 3) 右下角附近（图像边缘，验证不越界）
        rx, ry = max(0, w0 - 256), max(0, h0 - 256)
        region = slide.read_region((rx, ry), 0, (256, 256))
        assert region.mode == "RGBA"
        region.save(os.path.join(cache, f"region_{rx}_{ry}_256x256.png"))

        # 4) 不同 level 的采样（level 1，如果存在）
        if slide.level_count > 1:
            w1, h1 = slide.level_dimensions[1]
            lx, ly = max(0, w1 // 2 - 128), max(0, h1 // 2 - 128)
            region = slide.read_region(
                (int(lx * slide.level_downsamples[1]), int(ly * slide.level_downsamples[1])),
                1, (256, 256)
            )
            assert region.mode == "RGBA"
            region.save(os.path.join(cache, "region_level1_center_256x256.png"))

        # 5) 越界区域（透明黑）
        edge = slide.read_region((-100, -100), 0, (300, 300))
        assert edge.mode == "RGBA"
        px = edge.getpixel((0, 0))
        assert px[3] == 0  # alpha = 0
        edge.save(os.path.join(cache, "region_oob_300x300.png"))

        # --- get_best_level_for_downsample ---
        level = slide.get_best_level_for_downsample(2.0)
        assert 0 <= level < slide.level_count

        # --- get_thumbnail ---
        thumb = slide.get_thumbnail((512, 512))
        assert isinstance(thumb, Image.Image)
        thumb.save(os.path.join(cache, "thumbnail_512x512.png"))

        # --- color_profile ---
        assert slide.color_profile is None

        # --- set_cache (no-op for compatibility) ---
        slide.set_cache(None)

    # After close, operations should raise
    slide.close()
    with pytest.raises(OpenSlideError):
        slide.read_region((0, 0), 0, (256, 256))


def test_read_region_invalid_level():
    """read_region with invalid level raises OpenSlideError."""
    path = _get_sample_path()
    if not path:
        pytest.skip("No KFB test file available")

    with OpenSlide(path) as slide:
        with pytest.raises(OpenSlideError):
            slide.read_region((0, 0), slide.level_count, (256, 256))


def test_level_properties_consistency():
    """Level properties are internally consistent."""
    path = _get_sample_path()
    if not path:
        pytest.skip("No KFB test file available")

    with OpenSlide(path) as slide:
        for i in range(slide.level_count):
            w, h = slide.level_dimensions[i]
            ds = slide.level_downsamples[i]
            # Level 0 dimensions / ds should approximately equal level dimensions
            if i > 0:
                expected_w = slide.dimensions[0] / ds
                expected_h = slide.dimensions[1] / ds
                assert abs(w - expected_w) <= 2
                assert abs(h - expected_h) <= 2


# ---------------------------------------------------------------------------
# Tile index self-healing and error handling tests
# ---------------------------------------------------------------------------


def _get_healing_test_path():
    """Return a KFB path usable for corruption/self-healing tests."""
    candidates = [
        os.environ.get("KFB_TEST_FILE"),
        os.path.join(os.path.dirname(__file__), "sample.kfb"),
        "/home/fengyifan/disk/code/escc-h2ihc/data/he_anno/1046961-3/1046961-3.kfb",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def test_real_world_tile_index_corruption():
    """Regression test for patient 1046961-3 tile index corruption.

    The KFB file contains two adjacent corrupt tile index entries. kfbslide
    should detect the invalid JPEG stream, recover the correct byte range, and
    return the requested region without latching the slide into an error state.
    """
    path = "/home/fengyifan/disk/code/escc-h2ihc/data/he_anno/1046961-3/1046961-3.kfb"
    if not os.path.exists(path):
        pytest.skip("Patient 1046961-3 KFB not available")

    failing_patches = [
        (66048, 18432),
        (66560, 18432),
        (66048, 25088),
        (66560, 25088),
    ]

    with OpenSlide(path) as slide:
        for x, y in failing_patches:
            img = slide.read_region((x, y), 0, (512, 512))
            assert img.mode == "RGBA"
            assert img.size == (512, 512)


def test_tile_decode_error_does_not_latch():
    """A single unrecoverable tile should not poison the whole slide handle."""
    path = _get_healing_test_path()
    if not path:
        pytest.skip("No KFB test file available")

    with OpenSlide(path) as slide:
        # Pick a full-size tile index near the start of the data.
        idx = 0
        while idx < len(slide._index.entries):
            entry = slide._index.entries[idx]
            if entry["width"] == slide._index.tile_size and entry["height"] == slide._index.tile_size:
                break
            idx += 1
        else:
            pytest.skip("Could not find a full-size tile")

        real_offset = slide._index.offsets[idx]
        real_size = slide._index.entries[idx]["size"]

        # Corrupt the tile so it cannot be decoded or healed.
        slide._index.entries[idx]["size"] = 4
        slide._index.offsets[idx] = 0

        try:
            with pytest.raises(Exception):
                slide.read_region(
                    (slide._index.entries[idx]["x"], slide._index.entries[idx]["y"]),
                    0,
                    (slide._index.tile_size, slide._index.tile_size),
                )
        finally:
            slide._index.entries[idx]["size"] = real_size
            slide._index.offsets[idx] = real_offset

        # Slide should still be usable.
        region = slide.read_region(
            (slide._index.entries[idx]["x"], slide._index.entries[idx]["y"]),
            0,
            (slide._index.tile_size, slide._index.tile_size),
        )
        assert region.mode == "RGBA"
        assert region.size == (slide._index.tile_size, slide._index.tile_size)


def test_self_healing_cumulative_offsets_remain_consistent():
    """Healing a tile must keep the cumulative offset table consistent."""
    path = _get_healing_test_path()
    if not path:
        pytest.skip("No KFB test file available")

    with OpenSlide(path) as slide:
        idx = 0
        while idx + 1 < len(slide._index.entries):
            e = slide._index.entries[idx]
            e_next = slide._index.entries[idx + 1]
            if (
                e["width"] == slide._index.tile_size
                and e["height"] == slide._index.tile_size
                and e_next["width"] == slide._index.tile_size
                and e_next["height"] == slide._index.tile_size
            ):
                break
            idx += 1
        else:
            pytest.skip("Could not find two adjacent full-size tiles")

        old_size = slide._index.entries[idx]["size"]
        old_next_size = slide._index.entries[idx + 1]["size"]

        # Corruption: tile N's size is too small by 1024 bytes.
        shift = 1024
        slide._index.entries[idx]["size"] -= shift
        slide._index.entries[idx + 1]["size"] -= shift
        # Note: we intentionally do not shift offsets[idx+1] here; the
        # self-healing for tile N will recompute all subsequent offsets.

        try:
            # Read tile N (triggers healing).
            region = slide.read_region(
                (slide._index.entries[idx]["x"], slide._index.entries[idx]["y"]),
                0,
                (slide._index.tile_size, slide._index.tile_size),
            )
            assert region.mode == "RGBA"
            assert region.size == (slide._index.tile_size, slide._index.tile_size)

            # Read tile N+1 to verify the cumulative offset table is still
            # consistent after healing.
            region_next = slide.read_region(
                (slide._index.entries[idx + 1]["x"], slide._index.entries[idx + 1]["y"]),
                0,
                (slide._index.tile_size, slide._index.tile_size),
            )
            assert region_next.mode == "RGBA"
            assert region_next.size == (slide._index.tile_size, slide._index.tile_size)
        finally:
            slide._index.entries[idx]["size"] = old_size
            slide._index.entries[idx + 1]["size"] = old_next_size
            # Recompute offsets to restore original state.
            cumulative = slide._index.offsets[idx]
            for i in range(idx, len(slide._index.offsets)):
                slide._index.offsets[i] = cumulative
                cumulative += slide._index.entries[i]["size"]


def test_self_healing_simulated_adjacent_corruption():
    """Simulate adjacent corrupt tile index entries and verify recovery."""
    path = _get_healing_test_path()
    if not path:
        pytest.skip("No KFB test file available")

    with OpenSlide(path) as slide:
        idx = 0
        while idx + 1 < len(slide._index.entries):
            e = slide._index.entries[idx]
            e_next = slide._index.entries[idx + 1]
            if (
                e["width"] == slide._index.tile_size
                and e["height"] == slide._index.tile_size
                and e_next["width"] == slide._index.tile_size
                and e_next["height"] == slide._index.tile_size
            ):
                break
            idx += 1
        else:
            pytest.skip("Could not find two adjacent full-size tiles")

        old_size = slide._index.entries[idx]["size"]
        old_next_offset = slide._index.offsets[idx + 1]
        old_next_size = slide._index.entries[idx + 1]["size"]

        # Simulate the real-world corruption pattern: tile N's size is too
        # small and tile N+1's offset is shifted forward by the same amount.
        shift = 1024
        slide._index.entries[idx]["size"] -= shift
        slide._index.offsets[idx + 1] += shift
        slide._index.entries[idx + 1]["size"] -= shift

        try:
            region = slide.read_region(
                (slide._index.entries[idx]["x"], slide._index.entries[idx]["y"]),
                0,
                (slide._index.tile_size, slide._index.tile_size),
            )
            assert region.mode == "RGBA"
            assert region.size == (slide._index.tile_size, slide._index.tile_size)
        finally:
            slide._index.entries[idx]["size"] = old_size
            slide._index.offsets[idx + 1] = old_next_offset
            slide._index.entries[idx + 1]["size"] = old_next_size
