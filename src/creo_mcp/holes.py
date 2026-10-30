"""Furos tipados em pecas existentes: passante, cego, escareado (rebaixo), rebaixo conico e roscado (cosmetico).

Cada furo vira uma ferramenta extrudada (passo `cut`, verificado ao vivo) com 1 mm de folga fora da face. O rebaixo conico
usa um chanfro de 90 graus na aresta circular do furo (seletor `zrc`) e so existe em faces z (topo/fundo). A rosca nao e
helicoidal: o furo roscado sai no diametro de broca de macho (ISO) e o diametro nominal vai para a lista de materiais.
"""

from __future__ import annotations

import math
import re

from .parts import PartError, Plan, Step, circle_area, safe_name
from .place import Built

TAP_DRILL = {3: 2.5, 4: 3.3, 5: 4.2, 6: 5.0, 8: 6.8, 10: 8.5, 12: 10.2, 14: 12.0, 16: 14.0, 18: 15.5, 20: 17.5, 22: 19.5,
             24: 21.0, 30: 26.5}
CLEARANCE = {"medium": {3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0, 12: 13.5, 14: 15.5, 16: 17.5, 18: 20.0, 20: 22.0,
                        22: 24.0, 24: 26.0, 30: 33.0},
             "fine": {3: 3.2, 4: 4.3, 5: 5.3, 6: 6.4, 8: 8.4, 10: 10.5, 12: 13.0, 14: 15.0, 16: 17.0, 18: 19.0, 20: 21.0,
                      22: 23.0, 24: 25.0, 30: 31.0}}
# rebaixo para parafuso Allen ISO 4762 (DIN 974-1): M -> (diametro, profundidade)
CBORE = {3: (6.5, 3.4), 4: (8.0, 4.6), 5: (10.0, 5.7), 6: (11.0, 6.8), 8: (15.0, 9.0), 10: (18.0, 11.0), 12: (20.0, 13.0),
         14: (24.0, 15.0), 16: (26.0, 17.5), 20: (33.0, 21.5), 24: (40.0, 25.5)}

FACES = {"top": ("z", 1), "bottom": ("z", -1), "front": ("y", -1), "back": ("y", 1), "left": ("x", -1), "right": ("x", 1)}
PLANE = {"z": (0, 1), "y": (0, 2), "x": (1, 2)}          # indices (u, v) do plano da face
_IDX = {"x": 0, "y": 1, "z": 2}
KINDS = ("through", "blind", "cbore", "csink", "tapped")


def parse_size(size) -> tuple:
    """'M8' -> (8, None); 'M8x1' -> (8, 1.0); 8 -> (8, None)."""
    if isinstance(size, (int, float)):
        return int(round(size)), None
    m = re.fullmatch(r"\s*[Mm](\d+(?:\.\d+)?)(?:\s*[xX]\s*(\d+(?:\.\d+)?))?\s*", str(size))
    if not m:
        raise PartError(f"tamanho de rosca invalido: {size!r} (exemplos: M8, M10x1.25)")
    return int(round(float(m.group(1)))), (float(m.group(2)) if m.group(2) else None)


def expand_holes(holes: list) -> list:
    """Expande {"pattern": {...}} em varios furos; cada item vira um dict com 'at': [a, b]."""
    from .place import pattern_points

    out = []
    for h in holes:
        h = dict(h)
        pat = h.pop("pattern", None)
        if pat:
            pat = dict(pat)
            kind = pat.pop("kind", "linear")
            for pt in pattern_points(kind, **pat):
                out.append(dict(h, at=list(pt)))
        else:
            out.append(h)
    if not out:
        raise PartError("informe ao menos um furo")
    if len(out) > 200:
        raise PartError("mais de 200 furos numa chamada: divida em partes")
    return out


