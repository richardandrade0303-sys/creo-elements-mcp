"""Interferencia exata entre duas malhas triangulares (Python puro, sem dependencias).

Duas pecas interferem quando as superficies se cruzam (aresta de um triangulo perfura o interior de um triangulo da
outra malha) ou quando uma esta inteira dentro da outra (teste de paridade do raio). Contato por face/aresta/vertice
(pecas encostadas, faces coplanares) NAO conta como interferencia: so travessias proprias, com tolerancia `eps`.
Furo exatamente sobre a borda de um triangulo (ex.: a diagonal de uma face quadrada, caso comum em pecas simetricas) e
ambiguo e e resolvido pela paridade dos pontos logo antes/depois do furo, para nao dar falso negativo.

Limites: malha de STL e uma aproximacao (circulos viram poligonos), entao uma interferencia menor que o erro de
tesselacao pode passar ou aparecer; o resultado traz `tessellation_note`.
"""

from __future__ import annotations

import math
import time

Tri = tuple


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def tri_box(t: Tri):
    return (min(p[0] for p in t), min(p[1] for p in t), min(p[2] for p in t),
            max(p[0] for p in t), max(p[1] for p in t), max(p[2] for p in t))


def _box_hit(a, b, pad=0.0) -> bool:
    return (a[0] <= b[3] + pad and b[0] <= a[3] + pad and a[1] <= b[4] + pad and b[1] <= a[4] + pad
            and a[2] <= b[5] + pad and b[2] <= a[5] + pad)


def segment_hit(p0, p1, tri: Tri, eps: float = 1e-6):
    """Segmento p0-p1 contra o triangulo (Moller-Trumbore). Devolve (tipo, t) ou None.

    tipo 'cross': fura o INTERIOR do triangulo; tipo 'edge': fura exatamente na borda (aresta/diagonal), caso ambiguo
    (a travessia pode ser real, quando a borda e so a diagonal de uma face plana, ou so um rasante)."""
    a, b, c = tri
    d = _sub(p1, p0)
    e1, e2 = _sub(b, a), _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    if abs(det) < 1e-12:
        return None                        # paralelo ou coplanar: contato, nao travessia
    f = 1.0 / det
    s = _sub(p0, a)
    u = f * _dot(s, h)
    if u < -eps or u > 1 + eps:
        return None
    q = _cross(s, e1)
    v = f * _dot(d, q)
    if v < -eps or u + v > 1 + eps:
        return None
    t = f * _dot(e2, q)
    if not (eps < t < 1 - eps):
        return None
    if u <= eps or v <= eps or u + v >= 1 - eps:
        return ("edge", t)
    return ("cross", t)


def segment_crosses_triangle(p0, p1, tri: Tri, eps: float = 1e-6) -> bool:
    """Segmento p0-p1 fura o INTERIOR do triangulo (so travessias proprias)."""
    hit = segment_hit(p0, p1, tri, eps)
    return bool(hit and hit[0] == "cross")


def triangle_events(t1: Tri, t2: Tri, eps: float = 1e-6):
    """(cruza, ambiguos): `cruza` = alguma aresta fura o interior do outro triangulo; `ambiguos` = lista de
    (p0, p1, t, dono_do_triangulo) para furos exatamente na borda ('a' = o triangulo furado e de t1)."""
    amb = []
    for i in range(3):
        hit = segment_hit(t1[i], t1[(i + 1) % 3], t2, eps)
        if hit:
            if hit[0] == "cross":
                return True, amb
            amb.append((t1[i], t1[(i + 1) % 3], hit[1], "b"))     # aresta de t1, malha furada = a de t2 ('b')
    for i in range(3):
        hit = segment_hit(t2[i], t2[(i + 1) % 3], t1, eps)
        if hit:
            if hit[0] == "cross":
                return True, amb
            amb.append((t2[i], t2[(i + 1) % 3], hit[1], "a"))     # aresta de t2, malha furada = a de t1 ('a')
    return False, amb


def triangles_cross(t1: Tri, t2: Tri, eps: float = 1e-6) -> bool:
    return triangle_events(t1, t2, eps)[0]


