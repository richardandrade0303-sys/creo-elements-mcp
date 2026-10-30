"""Ferragens e componentes normalizados: parafuso+arruela+porca, rolamentos 60xx/62xx/63xx e chavetas DIN 6885.

Tudo e construido no referencial local (eixo da peca em +z, origem na face inicial) e levado ao lugar por `place.orient`.
Os dimensionais vem das normas (ISO 4017/4014, ISO 4762, ISO 7089, ISO 4032, ISO 15, DIN 6885-1); o que e simplificacao
(rolamento sem esferas, sem roscas, sem raios de concordancia) vem declarado em `notes`.
"""

from __future__ import annotations

import math

from . import parts
from .parts import (COARSE_PITCH, HEX_TABLE, SOCKET_TABLE, WASHER_TABLE, PartError, Plan, Step, _pos, circle_area,
                    safe_name)
from .place import Built, merge_built, norm_axis, orient

STEEL_G_MM3 = 7.85e-3
STD_LENGTHS = [6, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160,
               180, 200, 220, 240, 260, 280, 300]


def mass_g(volume_mm3: float, density_g_mm3: float = STEEL_G_MM3) -> float:
    return round(volume_mm3 * density_g_mm3, 2)


def _std_length(required: float) -> float:
    for v in STD_LENGTHS:
        if v >= required - 1e-9:
            return float(v)
    return float(math.ceil(required / 10.0) * 10)


def _bom(item: str, category: str, qty: int, part_names: list, unit_volume: float, material: str = "Aço carbono",
         note: str = "") -> dict:
    return {"item": item, "category": category, "qty": qty, "parts": list(part_names),
            "unit_mass_g": mass_g(unit_volume), "material": material, "note": note}


# ------------------------------------------------------------------ arruela
def washer(size=12, name="arruela") -> Plan:
    """Arruela lisa ISO 7089 (serie normal)."""
    if int(size) not in WASHER_TABLE:
        raise PartError(f"M{size} fora da tabela de arruelas: {sorted(WASHER_TABLE)}")
    w_id, w_od, t = WASHER_TABLE[int(size)]
    p = safe_name(name, "arruela")
    box = ((-w_od / 2, -w_od / 2, 0.0), (w_od / 2, w_od / 2, t))
    plan = Plan(p, [Step("extrude", p, [("c", 0, 0, w_od / 2), ("c", 0, 0, w_id / 2)], 0, t, label="arruela")], p, box,
                (circle_area(w_od / 2) - circle_area(w_id / 2)) * t, volume_tol=0.03, export_parts=[p])
    plan.part_bboxes = {p: box}
    return plan


