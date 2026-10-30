"""Leitor de STL (binario e ASCII): volume, area e caixa envolvente.

Usado para AUTOVERIFICAR o que o Creo modelou: o servidor exporta o STL da peca e compara o volume/bbox
medidos com os valores analiticos esperados.
"""

from __future__ import annotations

import math
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


# ------------------------------------------------------------------ malha: carga e controle de qualidade
def load_triangles(path: str) -> list:
    """Lista de triangulos ((x,y,z),(x,y,z),(x,y,z)) de um STL binario ou ASCII."""
    with open(path, "rb") as fh:
        data = fh.read()
    is_binary = len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]
    tris = _triangles_binary(data) if is_binary else _triangles_ascii(data.decode("utf-8", "replace"))
    return [(tuple(a), tuple(b), tuple(c)) for a, b, c in tris]


def analyze_triangles(tris: list, weld: float = 1e-4) -> dict:
    """Controle de qualidade de uma malha: estanque (watertight), arestas abertas/nao-manifold, orientacao, triangulos
    degenerados ou repetidos, numero de cascas, caracteristica de Euler e volume assinado.

    Um STL exportado de um solido valido deve sair estanque, com 1 casca por solido e volume assinado positivo.
    """
    ids: dict = {}
    verts: list = []

    def vid(p):
        k = (round(p[0] / weld), round(p[1] / weld), round(p[2] / weld))
        i = ids.get(k)
        if i is None:
            i = ids[k] = len(verts)
            verts.append(p)
        return i

    edges: dict = {}                    # (a, b) a<b -> [sentido +1 / -1, ...]
    seen: dict = {}
    degenerate = duplicate = 0
    signed = area = 0.0
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    worst = 0.0
    shortest = float("inf")
    longest = 0.0
    for a, b, c in tris:
        ia, ib, ic = vid(a), vid(b), vid(c)
        ar = _tri_area(a, b, c)
        if len({ia, ib, ic}) < 3 or ar < 1e-12:
            degenerate += 1
            continue
        key = tuple(sorted((ia, ib, ic)))
        if key in seen:
            duplicate += 1
            continue
        seen[key] = True
        signed += _tri_volume(a, b, c)
        area += ar
        for u, v in ((ia, ib), (ib, ic), (ic, ia)):
            edges.setdefault((min(u, v), max(u, v)), []).append(1 if u < v else -1)
        for u, v in ((ia, ib), (ib, ic)):
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
        lens = [math.dist(a, b), math.dist(b, c), math.dist(c, a)]
        shortest = min(shortest, min(lens))
        longest = max(longest, max(lens))
        worst = max(worst, max(lens) ** 2 / (4 * math.sqrt(3) * ar))
    boundary = sum(1 for d in edges.values() if len(d) == 1)
    nonmanifold = sum(1 for d in edges.values() if len(d) > 2)
    flipped = sum(1 for d in edges.values() if len(d) == 2 and d[0] == d[1])
    shells = len({find(i) for i in range(len(verts))}) if verts else 0
    faces = len(seen)
    euler = len(verts) - len(edges) + faces
    watertight = bool(faces) and boundary == 0 and nonmanifold == 0
    out = {"triangles": len(tris), "unique_triangles": faces, "vertices": len(verts), "edges": len(edges),
           "degenerate_triangles": degenerate, "duplicate_triangles": duplicate, "open_edges": boundary,
           "non_manifold_edges": nonmanifold, "inconsistent_orientation_edges": flipped, "shells": shells,
           "watertight": watertight, "euler_characteristic": euler, "signed_volume_mm3": round(signed, 3),
           "area_mm2": round(area, 3), "volume_mm3": round(abs(signed), 3)}
    if faces:
        out["edge_length_mm"] = {"min": round(shortest, 5), "max": round(longest, 4)}
        out["worst_aspect_ratio"] = round(worst, 2)
    if watertight and shells:
        out["genus_estimate"] = max(0, round((2 * shells - euler) / 2))
    problems = []
    if not faces:
        problems.append("malha vazia")
    if boundary:
        problems.append(f"{boundary} arestas abertas (a malha nao fecha)")
    if nonmanifold:
        problems.append(f"{nonmanifold} arestas compartilhadas por mais de 2 triangulos")
    if flipped:
        problems.append(f"{flipped} arestas com orientacao inconsistente")
    if degenerate:
        problems.append(f"{degenerate} triangulos degenerados")
    if duplicate:
        problems.append(f"{duplicate} triangulos repetidos")
    if watertight and signed < 0:
        problems.append("volume assinado negativo: normais para dentro")
    out["problems"] = problems
    out["ok"] = not problems
    return out


def analyze(path: str, weld: float = 1e-4, max_triangles: int = 600000) -> dict:
    tris = load_triangles(path)
    if len(tris) > max_triangles:
        return {"ok": False, "triangles": len(tris), "problems": [f"malha com {len(tris)} triangulos: acima do limite de "
                                                                   f"{max_triangles} para a analise"]}
    out = analyze_triangles(tris, weld)
    out["file"] = path
    return out