def point_in_mesh(pt, tris: list, eps: float = 1e-9) -> bool:
    """Paridade de um raio em +x (com leve inclinacao para fugir de arestas/vertices)."""
    d = (1.0, 1.3e-4, 0.7e-4)
    hits = 0
    for a, b, c in tris:
        e1, e2 = _sub(b, a), _sub(c, a)
        h = _cross(d, e2)
        det = _dot(e1, h)
        if abs(det) < 1e-14:
            continue
        f = 1.0 / det
        s = _sub(pt, a)
        u = f * _dot(s, h)
        if u < 0 or u > 1:
            continue
        q = _cross(s, e1)
        v = f * _dot(d, q)
        if v < 0 or u + v > 1:
            continue
        if f * _dot(e2, q) > eps:
            hits += 1
    return hits % 2 == 1


MAX_RESOLVE = 48            # furos ambiguos (na borda do triangulo) resolvidos por paridade, no maximo, por teste


def point_on_surface(pt, tris: list, tol: float = 1e-6) -> bool:
    """O ponto esta sobre a superficie da malha (a menos de `tol`)? Nesse caso a paridade do raio nao vale."""
    for a, b, c in tris:
        if (pt[0] < min(a[0], b[0], c[0]) - tol or pt[0] > max(a[0], b[0], c[0]) + tol
                or pt[1] < min(a[1], b[1], c[1]) - tol or pt[1] > max(a[1], b[1], c[1]) + tol
                or pt[2] < min(a[2], b[2], c[2]) - tol or pt[2] > max(a[2], b[2], c[2]) + tol):
            continue
        e1, e2 = _sub(b, a), _sub(c, a)
        n = _cross(e1, e2)
        nl = math.sqrt(_dot(n, n))
        if nl < 1e-18:
            continue
        w = _sub(pt, a)
        if abs(_dot(w, n)) / nl > tol:
            continue
        d11, d12, d22 = _dot(e1, e1), _dot(e1, e2), _dot(e2, e2)
        w1, w2 = _dot(w, e1), _dot(w, e2)
        den = d11 * d22 - d12 * d12
        if abs(den) < 1e-18:
            continue
        u = (d22 * w1 - d12 * w2) / den
        v = (d11 * w2 - d12 * w1) / den
        if u >= -1e-9 and v >= -1e-9 and u + v <= 1 + 1e-9:
            return True
    return False


def _edge_hit_is_crossing(p0, p1, t, tris_other) -> bool:
    """Furo exatamente na borda de um triangulo: e travessia real se um ponto logo antes e outro logo depois do furo
    estao em lados opostos (dentro/fora) da outra malha. Se um deles cai SOBRE a superficie (aresta rente a uma face,
    peca apoiada) e contato, nao interferencia. Resolve o caso das diagonais de faces planas simetricas."""
    s = min(1e-3, t / 2.0, (1.0 - t) / 2.0)
    before = tuple(p0[i] + (p1[i] - p0[i]) * (t - s) for i in range(3))
    after = tuple(p0[i] + (p1[i] - p0[i]) * (t + s) for i in range(3))
    if point_on_surface(before, tris_other) or point_on_surface(after, tris_other):
        return False
    return point_in_mesh(before, tris_other) != point_in_mesh(after, tris_other)


def _mesh_box(tris):
    if not tris:
        return None
    return (min(t[i][0] for t in tris for i in range(3)), min(t[i][1] for t in tris for i in range(3)),
            min(t[i][2] for t in tris for i in range(3)), max(t[i][0] for t in tris for i in range(3)),
            max(t[i][1] for t in tris for i in range(3)), max(t[i][2] for t in tris for i in range(3)))


