"""Operacoes tipadas sobre pecas existentes: mover/girar, agrupar, colorir, espelhar, cortar (secao), mostrar/ocultar.

Cada operacao gera o Lisp a partir de sintaxes ja usadas ao vivo (skill creo-lisp-mcp, secao 6):
  * POSITION_PA "/p" :TRANSLATE dx,dy,dz COMPLETE   e   :ROTATE :AXIS :TWO_PTA p1 p2 :ROTATION_ANGLE graus COMPLETE
    (texto de comando por sd-put-buffer; assincrono; angulo em GRAUS);
  * (create_assembly :name "conj")  +  (change_pa_owner :new_owner "/conj" :parts_assemblies "/p")   (sem barra no nome);
  * (funcall (find-symbol "SET_PART_COLOR" "ELAN") obj :rgb (oli:make-gpnt3d :x r :y g :z b) :done);
  * MIRROR_3D / SECTION so dentro de um sd-defdialog :ok-action, com sd-call-cmds ... :success :ok :failure (...).
Cada passo e CONFERIDO por consulta (caixa envolvente, cor lida de volta, caminho na arvore) e o resultado diz o que foi
visto. `runner(code, timeout)` e a funcao do servidor; `settle()` fecha dialogos e espera o Creo.
"""

from __future__ import annotations

import math
import time

from . import analysis, lisp
from .lisp import lisp_str, num

NAMED_COLORS = {
    "vermelho": (1.0, 0.1, 0.1), "red": (1.0, 0.1, 0.1), "verde": (0.1, 0.7, 0.2), "green": (0.1, 0.7, 0.2),
    "azul": (0.1, 0.3, 0.9), "blue": (0.1, 0.3, 0.9), "amarelo": (1.0, 0.85, 0.1), "yellow": (1.0, 0.85, 0.1),
    "laranja": (1.0, 0.5, 0.0), "orange": (1.0, 0.5, 0.0), "cinza": (0.6, 0.6, 0.6), "gray": (0.6, 0.6, 0.6),
    "grey": (0.6, 0.6, 0.6), "preto": (0.1, 0.1, 0.1), "black": (0.1, 0.1, 0.1), "branco": (0.95, 0.95, 0.95),
    "white": (0.95, 0.95, 0.95), "aco": (0.7, 0.72, 0.75), "steel": (0.7, 0.72, 0.75), "bronze": (0.72, 0.5, 0.2),
    "cobre": (0.8, 0.45, 0.25), "copper": (0.8, 0.45, 0.25), "marrom": (0.45, 0.28, 0.12), "brown": (0.45, 0.28, 0.12),
    "roxo": (0.5, 0.2, 0.7), "purple": (0.5, 0.2, 0.7), "rosa": (1.0, 0.5, 0.7), "pink": (1.0, 0.5, 0.7),
}


def path_of(name: str) -> str:
    return "/" + name.strip().lstrip("/")


