"""Perfis estruturais padronizados (IPE, HEA, HEB, UPN, cantoneiras, tubos, barras) e estruturas (frames).

Tudo Python puro, sobre o `parts.Plan`/`parts.Step` ja verificado ao vivo (extrusao de contornos 2D em planos x/y/z):
cada perfil vira UMA extrusao do contorno da secao ao longo de x, y ou z. Simplificacoes (iguais as de parts.i_beam):
secao sem raios de concordancia e abas dos perfis U/I paralelas (sem a inclinacao de 8% das abas de UPN/IPE). O volume
esperado e area da secao x comprimento, entao o autoteste confere a geometria.

Nomes aceitos (maiusculas ou minusculas, 'x' ou 'X' como separador):
  IPE200  HEA200  HEB200  UPN100   L50x50x5  L100x50x6 (abas desiguais)  L50x5 (abas iguais)
  SHS40x3 (quadrado)  SHS40x40x3  RHS60x40x3  CHS60.3x3.2 (redondo)  FLAT40x5 (barra chata)  ROUND20  SQ20
"""

from __future__ import annotations

import math
import re

from . import parts
from .parts import PartError, Plan, Step, poly_area

# nome -> (h, b, tw, tf)  [mm]
_IPE = {"IPE80": (80, 46, 3.8, 5.2), "IPE100": (100, 55, 4.1, 5.7), "IPE120": (120, 64, 4.4, 6.3),
        "IPE140": (140, 73, 4.7, 6.9), "IPE160": (160, 82, 5.0, 7.4), "IPE180": (180, 91, 5.3, 8.0),
        "IPE200": (200, 100, 5.6, 8.5), "IPE220": (220, 110, 5.9, 9.2), "IPE240": (240, 120, 6.2, 9.8),
        "IPE270": (270, 135, 6.6, 10.2), "IPE300": (300, 150, 7.1, 10.7), "IPE330": (330, 160, 7.5, 11.5),
        "IPE360": (360, 170, 8.0, 12.7), "IPE400": (400, 180, 8.6, 13.5)}
_HEA = {"HEA100": (96, 100, 5, 8), "HEA120": (114, 120, 5, 8), "HEA140": (133, 140, 5.5, 8.5),
        "HEA160": (152, 160, 6, 9), "HEA180": (171, 180, 6, 9.5), "HEA200": (190, 200, 6.5, 10),
        "HEA220": (210, 220, 7, 11), "HEA240": (230, 240, 7.5, 12), "HEA260": (250, 260, 7.5, 12.5),
        "HEA280": (270, 280, 8, 13), "HEA300": (290, 300, 8.5, 14)}
_HEB = {"HEB100": (100, 100, 6, 10), "HEB120": (120, 120, 6.5, 11), "HEB140": (140, 140, 7, 12),
        "HEB160": (160, 160, 8, 13), "HEB180": (180, 180, 8.5, 14), "HEB200": (200, 200, 9, 15),
        "HEB220": (220, 220, 9.5, 16), "HEB240": (240, 240, 10, 17), "HEB260": (260, 260, 10, 17.5),
        "HEB280": (280, 280, 10.5, 18), "HEB300": (300, 300, 11, 19)}
_UPN = {"UPN50": (50, 38, 5, 7), "UPN65": (65, 42, 5.5, 7.5), "UPN80": (80, 45, 6, 8), "UPN100": (100, 50, 6, 8.5),
        "UPN120": (120, 55, 7, 9), "UPN140": (140, 60, 7, 10), "UPN160": (160, 65, 7.5, 10.5),
        "UPN180": (180, 70, 8, 11), "UPN200": (200, 75, 8.5, 11.5), "UPN220": (220, 80, 9, 12.5),
        "UPN240": (240, 85, 9.5, 13), "UPN260": (260, 90, 10, 14), "UPN280": (280, 95, 10, 15),
        "UPN300": (300, 100, 10, 16)}
I_SERIES = {**_IPE, **_HEA, **_HEB}

FAMILIES = {
    "IPE": "viga I (abas paralelas, sem raios)", "HEA": "viga H leve", "HEB": "viga H",
    "UPN": "perfil U (abas paralelas, sem a inclinacao)", "L": "cantoneira (abas iguais Lax t ou desiguais Laxbxt)",
    "SHS": "tubo quadrado SHSaxt ou SHSaxaxt", "RHS": "tubo retangular RHSaxbxt", "CHS": "tubo redondo CHSdxt",
    "FLAT": "barra chata FLATbxt (largura x espessura)", "ROUND": "barra redonda ROUNDd", "SQ": "barra quadrada SQa",
}