# ------------------------------------------------------------------ conjunto de fixacao
def fastener_stack(size=12, grip=20.0, length=0.0, head="hex", washer_top=True, washer_bottom=True, nut=None,
                   name="fix", at=(0.0, 0.0), axis="z", origin=(0.0, 0.0, 0.0), engagement=0.0) -> Built:
    """Um parafuso com arruelas e porca atravessando um pacote de espessura `grip`.

    Referencial local: z=0 e a face de baixo do pacote (lado da porca), z=grip a face de cima (lado da cabeca); (x, y)=`at`.
    head="hex" (ISO 4017/4014; com porca) | "socket" (ISO 4762; sem porca por padrao: rosca na peca de baixo, com
    `engagement` mm, padrao 1,5 d). `length=0` escolhe o menor comprimento normalizado que cobre o pacote com 2 passos de
    rosca sobrando; informado, e validado. O conjunto e levado a `axis`/`origin` (cabeca para o sentido do eixo)."""
    head = str(head).lower()
    if head not in ("hex", "socket"):
        raise PartError("head deve ser 'hex' ou 'socket'")
    sz = int(size)
    table = HEX_TABLE if head == "hex" else SOCKET_TABLE
    if sz not in table or sz not in WASHER_TABLE or sz not in COARSE_PITCH:
        raise PartError(f"M{sz} nao suportado para head={head}: " + str(sorted(set(table) & set(WASHER_TABLE))))
    grip = _pos("grip", grip, 1.0)
    use_nut = (head == "hex") if nut is None else bool(nut)
    P = COARSE_PITCH[sz]
    w_id, w_od, wt = WASHER_TABLE[sz]
    p = safe_name(name, "fix")
    zh = grip + (wt if washer_top else 0.0)                    # face de apoio da cabeca
    zn_top = -(wt if washer_bottom and use_nut else 0.0)       # topo da porca
    if use_nut:
        m = HEX_TABLE[sz][2]
        min_len = zh - (zn_top - m - 2 * P)
    else:
        eng = float(engagement) if engagement else 1.5 * sz
        min_len = zh + eng
    L = float(length) if length else _std_length(min_len)
    if L + 1e-9 < min_len:
        raise PartError(f"comprimento {L:g} curto demais: o pacote de {grip:g} mm com as arruelas exige >= {min_len:.1f} mm")
    pos = (float(at[0]), float(at[1]))
    comp: list = []
    bom_lines: list = []
    if head == "hex":
        bolt = parts.hex_bolt(sz, L, f"{p}_parafuso")
        spec = f"ISO 4017/4014 M{sz}x{L:g}"
        k_head = HEX_TABLE[sz][1]
    else:
        bolt = parts.socket_head_screw(sz, L, tip_chamfer=0.0, head_chamfer=0.0, name=f"{p}_parafuso")
        spec = f"ISO 4762 M{sz}x{L:g}"
        k_head = SOCKET_TABLE[sz][1]
    comp.append(orient(bolt, "z", (pos[0], pos[1], zh - L)))
    bom_lines.append(_bom(spec + " cl. 8.8", "parafuso", 1, [f"{p}_parafuso"], bolt.expected_volume))
    if washer_top:
        w = washer(sz, f"{p}_arruela_sup")
        comp.append(orient(w, "z", (pos[0], pos[1], grip)))
        bom_lines.append(_bom(f"ISO 7089 M{sz}", "arruela", 1, [f"{p}_arruela_sup"], w.expected_volume))
    if use_nut:
        if washer_bottom:
            w2 = washer(sz, f"{p}_arruela_inf")
            comp.append(orient(w2, "z", (pos[0], pos[1], -wt)))
            bom_lines.append(_bom(f"ISO 7089 M{sz}", "arruela", 1, [f"{p}_arruela_inf"], w2.expected_volume))
        nt = parts.hex_nut(sz, f"{p}_porca")
        comp.append(orient(nt, "z", (pos[0], pos[1], zn_top - HEX_TABLE[sz][2])))
        bom_lines.append(_bom(f"ISO 4032 M{sz}", "porca", 1, [f"{p}_porca"], nt.expected_volume))
    # junta as pecas deste conjunto (cada Plan ja traz o proprio bbox), depois leva ao eixo/origem pedidos
    sub = [Built(c, []) for c in comp]
    merged = merge_built(p, sub, notes=f"{spec}: pacote {grip:g} mm; rosca nao modelada; cabeca em z={zh:g}"
                                       + (f", porca em z={zn_top - HEX_TABLE[sz][2]:g}..{zn_top:g}" if use_nut else ""))
    plan = orient(merged.plan, axis, origin, name=p)
    info = {"size": f"M{sz}", "length": L, "min_length": round(min_len, 2), "grip": grip, "pitch": P,
            "thread_protrusion": round((zn_top - HEX_TABLE[sz][2]) - (zh - L), 2) if use_nut else None,
            "head_height": k_head, "axis": norm_axis(axis), "origin": list(origin)}
    return Built(plan, bom_lines, info)


def fastener_layout(positions: list, **kw) -> Built:
    """Varios conjuntos iguais nas posicoes (x, y) do plano local; nomes `<name>1`, `<name>2`..."""
    if not positions:
        raise PartError("informe ao menos uma posicao")
    from .scene import part_limit
    if part_limit() and len(positions) > part_limit():
        raise PartError(f"mais de {part_limit()} conjuntos: acima do limite de pecas configurado")
    base = safe_name(kw.pop("name", "fix"), "fix")
    axis, origin = kw.pop("axis", "z"), kw.pop("origin", (0.0, 0.0, 0.0))
    built = []
    for i, pt in enumerate(positions, 1):
        built.append(fastener_stack(name=f"{base}{i}" if len(positions) > 1 else base, at=(pt[0], pt[1]), axis="z",
                                    origin=(0.0, 0.0, 0.0), **kw))
    merged = merge_built(base, built, notes=built[0].plan.notes + f" (x{len(built)})")
    plan = orient(merged.plan, axis, origin, name=base)
    # BOM agregada por item
    agg: dict = {}
    for b in built:
        for line in b.bom:
            cur = agg.setdefault(line["item"], dict(line, qty=0, parts=[]))
            cur["qty"] += line["qty"]
            cur["parts"] += line["parts"]
    return Built(plan, list(agg.values()), dict(built[0].info, positions=len(built), axis=norm_axis(axis), origin=list(origin)))


