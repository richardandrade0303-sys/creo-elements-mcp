"""Leitor de STL (binario e ASCII): volume, area e caixa envolvente.

Usado para AUTOVERIFICAR o que o Creo modelou: o servidor exporta o STL da peca e compara o volume/bbox
medidos com os valores analiticos esperados.
"""

from __future__ import annotations

import re
import struct


def _tri_volume(a, b, c) -> float:
    """Volume assinado do tetraedro (origem, a, b, c)."""
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    ) / 6.0


def _tri_area(a, b, c) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * (cx * cx + cy * cy + cz * cz) ** 0.5


def _triangles_binary(data: bytes):
    n = struct.unpack("<I", data[80:84])[0]
    for i in range(n):
        f = struct.unpack_from("<12fH", data, 84 + 50 * i)
        yield f[3:6], f[6:9], f[9:12]


def _triangles_ascii(text: str):
    verts = [tuple(map(float, m)) for m in re.findall(
        r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)]
    for i in range(0, len(verts) - 2, 3):
        yield verts[i], verts[i + 1], verts[i + 2]


def read_stl(path: str) -> dict:
    """Devolve {triangles, volume, area, bbox_min, bbox_max, size}. Volume e sempre positivo (mm3)."""
    with open(path, "rb") as fh:
        data = fh.read()
    is_binary = len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]
    tris = _triangles_binary(data) if is_binary else _triangles_ascii(data.decode("utf-8", "replace"))
    vol = area = 0.0
    n = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for a, b, c in tris:
        n += 1
        vol += _tri_volume(a, b, c)
        area += _tri_area(a, b, c)
        for p in (a, b, c):
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
    if n == 0:
        return {"triangles": 0, "volume": 0.0, "area": 0.0, "bbox_min": None, "bbox_max": None, "size": None}
    return {
        "triangles": n,
        "volume": round(abs(vol), 3),
        "area": round(area, 3),
        "bbox_min": [round(v, 4) for v in lo],
        "bbox_max": [round(v, 4) for v in hi],
        "size": [round(h - l, 4) for l, h in zip(lo, hi)],
    }
