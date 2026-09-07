import argparse
from pathlib import Path

from PIL import Image


def receipt_raster(image, width=384, threshold=96, dither=True):
    height = max(1, round(width * image.height / image.width))
    gray = image.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    if dither:
        offset = 128 - threshold
        adjusted = gray.point(lambda value: max(0, min(255, value + offset)))
        return adjusted.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return gray.point(
        lambda value: 255 if value >= threshold else 0,
        mode="1",
    )


def packed_printer_bytes(raster):
    inverted = Image.eval(raster.convert("L"), lambda value: 255 - value)
    return inverted.convert("1").tobytes()


def send_to_printer(raster, bridge, feed_lines=3, rows_per_chunk=2):
    if raster.width != 384:
        raise ValueError("Printer raster must be 384 pixels wide")
    packed = packed_printer_bytes(raster)
    rows = raster.height
    if len(packed) != rows * 48:
        raise ValueError("Printer raster must contain exactly 48 bytes per row")
    if bridge.call("print_begin", rows) is not True:
        raise RuntimeError("Printer rejected the new job")
    try:
        for offset in range(0, rows, rows_per_chunk):
            chunk = packed[offset * 48:min(offset + rows_per_chunk, rows) * 48]
            if bridge.call("print_rows", list(chunk)) is not True:
                end = offset + len(chunk) // 48
                raise RuntimeError(f"Printer rejected rows {offset + 1}-{end}")
        if bridge.call("print_end", feed_lines) is not True:
            raise RuntimeError("Printer did not finish the job")
    except Exception:
        bridge.call("print_cancel")
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Rasterize a generated image for a monochrome receipt printer"
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--threshold", type=int, default=96)
    parser.add_argument("--no-dither", action="store_true")
    args = parser.parse_args()

    with Image.open(args.image) as image:
        receipt = receipt_raster(image, args.width, args.threshold, not args.no_dither)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt.save(args.output, optimize=True)


if __name__ == "__main__":
    main()