def resolve(h: dict, extent: float, faces_z: bool) -> dict:
    """Normaliza um furo: tipo, diametro, profundidade, rebaixos e nota de rosca."""
    size = h.get("size")
    nom, pitch = parse_size(size) if size is not None else (None, None)
    kind = str(h.get("kind") or ("blind" if h.get("depth") else "through")).lower()
    if kind not in KINDS:
        raise PartError(f"tipo de furo {kind!r}: use {KINDS}")
    fit = str(h.get("fit", "medium")).lower()
    if fit not in CLEARANCE:
        raise PartError("fit deve ser 'medium' ou 'fine'")
    note = ""
    if h.get("d"):
        d = float(h["d"])
    elif nom is None:
        raise PartError("informe 'd' (diametro) ou 'size' (ex.: M8)")
    elif kind == "tapped":
        if nom not in TAP_DRILL:
            raise PartError(f"M{nom} fora da tabela de brocas de macho: {sorted(TAP_DRILL)}")
        d = TAP_DRILL[nom] if not pitch else max(nom - pitch, 1.0)
        note = f"M{nom}" + (f"x{pitch:g}" if pitch else "") + " (rosca cosmetica, furo no Ø de broca de macho)"
    else:
        if nom not in CLEARANCE[fit]:
            raise PartError(f"M{nom} fora da tabela de furos de passagem: {sorted(CLEARANCE[fit])}")
        d = CLEARANCE[fit][nom]
    if d <= 0:
        raise PartError("diametro deve ser > 0")
    depth = float(h["depth"]) if h.get("depth") else None
    if kind == "blind" and depth is None:
        raise PartError("furo cego precisa de 'depth'")
    through = depth is None or depth >= extent - 1e-9
    if depth is not None and depth > extent + 1e-9:
        raise PartError(f"profundidade {depth:g} maior que a espessura da peca ({extent:g})")
    out = {"kind": kind, "d": d, "depth": extent if through else depth, "through": through, "note": note, "thread": note or None}
    if kind == "cbore":
        if h.get("cbore_d"):
            cbd, cbh = float(h["cbore_d"]), float(h.get("cbore_depth") or 0)
        elif nom in CBORE:
            cbd, cbh = CBORE[nom]
            if h.get("cbore_depth"):
                cbh = float(h["cbore_depth"])
        else:
            raise PartError("rebaixo: informe cbore_d/cbore_depth ou size com M3-M24")
        if cbd <= d or cbh <= 0 or cbh >= out["depth"]:
            raise PartError(f"rebaixo invalido (Ø{cbd:g} x {cbh:g}) para furo Ø{d:g} de profundidade {out['depth']:g}")
        out.update(cbore_d=cbd, cbore_depth=cbh)
    if kind == "csink":
        if not faces_z:
            raise PartError("rebaixo conico so em faces top/bottom (usa chanfro de aresta circular)")
        csk = float(h["csink_d"]) if h.get("csink_d") else (2.24 * nom if nom else 0.0)
        if csk <= d:
            raise PartError("informe csink_d > d (ou size M3-M24 para usar 2,24 x M)")
        out["csink_d"] = csk
    return out


