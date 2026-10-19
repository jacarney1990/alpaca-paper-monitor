"""Generate platform-standard Nexus PWA icon PNGs using only Python stdlib.

This creates public visual assets from mathematical shapes. No private assets,
credentials, external image sources, API traffic, or font files are involved.
"""
from __future__ import annotations
import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "static"

STAR = [(0, -158), (35, -35), (158, 0), (35, 35),
        (0, 158), (-35, 35), (-158, 0), (-35, -35)]


def polygon_contains(x, y, vertices):
    inside = False
    j = len(vertices) - 1
    for i in range(len(vertices)):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if (yi > y) != (yj > y):
            intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < intersect:
                inside = not inside
        j = i
    return inside


def png_chunk(kind, payload):
    return struct.pack("!I", len(payload)) + kind + payload + struct.pack(
        "!I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def make_png(size):
    rows = []
    for iy in range(size):
        raw = bytearray(b"\x00")
        for ix in range(size):
            x = (ix + .5) / size * 512
            y = (iy + .5) / size * 512
            cx, cy = x - 256, y - 256
            radius = math.hypot(cx, cy)
            # Rounded square app icon with transparent corners.
            inset_x = max(108 - x, 0, x - 404)
            inset_y = max(108 - y, 0, y - 404)
            if inset_x * inset_x + inset_y * inset_y > 108 * 108:
                raw.extend((0, 0, 0, 0))
                continue
            blend = min(1., (x + y) / 1024)
            col = (int(25 * (1 - blend) + 8 * blend),
                   int(61 * (1 - blend) + 16 * blend),
                   int(109 * (1 - blend) + 41 * blend), 255)
            if abs(radius - 171) < 2.4:
                col = (66, 117, 165, 255)
            elif abs(radius - 135) < 1.5:
                col = (51, 106, 153, 255)
            if polygon_contains(cx, cy, STAR):
                col = (115, 204, 255, 255) if cy < 0 else (106, 166, 255, 255)
            for ox, oy in ((-156, 0), (156, 0), (0, -156), (0, 156)):
                if (cx-ox)**2 + (cy-oy)**2 <= 17**2:
                    col = (142, 244, 215, 255)
                    break
            raw.extend(col)
        rows.append(bytes(raw))
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack("!2I5B", size, size, 8, 6, 0, 0, 0)
    return header + png_chunk(b"IHDR", ihdr) + png_chunk(
        b"IDAT", zlib.compress(b"".join(rows), level=8)
    ) + png_chunk(b"IEND", b"")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        target = ROOT / ("nexus-icon-%d.png" % size)
        target.write_bytes(make_png(size))
        print(target.relative_to(ROOT.parent.parent), target.stat().st_size)


if __name__ == "__main__":
    main()
