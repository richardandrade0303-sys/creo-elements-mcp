"""Transmissao de potencia: polias em V (perfis SPZ/SPA/SPB/SPC), polias sincronizadoras (GT2/GT3/HTD) e acoplamentos
(luva rigida, flangeado e de garras com elastomero).

Sem REVOLVE no Express 9.0, o canal em V e aproximado por degraus: o raio entre a raiz do canal e o diametro externo e
dividido em camadas e, em cada camada, o material fica fora da largura do canal naquele raio. O volume esperado e o do
proprio degrau (o que sera construido), e o contorno do canal segue a norma de forma aproximada: valores de referencia
(ISO 4183 / DIN 2215 / catalogos de correias); confira com o fornecedor da correia antes de fabricar.
"""

from __future__ import annotations

import math

from .hardware import KEYS, _bom, fastener_layout, hub_keyway_step, key_for, mass_g
from .parts import (WASHER_TABLE, PartError, Plan, Step, _pos, bolt_circle, circle_area, loops_bbox, poly_area,
                    safe_name)
from .place import Built, merge_built, orient

# perfil: (largura de referencia wd, passo entre canais e, distancia da borda f, altura acima do primitivo ha,
#          profundidade abaixo do primitivo hf, diametro primitivo minimo, diametro de troca 34->38 graus)
VGROOVES = {
    "SPZ": (8.5, 12.0, 8.0, 2.0, 11.0, 63.0, 80.0),
    "SPA": (11.0, 15.0, 10.0, 2.75, 13.75, 90.0, 118.0),
    "SPB": (14.0, 19.0, 12.5, 3.5, 17.5, 140.0, 190.0),
    "SPC": (19.0, 25.5, 17.0, 4.8, 23.8, 224.0, 315.0),
}


def _hub_defaults(bore: float, r_root: float, hub_od: float):
    ho = float(hub_od) if hub_od else min(max(1.8 * bore, bore + 16.0), 2 * r_root - 8.0)
    return ho


