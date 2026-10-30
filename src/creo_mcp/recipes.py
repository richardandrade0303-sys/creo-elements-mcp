"""Receitas de modelagem: geram codigo Lisp para o Creo.

Este modulo e RECARREGADO a cada chamada de ferramenta (importlib.reload),
entao a sintaxe pode ser corrigida sem reiniciar o Claude Desktop.

Sintaxe baseada no Commands Reference do Integration Kit (PTC):
  EXTRUDE      :part :wp :distance :keep_profile :no    (ex. oficial: (extrude :part "/p1" :wp "/w1" :distance d))
  RECTANGLE    <posicao1> <posicao2>                    (ex.: RECTANGLE 100,50 150,150)
  CIRCLE       :cen_rad <centro> <raio>                 (ex.: CIRCLE :CEN_RAD 100,100 50)
  UNITE_3D     :blank :tools :keep_tools :yes|:no
  SUBTRACT_3D  :blanks :tools :keep_tool :yes|:no
  INTERSECT_3D :blanks :tools :keep_tool :yes|:no
  STL          :file <arquivo> :overwrite :parts_assemblies <pecas> :binary|:ascii
Nomes de pecas/workplanes sao strings entre aspas, ex.: "/p1", "/w1".
Posicoes sao literais `x,y` (2D) ou `x,y,z` (3D) lidos pelo reader do Creo.
"""

from __future__ import annotations

import importlib
import math

from . import dde as _dde, lisp as _lisp, winui as _winui

# Recarregar este modulo tambem recarrega a camada DDE/Lisp (correcoes entram sem reiniciar).
for _mod in (_dde, _winui, _lisp):
    importlib.reload(_mod)

from .lisp import lisp_path, lisp_str, num  # noqa: E402


def first_defined_call(names: list[str], package: str = "OLI") -> str:
    """Forma Lisp que chama a primeira funcao definida da lista (sem risco de erro de leitura)."""
    cands = " ".join(lisp_str(n) for n in names)
    return (
        f'(let ((found :undefined)) '
        f'(dolist (n (list {cands}) found) '
        f'(let ((s (and (find-package "{package}") (find-symbol n "{package}")))) '
        f'(when (and s (fboundp s)) (setf found (funcall s)) (return found)))))'
    )


# Nomes padrao da sessao nova do Creo (barra de status mostra /w1 e /p1).
DEFAULT_PART = "/p1"
DEFAULT_WP = "/w1"


def _part(part: str | None) -> str:
    return lisp_str(part or DEFAULT_PART)


def _wp(wp: str | None) -> str:
    return lisp_str(wp or DEFAULT_WP)


def _ensure_part(part: str | None) -> str:
    """Forma Lisp que cria a peca (vazia) se ainda nao existir - permite criar ferramentas para booleanas."""
    path = (part or DEFAULT_PART).strip()
    name = path.lstrip("/")
    if not name or "/" in name:  # so pecas de nivel raiz (ex.: "/tool")
        return ""
    return f"  (unless (oli:sd-pathname-to-obj {lisp_str('/' + name)}) (create_part :name {lisp_str(name)}))\n"


# IMPORTANTE: `extrude` chamado em Lisp MANTEM o perfil 2D no workplane (:keep_profile) e a proxima extrusao
# reaproveita o retangulo/circulo antigo (pecas com altura errada, "Overlapping loops" Error 291).
# Por isso sempre :keep_profile :no.
def _extrude(part: str | None, wp: str | None, distance: float) -> str:
    return f"(extrude :part {_part(part)} :wp {_wp(wp)} :distance {num(distance)} :keep_profile :no)"


def block(x: float, y: float, width: float, depth: float, height: float,
          part: str | None = None, wp: str | None = None) -> str:
    """Retangulo no workplane + extrusao (bloco)."""
    if width == 0 and depth == 0 and height == 0:  # ping: so recarrega os modulos, nao modela nada
        return "(+ 0 0)"
    return (
        f"(progn\n{_ensure_part(part)}"
        f"  (current_wp {_wp(wp)})\n"
        f"  (rectangle {num(x)},{num(y)} {num(x + width)},{num(y + depth)})\n"
        f"  {_extrude(part, wp, height)})"
    )


def cylinder(x: float, y: float, radius: float, height: float,
             part: str | None = None, wp: str | None = None) -> str:
    """Circulo no workplane + extrusao (cilindro)."""
    return (
        f"(progn\n{_ensure_part(part)}"
        f"  (current_wp {_wp(wp)})\n"
        f"  (circle :cen_rad {num(x)},{num(y)} {num(radius)})\n"
        f"  {_extrude(part, wp, height)})"
    )


