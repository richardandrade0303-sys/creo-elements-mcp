"""Analise da cena (nivel 1): caixas envolventes, medidas, pre-filtro de interferencia, auditoria, inspecao.

Tudo que decide algo e Python puro sobre dados lidos do Creo (arvore + caixas + volumes), entao e testavel offline.
Do Creo so saem consultas de leitura:
  * caixas exatas:  (oli:sd-inq-exact-box ...)           - verificado ao vivo (creo_get_part_info);
  * volume e area:  GET_VOL_PROP dentro de um dialogo    - verificado ao vivo (volume 125695.077 mm3 ao vivo; cubo
                    10 mm: 1000 / 600).
A massa NAO vem do Creo (sem material definido ele devolve volume x 1): calcula-se aqui com a densidade informada.
"""

from __future__ import annotations

import itertools
import math

from . import lisp, scene
from .lisp import lisp_str

Box = tuple  # ((x0, y0, z0), (x1, y1, z1))

DENSITY_G_CM3 = {          # materiais comuns (g/cm3) para estimativa de massa
    "aco": 7.85, "steel": 7.85, "inox": 8.0, "stainless": 8.0, "aluminio": 2.70, "aluminum": 2.70,
    "latao": 8.5, "brass": 8.5, "bronze": 8.8, "cobre": 8.96, "copper": 8.96, "ferro_fundido": 7.2,
    "cast_iron": 7.2, "titanio": 4.51, "titanium": 4.51, "nylon": 1.14, "abs": 1.05, "pla": 1.24,
    "pvc": 1.4, "borracha": 1.2, "madeira": 0.6, "concreto": 2.4,
}


# ------------------------------------------------------------------ leitura no Creo
def box_code(path: str) -> str:
    """Caixa exata de UMA peca, na forma ja verificada ao vivo (recipes.part_info). NAO usar mapcar + ignore-errors em
    volta de sd-inq-exact-box: essa forma travou o Creo (0xc00000fd) em 4 de 4 tentativas em 2026-10-22/23."""
    p = path if path.startswith("/") else "/" + path
    return (
        f"(let ((p (oli:sd-pathname-to-obj {lisp_str(p)})))\n"
        "  (if p (oli:sd-inq-exact-box p 0,0,1 1,0,0 :source-space :global :dest-space :global) :part-not-found))"
    )


def boxes_code(paths: list[str]) -> str:
    """Compatibilidade: so aceita um caminho (uma peca por chamada ao Creo)."""
    if len(paths) != 1:
        raise ValueError("boxes_code le uma peca por vez (leitura em lote derrubou o Creo)")
    return box_code(paths[0])


def parse_box(result_text):
    """((x0,y0,z0),(x1,y1,z1)) ou None (peca ausente / resposta ilegivel)."""
    data = lisp.parse_sexp(result_text) if isinstance(result_text, str) else result_text
    ok = isinstance(data, list) and len(data) == 2 and all(isinstance(q, list) and len(q) == 3 for q in data)
    return (tuple(data[0]), tuple(data[1])) if ok else None


def parse_boxes(result_text: str) -> dict:
    """{caminho: ((x0,y0,z0),(x1,y1,z1)) | None}"""
    data = lisp.parse_sexp(result_text)
    out: dict = {}
    for entry in data if isinstance(data, list) else []:
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        path, box = str(entry[0]), entry[1]
        ok = isinstance(box, list) and len(box) == 2 and all(isinstance(p, list) and len(p) == 3 for p in box)
        out[path] = (tuple(box[0]), tuple(box[1])) if ok else None
    return out


VP_DIALOG = "MCP_VP_OP"


def vol_dialog_define() -> str:
    """Dialogo que mede volume e area (GET_VOL_PROP so roda em acao de usuario) de *mcp-vp-parts*.

    ATENCAO (verificado ao vivo em 2026-10-23): GET_VOL_PROP IGNORA `:for_part` e mede a peca CORRENTE. Por isso a peca
    e tornada corrente em `vol_setup` e o dialogo devolve tambem a peca corrente, para `parse_vol` descartar qualquer
    leitura em que ela nao seja a pedida."""
    return (
        "(progn\n(defvar *mcp-vp-parts* nil)\n(defvar *mcp-vp-result* :unset)\n"
        "(oli:sd-defdialog 'mcp_vp_op\n"
        "  :ok-action '(setf *mcp-vp-result*\n"
        "     (mapcar #'(lambda (path)\n"
        "                 (let ((p (oli:sd-pathname-to-obj path))\n"
        "                       (c (oli:sd-inq-curr-part)))\n"
        "                   (if p\n"
        "                       (list path\n"
        "                             (oli:sd-call-cmds (oli:get_vol_prop :for_part p :volume))\n"
        "                             (oli:sd-call-cmds (oli:get_vol_prop :for_part p :area))\n"
        "                             (if c (oli:sd-inq-obj-pathname c) :none))\n"
        "                       (list path :missing))))\n"
        "             *mcp-vp-parts*)))\n"
        ":defined)"
    )


