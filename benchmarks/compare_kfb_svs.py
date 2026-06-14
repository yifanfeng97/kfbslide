#!/usr/bin/env python3
"""
Benchmark: KFBSlide (KFB) vs OpenSlide (SVS)

Generates a comparison report under benchmarks/results/.

Usage:
    python benchmarks/compare_kfb_svs.py
    python benchmarks/compare_kfb_svs.py --kfb path/to/file.kfb --svs path/to/file.svs --output benchmarks/results
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

import kfbslide
from kfbslide import OpenSlide as KfbOpenSlide

try:
    import openslide
    from openslide import OpenSlide as OSlide

    HAS_OPENSLIDE = True
except ImportError:
    HAS_OPENSLIDE = False


REPORT_TEMPLATE = """# KFBSlide vs OpenSlide 性能对比报告

## 测试环境

| 项目 | 值 |
|------|-----|
| Python | {python_version} |
| Pillow | {pillow_version} |
| OpenSlide | {openslide_version} |
| NumPy | {numpy_version} |
| Matplotlib | {matplotlib_version} |
| CPU | {cpu_info} |

## 测试文件

| 格式 | 路径 | 大小 | 尺寸 | 层级数 |
|------|------|------|------|--------|
| KFB | `{kfb_path}` | {kfb_size} | {kfb_dims} | {kfb_levels} |
| SVS | `{svs_path}` | {svs_size} | {svs_dims} | {svs_levels} |

## 结果汇总

{summary_table}

## 图表

### 单区域读取延迟

![single_region_latency](single_region_latency.png)

### 连续扫描延迟

![sequential_scan_latency](sequential_scan_latency.png)

### 随机访问延迟

![random_access_latency](random_access_latency.png)

### 金字塔层级延迟

![level_latency](level_latency.png)

### 缓存效应

![cache_effect](cache_effect.png)

## 结论