def boolean(operation: str, blank: str, tool: str, keep_tool: bool = False) -> str:
    op = operation.lower()
    keep = ":yes" if keep_tool else ":no"
    if op == "unite":
        inner = f"(unite_3d :blank {lisp_str(blank)} :tools {lisp_str(tool)} :keep_tools {keep})"
    elif op == "subtract":
        inner = f"(subtract_3d :blanks {lisp_str(blank)} :tools {lisp_str(tool)} :keep_tool {keep})"
    elif op == "intersect":
        inner = f"(intersect_3d :blanks {lisp_str(blank)} :tools {lisp_str(tool)} :keep_tool {keep})"
    else:
        raise ValueError("operation deve ser 'unite', 'subtract' ou 'intersect'")
    return (
        f"(if (and (oli:sd-pathname-to-obj {lisp_str(blank)})\n"
        f"         (oli:sd-pathname-to-obj {lisp_str(tool)}))\n"
        f"    {inner}\n"
        f"    :missing-part)"
    )


def _q(text: str) -> str:
    """Aspas simples de linha de comando do Creo (nomes entre aspas duplas)."""
    return '"' + text.replace("\\", "/").replace('"', "") + '"'


def export_stl(file_path: str, part: str | None = None, binary: bool = True) -> str:
    """Exporta STL pelo texto de comando (`sd-put-buffer`), que e o jeito certo para comandos de entrada variavel.

    Chamar `(stl ...)` direto em Lisp deixa o comando aberto esperando COMPLETE; por texto,
    COMPLETE vai junto. O Creo ainda mostra 1 dialogo "Error 414" depois do export (o comando
    reinicia sem pecas) - o arquivo ja foi gravado; o servidor fecha esse dialogo.
    """
    parts = [p.strip() for p in (part or DEFAULT_PART).split(",") if p.strip()]
    cmd = f"STL :FILE {_q(file_path)} :OVERWRITE :PARTS_ASSEMBLIES {' '.join(_q(p) for p in parts)} "
    cmd += (":BINARY" if binary else ":ASCII") + " COMPLETE"
    return f"(oli:sd-put-buffer {lisp_str(cmd)})"


def send_command(text: str) -> str:
    """Enfileira texto como se fosse digitado na linha de comando do Creo (sd-put-buffer)."""
    return f"(oli:sd-put-buffer {lisp_str(text)})"


def cancel_command() -> str:
    return send_command("CANCEL")


def model_tree(depth: int = 4) -> str:
    """Arvore de objetos: lista de (caminho tipo filhos)."""
    return (
        "(labels ((kind (o) (cond ((oli:sd-inq-assembly-p o) :assembly) ((oli:sd-inq-part-p o) :part)\n"
        "                         ((oli:sd-inq-workplane-p o) :wp) (t :other)))\n"
        "         (walk (o d) (list (oli:sd-inq-obj-pathname o) (kind o)\n"
        f"                           (if (< d {int(depth)}) (mapcar #'(lambda (k) (walk k (+ d 1))) (oli:sd-inq-obj-children o)) nil))))\n"
        "  (mapcar #'(lambda (o) (walk o 1)) (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\"))))"
    )


def part_info(part: str | None = None) -> str:
    """Caixa envolvente (min/max em coordenadas globais) de uma peca."""
    return (
        f"(let ((p (oli:sd-pathname-to-obj {_part(part)})))\n"
        "  (if p (oli:sd-inq-exact-box p 0,0,1 1,0,0 :source-space :global :dest-space :global) :part-not-found))"
    )


# ------------------------------------------------------------ contornos e planos de trabalho
WZ = "wz"   # plano de trabalho auxiliar, movido para a altura z de cada passo


def loop_lisp(loop) -> str:
    """Forma Lisp que desenha um contorno 2D no workplane corrente."""
    kind = loop[0]
    if kind == "c":
        return f"(circle :cen_rad {num(loop[1])},{num(loop[2])} {num(loop[3])})"
    if kind == "r":
        return f"(rectangle {num(loop[1])},{num(loop[2])} {num(loop[3])},{num(loop[4])})"
    if kind == "p":
        pts = [f"{num(x)},{num(y)}" for x, y in loop[1]]
        rows = [" ".join(pts[i:i + 8]) for i in range(0, len(pts), 8)]
        return "(polygon\n    " + "\n    ".join(rows) + "\n    :close)"
    raise ValueError(f"contorno desconhecido: {kind!r}")


