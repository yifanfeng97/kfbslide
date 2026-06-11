"""
Example: read regions from a KFB file using the OpenSlide-compatible API.

Usage:
    python examples/read_region.py path/to/sample.kfb
"""

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

    # Read a 256x256 region at level 0 (returns RGBA)
    img = slide.read_region((0, 0), 0, (256, 256))
    print(f"Region mode: {img.mode}")
    img.save("region_0_0.png")

    # Get thumbnail
    thumb = slide.get_thumbnail((512, 512))
    thumb.save("thumbnail.jpg")

    # Access associated images lazily
    for name in slide.associated_images.keys():
        assoc_img = slide.associated_images[name]
        assoc_img.save(f"{name}.jpg")
        print(f"Saved {name}: {assoc_img.size} mode={assoc_img.mode}")

    # Properties
    props = slide.properties
    print(f"Vendor: {props.get(PROPERTY_NAME_VENDOR)}")
    print(f"MPP-X: {props.get(PROPERTY_NAME_MPP_X)}")

    slide.close()


if __name__ == "__main__":
    main()