# ------------------------------------------------------------------ rolamentos rigidos de esferas (ISO 15)
# designacao -> (d furo, D externo, B largura) em mm
BEARINGS = {
    "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9), "6003": (17, 35, 10), "6004": (20, 42, 12),
    "6005": (25, 47, 12), "6006": (30, 55, 13), "6007": (35, 62, 14), "6008": (40, 68, 15), "6009": (45, 75, 16),
    "6010": (50, 80, 16), "6011": (55, 90, 18), "6012": (60, 95, 18), "6013": (65, 100, 18), "6014": (70, 110, 20),
    "6015": (75, 115, 20), "6016": (80, 125, 22),
    "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11), "6203": (17, 40, 12), "6204": (20, 47, 14),
    "6205": (25, 52, 15), "6206": (30, 62, 16), "6207": (35, 72, 17), "6208": (40, 80, 18), "6209": (45, 85, 19),
    "6210": (50, 90, 20), "6211": (55, 100, 21), "6212": (60, 110, 22), "6213": (65, 120, 23), "6214": (70, 125, 24),
    "6215": (75, 130, 25), "6216": (80, 140, 26),
    "6300": (10, 35, 11), "6301": (12, 37, 12), "6302": (15, 42, 13), "6303": (17, 47, 14), "6304": (20, 52, 15),
    "6305": (25, 62, 17), "6306": (30, 72, 19), "6307": (35, 80, 21), "6308": (40, 90, 23), "6309": (45, 100, 25),
    "6310": (50, 110, 27), "6311": (55, 120, 29), "6312": (60, 130, 31),
}


def bearing_dims(designation: str) -> tuple:
    key = str(designation).strip().upper().replace("-", "").replace(" ", "")
    for suffix in ("2RS", "2RSH", "ZZ", "2Z", "RS", "Z"):
        if key.endswith(suffix) and key[: -len(suffix)] in BEARINGS:
            key = key[: -len(suffix)]
            break
    if key not in BEARINGS:
        raise PartError(f"rolamento {designation!r} fora da tabela. Disponiveis: 60xx (6000-6016), 62xx (6200-6216), "
                        "63xx (6300-6312)")
    return key, BEARINGS[key]


def bearing(designation="6205", mode="body", rolling=0, name="rolamento", axis="z", origin=(0.0, 0.0, 0.0)) -> Built:
    """Rolamento rigido de esferas (serie 60/62/63). mode: "body" = UMA peca (anel cheio, envelope) | "rings" = anel interno +
    anel externo (2 pecas, com o vao das esferas) | "full" = aneis + `rolling` esferas aproximadas por cilindros (N+2 pecas;
    padrao de contagem 1,7 diametros de esfera de passo). Eixo +z, face inicial em z=0, centro na origem."""
    mode = str(mode).lower()
    if mode not in ("body", "rings", "full"):
        raise PartError("mode deve ser body, rings ou full")
    key, (d, D, B) = bearing_dims(designation)
    p = safe_name(name, "rolamento")
    dm = (D + d) / 2.0
    dw = 0.29 * (D - d)
    d1, D1 = dm - 0.9 * dw, dm + 0.9 * dw                        # ombros dos aneis (referencia)
    steps: list = []
    bb: dict = {}
    vols: list = []
    export: list = []
    if mode == "body":
        steps.append(Step("extrude", p, [("c", 0, 0, D / 2), ("c", 0, 0, d / 2)], 0, B, label="rolamento (anel cheio)"))
        bb[p] = ((-D / 2, -D / 2, 0.0), (D / 2, D / 2, float(B)))
        vols.append((p, (circle_area(D / 2) - circle_area(d / 2)) * B))
        export = [p]
        names = [p]
        total_vol = vols[0][1]
    else:
        if d1 - d < 2.0 or D - D1 < 2.0:
            raise PartError("rolamento pequeno demais para modelar os aneis separados; use mode='body'")
        pi_, po_ = f"{p}_interno", f"{p}_externo"
        steps.append(Step("extrude", pi_, [("c", 0, 0, d1 / 2), ("c", 0, 0, d / 2)], 0, B, label="anel interno"))
        steps.append(Step("extrude", po_, [("c", 0, 0, D / 2), ("c", 0, 0, D1 / 2)], 0, B, label="anel externo"))
        bb[pi_] = ((-d1 / 2, -d1 / 2, 0.0), (d1 / 2, d1 / 2, float(B)))
        bb[po_] = ((-D / 2, -D / 2, 0.0), (D / 2, D / 2, float(B)))
        vols += [(pi_, (circle_area(d1 / 2) - circle_area(d / 2)) * B), (po_, (circle_area(D / 2) - circle_area(D1 / 2)) * B)]
        names = [pi_, po_]
        export = [pi_, po_]
        total_vol = sum(v for _n, v in vols)
        if mode == "full":
            n = int(rolling) if rolling else int(round(math.pi * dm / (dw * 1.7)))
            if n < 3 or n > 30:
                raise PartError("rolling deve estar entre 3 e 30")
            if dw > B - 0.5:
                raise PartError("esfera maior que a largura do rolamento")
            for i in range(n):
                ang = 2 * math.pi * i / n
                cx, cy = dm / 2 * math.cos(ang), dm / 2 * math.sin(ang)
                bn = f"{p}_esfera{i + 1}"
                z0 = B / 2.0 - dw / 2.0
                steps.append(Step("extrude", bn, [("c", cx, cy, dw / 2)], z0, dw, label=f"esfera {i + 1}"))
                bb[bn] = ((cx - dw / 2, cy - dw / 2, z0), (cx + dw / 2, cy + dw / 2, z0 + dw))
                names.append(bn)
                export.append(bn)
            ball_vol = circle_area(dw / 2) * dw
            vols.append((f"{p}_esfera1", ball_vol))
            total_vol += ball_vol * n
    main = names[0]
    plan = Plan(p, steps, main, bb[main], total_vol, volume_tol=0.03, export_parts=export, volume_parts=vols,
                part_bboxes=bb,
                notes=f"rolamento {key} ({d}x{D}x{B}): " + {"body": "anel cheio (envelope)", "rings": "2 aneis, sem esferas",
                                                          "full": "2 aneis + esferas aproximadas por cilindros"}[mode])
    oriented = orient(plan, axis, origin, name=p)
    bom = [_bom(f"Rolamento ISO 15 {key} ({d}x{D}x{B})", "rolamento", 1, names, total_vol, "Aço cromo (52100)",
                "modelo simplificado" if mode != "full" else "esferas aproximadas por cilindros")]
    info = {"designation": key, "bore": d, "outer": D, "width": B, "pitch_diameter": round(dm, 2),
            "ball_diameter_ref": round(dw, 2), "mode": mode, "shaft_fit": _shaft_fit_hint(d), "housing_fit": "H7 (anel externo fixo) / J7"}
    return Built(oriented, bom, info)