def _part_name(part: str) -> str:
    return part.strip().lstrip("/")


def _axis(axis: str | None) -> str:
    a = (axis or "z").lower()
    if a not in ("x", "y", "z"):
        raise ValueError(f"eixo invalido: {axis!r} (use 'x', 'y' ou 'z')")
    return a


# Plano de trabalho perpendicular a cada eixo global. Coordenadas dos contornos NO PLANO:
#   z: (x, y), normal +z    y: (x, z), normal -y    x: (y, z), normal +x
# `direction` das etapas e SEMPRE global: "+w" = no sentido +eixo, "-w" = no sentido -eixo
# (para o plano y a normal do workplane e -y, entao o sentido e invertido aqui).
PLANE_NORMAL = {"z": (0, 0, 1), "y": (0, -1, 0), "x": (1, 0, 0)}


def plane_points(axis: str, h: float):
    """Tres pontos (origem, ponta de u, ponta de v) do plano perpendicular a `axis` na posicao h."""
    a = _axis(axis)
    if a == "z":
        return (0, 0, h), (1, 0, h), (0, 1, h)
    if a == "y":
        return (0, h, 0), (1, h, 0), (0, h, 1)
    return (h, 0, 0), (h, 1, 0), (h, 0, 1)


def needs_wz(axis: str, z0: float) -> bool:
    """True se o passo precisa do plano auxiliar /wz (qualquer plano que nao seja o /w1)."""
    return _axis(axis) != "z" or abs(z0) > 1e-9


def sketch_extrude(part: str, loops: list, dz: float, direction: str = "+w", wp: str | None = None,
                   z0: float = 0.0, axis: str = "z") -> str:
    """Desenha `loops` no plano (/w1 ou /wz) e extruda `dz` para dentro de `part` (criada se nao existir)."""
    name = _part_name(part)
    a = _axis(axis)
    wp_path = "/" + WZ if needs_wz(a, z0) else (wp or DEFAULT_WP)
    minus_w = (direction == "-w") != (a == "y")          # plano y: normal = -y
    dir_kw = " :direction :-w" if minus_w else ""
    body = "\n  ".join(loop_lisp(lp) for lp in loops)
    return (
        f"(progn\n"
        f"  (unless (oli:sd-pathname-to-obj {lisp_str('/' + name)}) (create_part :name {lisp_str(name)}))\n"
        f"  (current_wp {lisp_str(wp_path)})\n"
        f"  {body}\n"
        f"  (extrude :part {lisp_str('/' + name)} :wp {lisp_str(wp_path)} :distance {num(dz)}{dir_kw} :keep_profile :no)\n"
        f"  (current_wp {lisp_str(DEFAULT_WP)})\n"
        f"  :ok)"
    )


def ensure_wp(name: str = WZ) -> str:
    return (f"(progn (unless (oli:sd-pathname-to-obj {lisp_str('/' + name)}) (create_workplane :new :name {lisp_str(name)}))"
            f" (list :wp {lisp_str('/' + name)}))")


def position_wp(z: float, name: str = WZ, axis: str = "z") -> str:
    """Move o plano de trabalho `name` para a posicao z do eixo `axis`. Entrada variavel: so por texto (assincrono)."""
    pts = " ".join(f"{num(x)},{num(y)},{num(zz)}" for x, y, zz in plane_points(axis, z))
    cmd = f'POSITION_WP "/{name}" :PT_PT_PT {pts} COMPLETE'
    return f"(oli:sd-put-buffer {lisp_str(cmd)})"


def wp_origin(name: str = WZ) -> str:
    return f"(oli:sd-inq-wp-origin (oli:sd-pathname-to-obj {lisp_str('/' + name)}))"


def wp_w_dir(name: str = WZ) -> str:
    """Normal do plano de trabalho (conferir a orientacao dos planos x e y)."""
    return f"(oli:sd-inq-wp-w-dir (oli:sd-pathname-to-obj {lisp_str('/' + name)}))"


