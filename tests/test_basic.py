"""Basic tests for KFBSlide.

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import collections.abc
import os
import struct

import pytest
from PIL import Image

import kfbslide
from kfbslide import (
    PROPERTY_NAME_MPP_X,
    PROPERTY_NAME_MPP_Y,
    PROPERTY_NAME_VENDOR,
    KfbError,
    KfbSlide,
    OpenSlide,
    OpenSlideError,
    OpenSlideUnsupportedFormatError,
    open_slide,
)
from kfbslide._kfbformat import KfbSection, _parse_header, parse_kfb_file

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


def test_open_zero_filled_file(tmp_path):
    """Opening a zero-filled file gives a clear error message."""
    bad = tmp_path / "zero.kfb"
    bad.write_bytes(b"\x00" * 2048)
    with pytest.raises(OpenSlideUnsupportedFormatError, match="zero-filled"):
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
            if (
                entry["width"] == slide._index.tile_size
                and entry["height"] == slide._index.tile_size
            ):
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


def test_corrupt_tile_boundary_repair():
    """Regression test for corrupt tile that poisons the next tile's offset.

    In some KFB files a tile's recorded size is too small and the JPEG stream
    itself is also corrupt. The corrupt tile cannot be recovered, but the
    reader must still locate the next tile boundary so that the following tile
    (whose offset was wrong) can be decoded correctly.
    """
    path = os.path.join(os.path.dirname(__file__), "data", "133923-3.kfb")
    if not os.path.exists(path):
        pytest.skip("133923-3.kfb not available")

    with OpenSlide(path) as slide:
        # Tile at (23552, 28416) has a corrupt JPEG stream and a recorded size
        # that is smaller than the real data range, so tile (24064, 6400) used
        # to start in the middle of the corrupt tile's data.
        with pytest.raises(Exception):
            slide.read_region((23552, 28416), 0, (256, 256))

        # After the failed read, the boundary should be repaired and the next
        # tile must be readable.
        region = slide.read_region((24064, 6400), 0, (256, 256))
        assert region.mode == "RGBA"
        assert region.size == (256, 256)

        # A tile further downstream should also remain readable.
        region = slide.read_region((24320, 6400), 0, (256, 256))
        assert region.mode == "RGBA"
        assert region.size == (256, 256)


# ---------------------------------------------------------------------------
# Multiprocessing (PyTorch DataLoader-style) regression test
# ---------------------------------------------------------------------------


def _fork_read_worker(slide, coords, n_reads, err_queue):
    """Run in a forked child process: read regions from the inherited slide."""
    try:
        parent_pid = slide._pid
        for i in range(n_reads):
            x, y = coords[i % len(coords)]
            img = slide.read_region((x, y), 0, (256, 256))
            assert img.mode == "RGBA"
            assert img.size == (256, 256)
        # The file handle must have been reopened in this child process.
        assert slide._pid != parent_pid
        assert slide._pid == os.getpid()
        err_queue.put(None)
    except Exception as e:  # noqa: BLE001
        err_queue.put(f"{type(e).__name__}: {e}")


def test_read_region_from_forked_workers():
    """A slide opened in the parent must be readable from forked children.

    Regression test for https://github.com/yifanfeng97/kfbslide/issues/1
    PyTorch DataLoader forks worker processes; the inherited file handle
    shares the OS-level file offset, so concurrent seek/read corrupted the
    JPEG stream ("cannot identify image file"). kfbslide now reopens the
    file handle per process.
    """
    path = _get_sample_path()
    if not path:
        pytest.skip("No KFB test file available")

    import multiprocessing as mp
    import queue as queue_module

    try:
        ctx = mp.get_context("fork")
    except ValueError:
        pytest.skip("fork start method not available")

    slide = OpenSlide(path)
    w0, h0 = slide.dimensions
    coords = [
        (0, 0),
        (min(4096, max(0, w0 - 256)), min(4096, max(0, h0 - 256))),
        (max(0, w0 // 2), max(0, h0 // 2)),
        (max(0, w0 - 256), max(0, h0 - 256)),
    ]

    n_workers, n_reads = 8, 25
    err_queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_fork_read_worker, args=(slide, coords, n_reads, err_queue)
        )
        for _ in range(n_workers)
    ]
    for p in procs:
        p.start()

    errors = []
    for _ in procs:
        try:
            errors.append(err_queue.get(timeout=120))
        except queue_module.Empty:
            errors.append("worker did not report")
    for p in procs:
        p.join(timeout=120)
    slide.close()

    assert all(p.exitcode == 0 for p in procs)
    assert errors == [None] * n_workers


# ---------------------------------------------------------------------------
# Large file (>4 GiB) regression tests: index table locators are u64
# ---------------------------------------------------------------------------

# Values decoded from a real 5,438,911,670-byte KFB file (scanner-produced).
# tile_index_end:  low dword 0x442F21C0 at payload 0x38, high dword 1 at 0x3C
# tile_index_start: low dword 0x42C18A40 at payload 0x40, high dword 1 at 0x44
_REAL_U64_IDX_END = 5_438_906_816
_REAL_U64_IDX_START = 5_414_947_392


def _make_header_payload(
    tile_count: int,
    height: int,
    width: int,
    idx_end: int,
    idx_start: int,
    tile_size: int = 256,
) -> bytes:
    """Build an 88-byte KFB section 0x01 payload."""
    p = bytearray(88)
    p[0:4] = b"KFB\x00"
    p[8:12] = struct.pack("<f", 1.6)
    p[12:16] = struct.pack("<I", tile_count)
    p[16:20] = struct.pack("<I", height)
    p[20:24] = struct.pack("<I", width)
    p[24:28] = struct.pack("<I", 40)
    p[28:32] = b"JPEG"
    p[40:48] = struct.pack("<q", 0)
    p[48:52] = struct.pack("<I", 220)  # section_0x02_offset
    p[52:56] = struct.pack("<I", 0)  # no label image
    p[56:64] = struct.pack("<Q", idx_end)
    p[64:72] = struct.pack("<Q", idx_start)
    p[72:76] = struct.pack("<f", 0.25)
    p[84:88] = struct.pack("<I", tile_size)
    return bytes(p)


def test_header_index_locators_are_u64():
    """Index table locators must decode as little-endian u64.

    Regression test: a >4 GiB KFB file has its tile index table beyond the
    2^32 boundary; the two locator fields in the header are 64-bit LE
    integers whose high dwords sit at payload offsets 0x3C and 0x44.
    Reading them as u32 wraps the offset and the parser then interprets
    tile JPEG data as an index table (garbage level_count/dimensions).
    """
    payload = _make_header_payload(
        tile_count=374_366,
        height=125_264,
        width=147_433,
        idx_end=_REAL_U64_IDX_END,
        idx_start=_REAL_U64_IDX_START,
    )
    section = KfbSection(
        sec_type=0x01, marker=0xF1, offset=0, footer_pos=92, payload=payload
    )
    header = _parse_header(section)
    assert header.tile_index_end == _REAL_U64_IDX_END
    assert header.tile_index_start == _REAL_U64_IDX_START


def test_header_index_locators_backward_compatible_u32():
    """Files below 4 GiB have zero high dwords; u64 decode keeps old values."""
    legacy_end = 1_469_244_117
    legacy_start = 1_461_866_965
    payload = _make_header_payload(
        tile_count=115_268,
        height=60_363,
        width=93_656,
        idx_end=legacy_end,
        idx_start=legacy_start,
    )
    section = KfbSection(
        sec_type=0x01, marker=0xF1, offset=0, footer_pos=92, payload=payload
    )
    header = _parse_header(section)
    assert header.tile_index_end == legacy_end
    assert header.tile_index_start == legacy_start


def _build_synthetic_kfb(path: str, idx_start: int) -> None:
    """Build a minimal valid KFB file whose tile index sits at ``idx_start``.

    Layout: section 0x01 (header) + section 0x02 (macro, 64x64 JPEG) +
    two 256x256 tile JPEGs + the 2-entry tile index table at ``idx_start``.
    """
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGB", (256, 256), (200, 30, 30)).save(buf, format="JPEG")
    tile_jpeg = buf.getvalue()
    buf = _io.BytesIO()
    Image.new("RGB", (64, 64), (30, 200, 30)).save(buf, format="JPEG")
    macro_jpeg = buf.getvalue()

    tile_count = 2
    idx_end = idx_start + tile_count * 64

    with open(path, "wb") as f:
        # Section 0x01: marker + 88-byte payload + footer.
        f.write(b"\xf1\x01\xee\xee")
        f.write(
            _make_header_payload(
                tile_count=tile_count,
                height=512,
                width=512,
                idx_end=idx_end,
                idx_start=idx_start,
            )
        )
        f.write(b"\xff\x01\xee\xee")

        # Section 0x02 (macro): marker + 64-byte payload + footer, followed
        # by the macro JPEG; rel_offset points at the JPEG data.
        sec2_off = 220
        sec2_payload_len = 64
        macro_data_off = sec2_off + 4 + sec2_payload_len + 4  # + footer
        f.seek(sec2_off)
        f.write(b"\xf2\x02\xee\xee")
        p = bytearray(sec2_payload_len)
        p[4:8] = struct.pack("<I", 64)  # height
        p[8:12] = struct.pack("<I", 64)  # width
        p[16:20] = struct.pack("<I", len(macro_jpeg))  # data_length
        p[20:24] = struct.pack("<I", macro_data_off - sec2_off)  # rel_offset
        f.write(p)
        f.write(b"\xff\x02\xee\xee")
        f.write(macro_jpeg)

        # Two tile JPEGs, contiguous; the parser locates them by scanning
        # for the JPEG SOI after the macro image.
        tile_data_off = f.tell()
        f.write(tile_jpeg)
        f.write(tile_jpeg)

        # Tile index table beyond 2^32 (sparse file hole in between).
        entries = [
            (0, 0),  # x, y of tile 1
            (256, 0),  # x, y of tile 2
        ]
        f.truncate(idx_start)
        f.seek(idx_start)
        for x, y in entries:
            e = bytearray(64)
            e[4:8] = struct.pack("<I", x)
            e[8:12] = struct.pack("<I", y)
            e[12:16] = struct.pack("<I", 256)  # width
            e[16:20] = struct.pack("<I", 256)  # height
            e[20:24] = struct.pack("<f", 40.0)  # scale
            e[32:36] = struct.pack("<I", len(tile_jpeg))  # size
            f.write(e)
        assert f.tell() == idx_end
    return tile_data_off


def test_sparse_kfb_index_table_beyond_4gib(tmp_path):
    """End-to-end: a KFB whose tile index lies past 2^32 must open correctly.

    Regression test for the >4 GiB report (5,438,911,670-byte file,
    level_count in the tens of thousands, 43-digit level-0 dimensions).
    The synthetic file is sparse, so the 4 GiB hole costs no real disk.
    """
    import sys as _sys

    if _sys.platform == "win32":
        pytest.skip("sparse-file test relies on POSIX sparse allocation")

    idx_start = 2**32 + 1024 * 1024
    path = str(tmp_path / "large.kfb")
    _build_synthetic_kfb(path, idx_start)

    # Skip if the filesystem does not actually keep the file sparse.
    if os.stat(path).st_blocks * 512 > 100 * 1024 * 1024:
        pytest.skip("filesystem does not support sparse files")

    info = parse_kfb_file(path)
    assert info.header.tile_index_start == idx_start
    assert info.header.tile_count == 2

    with OpenSlide(path) as slide:
        # Exactly one pyramid level (single scale 40.0), correct dimensions.
        assert slide.level_count == 1
        assert slide.dimensions == (512, 512)
        assert slide.level_downsamples == (1.0,)

        region = slide.read_region((0, 0), 0, (256, 256))
        assert region.mode == "RGBA"
        assert region.size == (256, 256)

        region = slide.read_region((256, 0), 0, (256, 256))
        assert region.mode == "RGBA"
        assert region.size == (256, 256)
