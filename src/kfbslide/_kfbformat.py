"""
KFB 文件格式解析器 —— 纯 Python 实现

基于对 KFB 文件二进制结构的分析：
- Section 0x01: 文件基本信息（版本、尺寸、瓦片数等）
- Section 0x02: 关联图像（macro/label/thumbnail）信息
- 关联图像数据: JPEG 格式，紧跟在 section 0x02 之后
- 瓦片数据: JPEG 格式，通过瓦片索引表定位

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import struct
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class KfbSection:
    """KFB Section: f? XX ee ee ... ff XX ee ee"""

    sec_type: int
    marker: int
    offset: int  # 在文件中的绝对偏移
    footer_pos: int  # footer 在文件中的绝对偏移
    payload: bytes


@dataclass
class KfbHeader:
    """KFB 文件头信息 (Section 0x01)"""

    magic: str  # "KFB"
    version: float  # 版本号 (如 1.6)
    tile_count: int  # 瓦片总数
    height: int  # 基础图像高度
    width: int  # 基础图像宽度
    scan_scale: int  # 扫描倍率 (如 40)
    format: str  # 压缩格式 (如 "JPEG")
    spend_time: int  # 扫描耗时
    scan_time: int  # 扫描时间戳
    tile_size: int  # 瓦片尺寸 (如 256)
    # 以下为解析出的字段
    section_0x02_offset: int  # section 0x02 (macro) 在文件中的偏移
    section_0x03_offset: int  # section 0x03 (label) 在文件中的偏移
    # 瓦片索引表范围
    tile_index_start: int  # 索引表起始偏移
    tile_index_end: int  # 索引表结束偏移
    # 原始未知字段
    _raw_field_0x3c: int
    mpp: float  # microns per pixel


@dataclass
class KfbAssocImage:
    """关联图像信息"""

    name: str
    width: int
    height: int
    data_length: int
    data_offset: int  # 在文件中的绝对偏移


@dataclass
class KfbFileInfo:
    """完整的 KFB 文件信息"""

    header: KfbHeader
    assoc_images: List[KfbAssocImage]
    tile_index_offset: int
    tile_data_offset: int


def _read_section(data: bytes, offset: int) -> Optional[KfbSection]:
    """读取一个 section: f? XX ee ee ... ff XX ee ee"""
    if offset + 4 > len(data):
        return None

    header = data[offset : offset + 4]
    if header[2:4] != b"\xee\xee":
        return None

    sec_type = header[1]
    sec_marker = header[0]

    # 寻找 footer: ff XX ee ee
    footer_pos = offset + 4
    max_search = min(offset + 100000, len(data) - 4)
    while footer_pos < max_search:
        if data[footer_pos : footer_pos + 4] == bytes([0xFF, sec_type, 0xEE, 0xEE]):
            break
        footer_pos += 1
    else:
        return None

    payload = data[offset + 4 : footer_pos]
    return KfbSection(
        sec_type=sec_type,
        marker=sec_marker,
        offset=offset,
        footer_pos=footer_pos,
        payload=payload,
    )


def _parse_header(section: KfbSection) -> KfbHeader:
    """解析 Section 0x01 (文件头)"""
    p = section.payload
    if len(p) < 88:
        raise ValueError(f"Header section too small: {len(p)} bytes")

    return KfbHeader(
        magic=p[0:4].decode("ascii", errors="replace").rstrip("\x00"),
        version=struct.unpack("<f", p[8:12])[0],
        tile_count=struct.unpack("<I", p[12:16])[0],
        height=struct.unpack("<I", p[16:20])[0],
        width=struct.unpack("<I", p[20:24])[0],
        scan_scale=struct.unpack("<I", p[24:28])[0],
        format=p[28:32].decode("ascii", errors="replace").rstrip("\x00"),
        spend_time=struct.unpack("<I", p[36:40])[0],
        scan_time=struct.unpack("<q", p[40:48])[0],
        tile_size=struct.unpack("<I", p[84:88])[0],
        section_0x02_offset=struct.unpack("<I", p[48:52])[0],
        section_0x03_offset=struct.unpack("<I", p[52:56])[0],
        tile_index_end=struct.unpack("<I", p[56:60])[0],
        _raw_field_0x3c=struct.unpack("<I", p[60:64])[0],
        tile_index_start=struct.unpack("<I", p[64:68])[0],
        mpp=struct.unpack("<f", p[72:76])[0],
    )


def _parse_image_section(data: bytes, section_offset: int, name: str) -> KfbAssocImage:
    """解析一个图像描述 section (0x02 或 0x03)"""
    p = data[section_offset + 4 : section_offset + 4 + 44]
    height = struct.unpack("<I", p[4:8])[0]
    width = struct.unpack("<I", p[8:12])[0]
    data_length = struct.unpack("<I", p[16:20])[0]
    rel_offset = struct.unpack("<I", p[20:24])[0]
    data_offset = section_offset + rel_offset
    return KfbAssocImage(
        name=name,
        width=width,
        height=height,
        data_length=data_length,
        data_offset=data_offset,
    )


def _parse_assoc_images(sec2: KfbSection, header: KfbHeader, data: bytes) -> List[KfbAssocImage]:
    """
    解析所有关联图像信息。

    文件结构:
    - section 0x02: macro 图像信息
    - section 0x03: label 图像信息
    - thumbnail: 动态生成，不在文件中预存
    """
    images = []

    # macro (section 0x02)
    images.append(_parse_image_section(data, sec2.offset, "macro"))

    # label (section 0x03)
    label_sec_offset = header.section_0x03_offset
    if label_sec_offset > 0 and data[label_sec_offset : label_sec_offset + 2] == b"\xf1\x03":
        images.append(_parse_image_section(data, label_sec_offset, "label"))

    return images


def parse_kfb_file(path: str) -> KfbFileInfo:
    """解析 KFB 文件，返回完整信息。

    按需读取数据：先读 1MB，如果 section 0x02 或 JPEG 标记超出范围，
    则动态扩展读取更多数据。
    """
    with open(path, "rb") as f:
        # 先读取前 1MB（通常足够包含文件头）
        data = bytearray(f.read(1024 * 1024))

        # 读取 section 0x01
        sec1 = _read_section(data, 0)
        if not sec1 or sec1.sec_type != 0x01:
            raise ValueError("Invalid KFB file: missing section 0x01")

        header = _parse_header(sec1)

        # 搜索 section 0x02 (它不一定紧跟在 section 0x01 之后)
        sec2_pos = sec1.footer_pos + 4
        needed = sec2_pos + 10000 + 4  # 搜索范围 + footer 安全区
        while len(data) < needed:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)

        sec2 = None
        max_search = min(sec2_pos + 10000, len(data) - 4)
        while sec2_pos < max_search:
            sec2 = _read_section(data, sec2_pos)
            if sec2 and sec2.sec_type == 0x02:
                break
            sec2_pos += 1
        else:
            raise ValueError("Invalid KFB file: missing section 0x02")

        # 关联图像
        assoc_images = _parse_assoc_images(sec2, header, data)

        # 计算瓦片区域
        last_assoc = assoc_images[-1]
        tile_index_offset = last_assoc.data_offset + last_assoc.data_length

        # 搜索第一个 JPEG 瓦片：确保数据足够覆盖搜索范围
        needed_jpeg = tile_index_offset + 1024 * 1024  # 至少再读 1MB
        while len(data) < needed_jpeg:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)

        first_jpeg = data.find(b"\xff\xd8\xff", tile_index_offset)
        if first_jpeg == -1:
            # 继续读取更多数据直到文件末尾
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                offset = len(data)
                data.extend(chunk)
                local_jpeg = chunk.find(b"\xff\xd8\xff")
                if local_jpeg != -1:
                    first_jpeg = offset + local_jpeg
                    break

    tile_data_offset = first_jpeg if first_jpeg != -1 else tile_index_offset

    return KfbFileInfo(
        header=header,
        assoc_images=assoc_images,
        tile_index_offset=tile_index_offset,
        tile_data_offset=tile_data_offset,
    )