def cut_with_loops(main: str, loops: list, dz: float, z0: float = 0.0, tool: str = "cuttool",
                   axis: str = "z", direction: str = "+w") -> str:
    """Peca-ferramenta extrudada dos contornos e subtraida de `main`; devolve :consumed ou :tool-left."""
    t = _part_name(tool)
    prefix = "/" + t
    n = len(prefix)
    # Contornos disjuntos (varios furos) viram varias pecas: /cuttool, /cuttool.1, ... -> subtrai TODAS.
    return (
        sketch_extrude(t, loops, dz, direction, None, z0, axis)[:-len("\n  :ok)")] + "\n"
        f"  (let ((pieces (remove-if-not\n"
        f"                  (lambda (n) (and (>= (length n) {n}) (string= (subseq n 0 {n}) {lisp_str(prefix)})))\n"
        f"                  (mapcar #'oli:sd-inq-obj-pathname\n"
        f"                          (remove-if-not #'oli:sd-inq-part-p\n"
        f"                                         (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\")))))))\n"
        f"    (dolist (n pieces)\n"
        f"      (when (oli:sd-pathname-to-obj n)\n"
        f"        (subtract_3d :blanks {lisp_str('/' + _part_name(main))} :tools n :keep_tool :no)))\n"
        f"    (let ((left (remove-if-not #'oli:sd-pathname-to-obj pieces)))\n"
        f"      (if left\n"
        f"          (progn (dolist (n left) (delete_3d n)) :tool-left)\n"
        f"          :consumed))))"
    )


# ------------------------------------------------------------ helice (mola, rosca)
# HELIX_ADD/HELIX_REM sao obsoletos no Express 9 (abrem dialogo de erro); o comando valido e HELIX_DIA, com
# `:remove_material` para cortar. Verificado ao vivo: o perfil (desenhado em /wz, plano que CONTEM o eixo) varre `turns`
# voltas de passo `pitch` em torno do eixo z (sentido direito, anti-horario subindo); o perfil FICA no workplane depois
# do comando, entao ele e extrudado numa peca descartavel (/hjunk, :keep_profile :no) e apagada, senao o proximo
# extrude o levaria junto.
HELIX_JUNK = "hjunk"


def loops_z_extent(loops) -> float:
    """Extensao do perfil ao longo da 2a coordenada (z no plano y=0) - para validar o passo da helice."""
    zs = []
    for lp in loops:
        if lp[0] == "c":
            zs += [lp[2] - lp[3], lp[2] + lp[3]]
        elif lp[0] == "r":
            zs += [lp[2], lp[4]]
        elif lp[0] == "p":
            zs += [p[1] for p in lp[1]]
        else:
            raise ValueError(f"contorno desconhecido: {lp[0]!r}")
    return (max(zs) - min(zs)) if zs else 0.0


def helix_check(loops, pitch: float, turns: float) -> None:
    """Recusa uma helice que o Creo nao consegue varrer. Verificado ao vivo: passo menor que a altura do perfil
    (por exemplo 0,01 mm para 'imitar' uma revolucao) da 'Sweep body contains self-intersecting surfaces' e o Creo
    fica reabrindo o dialogo em laco ate fechar sozinho."""
    ext = loops_z_extent(loops)
    if pitch <= 0 or turns <= 0:
        raise ValueError("pitch e turns da helice devem ser > 0")
    if pitch < ext * 1.02:
        raise ValueError(f"passo {pitch:g} menor que a altura do perfil ({ext:g} mm): a varredura se auto-intersecta "
                         "(o Creo entra em laco de erro e pode fechar). Use passo >= 1,02 x altura do perfil.")


def helix_sweep(part: str, loops: list, pitch: float, turns: float, remove: bool = False,
                wp: str | None = None) -> str:
    """Desenha `loops` (plano de /wz que contem o eixo z, coordenadas (x, z)), varre-os em helice em torno do eixo z
    (adicionando material a `part` ou, com remove=True, cortando-o) e limpa o perfil que sobra no workplane."""
    helix_check(loops, pitch, turns)
    name = _part_name(part)
    path = "/" + name
    wp_path = wp or ("/" + WZ)
    junk = "/" + HELIX_JUNK
    n = len(junk)
    body = "\n  ".join(loop_lisp(lp) for lp in loops)
    if remove:
        prep = (f"  (unless (oli:sd-pathname-to-obj {lisp_str(path)})\n"
                f"    (error \"peca {name} nao existe: a rosca corta uma peca existente\"))\n")
    else:
        prep = f"  (unless (oli:sd-pathname-to-obj {lisp_str(path)}) (create_part :name {lisp_str(name)}))\n"
    rm = " :remove_material" if remove else ""
    return (
        f"(progn\n"
        f"{prep}"
        f"  (current_wp {lisp_str(wp_path)})\n"
        f"  {body}\n"
        f"  (helix_dia{rm} :blank_part {lisp_str(path)} :wp {lisp_str(wp_path)} :axis :two_pta 0,0,0 0,0,10"
        f" :pitch {num(pitch)} :turns {num(turns)})\n"
        f"  (unless (oli:sd-pathname-to-obj {lisp_str(junk)}) (create_part :name {lisp_str(HELIX_JUNK)}))\n"
        f"  (extrude :part {lisp_str(junk)} :wp {lisp_str(wp_path)} :distance 1 :keep_profile :no)\n"
        f"  (dolist (n (remove-if-not\n"
        f"               (lambda (n) (and (>= (length n) {n}) (string= (subseq n 0 {n}) {lisp_str(junk)})))\n"
        f"               (mapcar #'oli:sd-inq-obj-pathname\n"
        f"                       (remove-if-not #'oli:sd-inq-part-p\n"
        f"                                      (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\"))))))\n"
        f"    (when (oli:sd-pathname-to-obj n) (delete_3d n)))\n"
        f"  (current_wp {lisp_str(DEFAULT_WP)})\n"
        f"  :ok)"
    )