_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


class Section:
    """Secao transversal: contornos 2D (centrados na origem) + area + caixa (u, v)."""

    def __init__(self, name: str, loops: list, area: float, box: tuple, family: str, mass_per_m_kg: float):
        self.name, self.loops, self.area, self.box = name, loops, area, box
        self.family, self.mass_per_m_kg = family, mass_per_m_kg

    def size(self) -> tuple:
        return (self.box[2] - self.box[0], self.box[3] - self.box[1])

    def info(self) -> dict:
        return {"name": self.name, "family": self.family, "section_area_mm2": round(self.area, 2),
                "width_u_mm": round(self.size()[0], 3), "height_v_mm": round(self.size()[1], 3),
                "mass_kg_per_m": round(self.mass_per_m_kg, 3)}


STEEL = 7.85  # g/cm3


def _i_section(name, h, b, tw, tf, family) -> Section:
    pts = [(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, -h / 2 + tf), (tw / 2, -h / 2 + tf), (tw / 2, h / 2 - tf),
           (b / 2, h / 2 - tf), (b / 2, h / 2), (-b / 2, h / 2), (-b / 2, h / 2 - tf), (-tw / 2, h / 2 - tf),
           (-tw / 2, -h / 2 + tf), (-b / 2, -h / 2 + tf)]
    a = poly_area(pts)
    return Section(name, [("p", pts)], a, (-b / 2, -h / 2, b / 2, h / 2), family, a * 1e-6 * 1000 * STEEL)


def _u_section(name, h, b, tw, tf) -> Section:
    # alma vertical a esquerda (u=0), abas para a direita; centrada pela caixa
    pts = [(0, 0), (b, 0), (b, tf), (tw, tf), (tw, h - tf), (b, h - tf), (b, h), (0, h)]
    pts = [(x - b / 2, y - h / 2) for x, y in pts]
    a = poly_area(pts)
    return Section(name, [("p", pts)], a, (-b / 2, -h / 2, b / 2, h / 2), "UPN", a * 1e-6 * 1000 * STEEL)


def _l_section(name, a_leg, b_leg, t) -> Section:
    pts = [(0, 0), (a_leg, 0), (a_leg, t), (t, t), (t, b_leg), (0, b_leg)]
    pts = [(x - a_leg / 2, y - b_leg / 2) for x, y in pts]
    a = poly_area(pts)
    return Section(name, [("p", pts)], a, (-a_leg / 2, -b_leg / 2, a_leg / 2, b_leg / 2), "L", a * 1e-6 * 1000 * STEEL)


def _rect_tube(name, w, h, t) -> Section:
    if 2 * t >= min(w, h):
        raise PartError(f"{name}: parede {t} grande demais para a secao {w}x{h}")
    a = w * h - (w - 2 * t) * (h - 2 * t)
    return Section(name, [("r", -w / 2, -h / 2, w / 2, h / 2), ("r", -w / 2 + t, -h / 2 + t, w / 2 - t, h / 2 - t)], a,
                   (-w / 2, -h / 2, w / 2, h / 2), "RHS" if abs(w - h) > 1e-9 else "SHS", a * 1e-6 * 1000 * STEEL)


def _round_tube(name, d, t) -> Section:
    if 2 * t >= d:
        raise PartError(f"{name}: parede {t} grande demais para o diametro {d}")
    a = math.pi * (d * d - (d - 2 * t) ** 2) / 4
    return Section(name, [("c", 0, 0, d / 2), ("c", 0, 0, d / 2 - t)], a, (-d / 2, -d / 2, d / 2, d / 2), "CHS",
                   a * 1e-6 * 1000 * STEEL)


