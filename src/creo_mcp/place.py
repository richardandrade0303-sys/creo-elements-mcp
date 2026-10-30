"""Posicionamento e composicao de `parts.Plan`: orientar uma peca em qualquer eixo, renomear, repetir (padroes) e juntar.

Toda peca da biblioteca (ferragens, rolamentos, polias, perfis) e construida num referencial LOCAL com o eixo da peca em +z
e a origem na face inicial. `orient` leva esse referencial para o modelo global: eixo +x/-x/+y/-y/+z/-z e origem (x, y, z),
usando so rotacoes proprias (sem espelhar) e passos 'extrude'/'cut' (o Creo Express nao tem REVOLVE nem MOVE, entao a peca ja
nasce na posicao final, como nas montagens por coordenadas verificadas ao vivo).

Passos de helice (rosca) e de chanfro/filete/casca nao sao reorientaveis: ficam so em z com a origem em (0,0,0).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

from .parts import PartError, Plan, Step

# Matriz local -> global (linhas = eixos globais x, y, z); todas com determinante +1
_ROT = {
    "+z": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "-z": ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
    "+x": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "-x": ((0, 0, -1), (1, 0, 0), (0, -1, 0)),
    "+y": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "-y": ((0, -1, 0), (0, 0, -1), (1, 0, 0)),
}
AXES = tuple(_ROT)


@dataclass
class Built:
    """Resultado de um construtor de biblioteca: o plano de execucao + lista de materiais + dados de projeto."""
    plan: Plan
    bom: list = field(default_factory=list)       # [{"item", "category", "qty", "parts", "mass_g", "note"}]
    info: dict = field(default_factory=dict)       # dimensoes calculadas (para mostrar ao usuario)

    def to_dict(self) -> dict:
        return {"name": self.plan.name, "parts": self.plan.parts(), "bom": self.bom, "info": self.info,
                "notes": self.plan.notes}


def norm_axis(axis: str) -> str:
    a = str(axis or "z").strip().lower().replace(" ", "")
    if a[:1] not in "+-":
        a = "+" + a
    if a not in _ROT:
        raise PartError(f"eixo invalido {axis!r}: use x, y, z, -x, -y ou -z")
    return a


def rotation(axis: str):
    return _ROT[norm_axis(axis)]


def _apply(m, p, org):
    return tuple(sum(m[i][j] * p[j] for j in range(3)) + org[i] for i in range(3))


def _map_box(box, m, org):
    (x0, y0, z0), (x1, y1, z1) = box
    pts = [_apply(m, (x, y, z), org) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    return (tuple(round(min(p[i] for p in pts), 9) for i in range(3)), tuple(round(max(p[i] for p in pts), 9) for i in range(3)))


def _map_loop(lp, m, org, a: int, plane_l: list, plane_g: list):
    """Contorno 2D do plano local (eixo normal `a`) -> contorno 2D do plano global correspondente."""
    def g(u, v):
        p = [0.0, 0.0, 0.0]
        p[plane_l[0]], p[plane_l[1]] = u, v
        q = _apply(m, p, org)
        return q[plane_g[0]], q[plane_g[1]]

    if lp[0] == "c":
        cx, cy = g(lp[1], lp[2])
        return ("c", cx, cy, lp[3])
    if lp[0] == "r":
        (x1, y1), (x2, y2) = g(lp[1], lp[2]), g(lp[3], lp[4])
        return ("r", min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if lp[0] == "p":
        return ("p", [g(x, y) for x, y in lp[1]])
    raise PartError(f"contorno '{lp[0]}' desconhecido")


def orient(plan: Plan, axis: str = "z", origin=(0.0, 0.0, 0.0), name: str | None = None) -> Plan:
    """Copia de `plan` com o eixo local z levado para `axis` e a origem local para `origin` (coordenadas globais)."""
    ax = norm_axis(axis)
    m = _ROT[ax]
    org = tuple(float(v) for v in origin)
    if len(org) != 3:
        raise PartError("origin precisa de 3 coordenadas")
    ident = ax == "+z" and org == (0.0, 0.0, 0.0)
    steps = []
    for st in plan.steps:
        if st.kind in ("extrude", "cut"):
            a = "xyz".index(st.axis)
            k = next(i for i in range(3) if m[i][a] != 0)          # eixo global do plano deste passo
            sgn = m[k][a]
            plane_l = [i for i in range(3) if i != a]
            plane_g = [i for i in range(3) if i != k]
            if st.direction == "+w":
                lo, hi = st.z0, st.z0 + st.dz
            else:
                lo, hi = st.z0 - st.dz, st.z0
            g1, g2 = sgn * lo + org[k], sgn * hi + org[k]
            glo, ghi = min(g1, g2), max(g1, g2)
            loops = [_map_loop(lp, m, org, a, plane_l, plane_g) for lp in st.loops]
            steps.append(dataclasses.replace(st, loops=loops, axis="xyz"[k], z0=glo, dz=ghi - glo, direction="+w"))
        elif ident:
            steps.append(st)
        else:
            raise PartError(f"o passo '{st.label or st.kind}' ({st.kind}) nao pode ser reorientado/deslocado; use a peca em "
                            "z com origem 0,0,0 (roscas helicoidais e chanfros ficam so nessa posicao)")
    bb = {k: _map_box(v, m, org) for k, v in plan.part_bboxes.items()}
    exp = _map_box(plan.expected_bbox, m, org) if plan.expected_bbox else None
    return dataclasses.replace(plan, name=name or plan.name, steps=steps, expected_bbox=exp, part_bboxes=bb)


def rename_plan(plan: Plan, fn, name: str | None = None) -> Plan:
    """Renomeia as pecas (fn: nome -> novo nome); atualiza passos, bbox, exportacao e amostras de volume."""
    steps = [dataclasses.replace(s, part=fn(s.part)) for s in plan.steps]
    return dataclasses.replace(
        plan, name=name or plan.name, steps=steps, main_part=fn(plan.main_part) if plan.main_part else "",
        export_parts=[fn(p) for p in plan.export_parts], volume_parts=[(fn(p), v) for p, v in plan.volume_parts],
        part_bboxes={fn(k): v for k, v in plan.part_bboxes.items()})


def with_prefix(plan: Plan, prefix: str, name: str | None = None) -> Plan:
    return rename_plan(plan, lambda n: f"{prefix}_{n}" if prefix else n, name)


def merge(name: str, plans: list, notes: str = "") -> Plan:
    """Junta varios Plans (pecas distintas) num so. Nomes de peca repetidos sao recusados."""
    seen: set = set()
    steps, bboxes, export, vol_parts = [], {}, [], []
    total = 0.0
    for p in plans:
        for part in p.parts():
            if part in seen:
                raise PartError(f"peca repetida na composicao: {part}")
            seen.add(part)
        steps += p.steps
        if p.part_bboxes:
            bboxes.update(p.part_bboxes)
        elif p.main_part and p.expected_bbox:                    # peca unica do catalogo: o bbox esperado e o da peca
            bboxes[p.main_part] = p.expected_bbox
        export += [e for e in p.export_parts if e not in export]
        if p.volume_parts:
            vol_parts += list(p.volume_parts)
            total += sum(v for _n, v in p.volume_parts)
        elif p.expected_volume and p.main_part:
            vol_parts.append((p.main_part, p.expected_volume))
            total += p.expected_volume
    # varias pecas iguais -> amostra: o STL multi-peca so exporta a primeira, entao 1 representante por volume (ate 6)
    sample, keys = [], set()
    for n, v in vol_parts:
        k = round(v, 1)
        if k not in keys and len(sample) < 6:
            keys.add(k)
            sample.append((n, v))
    main = plans[0].main_part if plans else ""
    # o construtor soma os STL das pecas AMOSTRADAS, entao o volume esperado e a soma da amostra (nao do total)
    total = sum(v for _n, v in sample)
    return Plan(name, steps, main, bboxes.get(main), total, volume_tol=max((p.volume_tol for p in plans), default=0.03),
                export_parts=export, volume_parts=sample, part_bboxes=bboxes, notes=notes)


def merge_built(name: str, items: list, notes: str = "", info: dict | None = None) -> Built:
    return Built(merge(name, [b.plan for b in items], notes), [line for b in items for line in b.bom], info or {})


# ------------------------------------------------------------------ padroes de posicao
def pattern_points(kind: str, **kw) -> list:
    """Lista de (x, y) para repetir furos/pecas.

    linear:   count, dx, dy=0, x0=0, y0=0
    grid:     nx, ny, dx, dy, x0=0, y0=0
    circular: count, pcd (ou radius), cx=0, cy=0, start_deg=0, sweep_deg=360
    """
    k = str(kind).lower()
    f = lambda key, default=0.0: float(kw.get(key, default))      # noqa: E731
    pts: list = []
    if k == "linear":
        n = int(kw.get("count", 0))
        if n < 1 or n > 500:
            raise PartError("count deve estar entre 1 e 500")
        pts = [(f("x0") + i * f("dx"), f("y0") + i * f("dy")) for i in range(n)]
    elif k == "grid":
        nx, ny = int(kw.get("nx", 0)), int(kw.get("ny", 0))
        if nx < 1 or ny < 1 or nx * ny > 500:
            raise PartError("nx e ny devem ser >= 1 e nx*ny <= 500")
        pts = [(f("x0") + i * f("dx"), f("y0") + j * f("dy")) for j in range(ny) for i in range(nx)]
    elif k == "circular":
        n = int(kw.get("count", 0))
        if n < 1 or n > 500:
            raise PartError("count deve estar entre 1 e 500")
        r = float(kw["radius"]) if "radius" in kw else float(kw.get("pcd", 0)) / 2.0
        if r <= 0:
            raise PartError("circular precisa de pcd (ou radius) > 0")
        sweep = f("sweep_deg", 360.0)
        full = abs(sweep - 360.0) < 1e-9
        step = sweep / n if full else (sweep / (n - 1) if n > 1 else 0.0)
        pts = [(f("cx") + r * math.cos(math.radians(f("start_deg") + i * step)),
                f("cy") + r * math.sin(math.radians(f("start_deg") + i * step))) for i in range(n)]
    else:
        raise PartError("padrao desconhecido: use linear, grid ou circular")
    return [(round(x, 6), round(y, 6)) for x, y in pts]


# ------------------------------------------------------------------ conferencia geometrica do proprio plano
def steps_box(plan: Plan, part: str):
    """Caixa (min, max) coberta pelos passos 'extrude' de uma peca, em coordenadas globais (nao considera cortes).

    Serve para conferir, antes de ir ao Creo, que o bbox esperado bate com o que os passos realmente constroem."""
    from .parts import loops_bbox

    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for st in plan.steps:
        if st.kind != "extrude" or st.part != part:
            continue
        a = "xyz".index(st.axis)
        pl = [i for i in range(3) if i != a]
        u0, v0, u1, v1 = loops_bbox(st.loops)
        a0, a1 = (st.z0, st.z0 + st.dz) if st.direction == "+w" else (st.z0 - st.dz, st.z0)
        for idx, (l_, h_) in zip(pl + [a], [(u0, u1), (v0, v1), (a0, a1)]):
            lo[idx], hi[idx] = min(lo[idx], l_), max(hi[idx], h_)
    if lo[0] == math.inf:
        return None
    return tuple(lo), tuple(hi)
