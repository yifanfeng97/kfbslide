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
        region = slide.read_region((0, 0), 0, (256, 256))
        assert isinstance(region, Image.Image)
        assert region.mode == "RGBA"
        assert region.size == (256, 256)
        region.save(os.path.join(cache, "region_0_0_256x256.png"))

        # Out-of-bounds returns transparent black
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