def parse_color(value) -> tuple:
    """Cor: nome (tabela), '#RRGGBB' ou [r, g, b] com 0-1 (ou 0-255 se algum valor passar de 1)."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in NAMED_COLORS:
            return NAMED_COLORS[v]
        if v.startswith("#") and len(v) == 7:
            try:
                return tuple(int(v[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            except ValueError:
                pass
        raise ValueError(f"cor desconhecida: {value!r} (nomes: {', '.join(sorted(set(NAMED_COLORS)))}; ou '#RRGGBB'; ou [r,g,b])")
    try:
        rgb = [float(x) for x in value]
    except (TypeError, ValueError):
        raise ValueError("cor deve ser nome, '#RRGGBB' ou [r, g, b]")
    if len(rgb) != 3:
        raise ValueError("cor [r, g, b] precisa de 3 valores")
    if max(rgb) > 1.0:
        rgb = [x / 255.0 for x in rgb]
    if min(rgb) < 0 or max(rgb) > 1.0:
        raise ValueError("componentes de cor fora de 0-1 (ou 0-255)")
    return tuple(rgb)


# ------------------------------------------------------------------ dialogo generico
def run_dialog(runner, settle, define: str, setup: str, command: str, result_code: str, unset: str = ":UNSET",
               tries: int = 30, pause: float = 0.7) -> dict:
    """Define o dialogo, configura as variaveis, chama por texto (assincrono) e le o resultado.

    Devolve {ok, result (ja parseado), dialogs, error}. `ok` so diz que o dialogo rodou ate o fim e deixou um resultado."""
    out: dict = {"ok": False, "result": None, "dialogs": None}
    for code in (define, setup):
        r = runner(code, 30)
        if (not r.get("ok")) or r.get("dialogs"):
            out["error"] = r.get("error") or "o Creo abriu dialogos ao preparar a operacao"
            out["dialogs"] = r.get("dialogs")
            return out
    r = runner(command, 20)
    out["dialogs"] = settle(3.0) or None
    if (not r.get("ok")) or (r.get("dialogs") and not r.get("note")):
        out["error"] = r.get("error") or "falha ao enfileirar o comando"
        return out
    res = None
    for _ in range(tries):
        raw = runner(result_code, 15)
        res = lisp.parse_sexp(raw["result"]) if raw.get("ok") and raw.get("result") else None
        if not (raw.get("ok") and (res is None or str(res).upper() == unset)):
            break
        time.sleep(pause)
    out["result"] = res
    if res is None or str(res).upper() == unset:
        out["error"] = "o resultado da operacao nao apareceu (veja creo_screenshot)"
        return out
    out["ok"] = True
    return out


def _fail(res) -> str | None:
    """Texto do erro se `res` for (:FAIL codigo mensagem)."""
    if isinstance(res, list) and res and str(res[0]).upper() == ":FAIL":
        return " ".join(str(x) for x in res[1:])
    return None


# ------------------------------------------------------------------ mover e girar
def translate_cmd(part: str, d) -> str:
    cmd = f'POSITION_PA "{path_of(part)}" :TRANSLATE {num(d[0])},{num(d[1])},{num(d[2])} COMPLETE'
    return f"(oli:sd-put-buffer {lisp_str(cmd)})"


AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def rotate_cmd(part: str, origin, axis, angle_deg: float) -> str:
    """Giro em GRAUS em torno do eixo que passa por `origin` com direcao `axis` ('x'|'y'|'z' ou vetor)."""
    u = AXES.get(axis) if isinstance(axis, str) else tuple(float(v) for v in axis)
    if u is None or len(u) != 3 or all(abs(v) < 1e-12 for v in u):
        raise ValueError("axis deve ser 'x', 'y', 'z' ou um vetor [x, y, z] nao nulo")
    p2 = [origin[i] + u[i] for i in range(3)]
    cmd = (f'POSITION_PA "{path_of(part)}" :ROTATE :AXIS :TWO_PTA {num(origin[0])},{num(origin[1])},{num(origin[2])} '
           f'{num(p2[0])},{num(p2[1])},{num(p2[2])} :ROTATION_ANGLE {num(angle_deg)} COMPLETE')
    return f"(oli:sd-put-buffer {lisp_str(cmd)})"


def rotate_point(pt, origin, axis, angle_deg: float):
    """Rodrigues: gira `pt` em torno do eixo (origem, direcao unitaria), regra da mao direita."""
    u = AXES.get(axis) if isinstance(axis, str) else tuple(float(v) for v in axis)
    n = math.sqrt(sum(v * v for v in u))
    u = tuple(v / n for v in u)
    th = math.radians(angle_deg)
    v = [pt[i] - origin[i] for i in range(3)]
    c, s = math.cos(th), math.sin(th)
    d = sum(u[i] * v[i] for i in range(3))
    cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    return tuple(origin[i] + v[i] * c + cr[i] * s + u[i] * d * (1 - c) for i in range(3))


def expected_box_after_rotation(box, origin, axis, angle_deg: float):
    """Caixa envolvente dos 8 cantos girados: EXATA so para solidos que preenchem a caixa (blocos) ou giros de
    multiplos de 90 graus em torno de eixos alinhados; para os demais e um limite superior."""
    corners = [(x, y, z) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1]) for z in (box[0][2], box[1][2])]
    rc = [rotate_point(c, origin, axis, angle_deg) for c in corners]
    return (tuple(min(c[i] for c in rc) for i in range(3)), tuple(max(c[i] for c in rc) for i in range(3)))


def boxes_close(a, b, tol: float = 0.05) -> bool:
    return a is not None and b is not None and all(abs(a[k][i] - b[k][i]) <= tol for k in range(2) for i in range(3))


def _read_box(runner, part: str):
    raw = runner(analysis.box_code(path_of(part)), 30)
    if not raw.get("ok") or not raw.get("result"):
        return None
    return analysis.parse_box(raw["result"])


def transform(runner, settle, part: str, steps: list[dict], tol: float = 0.05) -> dict:
    """Aplica passos em ordem: {'translate': [dx,dy,dz]} ou {'rotate': {'axis': 'z', 'origin': [..], 'angle': 90}}.

    Confere a caixa depois de cada passo. Translacao: caixa deslocada exatamente. Giro: compara com o resultado esperado
    nos dois sentidos (regra da mao direita e oposto) e informa qual casou ('sense'). Nunca altera o que nao foi pedido."""
    p = path_of(part)
    box = _read_box(runner, p)
    if box is None:
        return {"ok": False, "error": f"a peca {p} nao existe ou nao tem caixa (creo_get_model_tree)"}
    out: dict = {"ok": True, "part": p, "start_box": [list(box[0]), list(box[1])], "steps": []}
    for i, st in enumerate(steps):
        before = box
        entry: dict = {"i": i}
        try:
            if "translate" in st:
                d = [float(v) for v in st["translate"]]
                if len(d) != 3:
                    raise ValueError("translate precisa de [dx, dy, dz]")
                code = translate_cmd(p, d)
                expected = (tuple(before[0][k] + d[k] for k in range(3)), tuple(before[1][k] + d[k] for k in range(3)))
                entry["translate"] = d
                alts = {"exact": expected}
            elif "rotate" in st:
                r = st["rotate"]
                origin = [float(v) for v in r.get("origin", [0, 0, 0])]
                angle = float(r["angle"])
                code = rotate_cmd(p, origin, r.get("axis", "z"), angle)
                entry["rotate"] = {"axis": r.get("axis", "z"), "origin": origin, "angle": angle}
                alts = {"right_hand": expected_box_after_rotation(before, origin, r.get("axis", "z"), angle),
                        "left_hand": expected_box_after_rotation(before, origin, r.get("axis", "z"), -angle)}
            else:
                raise ValueError("cada passo precisa de 'translate' ou 'rotate'")
        except (ValueError, KeyError, TypeError) as exc:
            entry.update(ok=False, error=str(exc))
            out["steps"].append(entry)
            out["ok"] = False
            return out
        res = runner(code, 20)
        entry["dialogs"] = settle(3.0) or None
        if (not res.get("ok")) or (res.get("dialogs") and not res.get("note")):
            entry.update(ok=False, error=res.get("error") or "o Creo recusou o comando")
            out["steps"].append(entry)
            out["ok"] = False
            return out
        after = None
        for _ in range(8):                         # comando enfileirado: espera a caixa mudar
            after = _read_box(runner, p)
            if after is not None and not boxes_close(after, before, 1e-6):
                break
            time.sleep(0.6)
        entry["box"] = [list(after[0]), list(after[1])] if after else None
        matched = [k for k, b in alts.items() if boxes_close(after, b, tol)]
        if after is None:
            entry.update(ok=False, error="nao consegui reler a caixa depois do comando")
        elif boxes_close(after, before, 1e-6) and ("translate" not in st or any(abs(v) > 1e-9 for v in st["translate"])):
            entry.update(ok=False, error="a peca nao se moveu (o comando nao teve efeito)")
        elif "translate" in st:
            entry["ok"] = bool(matched)
            if not matched:
                entry["error"] = "a caixa depois do movimento nao e a esperada"
        else:
            entry["ok"] = True
            entry["sense"] = (matched[0] if matched else "nao conferido (solido nao preenche a caixa ou giro inclinado)")
        out["steps"].append(entry)
        if not entry["ok"]:
            out["ok"] = False
            return out
        box = after
    out["end_box"] = [list(box[0]), list(box[1])]
    return out


# ------------------------------------------------------------------ agrupar em montagem
def group_code(name: str, parts: list[str]) -> str:
    """Cria a montagem (sem barra no nome) e move as pecas para dentro. A posicao 3D nao muda."""
    n = name.strip().lstrip("/")
    if not n or "/" in n:
        raise ValueError("nome da montagem: letras/numeros/_ , sem barras")
    moves = "\n".join(f"  (when (oli:sd-pathname-to-obj {lisp_str(path_of(p))})\n"
                      f"    (change_pa_owner :new_owner {lisp_str('/' + n)} :parts_assemblies {lisp_str(path_of(p))}))"
                      for p in parts)
    return (f"(progn\n  (unless (oli:sd-pathname-to-obj {lisp_str('/' + n)}) (create_assembly :name {lisp_str(n)}))\n"
            f"{moves}\n  :ok)")


def group(runner, name: str, parts: list[str]) -> dict:
    try:
        code = group_code(name, parts)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    res = runner(code, 60)
    out: dict = {"ok": bool(res.get("ok")) and not res.get("dialogs"), "assembly": "/" + name.strip().lstrip("/"),
                 "lisp": code}
    if res.get("dialogs"):
        out["dialogs"] = res["dialogs"]
    if res.get("error"):
        out["error"] = res["error"]
    return out


# ------------------------------------------------------------------ cor
def color_code(parts: list[str], rgb) -> str:
    items = " ".join(lisp_str(path_of(p)) for p in parts)
    r, g, b = rgb
    return (
        "(mapcar #'(lambda (path)\n"
        "            (let ((o (oli:sd-pathname-to-obj path)))\n"
        "              (if o\n"
        "                  (progn\n"
        "                    (funcall (find-symbol \"SET_PART_COLOR\" \"ELAN\") o\n"
        f"                             :rgb (oli:make-gpnt3d :x {num(r)} :y {num(g)} :z {num(b)}) :done)\n"
        "                    (let ((c (oli:sd-inq-part-color o)))\n"
        "                      (list path (list (oli:gpnt3d_x c) (oli:gpnt3d_y c) (oli:gpnt3d_z c)))))\n"
        "                  (list path :missing))))\n"
        f"        (list {items}))"
    )


def color(runner, parts: list[str], rgb, tol: float = 0.01) -> dict:
    res = runner(color_code(parts, rgb), 60)
    out: dict = {"ok": False, "rgb": [round(v, 4) for v in rgb], "parts": {}}
    if res.get("dialogs"):
        out["dialogs"] = res["dialogs"]
    if not res.get("ok") or not res.get("result"):
        out["error"] = res.get("error") or "falha ao colorir"
        return out
    data = lisp.parse_sexp(res["result"])
    ok_all = True
    for entry in data if isinstance(data, list) else []:
        if not (isinstance(entry, list) and len(entry) == 2):
            continue
        path, got = str(entry[0]), entry[1]
        if isinstance(got, list) and len(got) == 3:
            good = all(abs(a - b) <= tol + 0.005 for a, b in zip(got, rgb))
            out["parts"][path] = {"read_back": [round(v, 4) for v in got], "ok": good}
            ok_all &= good
        else:
            out["parts"][path] = {"ok": False, "error": "peca nao encontrada" if str(got) == ":MISSING" else "cor nao lida"}
            ok_all = False
    out["ok"] = ok_all and bool(out["parts"])
    return out


# ------------------------------------------------------------------ espelhar (MIRROR_3D) e cortar (SECTION)
MIRROR_DIALOG = "MCP_MIRROR_OP"
SECTION_DIALOG = "MCP_SECTION_OP"
PLANES = {"x": ((1, 0, 0)), "y": ((0, 1, 0)), "z": ((0, 0, 1))}


def _plane(plane, offset: float, normal=None, point=None):
    """Ponto e normal do plano. plane: 'x'|'y'|'z' (normal = o eixo, passando por offset) ou 'custom' com point+normal."""
    if plane == "custom":
        if point is None or normal is None:
            raise ValueError("plane='custom' exige point e normal")
        pt = [float(v) for v in point]
        nv = [float(v) for v in normal]
        if len(pt) != 3 or len(nv) != 3 or all(abs(v) < 1e-12 for v in nv):
            raise ValueError("point e normal devem ter 3 valores (normal nao nula)")
        return pt, nv
    if plane not in PLANES:
        raise ValueError("plane deve ser 'x', 'y', 'z' ou 'custom'")
    n = list(PLANES[plane])
    pt = [0.0, 0.0, 0.0]
    pt["xyz".index(plane)] = float(offset)
    return pt, [float(v) for v in n]


def _gp(v) -> str:
    return f"(oli:make-gpnt3d :x {num(v[0])} :y {num(v[1])} :z {num(v[2])})"


def mirror_define() -> str:
    return (
        "(progn\n(defvar *mcp-mi-part* \"\")\n(defvar *mcp-mi-pt* nil)\n(defvar *mcp-mi-dir* nil)\n(defvar *mcp-mi-result* :unset)\n"
        "(oli:sd-defdialog 'mcp_mirror_op\n"
        "  :ok-action '(setf *mcp-mi-result*\n"
        "     (let ((part (oli:sd-pathname-to-obj *mcp-mi-part*)))\n"
        "       (if (null part)\n"
        "           :missing-part\n"
        "           (oli:sd-call-cmds (oli:mirror_3d :parts part :mirror_plane :pt_dir *mcp-mi-pt* *mcp-mi-dir*)\n"
        "                             :success :ok\n"
        "                             :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message)))))))\n"
        ":defined)"
    )


def mirror_setup(part: str, pt, normal) -> str:
    return (f"(progn (setf *mcp-mi-part* {lisp_str(path_of(part))}) (setf *mcp-mi-pt* {_gp(pt)}) "
            f"(setf *mcp-mi-dir* {_gp(normal)}) (setf *mcp-mi-result* :unset) :ready)")


def mirror_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(MIRROR_DIALOG + ' COMPLETE')})"


def reflect_box(box, plane: str, offset: float):
    """Caixa refletida em plano x|y|z = offset (so planos alinhados aos eixos)."""
    i = "xyz".index(plane)
    lo, hi = list(box[0]), list(box[1])
    nlo, nhi = 2 * offset - hi[i], 2 * offset - lo[i]
    lo[i], hi[i] = nlo, nhi
    return (tuple(lo), tuple(hi))


def mirror(runner, settle, part: str, plane: str = "x", offset: float = 0.0, point=None, normal=None) -> dict:
    """Espelha a peca EM LUGAR (a peca vira a imagem especular). Planos x/y/z sao conferidos pela caixa refletida."""
    p = path_of(part)
    try:
        pt, nv = _plane(plane, offset, normal, point)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    before = _read_box(runner, p)
    if before is None:
        return {"ok": False, "error": f"a peca {p} nao existe ou nao tem caixa"}
    r = run_dialog(runner, settle, mirror_define(), mirror_setup(p, pt, nv), mirror_command(), "*mcp-mi-result*")
    out: dict = {"ok": False, "part": p, "plane": plane, "offset": offset, "dialogs": r.get("dialogs")}
    if not r["ok"]:
        out["error"] = r.get("error")
        return out
    res = r["result"]
    if _fail(res):
        out["error"] = _fail(res)
        return out
    if str(res).upper() != ":OK":
        out["error"] = f"resposta inesperada do MIRROR_3D: {res!r}"
        return out
    after = None
    for _ in range(8):
        after = _read_box(runner, p)
        if after is not None:
            break
        time.sleep(0.5)
    out["box"] = [list(after[0]), list(after[1])] if after else None
    if plane in PLANES and after is not None:
        want = reflect_box(before, plane, offset)
        out["expected_box"] = [list(want[0]), list(want[1])]
        out["ok"] = boxes_close(after, want, 0.05)
        if not out["ok"]:
            out["error"] = "a caixa depois do espelhamento nao e a refletida esperada"
    else:
        out["ok"] = after is not None
        out["note"] = "plano custom: espelhamento enviado, caixa nao conferida analiticamente"
    return out


def section_define() -> str:
    return (
        "(progn\n(defvar *mcp-se-part* \"\")\n(defvar *mcp-se-pt* nil)\n(defvar *mcp-se-dir* nil)\n(defvar *mcp-se-result* :unset)\n"
        "(oli:sd-defdialog 'mcp_section_op\n"
        "  :ok-action '(setf *mcp-se-result*\n"
        "     (let ((part (oli:sd-pathname-to-obj *mcp-se-part*)))\n"
        "       (if (null part)\n"
        "           :missing-part\n"
        "           (oli:sd-call-cmds (oli:section :parts part :type :plane :sectionplane *mcp-se-pt* *mcp-se-dir*)\n"
        "                             :success :ok\n"
        "                             :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message)))))))\n"
        ":defined)"
    )


def section_setup(part: str, pt, normal) -> str:
    return (f"(progn (setf *mcp-se-part* {lisp_str(path_of(part))}) (setf *mcp-se-pt* {_gp(pt)}) "
            f"(setf *mcp-se-dir* {_gp(normal)}) (setf *mcp-se-result* :unset) :ready)")


def section_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(SECTION_DIALOG + ' COMPLETE')})"


def _drawlist_cmd(verb: str, part: str, viewport: str) -> str:
    text = verb + ' "' + viewport + '" "' + path_of(part) + '" COMPLETE'
    return "(oli:sd-put-buffer " + lisp_str(text) + ")"


def hide_cmd(part: str, viewport: str = "vport1") -> str:
    return _drawlist_cmd("REMOVE_FROM_VP_DRAWLIST", part, viewport)


def show_cmd(part: str, viewport: str = "vport1") -> str:
    return _drawlist_cmd("ADD_TO_VP_DRAWLIST", part, viewport)


def section(runner, settle, part: str, plane: str = "x", offset: float = 0.0, point=None, normal=None,
            hide_half: bool = False) -> dict:
    """Corta a peca por um plano. A metade nova ganha o sufixo `.1` (mesma montagem); `hide_half` oculta essa metade para
    ver o interior. Verifica que apareceu a peca `.1` e que as caixas das duas metades cobrem a original."""
    p = path_of(part)
    try:
        pt, nv = _plane(plane, offset, normal, point)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    before = _read_box(runner, p)
    if before is None:
        return {"ok": False, "error": f"a peca {p} nao existe ou nao tem caixa"}
    r = run_dialog(runner, settle, section_define(), section_setup(p, pt, nv), section_command(), "*mcp-se-result*")
    out: dict = {"ok": False, "part": p, "plane": plane, "offset": offset, "dialogs": r.get("dialogs")}
    if not r["ok"]:
        out["error"] = r.get("error")
        return out
    res = r["result"]
    if _fail(res):
        out["error"] = _fail(res)
        return out
    if str(res).upper() != ":OK":
        out["error"] = f"resposta inesperada do SECTION: {res!r}"
        return out
    half = p + ".1"
    for _ in range(8):
        a, b = _read_box(runner, p), _read_box(runner, half)
        if a is not None and b is not None:
            break
        time.sleep(0.6)
    else:
        a = b = None
    out["boxes"] = {p: [list(a[0]), list(a[1])] if a else None, half: [list(b[0]), list(b[1])] if b else None}
    if a is None or b is None:
        out["error"] = f"depois do corte nao encontrei as duas metades ({p} e {half})"
        return out
    union = analysis.union_box([a, b])
    out["covers_original"] = boxes_close(union, before, 0.05)
    out["ok"] = True
    if plane in PLANES:
        out["ok"] = bool(out["covers_original"])
        if not out["ok"]:
            out["error"] = "as caixas das duas metades nao cobrem a peca original"
    if hide_half and out["ok"]:
        hr = runner(hide_cmd(half), 20)
        out["hidden"] = half if hr.get("ok") else None
        settle(2.0)
    return out