def v_pulley(profile="SPA", pitch_d=140.0, grooves=2, bore=28.0, hub_len=0.0, hub_od=0.0, keyway=True, layers=4,
             name="polia_v", axis="z", origin=(0.0, 0.0, 0.0)) -> Built:
    """Polia em V de `grooves` canais (perfil SPZ/SPA/SPB/SPC) com diametro primitivo `pitch_d`, furo `bore` e rasgo de chaveta
    DIN 6885 (se `keyway` e o furo for de 6 a 130 mm). Eixo +z; canais de z=0 a z=W; cubo opcional (hub_len > W) para -z."""
    prof = str(profile).upper()
    if prof not in VGROOVES:
        raise PartError(f"perfil {profile!r} desconhecido: {sorted(VGROOVES)}")
    wd, e, f, ha, hf, dp_min, dp_sw = VGROOVES[prof]
    dp = _pos("pitch_d", pitch_d, dp_min)
    n = int(_pos("grooves", grooves, 1, 8))
    bore = _pos("bore", bore, 4)
    layers = int(_pos("layers", layers, 2, 8))
    phi = 34.0 if dp <= dp_sw else 38.0
    tan_h = math.tan(math.radians(phi / 2.0))
    rp = dp / 2.0
    ro, r_root, rb = rp + ha, rp - hf, bore / 2.0
    if r_root - rb < 6.0:
        raise PartError(f"furo Ø{bore:g} grande demais para a polia {prof} Ø{dp:g} (raiz do canal em Ø{2 * r_root:g}): "
                        "aumente o diametro primitivo")
    W = 2 * f + (n - 1) * e
    hub_ext = max(float(hub_len) - W, 0.0)
    ho = _hub_defaults(bore, r_root, hub_od)
    if hub_ext > 0 and (ho < bore + 8.0 or ho / 2.0 > r_root - 1.0):
        raise PartError(f"cubo Ø{ho:g} incompativel (precisa de {bore + 8:g} <= Ø <= {2 * r_root - 2:g})")
    p = safe_name(name, "polia_v")
    steps = [Step("extrude", p, [("c", 0, 0, r_root), ("c", 0, 0, rb)], 0.0, W, label="corpo (raiz do canal)")]
    vol = (circle_area(r_root) - circle_area(rb)) * W
    if hub_ext > 0:
        steps.append(Step("extrude", p, [("c", 0, 0, ho / 2.0), ("c", 0, 0, rb)], -hub_ext, hub_ext, label="cubo"))
        vol += (circle_area(ho / 2.0) - circle_area(rb)) * hub_ext
    radii = [r_root + k * (ro - r_root) / layers for k in range(layers + 1)]
    centers = [f + i * e for i in range(n)]
    layer_steps = []
    for k in range(layers):
        rm = (radii[k] + radii[k + 1]) / 2.0
        hw = max(wd / 2.0 + (rm - rp) * tan_h, 0.3)
        edges = [0.0]
        for c in centers:
            edges += [c - hw, c + hw]
        edges.append(W)
        for a, b in zip(edges[0::2], edges[1::2]):
            if b - a > 0.05:
                layer_steps.append(Step("extrude", p, [("c", 0, 0, radii[k + 1]), ("c", 0, 0, radii[k] - 0.3)], a, b - a,
                                        label=f"camada {k + 1} de {layers}"))
                vol += (circle_area(radii[k + 1]) - circle_area(radii[k])) * (b - a)
    layer_steps.sort(key=lambda s: s.z0)
    steps += layer_steps
    if keyway and 6 < bore <= 130:
        k_ = key_for(bore)
        if ho / 2.0 < rb + k_["t2"] + 1.0 and hub_ext > 0:
            raise PartError("cubo fino demais para o rasgo de chaveta")
        st, kv = hub_keyway_step(p, bore, -hub_ext, W + hub_ext, k_, rounded=False)
        steps.append(st)
        vol -= kv
    box = ((-ro, -ro, -hub_ext), (ro, ro, W))
    plan = Plan(p, steps, p, box, vol, volume_tol=0.04, export_parts=[p], part_bboxes={p: box},
                notes=f"polia em V {prof} x{n}: Ø primitivo {dp:g}, Ø externo {2 * ro:g}, canal {phi:g} graus por degraus "
                      f"({layers} camadas), largura {W:g} mm; disco maciço; valores de referência (ISO 4183)")
    info = {"profile": prof, "pitch_diameter": dp, "outer_diameter": round(2 * ro, 2), "root_diameter": round(2 * r_root, 2),
            "groove_angle": phi, "width": W, "grooves": n, "groove_pitch": e, "bore": bore, "mass_kg_steel": round(vol * 7.85e-6, 2),
            "hub_extension": hub_ext}
    bom = [_bom(f"Polia em V {prof} x{n} Ø{dp:g} furo Ø{bore:g}", "polia", 1, [p], vol, "Ferro fundido / aço",
                "modelo por degraus; massa em aço")]
    return Built(orient(plan, axis, origin, name=p), bom, info)


# ------------------------------------------------------------------ polias sincronizadoras
# perfil: (passo p, altura do dente h, diferenca da linha primitiva PLD, largura do vao no diametro externo / p)
TIMING = {"GT2": (2.0, 0.75, 0.254, 0.60), "GT3": (3.0, 1.14, 0.381, 0.60), "HTD3M": (3.0, 1.2, 0.381, 0.58),
          "HTD5M": (5.0, 2.06, 0.5715, 0.60), "HTD8M": (8.0, 3.4, 0.686, 0.60)}


def timing_outline(belt: str, teeth: int):
    """Contorno (poligono) da polia sincronizadora e dados: devolve (pontos, info)."""
    b = str(belt).upper()
    if b not in TIMING:
        raise PartError(f"correia {belt!r} desconhecida: {sorted(TIMING)}")
    p, h, pld, wfrac = TIMING[b]
    z = int(teeth)
    if z < 10 or z > 80:
        raise PartError("teeth deve estar entre 10 e 80")
    od = z * p / math.pi - 2 * pld
    ro = od / 2.0
    if h >= ro * 0.7:
        raise PartError(f"{z} dentes e pouco para a correia {b} (dente alto demais para o diametro)")
    a_out = wfrac * p / 2.0 / ro
    a_in = 0.25 * p / 2.0 / (ro - h)
    pts = []
    for i in range(z):
        th = 2 * math.pi * i / z
        for r, a in ((ro, -a_out), (ro - h, -a_in), (ro - h, a_in), (ro, a_out)):
            pts.append((r * math.cos(th + a), r * math.sin(th + a)))
    return pts, {"belt": b, "pitch": p, "outer_diameter": round(od, 3), "root_diameter": round(od - 2 * h, 3),
                 "pitch_diameter": round(z * p / math.pi, 3), "tooth_height": h}


