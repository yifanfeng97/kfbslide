"""
Example: read regions from a KFB file using the OpenSlide-compatible API.

Usage:
    python examples/read_region.py path/to/sample.kfb

Note:
    kfbslide returns RGBA images (OpenSlide-compatible). JPEG does not support
    an alpha channel, so this example converts to RGB before saving as JPG.

Author: Yifan Feng <evanfeng97@gmail.com>
"""

import random
import sys

from kfbslide import OpenSlide, PROPERTY_NAME_VENDOR, PROPERTY_NAME_MPP_X


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path/to/sample.kfb>")
        sys.exit(1)

    path = sys.argv[1]

    # OpenSlide-compatible usage
    slide = OpenSlide(path)

    print(f"level_count: {slide.level_count}")
    print(f"dimensions (level 0): {slide.dimensions}")
    for i in range(slide.level_count):
        w, h = slide.level_dimensions[i]
        ds = slide.level_downsamples[i]
        print(f"  Level {i}: {w}x{h}, downsample={ds}")

    # Read 6 random patches at level 0 (returns RGBA)
    patch_size = 256
    w0, h0 = slide.dimensions
    max_x = max(0, w0 - patch_size)
    max_y = max(0, h0 - patch_size)

    for i in range(6):
        x = random.randint(0, max_x)
        y = random.randint(0, max_y)
        img = slide.read_region((x, y), 0, (patch_size, patch_size))
        print(f"Patch {i}: ({x}, {y}) mode={img.mode}")
        img.convert("RGB").save(f"patch_{i}_{x}_{y}.jpg")

    # Get thumbnail
    thumb = slide.get_thumbnail((512, 512))
    thumb.convert("RGB").save("thumbnail.jpg")

    # Access associated images lazily
    for name in slide.associated_images.keys():
        assoc_img = slide.associated_images[name]
        assoc_img.convert("RGB").save(f"{name}.jpg")
        print(f"Saved {name}: {assoc_img.size} mode={assoc_img.mode}")

    # Properties
    props = slide.properties
    print(f"Vendor: {props.get(PROPERTY_NAME_VENDOR)}")
    print(f"MPP-X: {props.get(PROPERTY_NAME_MPP_X)}")

    slide.close()


if __name__ == "__main__":
    main()
