"""Orquestracao ao vivo das ferramentas novas: leitura da cena, analise, exportacao, lista de materiais, operacoes tipadas.

Tudo recebe um `Env` (a funcao `run(code, timeout)` do servidor, `settle(seconds, until)` e, opcionalmente, pids/screenshot)
e devolve um dict com `ok`. Nenhuma funcao aqui imprime estruturas internas do Creo nem le propriedades de simbolos
(regra 13 da skill: isso derruba o Creo com 0xc00000fd).

Recarregado a quente (como builder.py). Estado so em arquivos de ~/.creo_mcp.
"""

from __future__ import annotations

import json
import os
import re
import time

from . import (analysis, builder, capabilities, config, exchange, health, journal, lease, lisp, mesh, ops, recipes,
               registry, scene, snapshot, stl)
from .parts import PartError

EXPORT_DIR = os.path.join(config.HOME, "exports")
BOM_STORE = os.path.join(config.HOME, "bom_lines.json")
SYNTAX_FILE = os.path.join(config.HOME, "command_syntax.json")
MAX_CLASH_PAIRS = 24
MAX_CLASH_PARTS = 16


class Env:
    """O que as acoes precisam do servidor (e o que os testes trocam por um falso)."""

    def __init__(self, run, settle, sleep=time.sleep, pids=None, shot=None):
        self.run = run
        self.settle = settle
        self.sleep = sleep
        self.pids = pids or (lambda: [])
        self.shot = shot


# ------------------------------------------------------------------ leitura da cena
def norm(path: str) -> str:
    return "/" + str(path).strip().lstrip("/")