def _shaft_fit_hint(d: float) -> str:
    return "j5/k5 (carga normal; eixo giratorio)" if d <= 100 else "m5/m6 (carga pesada)"


# ------------------------------------------------------------------ chavetas paralelas DIN 6885-1
# (d acima de, ate, b, h, t1 no eixo, t2 no cubo)
KEYS = [(6, 8, 2, 2, 1.2, 1.0), (8, 10, 3, 3, 1.8, 1.4), (10, 12, 4, 4, 2.5, 1.8), (12, 17, 5, 5, 3.0, 2.3),
        (17, 22, 6, 6, 3.5, 2.8), (22, 30, 8, 7, 4.0, 3.3), (30, 38, 10, 8, 5.0, 3.3), (38, 44, 12, 8, 5.0, 3.3),
        (44, 50, 14, 9, 5.5, 3.8), (50, 58, 16, 10, 6.0, 4.3), (58, 65, 18, 11, 7.0, 4.4), (65, 75, 20, 12, 7.5, 4.9),
        (75, 85, 22, 14, 9.0, 5.4), (85, 95, 25, 14, 9.0, 5.4), (95, 110, 28, 16, 10.0, 6.4), (110, 130, 32, 18, 11.0, 7.4)]
KEY_LENGTHS = [6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 50, 56, 63, 70, 80, 90, 100, 110, 125, 140, 160,
               180, 200, 220, 250, 280, 320, 360, 400]


def key_for(shaft_d: float) -> dict:
    d = float(shaft_d)
    for lo, hi, b, h, t1, t2 in KEYS:
        if lo < d <= hi:
            return {"b": float(b), "h": float(h), "t1": t1, "t2": t2, "range": f"{lo}-{hi}"}
    raise PartError(f"diametro de eixo {shaft_d:g} fora da DIN 6885-1 (acima de 6 ate 130 mm)")


def _key_slot(cz: float, length: float, b: float, rounded: bool):
    if rounded:
        return parts.slot(0.0, cz, length, b, horizontal=False)
    return ("r", -b / 2.0, cz - length / 2.0, b / 2.0, cz + length / 2.0)