def holes_plan(part: str, bbox, holes: list, face: str = "top", from_corner: bool = False, name: str | None = None) -> Built:
    """Plano de furos em `part` (bbox atual = ((x0,y0,z0),(x1,y1,z1))). Posicao 'at': [a, b] nas coordenadas do plano da face
    (top/bottom: x,y; front/back: x,z; left/right: y,z), absolutas ou, com from_corner, medidas do canto minimo da face."""
    f = str(face).lower()
    if f not in FACES:
        raise PartError(f"face {face!r}: use {sorted(FACES)}")
    ax, sgn = FACES[f]
    a = _IDX[ax]
    (lo, hi) = bbox
    lvl = hi[a] if sgn > 0 else lo[a]
    extent = hi[a] - lo[a]
    iu, iv = PLANE[ax]
    expanded = expand_holes(holes)
    groups: dict = {}
    resolved = []
    for n, h in enumerate(expanded, 1):
        at = h.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            raise PartError(f"furo {n}: informe 'at': [a, b] (coordenadas no plano da face {f})")
        u, v = float(at[0]), float(at[1])
        if from_corner:
            u, v = lo[iu] + u, lo[iv] + v
        r = resolve(h, extent, ax == "z")
        rr = r["d"] / 2.0
        edge = r.get("cbore_d", r.get("csink_d", r["d"])) / 2.0
        if u - edge < lo[iu] + 0.4 or u + edge > hi[iu] - 0.4 or v - edge < lo[iv] + 0.4 or v + edge > hi[iv] - 0.4:
            raise PartError(f"furo {n} em ({u:g}, {v:g}) Ø{2 * edge:g} sai (ou encosta) da face da peca "
                            f"[{lo[iu]:g}..{hi[iu]:g}] x [{lo[iv]:g}..{hi[iv]:g}]")
        for m, q in enumerate(resolved):
            if math.hypot(u - q["u"], v - q["v"]) < edge + q["edge"] + 0.5:
                raise PartError(f"furos {m + 1} e {n} se sobrepoem ou ficam a menos de 0,5 mm")
        r.update(u=u, v=v, edge=edge, r=rr)
        resolved.append(r)
        key = (r["kind"], round(r["d"], 4), round(r["depth"], 4), round(r.get("cbore_d", 0), 4), round(r.get("cbore_depth", 0), 4))
        groups.setdefault(key, []).append(r)
    p = safe_name(part, "peca")
    steps: list = []
    removed = 0.0
    inward = "-w" if sgn > 0 else "+w"
    start = lvl + sgn * 1.0
    for key, items in groups.items():
        kind, d, depth, cbd, cbh = key
        loops = [("c", it["u"], it["v"], d / 2.0) for it in items]
        through = items[0]["through"]
        dz = (extent + 2.0) if through else (depth + 1.0)
        label = {"through": "furo passante", "blind": "furo cego", "cbore": "furo com rebaixo", "csink": "furo com rebaixo conico",
                 "tapped": "furo roscado"}[kind] + f" Ø{d:g}" + (f" x{depth:g}" if not through else "")
        if kind == "cbore":
            steps.append(Step("cut", p, [("c", it["u"], it["v"], cbd / 2.0) for it in items], start, cbh + 1.0, inward,
                              f"rebaixo Ø{cbd:g} x {cbh:g}", axis=ax))
            removed += len(items) * circle_area(cbd / 2.0) * cbh
            steps.append(Step("cut", p, loops, start, dz, inward, label, axis=ax))
            removed += len(items) * circle_area(d / 2.0) * (depth - cbh)
        else:
            steps.append(Step("cut", p, loops, start, dz, inward, label, axis=ax))
            removed += len(items) * circle_area(d / 2.0) * depth
    for key, items in groups.items():
        if key[0] != "csink":
            continue
        for it in items:
            s = (it["csink_d"] - it["d"]) / 2.0
            steps.append(Step("chamfer", p, [("zrc", lvl, it["u"], it["v"], it["r"] + 0.05, max(it["r"] - 0.05, 0.0))],
                              dz=s, label=f"rebaixo conico Ø{it['csink_d']:g}"))
            removed += (s * s / 2.0) * 2 * math.pi * (it["r"] + s / 3.0)
    nm = name or f"furos_{p}"
    plan = Plan(safe_name(nm, "furos"), steps, "", None, None, volume_tol=0.03,
                notes=f"{len(resolved)} furo(s) em {part} na face {f}; volume a remover ~{removed:.1f} mm3")
    threads = sorted({r["note"] for r in resolved if r["note"]})
    info = {"part": part, "face": f, "axis": ax, "face_level": lvl, "holes": len(resolved), "removed_volume_mm3": round(removed, 3),
            "threads": threads, "groups": len(groups),
            "items": [{"at": [round(r["u"], 4), round(r["v"], 4)], "kind": r["kind"], "d": round(r["d"], 4),
                       "depth": round(r["depth"], 4), "through": r["through"]} for r in resolved]}
    bom = [{"item": t, "category": "rosca", "qty": sum(1 for r in resolved if r["note"] == t), "parts": [part],
            "unit_mass_g": 0.0, "material": "", "note": "furo roscado cosmetico"} for t in threads]
    return Built(plan, bom, info)
