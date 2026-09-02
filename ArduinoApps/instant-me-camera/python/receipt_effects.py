import argparse
from pathlib import Path

from PIL import Image


def receipt_raster(image, width=384, threshold=95):
    height = max(1, round(width * image.height / image.width))
    gray = image.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    return gray.point(
        lambda value: 255 if value >= threshold else 0,
        mode="1",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Rasterize a generated image for a monochrome receipt printer"
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--threshold", type=int, default=95)
    args = parser.parse_args()

    with Image.open(args.image) as image:
        receipt = receipt_raster(image, args.width, args.threshold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt.save(args.output, optimize=True)


if __name__ == "__main__":
    main()