def timing_pulley(belt="GT2", teeth=20, belt_width=6.0, bore=6.0, flanges=True, hub_len=0.0, hub_od=0.0, set_screw_d=0.0,
                  keyway=False, name="polia_sincrona", axis="z", origin=(0.0, 0.0, 0.0)) -> Built:
    """Polia sincronizadora (GT2/GT3/HTD) de `teeth` dentes para correia de `belt_width` mm: dentes de z=ft a z=ft+bw+2, flanges
    de 1 mm (opcional) e cubo no lado +z (hub_len, padrao 8 mm quando cabe) com parafuso de fixacao radial opcional."""
    pts, meta = timing_outline(belt, teeth)
    p_, h_ = meta["pitch"], meta["tooth_height"]
    bw = _pos("belt_width", belt_width, 2)
    bore = _pos("bore", bore, 2)
    rb = bore / 2.0
    root_r = meta["root_diameter"] / 2.0
    if rb > root_r - 1.5:
        raise PartError(f"furo Ø{bore:g} grande demais: raiz em Ø{meta['root_diameter']:g}")
    ft = 1.0 if flanges else 0.0
    tw = bw + 2.0
    ro = meta["outer_diameter"] / 2.0
    p = safe_name(name, "polia_sincrona")
    ho = float(hub_od) if hub_od else min(root_r * 2 - 2.0, bore + 8.0)
    hl = float(hub_len) if hub_len else (8.0 if ho >= bore + 4.0 else 0.0)
    if hl > 0 and ho < bore + 4.0:
        raise PartError("cubo fino demais: hub_od >= bore + 4")
    steps = [Step("extrude", p, [("p", pts), ("c", 0, 0, rb)], ft, tw, label=f"dentes {meta['belt']} z={teeth}")]
    vol = (poly_area(pts) - circle_area(rb)) * tw
    rf = ro + max(2.0, 1.2 * h_ + 1.5)
    z_top = ft + tw + ft
    if flanges:
        steps.append(Step("extrude", p, [("c", 0, 0, rf), ("c", 0, 0, rb)], 0.0, ft, label="flange inferior"))
        steps.append(Step("extrude", p, [("c", 0, 0, rf), ("c", 0, 0, rb)], ft + tw, ft, label="flange superior"))
        vol += 2 * (circle_area(rf) - circle_area(rb)) * ft
    if hl > 0:
        steps.append(Step("extrude", p, [("c", 0, 0, ho / 2.0), ("c", 0, 0, rb)], z_top, hl, label="cubo"))
        vol += (circle_area(ho / 2.0) - circle_area(rb)) * hl
    total = z_top + hl
    if keyway and 6 < bore <= 130:
        st, kv = hub_keyway_step(p, bore, 0.0, total, key_for(bore), rounded=False)
        steps.append(st)
        vol -= kv
    if set_screw_d and hl > 0:
        sr = float(set_screw_d) / 2.0
        if 2 * sr > hl - 1.5:
            raise PartError("set_screw_d grande demais para o cubo")
        y0 = ho / 2.0 + 1.0
        steps.append(Step("cut", p, [("c", 0, z_top + hl / 2.0, sr)], y0, y0 - (rb - 0.5), "-w", "furo do parafuso de fixacao",
                          axis="y"))
        vol -= math.pi * sr * sr * (ho / 2.0 - rb)
    x0, y0_, x1, y1 = loops_bbox([("p", pts)] + ([("c", 0, 0, rf)] if flanges else []))
    box = ((x0, y0_, 0.0), (x1, y1, total))
    plan = Plan(p, steps, p, box, vol, volume_tol=0.03, export_parts=[p], part_bboxes={p: box},
                notes=f"polia {meta['belt']} z={teeth}: Ø primitivo {meta['pitch_diameter']}, Ø externo {meta['outer_diameter']}, "
                      f"correia {bw:g} mm; perfil do dente aproximado por trapezio (valores de referência)")
    info = dict(meta, teeth=int(teeth), bore=bore, belt_width=bw, flange_diameter=round(2 * rf, 2) if flanges else None,
                hub_length=hl, total_length=round(total, 2))
    bom = [_bom(f"Polia sincronizadora {meta['belt']} {int(teeth)}T furo Ø{bore:g} p/ correia {bw:g} mm", "polia", 1, [p], vol,
                "Alumínio / aço", "massa em aço")]
    return Built(orient(plan, axis, origin, name=p), bom, info)


# ------------------------------------------------------------------ acoplamentos
def _auto_od(bore1: float, bore2: float) -> float:
    return float(round(max(bore1, bore2) * 1.9 + 6.0))