# ------------------------------------------------------------ chanfro e filete (arestas)
# CHAMFER/BLEND pedem arestas selecionadas. `get_selection` so roda numa acao de usuario, entao um dialogo
# (sd-defdialog) lista as arestas da peca, filtra por um predicado e chama o comando dentro de `sd-call-cmds`
# (erro volta como texto e nunca fica interativo). Verificado ao vivo: chanfro 2 mm e filete R3 nas 4 arestas do topo
# de um bloco 40x40x20 -> volumes do STL iguais ao calculo analitico.
EDGE_DIALOG = "MCP_EDGE_OP2"
EDGE_UNSET = ":UNSET"


def edge_pred_lisp(selectors) -> str:
    """lambda (x1 y1 z1 x2 y2 z2) -> T se a aresta (pelos extremos) casa com algum seletor.

    Seletores: ("all",) | ("z", nivel) arestas no plano z=nivel | ("zr", nivel, rmax) idem, dentro do raio rmax do
    eixo z | ("zr", nivel, rmax, rmin) idem, num anel rmin <= r <= rmax | ("vertical",) ou ("vertical", rmin)
    arestas paralelas a z a distancia >= rmin do eixo z.
    """
    conds = []
    for sel in selectors:
        kind = sel[0]
        if kind == "all":
            conds.append("t")
        elif kind in ("z", "zr"):
            lvl = num(sel[1])
            c = f"(< (abs (- z1 {lvl})) 0.01) (< (abs (- z2 {lvl})) 0.01)"
            if kind == "zr":
                r2 = num(float(sel[2]) ** 2)
                c += f" (<= (+ (* x1 x1) (* y1 y1)) {r2}) (<= (+ (* x2 x2) (* y2 y2)) {r2})"
                if len(sel) > 3 and float(sel[3]) > 0:
                    q2 = num(float(sel[3]) ** 2)
                    c += f" (>= (+ (* x1 x1) (* y1 y1)) {q2}) (>= (+ (* x2 x2) (* y2 y2)) {q2})"
            conds.append(f"(and {c})")
        elif kind == "zrc":                        # ("zrc", nivel, cx, cy, rmax[, rmin]): anel em torno de (cx, cy)
            if len(sel) < 5:
                raise ValueError("seletor 'zrc' precisa de nivel, cx, cy, rmax [, rmin]")
            lvl, cx, cy = num(sel[1]), num(float(sel[2])), num(float(sel[3]))
            r2 = num(float(sel[4]) ** 2)
            d1 = f"(+ (* (- x1 {cx}) (- x1 {cx})) (* (- y1 {cy}) (- y1 {cy})))"
            d2 = f"(+ (* (- x2 {cx}) (- x2 {cx})) (* (- y2 {cy}) (- y2 {cy})))"
            c = (f"(< (abs (- z1 {lvl})) 0.01) (< (abs (- z2 {lvl})) 0.01) (<= {d1} {r2}) (<= {d2} {r2})")
            if len(sel) > 5 and float(sel[5]) > 0:
                q2 = num(float(sel[5]) ** 2)
                c += f" (>= {d1} {q2}) (>= {d2} {q2})"
            conds.append(f"(and {c})")
        elif kind == "edge":                       # uma aresta pelos dois extremos (em qualquer ordem)
            a = [num(float(v)) for v in sel[1:7]]
            if len(a) != 6:
                raise ValueError("seletor 'edge' precisa de x1,y1,z1,x2,y2,z2")

            def near(v, c):
                return f"(< (abs (- {v} {c})) 0.01)"
            fwd = " ".join(near(v, c) for v, c in zip(("x1", "y1", "z1", "x2", "y2", "z2"), a))
            rev = " ".join(near(v, c) for v, c in zip(("x1", "y1", "z1", "x2", "y2", "z2"), a[3:] + a[:3]))
            conds.append(f"(or (and {fwd}) (and {rev}))")
        elif kind == "vertical":
            c = "(< (abs (- x1 x2)) 0.01) (< (abs (- y1 y2)) 0.01) (> (abs (- z1 z2)) 0.01)"
            if len(sel) > 1:
                c += f" (>= (+ (* x1 x1) (* y1 y1)) {num(float(sel[1]) ** 2)})"
            conds.append(f"(and {c})")
        else:
            raise ValueError(f"seletor de aresta desconhecido: {kind!r}")
    if not conds:
        raise ValueError("informe ao menos um seletor de aresta")
    return ("(lambda (x1 y1 z1 x2 y2 z2) (declare (ignorable x1 y1 z1 x2 y2 z2)) (or " + " ".join(conds) + "))")