def parse_profile(name: str) -> Section:
    """Secao a partir do nome (ver docstring do modulo)."""
    raw = (name or "").strip().replace(" ", "").replace("-", "")
    key = raw.upper().replace("X", "x")
    if key in I_SERIES:
        h, b, tw, tf = I_SERIES[key]
        return _i_section(key, h, b, tw, tf, key[:3])
    if key in _UPN:
        h, b, tw, tf = _UPN[key]
        return _u_section(key, h, b, tw, tf)
    m = re.fullmatch(rf"L{_NUM}x{_NUM}(?:x{_NUM})?", key)
    if m:
        g = [_f(x) if x else None for x in m.groups()]
        a_leg, b_leg, t = (g[0], g[1], g[2]) if g[2] is not None else (g[0], g[0], g[1])
        if t is None or t >= min(a_leg, b_leg):
            raise PartError(f"cantoneira {name}: espessura invalida")
        return _l_section(f"L{a_leg:g}x{b_leg:g}x{t:g}", a_leg, b_leg, t)
    m = re.fullmatch(rf"SHS{_NUM}x{_NUM}(?:x{_NUM})?", key)
    if m:
        g = [_f(x) if x else None for x in m.groups()]
        w, h, t = (g[0], g[0], g[1]) if g[2] is None else (g[0], g[1], g[2])
        return _rect_tube(f"SHS{w:g}x{h:g}x{t:g}" if abs(w - h) > 1e-9 else f"SHS{w:g}x{t:g}", w, h, t)
    m = re.fullmatch(rf"RHS{_NUM}x{_NUM}x{_NUM}", key)
    if m:
        w, h, t = (_f(x) for x in m.groups())
        return _rect_tube(f"RHS{w:g}x{h:g}x{t:g}", w, h, t)
    m = re.fullmatch(rf"CHS{_NUM}x{_NUM}", key)
    if m:
        d, t = (_f(x) for x in m.groups())
        return _round_tube(f"CHS{d:g}x{t:g}", d, t)
    m = re.fullmatch(rf"FLAT{_NUM}x{_NUM}", key)
    if m:
        w, t = (_f(x) for x in m.groups())
        a = w * t
        return Section(f"FLAT{w:g}x{t:g}", [("r", -w / 2, -t / 2, w / 2, t / 2)], a, (-w / 2, -t / 2, w / 2, t / 2), "FLAT",
                       a * 1e-6 * 1000 * STEEL)
    m = re.fullmatch(rf"SQ{_NUM}", key)
    if m:
        s = _f(m.group(1))
        return Section(f"SQ{s:g}", [("r", -s / 2, -s / 2, s / 2, s / 2)], s * s, (-s / 2, -s / 2, s / 2, s / 2), "SQ",
                       s * s * 1e-6 * 1000 * STEEL)
    m = re.fullmatch(rf"ROUND{_NUM}", key)
    if m:
        d = _f(m.group(1))
        a = math.pi * d * d / 4
        return Section(f"ROUND{d:g}", [("c", 0, 0, d / 2)], a, (-d / 2, -d / 2, d / 2, d / 2), "ROUND",
                       a * 1e-6 * 1000 * STEEL)
    raise PartError(f"perfil desconhecido: {name!r}. Exemplos: IPE200, HEA160, UPN100, L50x50x5, SHS40x3, RHS60x40x3, "
                    "CHS60.3x3.2, FLAT40x5, ROUND20, SQ20 (creo_profile_std(list=true) mostra as series)")


def list_profiles() -> dict:
    return {"families": FAMILIES,
            "series": {"IPE": sorted(_IPE, key=lambda s: int(s[3:])), "HEA": sorted(_HEA, key=lambda s: int(s[3:])),
                       "HEB": sorted(_HEB, key=lambda s: int(s[3:])), "UPN": sorted(_UPN, key=lambda s: int(s[3:]))},
            "free_form": ["L<a>x<b>x<t>  ou  L<a>x<t>", "SHS<a>x<t>  ou  SHS<a>x<a>x<t>", "RHS<a>x<b>x<t>", "CHS<d>x<t>",
                          "FLAT<b>x<t>", "ROUND<d>", "SQ<a>"],
            "note": "secoes sem raios de concordancia; abas dos perfis U/I paralelas; massa por metro para aco 7,85 g/cm3"}


