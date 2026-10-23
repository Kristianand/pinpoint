"""Draws the Pinpoint app icons (a marigold map pin on atlas blue) as PNGs in static/.
Pure standard library, so it runs anywhere: python make_icons.py"""
import math
import os
import struct
import zlib

BG = (14, 27, 40)
PIN = (242, 169, 59)
RING = (232, 238, 243)

CX, CY, R = 0.5, 0.42, 0.22        # pin head
TIP = (0.5, 0.82)                  # pin point
HOLE = 0.085
PHI = math.acos(R / (TIP[1] - CY))
T1 = (CX - R * math.sin(PHI), CY + R * math.cos(PHI))
T2 = (CX + R * math.sin(PHI), CY + R * math.cos(PHI))


def in_triangle(p, a, b, c):
    def side(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = side(p, a, b), side(p, b, c), side(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def color_at(x, y):
    d = math.hypot(x - CX, y - CY)
    if d < HOLE:
        return BG
    if d < R or in_triangle((x, y), TIP, T1, T2):
        return PIN
    # faint ground ring under the pin tip
    e = math.hypot((x - TIP[0]) / 0.17, (y - TIP[1] - 0.005) / 0.045)
    if 0.78 < e < 1.0 and y > TIP[1] - 0.01:
        return tuple(round(b + (r - b) * 0.35) for r, b in zip(RING, BG))
    return BG


def render(size, ss=4):
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = color_at((px + (sx + .5) / ss) / size, (py + (sy + .5) / ss) / size)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            n = ss * ss
            row += bytes((acc[0] // n, acc[1] // n, acc[2] // n))
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, size):
    raw = render(size)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    for size in (180, 192, 512):
        write_png(os.path.join(out, f"icon-{size}.png"), size)
        print("wrote", f"icon-{size}.png")