def vol_setup(paths: list[str]) -> str:
    """Define a peca a medir E a torna corrente (`current_part`). Exatamente UMA peca por vez."""
    if len(paths) != 1:
        raise ValueError("GET_VOL_PROP mede a peca corrente: uma peca por leitura")
    path = paths[0] if paths[0].startswith("/") else "/" + paths[0]
    q = lisp_str(path)
    return (f"(progn (setf *mcp-vp-parts* (list {q})) (setf *mcp-vp-result* :unset) "
            f"(when (oli:sd-pathname-to-obj {q}) (current_part {q})) :ready)")


def vol_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(VP_DIALOG + ' COMPLETE')})"


def vol_result_code() -> str:
    return "*mcp-vp-result*"


def parse_vol(result) -> dict:
    """{caminho: {'volume_mm3', 'area_mm2'} | None}. Leitura cuja peca corrente nao e a pedida vira None (nao confiavel)."""
    out: dict = {}
    for entry in result if isinstance(result, list) else []:
        if (isinstance(entry, list) and len(entry) in (3, 4)
                and all(isinstance(v, (int, float)) for v in entry[1:3])):
            if len(entry) == 4 and str(entry[3]).lstrip("/") != str(entry[0]).lstrip("/"):
                out[str(entry[0])] = None
                continue
            out[str(entry[0])] = {"volume_mm3": float(entry[1]), "area_mm2": float(entry[2])}
        elif isinstance(entry, list) and entry:
            out[str(entry[0])] = None
    return out



# ------------------------------------------------------------------ geometria de caixas
def box_size(b: Box) -> tuple:
    return tuple(b[1][i] - b[0][i] for i in range(3))


def box_center(b: Box) -> tuple:
    return tuple((b[0][i] + b[1][i]) / 2.0 for i in range(3))


def box_volume(b: Box) -> float:
    s = box_size(b)
    return max(s[0], 0.0) * max(s[1], 0.0) * max(s[2], 0.0)


def overlap(a: Box, b: Box) -> tuple:
    """Interseccao por eixo: (dx, dy, dz); valor negativo = folga (distancia) naquele eixo."""
    return tuple(min(a[1][i], b[1][i]) - max(a[0][i], b[0][i]) for i in range(3))


def box_gap(a: Box, b: Box) -> float:
    """Menor distancia entre as caixas (0 se tocam ou se interpenetram)."""
    gaps = [max(0.0, -o) for o in overlap(a, b)]
    return math.sqrt(sum(g * g for g in gaps))


def overlap_volume(a: Box, b: Box) -> float:
    o = overlap(a, b)
    return max(o[0], 0.0) * max(o[1], 0.0) * max(o[2], 0.0) if all(v > 0 for v in o) else 0.0


def point_box_distance(pt, b: Box) -> float:
    d = [max(b[0][i] - pt[i], 0.0, pt[i] - b[1][i]) for i in range(3)]
    return math.sqrt(sum(x * x for x in d))


def union_box(boxes) -> Box | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (tuple(min(b[0][i] for b in boxes) for i in range(3)), tuple(max(b[1][i] for b in boxes) for i in range(3)))


def r3(v, nd=3):
    return [round(float(x), nd) for x in v]