def edge_dialog_define() -> str:
    """Define as variaveis globais e o dialogo MCP_EDGE_OP (idempotente)."""
    return (
        "(progn\n"
        "(defvar *mcp-eo-mode* :chamfer)\n(defvar *mcp-eo-size* 2.0)\n(defvar *mcp-eo-size2* 0.0)\n(defvar *mcp-eo-pred* nil)\n"
        "(defvar *mcp-eo-result* :unset)\n(defvar *mcp-eo-part* \"\")\n"
        "(oli:sd-defdialog 'mcp_edge_op2\n"
        "  :ok-action '(setf *mcp-eo-result*\n"
        "     (let* ((part-item (oli:sd-pathname-to-obj *mcp-eo-part*))\n"
        "            (edges (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-edge-3d-seltype*\n"
        "                                                        :select :selected_part part-item :all_3d)))\n"
        "            (sel (remove-if-not\n"
        "                   (lambda (e)\n"
        "                     (let* ((g (oli:sd-inq-edge-geo e :dest-space :global))\n"
        "                            (s (oli:sd-edge-start-pnt g))\n"
        "                            (en (oli:sd-edge-end-pnt g)))\n"
        "                       (or (null *mcp-eo-pred*)\n"
        "                           (funcall *mcp-eo-pred* (oli:gpnt3d_x s) (oli:gpnt3d_y s) (oli:gpnt3d_z s)\n"
        "                                                  (oli:gpnt3d_x en) (oli:gpnt3d_y en) (oli:gpnt3d_z en)))))\n"
        "                   edges)))\n"
        "       (list (length edges) (length sel)\n"
        "             (if (null sel)\n"
        "                 :no-edges\n"
        "                 (oli:sd-call-cmds\n"
        "                   (case *mcp-eo-mode*\n"
        "                     (:blend (oli:blend :const_radius sel :new_radius *mcp-eo-size*))\n"
        "                     (:chamfer2 (oli:chamfer :dist_dist sel :new_distance1 *mcp-eo-size* :new_distance2 *mcp-eo-size2*))\n"
        "                     (:chamfer_angle (oli:chamfer :dist_angle sel :new_angle_dist *mcp-eo-size* :new_angle *mcp-eo-size2*))\n"
        "                     (t (oli:chamfer :distance sel :new_distance *mcp-eo-size*)))\n"
        "                   :success :ok\n"
        "                   :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))))))\n"
        ":defined)"
    )


EDGE_MODES = ("chamfer", "blend", "chamfer2", "chamfer_angle")


def _fl(v) -> str:
    t = num(v)
    return t if "." in t else t + ".0"                  # literal float (3 -> 3.0)


def edge_op_setup(mode: str, size: float, selectors, part: str, size2: float = 0.0) -> str:
    """Prepara a operacao: mode 'chamfer' | 'blend' | 'chamfer2' (duas distancias: size e size2) |
    'chamfer_angle' (distancia `size` + angulo `size2` em graus), seletores e peca; zera o resultado.

    A peca vai por variavel global: o dialogo NAO usa variavel :part, que ignora o nome digitado e usa a peca
    CORRENTE do Creo (descoberto ao vivo: depois de apagar a ferramenta do furo nao havia peca corrente).
    """
    if mode not in EDGE_MODES:
        raise ValueError(f"modo invalido: {mode!r} (use {', '.join(EDGE_MODES)})")
    if not size or float(size) <= 0:
        raise ValueError("o tamanho do chanfro/filete deve ser > 0")
    if mode in ("chamfer2", "chamfer_angle") and (not size2 or float(size2) <= 0):
        raise ValueError("chanfro assimetrico: informe size2 > 0 (segunda distancia ou angulo em graus)")
    if mode == "chamfer_angle" and float(size2) >= 90:
        raise ValueError("o angulo do chanfro deve ser < 90 graus")
    second = math.radians(float(size2)) if mode == "chamfer_angle" else (size2 or 0)    # o Creo recebe o angulo em RADIANOS
    return (f"(progn (setf *mcp-eo-part* {lisp_str('/' + _part_name(part))}) "
            f"(setf *mcp-eo-mode* :{mode}) (setf *mcp-eo-size* {_fl(size)}) (setf *mcp-eo-size2* {_fl(second)}) "
            f"(setf *mcp-eo-pred* {edge_pred_lisp(selectors)}) (setf *mcp-eo-result* :unset) :ready)")


