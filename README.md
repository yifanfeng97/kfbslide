# KFBSlide

纯 Python 实现的 KFB（KFBio）数字病理切片读取库，提供 **与 OpenSlide 完全兼容的 API**。

> **核心卖点**：跨平台、零原生依赖、无需 `libkfbslide.so`，Windows / macOS / Linux 均可直接 `pip install` 使用。
> 
> **可直接替换 OpenSlide**：`import kfbslide as openslide`

---

## 特性

- ✅ **纯 Python 实现** — 不依赖任何 C/C++ 扩展或 SO/DLL
- ✅ **OpenSlide 兼容 API** — 可直接作为 `openslide-python` 的 drop-in 替代品
- ✅ **金字塔多层级读取** — 自动解析 KFB 内部 40x / 20x / 10x / 5x / 2.5x / 1.25x 层级
- ✅ **关联图像读取** — macro、label、thumbnail
- ✅ **Tile LRU 缓存** — 重复读取同区域加速 **10~20 倍**
- ✅ **可选 TurboJPEG 加速** — 安装系统 `libjpeg-turbo` 后首次解码可快 2~3 倍
- ✅ **支持属性读取** — MPP、扫描倍率、瓦片尺寸等元数据

---

## 安装

### 基础安装（推荐）

```bash
pip install kfbslide
```

仅依赖 `Pillow`，任何平台都能直接安装。

### 带 TurboJPEG 加速（可选）

如需更快首次读取速度，可安装 TurboJPEG 后端：

```bash
# Ubuntu/Debian
sudo apt install libturbojpeg0-dev

# macOS
brew install jpeg-turbo

# 然后安装 Python 包
pip install kfbslide[turbo]
```

---

## 快速开始

### 作为 OpenSlide 的 drop-in 替代品

```python
import kfbslide as openslide

slide = openslide.OpenSlide("path/to/sample.kfb")

print(f"层级数: {slide.level_count}")
print(f"Level 0 尺寸: {slide.dimensions}")
for i in range(slide.level_count):
    print(f"  Level {i}: {slide.level_dimensions[i]} "
          f"downsample={slide.level_downsamples[i]}")

# 读取某个区域 (x, y) = (1000, 2000), level=0, size=256x256
# 注意：返回 RGBA 模式（与 OpenSlide 一致）
img = slide.read_region((1000, 2000), 0, (256, 256))
img.save("region.png")

# 读取缩略图
thumb = slide.get_thumbnail((512, 512))
thumb.save("thumbnail.jpg")

# 读取关联图像
macro = slide.associated_images["macro"]
macro.save("macro.jpg")

# 属性读取
vendor = slide.properties[openslide.PROPERTY_NAME_VENDOR]
mpp_x = slide.properties[openslide.PROPERTY_NAME_MPP_X]

slide.close()
```

### 原生 API（向后兼容）

```python
from kfbslide import open_slide

slide = open_slide("path/to/sample.kfb")
# ... 与上面相同 ...
slide.close()
```

---

## API 参考

### `OpenSlide(filename)`

打开一个 KFB 文件。

| 参数 | 说明 |
|------|------|
| `filename` | KFB 文件路径 |

### 类方法

| 方法 | 说明 |
|------|------|
| `OpenSlide.detect_format(filename)` | 检测文件格式，返回 `"kfbio"` 或 `None` |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `level_count` | `int` | 金字塔层级数 |
| `dimensions` | `(int, int)` | Level 0 尺寸（最高分辨率） |
| `level_dimensions` | `Tuple[(w, h), ...]` | 每层尺寸 |
| `level_downsamples` | `Tuple[float, ...]` | 每层下采样倍数 |
| `properties` | `Mapping[str, str]` | 元数据属性（只读映射） |
| `associated_images` | `Mapping[str, PIL.Image]` | 关联图像：只读映射，lazy 读取 |
| `color_profile` | `object \| None` | ICC 颜色配置文件（当前返回 `None`） |

### 方法