{conclusion}
"""


@dataclass
class BenchmarkResult:
    """A single benchmark comparison."""

    name: str
    kfb_mean: float  # ms or other unit
    kfb_std: float
    svs_mean: float
    svs_std: float
    unit: str = "ms"

    @property
    def speedup(self) -> str:
        """Return kfb / svs ratio, or 'N/A' if svs is zero."""
        if self.svs_mean <= 0:
            return "N/A"
        ratio = self.svs_mean / self.kfb_mean
        return f"{ratio:.2f}×"


@dataclass
class SingleValueResult:
    """A single-format benchmark result (e.g. cache effect)."""

    name: str
    mean: float
    std: float
    unit: str = "ms"


@dataclass
class BenchmarkSuite:
    """Container for all benchmark results."""

    comparisons: List[BenchmarkResult] = field(default_factory=list)
    single_values: Dict[str, List[SingleValueResult]] = field(default_factory=dict)
    metadata: Dict[str, str] = field(default_factory=dict)


def _format_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _timeit(func: Callable[[], object], repeats: int = 5) -> Tuple[float, float]:
    """Return (mean_ms, stdev_ms) for calling func `repeats` times."""
    times: List[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000.0)
    return statistics.mean(times), statistics.stdev(times) if len(times) > 1 else 0.0


def _get_cpu_info() -> str:
    """Best-effort CPU info string."""
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def _make_chart(
    output_path: str,
    title: str,
    labels: List[str],
    kfb_values: List[float],
    svs_values: List[float],
    ylabel: str = "Time (ms)",
    value_labels: bool = True,
) -> None:
    """Create a side-by-side bar chart."""
    x = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.5), 6))
    bars1 = ax.bar(x - width / 2, kfb_values, width, label="KFBSlide (KFB)", color="#3498db")
    bars2 = ax.bar(x + width / 2, svs_values, width, label="OpenSlide (SVS)", color="#e74c3c")

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    if value_labels:
        for bars in (bars1, bars2):
            for bar in bars:
                height = bar.get_height()
                if height > 0:
                    ax.annotate(
                        f"{height:.1f}",
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha="center",
                        va="bottom",
                        fontsize=8,
                    )

    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def _make_single_value_chart(
    output_path: str,
    title: str,
    labels: List[str],
    series: Dict[str, List[float]],
    ylabel: str = "Time (ms)",
) -> None:
    """Create a grouped bar chart for single-format multi-series data."""
    n_series = len(series)
    x = np.arange(len(labels))
    width = 0.8 / n_series if n_series else 0.8

    fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.5), 6))
    colors = ["#3498db", "#2ecc71", "#9b59b6", "#f39c12"]
    for idx, (name, values) in enumerate(series.items()):
        offset = (idx - n_series / 2 + 0.5) * width
        bars = ax.bar(x + offset, values, width, label=name, color=colors[idx % len(colors)])
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.annotate(
                    f"{height:.2f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.savefig(output_path, dpi=150)
    plt.close(fig)


def _warmup(kfb: KfbOpenSlide, svs: OSlide) -> None:
    """Read one region from each slide to warm up file system / internal caches."""
    kfb.read_region((0, 0), 0, (512, 512))
    svs.read_region((0, 0), 0, (512, 512))


def _benchmark_single_region(
    kfb: KfbOpenSlide,
    svs: OSlide,
    repeats: int,
) -> List[BenchmarkResult]:
    """Benchmark single read_region calls at level 0."""
    results: List[BenchmarkResult] = []
    kfb_w, kfb_h = kfb.dimensions
    svs_w, svs_h = svs.dimensions

    sizes = [(512, 512), (1024, 1024)]
    for w, h in sizes:
        kfb_x = max(0, kfb_w // 2 - w // 2)
        kfb_y = max(0, kfb_h // 2 - h // 2)
        svs_x = max(0, svs_w // 2 - w // 2)
        svs_y = max(0, svs_h // 2 - h // 2)

        def kfb_read():
            kfb._tile_cache.clear()
            return kfb.read_region((kfb_x, kfb_y), 0, (w, h))

        def svs_read():
            return svs.read_region((svs_x, svs_y), 0, (w, h))

        kfb_mean, kfb_std = _timeit(kfb_read, repeats)
        svs_mean, svs_std = _timeit(svs_read, repeats)
        results.append(
            BenchmarkResult(
                name=f"read_region {w}×{h}",
                kfb_mean=kfb_mean,
                kfb_std=kfb_std,
                svs_mean=svs_mean,
                svs_std=svs_std,
            )
        )
    return results


def _benchmark_sequential_scan(
    kfb: KfbOpenSlide,
    svs: OSlide,
    repeats: int,
) -> List[BenchmarkResult]:
    """Benchmark reading a grid of adjacent tiles."""
    results: List[BenchmarkResult] = []
    tile_size = 512
    counts = [20, 50, 100]

    # Map counts to rectangular grids that fit in both images.
    grids = {
        20: (5, 4),
        50: (10, 5),
        100: (10, 10),
    }

    kfb_w, kfb_h = kfb.dimensions
    svs_w, svs_h = svs.dimensions

    for count in counts:
        cols, rows = grids[count]

        def make_reader(slide, w_max: int, h_max: int):
            def reader():
                for row in range(rows):
                    for col in range(cols):
                        x = min(col * tile_size, w_max - tile_size)
                        y = min(row * tile_size, h_max - tile_size)
                        slide.read_region((x, y), 0, (tile_size, tile_size))

            return reader

        kfb_reader = make_reader(kfb, kfb_w, kfb_h)
        svs_reader = make_reader(svs, svs_w, svs_h)

        def kfb_read():
            kfb._tile_cache.clear()
            kfb_reader()

        def svs_read():
            svs_reader()

        kfb_mean, kfb_std = _timeit(kfb_read, repeats)
        svs_mean, svs_std = _timeit(svs_read, repeats)
        results.append(
            BenchmarkResult(
                name=f"sequential scan {count} tiles",
                kfb_mean=kfb_mean,
                kfb_std=kfb_std,
                svs_mean=svs_mean,
                svs_std=svs_std,
            )
        )
    return results


def _benchmark_random_access(
    kfb: KfbOpenSlide,
    svs: OSlide,
    repeats: int,
) -> List[BenchmarkResult]:
    """Benchmark reading scattered tiles to defeat cache."""
    results: List[BenchmarkResult] = []
    tile_size = 512
    counts = [20, 50, 100]
    rng = random.Random(42)

    kfb_w, kfb_h = kfb.dimensions
    svs_w, svs_h = svs.dimensions

    # Pre-generate random locations for the largest count; smaller counts use subsets.
    kfb_locs = [
        (rng.randint(0, max(0, kfb_w - tile_size)), rng.randint(0, max(0, kfb_h - tile_size)))
        for _ in range(max(counts))
    ]
    rng.seed(42)  # Reset for SVS so relative distributions match.
    svs_locs = [
        (rng.randint(0, max(0, svs_w - tile_size)), rng.randint(0, max(0, svs_h - tile_size)))
        for _ in range(max(counts))
    ]

    for count in counts:
        kfb_subset = kfb_locs[:count]
        svs_subset = svs_locs[:count]

        def kfb_read():
            kfb._tile_cache.clear()
            for x, y in kfb_subset:
                kfb.read_region((x, y), 0, (tile_size, tile_size))

        def svs_read():
            for x, y in svs_subset:
                svs.read_region((x, y), 0, (tile_size, tile_size))

        kfb_mean, kfb_std = _timeit(kfb_read, repeats)
        svs_mean, svs_std = _timeit(svs_read, repeats)
        results.append(
            BenchmarkResult(
                name=f"random access {count} tiles",
                kfb_mean=kfb_mean,
                kfb_std=kfb_std,
                svs_mean=svs_mean,
                svs_std=svs_std,
            )
        )
    return results


def _benchmark_levels(
    kfb: KfbOpenSlide,
    svs: OSlide,
    repeats: int,
) -> List[BenchmarkResult]:
    """Benchmark read_region at different pyramid levels."""
    results: List[BenchmarkResult] = []
    tile_size = 512
    max_level = min(4, kfb.level_count, svs.level_count)

    # Location is always in level-0 coordinates for OpenSlide-compatible APIs.
    kfb_w0, kfb_h0 = kfb.dimensions
    svs_w0, svs_h0 = svs.dimensions
    kfb_x = max(0, kfb_w0 // 2 - tile_size // 2)
    kfb_y = max(0, kfb_h0 // 2 - tile_size // 2)
    svs_x = max(0, svs_w0 // 2 - tile_size // 2)
    svs_y = max(0, svs_h0 // 2 - tile_size // 2)

    for level in range(max_level):

        def kfb_read():
            kfb._tile_cache.clear()
            return kfb.read_region((kfb_x, kfb_y), level, (tile_size, tile_size))

        def svs_read():
            return svs.read_region((svs_x, svs_y), level, (tile_size, tile_size))

        kfb_mean, kfb_std = _timeit(kfb_read, repeats)
        svs_mean, svs_std = _timeit(svs_read, repeats)
        results.append(
            BenchmarkResult(
                name=f"level {level} 512×512",
                kfb_mean=kfb_mean,
                kfb_std=kfb_std,
                svs_mean=svs_mean,
                svs_std=svs_std,
            )
        )
    return results


def _benchmark_cache_effect(
    kfb: KfbOpenSlide,
    svs: OSlide,
    repeats: int,
) -> Dict[str, List[SingleValueResult]]:
    """Measure first-read (cold) vs cached-read times for fixed tiles.

    Returns a dict mapping series labels to [first_read, cached_read] results.
    """
    sizes = [512, 1024]
    kfb_w, kfb_h = kfb.dimensions
    svs_w, svs_h = svs.dimensions
    all_results: Dict[str, List[SingleValueResult]] = {}

    for tile_size in sizes:
        kfb_x = max(0, kfb_w // 2 - tile_size // 2)
        kfb_y = max(0, kfb_h // 2 - tile_size // 2)
        svs_x = max(0, svs_w // 2 - tile_size // 2)
        svs_y = max(0, svs_h // 2 - tile_size // 2)

        kfb_cold: List[float] = []
        kfb_warm: List[float] = []
        svs_cold: List[float] = []
        svs_warm: List[float] = []

        for _ in range(repeats):
            # KFB cold
            kfb._tile_cache.clear()
            start = time.perf_counter()
            kfb.read_region((kfb_x, kfb_y), 0, (tile_size, tile_size))
            kfb_cold.append((time.perf_counter() - start) * 1000.0)

            # KFB warm (same tile, cache should hit)
            start = time.perf_counter()
            kfb.read_region((kfb_x, kfb_y), 0, (tile_size, tile_size))
            kfb_warm.append((time.perf_counter() - start) * 1000.0)

            # SVS cold
            start = time.perf_counter()
            svs.read_region((svs_x, svs_y), 0, (tile_size, tile_size))
            svs_cold.append((time.perf_counter() - start) * 1000.0)

            # SVS warm
            start = time.perf_counter()
            svs.read_region((svs_x, svs_y), 0, (tile_size, tile_size))
            svs_warm.append((time.perf_counter() - start) * 1000.0)

        all_results[f"KFBSlide {tile_size}×{tile_size}"] = [
            SingleValueResult(
                name="first read",
                mean=statistics.mean(kfb_cold),
                std=statistics.stdev(kfb_cold) if len(kfb_cold) > 1 else 0.0,
            ),
            SingleValueResult(
                name="cached read",
                mean=statistics.mean(kfb_warm),
                std=statistics.stdev(kfb_warm) if len(kfb_warm) > 1 else 0.0,
            ),
        ]
        all_results[f"OpenSlide {tile_size}×{tile_size}"] = [
            SingleValueResult(
                name="first read",
                mean=statistics.mean(svs_cold),
                std=statistics.stdev(svs_cold) if len(svs_cold) > 1 else 0.0,
            ),
            SingleValueResult(
                name="cached read",
                mean=statistics.mean(svs_warm),
                std=statistics.stdev(svs_warm) if len(svs_warm) > 1 else 0.0,
            ),
        ]

    return all_results


def _build_summary_table(results: List[BenchmarkResult]) -> str:
    lines = [
        "| 操作 | KFBSlide (ms) | OpenSlide (ms) | 加速比 |",
        "|------|---------------|----------------|--------|",
    ]
    for r in results:
        kfb = f"{r.kfb_mean:.2f} ± {r.kfb_std:.2f}"
        svs = f"{r.svs_mean:.2f} ± {r.svs_std:.2f}"
        lines.append(f"| {r.name} | {kfb} | {svs} | {r.speedup} |")
    return "\n".join(lines)


def _build_conclusion(results: List[BenchmarkResult]) -> str:
    if not results:
        return "无可用结果。"
    # Use a representative subset for conclusion text.
    single_region = [r for r in results if r.name.startswith("read_region")]
    sequential = [r for r in results if r.name.startswith("sequential scan")]
    random_access = [r for r in results if r.name.startswith("random access")]

    lines = []
    if single_region:
        avg_speedup = statistics.mean(
            r.svs_mean / r.kfb_mean for r in single_region if r.kfb_mean > 0
        )
        lines.append(
            f"- 单区域读取：KFBSlide 平均约为 OpenSlide 的 **{avg_speedup:.2f}×**。"
        )
    if sequential:
        avg_speedup = statistics.mean(
            r.svs_mean / r.kfb_mean for r in sequential if r.kfb_mean > 0
        )
        lines.append(
            f"- 连续扫描：KFBSlide 平均约为 OpenSlide 的 **{avg_speedup:.2f}×**。"
        )
    if random_access:
        avg_speedup = statistics.mean(
            r.svs_mean / r.kfb_mean for r in random_access if r.kfb_mean > 0
        )
        lines.append(
            f"- 随机访问：KFBSlide 平均约为 OpenSlide 的 **{avg_speedup:.2f}×**。"
        )

    lines.append(
        "\n> 注：本次测试基于当前两台切片样本及运行环境，结果仅供参考；"
        "不同文件大小、压缩率及硬件会导致差异。"
    )
    return "\n".join(lines)


def _generate_charts(suite: BenchmarkSuite, output_dir: str) -> None:
    comparisons = suite.comparisons

    # 1. Single region latency
    single_region = [r for r in comparisons if r.name.startswith("read_region")]
    if single_region:
        labels = [r.name.replace("read_region ", "") for r in single_region]
        _make_chart(
            os.path.join(output_dir, "single_region_latency.png"),
            "Single Region Read Latency (level 0)",
            labels,
            [r.kfb_mean for r in single_region],
            [r.svs_mean for r in single_region],
        )

    # 2. Sequential scan latency
    sequential = [r for r in comparisons if r.name.startswith("sequential scan")]
    if sequential:
        labels = [r.name.replace("sequential scan ", "") for r in sequential]
        _make_chart(
            os.path.join(output_dir, "sequential_scan_latency.png"),
            "Sequential Tile Scan Latency",
            labels,
            [r.kfb_mean for r in sequential],
            [r.svs_mean for r in sequential],
        )

    # 3. Random access latency
    random_access = [r for r in comparisons if r.name.startswith("random access")]
    if random_access:
        labels = [r.name.replace("random access ", "") for r in random_access]
        _make_chart(
            os.path.join(output_dir, "random_access_latency.png"),
            "Random Tile Access Latency",
            labels,
            [r.kfb_mean for r in random_access],
            [r.svs_mean for r in random_access],
        )

    # 4. Level latency
    levels = [r for r in comparisons if r.name.startswith("level ")]
    if levels:
        labels = [r.name.replace("level ", "L") for r in levels]
        _make_chart(
            os.path.join(output_dir, "level_latency.png"),
            "Read Latency by Pyramid Level (512×512)",
            labels,
            [r.kfb_mean for r in levels],
            [r.svs_mean for r in levels],
        )

    # 5. Cache effect
    if "cache_effect" in suite.single_values:
        cache_results = suite.single_values["cache_effect"]
        labels = ["first read", "cached read"]
        _make_single_value_chart(
            os.path.join(output_dir, "cache_effect.png"),
            "First Read vs Cached Read",
            labels,
            {
                name: [v.mean for v in vals]
                for name, vals in cache_results.items()
            },
        )


def _bar_with_labels(ax, x, kfb_values, svs_values, width, kfb_label, svs_label, colors):
    """Draw grouped bars with value labels."""
    bars1 = ax.bar(x - width / 2, kfb_values, width, label=kfb_label, color=colors[0])
    bars2 = ax.bar(x + width / 2, svs_values, width, label=svs_label, color=colors[1])
    for bars in (bars1, bars2):
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.annotate(
                    f"{height:.1f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
    return bars1, bars2


def _generate_docs_charts(suite: BenchmarkSuite, docs_dir: str, chinese_font_path: Optional[str] = None) -> None:
    """Generate polished Chinese/English charts for README embedding."""
    os.makedirs(docs_dir, exist_ok=True)
    comparisons = suite.comparisons
    cache_results = suite.single_values.get("cache_effect", {})

    colors = {"kfb": "#3498db", "svs": "#e74c3c"}

    # Try to load a CJK-capable font for Chinese charts.
    zh_font_prop = None
    if chinese_font_path and os.path.exists(chinese_font_path):
        from matplotlib import font_manager as fm
        zh_font_prop = fm.FontProperties(fname=chinese_font_path)

    # ------------------------------------------------------------------
    # Chart 1: Single region cold read + cache hit (1x2 subplots)
    # ------------------------------------------------------------------
    def make_region_cache_chart(path: str, lang: str) -> None:
        labels = {
            "zh": {"title": "单区域读取 vs 缓存命中", "cold": "首次读取", "cached": "缓存命中", "kfb": "KFBSlide", "svs": "OpenSlide"},
            "en": {"title": "Single Region Read vs Cache Hit", "cold": "First read", "cached": "Cache hit", "kfb": "KFBSlide", "svs": "OpenSlide"},
        }[lang]

        single = [r for r in comparisons if r.name.startswith("read_region")]
        sizes = [r.name.replace("read_region ", "") for r in single]
        cold_kfb = [r.kfb_mean for r in single]
        cold_svs = [r.svs_mean for r in single]

        cached_kfb = []
        cached_svs = []
        for size in ["512×512", "1024×1024"]:
            kfb_vals = cache_results.get(f"KFBSlide {size}", [])
            svs_vals = cache_results.get(f"OpenSlide {size}", [])
            cached_kfb.append(kfb_vals[1].mean if len(kfb_vals) > 1 else 0.0)
            cached_svs.append(svs_vals[1].mean if len(svs_vals) > 1 else 0.0)

        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        x = np.arange(len(sizes))
        width = 0.35

        def apply_font(text_obj):
            if zh_font_prop is not None and lang == "zh":
                text_obj.set_fontproperties(zh_font_prop)

        # Cold read
        _bar_with_labels(axes[0], x, cold_kfb, cold_svs, width, labels["kfb"], labels["svs"], [colors["kfb"], colors["svs"]])
        axes[0].set_ylabel("Time (ms)")
        apply_font(axes[0].set_title(labels["cold"]))
        axes[0].set_xticks(x)
        for label in axes[0].get_xticklabels():
            apply_font(label)
        axes[0].legend()
        axes[0].grid(axis="y", linestyle="--", alpha=0.4)

        # Cache hit
        _bar_with_labels(axes[1], x, cached_kfb, cached_svs, width, labels["kfb"], labels["svs"], [colors["kfb"], colors["svs"]])
        axes[1].set_ylabel("Time (ms)")
        apply_font(axes[1].set_title(labels["cached"]))
        axes[1].set_xticks(x)
        for label in axes[1].get_xticklabels():
            apply_font(label)
        axes[1].legend()
        axes[1].grid(axis="y", linestyle="--", alpha=0.4)

        suptitle = fig.suptitle(labels["title"], fontsize=14, fontweight="bold")
        if lang == "zh":
            apply_font(suptitle)
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)

    # ------------------------------------------------------------------
    # Chart 2: Sequential + random access (1x2 subplots)
    # ------------------------------------------------------------------
    def make_scan_chart(path: str, lang: str) -> None:
        labels = {
            "zh": {"title": "瓦片扫描延迟", "seq": "连续扫描", "rand": "随机访问", "kfb": "KFBSlide", "svs": "OpenSlide"},
            "en": {"title": "Tile Scan Latency", "seq": "Sequential", "rand": "Random", "kfb": "KFBSlide", "svs": "OpenSlide"},
        }[lang]

        sequential = [r for r in comparisons if r.name.startswith("sequential scan")]
        random_access = [r for r in comparisons if r.name.startswith("random access")]
        seq_labels = [r.name.replace("sequential scan ", "") for r in sequential]
        rand_labels = [r.name.replace("random access ", "") for r in random_access]

        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        width = 0.35

        def apply_font(text_obj):
            if zh_font_prop is not None and lang == "zh":
                text_obj.set_fontproperties(zh_font_prop)

        # Sequential
        x = np.arange(len(seq_labels))
        _bar_with_labels(axes[0], x, [r.kfb_mean for r in sequential], [r.svs_mean for r in sequential], width, labels["kfb"], labels["svs"], [colors["kfb"], colors["svs"]])
        axes[0].set_ylabel("Time (ms)")
        apply_font(axes[0].set_title(labels["seq"]))
        axes[0].set_xticks(x)
        for label in axes[0].get_xticklabels():
            apply_font(label)
        axes[0].legend()
        axes[0].grid(axis="y", linestyle="--", alpha=0.4)

        # Random
        x = np.arange(len(rand_labels))
        _bar_with_labels(axes[1], x, [r.kfb_mean for r in random_access], [r.svs_mean for r in random_access], width, labels["kfb"], labels["svs"], [colors["kfb"], colors["svs"]])
        axes[1].set_ylabel("Time (ms)")
        apply_font(axes[1].set_title(labels["rand"]))
        axes[1].set_xticks(x)
        for label in axes[1].get_xticklabels():
            apply_font(label)
        axes[1].legend()
        axes[1].grid(axis="y", linestyle="--", alpha=0.4)

        suptitle = fig.suptitle(labels["title"], fontsize=14, fontweight="bold")
        if lang == "zh":
            apply_font(suptitle)
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)

    # ------------------------------------------------------------------
    # Chart 3: Pyramid level latency
    # ------------------------------------------------------------------
    def make_level_chart(path: str, lang: str) -> None:
        labels = {
            "zh": {"title": "金字塔层级读取延迟 (512×512)", "kfb": "KFBSlide", "svs": "OpenSlide"},
            "en": {"title": "Pyramid Level Read Latency (512×512)", "kfb": "KFBSlide", "svs": "OpenSlide"},
        }[lang]

        levels = [r for r in comparisons if r.name.startswith("level ")]
        level_labels = [r.name.replace("level ", "L") for r in levels]
        x = np.arange(len(level_labels))
        width = 0.35

        def apply_font(text_obj):
            if zh_font_prop is not None and lang == "zh":
                text_obj.set_fontproperties(zh_font_prop)

        fig, ax = plt.subplots(figsize=(8, 5))
        _bar_with_labels(ax, x, [r.kfb_mean for r in levels], [r.svs_mean for r in levels], width, labels["kfb"], labels["svs"], [colors["kfb"], colors["svs"]])
        ax.set_ylabel("Time (ms)")
        title = ax.set_title(labels["title"])
        if lang == "zh":
            apply_font(title)
        ax.set_xticks(x)
        ax.set_xticklabels(level_labels)
        ax.legend()
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        plt.tight_layout()
        plt.savefig(path, dpi=150)
        plt.close(fig)

    # Generate Chinese charts first with CJK font if available.
    make_region_cache_chart(os.path.join(docs_dir, "benchmark_region_zh.png"), "zh")
    make_scan_chart(os.path.join(docs_dir, "benchmark_scan_zh.png"), "zh")
    make_level_chart(os.path.join(docs_dir, "benchmark_level_zh.png"), "zh")

    # Generate English charts.
    make_region_cache_chart(os.path.join(docs_dir, "benchmark_region_en.png"), "en")
    make_scan_chart(os.path.join(docs_dir, "benchmark_scan_en.png"), "en")
    make_level_chart(os.path.join(docs_dir, "benchmark_level_en.png"), "en")


def _generate_report(suite: BenchmarkSuite, output_dir: str) -> str:
    from PIL import __version__ as pillow_version

    report_path = os.path.join(output_dir, "report.md")

    kfb_path = suite.metadata["kfb_path"]
    svs_path = suite.metadata["svs_path"]

    report = REPORT_TEMPLATE.format(
        python_version=suite.metadata.get("python_version", "unknown"),
        pillow_version=pillow_version,
        openslide_version=suite.metadata.get("openslide_version", "unknown"),
        numpy_version=np.__version__,
        matplotlib_version=plt.matplotlib.__version__,
        cpu_info=suite.metadata.get("cpu_info", "unknown"),
        kfb_path=kfb_path,
        kfb_size=_format_size(os.path.getsize(kfb_path)),
        kfb_dims=suite.metadata.get("kfb_dims", "unknown"),
        kfb_levels=suite.metadata.get("kfb_levels", "unknown"),
        svs_path=svs_path,
        svs_size=_format_size(os.path.getsize(svs_path)),
        svs_dims=suite.metadata.get("svs_dims", "unknown"),
        svs_levels=suite.metadata.get("svs_levels", "unknown"),
        summary_table=_build_summary_table(suite.comparisons),
        conclusion=_build_conclusion(suite.comparisons),
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    return report_path


def _print_summary(suite: BenchmarkSuite) -> None:
    print("\n=== KFBSlide vs OpenSlide Benchmark Summary ===\n")
    print(_build_summary_table(suite.comparisons))
    print()


def run_benchmark(
    kfb_path: str,
    svs_path: str,
    output_dir: str,
    repeats: int = 5,
    chinese_font_path: Optional[str] = None,
) -> BenchmarkSuite:
    """Run the full benchmark suite and return results."""
    suite = BenchmarkSuite()
    suite.metadata["kfb_path"] = kfb_path
    suite.metadata["svs_path"] = svs_path
    suite.metadata["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    suite.metadata["cpu_info"] = _get_cpu_info()
    suite.metadata["openslide_version"] = openslide.__version__ if HAS_OPENSLIDE else "N/A"

    print(f"Opening KFB: {kfb_path}")
    kfb = KfbOpenSlide(kfb_path)
    print(f"Opening SVS: {svs_path}")
    svs = OSlide(svs_path)

    suite.metadata["kfb_dims"] = f"{kfb.dimensions[0]} × {kfb.dimensions[1]}"
    suite.metadata["kfb_levels"] = str(kfb.level_count)
    suite.metadata["svs_dims"] = f"{svs.dimensions[0]} × {svs.dimensions[1]}"
    suite.metadata["svs_levels"] = str(svs.level_count)

    print("Warming up...")
    _warmup(kfb, svs)

    print("Benchmarking single region reads...")
    suite.comparisons.extend(_benchmark_single_region(kfb, svs, repeats))

    print("Benchmarking sequential scans...")
    suite.comparisons.extend(_benchmark_sequential_scan(kfb, svs, repeats))

    print("Benchmarking random access...")
    suite.comparisons.extend(_benchmark_random_access(kfb, svs, repeats))

    print("Benchmarking pyramid levels...")
    suite.comparisons.extend(_benchmark_levels(kfb, svs, repeats))

    print("Benchmarking cache effect...")
    suite.single_values["cache_effect"] = _benchmark_cache_effect(kfb, svs, repeats)

    kfb.close()
    svs.close()

    print("Generating charts...")
    _generate_charts(suite, output_dir)

    print("Generating docs charts...")
    _generate_docs_charts(suite, "docs", chinese_font_path)

    print("Generating report...")
    report_path = _generate_report(suite, output_dir)
    print(f"Report saved to: {report_path}")

    _print_summary(suite)
    return suite


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark KFBSlide (KFB) vs OpenSlide (SVS)."
    )
    parser.add_argument(
        "--kfb",
        default="tests/sample.kfb",
        help="Path to a KFB file (default: tests/sample.kfb)",
    )
    parser.add_argument(
        "--svs",
        default="tests/sample.svs",
        help='Path to an SVS file (default: "tests/sample.svs")',
    )
    parser.add_argument(
        "--output",
        default="benchmarks/results",
        help="Output directory for report and charts (default: benchmarks/results)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=10,
        help="Number of repeats per measurement (default: 10)",
    )
    parser.add_argument(
        "--chinese-font",
        default=None,
        help="Path to a TTF/OTF font supporting CJK glyphs for Chinese charts",
    )
    args = parser.parse_args()

    if not HAS_OPENSLIDE:
        print(
            "Error: openslide-python is not installed.\n\n"
            "Install instructions (Ubuntu/Debian):\n"
            "  sudo apt-get install libopenslide-dev\n"
            "  pip install openslide-python\n"
        )
        return 1

    kfb_path = os.path.abspath(args.kfb)
    svs_path = os.path.abspath(args.svs)

    if not os.path.exists(kfb_path):
        print(
            f"Error: KFB file not found: {kfb_path}\n"
            "You can set a custom path with --kfb or set the KFB_TEST_FILE environment variable."
        )
        return 1
    if not os.path.exists(svs_path):
        print(
            f"Error: SVS file not found: {svs_path}\n"
            "You can set a custom path with --svs."
        )
        return 1

    run_benchmark(kfb_path, svs_path, args.output, args.repeats, args.chinese_font)
    return 0


if __name__ == "__main__":
    sys.exit(main())
