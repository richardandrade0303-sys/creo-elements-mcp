"""Catalogo de pecas industriais: cada gerador devolve um `Plan` (passos de modelagem + valores esperados).

Tudo e feito com o que foi VERIFICADO no Creo Express 9.0:
  * contornos 2D no workplane: circulo, retangulo, poligono (aninhados viram furos num unico extrude);
  * extrusao ascendente (+w) ou descendente (-w), sempre com :keep_profile :no;
  * planos de trabalho em qualquer Z (POSITION_WP :PT_PT_PT via sd-put-buffer);
  * 'cut': extrude de uma peca-ferramenta + subtract_3d (varios furos = varias pecas-ferramenta, todas subtraidas);
  * 'chamfer' / 'fillet': arestas escolhidas por seletores (ver recipes.edge_pred_lisp) via dialogo + CHAMFER/BLEND.
Este modulo e Python puro (sem Creo), entao e testavel offline e recarregavel a quente.

Contornos (loops):
  ("c", x, y, r)            circulo
  ("r", x1, y1, x2, y2)     retangulo
  ("p", [(x, y), ...])      poligono fechado
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

Loop = tuple


# ------------------------------------------------------------------ modelo
@dataclass
class Step:
    kind: str                 # "extrude" | "cut" | "chamfer" | "chamfer2" | "chamfer_angle" | "fillet" | "shell" (arestas/faces: loops = seletores, dz = tamanho)
                              # | "helix_add" | "helix_cut" (helice: axis "y", loops em (x, z), dz = passo, dz2 = voltas)
    part: str                 # nome da peca (sem a barra): "flange"
    loops: list               # lista de Loop
    z0: float = 0.0           # posicao do plano de trabalho ao longo de `axis`
    dz: float = 10.0          # distancia da extrusao (sempre > 0)
    direction: str = "+w"     # "+w" = sentido +eixo global, "-w" = sentido -eixo global
    label: str = ""
    axis: str = "z"           # eixo normal ao plano: "z" (contornos em x,y) | "y" (x,z) | "x" (y,z)
    dz2: float = 0.0          # chanfro assimetrico: 2a distancia ("chamfer2") ou angulo em graus ("chamfer_angle")


@dataclass
class Plan:
    name: str
    steps: list
    main_part: str = ""                          # peca principal (bbox esperado)
    expected_bbox: tuple | None = None           # ((x,y,z) min, (x,y,z) max)
    expected_volume: float | None = None         # mm3 (soma de todas as pecas exportadas)
    volume_tol: float = 0.025                    # tolerancia relativa (tesselacao de circulos)
    export_parts: list = field(default_factory=list)
    # [(peca, volume_esperado)]: o STL com varias pecas exporta so a PRIMEIRA (verificado ao vivo), entao cada peca
    # listada aqui e exportada separadamente e os volumes sao somados (use pecas representativas).
    volume_parts: list = field(default_factory=list)
    # {peca: ((x,y,z) min, (x,y,z) max)}: bbox esperado de CADA peca (montagens: confere a posicao de todas)
    part_bboxes: dict = field(default_factory=dict)
    notes: str = ""

    def parts(self) -> list:
        seen = []
        for s in self.steps:
            if s.kind in ("extrude", "helix_add") and s.part not in seen:
                seen.append(s.part)
        return seen


class PartError(ValueError):
    """Parametros invalidos de uma peca."""


# ------------------------------------------------------------------ geometria
def poly_area(pts) -> float:
    """Area (positiva) de um poligono pela formula de Gauss."""
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def circle_area(r: float) -> float:
    return math.pi * r * r


def regular_polygon(n: int, circum_r: float, start_deg: float = 0.0, cx: float = 0.0, cy: float = 0.0):
    return [(cx + circum_r * math.cos(math.radians(start_deg + 360.0 * i / n)),
             cy + circum_r * math.sin(math.radians(start_deg + 360.0 * i / n))) for i in range(n)]


def bolt_circle(n: int, pcd: float, hole_d: float, start_deg: float = 0.0) -> list:
    """Furos igualmente espacados num circulo de passo `pcd`."""
    return [("c", pcd / 2 * math.cos(math.radians(start_deg + 360.0 * i / n)),
             pcd / 2 * math.sin(math.radians(start_deg + 360.0 * i / n)), hole_d / 2) for i in range(n)]


def slot(cx: float, cy: float, length: float, width: float, horizontal: bool = True, arc_pts: int = 8):
    """Rasgo oblongo (stadium) como poligono; `length` e o comprimento total."""
    r = width / 2.0
    c = max(length - width, 0.0) / 2.0
    pts = []
    for i in range(arc_pts + 1):                     # semicirculo direito: -90 -> +90
        a = math.radians(-90 + 180.0 * i / arc_pts)
        pts.append((c + r * math.cos(a), r * math.sin(a)))
    for i in range(arc_pts + 1):                     # semicirculo esquerdo: 90 -> 270
        a = math.radians(90 + 180.0 * i / arc_pts)
        pts.append((-c + r * math.cos(a), r * math.sin(a)))
    if not horizontal:
        pts = [(y, x) for x, y in pts]
    return ("p", [(cx + x, cy + y) for x, y in pts])


def loop_area(loop) -> float:
    if loop[0] == "c":
        return circle_area(loop[3])
    if loop[0] == "r":
        return abs(loop[3] - loop[1]) * abs(loop[4] - loop[2])
    return poly_area(loop[1])


def loops_bbox(loops) -> tuple:
    xs, ys = [], []
    for lp in loops:
        if lp[0] == "c":
            xs += [lp[1] - lp[3], lp[1] + lp[3]]
            ys += [lp[2] - lp[3], lp[2] + lp[3]]
        elif lp[0] == "r":
            xs += [lp[1], lp[3]]
            ys += [lp[2], lp[4]]
        else:
            xs += [p[0] for p in lp[1]]
            ys += [p[1] for p in lp[1]]
    return min(xs), min(ys), max(xs), max(ys)


def _pos(name: str, value, lo=None, hi=None) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise PartError(f"'{name}' deve ser numero (recebido {value!r})")
    if lo is not None and v < lo:
        raise PartError(f"'{name}'={v} deve ser >= {lo}")
    if hi is not None and v > hi:
        raise PartError(f"'{name}'={v} deve ser <= {hi}")
    return v


def safe_name(name: str, default: str) -> str:
    s = re.sub(r"[^a-z0-9_]", "_", (name or default).lower()).strip("_") or default
    return s if not s[0].isdigit() else "p_" + s


# ------------------------------------------------------------------ pecas
def flange(od=160.0, bore=50.0, thickness=18.0, hub_od=90.0, hub_height=40.0, bolts=8, pcd=125.0,
           bolt_d=18.0, hub_fillet=0.0, rim_chamfer=0.0, name="flange") -> Plan:
    """Flange de tubulacao com pescoco (hub), furo central e furos de parafuso em circulo de passo.

    Opcionais: `hub_fillet` (filete concavo na junta pescoco/disco) e `rim_chamfer` (chanfro nas duas bordas do disco)."""
    od, bore, thickness = _pos("od", od, 10), _pos("bore", bore, 1), _pos("thickness", thickness, 1)
    hub_od, hub_height = _pos("hub_od", hub_od, 0), _pos("hub_height", hub_height, 0)
    bolts, pcd, bolt_d = int(_pos("bolts", bolts, 0)), _pos("pcd", pcd, 0), _pos("bolt_d", bolt_d, 0)
    if bore >= (hub_od if hub_od else od):
        raise PartError("bore deve ser menor que hub_od/od")
    if bolts and (pcd + bolt_d >= od or pcd - bolt_d <= bore or (hub_od and pcd - bolt_d <= hub_od)):
        raise PartError("os furos de parafuso devem ficar entre o hub e a borda do disco (ajuste pcd/bolt_d)")
    p = safe_name(name, "flange")
    disc = [("c", 0, 0, od / 2), ("c", 0, 0, bore / 2)] + bolt_circle(bolts, pcd, bolt_d)
    steps = [Step("extrude", p, disc, 0, thickness, label="disco com furos")]
    total_h = thickness
    vol = circle_area(od / 2) * thickness - circle_area(bore / 2) * thickness - bolts * circle_area(bolt_d / 2) * thickness
    if hub_od > 0 and hub_height > thickness:
        steps.append(Step("extrude", p, [("c", 0, 0, hub_od / 2), ("c", 0, 0, bore / 2)], thickness,
                          hub_height - thickness, label="pescoco"))
        total_h = hub_height
        vol += (circle_area(hub_od / 2) - circle_area(bore / 2)) * (hub_height - thickness)
    rf, rc = _pos("hub_fillet", hub_fillet, 0), _pos("rim_chamfer", rim_chamfer, 0)
    if rf:
        if not (hub_od > 0 and hub_height > thickness):
            raise PartError("hub_fillet exige pescoco (hub_od > 0 e hub_height > thickness)")
        if rf >= (od - hub_od) / 2 or rf >= hub_height - thickness or (bolts and (pcd - bolt_d) / 2 <= hub_od / 2 + rf + 1):
            raise PartError("hub_fillet grande demais para o disco / altura do pescoco / furos de parafuso")
        rh = hub_od / 2
        # filete concavo (cilindro x plano): area (1-pi/4)r^2, centroide a rh + k*r do eixo (Pappus)
        k = (5.0 / 6.0 - math.pi / 4) / (1 - math.pi / 4)
        vol += (1 - math.pi / 4) * rf * rf * 2 * math.pi * (rh + k * rf)
        steps.append(Step("fillet", p, [("zr", thickness, rh + 0.5, rh - 0.5)], dz=rf, label="filete do pescoco"))
    if rc:
        if rc >= thickness / 2 or (bolts and (pcd + bolt_d) / 2 >= od / 2 - rc - 1):
            raise PartError("rim_chamfer grande demais (espessura do disco ou furos de parafuso perto da borda)")
        R = od / 2
        for level in (0.0, thickness):
            steps.append(Step("chamfer", p, [("zr", level, R + 0.5, R - 0.5)], dz=rc, label=f"chanfro da borda z={level:g}"))
            vol -= math.pi * (R * rc ** 2 - rc ** 3 / 3)
    return Plan(p, steps, p, ((-od / 2, -od / 2, 0), (od / 2, od / 2, total_h)), vol, export_parts=[p])


def stepped_shaft(sections=((30, 40), (40, 60), (50, 80), (40, 50), (30, 40)), keyway_width=0.0,
                  keyway_depth=0.0, keyway_length=0.0, keyway_section=2, end_chamfer=0.0, name="eixo") -> Plan:
    """Eixo escalonado: `sections` = [(diametro, comprimento), ...] de baixo para cima; rasgo de chaveta e chanfro
    nas duas pontas (`end_chamfer`, mm) opcionais."""
    secs = [(_pos("diametro", d, 1), _pos("comprimento", l, 0.1)) for d, l in sections]
    if not secs:
        raise PartError("informe pelo menos uma secao (diametro, comprimento)")
    p = safe_name(name, "eixo")
    steps, z, vol = [], 0.0, 0.0
    starts = []
    for i, (d, l) in enumerate(secs):
        steps.append(Step("extrude", p, [("c", 0, 0, d / 2)], z, l, label=f"secao {i + 1} d={d:g}"))
        starts.append(z)
        vol += circle_area(d / 2) * l
        z += l
    if keyway_width > 0 and keyway_depth > 0 and keyway_length > 0:
        k = int(keyway_section)
        if not 0 <= k < len(secs):
            raise PartError("keyway_section fora do intervalo")
        d, l = secs[k]
        if keyway_length > l or keyway_depth >= d / 2 or keyway_width >= d:
            raise PartError("rasgo de chaveta maior que a secao do eixo")
        z_key = starts[k] + (l - keyway_length) / 2.0
        r = d / 2.0
        steps.append(Step("cut", p, [("r", r - keyway_depth, -keyway_width / 2, r + 2.0, keyway_width / 2)],
                          z_key, keyway_length, label="rasgo de chaveta"))
        vol -= keyway_width * keyway_depth * keyway_length
    c = _pos("end_chamfer", end_chamfer, 0)
    if c:
        if c >= min(secs[0][0], secs[-1][0]) / 2 or c >= min(secs[0][1], secs[-1][1]):
            raise PartError("end_chamfer deve ser menor que o raio e que o comprimento das secoes das pontas")
        steps.append(Step("chamfer", p, [("z", 0.0)], dz=c, label="chanfro da ponta inferior"))
        steps.append(Step("chamfer", p, [("z", z)], dz=c, label="chanfro da ponta superior"))
        # aresta circular externa: cada ponta perde pi*(r*c^2 - c^3/3)
        for r_end in (secs[0][0] / 2, secs[-1][0] / 2):
            vol -= math.pi * (r_end * c ** 2 - c ** 3 / 3)
    rmax = max(d for d, _ in secs) / 2
    return Plan(p, steps, p, ((-rmax, -rmax, 0), (rmax, rmax, z)), vol, volume_tol=0.03, export_parts=[p])


# Cabeca/porca sextavada (ISO 4017/4032): chave s e altura da cabeca k / altura da porca m  (mm)
HEX_TABLE = {5: (8, 3.5, 4.7), 6: (10, 4.0, 5.2), 8: (13, 5.3, 6.8), 10: (16, 6.4, 8.4), 12: (18, 7.5, 10.8),
             16: (24, 10.0, 14.8), 20: (30, 12.5, 18.0), 24: (36, 15.0, 21.5), 30: (46, 18.7, 25.6)}


def _hex(size: int):
    if int(size) not in HEX_TABLE:
        raise PartError(f"tamanho M{size} fora da tabela: {sorted(HEX_TABLE)}")
    return HEX_TABLE[int(size)]


# ------------------------------------------------------------------ roscas e molas (HELIX_DIA)
# Passo grosso ISO 261 (mm) por diametro nominal M
COARSE_PITCH = {3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5,
                22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5}
_TAN30 = math.tan(math.radians(30.0))


def thread_groove(d: float, pitch: float = 0.0):
    """Sulco triangular (60 graus) de rosca externa ISO: devolve (passo, R, h3, largura na superficie, mm3 por mm de eixo).

    Profundidade h3 = 0,6134 P (perfil basico); area do sulco dentro do material h3^2 tan30; Pappus numa volta: A.2.pi.rc com
    rc = R - h3/3; por simetria helicoidal o volume por mm de eixo e A.2.pi.rc / P (exato para rosca que atravessa a peca).
    """
    P = float(pitch) if pitch else COARSE_PITCH.get(int(round(d)))
    if not P:
        raise PartError(f"M{d:g}: sem passo grosso na tabela {sorted(COARSE_PITCH)}; informe 'pitch'")
    if P <= 0:
        raise PartError("'pitch' deve ser > 0")
    R = d / 2.0
    h3 = 0.6134 * P
    if h3 >= R * 0.6:
        raise PartError(f"passo {P:g} grande demais para o diametro {d:g}")
    w = 2.0 * h3 * _TAN30
    area = h3 * h3 * _TAN30
    rc = R - h3 / 3.0
    return P, R, h3, w, area * 2.0 * math.pi * rc / P


def thread_step(part: str, d: float, length: float, thread_length: float = 0.0, pitch: float = 0.0,
                through: bool = False, ext: float = 0.3):
    """Passo helix_cut de uma rosca externa M`d` em `part` (haste de z=0 a z=`length`): devolve (Step, volume do sulco, passo).

    A hélice comeca com o perfil fora da peca (z = -P) para a rosca nascer limpa na ponta. `through=True` (e
    thread_length <= 0 ou >= length) atravessa ate a extremidade livre z=length (varao); senao o sulco termina em
    z=thread_length (por exemplo, antes da cabeca de um parafuso)."""
    P, R, h3, w, per_mm = thread_groove(d, pitch)
    full = through and (thread_length <= 0 or thread_length >= length)
    if not full:
        if thread_length < 2 * P:
            raise PartError(f"thread_length={thread_length:g} deve ser >= 2 passos ({2 * P:g} mm)")
        if thread_length > length - w / 2.0 - 0.05:
            raise PartError(f"thread_length={thread_length:g} deve ser <= {length - w / 2.0 - 0.05:g} "
                            "(o sulco nao pode alcancar a cabeca)")
    ext = min(ext, 0.15 * P)                          # a altura do perfil (0,708 P + 1,155 ext) tem de ficar < P (<= 0,88 P)
    zc0 = -P
    z_end = (length + P) if full else float(thread_length)
    turns = (z_end - zc0) / P
    half = w / 2.0 + ext * _TAN30
    tri = [(R + ext, zc0 - half), (R - h3, zc0), (R + ext, zc0 + half)]
    st = Step("helix_cut", part, [("p", tri)], 0.0, P, label=f"rosca M{d:g}x{P:g}", axis="y", dz2=turns)
    return st, per_mm * (length if full else float(thread_length)), P



def internal_thread_step(part: str, d: float, thickness: float, pitch: float = 0.0, ext: float = 0.3):
    """Passo helix_cut de uma rosca INTERNA ISO M`d` num furo de diametro menor D1 = d - 1,0825 P que atravessa uma peca de
    z=0 a z=`thickness`: devolve (Step, volume do sulco, passo, raio do furo D1/2). O sulco (perfil 60 graus com a ponta
    no diametro nominal) comeca fora da peca (z = -P) e termina alem da outra face."""
    P = float(pitch) if pitch else COARSE_PITCH.get(int(round(d)))
    if not P or P <= 0:
        raise PartError(f"M{d:g}: sem passo grosso na tabela {sorted(COARSE_PITCH)}; informe 'pitch'")
    h = 0.5413 * P                                    # profundidade do filete interno (D - D1)/2
    r1 = d / 2.0 - h
    if r1 <= 0.3 * d:
        raise PartError(f"passo {P:g} grande demais para o diametro {d:g}")
    area = h * h * _TAN30
    rc = r1 + h / 3.0
    per_mm = area * 2.0 * math.pi * rc / P
    ext = min(ext, 0.2 * P)                           # altura do perfil 0,625 P + 1,155 ext < P (<= 0,86 P)
    zc0 = -P
    turns = (thickness + P - zc0) / P
    half = (h + ext) * _TAN30
    tri = [(r1 - ext, zc0 - half), (d / 2.0, zc0), (r1 - ext, zc0 + half)]
    st = Step("helix_cut", part, [("p", tri)], 0.0, P, label=f"rosca interna M{d:g}x{P:g}", axis="y", dz2=turns)
    return st, per_mm * thickness, P, r1


def hex_bolt(size=12, length=50.0, name="parafuso", thread_length=0.0) -> Plan:
    """Parafuso de cabeca sextavada Mx (ISO 4017): haste cilindrica + cabeca hexagonal; com thread_length > 0 a haste
    ganha rosca helicoidal modelada (sulco 60 graus por HELIX_DIA, passo grosso ISO) a partir da ponta."""
    s, k, _ = _hex(size)
    length = _pos("length", length, 1)
    thread_length = _pos("thread_length", thread_length, 0)
    p = safe_name(name, "parafuso")
    R = s / math.sqrt(3)                                   # raio circunscrito (chave s)
    steps = [Step("extrude", p, [("c", 0, 0, size / 2)], 0, length, label="haste"),
             Step("extrude", p, [("p", regular_polygon(6, R))], length, k, label="cabeca sextavada")]
    vol = circle_area(size / 2) * length + (math.sqrt(3) / 2) * s * s * k
    notes = "rosca nao modelada"
    if thread_length > 0:
        st, groove, P = thread_step(p, float(size), length, thread_length)
        steps.append(st)
        vol -= groove
        notes = f"rosca M{size}x{P:g} modelada em {thread_length:g} mm a partir da ponta (sulco helicoidal)"
    return Plan(p, steps, p, ((-R, -s / 2, 0), (R, s / 2, length + k)), vol, volume_tol=0.03, export_parts=[p],
                notes=notes)


def hex_nut(size=12, name="porca", threaded=False) -> Plan:
    """Porca sextavada Mx (ISO 4032): furo no diametro nominal ou, com threaded=True, furo no diametro menor D1 e rosca
    interna helicoidal modelada (sulco 60 graus por HELIX_DIA, passo grosso ISO)."""
    s, _, m = _hex(size)
    p = safe_name(name, "porca")
    R = s / math.sqrt(3)
    hexa = (math.sqrt(3) / 2) * s * s
    if not threaded:
        loops = [("p", regular_polygon(6, R)), ("c", 0, 0, size / 2)]
        vol = (hexa - circle_area(size / 2)) * m
        return Plan(p, [Step("extrude", p, loops, 0, m, label="porca")], p, ((-R, -s / 2, 0), (R, s / 2, m)), vol,
                    volume_tol=0.03, export_parts=[p])
    st, groove, P, r1 = internal_thread_step(p, float(size), m)
    loops = [("p", regular_polygon(6, R)), ("c", 0, 0, r1)]
    vol = (hexa - circle_area(r1)) * m - groove
    return Plan(p, [Step("extrude", p, loops, 0, m, label="porca"), st], p, ((-R, -s / 2, 0), (R, s / 2, m)), vol,
                volume_tol=0.04, export_parts=[p],
                notes=f"rosca interna M{size}x{P:g}: furo no diametro menor {2 * r1:.3f} mm + sulco helicoidal ate o diametro nominal")


def involute_gear_outline(module: float, teeth: int, pressure_angle: float = 20.0, flank_pts: int = 6):
    """Contorno (poligono) de uma engrenagem cilindrica de dentes retos, perfil evolvente. Dente centrado no eixo +x."""
    a = math.radians(pressure_angle)
    r = module * teeth / 2.0
    rb = r * math.cos(a)
    ra = r + module
    rf = max(r - 1.25 * module, 0.1 * r)
    inv = lambda x: math.tan(x) - x                    # noqa: E731
    psi0 = math.pi / (2 * teeth)

    def beta(rho):                                     # meia espessura angular do dente no raio rho
        return psi0 + inv(a) - inv(math.acos(min(1.0, rb / rho)))

    rs = max(rb, rf)
    pitch = 2 * math.pi / teeth
    pts = []

    def polar(rho, ang):
        return (rho * math.cos(ang), rho * math.sin(ang))

    for k in range(teeth):
        c = k * pitch
        for i in range(flank_pts + 1):                 # flanco direito, para fora
            rho = rs + (ra - rs) * i / flank_pts
            pts.append(polar(rho, c - beta(rho)))
        pts.append(polar(ra, c))                       # meio do topo
        for i in range(flank_pts, -1, -1):             # flanco esquerdo, para dentro
            rho = rs + (ra - rs) * i / flank_pts
            pts.append(polar(rho, c + beta(rho)))
        a0, a1 = c + beta(rs), c + pitch - beta(rs)    # fundo do vao, arco em rf
        if rs > rf + 1e-9:
            pts.append(polar(rf, a0))
        for j in range(1, 4):
            pts.append(polar(rf, a0 + (a1 - a0) * j / 4))
        if rs > rf + 1e-9:
            pts.append(polar(rf, a1))
    return pts, {"pitch_radius": r, "base_radius": rb, "tip_radius": ra, "root_radius": rf}


def spur_gear(module=2.0, teeth=24, thickness=20.0, bore=25.0, pressure_angle=20.0, keyway_width=8.0,
              keyway_depth=3.3, name="engrenagem") -> Plan:
    """Engrenagem cilindrica de dentes retos (evolvente), furo e rasgo de chaveta opcional."""
    module, thickness, bore = _pos("module", module, 0.2), _pos("thickness", thickness, 0.5), _pos("bore", bore, 0)
    teeth = int(_pos("teeth", teeth, 8, 200))
    pressure_angle = _pos("pressure_angle", pressure_angle, 10, 30)
    pts, info = involute_gear_outline(module, teeth, pressure_angle)
    if bore / 2 >= info["root_radius"] - 1.0:
        raise PartError(f"furo ({bore} mm) grande demais: raio do pe do dente = {info['root_radius']:.2f} mm")
    p = safe_name(name, "engrenagem")
    loops = [("p", pts)] + ([("c", 0, 0, bore / 2)] if bore > 0 else [])
    steps = [Step("extrude", p, loops, 0, thickness, label=f"corpo z={teeth} m={module:g}")]
    vol = (poly_area(pts) - circle_area(bore / 2)) * thickness
    if keyway_width > 0 and keyway_depth > 0 and bore > 0:
        r = bore / 2
        steps.append(Step("cut", p, [("r", r - 0.5, -keyway_width / 2, r + keyway_depth, keyway_width / 2)], 0,
                          thickness, label="rasgo de chaveta"))
        vol -= keyway_width * keyway_depth * thickness
    x0, y0, x1, y1 = loops_bbox([("p", pts)])
    plan = Plan(p, steps, p, ((x0, y0, 0), (x1, y1, thickness)), vol, volume_tol=0.02, export_parts=[p])
    plan.notes = (f"modulo {module:g}, z={teeth}, ang. {pressure_angle:g} graus; primitivo {2 * info['pitch_radius']:.2f}, "
                  f"externo {2 * info['tip_radius']:.2f}, raiz {2 * info['root_radius']:.2f} mm")
    return plan


def i_beam(height=200.0, flange_width=100.0, web=5.6, flange_t=8.5, length=1000.0, name="viga_i") -> Plan:
    """Viga/perfil I (padrao IPE200 por default), sem raio de concordancia; extrudada ao longo de z."""
    h, b, tw, tf, L = (_pos("height", height, 10), _pos("flange_width", flange_width, 5), _pos("web", web, 0.5),
                       _pos("flange_t", flange_t, 0.5), _pos("length", length, 1))
    if 2 * tf >= h or tw >= b:
        raise PartError("espessuras incompativeis com a altura/largura")
    p = safe_name(name, "viga_i")
    pts = [(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, -h / 2 + tf), (tw / 2, -h / 2 + tf), (tw / 2, h / 2 - tf),
           (b / 2, h / 2 - tf), (b / 2, h / 2), (-b / 2, h / 2), (-b / 2, h / 2 - tf), (-tw / 2, h / 2 - tf),
           (-tw / 2, -h / 2 + tf), (-b / 2, -h / 2 + tf)]
    return Plan(p, [Step("extrude", p, [("p", pts)], 0, L, label="perfil I")], p,
                ((-b / 2, -h / 2, 0), (b / 2, h / 2, L)), poly_area(pts) * L, volume_tol=0.002, export_parts=[p])


def angle_bar(leg_a=75.0, leg_b=50.0, thickness=6.0, length=300.0, name="cantoneira") -> Plan:
    """Cantoneira (perfil L) extrudada ao longo de z."""
    a, b, t, L = _pos("leg_a", leg_a, 5), _pos("leg_b", leg_b, 5), _pos("thickness", thickness, 0.5), _pos("length", length, 1)
    if t >= min(a, b):
        raise PartError("thickness deve ser menor que as abas")
    p = safe_name(name, "cantoneira")
    pts = [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]
    return Plan(p, [Step("extrude", p, [("p", pts)], 0, L, label="perfil L")], p, ((0, 0, 0), (a, b, L)),
                poly_area(pts) * L, volume_tol=0.002, export_parts=[p])


def mounting_plate(width=120.0, depth=80.0, thickness=10.0, hole_d=9.0, margin=15.0, center_bore=30.0,
                   slots=2, slot_length=24.0, slot_width=9.0, name="placa_base") -> Plan:
    """Placa de fixacao: furos nos 4 cantos, furo central e rasgos oblongos opcionais (ajuste de posicao)."""
    w, d, t = _pos("width", width, 10), _pos("depth", depth, 10), _pos("thickness", thickness, 0.5)
    hole_d, margin, bore = _pos("hole_d", hole_d, 0), _pos("margin", margin, 1), _pos("center_bore", center_bore, 0)
    slots = int(_pos("slots", slots, 0, 2))
    if margin * 2 >= min(w, d):
        raise PartError("margin grande demais")
    p = safe_name(name, "placa_base")
    loops = [("r", -w / 2, -d / 2, w / 2, d / 2)]
    for sx in (-1, 1):
        for sy in (-1, 1):
            loops.append(("c", sx * (w / 2 - margin), sy * (d / 2 - margin), hole_d / 2))
    if bore > 0:
        loops.append(("c", 0, 0, bore / 2))
    sl = []
    sx0 = bore / 2 + slot_length / 2 + 3.0                    # rasgos no eixo x, entre o furo central e os cantos
    if slots:
        if sx0 + slot_length / 2 > w / 2 - 3.0:
            raise PartError("rasgos nao cabem na placa (reduza slot_length/center_bore ou aumente width)")
        sl.append(slot(sx0, 0, slot_length, slot_width, True))
        if slots == 2:
            sl.append(slot(-sx0, 0, slot_length, slot_width, True))
    loops += sl
    vol = (w * d - 4 * circle_area(hole_d / 2) - (circle_area(bore / 2) if bore > 0 else 0)
           - sum(poly_area(lp[1]) for lp in sl)) * t
    return Plan(p, [Step("extrude", p, loops, 0, t, label="placa")], p, ((-w / 2, -d / 2, 0), (w / 2, d / 2, t)), vol,
                volume_tol=0.02, export_parts=[p])


def heat_sink(base_width=100.0, base_depth=60.0, base_t=5.0, fins=9, fin_t=2.0, fin_height=25.0, name="dissipador") -> Plan:
    """Dissipador de calor: base + aletas retangulares (uma extrusao no plano z=base_t funde com a base)."""
    bw, bd, bt = _pos("base_width", base_width, 5), _pos("base_depth", base_depth, 5), _pos("base_t", base_t, 0.5)
    fins, ft, fh = int(_pos("fins", fins, 1, 60)), _pos("fin_t", fin_t, 0.3), _pos("fin_height", fin_height, 1)
    if fins * ft >= bw:
        raise PartError("aletas nao cabem na largura da base")
    p = safe_name(name, "dissipador")
    gap = (bw - fins * ft) / (fins - 1) if fins > 1 else 0.0
    fin_loops = [("r", -bw / 2 + i * (ft + gap), -bd / 2, -bw / 2 + i * (ft + gap) + ft, bd / 2) for i in range(fins)]
    steps = [Step("extrude", p, [("r", -bw / 2, -bd / 2, bw / 2, bd / 2)], 0, bt, label="base"),
             Step("extrude", p, fin_loops, bt, fh, label=f"{fins} aletas")]
    vol = bw * bd * bt + fins * ft * bd * fh
    return Plan(p, steps, p, ((-bw / 2, -bd / 2, 0), (bw / 2, bd / 2, bt + fh)), vol, volume_tol=0.002, export_parts=[p])


def roller_bearing(bore=25.0, outer=52.0, width=15.0, roller_d=5.4, roller_len=9.0, rollers=12, name="rolamento") -> Plan:
    """Rolamento de rolos cilindricos (tipo NU205, eixo vertical): anel interno, anel externo e rolos soltos."""
    d, D, B = _pos("bore", bore, 2), _pos("outer", outer, 6), _pos("width", width, 2)
    dw, lw, n = _pos("roller_d", roller_d, 0.5), _pos("roller_len", roller_len, 0.5), int(_pos("rollers", rollers, 3, 40))
    fw = d + 0.4 * (D - d) / 2 * 1.0           # diametro da pista do anel interno
    ew = fw + 2 * dw + 0.2                     # diametro da pista do anel externo (folga 0,1 mm por lado)
    if ew >= D - 2 or lw >= B:
        raise PartError("dimensoes incompativeis (rolo maior que o anel)")
    p = safe_name(name, "rolamento")
    pr = (fw + ew) / 4.0
    if n * dw >= 2 * math.pi * pr:
        raise PartError("rolos demais para o circulo primitivo")
    steps = [Step("extrude", f"{p}_ai", [("c", 0, 0, fw / 2), ("c", 0, 0, d / 2)], 0, B, label="anel interno"),
             Step("extrude", f"{p}_ae", [("c", 0, 0, D / 2), ("c", 0, 0, ew / 2)], 0, B, label="anel externo")]
    z0 = (B - lw) / 2.0
    for i in range(n):
        a = 2 * math.pi * i / n
        steps.append(Step("extrude", f"{p}_r{i + 1}", [("c", pr * math.cos(a), pr * math.sin(a), dw / 2)], z0, lw,
                          label=f"rolo {i + 1}"))
    parts = [f"{p}_ai", f"{p}_ae"] + [f"{p}_r{i + 1}" for i in range(n)]
    v_ai = (circle_area(fw / 2) - circle_area(d / 2)) * B
    v_ae = (circle_area(D / 2) - circle_area(ew / 2)) * B
    v_r = circle_area(dw / 2) * lw
    vol = v_ai + v_ae + n * v_r
    # Verificacao de volume por amostra: aneis + 1 rolo (os rolos sao identicos; o STL multi-peca so leva a 1a).
    sample = [(f"{p}_ai", v_ai), (f"{p}_ae", v_ae), (f"{p}_r1", v_r)]
    return Plan(p, steps, f"{p}_ae", ((-D / 2, -D / 2, 0), (D / 2, D / 2, B)), v_ai + v_ae + v_r, volume_tol=0.03,
                export_parts=parts, volume_parts=sample,
                notes=f"pista interna {fw:.2f}, externa {ew:.2f}, circulo primitivo {2 * pr:.2f}, {n} rolos "
                      f"(volume total esperado {vol:.0f} mm3; verificado por amostra: aneis + 1 rolo)")


# ------------------------------------------------------------------ pecas com planos laterais (x / y)
def _rounded_rect_pts(x0, y0, x1, y1, r, seg=12):
    """Contorno (anti-horario) de um retangulo com cantos arredondados (arcos em `seg` segmentos)."""
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def rounded_plate(width=120.0, depth=80.0, thickness=10.0, corner_r=12.0, hole_d=9.0, margin=15.0,
                  center_bore=0.0, name="placa_arredondada") -> Plan:
    """Placa de base com cantos arredondados, 4 furos de fixacao e furo central opcional."""
    w, d, t = _pos("width", width, 10), _pos("depth", depth, 10), _pos("thickness", thickness, 0.5)
    r, hd, m = _pos("corner_r", corner_r, 0.5), _pos("hole_d", hole_d, 0), _pos("margin", margin, 1)
    cb = _pos("center_bore", center_bore, 0)
    if 2 * r >= min(w, d):
        raise PartError("corner_r deve ser menor que metade do menor lado")
    if hd and (m - hd / 2 < 1.5 or m + hd / 2 > min(w, d) / 2 - 1):
        raise PartError("margin/hole_d: o furo sai da placa ou fica a menos de 1,5 mm da borda")
    if cb and (cb / 2 >= min(w, d) / 2 - m - hd / 2 - 1):
        raise PartError("center_bore grande demais (encosta nos furos de fixacao)")
    p = safe_name(name, "placa_arredondada")
    outline = _rounded_rect_pts(-w / 2, -d / 2, w / 2, d / 2, r)
    loops = [("p", outline)]
    if hd:
        loops += [("c", sx * (w / 2 - m), sy * (d / 2 - m), hd / 2) for sx in (-1, 1) for sy in (-1, 1)]
    if cb:
        loops.append(("c", 0, 0, cb / 2))
    vol = (poly_area(outline) - (4 * circle_area(hd / 2) if hd else 0) - (circle_area(cb / 2) if cb else 0)) * t
    return Plan(p, [Step("extrude", p, loops, 0, t, label="placa arredondada")], p,
                ((-w / 2, -d / 2, 0), (w / 2, d / 2, t)), vol, volume_tol=0.02, export_parts=[p])


def l_bracket(width=60.0, base_depth=50.0, wall_h=40.0, thickness=5.0, base_holes=2, wall_holes=2, hole_d=6.5,
              name="suporte_l") -> Plan:
    """Suporte em L: perfil L extrudado ao longo de x (plano lateral), furos verticais na base e furos na parede."""
    w, bd, wh, t = _pos("width", width, 5), _pos("base_depth", base_depth, 5), _pos("wall_h", wall_h, 5), _pos("thickness", thickness, 0.5)
    nb, nw, hd = int(_pos("base_holes", base_holes, 0, 8)), int(_pos("wall_holes", wall_holes, 0, 8)), _pos("hole_d", hole_d, 0)
    if wh <= t or bd <= t:
        raise PartError("wall_h e base_depth devem ser maiores que thickness")
    yb, zc = (t + bd) / 2.0, (t + wh) / 2.0
    if nb and hd and (yb - hd / 2 < t + 1 or yb + hd / 2 > bd - 1):
        raise PartError("os furos da base nao cabem (aumente base_depth ou reduza hole_d)")
    if nw and hd and (zc - hd / 2 < t + 1 or zc + hd / 2 > wh - 1):
        raise PartError("os furos da parede nao cabem (aumente wall_h ou reduza hole_d)")
    if hd and max(nb, nw) and (w / max(nb, nw) < hd + 2):
        raise PartError("furos demais para a largura (hole_d)")
    p = safe_name(name, "suporte_l")
    prof = [(0, 0), (bd, 0), (bd, t), (t, t), (t, wh), (0, wh)]                 # (y, z)
    steps = [Step("extrude", p, [("p", prof)], 0, w, "+w", "perfil L", axis="x")]
    if nb and hd:
        loops = [("c", w * (i + 0.5) / nb, yb, hd / 2) for i in range(nb)]       # (x, y)
        steps.append(Step("cut", p, loops, -1.0, t + 2.0, "+w", "furos da base", axis="z"))
    if nw and hd:
        loops = [("c", w * (i + 0.5) / nw, zc, hd / 2) for i in range(nw)]       # (x, z)
        steps.append(Step("cut", p, loops, -1.0, t + 2.0, "+w", "furos da parede", axis="y"))
    vol = (bd * t + (wh - t) * t) * w - (nb + nw) * circle_area(hd / 2) * t
    return Plan(p, steps, p, ((0, 0, 0), (w, bd, wh)), vol, volume_tol=0.02, export_parts=[p])


def pipe_tee(od=60.0, bore=50.0, run_length=200.0, branch_height=100.0, name="te") -> Plan:
    """Te de tubulacao: tubo principal ao longo de x e derivacao vertical (z); furos passantes cortados em cada tubo."""
    R, r = _pos("od", od, 4) / 2, _pos("bore", bore, 1) / 2
    L, H = _pos("run_length", run_length, 4), _pos("branch_height", branch_height, 1)
    if r >= R - 0.5:
        raise PartError("bore deve ser menor que od (parede minima 0,5 mm)")
    if L <= 2 * R or H < R:
        raise PartError("run_length deve ser > od e branch_height >= od/2")
    p = safe_name(name, "te")
    steps = [Step("extrude", p, [("c", 0, 0, R)], -L / 2, L, "+w", "tubo principal (eixo x)", axis="x"),
             Step("extrude", p, [("c", 0, 0, R)], 0, H, "+w", "derivacao (eixo z)", axis="z"),
             Step("cut", p, [("c", 0, 0, r)], -L / 2 - 1, L + 2, "+w", "furo do tubo principal", axis="x"),
             Step("cut", p, [("c", 0, 0, r)], 0, H + 1, "+w", "furo da derivacao", axis="z")]
    # Dois cilindros perpendiculares de mesmo raio: interseccao (z>=0) = 8/3 R^3 (Steinmetz).
    vol = (math.pi * R * R * (L + H) - 8.0 / 3.0 * R ** 3) - (math.pi * r * r * (L + H) - 8.0 / 3.0 * r ** 3)
    return Plan(p, steps, p, ((-L / 2, -R, -R), (L / 2, R, H)), vol, volume_tol=0.03, export_parts=[p],
                notes="volume analitico por Steinmetz (cilindros perpendiculares)")


def _cross_hole_volume(R: float, r: float, c: float, n: int = 400) -> float:
    """Volume retirado por um furo transversal (raio c, ao longo de y) atraves das duas paredes de um tubo R/r.

    Na posicao lateral x o furo tem altura 2*sqrt(c^2-x^2) (em z) e atravessa duas paredes de espessura
    sqrt(R^2-x^2) - sqrt(r^2-x^2) (para |x| < r; exige c < r).
    """
    if c >= r:
        raise PartError("o furo transversal deve ser menor que o furo passante")
    tot = 0.0
    for i in range(n):
        x = -c + (i + 0.5) * 2 * c / n
        chord = 2 * math.sqrt(max(c * c - x * x, 0.0))
        wall = math.sqrt(R * R - x * x) - math.sqrt(r * r - x * x)
        tot += chord * wall * (2 * c / n)
    return 2 * tot


def bearing_housing(base_l=140.0, base_w=80.0, base_t=14.0, boss_od=72.0, boss_h=50.0, bore=52.0,
                    slot_len=18.0, slot_w=11.0, slot_x=55.0, set_screw_d=8.5, name="mancal") -> Plan:
    """Mancal (housing de rolamento): base com 2 rasgos de fixacao, corpo cilindrico, furo passante e furo transversal."""
    L, W, T = _pos("base_l", base_l, 20), _pos("base_w", base_w, 20), _pos("base_t", base_t, 2)
    OD, H, B = _pos("boss_od", boss_od, 10), _pos("boss_h", boss_h, 5), _pos("bore", bore, 2)
    sl, sw, sx, ss = _pos("slot_len", slot_len, 2), _pos("slot_w", slot_w, 2), _pos("slot_x", slot_x, 0), _pos("set_screw_d", set_screw_d, 0)
    if OD > W or H <= T or B > OD - 6 or sx - sl / 2 < OD / 2 + 2 or sx + sl / 2 > L / 2 - 2:
        raise PartError("dimensoes incompativeis (boss_od<=base_w, boss_h>base_t, parede>=3 mm, rasgos entre o corpo e a borda)")
    zc = (T + H) / 2.0
    if ss and (zc - ss / 2 < T + 1 or zc + ss / 2 > H - 1):
        raise PartError("set_screw_d nao cabe na altura do corpo")
    p = safe_name(name, "mancal")
    slots = [slot(-sx, 0, sl, sw), slot(sx, 0, sl, sw)]
    steps = [Step("extrude", p, [("r", -L / 2, -W / 2, L / 2, W / 2)] + slots, 0, T, label="base com rasgos"),
             Step("extrude", p, [("c", 0, 0, OD / 2)], T, H - T, label="corpo cilindrico"),
             Step("cut", p, [("c", 0, 0, B / 2)], -1.0, H + 2.0, "+w", "furo passante", axis="z")]
    vol = (L * W * T - 2 * poly_area(slots[0][1]) * T + math.pi * (OD / 2) ** 2 * (H - T) - math.pi * (B / 2) ** 2 * H)
    if ss:
        steps.append(Step("cut", p, [("c", 0, zc, ss / 2)], -OD / 2 - 1, OD + 2, "+w", "furo transversal", axis="y"))
        vol -= _cross_hole_volume(OD / 2, B / 2, ss / 2)
    return Plan(p, steps, p, ((-L / 2, -W / 2, 0), (L / 2, W / 2, H)), vol, volume_tol=0.03, export_parts=[p])


def tslot_profile(size=40.0, length=200.0, slot_w=8.0, lip=4.0, cavity_w=14.0, cavity_depth=7.0, bore=8.5,
                  name="perfil_t") -> Plan:
    """Perfil de aluminio com 4 ranhuras em T (tipo 40x40), furo central, extrudado em z."""
    s_, Lg = _pos("size", size, 10), _pos("length", length, 1)
    a, l, b, d, hb = _pos("slot_w", slot_w, 1), _pos("lip", lip, 0.5), _pos("cavity_w", cavity_w, 2), _pos("cavity_depth", cavity_depth, 1), _pos("bore", bore, 0)
    h = s_ / 2.0
    if not (a < b < s_ - 2 * l) or l + d >= h - 1:
        raise PartError("ranhura invalida: exige slot_w < cavity_w e lip + cavity_depth < size/2 - 1")
    core = h - l - d                                    # meia-largura do nucleo central
    if b / 2 >= core - 0.5 or (hb and hb / 2 >= core - 1):
        raise PartError("as cavidades vizinhas se tocam ou o furo central e grande demais (reduza cavity_w/cavity_depth/bore)")
    side = [(-h, -h), (-a / 2, -h), (-a / 2, -h + l), (-b / 2, -h + l), (-b / 2, -h + l + d),
            (b / 2, -h + l + d), (b / 2, -h + l), (a / 2, -h + l), (a / 2, -h)]
    outline = []
    for k in range(4):
        for x, y in side:
            for _ in range(k):
                x, y = -y, x                            # gira 90 graus anti-horario
            outline.append((x, y))
    loops = [("p", outline)] + ([("c", 0, 0, hb / 2)] if hb else [])
    p = safe_name(name, "perfil_t")
    vol = (poly_area(outline) - (circle_area(hb / 2) if hb else 0)) * Lg
    return Plan(p, [Step("extrude", p, loops, 0, Lg, label="perfil com ranhuras em T")], p,
                ((-h, -h, 0), (h, h, Lg)), vol, volume_tol=0.005, export_parts=[p])


def machined_block(length=80.0, width=60.0, height=30.0, corner_r=6.0, chamfer=2.0, bore=20.0, bore_lead_in=0.0,
                   name="bloco_usinado") -> Plan:
    """Bloco usinado: cantos verticais filetados (BLEND), chanfro no topo e furo central chanfrado (CHAMFER).

    `bore_lead_in` > 0: o chanfro do furo vira ASSIMETRICO (DIST_DIST): `chamfer` na face do topo (radial) e
    `bore_lead_in` na parede do furo (axial), como uma entrada de furo usinada."""
    L, W, H = _pos("length", length, 10), _pos("width", width, 10), _pos("height", height, 4)
    R, c, B = _pos("corner_r", corner_r, 1), _pos("chamfer", chamfer, 0.2), _pos("bore", bore, 0)
    bl = _pos("bore_lead_in", bore_lead_in, 0)
    if bl and (not B or bl >= H / 2):
        raise PartError("bore_lead_in exige furo (bore > 0) e deve ser menor que height/2")
    if R >= min(L, W) / 2:
        raise PartError("corner_r deve ser menor que metade do menor lado")
    if c >= R or c >= H / 2:
        raise PartError("chamfer deve ser menor que corner_r e que height/2")
    if B and B / 2 + 2 * c + 1 > min(L, W) / 2:
        raise PartError("bore grande demais para o bloco (sobra < chanfro + 1 mm de parede)")
    p = safe_name(name, "bloco_usinado")
    steps = [Step("extrude", p, [("r", -L / 2, -W / 2, L / 2, W / 2)], 0, H, "+w", "bloco"),
             Step("fillet", p, [("vertical",)], dz=R, label="filete dos cantos verticais"),
             Step("chamfer", p, [("z", H)], dz=c, label="chanfro do contorno do topo")]
    r = B / 2
    if B:
        steps.append(Step("cut", p, [("c", 0, 0, r)], -1.0, H + 2.0, "+w", "furo central", axis="z"))
        if bl:
            # medido ao vivo: na aresta do furo, distancia1 vai para a PAREDE (axial) e distancia2 para a face do topo
            # (radial) - o contrario do contorno externo; por isso dz = axial (bl) e dz2 = radial (c)
            steps.append(Step("chamfer2", p, [("zr", H, r + 0.5)], dz=bl, dz2=c, label="chanfro assimetrico do furo"))
        else:
            steps.append(Step("chamfer", p, [("zr", H, r + 0.5)], dz=c, label="chanfro do furo"))
    # Secao a altura z do chanfro: contorno recuado d = (z - (H - c)) -> retangulo (L-2d)(W-2d) com raio R-d.
    v_outer = (L + W) * c ** 2 - 4 * c ** 3 / 3 - (4 - math.pi) * (R * c ** 2 - c ** 3 / 3)
    # chanfro do furo: triangulo (radial c x axial b) girado ao redor do eixo (Pappus: centroide a r + c/3)
    v_bore = (math.pi * r * r * H + math.pi * c * (bl or c) * (r + c / 3)) if B else 0.0
    vol = L * W * H - (4 - math.pi) * R * R * H - v_outer - v_bore
    return Plan(p, steps, p, ((-L / 2, -W / 2, 0), (L / 2, W / 2, H)), vol, volume_tol=0.02, export_parts=[p],
                notes="volume analitico: bloco - cantos filetados - chanfro do contorno - furo e seu chanfro")


def enclosure(length=120.0, width=80.0, height=50.0, wall=3.0, corner_r=8.0, boss_d=9.0, boss_hole=3.4, boss_h=12.0,
              boss_hole_depth=10.0, cable_hole=16.0, lid_t=3.0, name="caixa") -> Plan:
    """Caixa eletrica/carcaca (2 pecas): corpo com cantos filetados e SHELL_3D (topo aberto, parede `wall`), 4 colunas
    de parafuso com furo cego, furo de passagem de cabo na parede frontal (plano y) e tampa lisa com 4 furos."""
    L, W, H = _pos("length", length, 40), _pos("width", width, 40), _pos("height", height, 20)
    w, R = _pos("wall", wall, 1), _pos("corner_r", corner_r, 1)
    bd, bh_, bh, bdepth = _pos("boss_d", boss_d, 3), _pos("boss_hole", boss_hole, 1), _pos("boss_h", boss_h, 2), _pos("boss_hole_depth", boss_hole_depth, 1)
    cd, lt = _pos("cable_hole", cable_hole, 0), _pos("lid_t", lid_t, 1)
    if R <= w or R >= min(L, W) / 2 - 2:
        raise PartError("corner_r deve ser maior que a parede (wall) e menor que metade do menor lado")
    if bh_ >= bd - 1 or bdepth > bh or w + bh >= H - 2:
        raise PartError("colunas: boss_hole < boss_d - 1, boss_hole_depth <= boss_h e w + boss_h < height - 2")
    g = 2.0                                           # folga entre a coluna e a parede
    br = bd / 2
    bx, by = L / 2 - w - br - g, W / 2 - w - br - g
    if 2 * bx < bd + 2 or 2 * by < bd + 2:
        raise PartError("as colunas se tocam: reduza boss_d ou aumente length/width")
    rc = R - w
    ax, ay = L / 2 - w - rc, W / 2 - w - rc          # centro do arco do canto interno
    for k in range(360):                              # a coluna cabe no interior arredondado
        px, py = bx + br * math.cos(math.radians(k)), by + br * math.sin(math.radians(k))
        if px > ax and py > ay and math.hypot(px - ax, py - ay) > rc - 0.3:
            raise PartError("a coluna nao cabe no canto arredondado: reduza boss_d/corner_r ou aumente wall")
    if cd and (cd / 2 >= L / 2 - R - 1 or H / 2 - cd / 2 < w + 2 or H / 2 + cd / 2 > H - 2):
        raise PartError("cable_hole grande demais para a parede frontal")
    p = safe_name(name, "caixa")
    lid = f"{p}_tampa"
    hole_xy = [(sx * bx, sy * by) for sx in (-1, 1) for sy in (-1, 1)]
    steps = [Step("extrude", p, [("r", -L / 2, -W / 2, L / 2, W / 2)], 0, H, label="bloco da caixa"),
             Step("fillet", p, [("vertical",)], dz=R, label="filete dos cantos"),
             Step("shell", p, [("face_z", H)], dz=w, label="casca (topo aberto)"),
             Step("extrude", p, [("c", x, y, br) for x, y in hole_xy], w - 1.0, bh + 1.0, label="colunas de parafuso"),
             Step("cut", p, [("c", x, y, bh_ / 2) for x, y in hole_xy], w + bh + 1.0, bdepth + 1.0, "-w", "furos das colunas")]
    if cd:
        steps.append(Step("cut", p, [("c", 0, H / 2, cd / 2)], -W / 2 - 1.0, w + 2.0, "+w", "furo do cabo (y=-W/2)", axis="y"))
    hl = bh_ + 0.8
    steps += [Step("extrude", lid, [("r", -L / 2, -W / 2, L / 2, W / 2)], H, lt, label="tampa"),
              Step("fillet", lid, [("vertical",)], dz=R, label="filete da tampa"),
              Step("cut", lid, [("c", x, y, hl / 2) for x, y in hole_xy], H + lt + 1.0, lt + 2.0, "-w", "furos da tampa")]
    corner = (4 - math.pi) * R * R
    rc_area = (4 - math.pi) * rc * rc
    v_body = (L * W - corner) * H - ((L - 2 * w) * (W - 2 * w) - rc_area) * (H - w)
    v_body += 4 * circle_area(br) * bh - 4 * circle_area(bh_ / 2) * bdepth - (circle_area(cd / 2) * w if cd else 0.0)
    v_lid = (L * W - corner) * lt - 4 * circle_area(hl / 2) * lt
    bb_body = ((-L / 2, -W / 2, 0), (L / 2, W / 2, H))
    bb_lid = ((-L / 2, -W / 2, H), (L / 2, W / 2, H + lt))
    return Plan(p, steps, p, bb_body, v_body + v_lid, volume_tol=0.02, export_parts=[p, lid],
                volume_parts=[(p, v_body), (lid, v_lid)], part_bboxes={p: bb_body, lid: bb_lid},
                notes="volume analitico: casca (bloco - cavidade) + colunas - furos - furo do cabo; tampa com 4 furos")


def manifold_block(length=100.0, width=60.0, height=50.0, top_port_d=10.0, top_depth=30.0, cbore_d=16.0,
                   cbore_depth=8.0, side_port_d=8.0, side_depth=22.0, name="manifold") -> Plan:
    """Bloco de manifold hidraulico: furo cego escalonado (rebaixo) no topo e 4 furos cegos laterais (planos x/y/z)."""
    L, W, H = _pos("length", length, 30), _pos("width", width, 30), _pos("height", height, 20)
    td, tdep, cd, cdep = _pos("top_port_d", top_port_d, 1), _pos("top_depth", top_depth, 1), _pos("cbore_d", cbore_d, 0), _pos("cbore_depth", cbore_depth, 0)
    sd, sdep = _pos("side_port_d", side_port_d, 1), _pos("side_depth", side_depth, 1)
    if cd and (cd <= td or cdep >= tdep):
        raise PartError("cbore_d deve ser maior que top_port_d e cbore_depth menor que top_depth")
    big = max(td, cd)
    if tdep >= H - 3:
        raise PartError("top_depth deve deixar >= 3 mm de fundo (height - 3)")
    if sd >= H - 6 or 2 * sdep >= W - 2 or sdep >= W / 2 - sd / 2 - 1:
        raise PartError("furos laterais: side_depth/side_port_d incompativeis com a largura/altura do bloco")
    if sdep >= L / 2 - big / 2 - 2 or abs(L / 4 - L / 2) < big / 2 + sd / 2 + 2 or big > min(L, W) - 6:
        raise PartError("os furos laterais nao podem encontrar o furo do topo (aumente length/width ou reduza as profundidades)")
    p = safe_name(name, "manifold")
    cx, cy, zc = L / 2, W / 2, H / 2
    steps = [Step("extrude", p, [("r", 0, 0, L, W)], 0, H, label="bloco")]
    steps.append(Step("cut", p, [("c", cx, cy, td / 2)], H + 1, tdep + 1, "-w", "furo do topo", axis="z"))
    vol_cut = circle_area(td / 2) * tdep
    if cd:
        steps.append(Step("cut", p, [("c", cx, cy, cd / 2)], H + 1, cdep + 1, "-w", "rebaixo do topo", axis="z"))
        vol_cut += (circle_area(cd / 2) - circle_area(td / 2)) * cdep
    steps.append(Step("cut", p, [("c", L / 4, zc, sd / 2)], -1.0, sdep + 1, "+w", "furo frontal (y=0)", axis="y"))
    steps.append(Step("cut", p, [("c", 3 * L / 4, zc, sd / 2)], W + 1, sdep + 1, "-w", "furo traseiro (y=W)", axis="y"))
    steps.append(Step("cut", p, [("c", cy, zc, sd / 2)], -1.0, sdep + 1, "+w", "furo esquerdo (x=0)", axis="x"))
    steps.append(Step("cut", p, [("c", cy, zc, sd / 2)], L + 1, sdep + 1, "-w", "furo direito (x=L)", axis="x"))
    vol_cut += 4 * circle_area(sd / 2) * sdep
    vol = L * W * H - vol_cut
    return Plan(p, steps, p, ((0, 0, 0), (L, W, H)), vol, volume_tol=0.01, export_parts=[p],
                notes="furos cegos em 5 faces; sentidos negativos (-w) nos planos z, y e x")


# Parafuso Allen (ISO 4762 / DIN 912): d -> (diametro da cabeca dk, altura k, sextavado interno s, profundidade t)
SOCKET_TABLE = {5: (8.5, 5.0, 4.0, 2.5), 6: (10.0, 6.0, 5.0, 3.0), 8: (13.0, 8.0, 6.0, 4.0), 10: (16.0, 10.0, 8.0, 5.0),
                12: (18.0, 12.0, 10.0, 6.0), 16: (24.0, 16.0, 14.0, 8.0), 20: (30.0, 20.0, 17.0, 10.0)}


def socket_head_screw(size=10, length=40.0, tip_chamfer=1.0, head_chamfer=0.8, name="allen", thread_length=0.0) -> Plan:
    """Parafuso de cabeca cilindrica com sextavado interno (ISO 4762): haste, cabeca, sextavado interno cego, chanfros da
    ponta e do topo da cabeca e, com thread_length > 0, rosca helicoidal modelada a partir da ponta."""
    if int(size) not in SOCKET_TABLE:
        raise PartError(f"tamanho M{size} fora da tabela: {sorted(SOCKET_TABLE)}")
    dk, k, s, t = SOCKET_TABLE[int(size)]
    d, L = float(int(size)), _pos("length", length, 5)
    ct, ch = _pos("tip_chamfer", tip_chamfer, 0), _pos("head_chamfer", head_chamfer, 0)
    if ct >= d / 2 or ch >= k / 2 or ch >= (dk - 2 * s / math.sqrt(3)) / 2:
        raise PartError("chanfros grandes demais para o tamanho do parafuso")
    p = safe_name(name, "allen")
    top = L + k
    hexa = [(x, y) for x, y in regular_polygon(6, s / math.sqrt(3), 0.0)]
    steps = [Step("extrude", p, [("c", 0, 0, d / 2)], 0, L, label="haste"),
             Step("extrude", p, [("c", 0, 0, dk / 2)], L, k, label="cabeca"),
             Step("cut", p, [("p", hexa)], top + 1, t + 1, "-w", "sextavado interno", axis="z")]
    vol = circle_area(d / 2) * L + circle_area(dk / 2) * k - (math.sqrt(3) / 2) * s * s * t
    if ct:
        steps.append(Step("chamfer", p, [("zr", 0.0, d / 2 + 0.5, d / 2 - 0.5)], dz=ct, label="chanfro da ponta"))
        vol -= math.pi * (d / 2 * ct ** 2 - ct ** 3 / 3)
    if ch:
        steps.append(Step("chamfer", p, [("zr", top, dk / 2 + 0.5, dk / 2 - 0.5)], dz=ch, label="chanfro da cabeca"))
        vol -= math.pi * (dk / 2 * ch ** 2 - ch ** 3 / 3)
    thread_length = _pos("thread_length", thread_length, 0)
    notes = "sextavado interno de s mm entre faces e t mm de profundidade; rosca nao modelada"
    if thread_length > 0:
        st, groove, P = thread_step(p, d, L, thread_length)
        steps.append(st)
        vol -= groove
        notes = (f"sextavado interno de s mm entre faces e t mm de profundidade; rosca M{size}x{P:g} modelada em "
                 f"{thread_length:g} mm a partir da ponta")
    return Plan(p, steps, p, ((-dk / 2, -dk / 2, 0), (dk / 2, dk / 2, top)), vol, volume_tol=0.03, export_parts=[p],
                notes=notes)


def _move_loops(loops, dx, dy) -> list:
    """Translada contornos 2D (c, r, p) em (dx, dy)."""
    out = []
    for lp in loops:
        if lp[0] == "c":
            out.append(("c", lp[1] + dx, lp[2] + dy, lp[3]))
        elif lp[0] == "r":
            out.append(("r", lp[1] + dx, lp[2] + dy, lp[3] + dx, lp[4] + dy))
        elif lp[0] == "p":
            out.append(("p", [(x + dx, y + dy) for x, y in lp[1]]))
        else:
            raise PartError(f"contorno '{lp[0]}' nao pode ser transladado")
    return out


def _placed(plan: Plan, dx: float, dy: float, dz: float) -> list:
    """Copia os passos de `plan` deslocados (dx, dy, dz); so extrusoes em planos z (pecas de revolucao do catalogo)."""
    import dataclasses

    out = []
    for st in plan.steps:
        if st.kind != "extrude" or st.axis != "z":
            raise PartError("so e possivel posicionar passos 'extrude' em planos z")
        out.append(dataclasses.replace(st, z0=st.z0 + dz, loops=_move_loops(st.loops, dx, dy)))
    return out


def flanged_joint(size=16, bolts=4, od=165.0, bore=60.0, pcd=125.0, thickness=20.0, gasket_t=3.0,
                  protrusion=4.0, name="juncao") -> Plan:
    """Juncao flangeada de tubulacao: 2 flanges + junta de vedacao + N parafusos sextavados + N porcas.

    Cada peca e posicionada por (x, y, z) - nada de MOVE. Todos os bbox sao conferidos; o volume do STL e conferido
    por amostra (flanges, junta, 1 parafuso, 1 porca)."""
    if int(size) not in HEX_TABLE:
        raise PartError(f"tamanho M{size} fora da tabela: {sorted(HEX_TABLE)}")
    sz = int(size)
    s_af, k, m = HEX_TABLE[sz]
    n = int(_pos("bolts", bolts, 3, 16))
    od, bore, pcd = _pos("od", od, 30), _pos("bore", bore, 5), _pos("pcd", pcd, 20)
    t, g, prot = _pos("thickness", thickness, 2), _pos("gasket_t", gasket_t, 0.5), _pos("protrusion", protrusion, 2)
    hole = sz + 2.0
    R = s_af / math.sqrt(3)
    if pcd / 2 + max(R, hole / 2) + 1 > od / 2:
        raise PartError("os parafusos (cabeca/porca) passam da borda do flange: aumente od ou reduza pcd")
    if pcd / 2 - max(R, hole / 2) - 1 < bore / 2:
        raise PartError("os parafusos (cabeca/porca) invadem o furo central: reduza bore ou aumente pcd")
    if 2 * (pcd / 2) * math.sin(math.pi / n) < 2 * R + 1:
        raise PartError("parafusos muito proximos entre si (porcas se tocam): reduza bolts ou aumente pcd")
    g_od = pcd - hole - 2.0                      # junta em anel, dentro do circulo de parafusos
    if g_od <= bore + 4:
        raise PartError("pcd pequeno demais para a junta de vedacao")
    p = safe_name(name, "juncao")
    start = 180.0 / n                            # furos simetricos em relacao aos eixos
    centers = [(pcd / 2 * math.cos(math.radians(start + 360.0 * i / n)),
                pcd / 2 * math.sin(math.radians(start + 360.0 * i / n))) for i in range(n)]
    z_f1 = prot + m
    z_g = z_f1 + t
    z_f2 = z_g + g
    z_head = z_f2 + t                            # = comprimento da haste
    names = {"f1": f"{p}_flange1", "f2": f"{p}_flange2", "gasket": f"{p}_junta"}
    bolt_names = [f"{p}_parafuso{i + 1}" for i in range(n)]
    nut_names = [f"{p}_porca{i + 1}" for i in range(n)]
    disc = [("c", 0, 0, od / 2), ("c", 0, 0, bore / 2)] + bolt_circle(n, pcd, hole, start)
    entries = [(z_f1, Step("extrude", names["f1"], disc, z_f1, t, label="flange 1")),
               (z_g, Step("extrude", names["gasket"], [("c", 0, 0, g_od / 2), ("c", 0, 0, bore / 2)], z_g, g, label="junta")),
               (z_f2, Step("extrude", names["f2"], disc, z_f2, t, label="flange 2"))]
    bolt_tpl, nut_tpl = hex_bolt(sz, z_head, "b"), hex_nut(sz, "n")
    for i, (cx, cy) in enumerate(centers):
        for st in _placed(Plan("b", bolt_tpl.steps), cx, cy, 0.0):
            entries.append((st.z0, _rename(st, bolt_names[i])))
        for st in _placed(Plan("n", nut_tpl.steps), cx, cy, prot):
            entries.append((st.z0, _rename(st, nut_names[i])))
    entries.sort(key=lambda e: e[0])             # estavel: menos reposicionamentos do plano e ordem z por peca
    steps = [st for _z, st in entries]
    flange_vol = (circle_area(od / 2) - circle_area(bore / 2) - n * circle_area(hole / 2)) * t
    gasket_vol = (circle_area(g_od / 2) - circle_area(bore / 2)) * g
    sample = [(names["f1"], flange_vol), (names["f2"], flange_vol), (names["gasket"], gasket_vol),
              (bolt_names[0], bolt_tpl.expected_volume), (nut_names[0], nut_tpl.expected_volume)]
    bboxes = {names["f1"]: ((-od / 2, -od / 2, z_f1), (od / 2, od / 2, z_f1 + t)),
              names["f2"]: ((-od / 2, -od / 2, z_f2), (od / 2, od / 2, z_f2 + t)),
              names["gasket"]: ((-g_od / 2, -g_od / 2, z_g), (g_od / 2, g_od / 2, z_g + g))}
    for i, (cx, cy) in enumerate(centers):
        bboxes[bolt_names[i]] = ((cx - R, cy - s_af / 2, 0.0), (cx + R, cy + s_af / 2, z_head + k))
        bboxes[nut_names[i]] = ((cx - R, cy - s_af / 2, prot), (cx + R, cy + s_af / 2, prot + m))
    all_parts = list(names.values()) + bolt_names + nut_names
    return Plan(p, steps, names["f1"], bboxes[names["f1"]], sum(v for _n, v in sample), volume_tol=0.03,
                export_parts=all_parts, volume_parts=sample, part_bboxes=bboxes,
                notes=f"{n} parafusos M{sz}x{z_head:g} + {n} porcas; bbox conferido em todas as {len(all_parts)} pecas, "
                      f"volume por amostra (flanges, junta, 1 parafuso, 1 porca)")


def _rename(st: Step, part: str) -> Step:
    import dataclasses

    return dataclasses.replace(st, part=part)


WASHER_TABLE = {5: (5.3, 10.0, 1.0), 6: (6.4, 12.0, 1.6), 8: (8.4, 16.0, 1.6), 10: (10.5, 20.0, 2.0), 12: (13.0, 24.0, 2.5),
                16: (17.0, 30.0, 3.0), 20: (21.0, 37.0, 3.0), 24: (25.0, 44.0, 4.0), 30: (31.0, 56.0, 4.0)}


def _shifted(plan: Plan, dz: float) -> list:
    """Copia os passos de `plan` com o plano de trabalho deslocado `dz` em z (so planos z)."""
    import dataclasses

    out = []
    for st in plan.steps:
        if st.axis != "z":
            raise PartError("so e possivel deslocar passos em planos z")
        out.append(dataclasses.replace(st, z0=st.z0 + dz))
    return out


def bolted_joint(size=12, plate_w=80.0, plate_d=60.0, t1=15.0, t2=15.0, protrusion=4.0, name="junta") -> Plan:
    """Junta parafusada (montagem de 5 pecas): 2 chapas furadas, parafuso sextavado com cabeca em cima, arruela e porca.

    Cada peca e posicionada pela cota z (sem MOVE); o plano confere bbox e volume de CADA peca."""
    if int(size) not in HEX_TABLE or int(size) not in WASHER_TABLE:
        raise PartError(f"tamanho M{size} fora da tabela: {sorted(set(HEX_TABLE) & set(WASHER_TABLE))}")
    sz = int(size)
    s_af, k, m = HEX_TABLE[sz]
    w_id, w_od, w_t = WASHER_TABLE[sz]
    W, D = _pos("plate_w", plate_w, 20), _pos("plate_d", plate_d, 20)
    a1, a2, prot = _pos("t1", t1, 2), _pos("t2", t2, 2), _pos("protrusion", protrusion, 2)
    if W < w_od + 6 or D < w_od + 6:
        raise PartError("as chapas devem ser maiores que a arruela (plate_w/plate_d >= OD da arruela + 6)")
    hole = sz + 2.0
    p = safe_name(name, "junta")
    z_nut0 = prot
    z_p1 = z_nut0 + m
    z_p2 = z_p1 + a1
    z_wsh = z_p2 + a2
    z_head = z_wsh + w_t                                  # = comprimento L da haste
    L = z_head
    names = {"p1": f"{p}_chapa1", "p2": f"{p}_chapa2", "bolt": f"{p}_parafuso", "washer": f"{p}_arruela", "nut": f"{p}_porca"}
    plate_loops = [("r", -W / 2, -D / 2, W / 2, D / 2), ("c", 0, 0, hole / 2)]
    bolt = hex_bolt(sz, L, names["bolt"])
    nut = hex_nut(sz, names["nut"])
    steps = [Step("extrude", names["p1"], plate_loops, z_p1, a1, label="chapa 1"),
             Step("extrude", names["p2"], plate_loops, z_p2, a2, label="chapa 2"),
             Step("extrude", names["washer"], [("c", 0, 0, w_od / 2), ("c", 0, 0, w_id / 2)], z_wsh, w_t, label="arruela")]
    steps += _shifted(bolt, 0.0)
    steps += _shifted(nut, z_nut0)
    plate_vol = lambda t: (W * D - circle_area(hole / 2)) * t                        # noqa: E731
    vols = {names["p1"]: plate_vol(a1), names["p2"]: plate_vol(a2),
            names["washer"]: (circle_area(w_od / 2) - circle_area(w_id / 2)) * w_t,
            names["bolt"]: bolt.expected_volume, names["nut"]: nut.expected_volume}
    R = s_af / math.sqrt(3)
    bboxes = {names["p1"]: ((-W / 2, -D / 2, z_p1), (W / 2, D / 2, z_p1 + a1)),
              names["p2"]: ((-W / 2, -D / 2, z_p2), (W / 2, D / 2, z_p2 + a2)),
              names["washer"]: ((-w_od / 2, -w_od / 2, z_wsh), (w_od / 2, w_od / 2, z_wsh + w_t)),
              names["bolt"]: ((-R, -s_af / 2, 0), (R, s_af / 2, L + k)),
              names["nut"]: ((-R, -s_af / 2, z_nut0), (R, s_af / 2, z_nut0 + m))}
    return Plan(p, steps, names["bolt"], bboxes[names["bolt"]], sum(vols.values()), volume_tol=0.03,
                volume_parts=list(vols.items()), part_bboxes=bboxes, export_parts=list(names.values()),
                notes=f"parafuso M{sz}x{L:g} (haste de 0 a {L:g}, cabeca em cima), porca embaixo; 5 pecas posicionadas por cota z")


def compression_spring(wire_d=3.0, mean_d=20.0, pitch=6.0, turns=6.0, name="mola") -> Plan:
    """Mola helicoidal de compressao de fio redondo (HELIX_DIA, material adicionado): diametro do fio, diametro medio da
    espira, passo e numero de voltas ativas. Altura livre = voltas x passo + diametro do fio."""
    d = _pos("wire_d", wire_d, 0.2)
    D = _pos("mean_d", mean_d)
    P = _pos("pitch", pitch)
    N = _pos("turns", turns, 1)
    if D < 1.5 * d:
        raise PartError(f"mean_d={D:g} deve ser >= 1,5 x wire_d ({1.5 * d:g}); senao nao sobra furo interno")
    if P < 1.05 * d:
        raise PartError(f"pitch={P:g} deve ser >= 1,05 x wire_d ({1.05 * d:g}): espiras se sobrepoem")
    p = safe_name(name, "mola")
    r, Rc = d / 2.0, D / 2.0
    steps = [Step("helix_add", p, [("c", Rc, r, r)], 0.0, P, axis="y", dz2=N,
                  label=f"mola d{d:g} D{D:g} p{P:g} x{N:g}")]
    height = N * P + d
    vol = circle_area(r) * 2.0 * math.pi * Rc * N                      # Pappus (secao no plano axial)
    return Plan(p, steps, p, ((-(Rc + r), -(Rc + r), 0), (Rc + r, Rc + r, height)), vol, volume_tol=0.12,
                export_parts=[p],
                notes=("o STL tessela fios finos de forma grosseira (Ø5 -> octogono, ~-10 % de volume): o bbox e a "
                       "referencia exata; hélice direita; altura livre " + f"{height:g} mm"))


def threaded_rod(size=12, length=60.0, name="varao", thread_length=0.0, end_chamfer=0.0, pitch=0.0) -> Plan:
    """Varao roscado (tipo DIN 975/976): haste de Ø M`size` com rosca helicoidal ISO (sulco 60 graus) em thread_length mm a
    partir da ponta (0 = rosca total) e chanfros opcionais nas pontas."""
    d = float(int(size))
    L = _pos("length", length, 4 * (float(pitch) or COARSE_PITCH.get(int(size), 1.0)))
    tl = _pos("thread_length", thread_length, 0)
    c = _pos("end_chamfer", end_chamfer, 0)
    p = safe_name(name, "varao")
    st, groove, P = thread_step(p, d, L, tl, pitch, through=True)
    R = d / 2.0
    if c >= R / 2:
        raise PartError("end_chamfer grande demais para o diametro")
    steps = [Step("extrude", p, [("c", 0, 0, R)], 0, L, label="haste")]
    vol = circle_area(R) * L
    if c > 0:
        steps.append(Step("chamfer", p, [("zr", 0.0, R + 0.5, R - 0.5)], dz=c, label="chanfro da ponta"))
        steps.append(Step("chamfer", p, [("zr", L, R + 0.5, R - 0.5)], dz=c, label="chanfro do topo"))
        vol -= 2 * math.pi * (R * c ** 2 - c ** 3 / 3)
    steps.append(st)
    vol -= groove
    full = tl <= 0 or tl >= L
    return Plan(p, steps, p, ((-R, -R, 0), (R, R, L)), vol, volume_tol=0.04, export_parts=[p],
                notes=f"rosca M{size:g}x{P:g} " + ("total" if full else f"em {tl:g} mm a partir da ponta")
                + "; o STL tessela o cilindro (-2 %) e o sulco helicoidal (-10 % do volume do sulco)")


# Rosca trapezoidal ISO 2904 (fuso de acionamento): diametro nominal -> passo normal (mm)
TR_TABLE = {10: 2.0, 12: 3.0, 14: 3.0, 16: 4.0, 18: 4.0, 20: 4.0, 22: 5.0, 24: 5.0, 26: 5.0, 28: 5.0, 30: 6.0,
            32: 6.0, 36: 6.0, 40: 7.0}
_TAN15 = math.tan(math.radians(15.0))
_TAN20 = math.tan(math.radians(20.0))


def trapezoid_groove(d: float, pitch: float = 0.0):
    """Sulco da rosca trapezoidal externa (flancos a 15 graus): devolve (passo, R, h3, largura na crista g_R, largura no
    fundo g_r, mm3 por mm de eixo). Altura h3 = 0,5 P + ac (ac = 0,25 se P <= 5, senao 0,5); a linha primitiva fica a 0,25 P da crista."""
    P = float(pitch) if pitch else TR_TABLE.get(int(round(d)))
    if not P or P <= 0:
        raise PartError(f"Tr{d:g}: sem passo na tabela {sorted(TR_TABLE)}; informe 'pitch'")
    ac = 0.25 if P <= 5 else 0.5
    h3 = 0.5 * P + ac
    R = d / 2.0
    if h3 >= 0.45 * R:
        raise PartError(f"passo {P:g} grande demais para o diametro {d:g}")
    w_c = 0.5 * P - 2 * 0.25 * P * _TAN15                  # largura do filete na crista
    w_r = 0.5 * P + 2 * (h3 - 0.25 * P) * _TAN15           # ... e na raiz
    g_R, g_r = P - w_c, P - w_r
    area = (g_R + g_r) / 2.0 * h3
    rc = R - h3 * (g_R + 2 * g_r) / (3.0 * (g_R + g_r))
    return P, R, h3, g_R, g_r, area * 2.0 * math.pi * rc / P


def lead_screw(size=20, length=200.0, name="fuso", thread_length=0.0, end_chamfer=0.0, pitch=0.0) -> Plan:
    """Fuso trapezoidal Tr (ISO 2904): haste de Ø `size` com rosca trapezoidal helicoidal (sulco com flancos de 15 graus por
    HELIX_DIA) em thread_length mm a partir da ponta (0 = rosca total) e chanfros opcionais nas pontas."""
    d = float(int(size))
    P, R, h3, g_R, g_r, per_mm = trapezoid_groove(d, pitch)
    L = _pos("length", length, 4 * P)
    tl = _pos("thread_length", thread_length, 0)
    c = _pos("end_chamfer", end_chamfer, 0)
    if c >= R / 2:
        raise PartError("end_chamfer grande demais para o diametro")
    full = tl <= 0 or tl >= L
    if not full:
        if tl < 2 * P:
            raise PartError(f"thread_length={tl:g} deve ser >= 2 passos ({2 * P:g} mm)")
        if tl > L - g_R / 2 - 0.05:
            raise PartError(f"thread_length={tl:g} deve ser <= {L - g_R / 2 - 0.05:g} ou 0 (rosca total)")
    p = safe_name(name, "fuso")
    ext = min(0.3, 0.15 * P)
    zc0 = -P
    z_end = (L + P) if full else tl
    turns = (z_end - zc0) / P
    gx = ext * _TAN15
    prof = [(R + ext, zc0 - g_R / 2 - gx), (R - h3, zc0 - g_r / 2), (R - h3, zc0 + g_r / 2), (R + ext, zc0 + g_R / 2 + gx)]
    steps = [Step("extrude", p, [("c", 0, 0, R)], 0, L, label="haste")]
    vol = circle_area(R) * L
    if c > 0:
        steps.append(Step("chamfer", p, [("zr", 0.0, R + 0.5, R - 0.5)], dz=c, label="chanfro da ponta"))
        steps.append(Step("chamfer", p, [("zr", L, R + 0.5, R - 0.5)], dz=c, label="chanfro do topo"))
        vol -= 2 * math.pi * (R * c ** 2 - c ** 3 / 3)
    steps.append(Step("helix_cut", p, [("p", prof)], 0.0, P, label=f"rosca Tr{d:g}x{P:g}", axis="y", dz2=turns))
    vol -= per_mm * (L if full else tl)
    return Plan(p, steps, p, ((-R, -R, 0), (R, R, L)), vol, volume_tol=0.04, export_parts=[p],
                notes=f"rosca trapezoidal Tr{d:g}x{P:g} " + ("total" if full else f"em {tl:g} mm a partir da ponta")
                + f"; profundidade {h3:g} mm, flancos a 15 graus")


def worm_shaft(module=2.0, q=10.0, length=80.0, name="semfim") -> Plan:
    """Parafuso sem-fim de uma entrada (perfil axial trapezoidal, angulo de pressao 20 graus): nucleo cilindrico de raio
    rf = d1/2 - 1,2 m (d1 = q m) + filete helicoidal ADICIONADO por HELIX_DIA, passo axial = pi m, ate o raio de topo d1/2 + m."""
    m = _pos("module", module, 0.5)
    qf = _pos("q", q, 6)
    L = _pos("length", length, 1)
    d1 = qf * m
    ra, rf = d1 / 2.0 + m, d1 / 2.0 - 1.2 * m
    px = math.pi * m
    ov = 0.3                                                # a base do filete entra no nucleo para unir os solidos
    wt = px / 2.0 - 2 * m * _TAN20
    wb = px / 2.0 + 2 * 1.2 * m * _TAN20                    # largura na raiz (r = rf)
    wo = px / 2.0 + 2 * (1.2 * m + ov) * _TAN20             # ... na base do perfil (r = rf - ov)
    if wt < 0.2 * m:
        raise PartError("filete pontudo demais (aumente o modulo ou reduza o angulo)")
    if wo > 0.9 * px:
        raise PartError("perfil do filete largo demais para o passo")
    margin = 0.5
    zc0 = wo / 2.0 + margin
    turns = (L - wo - 2 * margin) / px
    if turns < 1.5:
        raise PartError(f"length={L:g} curto demais: o filete precisa de pelo menos 1,5 volta (>= {1.5 * px + wo + 2 * margin:.1f} mm)")
    p = safe_name(name, "semfim")
    prof = [(rf - ov, zc0 - wo / 2.0), (ra, zc0 - wt / 2.0), (ra, zc0 + wt / 2.0), (rf - ov, zc0 + wo / 2.0)]
    h = ra - rf
    area = (wb + wt) / 2.0 * h
    rc = rf + h * (wb + 2 * wt) / (3.0 * (wb + wt))
    vol = circle_area(rf) * L + area * 2.0 * math.pi * rc * turns
    steps = [Step("extrude", p, [("c", 0, 0, rf)], 0, L, label="nucleo"),
             Step("helix_add", p, [("p", prof)], 0.0, px, label=f"filete m{m:g} passo {px:.3f}", axis="y", dz2=turns)]
    return Plan(p, steps, p, ((-ra, -ra, 0), (ra, ra, L)), vol, volume_tol=0.05, export_parts=[p],
                notes=f"sem-fim m{m:g} q{qf:g}: d1={d1:g}, raio de topo {ra:g}, raiz {rf:g}, passo axial {px:.3f}, "
                      f"{turns:.2f} voltas; uma entrada, direita")


def sprocket_outline(teeth: int, pitch: float, roller_d: float, seat_pts: int = 7):
    """Contorno (poligono) de roda dentada para corrente de roletes (ISO 606): assento circular do rolete (raio
    0,505 d1 + 0,069 d1^(1/3), angulo de assento 120 - 90/z graus) e flancos retos ate a ponta do dente.
    Devolve (pontos, info). Dente centrado em +x."""
    z = int(teeth)
    pd = pitch / math.sin(math.pi / z)                          # diametro primitivo
    df = pd - roller_d                                          # diametro de raiz
    da = pd + 1.125 * pitch - pitch * 0.8 / z - roller_d        # media dos limites min/max da ISO 606
    rs = 0.505 * roller_d + 0.069 * roller_d ** (1.0 / 3.0)
    rc = df / 2.0 + rs                                          # raio do centro do assento
    half = math.radians(60.0 - 45.0 / z)                        # meio angulo de assento
    phi = 2 * math.pi / z
    ra = da / 2.0
    pts = []
    for k in range(z):
        c = k * phi
        cx, cy = rc * math.cos(c), rc * math.sin(c)
        # assento: arco em torno da direcao que aponta para o eixo (c + pi), do lado de c-phi/2 ao de c+phi/2
        for i in range(seat_pts):
            a = c + math.pi + half - 2 * half * i / (seat_pts - 1)       # c+pi+half ... c+pi-half (sentido horario)
            pts.append((cx + rs * math.cos(a), cy + rs * math.sin(a)))
        pts.append((ra * math.cos(c + phi / 2.0), ra * math.sin(c + phi / 2.0)))      # ponta do dente
    return pts, {"pitch_d": pd, "root_d": df, "tip_d": da, "seat_r": rs, "seat_center_r": rc}


def chain_sprocket(teeth=19, pitch=12.7, roller_d=7.92, thickness=7.0, bore=16.0, hub_od=0.0, hub_len=15.0,
                   keyway_width=5.0, keyway_depth=2.3, set_screw_d=5.0, name="pinhao") -> Plan:
    """Roda dentada (pinhao) para corrente de roletes ISO 606 (padrao: 08B/ANSI 40, passo 12,7): placa dentada, cubo,
    furo com rasgo de chaveta e furo radial do parafuso de fixacao (plano y)."""
    z = int(_pos("teeth", teeth, 9, 120))
    pitch, roller_d = _pos("pitch", pitch, 4), _pos("roller_d", roller_d, 2)
    t, bore, hub_len = _pos("thickness", thickness, 1), _pos("bore", bore, 0), _pos("hub_len", hub_len, 0)
    if roller_d >= pitch * 0.7:
        raise PartError("roller_d deve ser < 0,7 x passo")
    pts, info = sprocket_outline(z, pitch, roller_d)
    root_r = info["root_d"] / 2.0
    ho = float(hub_od) if hub_od else (min(0.6 * info["root_d"], bore + 22.0) if hub_len > 0 else 0.0)
    if bore / 2.0 >= root_r - 4.0:
        raise PartError(f"furo ({bore:g}) grande demais: diametro de raiz = {info['root_d']:.2f}")
    if hub_len > 0 and (ho < bore + 6.0 or ho / 2.0 > root_r - 1.0):
        raise PartError(f"cubo (hub_od={ho:g}) incompativel: precisa de bore+6 <= hub_od <= {info['root_d'] - 2:.1f}")
    p = safe_name(name, "pinhao")
    rb = bore / 2.0
    total = t + (hub_len if hub_len > 0 else 0.0)
    steps = [Step("extrude", p, [("p", pts)] + ([("c", 0, 0, rb)] if bore > 0 else []), 0, t, label=f"placa dentada z={z}")]
    vol = (poly_area(pts) - circle_area(rb)) * t
    if hub_len > 0:
        steps.append(Step("extrude", p, [("c", 0, 0, ho / 2.0)] + ([("c", 0, 0, rb)] if bore > 0 else []), t, hub_len, label="cubo"))
        vol += (circle_area(ho / 2.0) - circle_area(rb)) * hub_len
    if keyway_width > 0 and keyway_depth > 0 and bore > 0:
        kw = float(keyway_width)
        steps.append(Step("cut", p, [("r", rb - 0.5, -kw / 2, rb + keyway_depth, kw / 2)], 0, total, label="rasgo de chaveta"))
        vol -= (kw * (rb + keyway_depth) - (kw / 2 * math.sqrt(rb * rb - kw * kw / 4) + rb * rb * math.asin(kw / (2 * rb)))) * total
    if set_screw_d > 0 and hub_len > 0 and bore > 0:
        rs_ = float(set_screw_d) / 2.0
        if rs_ * 2 > hub_len - 2:
            raise PartError("set_screw_d grande demais para o cubo")
        y0 = ho / 2.0 + 1.0
        steps.append(Step("cut", p, [("c", 0, t + hub_len / 2.0, rs_)], y0, y0 - (rb - 0.5), "-w", "furo do parafuso de fixacao", axis="y"))
        vol -= math.pi * rs_ * rs_ * (ho / 2.0 - rb)
    x0, y0_, x1, y1 = loops_bbox([("p", pts)])
    return Plan(p, steps, p, ((x0, y0_, 0), (x1, y1, total)), vol, volume_tol=0.02, export_parts=[p],
                notes=f"pinhao z={z} passo {pitch:g} rolete {roller_d:g}: primitivo {info['pitch_d']:.2f}, externo {info['tip_d']:.2f}, "
                      f"raiz {info['root_d']:.2f} mm; cubo Ø{ho:g} x {hub_len:g}")


def bench_vise(jaw_w=60.0, jaw_h=46.0, base_l=140.0, base_w=60.0, base_t=12.0, opening=15.0,
               name="morsa") -> Plan:
    """Morsa de bancada funcional (conjunto de 7 pecas): base fixa com barramento e mandibula traseira,
    mordente movel guiado, 2 placas de mordente postico, fuso de avanco, alavanca com batentes e peca fixada."""
    jw = _pos("jaw_w", jaw_w, 20)
    bl, bw, bt = _pos("base_l", base_l, 40), _pos("base_w", base_w, 20), _pos("base_t", base_t, 4)
    op = _pos("opening", opening, 2)
    p = safe_name(name, "morsa")

    names = {
        "base": f"{p}_base",
        "movel": f"{p}_mordente_movel",
        "p_fixa": f"{p}_placa_fixa",
        "p_movel": f"{p}_placa_movel",
        "peca": f"{p}_peca_fixada",
        "fuso": f"{p}_fuso",
        "alavanca": f"{p}_alavanca",
    }

    hx = bl / 2.0 - 15.0
    hy = bw / 2.0 - 12.0
    holes = [("c", -hx, hy, 4.5), ("c", -hx, -hy, 4.5), ("c", hx, hy, 4.5), ("c", hx, -hy, 4.5)]
    base_plate = [("r", -bl / 2.0, -bw / 2.0, bl / 2.0, bw / 2.0)] + holes
    gw, gh = 28.0, 16.0
    x_jaw_fixed = bl / 4.0
    bar_rect = [("r", -bl / 2.0, -gw / 2.0, x_jaw_fixed, gw / 2.0)]
    z_jaw_top = bt + jaw_h
    fixed_jaw_rect = [("r", x_jaw_fixed, -jw / 2.0, bl / 2.0, jw / 2.0)]

    t_plate = 4.0
    x_pf_back = x_jaw_fixed
    x_pf_front = x_pf_back - t_plate
    x_peca_back = x_pf_front
    x_peca_front = x_peca_back - op
    x_pm_back = x_peca_front
    x_pm_front = x_pm_back - t_plate
    x_mov_front_face = x_pm_front
    mov_len = 27.0
    x_mov_rear = x_mov_front_face - mov_len

    mov_wings = [("r", x_mov_rear, gw / 2.0, x_mov_front_face, jw / 2.0),
                 ("r", x_mov_rear, -jw / 2.0, x_mov_front_face, -gw / 2.0)]
    z_bar_top = bt + gh
    mov_upper = [("r", x_mov_rear, -jw / 2.0, x_mov_front_face, jw / 2.0)]

    z_pl_bottom = z_jaw_top - 18.0
    pl_fixa = [("r", x_pf_front, -jw / 2.0, x_pf_back, jw / 2.0)]
    pl_movel = [("r", x_pm_front, -jw / 2.0, x_pm_back, jw / 2.0)]

    peca_w = jw * 0.6
    z_peca_bottom = z_jaw_top - 22.0
    peca_rect = [("r", x_peca_front, -peca_w / 2.0, x_peca_back, peca_w / 2.0)]

    z_spindle = bt + gh / 2.0
    r_spindle, r_collar, r_head = 6.0, 10.0, 8.5
    x_spindle_start = -bl / 2.0
    x_spindle_end = x_jaw_fixed
    x_collar_start = x_spindle_start - 6.0
    x_head_start = x_collar_start - 20.0
    x_lever = x_head_start + 10.0

    r_lever, lever_len = 2.75, 84.0
    z_lever_start = z_spindle - lever_len / 2.0
    r_knob = 5.5

    steps = [
        # Base
        Step("extrude", names["base"], base_plate, 0.0, bt, label="sapata com 4 furos"),
        Step("extrude", names["base"], bar_rect, bt, gh, label="barramento central"),
        Step("extrude", names["base"], fixed_jaw_rect, bt, jaw_h, label="mandibula fixa"),
        # Mordente movel
        Step("extrude", names["movel"], mov_wings, bt, gh, label="guias deslizantes"),
        Step("extrude", names["movel"], mov_upper, z_bar_top, jaw_h - gh, label="mandibula movel"),
        # Placas de mordente
        Step("extrude", names["p_fixa"], pl_fixa, z_pl_bottom, 18.0, label="placa mordente fixo"),
        Step("extrude", names["p_movel"], pl_movel, z_pl_bottom, 18.0, label="placa mordente movel"),
        # Peca fixada
        Step("extrude", names["peca"], peca_rect, z_peca_bottom, 20.0, label="peca prensada"),
        # Fuso
        Step("extrude", names["fuso"], [("c", 0, z_spindle, r_spindle)], x_spindle_start, x_spindle_end - x_spindle_start, "+w", label="haste do fuso", axis="x"),
        Step("extrude", names["fuso"], [("c", 0, z_spindle, r_collar)], x_collar_start, 6.0, "+w", label="colar batente", axis="x"),
        Step("extrude", names["fuso"], [("c", 0, z_spindle, r_head)], x_head_start, 20.0, "+w", label="cabeca do fuso", axis="x"),
        # Alavanca
        Step("extrude", names["alavanca"], [("c", x_lever, 0, r_lever)], z_lever_start, lever_len, label="barra da alavanca", axis="z"),
        Step("extrude", names["alavanca"], [("c", x_lever, 0, r_knob)], z_lever_start + lever_len, 5.0, label="batente superior", axis="z"),
        Step("extrude", names["alavanca"], [("c", x_lever, 0, r_knob)], z_lever_start - 5.0, 5.0, label="batente inferior", axis="z"),
    ]

    v_base = (bl * bw - 4 * math.pi * 4.5 ** 2) * bt + (x_jaw_fixed - (-bl / 2.0)) * gw * gh + (bl / 2.0 - x_jaw_fixed) * jw * jaw_h
    v_mov = mov_len * (jw - gw) * gh + mov_len * jw * (jaw_h - gh)
    v_pf = t_plate * jw * 18.0
    v_pm = t_plate * jw * 18.0
    v_peca = op * peca_w * 20.0
    v_fuso = math.pi * r_spindle ** 2 * (x_spindle_end - x_spindle_start) + math.pi * r_collar ** 2 * 6.0 + math.pi * r_head ** 2 * 20.0
    v_alav = math.pi * r_lever ** 2 * lever_len + 2 * math.pi * r_knob ** 2 * 5.0
    tot_vol = v_base + v_mov + v_pf + v_pm + v_peca + v_fuso + v_alav

    vols = [
        (names["base"], v_base), (names["movel"], v_mov), (names["p_fixa"], v_pf),
        (names["p_movel"], v_pm), (names["peca"], v_peca), (names["fuso"], v_fuso),
        (names["alavanca"], v_alav)
    ]
    bboxes = {
        names["base"]: ((-bl / 2.0, -bw / 2.0, 0.0), (bl / 2.0, bw / 2.0, z_jaw_top)),
        names["movel"]: ((x_mov_rear, -jw / 2.0, bt), (x_mov_front_face, jw / 2.0, z_jaw_top)),
        names["p_fixa"]: ((x_pf_front, -jw / 2.0, z_pl_bottom), (x_pf_back, jw / 2.0, z_jaw_top)),
        names["p_movel"]: ((x_pm_front, -jw / 2.0, z_pl_bottom), (x_pm_back, jw / 2.0, z_jaw_top)),
        names["peca"]: ((x_peca_front, -peca_w / 2.0, z_peca_bottom), (x_peca_back, peca_w / 2.0, z_peca_bottom + 20.0)),
        names["fuso"]: ((x_head_start, -r_collar, z_spindle - r_collar), (x_spindle_end, r_collar, z_spindle + r_collar)),
        names["alavanca"]: ((x_lever - r_knob, -r_knob, z_lever_start - 5.0), (x_lever + r_knob, r_knob, z_lever_start + lever_len + 5.0)),
    }
    all_parts = list(names.values())
    return Plan(p, steps, names["base"], bboxes[names["base"]], tot_vol, volume_tol=0.03,
                volume_parts=vols, part_bboxes=bboxes, export_parts=all_parts,
                notes=f"morsa de bancada (7 pecas): abertura {op:g} mm, mandíbula {jw:g} mm, base {bl:g}x{bw:g} mm")


CATALOG = {
    "flange": flange, "stepped_shaft": stepped_shaft, "hex_bolt": hex_bolt, "hex_nut": hex_nut,
    "spur_gear": spur_gear, "i_beam": i_beam, "angle_bar": angle_bar, "mounting_plate": mounting_plate,
    "heat_sink": heat_sink, "roller_bearing": roller_bearing,
    "rounded_plate": rounded_plate, "l_bracket": l_bracket, "pipe_tee": pipe_tee,
    "bearing_housing": bearing_housing, "tslot_profile": tslot_profile, "machined_block": machined_block,
    "manifold_block": manifold_block, "socket_head_screw": socket_head_screw, "bolted_joint": bolted_joint, "flanged_joint": flanged_joint, "enclosure": enclosure,
    "compression_spring": compression_spring, "threaded_rod": threaded_rod,
    "lead_screw": lead_screw, "worm_shaft": worm_shaft, "chain_sprocket": chain_sprocket,
    "bench_vise": bench_vise,
}


def build(kind: str, params: dict | None = None) -> Plan:
    """Cria o Plan de um item do catalogo a partir de parametros (dict)."""
    if kind not in CATALOG:
        raise PartError(f"tipo desconhecido '{kind}'. Disponiveis: {', '.join(sorted(CATALOG))}")
    params = dict(params or {})
    fn = CATALOG[kind]
    import inspect

    allowed = set(inspect.signature(fn).parameters)
    bad = sorted(set(params) - allowed)
    if bad:
        raise PartError(f"parametros desconhecidos para {kind}: {bad}. Validos: {sorted(allowed)}")
    return fn(**params)


def catalog_help() -> dict:
    import inspect

    out = {}
    for k, fn in CATALOG.items():
        sig = inspect.signature(fn)
        out[k] = {"descricao": (fn.__doc__ or "").strip().split("\n")[0],
                  "parametros": {n: p.default for n, p in sig.parameters.items()}}
    return out


def check_result(plan: Plan, measured_bbox, measured_volume, bbox_tol: float = 0.05) -> dict:
    """Compara bbox/volume medidos com os esperados; devolve {ok, bbox_ok, volume_ok, ...}."""
    res = {"ok": True}
    if plan.expected_bbox and measured_bbox:
        lo_e, hi_e = plan.expected_bbox
        lo_m, hi_m = measured_bbox
        diffs = [abs(a - b) for a, b in zip(list(lo_e) + list(hi_e), list(lo_m) + list(hi_m))]
        res["bbox_max_diff_mm"] = round(max(diffs), 4)
        res["bbox_ok"] = max(diffs) <= bbox_tol
        res["ok"] &= res["bbox_ok"]
    if plan.expected_volume and measured_volume:
        err = (measured_volume - plan.expected_volume) / plan.expected_volume
        res["volume_error_pct"] = round(100 * err, 3)
        res["volume_ok"] = abs(err) <= plan.volume_tol
        res["ok"] &= res["volume_ok"]
    return res