def interference(tris_a: list, tris_b: list, eps: float = 1e-6, max_pairs: int = 4_000_000,
                 stop_after: int = 1, deadline: float | None = None) -> dict:
    """Testa duas malhas. `stop_after`: para ao achar essa quantidade de travessias (1 = so quer saber se interfere).
    `deadline` (time.time()): ao passar dele devolve o que tem com truncated=True (malhas de rosca sao enormes).

    Devolve {intersects, crossings, contained, pairs_tested, truncated}."""
    out = {"intersects": False, "crossings": 0, "contained": None, "pairs_tested": 0, "truncated": False,
           "tessellation_note": "malha STL aproxima circulos por poligonos: interferencias menores que o erro de "
                                "tesselacao podem nao aparecer"}
    ba, bb = _mesh_box(tris_a), _mesh_box(tris_b)
    if ba is None or bb is None or not _box_hit(ba, bb):
        return out
    region = (max(ba[0], bb[0]), max(ba[1], bb[1]), max(ba[2], bb[2]), min(ba[3], bb[3]), min(ba[4], bb[4]), min(ba[5], bb[5]))
    resolved = 0
    fa = [(t, tri_box(t)) for t in tris_a]
    fb = [(t, tri_box(t)) for t in tris_b]
    fa = [(t, b) for t, b in fa if _box_hit(b, region)]
    fb = [(t, b) for t, b in fb if _box_hit(b, region)]
    if fa and fb:
        # grade uniforme sobre B: celula ~ 2x o tamanho medio do triangulo
        sizes = [max(b[3] - b[0], b[4] - b[1], b[5] - b[2]) for _t, b in fb]
        cell = max(2.0 * sum(sizes) / len(sizes), 1e-3)
        grid: dict = {}
        for idx, (_t, b) in enumerate(fb):
            for ix in range(int(math.floor(b[0] / cell)), int(math.floor(b[3] / cell)) + 1):
                for iy in range(int(math.floor(b[1] / cell)), int(math.floor(b[4] / cell)) + 1):
                    for iz in range(int(math.floor(b[2] / cell)), int(math.floor(b[5] / cell)) + 1):
                        grid.setdefault((ix, iy, iz), []).append(idx)
        for n_a, (ta, boxa) in enumerate(fa):
            if deadline is not None and n_a % 64 == 0 and time.time() > deadline:
                out["truncated"] = True
                out["timed_out"] = True
                return out
            cand = set()
            for ix in range(int(math.floor(boxa[0] / cell)), int(math.floor(boxa[3] / cell)) + 1):
                for iy in range(int(math.floor(boxa[1] / cell)), int(math.floor(boxa[4] / cell)) + 1):
                    for iz in range(int(math.floor(boxa[2] / cell)), int(math.floor(boxa[5] / cell)) + 1):
                        cand.update(grid.get((ix, iy, iz), ()))
            for idx in cand:
                tb, boxb = fb[idx]
                if not _box_hit(boxa, boxb):
                    continue
                out["pairs_tested"] += 1
                if out["pairs_tested"] > max_pairs:
                    out["truncated"] = True
                    return out
                crossed, amb = triangle_events(ta, tb, eps)
                if not crossed and amb and resolved < MAX_RESOLVE:
                    for p0, p1, t, owner in amb[:2]:
                        resolved += 1
                        if _edge_hit_is_crossing(p0, p1, t, tris_b if owner == "b" else tris_a):
                            crossed = True
                            break
                elif not crossed and amb:
                    out["ambiguous_unresolved"] = out.get("ambiguous_unresolved", 0) + 1
                if crossed:
                    out["crossings"] += 1
                    out["intersects"] = True
                    if out["crossings"] >= stop_after:
                        return out
    if out["intersects"]:
        return out
    # sem travessia: uma pode estar inteira dentro da outra
    if point_in_mesh(tris_a[0][0], tris_b) and _inside_box(ba, bb):
        out.update(intersects=True, contained="a_inside_b")
    elif point_in_mesh(tris_b[0][0], tris_a) and _inside_box(bb, ba):
        out.update(intersects=True, contained="b_inside_a")
    elif _volume_overlap(tris_a, tris_b, region):
        # malhas simetricas (leque de triangulos com vertices coincidentes) podem cruzar so em vertices/arestas e o
        # teste de travessia nao ve. Amostra o volume comum das caixas: ponto dentro das DUAS malhas = interferencia.
        out.update(intersects=True, crossings=1, overlap_by="amostragem do volume comum")
    return out


def _volume_overlap(tris_a, tris_b, region, n: int = 4, min_thick: float = 1e-3) -> bool:
    """Ha ponto da regiao comum das caixas dentro das duas malhas? Contato (regiao sem espessura) nao conta."""
    spans = [region[i + 3] - region[i] for i in range(3)]
    if any(sp <= min_thick for sp in spans):
        return False
    for i in range(n):
        for j in range(n):
            for k in range(n):
                pt = (region[0] + spans[0] * (i + 0.5) / n, region[1] + spans[1] * (j + 0.5) / n,
                      region[2] + spans[2] * (k + 0.5) / n)
                if point_in_mesh(pt, tris_a) and point_in_mesh(pt, tris_b):
                    return True
    return False


def _inside_box(inner, outer, pad=1e-6) -> bool:
    return (inner[0] >= outer[0] - pad and inner[1] >= outer[1] - pad and inner[2] >= outer[2] - pad
            and inner[3] <= outer[3] + pad and inner[4] <= outer[4] + pad and inner[5] <= outer[5] + pad)
