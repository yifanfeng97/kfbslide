# KFBSlide vs OpenSlide 对比测试设计

## 目标

生成一份可用于 README 宣传的对比报告，定量展示 `kfbslide` 读取 KFB 与 `OpenSlide` 读取 SVS 在典型病理图像操作上的速度差异。

## 背景

- `kfbslide` 是纯 Python 的 KFB 读取库，提供 OpenSlide 兼容 API。
- 测试文件：
  - KFB：`tests/sample.kfb`（约 828 MB，85,678 × 44,995，40×，6 层金字塔，tile 256）
  - SVS：`tests/207240-3 CD20.svs`（约 180 MB，42,009 × 22,721，40×，7 层金字塔）
- OpenSlide 需要系统库 `libopenslide-dev` 和 Python 包 `openslide-python`。

## 方案选择

采用 **方案 B：Notebook 风格详细报告**。相比轻量脚本，输出更多维度的图表；相比 pytest-benchmark，不引入库级依赖，更贴合一次性宣传测试的定位。

## 产物

```
benchmarks/
├── compare_kfb_svs.py      # 主脚本
└── results/                # 运行后生成，gitignored
    ├── report.md           # 最终报告（表格 + 图表）
    ├── single_region_latency.png
    ├── sequential_scan_latency.png
    ├── random_access_latency.png
    ├── level_latency.png
    └── cache_effect.png
```

## 对比维度

| 类别 | 具体操作 | 参数 |
|------|----------|------|
| 单区域读取 | `read_region((x, y), 0, (w, h))` | 256×256、512×512、1024×1024 |
| 连续扫描 | 读取 N 个相邻 256×256 瓦片 | N = 20、50、100 |
| 随机访问 | 读取 N 个随机位置 256×256 瓦片 | N = 20、50、100 |
| 金字塔层级 | 同一相对位置在不同 level 读 256×256 | Level 0、1、2、3 |
| 缓存效应 | 同一瓦片首次读取 vs 重复读取 | 重复 10 次（仅 kfbslide） |

## 度量指标

- 单次操作耗时（ms），使用 `time.perf_counter()` 测量。
- 平均耗时 ± 标准差，每项操作重复 5 次（脚本中可配置）。
- 瓦片吞吐（tiles/s）。
- 像素吞吐（MP/s）。

## 公平性控制

- 所有测试前执行一次 warm-up，排除文件系统缓存冷启动影响。
- KFB 和 SVS 选择相对位置一致的区域（如图像中心附近）。
- 连续扫描从同一左上角开始，按行优先顺序读取。
- 随机访问使用固定随机种子，保证两次运行可复现。
- kfbslide 每项测试前调用内部 tile cache 清空逻辑，避免前序测试的缓存影响冷读测试结果。

## 输出报告结构

`results/report.md` 包含以下章节：

1. 测试环境：Python 版本、Pillow 版本、OpenSlide 版本、操作系统、CPU、硬盘类型。
2. 测试文件信息：文件路径、尺寸、金字塔层数、文件大小。
3. 汇总表格：各项操作的 kfbslide vs OpenSlide 平均耗时与加速比。
4. 图表：
   - 单区域读取延迟（按区域尺寸分组）
   - 连续扫描延迟（按瓦片数分组）
   - 随机访问延迟（按瓦片数分组）
   - 金字塔层级延迟
   - kfbslide 缓存效应
5. 结论与 README 建议文案。

## 依赖

脚本运行时检测以下依赖，缺失则给出安装提示：

- `openslide-python`
- `matplotlib`
- `numpy`
- `Pillow`（已由 kfbslide 依赖）

系统库示例（Ubuntu/Debian）：

```bash
sudo apt-get install libopenslide-dev
```

## 运行方式

```bash
python benchmarks/compare_kfb_svs.py
```

默认输入：

- KFB：`tests/sample.kfb`
- SVS：`tests/207240-3 CD20.svs`

支持命令行参数覆盖：

```bash
python benchmarks/compare_kfb_svs.py --kfb path/to/file.kfb --svs path/to/file.svs --output benchmarks/results
```

## 边界处理

- 若 `tests/sample.kfb` 软链失效或文件不存在，脚本提示设置 `KFB_TEST_FILE` 环境变量。
- 若 `openslide` 导入失败，脚本输出安装命令后退出，不抛异常栈。
- 若某 level 在某一格式中不存在，则跳过该格式对应测试并在报告中标注 N/A。
- 若读取区域超出图像边界，使用 `min` 截断坐标，保证测试稳定运行。

## 非目标

- 不将本测试集成到 `tests/` 或 CI 流程。
- 不修改 `pyproject.toml` 增加依赖。
- 不做正确性像素级对比（假设两种库读取结果均正确）。