def keyway_volume(radius: float, width: float, length: float, y_bottom: float, rounded: bool, n: int = 400) -> float:
    """Volume (mm3) removido de um eixo cilindrico de raio `radius` por um rasgo de chaveta de largura `width` e comprimento
    `length` (extremos arredondados ou retos) cujo fundo esta em y=`y_bottom` (medido do centro). Integracao numerica."""
    hw = width / 2.0
    total = 0.0
    for i in range(n):
        x = -hw + (i + 0.5) * width / n
        depth = math.sqrt(max(radius * radius - x * x, 0.0)) - y_bottom
        if depth <= 0:
            continue
        run = (length - width) + 2.0 * math.sqrt(max(hw * hw - x * x, 0.0)) if rounded else length
        total += depth * run * (width / n)
    return total


def shaft_keyway_steps(part: str, shaft_d: float, z0: float, length: float, key: dict | None = None, rounded: bool = True):
    """Rasgo de chaveta no eixo `part` (eixo +z, centro na origem), de z0 ate z0+length, no lado +y. -> (Step, volume)."""
    k = key or key_for(shaft_d)
    r = shaft_d / 2.0
    if length < k["b"] * 1.5:
        raise PartError(f"rasgo de {length:g} mm curto demais para chaveta {k['b']:g}x{k['h']:g}")
    st = Step("cut", part, [_key_slot(z0 + length / 2.0, length, k["b"], rounded)], r + 1.0, k["t1"] + 1.0, "-w",
              f"rasgo de chaveta {k['b']:g}x{k['h']:g}", axis="y")
    return st, keyway_volume(r, k["b"], length, r - k["t1"], rounded)


def hub_keyway_step(part: str, bore_d: float, z0: float, length: float, key: dict | None = None, rounded: bool = False):
    """Rasgo de chaveta no cubo `part` com furo de diametro `bore_d` (eixo +z), lado +y. -> (Step, volume removido)."""
    k = key or key_for(bore_d)
    r = bore_d / 2.0
    # volume = prisma acima do furo: do raio da superficie do furo ate r + t2
    hw = k["b"] / 2.0
    n = 200
    vol = 0.0
    for i in range(n):
        x = -hw + (i + 0.5) * k["b"] / n
        base = math.sqrt(max(r * r - x * x, 0.0))
        run = (length - k["b"]) + 2.0 * math.sqrt(max(hw * hw - x * x, 0.0)) if rounded else length
        vol += max(r + k["t2"] - base, 0.0) * run * (k["b"] / n)
    st = Step("cut", part, [_key_slot(z0 + length / 2.0, length, k["b"], rounded)], r - 0.5, k["t2"] + 0.5, "+w",
              f"rasgo de chaveta do cubo {k['b']:g}", axis="y")
    return st, vol


def key_part(shaft_d: float, length: float, form: str = "A", name: str = "chaveta", axis="z",
             origin=(0.0, 0.0, 0.0)) -> Built:
    """Chaveta paralela DIN 6885-1 forma A (extremos arredondados) ou B (retos) para o eixo de `shaft_d` mm. A chaveta fica
    sobre o eixo (eixo da peca = +z; assento no lado +y), de z=0 a z=length."""
    k = key_for(shaft_d)
    form = str(form).upper()
    if form not in ("A", "B"):
        raise PartError("form deve ser A (extremos arredondados) ou B (retos)")
    length = _pos("length", length, k["b"] * 1.5)
    p = safe_name(name, "chaveta")
    y0 = shaft_d / 2.0 - k["t1"]
    rounded = form == "A"
    area = k["b"] * length - ((k["b"] ** 2) * (1 - math.pi / 4) if rounded else 0.0)
    box = ((-k["b"] / 2, y0, 0.0), (k["b"] / 2, y0 + k["h"], float(length)))
    plan = Plan(p, [Step("extrude", p, [_key_slot(length / 2.0, length, k["b"], rounded)], y0, k["h"], "+w",
                         f"chaveta {form} {k['b']:g}x{k['h']:g}x{length:g}", axis="y")],
                p, box, area * k["h"], volume_tol=0.03, export_parts=[p],
                notes=f"chaveta DIN 6885-1 {form} {k['b']:g}x{k['h']:g}x{length:g} para eixo Ø{shaft_d:g} (t1={k['t1']}, t2={k['t2']})")
    plan.part_bboxes = {p: box}
    std = length in KEY_LENGTHS
    bom = [_bom(f"Chaveta DIN 6885-1 {form} {k['b']:g}x{k['h']:g}x{length:g}", "chaveta", 1, [p], area * k["h"], "Aço C45",
                "" if std else "comprimento nao normalizado")]
    return Built(orient(plan, axis, origin, name=p), bom,
                 dict(k, length=length, standard_length=std, shaft_diameter=shaft_d, form=form))