# ------------------------------------------------------------------ medidas
def measure(boxes: dict, a: str, b: str | None = None, point=None) -> dict:
    """Medidas por caixa envolvente: tamanho/centro de `a`; com `b` a distancia entre as caixas, entre os centros e a
    interpenetracao por eixo; com `point` a distancia do ponto a caixa. Exato so para solidos alinhados aos eixos."""
    ba = boxes.get(a)
    if ba is None:
        return {"ok": False, "error": f"sem caixa para {a} (a peca existe? creo_get_model_tree)"}
    out: dict = {"ok": True, "basis": "caixa envolvente alinhada aos eixos globais",
                 "a": {"path": a, "min": r3(ba[0]), "max": r3(ba[1]), "size": r3(box_size(ba)),
                       "center": r3(box_center(ba))}}
    if b:
        bb = boxes.get(b)
        if bb is None:
            return {"ok": False, "error": f"sem caixa para {b}"}
        ov = overlap(ba, bb)
        ca, cb = box_center(ba), box_center(bb)
        out["b"] = {"path": b, "min": r3(bb[0]), "max": r3(bb[1]), "size": r3(box_size(bb)), "center": r3(cb)}
        out["gap_mm"] = round(box_gap(ba, bb), 4)
        out["center_distance_mm"] = round(math.dist(ca, cb), 4)
        out["delta_centers"] = r3([cb[i] - ca[i] for i in range(3)])
        out["axis_overlap_mm"] = r3(ov)       # >0 interpenetra naquele eixo, <0 folga
        out["boxes_intersect"] = all(v > 1e-9 for v in ov)
        out["boxes_touch"] = (not out["boxes_intersect"]) and out["gap_mm"] < 1e-6
    if point is not None:
        try:
            pt = [float(v) for v in point]
            if len(pt) != 3:
                raise ValueError
        except (TypeError, ValueError):
            return {"ok": False, "error": "point deve ser [x, y, z]"}
        out["point"] = pt
        out["point_distance_mm"] = round(point_box_distance(pt, ba), 4)
        out["point_inside_box"] = out["point_distance_mm"] < 1e-9
    return out


# ------------------------------------------------------------------ interferencia (pre-filtro por caixa)
def clash_candidates(boxes: dict, tol: float = 0.01, include_touching: bool = False, pairs=None) -> list[dict]:
    """Pares cujas caixas se sobrepoem em mais que `tol` mm nos tres eixos. Sao CANDIDATOS: duas pecas podem ter caixas
    sobrepostas sem se tocar (ex.: parafuso dentro do furo de uma chapa). A confirmacao exata e feita sobre a malha."""
    names = [n for n, b in boxes.items() if b]
    out = []
    iterator = pairs if pairs is not None else itertools.combinations(names, 2)
    for a, b in iterator:
        if not (boxes.get(a) and boxes.get(b)):
            continue
        ov = overlap(boxes[a], boxes[b])
        if all(v > tol for v in ov):
            out.append({"a": a, "b": b, "overlap_mm": r3(ov), "overlap_volume_mm3": round(box_volume_from(ov), 3)})
        elif include_touching and all(v >= -tol for v in ov):
            out.append({"a": a, "b": b, "overlap_mm": r3(ov), "overlap_volume_mm3": 0.0, "touching": True})
    out.sort(key=lambda r: -r["overlap_volume_mm3"])
    return out


def box_volume_from(ov) -> float:
    return max(ov[0], 0.0) * max(ov[1], 0.0) * max(ov[2], 0.0)