def _sector(r_in, r_out, a0, a1, n=6):
    outer = [(r_out * math.cos(a0 + (a1 - a0) * i / n), r_out * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
    inner = [(r_in * math.cos(a1 - (a1 - a0) * i / n), r_in * math.sin(a1 - (a1 - a0) * i / n)) for i in range(n + 1)]
    return outer + inner


def coupling(kind="rigid", bore1=20.0, bore2=0.0, od=0.0, length=0.0, keyway=True, set_screw_d=0.0, bolts=0, bolt_size=8,
             jaws=6, name="acoplamento", axis="z", origin=(0.0, 0.0, 0.0)) -> Built:
    """Acoplamento de eixos. kind: "rigid" (luva rigida, 1 peca, furos `bore1`/`bore2`) | "flange" (2 cubos flangeados, `bolts`
    furos M`bolt_size`; com bolts>0 inclui os conjuntos parafuso+porca) | "jaw" (2 cubos de garras + estrela de elastomero,
    `jaws` garras por cubo). Dimensoes proporcionais ao furo quando od/length nao sao dados: confira com o catalogo do
    fabricante. Eixo +z, face inicial em z=0."""
    k = str(kind).lower()
    b1 = _pos("bore1", bore1, 4)
    b2 = float(bore2) if bore2 else b1
    if b2 < 4:
        raise PartError("bore2 deve ser >= 4")
    OD = float(od) if od else _auto_od(b1, b2)
    if OD < max(b1, b2) + 10:
        raise PartError(f"od={OD:g} pequeno demais para furos Ø{b1:g}/Ø{b2:g}")
    p = safe_name(name, "acoplamento")
    if k == "rigid":
        L = float(length) if length else float(round(2.6 * max(b1, b2) + 10.0))
        half = L / 2.0
        steps = [Step("extrude", p, [("c", 0, 0, OD / 2), ("c", 0, 0, b1 / 2)], 0.0, half, label="metade 1"),
                 Step("extrude", p, [("c", 0, 0, OD / 2), ("c", 0, 0, b2 / 2)], half, half, label="metade 2")]
        vol = (circle_area(OD / 2) * L - circle_area(b1 / 2) * half - circle_area(b2 / 2) * half)
        if keyway:
            for bore, z0 in ((b1, 0.0), (b2, half)):
                if 6 < bore <= 130:
                    st, kv = hub_keyway_step(p, bore, z0, half, key_for(bore), rounded=False)
                    if OD / 2 < bore / 2 + key_for(bore)["t2"] + 2.0:
                        raise PartError("luva fina demais para o rasgo de chaveta: aumente od")
                    steps.append(st)
                    vol -= kv
        if set_screw_d:
            sr = float(set_screw_d) / 2.0
            for z in (half / 2.0, half * 1.5):
                y0 = OD / 2 + 1.0
                steps.append(Step("cut", p, [("c", 0, z, sr)], y0, y0 - (min(b1, b2) / 2 - 0.5), "-w", "parafuso de fixacao", axis="y"))
                vol -= math.pi * sr * sr * (OD / 2 - max(b1, b2) / 2)
        box = ((-OD / 2, -OD / 2, 0.0), (OD / 2, OD / 2, L))
        plan = Plan(p, steps, p, box, vol, volume_tol=0.04, export_parts=[p], part_bboxes={p: box},
                    notes=f"luva rígida Ø{OD:g} x {L:g}, furos Ø{b1:g}/Ø{b2:g}")
        bom = [_bom(f"Acoplamento rígido (luva) Ø{OD:g}x{L:g} furos {b1:g}/{b2:g}", "acoplamento", 1, [p], vol)]
        return Built(orient(plan, axis, origin, name=p), bom, {"kind": k, "od": OD, "length": L, "bore1": b1, "bore2": b2})
    if k == "flange":
        hub_od = float(round(max(b1, b2) * 1.6 + 6.0))
        ft = float(round(max(b1, b2) * 0.35 + 4.0))
        hl = float(round(max(b1, b2) * 1.1 + 4.0))
        n = int(bolts) if bolts else 4
        sz = int(bolt_size)
        if sz not in WASHER_TABLE:
            raise PartError(f"bolt_size M{sz} fora da tabela: {sorted(WASHER_TABLE)}")
        w_od = WASHER_TABLE[sz][1]
        if not od:                                   # a flange precisa deixar a arruela passar ao lado do cubo
            OD = hub_od + 2 * (w_od + 6.0)
        pcd = (OD + hub_od) / 2.0
        hole = sz + 1.0
        if pcd / 2 + w_od / 2 + 1 > OD / 2 or pcd / 2 - w_od / 2 - 1 < hub_od / 2:
            raise PartError(f"flange Ø{OD:g} sem espaço para arruelas M{sz} ao lado do cubo Ø{hub_od:g}: aumente od")
        hubs = []
        for idx, (bore, zdir) in enumerate(((b1, 1), (b2, -1)), 1):
            pn = f"{p}_cubo{idx}"
            loops = [("c", 0, 0, OD / 2), ("c", 0, 0, bore / 2)] + bolt_circle(n, pcd, hole, 180.0 / n)
            st_f = Step("extrude", pn, loops, 0.0, ft, label=f"flange {idx}")
            st_h = Step("extrude", pn, [("c", 0, 0, hub_od / 2), ("c", 0, 0, bore / 2)], ft, hl, label=f"cubo {idx}")
            vol = (circle_area(OD / 2) - circle_area(bore / 2) - n * circle_area(hole / 2)) * ft \
                + (circle_area(hub_od / 2) - circle_area(bore / 2)) * hl
            steps_ = [st_f, st_h]
            if keyway and 6 < bore <= 130:
                st_k, kv = hub_keyway_step(pn, bore, 0.0, ft + hl, key_for(bore), rounded=False)
                steps_.append(st_k)
                vol -= kv
            box = ((-OD / 2, -OD / 2, 0.0), (OD / 2, OD / 2, ft + hl))
            pl = Plan(pn, steps_, pn, box, vol, volume_tol=0.04, export_parts=[pn], part_bboxes={pn: box})
            # cubo 2 espelhado: face do flange em z = 0 (de encontro ao cubo 1) e cubo para -z (giro de 180 graus em torno de x)
            hubs.append(pl if zdir > 0 else orient(pl, "-z", (0.0, 0.0, 0.0)))
        built = [Built(h, [_bom(f"Cubo flangeado Ø{OD:g} furo Ø{bb:g}", "acoplamento", 1, [h.main_part], h.expected_volume)])
                 for h, bb in zip(hubs, (b1, b2))]
        if bolts:
            fl = fastener_layout(bolt_circle_pts(n, pcd, 180.0 / n), size=sz, grip=2 * ft, name=f"{p}_fix", washer_top=True,
                                 washer_bottom=True)
            built.append(Built(orient(fl.plan, "z", (0.0, 0.0, -ft)), fl.bom))
        merged = merge_built(p, built, notes=f"acoplamento flangeado Ø{OD:g}: 2 cubos, {n} furos Ø{hole:g} em PCD {pcd:g}"
                                             + (f", {n} conjuntos M{sz}" if bolts else ""))
        info = {"kind": k, "od": OD, "hub_od": hub_od, "flange_t": ft, "hub_length": hl, "pcd": pcd, "bolts": n,
                "bore1": b1, "bore2": b2, "total_length": 2 * (ft + hl)}
        return Built(orient(merged.plan, axis, origin, name=p), merged.bom, info)
    if k == "jaw":
        n = int(jaws)
        if n < 3 or n > 8:
            raise PartError("jaws deve estar entre 3 e 8")
        hub_od = OD
        hl = float(round(max(b1, b2) * 1.2 + 6.0))
        jh = float(round(0.35 * OD))
        gap = 2.0
        r_out = hub_od / 2.0
        r_in = r_out * 0.62
        alpha = 2 * math.pi / n
        wj = 0.30 * alpha
        z2 = hl + jh + gap
        if r_in - max(b1, b2) / 2.0 < 3.0:
            raise PartError("furo grande demais para o cubo de garras: aumente od")
        # cubo 1: corpo de z=0 a hl, garras de hl a hl+jh nos angulos j*alpha
        def jaw_hub(pn, bore, ang0):
            ss = [Step("extrude", pn, [("c", 0, 0, r_out), ("c", 0, 0, bore / 2)], 0.0, hl, label="corpo do cubo")]
            v = (circle_area(r_out) - circle_area(bore / 2)) * hl
            jl = []
            for j in range(n):
                c = ang0 + j * alpha
                sec = _sector(r_in, r_out, c - wj / 2, c + wj / 2)
                jl.append(("p", sec))
                v += poly_area(sec) * jh
            ss.append(Step("extrude", pn, jl, hl, jh, label="garras"))
            return ss, v
        n1, n2, ns = f"{p}_cubo1", f"{p}_cubo2", f"{p}_estrela"
        s1, v1 = jaw_hub(n1, b1, 0.0)
        box1 = ((-r_out, -r_out, 0.0), (r_out, r_out, hl + jh))
        if keyway and 6 < b1 <= 130:
            sk, kv = hub_keyway_step(n1, b1, 0.0, hl, key_for(b1), rounded=False)
            s1.append(sk)
            v1 -= kv
        pl1 = Plan(n1, s1, n1, box1, v1, volume_tol=0.04, export_parts=[n1], part_bboxes={n1: box1})
        s2, v2 = jaw_hub(n2, b2, alpha / 2.0)          # cubo 2 gerado como o 1 e girado 180 graus em torno de x, garras para -z
        box2 = ((-r_out, -r_out, 0.0), (r_out, r_out, hl + jh))
        if keyway and 6 < b2 <= 130:
            sk, kv = hub_keyway_step(n2, b2, 0.0, hl, key_for(b2), rounded=False)
            s2.append(sk)
            v2 -= kv
        pl2 = Plan(n2, s2, n2, box2, v2, volume_tol=0.04, export_parts=[n2], part_bboxes={n2: box2})
        pl2 = orient(pl2, "-z", (0.0, 0.0, hl + jh + gap + hl))     # corpo em [jh+gap+hl .. ], garras descendo ate hl+gap
        # estrela de elastomero: nucleo + 2n lobos nos vaos entre garras
        rc = r_in - 0.5
        rs_in = max(b1, b2) / 2.0 + 3.0
        pts = []
        free = alpha / 2.0 - wj
        lobe = free / 2.0 - 0.02
        for i in range(2 * n):
            c = i * alpha / 2.0 + alpha / 4.0
            lobe_pts = [(rc * math.cos(c - lobe), rc * math.sin(c - lobe))] + \
                       [(r_out * math.cos(c - lobe + 2 * lobe * t / 4.0), r_out * math.sin(c - lobe + 2 * lobe * t / 4.0)) for t in range(5)] + \
                       [(rc * math.cos(c + lobe), rc * math.sin(c + lobe))]
            # entre lobos: arco do nucleo
            nxt = (i + 1) * alpha / 2.0 + alpha / 4.0 - lobe
            arc = [(rc * math.cos(c + lobe + (nxt - c - lobe) * t / 3.0), rc * math.sin(c + lobe + (nxt - c - lobe) * t / 3.0)) for t in range(1, 3)]
            pts += lobe_pts + arc
        zs0, zs1 = hl + 0.5, z2 - 0.5
        sp = Step("extrude", ns, [("p", pts), ("c", 0, 0, rs_in)], zs0, zs1 - zs0, label="estrela de elastomero")
        vs = (poly_area(pts) - circle_area(rs_in)) * (zs1 - zs0)
        box_s = loops_bbox([("p", pts)])
        boxs = ((box_s[0], box_s[1], zs0), (box_s[2], box_s[3], zs1))
        pls = Plan(ns, [sp], ns, boxs, vs, volume_tol=0.04, export_parts=[ns], part_bboxes={ns: boxs})
        built = [Built(pl1, [_bom(f"Cubo de garras Ø{hub_od:g} furo Ø{b1:g}", "acoplamento", 1, [n1], v1)]),
                 Built(pl2, [_bom(f"Cubo de garras Ø{hub_od:g} furo Ø{b2:g}", "acoplamento", 1, [n2], v2)]),
                 Built(pls, [_bom(f"Estrela de elastômero {2 * n} lobos", "acoplamento", 1, [ns], vs, "Poliuretano 92 Shore A")])]
        merged = merge_built(p, built, notes=f"acoplamento de garras Ø{hub_od:g}, {n} garras por cubo, estrela de elastômero; "
                                           "proporções típicas (confira com o catálogo)")
        total_len = 2 * hl + jh + gap
        info = {"kind": k, "od": hub_od, "hub_length": hl, "jaw_height": jh, "gap": gap, "total_length": total_len, "jaws": n,
                "bore1": b1, "bore2": b2}
        return Built(orient(merged.plan, axis, origin, name=p), merged.bom, info)
    raise PartError("kind deve ser rigid, flange ou jaw")


def bolt_circle_pts(n: int, pcd: float, start_deg: float = 0.0) -> list:
    return [(pcd / 2 * math.cos(math.radians(start_deg + 360.0 * i / n)), pcd / 2 * math.sin(math.radians(start_deg + 360.0 * i / n)))
            for i in range(n)]