LIST_DIALOG = "MCP_EDGE_LIST"


def edge_list_define() -> str:
    """Dialogo de diagnostico: guarda em *mcp-el-result* os extremos (x1 y1 z1 x2 y2 z2, arredondados) de cada
    aresta da peca *mcp-eo-part* (get_selection so roda em acao de usuario)."""
    return (
        "(progn\n(defvar *mcp-el-result* :unset)\n(defvar *mcp-eo-part* \"\")\n"
        "(oli:sd-defdialog 'mcp_edge_list\n"
        "  :ok-action '(setf *mcp-el-result*\n"
        "     (let* ((part-item (oli:sd-pathname-to-obj *mcp-eo-part*))\n"
        "            (edges (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-edge-3d-seltype*\n"
        "                                                        :select :selected_part part-item :all_3d)))\n"
        "            (rd (lambda (v) (/ (fround (* v 100.0)) 100.0))))\n"
        "       (mapcar (lambda (e)\n"
        "                 (let* ((g (oli:sd-inq-edge-geo e :dest-space :global))\n"
        "                        (s (oli:sd-edge-start-pnt g)) (en (oli:sd-edge-end-pnt g)))\n"
        "                   (list (funcall rd (oli:gpnt3d_x s)) (funcall rd (oli:gpnt3d_y s)) (funcall rd (oli:gpnt3d_z s))\n"
        "                         (funcall rd (oli:gpnt3d_x en)) (funcall rd (oli:gpnt3d_y en)) (funcall rd (oli:gpnt3d_z en)))))\n"
        "               edges))))\n"
        ":defined)"
    )


def edge_list_setup(part: str) -> str:
    return (f"(progn (setf *mcp-eo-part* {lisp_str('/' + _part_name(part))}) (setf *mcp-el-result* :unset) :ready)")


def edge_list_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(LIST_DIALOG + ' COMPLETE')})"


# ------------------------------------------------------------------ casca (SHELL_3D) e lista de faces
SHELL_DIALOG = "MCP_SHELL_OP"
FACE_LIST_DIALOG = "MCP_FACE_LIST"


def face_pred_lisp(selectors) -> str:
    """lambda (x1 y1 z1 x2 y2 z2) sobre a CAIXA da face (cantos ll/ur globais) -> T se casa com algum seletor.

    Seletores: ("face_z", nivel) | ("face_x", nivel) | ("face_y", nivel): face plana no plano coordenado dado."""
    conds = []
    axes = {"face_x": ("x1", "x2"), "face_y": ("y1", "y2"), "face_z": ("z1", "z2")}
    for sel in selectors:
        kind = sel[0]
        if kind not in axes:
            raise ValueError(f"seletor de face desconhecido: {kind!r} (use face_x, face_y ou face_z)")
        if len(sel) < 2:
            raise ValueError(f"seletor {kind} precisa de um nivel")
        v = num(float(sel[1]))
        a, b = axes[kind]
        conds.append(f"(and (< (abs (- {a} {v})) 0.01) (< (abs (- {b} {v})) 0.01))")
    if not conds:
        raise ValueError("informe ao menos um seletor de face")
    return "(lambda (x1 y1 z1 x2 y2 z2) (declare (ignorable x1 y1 z1 x2 y2 z2)) (or " + " ".join(conds) + "))"