# ------------------------------------------------------------------ transformacoes de contorno
def rotate_loops(loops: list, roll: int) -> list:
    """Gira os contornos em torno da origem de 0/90/180/270 graus (ponto a ponto; circulos giram o centro)."""
    r = int(roll) % 360
    if r not in (0, 90, 180, 270):
        raise PartError("roll deve ser 0, 90, 180 ou 270")
    if r == 0:
        return list(loops)

    def rot(x, y):
        return {90: (-y, x), 180: (-x, -y), 270: (y, -x)}[r]

    out = []
    for lp in loops:
        if lp[0] == "c":
            cx, cy = rot(lp[1], lp[2])
            out.append(("c", cx, cy, lp[3]))
        elif lp[0] == "r":
            xs = [rot(lp[1], lp[2]), rot(lp[3], lp[4])]
            out.append(("r", min(p[0] for p in xs), min(p[1] for p in xs), max(p[0] for p in xs), max(p[1] for p in xs)))
        else:
            out.append(("p", [rot(x, y) for x, y in lp[1]]))
    return out


def _bbox_after_roll(box, roll):
    u0, v0, u1, v1 = box
    r = int(roll) % 360
    if r in (90, 270):
        return (-(v1), u0, -(v0), u1) if r == 90 else (v0, -(u1), v1, -(u0))
    if r == 180:
        return (-u1, -v1, -u0, -v0)
    return box


# Plano de cada eixo: coordenadas (u, v) do contorno -> eixos globais
PLANE_AXES = {"z": ("x", "y"), "y": ("x", "z"), "x": ("y", "z")}
_IDX = {"x": 0, "y": 1, "z": 2}


def member_plan(profile: str, length: float, axis: str = "z", start=(0.0, 0.0, 0.0), roll: int = 0,
                name: str = "perfil", end_trim=(0.0, 0.0)) -> Plan:
    """UMA peca: o perfil extrudado `length` ao longo de `axis` (x|y|z), com o CENTRO da secao em `start` (nos dois eixos
    do plano) e a face inicial em `start` ao longo do eixo. `end_trim=(a, b)` encurta a barra nas duas pontas."""
    sec = parse_profile(profile)
    axis = axis.lower()
    if axis not in PLANE_AXES:
        raise PartError("axis deve ser 'x', 'y' ou 'z'")
    L = float(length) - float(end_trim[0]) - float(end_trim[1])
    if L <= 0:
        raise PartError("comprimento util <= 0 depois do encurtamento das pontas")
    pname = parts.safe_name(name, "perfil")
    ua, va = PLANE_AXES[axis]
    cu, cv = float(start[_IDX[ua]]), float(start[_IDX[va]])
    s0 = float(start[_IDX[axis]]) + float(end_trim[0])
    loops = parts._move_loops(rotate_loops(sec.loops, roll), cu, cv)
    step = Step("extrude", pname, loops, s0, L, "+w", f"{sec.name} L={L:g} ao longo de {axis}", axis=axis)
    bu = _bbox_after_roll(sec.box, roll)
    lo, hi = [0.0] * 3, [0.0] * 3
    lo[_IDX[ua]], hi[_IDX[ua]] = bu[0] + cu, bu[2] + cu
    lo[_IDX[va]], hi[_IDX[va]] = bu[1] + cv, bu[3] + cv
    lo[_IDX[axis]], hi[_IDX[axis]] = s0, s0 + L
    plan = Plan(pname, [step], pname, (tuple(lo), tuple(hi)), sec.area * L, volume_tol=0.03, export_parts=[pname],
                notes=f"{sec.name}, area {sec.area:.1f} mm2, {sec.mass_per_m_kg * L / 1000:.2f} kg (aco)")
    plan.part_bboxes = {pname: (tuple(lo), tuple(hi))}
    return plan


def merge_plans(name: str, plans: list[Plan], notes: str = "") -> Plan:
    """Junta varios Plans (pecas independentes) num so, mantendo o bbox de CADA peca e a verificacao de volume por amostra."""
    steps, bboxes, vols, export = [], {}, [], []
    for p in plans:
        steps += p.steps
        bboxes.update(p.part_bboxes)
        export += p.export_parts
        if p.expected_volume:
            vols.append((p.main_part, p.expected_volume))
    total = sum(v for _n, v in vols)
    # varias pecas iguais -> amostra (a 1a de cada secao+comprimento): STL multi-peca so exporta a primeira
    seen, sample = set(), []
    for p, (n, v) in zip(plans, vols):
        key = round(v, 1)
        if key not in seen and len(sample) < 6:
            seen.add(key)
            sample.append((n, v))
    main = plans[0].main_part if plans else ""
    total = sum(v for _n, v in sample)       # o construtor soma os STL das pecas amostradas
    plan = Plan(name, steps, main, bboxes.get(main), total, volume_tol=0.03, export_parts=export, volume_parts=sample,
                part_bboxes=bboxes, notes=notes)
    return plan