| 方法 | 说明 |
|------|------|
| `read_region(location, level, size)` | 读取指定区域，`location` 为 level 0 坐标，返回 **RGBA** |
| `get_best_level_for_downsample(downsample)` | 根据下采样倍数选择最佳层级 |
| `get_thumbnail(size)` | 生成缩略图 |
| `set_cache(cache)` | API 兼容方法（当前为 no-op） |
| `close()` | 关闭并释放资源 |

### 上下文管理器

```python
with OpenSlide("sample.kfb") as slide:
    img = slide.read_region((0, 0), 0, (256, 256))
# 自动 close
```

### 属性常量

```python
from kfbslide import (
    PROPERTY_NAME_VENDOR,      # "openslide.vendor"
    PROPERTY_NAME_MPP_X,       # "openslide.mpp-x"
    PROPERTY_NAME_MPP_Y,       # "openslide.mpp-y"
    PROPERTY_NAME_OBJECTIVE_POWER,  # "openslide.objective-power"
    # ...
)
```

---

## 与 OpenSlide API 的差异

| 对比项 | OpenSlide | KFBSlide (本库) |
|--------|-----------|-----------------|
| 依赖 | `libopenslide.so` + 各格式库 | 仅 `Pillow` |
| 跨平台 | 需编译 | Windows / macOS / Linux |
| `read_region` 返回 | `RGBA` | `RGBA` ✅ |
| `properties` 类型 | `Mapping` | `Mapping` ✅ |
| `associated_images` 类型 | `Mapping` | `Mapping` (lazy) ✅ |
| `detect_format` | ✅ | ✅ |
| `set_cache` | 有效 | no-op（兼容） |
| `color_profile` | 可能有效 | 返回 `None` |
| L0 (0,0) 256×256 | ~40 ms | **~0.7 ms** |
| L0 center 256×256 | ~9 ms | **~2.6 ms** |

---

## 性能

在 `sample.kfb`（71,748 × 56,282，82,595 tiles）上测试：

| 操作 | 时间 | 备注 |
|------|------|------|
| 首次读取 256×256 region | ~2.1 ms | Pillow 后端 |
| 缓存命中读取 | **~0.10 ms** | 22× 加速 |
| 扫描 20 个相邻 region（首次） | ~33 ms | 1.6 ms/region |
| 扫描 20 个相邻 region（缓存后） | **~2.2 ms** | 0.11 ms/region，15× 加速 |
| 安装 TurboJPEG 后首次读取 | ~0.7 ms | 再快 3× |

> 测试环境：Python 3.12，Pillow，SSD。

---

## 项目结构

```
kfbslide/
├── src/kfbslide/
│   ├── __init__.py          # 包入口，导出 OpenSlide API
│   ├── _slide.py            # OpenSlide 主类
│   ├── _kfbformat.py        # KFB 二进制格式解析
│   ├── _jpeg_backend.py     # JPEG 解码后端（Pillow / TurboJPEG）
│   ├── _cache.py            # LRU tile 缓存
│   └── _exceptions.py       # OpenSlideError / 兼容异常
├── tests/                   # 测试（含 sample.kfb 软链）
├── examples/                # 示例脚本
├── README.md
├── LICENSE
└── pyproject.toml
```

---

## 开发

```bash
git clone https://github.com/yifanfeng97/kfbslide.git
cd kfbslide

# 使用 uv 创建虚拟环境并安装
uv sync --extra dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check src tests
uv run mypy src/kfbslide
```

---

## 发布到 PyPI

```bash
# 构建
uv build

# 上传到 TestPyPI 测试
uv publish --index testpypi

# 上传到 PyPI
uv publish
```

---

## 已知限制

1. **只读**：目前不支持写入 KFB 文件。
2. **KFB v1.6**：在版本 1.6 文件上验证过。其他版本可能需要适配。
3. **JPEG 解码器差异**：不同 JPEG 后端（Pillow / TurboJPEG / SO）对同一 tile 的解码结果可能有 ±1~5 的像素差异，这在病理图像中通常可接受。
4. **set_cache / color_profile**：API 兼容，但当前为 no-op / None。

---

## 致谢

本项目基于对 KFB v1.6 二进制格式的逆向分析实现，参考了 OpenSlide 的 API 设计。

---

## License

[MIT](LICENSE)

Copyright (c) 2026 Yifan Feng