def norm_list(value) -> list[str]:
    """Aceita lista, texto 'a,b' ou JSON '["a","b"]'. Devolve caminhos normalizados, sem repeticao."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("["):
            try:
                value = json.loads(v)
            except ValueError:
                value = [v]
        else:
            value = [x for x in re.split(r"[,;]", v)]
    out: list[str] = []
    for x in value:
        p = norm(str(x))
        if p != "/" and p not in out:
            out.append(p)
    return out


def read_tree(env: Env, depth: int = 6):
    """Arvore do modelo: lista (vazia se a cena nao tem nada) ou None se nao foi possivel ler."""
    res = env.run(recipes.model_tree(depth), 30)
    if not res.get("ok"):
        return None
    tree = lisp.parse_sexp(res["result"]) if res.get("result") else None
    return tree if isinstance(tree, list) else []


VOL_BATCH = 1            # volumes: uma peca por dialogo (leituras em lote ja derrubaram o Creo)
SCOPE_PREFIX: str | None = None     # creo_validate: so enxerga pecas /mcp_exp_* (nunca le as pecas do usuario)


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def read_boxes(env: Env, paths: list[str]):
    """{caminho: ((x0,y0,z0),(x1,y1,z1)) | None} ou None se a leitura falhou. UMA peca por chamada ao Creo (a leitura
    em lote com mapcar + ignore-errors derrubou o Creo 4 vezes)."""
    if not paths:
        return {}
    out: dict = {}
    for path in paths:
        res = env.run(analysis.box_code(path), 20)
        if not res.get("ok") or not res.get("result"):
            return None
        out[path] = analysis.parse_box(res["result"])
    return out


def read_volumes(env: Env, paths: list[str]):
    """({caminho: {'volume_mm3','area_mm2'} | None}, detalhes). Mede por GET_VOL_PROP (analitico, sem STL), em lotes."""
    if not paths:
        return {}, {"ok": True}
    merged: dict = {}
    last: dict = {"ok": True}
    for chunk in _chunks(list(paths), VOL_BATCH):
        r = ops.run_dialog(env.run, env.settle, analysis.vol_dialog_define(), analysis.vol_setup(chunk),
                           analysis.vol_command(), analysis.vol_result_code())
        if not r.get("ok"):
            return None, r
        merged.update(analysis.parse_vol(r["result"]))
        last = r
    return merged, last


def _no_scene(what: str = "") -> dict:
    return {"ok": False, "error": "nao consegui ler a cena (o Creo responde? veja creo_get_status)" + (f" {what}" if what else "")}


def _all_parts(tree) -> list[str]:
    found = scene.parts_of(tree)
    if SCOPE_PREFIX:
        found = [p for p in found if p.startswith(SCOPE_PREFIX)]
    return found


# ------------------------------------------------------------------ nivel 0
def budget(env: Env, planned: int = 0) -> dict:
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    out = scene.budget(tree, int(planned))          # 'ok' = cabe no limite (usadas + previstas <= limite, se houver)
    out["parts"] = _all_parts(tree)
    out["leftovers"] = scene.leftovers(tree)
    out["advice"] = ("" if out["ok"] else
                     "passa do limite configurado (CREO_MCP_PART_LIMIT): remova pecas, divida o projeto ou aumente/zere o limite")
    return out


def capabilities_report(env: Env, tool_names: list[str], probe: bool = True) -> dict:
    parsed = None
    if probe:
        res = env.run(capabilities.probe_code(), 60)
        if res.get("ok") and res.get("result"):
            parsed = capabilities.parse(res["result"])
        else:
            parsed = {"error": res.get("error") or "sondagem falhou", "dialogs": res.get("dialogs")}
    out = capabilities.describe(parsed if parsed and "error" not in parsed else None, tool_names)
    if parsed and "error" in parsed:
        out["probe_error"] = parsed
    out["ok"] = True
    return out


def units_report(env: Env) -> dict:
    res = env.run(capabilities.probe_code(units_only=True), 30)
    if not res.get("ok") or not res.get("result"):
        return {"ok": False, "error": res.get("error") or "nao consegui ler as unidades", "dialogs": res.get("dialogs")}
    parsed = capabilities.parse(res["result"])
    warns = parsed.get("unit_warnings") or []
    return {"ok": not warns, "units": parsed.get("units"), "lisp_version": parsed.get("lisp_version"),
            "assumed": {"length": "mm", "mass": "g"}, "warnings": warns,
            "advice": ("Mude as unidades do Creo para mm e g (menu Units) antes de modelar" if warns else "unidades conferem")}


def health_report(env: Env, tool_names: list[str] | None = None) -> dict:
    from . import __version__
    from .state import SESSION_ID

    pids = env.pids()
    health.observe_pids(pids)
    st = health.status()
    out = {"ok": st["state"] == "ok", "server_version": __version__, "session": SESSION_ID, "health": st,
           "policy": config.policy_summary(), "lease": lease.status() if config.lease_enabled() else {"enabled": False},
           "recent_incidents": health.recent_incidents(5), "journal_today": len(journal.entries(0, days=1))}
    return out


def _delete_code(paths: list[str]) -> str:
    items = " ".join(lisp.lisp_str(p) for p in paths)
    return (f"(progn (dolist (n (list {items})) (when (oli:sd-pathname-to-obj n) (delete_3d n))) "
            "(mapcar #'oli:sd-inq-obj-pathname (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\"))))")


def delete_objects(env: Env, tree, paths: list[str]) -> dict:
    """Apaga objetos de topo (pecas; montagens: tenta a montagem e, se sobrar, apaga as pecas de dentro)."""
    kinds = {n["path"]: n["kind"] for n in scene.flatten(tree)}
    res = env.run(_delete_code(paths), 90)
    after = read_tree(env, 6)
    left = [p for p in paths if after is not None and any(n["path"] == p for n in scene.flatten(after))]
    for p in list(left):
        if kinds.get(p) == "ASSEMBLY":
            inner = [n["path"] for n in scene.flatten(after) if n["parent"] == p or n["path"].startswith(p + "/")]
            inner = [q for q in inner if kinds.get(q) == "PART" or any(n["path"] == q and n["kind"] == "PART" for n in scene.flatten(after))]
            if inner:
                env.run(_delete_code(inner), 90)
    after = read_tree(env, 6)
    still = [p for p in paths if after is not None and any(n["path"] == p for n in scene.flatten(after))]
    return {"requested": paths, "remaining": still, "deleted": [p for p in paths if p not in still],
            "dialogs": res.get("dialogs") or None, "tree_after": after}


def clear_scene(env: Env, scope: str = "mine", confirm: bool = False, snapshot_first: bool = True) -> dict:
    """Limpeza segura. scope='mine' apaga so o que ESTE servidor criou; 'all' apaga todas as pecas e montagens. Sem
    confirm=True nada e apagado (devolve o plano). Antes de apagar salva um snapshot automatico."""
    if scope not in ("mine", "all"):
        return {"ok": False, "error": "scope deve ser 'mine' ou 'all'"}
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    top = scene.top_level(tree)
    existing = [n["path"] for n in top]
    kinds = {n["path"]: n["kind"] for n in top}
    plan = registry.plan_clear(existing, True, kinds)
    if scope == "all":
        deletable = [p for p in existing if kinds.get(p) in ("PART", "ASSEMBLY")]
        protected: list[str] = []
    else:
        deletable, protected = plan["deletable"], plan["protected"]
    out: dict = {"ok": True, "scope": scope, "would_delete": deletable, "protected": protected,
                 "workplanes_kept": [p for p in existing if kinds.get(p) in ("WP", "OTHER")]}
    if not confirm:
        out["dry_run"] = True
        out["message"] = ("Nada foi apagado. Repita com confirm=true para apagar a lista 'would_delete' (um snapshot "
                          "automatico e salvo antes)." + (" 'protected' sao objetos que este servidor nao criou: use "
                                                          "scope='all' so se tiver certeza." if protected else ""))
        return out
    if not deletable:
        out["message"] = "nada a apagar"
        return out
    if snapshot_first and scene.count_bodies(tree) > 0:
        bk = snapshot.create(env.run, "antes_de_limpar", auto=True)
        out["snapshot"] = {k: bk.get(k) for k in ("ok", "path", "error") if bk.get(k) is not None}
        if not bk.get("ok"):
            out.update(ok=False, error="nao consegui salvar o snapshot antes de apagar; nada foi apagado "
                                       "(snapshot_first=false ignora, por sua conta)")
            return out
    res = delete_objects(env, tree, deletable)
    registry.forget(res["deleted"])
    out.update(deleted=res["deleted"], remaining=res["remaining"], dialogs=res["dialogs"],
               objects_now=[n["path"] for n in scene.top_level(res["tree_after"])] if res["tree_after"] else None)
    out["ok"] = not res["remaining"]
    if res["remaining"]:
        out["error"] = f"nao consegui apagar: {res['remaining']}"
    return out


# ------------------------------------------------------------------ nivel 1: analise
def audit(env: Env, with_volumes: bool = False) -> dict:
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    paths = _all_parts(tree)
    boxes = read_boxes(env, paths) if paths else {}
    vols = None
    if with_volumes and paths:
        vols, _d = read_volumes(env, paths)
    out = analysis.audit(tree, boxes, vols)
    out["assemblies"] = scene.assemblies_of(tree)
    out["boxes_read"] = boxes is not None
    return out


def measure(env: Env, a: str, b: str = "", point=None) -> dict:
    pa = norm(a)
    pb = norm(b) if b else None
    boxes = read_boxes(env, [pa] + ([pb] if pb else []))
    if boxes is None:
        return _no_scene()
    return analysis.measure(boxes, pa, pb, point)


def export_stl_file(env: Env, part: str, path: str, wait_s: float = 45.0) -> dict:
    """Exporta UMA peca para STL (o STL com varias pecas so exporta a primeira) e espera o arquivo aparecer."""
    path = path.replace("\\", "/")
    local = path.replace("/", os.sep)
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    try:
        if os.path.exists(local):
            os.remove(local)                     # so um arquivo NOVO prova que o export funcionou
    except OSError:
        pass
    res = env.run(recipes.export_stl(path, part, True), 30)
    dialogs = env.settle(wait_s, until=lambda: os.path.exists(local))
    if not os.path.exists(local):
        # o Error 414 do export anterior pode deixar o comando STL pendente e engolir este: cancela e tenta uma vez mais
        env.run(recipes.cancel_command(), 15)
        res = env.run(recipes.export_stl(path, part, True), 30)
        dialogs = (dialogs or []) + (env.settle(min(wait_s, 30.0), until=lambda: os.path.exists(local)) or [])
    out: dict = {"part": part, "file": path, "ok": os.path.exists(local)}
    if dialogs:
        out["dialogs"] = dialogs
    if out["ok"]:
        env.sleep(0.6)
        out["bytes"] = os.path.getsize(local)
        health.stl_success()
    else:
        out["error"] = res.get("error") or "o arquivo STL nao apareceu (confira o nome da peca e creo_screenshot)"
        health.stl_failure(out["error"], part)
    return out


def _tmp_dir(prefix: str) -> str:
    d = os.path.join(config.HOME, "exports", f"{prefix}_{int(time.time())}")
    os.makedirs(d, exist_ok=True)
    return d


def _stl_name(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", part.strip("/")) or "peca"


def clash_check(env: Env, parts_: list[str] | None = None, tol: float = 0.01, exact: bool = True,
                include_touching: bool = False, time_budget_s: float = 240.0) -> dict:
    """Interferencia: pre-filtro por caixa envolvente (todos os pares) e, com exact=True, confirmacao sobre a malha STL
    dos candidatos. Caixa sobreposta NAO e interferencia (parafuso dentro de furo tem caixas sobrepostas)."""
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    allp = _all_parts(tree)
    names = [p for p in parts_] if parts_ else allp
    missing = [p for p in names if p not in allp]
    if missing:
        return {"ok": False, "error": f"pecas inexistentes: {missing}"}
    boxes = read_boxes(env, names)
    if boxes is None:
        return _no_scene()
    cands = analysis.clash_candidates({n: boxes.get(n) for n in names}, tol=tol, include_touching=include_touching)
    out: dict = {"ok": True, "parts_checked": len(names), "candidate_pairs": len(cands), "method": "caixa envolvente",
                 "no_box": [n for n in names if not boxes.get(n)]}
    if not exact or not cands:
        out["pairs"] = cands[:200]
        out["note"] = ("candidatos por caixa; use exact=true para confirmar sobre a malha" if cands
                       else "nenhum par com caixas sobrepostas: nao ha interferencia")
        out["clashes"] = [] if not cands else None
        return out
    if len(cands) > MAX_CLASH_PAIRS:
        out["pairs"] = cands[:MAX_CLASH_PAIRS]
        out["ok"] = False
        out["error"] = (f"{len(cands)} pares candidatos: acima de {MAX_CLASH_PAIRS} para a confirmacao exata. Reduza com "
                        "'parts' ou aumente tol; os candidatos estao em 'pairs'.")
        return out
    need = sorted({x for c in cands for x in (c["a"], c["b"])})
    if len(need) > MAX_CLASH_PARTS:
        out.update(ok=False, pairs=cands, error=f"{len(need)} pecas a exportar: acima de {MAX_CLASH_PARTS}")
        return out
    folder = _tmp_dir("clash")
    tris: dict = {}
    failed = []
    t_end = time.time() + max(10.0, float(time_budget_s))
    for p in need:
        if time.time() > t_end:
            failed.append({"part": p, "error": "tempo total esgotado antes de exportar (time_budget_s)"})
            continue
        fp = os.path.join(folder, _stl_name(p) + ".stl").replace("\\", "/")
        r = export_stl_file(env, p, fp)
        if not r["ok"]:
            failed.append({"part": p, "error": r.get("error")})
            continue
        try:
            tris[p] = stl.load_triangles(fp.replace("/", os.sep))
        except Exception as exc:  # noqa: BLE001
            failed.append({"part": p, "error": f"STL ilegivel: {exc!r}"})
    out["method"] = "malha STL (tesselada)"
    out["stl_folder"] = folder
    if failed:
        out["export_failures"] = failed
    results = []
    for c in cands:
        a, b = c["a"], c["b"]
        row = dict(c)
        if a in tris and b in tris:
            if time.time() > t_end:
                row.update(interferes=None, note="tempo total esgotado (time_budget_s): par nao verificado")
                results.append(row)
                continue
            iv = mesh.interference(tris[a], tris[b], deadline=t_end)
            row.update(interferes=bool(iv["intersects"]) if not iv.get("timed_out") else None,
                       crossings=iv["crossings"], contained=iv["contained"], truncated=iv["truncated"])
            if iv.get("timed_out"):
                row["note"] = "tempo esgotado no meio deste par (malha grande, ex.: rosca helicoidal): resultado incerto"
        else:
            row.update(interferes=None, note="sem malha de uma das pecas")
        results.append(row)
    out["pairs"] = results
    out["clashes"] = [r for r in results if r.get("interferes")]
    out["not_checked"] = [r for r in results if r.get("interferes") is None]
    out["ok"] = not failed
    out["tessellation_note"] = mesh.interference([], [])["tessellation_note"]
    return out


def mesh_qa(env: Env, part: str) -> dict:
    p = norm(part)
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    if p not in _all_parts(tree):
        return {"ok": False, "error": f"a peca {p} nao existe (creo_get_model_tree)"}
    folder = _tmp_dir("mesh")
    fp = os.path.join(folder, _stl_name(p) + ".stl").replace("\\", "/")
    r = export_stl_file(env, p, fp)
    if not r["ok"]:
        return {"ok": False, "part": p, "error": r.get("error"), "dialogs": r.get("dialogs")}
    try:
        qa = stl.analyze(fp.replace("/", os.sep))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "part": p, "error": f"nao consegui analisar o STL: {exc!r}"}
    qa["part"] = p
    qa["file"] = fp
    return qa


def inspect_part(env: Env, part: str, material=None, counts: bool = False) -> dict:
    p = norm(part)
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    if p not in _all_parts(tree):
        return {"ok": False, "error": f"a peca {p} nao existe (creo_get_model_tree)"}
    boxes = read_boxes(env, [p]) or {}
    vols, _d = read_volumes(env, [p])
    attrs = exchange.get_attributes(p)
    dens = analysis.density_of(material) if material not in (None, "") else analysis.density_of(
        attrs.get("density_g_cm3") or attrs.get("material"))
    out = analysis.inspect_part(p, boxes.get(p), (vols or {}).get(p), dens)
    out["ok"] = True
    if attrs:
        out["attributes"] = attrs
    if material not in (None, "") and dens is None:
        out["warning"] = f"material {material!r} desconhecido: informe a densidade em g/cm3 (numero)"
    if dens is None:
        out["mass_note"] = "sem densidade: informe material ou attrs.density_g_cm3 para obter a massa"
    if vols is None:
        out["warning_volume"] = "volume nao lido (GET_VOL_PROP falhou)"
    if counts:
        faces = _list_dialog(env, recipes.face_list_define(), recipes.face_list_setup(p), recipes.face_list_command(),
                             "*mcp-fl-result*")
        edges = _list_dialog(env, recipes.edge_list_define(), recipes.edge_list_setup(p), recipes.edge_list_command(),
                             "*mcp-el-result*")
        out["faces"] = len(faces) if faces is not None else None
        out["edges"] = len(edges) if edges is not None else None
    return out


def _list_dialog(env: Env, define: str, setup: str, command: str, result_var: str):
    r = ops.run_dialog(env.run, env.settle, define, setup, command, result_var, tries=20)
    return r["result"] if r.get("ok") and isinstance(r.get("result"), list) else None


# ------------------------------------------------------------------ nivel 2: troca de dados
def _folder(folder: str | None, default_name: str) -> tuple[str | None, str | None]:
    f = (folder or os.path.join(EXPORT_DIR, default_name)).replace("\\", "/")
    if not config.path_allowed(f):
        return None, (f"pasta fora das permitidas {config.export_roots()}: use uma pasta dentro delas ou defina "
                      "CREO_MCP_EXPORT_ROOTS no servidor MCP")
    os.makedirs(f.replace("/", os.sep), exist_ok=True)
    return f, None


EXPORT_TEMPLATES = {
    "step": [
        'STEP_EXPORT :FILE "{path}" :OVERWRITE :PARTS_ASSEMBLIES {parts} COMPLETE',
        'STEP_EXPORT :FILE "{path}" :PARTS_ASSEMBLIES {parts} COMPLETE',
        'STEP_EXPORT :FILENAME "{path}" :OVERWRITE :PARTS_ASSEMBLIES {parts} COMPLETE',
    ],
    "sat": [
        'NATIVE_CAD_OUT :CAD_SYSTEM :ACIS_SAT :FILE "{path}" :OVERWRITE :PARTS_ASSEMBLIES {parts} COMPLETE',
        'NATIVE_CAD_OUT :CAD_SYSTEM :ACIS_SAT :FILE "{path}" :PARTS_ASSEMBLIES {parts} COMPLETE',
        'NATIVE_CAD_OUT :CAD_SYSTEM :ACIS_SAT :FILENAME "{path}" :OVERWRITE :PARTS_ASSEMBLIES {parts} COMPLETE',
    ],
}


def load_syntax() -> dict:
    try:
        with open(SYNTAX_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_syntax(key: str, template: str) -> None:
    data = load_syntax()
    data[key] = {"template": template, "verified": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        os.makedirs(config.HOME, exist_ok=True)
        with open(SYNTAX_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def export_other(env: Env, fmt: str, parts_: list[str], path: str, wait_s: float = 40.0) -> dict:
    """STEP/SAT: tenta, em ordem, os modelos de comando (o que ja funcionou fica em command_syntax.json). O arquivo tem de
    aparecer e passar na validacao do conteudo. Cada tentativa recusada pelo Creo so mostra um dialogo de erro."""
    path = path.replace("\\", "/")
    local = path.replace("/", os.sep)
    quoted = " ".join('"' + p + '"' for p in parts_)
    known = load_syntax().get(f"export_{fmt}", {}).get("template")
    templates = ([known] if known else []) + [t for t in EXPORT_TEMPLATES[fmt] if t != known]
    attempts = []
    for tpl in templates:
        try:
            os.remove(local)
        except OSError:
            pass
        cmd = tpl.format(path=path, parts=quoted)
        res = env.run(recipes.send_command(cmd), 20)
        dialogs = env.settle(wait_s if tpl == templates[0] else 15.0, until=lambda: os.path.exists(local))
        ok = os.path.exists(local) and os.path.getsize(local) > 0
        attempts.append({"command": cmd, "file": ok, "dialogs": dialogs or None, "error": res.get("error")})
        if ok:
            env.sleep(0.6)
            val = exchange.validate_file(local, fmt)
            if val.get("ok"):
                save_syntax(f"export_{fmt}", tpl)
                return {"ok": True, "file": path, "command": cmd, "validation": val, "attempts": len(attempts)}
            attempts[-1]["validation"] = val
        else:
            env.run(recipes.cancel_command(), 10)       # painel pendente de uma tentativa recusada
            env.settle(3.0)
    return {"ok": False, "error": f"o Creo nao exportou {fmt.upper()} com nenhum dos modelos de comando", "attempts": attempts}


def export(env: Env, parts_: list[str] | None, fmt: str = "stl", folder: str | None = None, name: str = "",
           manifest: bool = True, validate: bool = True) -> dict:
    fmt = str(fmt).lower()
    if fmt not in exchange.FORMATS:
        return {"ok": False, "error": f"formato {fmt!r}: use {sorted(exchange.FORMATS)}"}
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    existing = {n["path"]: n["kind"] for n in scene.flatten(tree)}
    names = parts_ or [p for p in _all_parts(tree)]
    missing = [p for p in names if p not in existing]
    if missing:
        return {"ok": False, "error": f"nao existem na cena: {missing}"}
    if not names:
        return {"ok": False, "error": "cena sem pecas para exportar"}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[^A-Za-z0-9_\-]+", "_", name or (names[0].strip("/") if len(names) == 1 else "cena")) or "cena"
    f, err = _folder(folder, f"{base}_{stamp}")
    if err:
        return {"ok": False, "error": err}
    boxes = read_boxes(env, [p for p in names if existing.get(p) == "PART"]) or {}
    entries: list = []
    failures: list = []
    if fmt == "stl":
        for p in names:
            if existing.get(p) != "PART":
                failures.append({"part": p, "error": "STL exporta pecas, nao montagens: informe as pecas de dentro"})
                continue
            fp = f"{f}/{_stl_name(p)}.stl"
            r = export_stl_file(env, p, fp)
            if not r["ok"]:
                failures.append({"part": p, "error": r.get("error"), "dialogs": r.get("dialogs")})
                continue
            entry = exchange.file_info(fp.replace("/", os.sep))
            entry["parts"] = [p]
            if boxes.get(p):
                entry["bbox"] = [list(boxes[p][0]), list(boxes[p][1])]
            if validate:
                try:
                    entry["validation"] = exchange.validate_file(fp.replace("/", os.sep), "stl")
                except Exception as exc:  # noqa: BLE001
                    entry["validation"] = {"ok": False, "error": repr(exc)}
            entries.append(entry)
    else:
        fp = f"{f}/{base}{exchange.FORMATS[fmt]}"
        try:
            exchange.check_name(fp, fmt)
        except PartError as exc:
            return {"ok": False, "error": str(exc)}
        r = export_other(env, fmt, names, fp)
        if not r["ok"]:
            return dict(r, parts=names, experimental=True)
        entry = exchange.file_info(fp.replace("/", os.sep))
        entry.update(parts=names, validation=r.get("validation"), command=r.get("command"))
        entries.append(entry)
    out: dict = {"ok": bool(entries) and not failures, "format": fmt, "folder": f, "files": entries,
                 "failures": failures or None}
    if validate and any(not e.get("validation", {}).get("ok", True) for e in entries):
        out["ok"] = False
        out["error"] = "algum arquivo nao passou na validacao do conteudo (veja files[].validation)"
    if manifest and entries:
        mpath = f"{f}/manifest_{base}.json"
        exchange.write_json(mpath, exchange.manifest(entries, {"format": fmt, "scene_parts": len(_all_parts(tree))}))
        out["manifest"] = mpath
    return out


IMPORT_TEMPLATES = {
    "step": ['STEP_IMPORT :FILE "{path}" COMPLETE', 'STEP_IMPORT :FILENAME "{path}" COMPLETE'],
}


def import_file(env: Env, path: str, fmt: str = "step", dry_run: bool = False) -> dict:
    """Importa um arquivo (hoje: STEP). Confere a cena antes/depois e o limite de pecas (se configurado). EXPERIMENTAL: a Ajuda do
    Express diz que STEP so se salva sem licenca; a carga depende do modulo STEP da instalacao."""
    fmt = str(fmt).lower()
    if fmt not in IMPORT_TEMPLATES:
        return {"ok": False, "error": f"formato {fmt!r} nao importavel por esta ferramenta: {sorted(IMPORT_TEMPLATES)}"}
    p = path.replace("\\", "/")
    if not config.path_allowed(p):
        return {"ok": False, "error": f"arquivo fora das pastas permitidas {config.export_roots()}"}
    if not os.path.isfile(p.replace("/", os.sep)):
        return {"ok": False, "error": f"arquivo nao existe: {p}"}
    if len(os.path.basename(p)) > exchange.MAX_STEP_NAME:
        return {"ok": False, "error": f"o Creo recusa nomes STEP com mais de {exchange.MAX_STEP_NAME} caracteres"}
    before = read_tree(env, 6)
    if before is None:
        return _no_scene()
    info = {"file": exchange.file_info(p.replace("/", os.sep)), "parts_before": scene.count_bodies(before)}
    if dry_run:
        return {"ok": True, "dry_run": True, **info}
    known = load_syntax().get(f"import_{fmt}", {}).get("template")
    templates = ([known] if known else []) + [t for t in IMPORT_TEMPLATES[fmt] if t != known]
    attempts = []
    for tpl in templates:
        cmd = tpl.format(path=p)
        res = env.run(recipes.send_command(cmd), 20)
        dialogs = env.settle(20.0)
        env.sleep(2.0)
        after = read_tree(env, 6)
        new = sorted(set(scene.parts_of(after or [])) - set(scene.parts_of(before)))
        attempts.append({"command": cmd, "new_parts": new, "dialogs": dialogs or None, "error": res.get("error")})
        if new:
            save_syntax(f"import_{fmt}", tpl)
            registry.add(new, "creo_import")
            bud = scene.budget(after, 0)
            return {"ok": True, "imported_parts": new, "command": cmd, "budget": bud, **info}
        env.run(recipes.cancel_command(), 10)
        env.settle(3.0)
    return {"ok": False, "error": "nenhuma peca nova apareceu depois do comando de importacao", "attempts": attempts, **info}


# ------------------------------------------------------------------ lista de materiais
def _bom_load() -> list:
    try:
        with open(BOM_STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("lines", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def bom_store(lines: list, tool: str, created: list[str]) -> None:
    """Guarda as linhas de lista de materiais que a biblioteca devolveu (ferragens, perfis, roscas) para o creo_bom."""
    if not lines:
        return
    cur = _bom_load()
    now = round(time.time(), 1)
    for ln in lines:
        row = dict(ln)
        row["parts"] = [norm(p) for p in ln.get("parts", [])]
        row["tool"], row["ts"] = tool, now
        cur.append(row)
    cur = cur[-1500:]
    try:
        os.makedirs(config.HOME, exist_ok=True)
        with open(BOM_STORE, "w", encoding="utf-8") as fh:
            json.dump({"lines": cur}, fh, indent=1, ensure_ascii=False)
    except OSError:
        pass


def _item_name(base: str) -> str:
    return re.sub(r"[_\.]?\d+$", "", base) or base


def build_bom_lines(parts_: list[str], boxes: dict, vols: dict, attrs: dict, stored: list, default_density=None) -> list:
    """Linhas do BOM: as da biblioteca cujas pecas ainda existem + as pecas soltas (nome sem numero final, tamanho, volume,
    material/densidade dos atributos). Funcao pura."""
    present = set(parts_)
    covered: set = set()
    lines: list = []
    for ln in stored:
        have = [p for p in ln.get("parts", []) if p in present]
        if not have:
            continue
        row = dict(ln)
        row["parts"] = have
        row["qty"] = max(1, min(int(ln.get("qty", 1)), len(have)))
        lines.append(row)
        covered.update(have)
    for p in parts_:
        if p in covered:
            continue
        a = attrs.get(p, {})
        b = boxes.get(p)
        v = vols.get(p) if vols else None
        size = "x".join(f"{s:g}" for s in analysis.r3(analysis.box_size(b), 2)) if b else ""
        dens = analysis.density_of(a.get("density_g_cm3") or a.get("material") or default_density)
        vol = round(v["volume_mm3"], 1) if v else None
        base = p.rsplit("/", 1)[-1]
        item = a.get("part_number") or a.get("description") or _item_name(base)
        unit = round(analysis.mass_g(vol, dens), 2) if (vol and dens) else 0.0
        lines.append({"item": item, "category": "peca", "qty": 1, "parts": [p], "unit_mass_g": unit,
                      "material": str(a.get("material") or ""), "size": size, "volume_mm3": vol,
                      "note": ("sem densidade: massa nao calculada" if (vol and not dens) else "")})
    return lines


def bom(env: Env, fmt: str = "json", folder: str | None = None, name: str = "bom", default_material=None,
        with_volumes: bool = True) -> dict:
    fmt = str(fmt).lower()
    if fmt not in ("csv", "xlsx", "json"):
        return {"ok": False, "error": "fmt deve ser csv, xlsx ou json"}
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    parts_ = _all_parts(tree)
    if not parts_:
        return {"ok": False, "error": "cena sem pecas"}
    boxes = read_boxes(env, parts_) or {}
    vols: dict = {}
    if with_volumes:
        v, _d = read_volumes(env, parts_)
        vols = v or {}
    attrs = exchange.get_attributes()
    lines = build_bom_lines(parts_, boxes, vols, attrs, _bom_load(), default_material)
    rows = exchange.bom_rows(lines, group=True)
    tot = exchange.bom_totals(rows)
    out: dict = {"ok": True, "rows": rows, "totals": tot, "parts_in_scene": len(parts_),
                 "volumes_read": bool(vols), "warnings": []}
    if with_volumes and not vols:
        out["warnings"].append("volumes nao lidos: massas zeradas")
    nomass = [r["item"] for r in rows if not r["massa_unit_g"]]
    if nomass:
        out["warnings"].append(f"{len(nomass)} item(ns) sem massa (sem material/densidade): {nomass[:8]}")
    if fmt != "json" or folder:
        f, err = _folder(folder, "bom")
        if err:
            return {"ok": False, "error": err}
        base = re.sub(r"[^A-Za-z0-9_\-]+", "_", name) or "bom"
        fp = f"{f}/{base}.{fmt}"
        local = fp.replace("/", os.sep)
        if fmt == "csv":
            with open(local, "w", encoding="utf-8", newline="") as fh:
                fh.write(exchange.bom_csv(rows))
        elif fmt == "xlsx":
            exchange.bom_xlsx(rows, local)
        else:
            exchange.write_json(local, {"totals": tot, "rows": rows})
        out["file"] = fp
        out["file_bytes"] = os.path.getsize(local)
    return out


# ------------------------------------------------------------------ vistas e relatorio
def camera_read_code(vp: str = "vport1") -> str:
    return (f"(let ((c (oli:sd-inq-vp-camera {lisp.lisp_str(vp)})))\n"
            "  (if c (list (oli:sd-vp-camera-struct-from-pt c) (oli:sd-vp-camera-struct-to-pt c)\n"
            "              (oli:sd-vp-camera-struct-up-dir c) (oli:sd-vp-camera-struct-projection c)\n"
            "              (oli:sd-vp-camera-struct-view-field c)) :no-camera))")


def parse_camera(text) -> dict | None:
    data = lisp.parse_sexp(text) if isinstance(text, str) else text
    if isinstance(data, list) and len(data) == 5 and all(isinstance(data[i], list) and len(data[i]) == 3 for i in range(3)):
        return {"from": data[0], "to": data[1], "up": data[2], "projection": data[3], "view_field": data[4]}
    return None


def camera_set_code(cam: dict, vp: str = "vport1") -> str:
    """Poe a camera da viewport. EXPERIMENTAL: a assinatura de sd-set-vp-camera e confirmada ao vivo por creo_validate;
    a estrutura lida e copiada e so os pontos mudam."""
    f, t, u = cam["from"], cam["to"], cam["up"]
    pt = lambda v: f"(oli:make-gpnt3d :x {lisp.num(v[0])} :y {lisp.num(v[1])} :z {lisp.num(v[2])})"  # noqa: E731
    return (f"(let ((c (oli:copy-sd-vp-camera-struct (oli:sd-inq-vp-camera {lisp.lisp_str(vp)}))))\n"
            f"  (setf (oli:sd-vp-camera-struct-from-pt c) {pt(f)})\n"
            f"  (setf (oli:sd-vp-camera-struct-to-pt c) {pt(t)})\n"
            f"  (setf (oli:sd-vp-camera-struct-up-dir c) {pt(u)})\n"
            f"  (oli:sd-set-vp-camera {lisp.lisp_str(vp)} c)\n"
            "  :ok)")


def views(env: Env, names: list[str], folder: str | None = None, target: list[str] | None = None, fit: bool = True) -> dict:
    """Tira uma captura de tela por vista padrao (front, back, left, right, top, bottom, iso) e restaura a camera original.
    EXPERIMENTAL ate o creo_validate confirmar sd-set-vp-camera."""
    try:
        vnames = exchange.view_names(names)
    except PartError as exc:
        return {"ok": False, "error": str(exc)}
    if env.shot is None:
        return {"ok": False, "error": "captura de tela indisponivel neste ambiente"}
    f, err = _folder(folder, "views_" + time.strftime("%Y%m%d_%H%M%S"))
    if err:
        return {"ok": False, "error": err}
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    paths = norm_list(target) or _all_parts(tree)
    boxes = read_boxes(env, paths) or {}
    ub = analysis.union_box([b for b in boxes.values() if b])
    if ub is None:
        return {"ok": False, "error": "sem caixa para centralizar a vista (cena vazia?)"}
    center = analysis.box_center(ub)
    diag = max(sum(s * s for s in analysis.box_size(ub)) ** 0.5, 1.0)
    cur = env.run(camera_read_code(), 20)
    orig = parse_camera(cur["result"]) if cur.get("ok") and cur.get("result") else None
    if orig is None:
        return {"ok": False, "error": "nao consegui ler a camera da viewport", "dialogs": cur.get("dialogs")}
    shots, problems = [], []
    try:
        for v in vnames:
            eye, to, up = exchange.camera_points(v, center, diag * 2.2)
            cam = dict(orig, **{"from": list(eye), "to": list(to), "up": list(up)})
            r = env.run(camera_set_code(cam), 30)
            if not r.get("ok") or r.get("dialogs"):
                problems.append({"view": v, "error": r.get("error") or "camera recusada", "dialogs": r.get("dialogs")})
                continue
            if fit:
                env.run(recipes.send_command("FIT_VP"), 10)
            env.settle(2.0)
            env.sleep(0.5)
            res = env.shot(f"{f}/{v}.png".replace("/", os.sep))
            shots.append({"view": v, **{k: res.get(k) for k in ("path", "width", "height", "ok", "error") if k in res}})
    finally:
        env.run(camera_set_code(orig), 30)       # devolve a camera ao que o usuario tinha
    return {"ok": bool(shots) and not problems, "folder": f, "views": shots, "problems": problems or None,
            "camera_restored": True}


def report(env: Env, title: str = "Relatorio do modelo", folder: str | None = None, name: str = "relatorio",
           screenshot: bool = True) -> dict:
    """Relatorio HTML autonomo: orcamento de pecas, auditoria, lista de materiais, saude do servidor e uma captura de tela."""
    f, err = _folder(folder, "reports")
    if err:
        return {"ok": False, "error": err}
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    aud = audit(env, with_volumes=True)
    bud = scene.budget(tree, 0)
    bm = bom(env, "json", None, name, None, True)
    hl = health.status()
    sections = [{"title": "Cena", "status": "ok" if bud["ok"] else "error",
                 "items": [(f"{bud['used']} pecas (sem limite)" if not bud["limit"] else
                           f"{bud['used']} de {bud['limit']} pecas; {bud['left']} livres"),
                           f"{len(scene.assemblies_of(tree))} montagem(ns)"]}]
    findings = aud.get("findings", []) if aud.get("findings") is not None else []
    sections.append({"title": "Auditoria", "status": "ok" if aud.get("ok") else "warn",
                     "table": {"headers": ["nivel", "codigo", "itens", "mensagem"],
                               "rows": [[x["level"], x["code"], ", ".join(map(str, x["items"]))[:200], x["message"]]
                                        for x in findings]} if findings else None,
                     "text": None if findings else "sem achados"})
    if bm.get("ok"):
        sections.append({"title": "Lista de materiais",
                         "text": f"{bm['totals']['itens']} item(ns), massa total {bm['totals']['massa_total_kg']} kg",
                         "table": {"headers": ["pos", "item", "qtd", "material", "massa unit (g)", "dimensoes", "pecas"],
                                   "rows": [[r["pos"], r["item"], r["qtd"], r["material"], r["massa_unit_g"],
                                             r["dimensoes_mm"], ", ".join(r["pecas"])[:120]] for r in bm["rows"]]},
                         "items": bm.get("warnings") or None})
    sections.append({"title": "Estado do servidor", "status": "ok" if hl["state"] == "ok" else "warn",
                     "items": [f"estado: {hl['state']}"] + hl["reasons"]})
    images = []
    if screenshot and env.shot is not None:
        shot_path = os.path.join(f.replace("/", os.sep), f"{name}_tela.png")
        r = env.shot(shot_path)
        if r.get("ok") or os.path.exists(shot_path):
            images.append(shot_path)
    html_text = exchange.report_html(title, sections, images, footer="gerado por creo-elements-mcp")
    fp = f"{f}/{re.sub(r'[^A-Za-z0-9_-]+', '_', name) or 'relatorio'}.html"
    with open(fp.replace("/", os.sep), "w", encoding="utf-8") as fh:
        fh.write(html_text)
    return {"ok": True, "file": fp, "bytes": os.path.getsize(fp.replace("/", os.sep)), "audit_ok": aud.get("ok"),
            "parts": bud["used"]}


# ------------------------------------------------------------------ nivel 3: modelagem tipada
def slim_report(rep: dict) -> dict:
    out = {k: rep[k] for k in ("ok", "part", "problems", "checks", "bbox", "part_bboxes", "stl", "seconds", "notes")
           if k in rep and rep[k] not in (None, [], {})}
    steps = rep.get("steps") or []
    out["steps_total"] = len(steps)
    bad = [s for s in steps if not s.get("ok")]
    if bad:
        out["failed_steps"] = bad
    return out


def run_built(env: Env, built, verify: bool = True, tool: str = "", group: str | None = None) -> dict:
    """Executa um Built (plano + BOM + info): confere o orcamento de pecas, constroi e autoverifica, registra as pecas
    criadas (para a limpeza segura) e guarda a lista de materiais."""
    plan = built.plan
    names = plan.parts()
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    bud = scene.budget(tree, len(names))
    if not bud["ok"]:
        return {"ok": False, "error": (f"o plano cria {len(names)} peca(s) e a cena ja tem {bud['used']}: passaria do limite de "
                                       f"{bud['limit']} configurado"), "budget": bud}
    report = builder.run_plan(plan, env.run, verify)
    after = builder.top_level_paths(env.run)
    mine = [norm(n) for n in names if norm(n) in after]
    if mine:
        registry.add(mine, tool)
    ok = bool(report.get("ok"))
    out: dict = {"ok": ok, "parts_created": mine, "report": slim_report(report), "info": built.info}
    if built.bom:
        out["bom"] = built.bom
    if ok:
        bom_store(built.bom, tool, mine)
        if group:
            out["group"] = group_parts(env, group, [norm(n) for n in names])
            out["ok"] = bool(out["group"].get("ok"))
    return out


def group_parts(env: Env, name: str, parts_: list[str]) -> dict:
    """Move pecas para uma montagem real (:ASSEMBLY) e confere cada uma."""
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    have = set(_all_parts(tree))
    paths = [norm(p) for p in parts_]
    missing = [p for p in paths if p not in have]
    if missing:
        return {"ok": False, "error": f"pecas inexistentes: {missing}", "assembly": norm(name)}
    res = ops.group(env.run, name, paths)
    after = read_tree(env, 6)
    asm = norm(name)
    kinds = {n["path"]: n["kind"] for n in scene.flatten(after or [])}
    moved = [p for p in paths if f"{asm}/{p.lstrip('/')}" in kinds]
    res["moved"] = moved
    res["not_moved"] = [p for p in paths if p not in moved]
    res["ok"] = bool(res.get("ok")) and kinds.get(asm) == "ASSEMBLY" and not res["not_moved"]
    if res["not_moved"] and not res.get("error"):
        res["error"] = f"nao foram para a montagem: {res['not_moved']}"
    if res["ok"]:
        registry.add([asm], "creo_group")
    res.pop("lisp", None)
    return res


def holes(env: Env, part: str, holes_: list, face: str = "top", from_corner: bool = False, name: str | None = None,
          tol_pct: float = 3.0) -> dict:
    """Furos tipados numa peca existente. O volume removido e CONFERIDO: volume antes - depois contra o esperado."""
    from . import holes as holes_mod

    p = norm(part)
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    if p not in _all_parts(tree):
        return {"ok": False, "error": f"a peca {p} nao existe (creo_get_model_tree)"}
    boxes = read_boxes(env, [p])
    box = (boxes or {}).get(p)
    if not box:
        return {"ok": False, "error": f"sem caixa envolvente para {p}"}
    try:
        built = holes_mod.holes_plan(p, box, holes_, face, from_corner, name)
    except PartError as exc:
        return {"ok": False, "error": str(exc)}
    v0, _d = read_volumes(env, [p])
    before = (v0 or {}).get(p)
    report = builder.run_plan(built.plan, env.run, False)
    v1, _d = read_volumes(env, [p])
    after = (v1 or {}).get(p)
    out: dict = {"ok": bool(report.get("ok")), "report": slim_report(report), "info": built.info}
    after_tree = read_tree(env, 6)
    left = scene.leftovers(after_tree or [])
    if left:
        out["ok"] = False
        out["leftovers"] = left
        out["error"] = f"sobraram pecas auxiliares: {left} (ferramenta de corte nao consumida)"
    if before and after:
        removed = before["volume_mm3"] - after["volume_mm3"]
        want = built.info["removed_volume_mm3"]
        err = (removed - want) / want * 100.0 if want else 0.0
        out["volume"] = {"before_mm3": round(before["volume_mm3"], 3), "after_mm3": round(after["volume_mm3"], 3),
                         "removed_mm3": round(removed, 3), "expected_mm3": want, "error_pct": round(err, 2)}
        if abs(err) > tol_pct:
            out["ok"] = False
            out["error"] = (f"o volume removido ({removed:.1f} mm3) difere do esperado ({want:.1f} mm3) em {err:.1f}%: "
                            "confira a peca (creo_screenshot)")
    else:
        out["warning"] = "volume nao lido antes/depois: furos nao conferidos por volume"
    if out["ok"] and built.bom:
        bom_store(built.bom, "creo_holes", [p])
    return out


# ------------------------------------------------------------------ varredura (EXPERIMENTAL, nao validada ao vivo)
SWEEP_WP = "mcp_sw"
SWEEP_DIALOG = "MCP_SWEEP_OP"


def sweep_frame(p1, p2):
    """(origem, ponto_u, ponto_v) do plano do perfil: normal = direcao do trecho p1->p2. Funcao pura."""
    d = [p2[i] - p1[i] for i in range(3)]
    n = sum(c * c for c in d) ** 0.5
    if n < 1e-9:
        raise PartError("a aresta (spine) tem comprimento zero")
    d = [c / n for c in d]
    ref = (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = [ref[1] * d[2] - ref[2] * d[1], ref[2] * d[0] - ref[0] * d[2], ref[0] * d[1] - ref[1] * d[0]]
    un = sum(c * c for c in u) ** 0.5
    u = [c / un for c in u]
    v = [d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0]]
    pu = [p1[i] + u[i] for i in range(3)]
    pv = [p1[i] + v[i] for i in range(3)]
    return list(p1), pu, pv


def sweep_define() -> str:
    return (
        "(progn\n(defvar *mcp-sw-part* \"\")\n(defvar *mcp-sw-wp* \"\")\n(defvar *mcp-sw-pts* nil)\n"
        "(defvar *mcp-sw-remove* nil)\n(defvar *mcp-sw-result* :unset)\n"
        f"(oli:sd-defdialog '{SWEEP_DIALOG.lower()}\n"
        "  :ok-action '(setf *mcp-sw-result*\n"
        "     (let* ((part-item (oli:sd-pathname-to-obj *mcp-sw-part*))\n"
        "            (edges (oli:sd-call-cmds (oli:get_selection :focus_type oli:*sd-edge-3d-seltype*\n"
        "                                                        :select :selected_part part-item :all_3d)))\n"
        "            (near (lambda (a b) (< (abs (- a b)) 0.05)))\n"
        "            (hit (find-if\n"
        "                   (lambda (e)\n"
        "                     (let* ((g (oli:sd-inq-edge-geo e :dest-space :global))\n"
        "                            (s (oli:sd-edge-start-pnt g)) (en (oli:sd-edge-end-pnt g))\n"
        "                            (p *mcp-sw-pts*)\n"
        "                            (a (list (oli:gpnt3d_x s) (oli:gpnt3d_y s) (oli:gpnt3d_z s)\n"
        "                                     (oli:gpnt3d_x en) (oli:gpnt3d_y en) (oli:gpnt3d_z en)))\n"
        "                            (b (list (nth 3 a) (nth 4 a) (nth 5 a) (nth 0 a) (nth 1 a) (nth 2 a))))\n"
        "                       (or (every near a p) (every near b p))))\n"
        "                   edges)))\n"
        "       (if (null hit)\n"
        "           (list :fail :no-edge \"aresta (spine) nao encontrada na peca\")\n"
        "           (oli:sd-call-cmds\n"
        "             (if *mcp-sw-remove*\n"
        "                 (oli:general_sweep_dia :remove_material :blank_part part-item :wp (oli:sd-pathname-to-obj *mcp-sw-wp*)\n"
        "                                        :spine hit :control :const)\n"
        "                 (oli:general_sweep_dia :add_material :blank_part part-item :wp (oli:sd-pathname-to-obj *mcp-sw-wp*)\n"
        "                                        :spine hit :control :const))\n"
        "             :success :ok\n"
        "             :failure (list :fail (oli:sd-inq-error-obj :code) (oli:sd-inq-error-obj :message)))))))\n"
        ":defined)"
    )


def sweep_path(env: Env, part: str, loops: list, spine: list, name: str = "varredura", remove: bool = False) -> dict:
    """EXPERIMENTAL. Varre `loops` (desenhados no plano perpendicular a aresta) ao longo da aresta `spine` de `part`.
    O resultado e conferido pelo volume (sem cortar/somar nada fora do esperado, so ha mudanca) e o plano auxiliar e
    removido no fim."""
    tree = read_tree(env, 6)
    if tree is None:
        return _no_scene()
    if part not in _all_parts(tree):
        return {"ok": False, "error": f"a peca {part} nao existe"}
    p1, p2 = spine[:3], spine[3:]
    org, pu, pv = sweep_frame(p1, p2)
    wp = "/" + SWEEP_WP
    out: dict = {"ok": False, "part": part, "experimental": True}
    v0, _d = read_volumes(env, [part])
    before = (v0 or {}).get(part)
    r = env.run(recipes.ensure_wp(SWEEP_WP), 20)
    if not r.get("ok") or r.get("dialogs"):
        out["error"] = r.get("error") or "nao consegui criar o plano auxiliar"
        out["dialogs"] = r.get("dialogs")
        return out
    pts = " ".join(f"{lisp.num(x)},{lisp.num(y)},{lisp.num(z)}" for x, y, z in (org, pu, pv))
    env.run(recipes.send_command(f'POSITION_WP "{wp}" :PT_PT_PT {pts} COMPLETE'), 20)
    env.settle(4.0)
    draw = "(progn (current_wp %s) %s :ok)" % (lisp.lisp_str(wp), " ".join(recipes.loop_lisp(lp) for lp in loops))
    r = env.run(draw, 40)
    if not r.get("ok") or r.get("dialogs"):
        out["error"] = r.get("error") or "nao consegui desenhar o perfil"
        out["dialogs"] = r.get("dialogs")
        env.run(recipes.cancel_command(), 10)
        _sweep_cleanup(env)
        return out
    setup = (f"(progn (setf *mcp-sw-part* {lisp.lisp_str(part)}) (setf *mcp-sw-wp* {lisp.lisp_str(wp)}) "
             f"(setf *mcp-sw-pts* (list {' '.join(lisp.num(float(v)) for v in spine)})) "
             f"(setf *mcp-sw-remove* {'t' if remove else 'nil'}) (setf *mcp-sw-result* :unset) :ready)")
    dr = ops.run_dialog(env.run, env.settle, sweep_define(), setup,
                        f"(oli:sd-put-buffer {lisp.lisp_str(SWEEP_DIALOG + ' COMPLETE')})", "*mcp-sw-result*")
    out["dialogs"] = dr.get("dialogs")
    res = dr.get("result")
    fail = ops._fail(res)
    if not dr.get("ok") or fail or str(res).upper() != ":OK":
        out["error"] = dr.get("error") or fail or f"resposta inesperada: {res!r}"
        env.run(recipes.cancel_command(), 10)
        env.settle(3.0)
        _sweep_cleanup(env)
        return out
    _sweep_cleanup(env)
    v1, _d = read_volumes(env, [part])
    after = (v1 or {}).get(part)
    if before and after:
        out["volume"] = {"before_mm3": round(before["volume_mm3"], 3), "after_mm3": round(after["volume_mm3"], 3),
                         "delta_mm3": round(after["volume_mm3"] - before["volume_mm3"], 3)}
        out["ok"] = (after["volume_mm3"] < before["volume_mm3"]) if remove else (after["volume_mm3"] > before["volume_mm3"])
        if not out["ok"]:
            out["error"] = "o volume nao mudou no sentido esperado: a varredura nao teve efeito"
    else:
        out["ok"] = True
        out["warning"] = "volume nao lido: efeito nao conferido"
    return out


def _sweep_cleanup(env: Env) -> None:
    """Apaga o plano auxiliar e a sobra do perfil (extrudada numa peca descartavel e apagada)."""
    junk = "/" + SWEEP_WP + "_junk"
    # sem ignore-errors: em rotinas de acao ele faz o comando NAO ter efeito (verificado ao vivo com delete_3d)
    code = ("(progn (unless (oli:sd-pathname-to-obj %s) (create_part :name %s))"
            " (extrude :part %s :wp %s :distance 1 :keep_profile :no)"
            " (when (oli:sd-pathname-to-obj %s) (delete_3d %s))"
            " (current_wp \"/w1\")"
            " (when (oli:sd-pathname-to-obj %s) (delete_3d %s)) :ok)") % (
        lisp.lisp_str(junk), lisp.lisp_str(junk[1:]), lisp.lisp_str(junk), lisp.lisp_str("/" + SWEEP_WP),
        lisp.lisp_str(junk), lisp.lisp_str(junk), lisp.lisp_str("/" + SWEEP_WP), lisp.lisp_str("/" + SWEEP_WP))
    env.run(code, 40)