# ------------------------------------------------------------------ auditoria
def audit(tree, boxes: dict | None = None, vols: dict | None = None, limit: int | None = None,
          far_mm: float = 5000.0, dup_tol: float = 0.01) -> dict:
    """Auditoria da cena: lista de achados {level, code, items, message} + resumo. Nada e alterado."""
    findings: list[dict] = []

    def add(level, code, items, message):
        findings.append({"level": level, "code": code, "items": items, "message": message})

    limit = scene.part_limit() if limit is None else int(limit)
    bud = scene.budget(tree, 0, limit)
    if limit > 0:
        if bud["used"] > limit:
            add("error", "part_limit", [], f"{bud['used']} pecas: passa do limite configurado de {limit}")
        elif bud["used"] >= int(limit * 0.9):
            add("warn", "part_limit_near", [], f"{bud['used']} de {limit} pecas usadas ({bud['left']} livres)")
    left = scene.leftovers(tree)
    if left:
        add("error", "leftover_tool", left, "pecas auxiliares que sobraram (ferramenta de corte/helice): passo anterior falhou")
    split = scene.split_pieces(tree)
    if split:
        add("warn", "split_solids", [s["path"] for s in split],
            "solidos disjuntos separados em outra peca (x.N): a peca ficou em pedacos")
    emp = scene.empty_assemblies(tree)
    if emp:
        add("warn", "empty_assembly", emp, "montagens sem filhos")
    sim = scene.similar_names(tree)
    if sim:
        add("info", "similar_names", [", ".join(g) for g in sim], "nomes que diferem so por caixa/separador (duplicata provavel)")

    names = scene.parts_of(tree)
    if boxes is not None:
        missing = [n for n in names if not boxes.get(n)]
        if missing:
            add("warn", "no_bbox", missing, "sem caixa envolvente (peca vazia, so superficie ou erro de leitura)")
        flat, far = [], []
        for n in names:
            b = boxes.get(n)
            if not b:
                continue
            s = box_size(b)
            if min(s) < 1e-6:
                flat.append(n)
            if max(abs(v) for v in list(b[0]) + list(b[1])) > far_mm:
                far.append(n)
        if flat:
            add("warn", "zero_thickness", flat, "caixa com espessura zero em algum eixo (peca degenerada ou so uma face)")
        if far:
            add("warn", "far_from_origin", far, f"peca a mais de {far_mm:g} mm da origem (unidade errada ou erro de posicao)")
        keys = {}
        for n in names:
            b = boxes.get(n)
            if b:
                k = tuple(round(v / max(dup_tol, 1e-9)) for v in list(b[0]) + list(b[1]))
                keys.setdefault(k, []).append(n)
        dup = [v for v in keys.values() if len(v) > 1]
        if dup:
            add("warn", "coincident_parts", [", ".join(g) for g in dup],
                "pecas com a MESMA caixa envolvente: copia duplicada no mesmo lugar?")
        have = [n for n in names if boxes.get(n)]
        if len(have) > 1:
            iso = []
            for n in have:
                if not any(m != n and box_gap(boxes[n], boxes[m]) <= 1.0 for m in have):
                    iso.append(n)
            if iso and len(iso) < len(have):
                add("info", "floating_parts", iso, "pecas a mais de 1 mm de qualquer outra (montagem solta ou fora de posicao)")
        cands = clash_candidates({n: boxes[n] for n in have}, tol=0.05)
        # caixa dentro de caixa (furo + parafuso) e normal; so marca sobreposicao grande de volume
        big = []
        for c in cands:
            va = box_volume(boxes[c["a"]])
            vb = box_volume(boxes[c["b"]])
            small = min(va, vb)
            if small > 0 and c["overlap_volume_mm3"] / small > 0.5:
                big.append(f"{c['a']} x {c['b']}")
        if big:
            add("info", "bbox_overlap", big, "caixas com mais de 50% do volume da menor dentro da outra: confirme com creo_clash_check")
        if vols is not None:
            nv = [n for n in names if vols.get(n) is None]
            zero = [n for n in names if isinstance(vols.get(n), dict) and vols[n]["volume_mm3"] <= 1e-6]
            if zero:
                add("error", "zero_volume", zero, "volume zero: peca vazia")
            vk = {}
            for n in names:
                v, b = vols.get(n), boxes.get(n)
                if isinstance(v, dict) and b:
                    vk.setdefault((round(v["volume_mm3"], 1),) + tuple(round(x, 2) for x in box_size(b)), []).append(n)
            same = [g for g in vk.values() if len(g) > 1]
            if same:
                add("info", "identical_parts", [", ".join(g) for g in same],
                    "pecas com mesmo volume e mesmas dimensoes (peca repetida - no BOM contam como uma so?)")
            if nv and len(nv) < len(names):
                add("info", "no_volume", nv, "volume nao lido")
    levels = {"error": 0, "warn": 0, "info": 0}
    for f in findings:
        levels[f["level"]] += 1
    return {"ok": levels["error"] == 0, "parts": bud["used"], "part_limit": limit, "summary": levels,
            "findings": findings}


# ------------------------------------------------------------------ inspecao e massa
def density_of(material: str | float | None) -> float | None:
    if material is None or material == "":
        return None
    if isinstance(material, (int, float)):
        return float(material)
    key = str(material).strip().lower().replace(" ", "_")
    if key in DENSITY_G_CM3:
        return DENSITY_G_CM3[key]
    try:
        return float(key.replace(",", "."))
    except ValueError:
        return None


def mass_g(volume_mm3: float, density_g_cm3: float) -> float:
    return volume_mm3 * density_g_cm3 / 1000.0


def inspect_part(path: str, box: Box | None, vol: dict | None, density: float | None = None,
                 faces: list | None = None, edges: list | None = None) -> dict:
    out: dict = {"path": path}
    if box:
        out["bbox"] = {"min": r3(box[0]), "max": r3(box[1]), "size": r3(box_size(box)), "center": r3(box_center(box))}
        out["bbox_volume_mm3"] = round(box_volume(box), 3)
    if vol:
        out["volume_mm3"] = round(vol["volume_mm3"], 3)
        out["area_mm2"] = round(vol["area_mm2"], 3)
        if box and box_volume(box) > 0:
            out["fill_ratio"] = round(vol["volume_mm3"] / box_volume(box), 4)
        if density:
            out["density_g_cm3"] = density
            out["mass_g"] = round(mass_g(vol["volume_mm3"], density), 3)
            out["mass_kg"] = round(mass_g(vol["volume_mm3"], density) / 1000.0, 5)
    if faces is not None:
        out["faces"] = len(faces)
    if edges is not None:
        out["edges"] = len(edges)
    return out