# ------------------------------------------------------------------ estruturas (frames)
def frame_members(members: list[dict], prefix: str = "estrutura") -> Plan:
    """members: [{"profile", "from": [x,y,z], "to": [x,y,z], "roll": 0, "name": opcional, "trim": [a, b] opcional}].

    Cada barra deve seguir UM eixo (so uma coordenada muda entre from e to). A secao fica centrada na linha de centro
    from-to. Barras nao sao cortadas em esquadro nem em meia-esquadria (junta de topo): use `trim` para encostar uma barra
    na face da outra e evitar sobreposicao."""
    if not members:
        raise PartError("informe ao menos um membro")
    plans, used = [], set()
    for i, m in enumerate(members):
        try:
            p0 = [float(v) for v in m["from"]]
            p1 = [float(v) for v in m["to"]]
            prof = m["profile"]
        except (KeyError, TypeError, ValueError):
            raise PartError(f"membro {i + 1}: precisa de profile, from=[x,y,z], to=[x,y,z]")
        if len(p0) != 3 or len(p1) != 3:
            raise PartError(f"membro {i + 1}: from/to precisam de 3 coordenadas")
        diff = [abs(p1[k] - p0[k]) for k in range(3)]
        axes = [k for k in range(3) if diff[k] > 1e-9]
        if len(axes) != 1:
            raise PartError(f"membro {i + 1}: from-to deve seguir um unico eixo (x, y ou z); membros inclinados exigem "
                            "creo_transform depois")
        k = axes[0]
        ax = "xyz"[k]
        start = list(p0)
        if p1[k] < p0[k]:
            start[k] = p1[k]
        nm = parts.safe_name(m.get("name") or f"{prefix}_{i + 1}", f"{prefix}_{i + 1}")
        if nm in used:
            raise PartError(f"nome repetido: {nm}")
        used.add(nm)
        trim = m.get("trim") or [0.0, 0.0]
        plans.append(member_plan(prof, diff[k], ax, start, int(m.get("roll", 0)), nm, (float(trim[0]), float(trim[1]))))
    total_mass = sum(float(p.notes.split(", ")[-1].split(" kg")[0]) for p in plans if p.notes)
    return merge_plans(prefix, plans, f"{len(plans)} barras, ~{total_mass:.1f} kg de aco; juntas de topo (sem esquadria)")


def frame_box(length: float, width: float, height: float, columns: str = "SHS50x3", beams: str = "SHS40x3",
              origin=(0.0, 0.0, 0.0), bottom_beams: bool = True, top_beams: bool = True, prefix: str = "estrutura") -> list[dict]:
    """Mesa/skid/bastidor retangular: 4 colunas nos cantos + 2 longarinas e 2 travessas no topo (e embaixo). Devolve a lista
    de membros (use frame_members). Colunas de comprimento `height`; vigas ENTRE as colunas (encurtadas pela meia-secao)."""
    col = parse_profile(columns)
    bm = parse_profile(beams)
    cw, cd = col.size()
    ox, oy, oz = origin
    # colunas centradas nos cantos do retangulo (length x width) de linhas de centro
    xs = [ox, ox + length]
    ys = [oy, oy + width]
    members = []
    n = 1
    for x in xs:
        for y in ys:
            members.append({"profile": columns, "from": [x, y, oz], "to": [x, y, oz + height], "name": f"{prefix}_col{n}"})
            n += 1
    levels = []
    if bottom_beams:
        levels.append(("b", oz + bm.size()[1] / 2.0))
    if top_beams:
        levels.append(("t", oz + height - bm.size()[1] / 2.0))
    for tag, z in levels:
        # longarinas (ao longo de x) em y = oy e oy + width; entre as colunas
        for j, y in enumerate(ys):
            members.append({"profile": beams, "from": [xs[0], y, z], "to": [xs[1], y, z], "name": f"{prefix}_{tag}_long{j + 1}",
                            "trim": [cw / 2.0, cw / 2.0]})
        for j, x in enumerate(xs):
            members.append({"profile": beams, "from": [x, ys[0], z], "to": [x, ys[1], z], "name": f"{prefix}_{tag}_trav{j + 1}",
                            "trim": [cd / 2.0, cd / 2.0]})
    return members