def shell_dialog_define() -> str:
    """Dialogo MCP_SHELL_OP: seleciona as faces abertas por predicado e chama SHELL_3D (offset > 0 = para dentro)."""
    return (
        "(progn\n"
        "(defvar *mcp-sh-result* :unset)\n(defvar *mcp-sh-offset* 3.0)\n(defvar *mcp-sh-pred* nil)\n"
        "(defvar *mcp-eo-part* \"\")\n"
        "(oli:sd-defdialog 'mcp_shell_op\n"
        "  :ok-action '(setf *mcp-sh-result*\n"
        "     (let* ((part-item (oli:sd-pathname-to-obj *mcp-eo-part*))\n"
        "            (faces (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-face-seltype*\n"
        "                                                        :select :selected_part part-item :all_3d)))\n"
        "            (open (remove-if-not\n"
        "                    (lambda (f)\n"
        "                      (let* ((g (oli:sd-inq-face-geo f :dest-space :global))\n"
        "                             (a (oli:sd-face-ll-pnt g)) (b (oli:sd-face-ur-pnt g)))\n"
        "                        (funcall *mcp-sh-pred* (oli:gpnt3d_x a) (oli:gpnt3d_y a) (oli:gpnt3d_z a)\n"
        "                                               (oli:gpnt3d_x b) (oli:gpnt3d_y b) (oli:gpnt3d_z b))))\n"
        "                    faces)))\n"
        "       (list (length faces) (length open)\n"
        "             (if (null open)\n"
        "                 :no-faces\n"
        "                 (oli:sd-call-cmds\n"
        "                   (oli:shell_3d :select_part part-item :offset *mcp-sh-offset* :open_face open)\n"
        "                   :success :ok\n"
        "                   :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message))))))))\n"
        ":defined)"
    )


def shell_op_setup(thickness: float, selectors, part: str) -> str:
    if not thickness or float(thickness) <= 0:
        raise ValueError("a espessura da parede deve ser > 0")
    return (f"(progn (setf *mcp-eo-part* {lisp_str('/' + _part_name(part))}) (setf *mcp-sh-offset* {_fl(thickness)}) "
            f"(setf *mcp-sh-pred* {face_pred_lisp(selectors)}) (setf *mcp-sh-result* :unset) :ready)")


def shell_op_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(SHELL_DIALOG + ' COMPLETE')})"


def shell_op_result() -> str:
    return "*mcp-sh-result*"


def face_list_define() -> str:
    """Diagnostico: caixa (ll/ur globais, arredondada) de cada face da peca *mcp-eo-part* em *mcp-fl-result*."""
    return (
        "(progn\n(defvar *mcp-fl-result* :unset)\n(defvar *mcp-eo-part* \"\")\n"
        "(oli:sd-defdialog 'mcp_face_list\n"
        "  :ok-action '(setf *mcp-fl-result*\n"
        "     (let* ((part-item (oli:sd-pathname-to-obj *mcp-eo-part*))\n"
        "            (faces (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-face-seltype*\n"
        "                                                        :select :selected_part part-item :all_3d)))\n"
        "            (rd (lambda (v) (/ (fround (* v 100.0)) 100.0))))\n"
        "       (mapcar (lambda (f)\n"
        "                 (let* ((g (oli:sd-inq-face-geo f :dest-space :global))\n"
        "                        (a (oli:sd-face-ll-pnt g)) (b (oli:sd-face-ur-pnt g)))\n"
        "                   (list (funcall rd (oli:gpnt3d_x a)) (funcall rd (oli:gpnt3d_y a)) (funcall rd (oli:gpnt3d_z a))\n"
        "                         (funcall rd (oli:gpnt3d_x b)) (funcall rd (oli:gpnt3d_y b)) (funcall rd (oli:gpnt3d_z b)))))\n"
        "               faces))))\n"
        ":defined)"
    )


def face_list_setup(part: str) -> str:
    return f"(progn (setf *mcp-eo-part* {lisp_str('/' + _part_name(part))}) (setf *mcp-fl-result* :unset) :ready)"


def face_list_command() -> str:
    return f"(oli:sd-put-buffer {lisp_str(FACE_LIST_DIALOG + ' COMPLETE')})"


def edge_op_command() -> str:
    """Executa o dialogo por texto de comando (assincrono): o resultado fica em *mcp-eo-result*."""
    return f"(oli:sd-put-buffer {lisp_str(EDGE_DIALOG + ' COMPLETE')})"


def edge_op_result() -> str:
    return "*mcp-eo-result*"


def object_paths() -> str:
    return "(mapcar #'oli:sd-inq-obj-pathname (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\")))"


def clear_scene(keep_workplanes: bool = True) -> str:
    """Apaga todas as pecas de nivel raiz (os workplanes ficam)."""
    return (
        "(progn\n"
        "  (dolist (n (mapcar #'oli:sd-inq-obj-pathname\n"
        "                     (remove-if-not #'oli:sd-inq-part-p (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\")))))\n"
        "    (delete_3d n))\n"
        "  (mapcar #'oli:sd-inq-obj-pathname (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\"))))"
    )
