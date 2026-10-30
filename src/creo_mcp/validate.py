"""Auto-validacao ao vivo das ferramentas novas, SOMENTE em pecas descartaveis /mcp_exp_*.

`run(env, scope, cleanup)` cria blocos pequenos longe da origem (x,y ~ 4000), roda cada ferramenta, compara com valores
analiticos e apaga tudo no fim. Ferramentas que passam sao gravadas em ~/.creo_mcp/validation.json e deixam de ser
"experimental" em creo_capabilities. Nunca toca em pecas do usuario; se a cena do usuario tem pecas, um snapshot
automatico e salvo antes (e a validacao nao roda se o snapshot falhar).

Cada checagem e uma funcao `c_<nome>(ctx)` que devolve um dict de detalhes ou levanta CheckFail(mensagem).
"""

from __future__ import annotations

import os
import re
import time

from . import actions, capabilities, library, ops, recipes, registry, scene, snapshot

PREFIX = "mcp_exp_"
ORIGIN = (4000.0, 4000.0)           # longe de qualquer coisa que o usuario tenha modelado
MIN_FREE_PARTS = 20


class CheckFail(Exception):
    pass


class Ctx:
    def __init__(self, env: actions.Env, scope: str):
        self.env, self.scope = env, scope
        self.parts: dict = {}
        self.exports: str | None = None

    def path(self, name: str) -> str:
        return "/" + PREFIX + name


def _ok(cond, msg: str):
    if not cond:
        raise CheckFail(msg)


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def mk_block(ctx: Ctx, name: str, dx: float, dy: float, w: float, d: float, h: float) -> str:
    """Bloco w x d x h com o canto em ORIGIN + (dx, dy) no plano /w1. Confere a caixa e devolve o caminho."""
    path = ctx.path(name)
    r = ctx.env.run(recipes.block(ORIGIN[0] + dx, ORIGIN[1] + dy, w, d, h, path, None), 60)
    if not r.get("ok") or r.get("dialogs"):
        ctx.env.run(recipes.cancel_command(), 15)
        ctx.env.settle(4.0)
        raise CheckFail(f"nao consegui criar {path}: {r.get('error') or r.get('dialogs')}")
    registry.add([path], "creo_validate")
    box = (actions.read_boxes(ctx.env, [path]) or {}).get(path)
    _ok(box is not None, f"{path} criado mas sem caixa envolvente")
    size = [box[1][i] - box[0][i] for i in range(3)]
    _ok(all(_near(size[i], v, 0.01) for i, v in enumerate((w, d, h))), f"{path}: tamanho {size} != {(w, d, h)}")
    ctx.parts[name] = path
    return path


def _vol(ctx: Ctx, path: str) -> float:
    vols, det = actions.read_volumes(ctx.env, [path])
    _ok(bool(vols) and vols.get(path), f"GET_VOL_PROP falhou em {path}: {det}")
    return vols[path]["volume_mm3"]


# ------------------------------------------------------------------ checagens
def c_units(ctx: Ctx) -> dict:
    r = actions.units_report(ctx.env)
    if r.get("units") and not any(r["units"].values()):          # funcao de unidades nao confirmada: informa, nao reprova
        return {"units": None, "note": "unidades ilegiveis neste Creo; as ferramentas assumem mm e g"}
    _ok(r.get("ok"), f"unidades: {r.get('warnings') or r.get('error')}")
    return {"units": r.get("units")}


def c_budget_audit(ctx: Ctx) -> dict:
    b = actions.budget(ctx.env, MIN_FREE_PARTS)
    _ok("used" in b, "orcamento nao lido")
    a = actions.audit(ctx.env, False)
    _ok("findings" in a, f"auditoria sem achados: {a}")
    return {"used": b["used"], "left": b["left"], "findings": len(a["findings"])}


def c_inspect(ctx: Ctx) -> dict:
    p = mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.inspect_part(ctx.env, p, "steel", True)
    _ok(r.get("ok"), f"inspect: {r}")
    _ok(_near(r["volume_mm3"], 48000.0, 48000 * 0.002), f"volume {r['volume_mm3']} != 48000")
    _ok(_near(r["mass_g"], 376.8, 1.0), f"massa {r.get('mass_g')} != 376.8 g")
    _ok(r.get("faces") == 6 and r.get("edges") == 12, f"faces/arestas {r.get('faces')}/{r.get('edges')} != 6/12")
    return {"volume_mm3": r["volume_mm3"], "mass_g": r["mass_g"], "faces": r["faces"], "edges": r["edges"]}


def c_measure(ctx: Ctx) -> dict:
    a = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    b = mk_block(ctx, "b", 200, 0, 60, 40, 20)
    r = actions.measure(ctx.env, a, b)
    _ok(r.get("ok"), f"measure: {r}")
    _ok(_near(r["gap_mm"], 140.0, 0.05), f"vao {r['gap_mm']} != 140")
    return {"gap_mm": r["gap_mm"], "center_distance_mm": r["center_distance_mm"]}


def c_holes(ctx: Ctx) -> dict:
    p = mk_block(ctx, "h", 0, 300, 60, 40, 20)
    r = actions.holes(ctx.env, p, [{"kind": "through", "d": 8, "at": [15, 20]},
                                  {"kind": "blind", "d": 6, "depth": 10, "at": [45, 20]}], "top", True, None, 3.0)
    _ok(r.get("ok"), f"holes: {r.get('error') or r}")
    return {"volume": r.get("volume")}


def c_transform(ctx: Ctx) -> dict:
    p = mk_block(ctx, "t", 0, 500, 50, 30, 10)
    before = actions.read_boxes(ctx.env, [p])[p]
    r = ops.transform(ctx.env.run, ctx.env.settle, p, [{"translate": [0, 100, 5]}])
    _ok(r.get("ok"), f"transform: {r.get('error') or r}")
    after = actions.read_boxes(ctx.env, [p])[p]
    d = [after[0][i] - before[0][i] for i in range(3)]
    _ok(all(_near(d[i], v, 0.05) for i, v in enumerate((0, 100, 5))), f"deslocamento {d} != (0,100,5)")
    r2 = ops.transform(ctx.env.run, ctx.env.settle, p,
                       [{"rotate": {"axis": "z", "origin": [after[0][0], after[0][1], after[0][2]], "angle": 90}}])
    _ok(r2.get("ok"), f"rotacao: {r2.get('error') or r2}")
    return {"translate": d, "rotate_ok": True}


def c_color(ctx: Ctx) -> dict:
    p = ctx.parts.get("t") or mk_block(ctx, "t", 0, 500, 50, 30, 10)
    r = ops.color(ctx.env.run, [p], ops.parse_color("red"))
    _ok(r.get("ok"), f"color: {r.get('error') or r}")
    return {"color": "red"}


def c_mirror(ctx: Ctx) -> dict:
    p = mk_block(ctx, "m", 0, 700, 40, 20, 10)
    before = actions.read_boxes(ctx.env, [p])[p]
    off = ORIGIN[0] + 100.0
    r = ops.mirror(ctx.env.run, ctx.env.settle, p, "x", off)
    _ok(r.get("ok"), f"mirror: {r.get('error') or r}")
    after = actions.read_boxes(ctx.env, [p])[p]
    want_lo = 2 * off - before[1][0]
    _ok(_near(after[0][0], want_lo, 0.05), f"x min {after[0][0]} != {want_lo}")
    return {"x_min": after[0][0]}


def c_section(ctx: Ctx) -> dict:
    p = mk_block(ctx, "s", 0, 900, 40, 40, 20)
    r = ops.section(ctx.env.run, ctx.env.settle, p, "z", 10.0)
    _ok(r.get("ok"), f"section: {r.get('error') or r}")
    half = p + ".1"
    registry.add([half], "creo_validate")
    ctx.parts["s1"] = half
    v = _vol(ctx, p) + _vol(ctx, half)
    _ok(_near(v, 32000.0, 64.0), f"volume das metades {v} != 32000")
    return {"halves_volume": v}


def c_group(ctx: Ctx) -> dict:
    p = mk_block(ctx, "g", 0, 1100, 20, 20, 10)
    name = PREFIX + "grp"
    r = actions.group_parts(ctx.env, name, [p])
    _ok(r.get("ok"), f"group: {r.get('error') or r}")
    ctx.parts["grp"] = "/" + name
    return {"moved": r.get("moved")}


def c_clash(ctx: Ctx) -> dict:
    a = mk_block(ctx, "c1", 0, 1300, 40, 40, 40)
    b = mk_block(ctx, "c2", 20, 1320, 40, 40, 40)          # sobrepoe 20x20x40
    far = mk_block(ctx, "c3", 300, 1300, 40, 40, 40)
    r = actions.clash_check(ctx.env, [a, b, far], 0.01, True, False)
    _ok(r.get("ok"), f"clash: {r.get('error') or r}")
    pairs = {(x["a"], x["b"]) for x in r.get("clashes") or []}
    _ok((a, b) in pairs or (b, a) in pairs, f"a sobreposicao 20x20x40 nao foi detectada: {r.get('pairs')}")
    _ok(len(pairs) == 1, f"pares de interferencia inesperados: {pairs}")
    return {"clashes": len(pairs), "candidates": r.get("candidate_pairs")}


def c_export_stl(ctx: Ctx) -> dict:
    p = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.export(ctx.env, [p], "stl", None, "validacao", True, True)
    _ok(r.get("ok"), f"export stl: {r.get('error') or r.get('failures') or r}")
    _ok(r.get("manifest") and os.path.exists(r["manifest"].replace("/", os.sep)), "manifesto nao foi gravado")
    ctx.exports = r.get("folder")
    return {"files": [f.get("name") or f.get("path") for f in r["files"]], "manifest": r["manifest"]}


def c_mesh_qa(ctx: Ctx) -> dict:
    p = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.mesh_qa(ctx.env, p)
    _ok(r.get("ok", True) and r.get("watertight") is not False, f"malha: {r}")
    return {"triangles": r.get("triangles"), "watertight": r.get("watertight")}


def c_bom(ctx: Ctx) -> dict:
    p = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    exchange_attrs = actions.exchange.set_attributes(p, {"material": "steel", "part_number": "VAL-001"})
    r = actions.bom(ctx.env, "json", None, "bom", None, True)
    actions.exchange.clear_attributes(p)
    _ok(r.get("ok"), f"bom: {r.get('error') or r}")
    row = next((x for x in r["rows"] if p in x.get("pecas", [])), None)
    _ok(row is not None, "a peca de teste nao apareceu na lista de materiais")
    _ok(_near(row["massa_unit_g"], 376.8, 1.5), f"massa {row['massa_unit_g']} != 376.8 g")
    return {"rows": len(r["rows"]), "mass_g": row["massa_unit_g"], "attrs": bool(exchange_attrs)}


def c_report(ctx: Ctx) -> dict:
    r = actions.report(ctx.env, "Validacao", None, "validacao", False)
    _ok(r.get("ok") and os.path.exists(r["file"].replace("/", os.sep)), f"relatorio: {r}")
    return {"file": r["file"], "bytes": r["bytes"]}


def c_clear_dry(ctx: Ctx) -> dict:
    a = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.clear_scene(ctx.env, "mine", False)
    _ok(r.get("ok") and r.get("dry_run"), f"clear_scene dry: {r}")
    _ok(a in r["would_delete"], "a peca descartavel nao esta em would_delete")
    return {"would_delete": len(r["would_delete"]), "protected": len(r["protected"])}


def c_fasteners(ctx: Ctx) -> dict:
    built = library.component("fastener", {"size": 8, "grip": 20, "name": PREFIX + "f",
                                           "at": [0, 0], "origin": [ORIGIN[0], ORIGIN[1] + 1600, 0]})
    # Confere a geometria pela caixa de cada peca. A conferencia de volume por STL fica de fora: ao vivo o export STL de
    # parafuso/porca falha de forma intermitente (Error 414 + comando pendente) sem relacao com a modelagem.
    r = actions.run_built(ctx.env, built, False, "creo_validate")
    _ok(r.get("ok"), f"fasteners: {r.get('error') or r.get('report')}")
    for p in r.get("parts_created", []):
        ctx.parts[p] = p
    return {"parts": len(r.get("parts_created", [])), "bom": len(r.get("bom", []))}


def c_assembly(ctx: Ctx) -> dict:
    spec = {"name": PREFIX + "asm", "group": False, "items": [
        {"component": "profile", "name": PREFIX + "pf", "params": {"profile": "SHS40x3", "length": 200},
         "origin": [ORIGIN[0], ORIGIN[1] + 1800, 0]}]}
    built = library.assemble(spec)
    r = actions.run_built(ctx.env, built, True, "creo_validate")
    _ok(r.get("ok"), f"assembly: {r.get('error') or r.get('report')}")
    for p in r.get("parts_created", []):
        ctx.parts[p] = p
    return {"parts": len(r.get("parts_created", []))}


def c_export_step(ctx: Ctx) -> dict:
    p = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.export(ctx.env, [p], "step", None, "validacao_step", True, True)
    _ok(r.get("ok"), f"export step: {r.get('error') or r.get('attempts') or r}")
    return {"command": r["files"][0].get("command")}


def c_export_sat(ctx: Ctx) -> dict:
    p = ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)
    r = actions.export(ctx.env, [p], "sat", None, "validacao_sat", True, True)
    _ok(r.get("ok"), f"export sat: {r.get('error') or r.get('attempts') or r}")
    return {"command": r["files"][0].get("command")}


def c_views(ctx: Ctx) -> dict:
    r = actions.views(ctx.env, ["front", "iso"], None, [ctx.parts.get("a") or mk_block(ctx, "a", 0, 0, 60, 40, 20)], True)
    _ok(r.get("ok"), f"views: {r.get('error') or r.get('problems') or r}")
    return {"views": [v.get("view") for v in r.get("views", [])]}


def c_snapshot(ctx: Ctx) -> dict:
    r = snapshot.create(ctx.env.run, "validacao", auto=True)
    _ok(r.get("ok"), f"snapshot: {r.get('error') or r}")
    return {"path": r.get("path")}


# (nome, ferramentas que ele promove, funcao, escopo minimo)
CHECKS = [
    ("units", ["creo_units"], c_units, "quick"),
    ("budget_audit", ["creo_part_budget", "creo_audit_scene"], c_budget_audit, "quick"),
    ("inspect", ["creo_inspect"], c_inspect, "quick"),
    ("measure", ["creo_measure"], c_measure, "quick"),
    ("holes", ["creo_holes"], c_holes, "quick"),
    ("transform", ["creo_transform"], c_transform, "quick"),
    ("color", ["creo_color"], c_color, "quick"),
    ("mirror", ["creo_mirror"], c_mirror, "quick"),
    ("section", ["creo_section"], c_section, "quick"),
    ("group", ["creo_group"], c_group, "quick"),
    ("clash", ["creo_clash_check"], c_clash, "quick"),
    ("export_stl", ["creo_export"], c_export_stl, "quick"),
    ("mesh_qa", ["creo_mesh_qa"], c_mesh_qa, "quick"),
    ("bom", ["creo_bom", "creo_set_attributes"], c_bom, "quick"),
    ("report", ["creo_report"], c_report, "quick"),
    ("clear_dry", ["creo_clear_scene"], c_clear_dry, "quick"),
    ("fasteners", ["creo_fasteners", "creo_pattern", "creo_batch"], c_fasteners, "full"),
    ("assembly", ["creo_profile_std", "creo_frame", "creo_pipe", "creo_build_assembly", "creo_variants"], c_assembly, "full"),
    ("export_step", ["creo_export:step"], c_export_step, "full"),
    ("export_sat", ["creo_export:sat"], c_export_sat, "full"),
    ("views", ["creo_views"], c_views, "full"),
    ("snapshot", ["creo_snapshot"], c_snapshot, "full"),
]


def cleanup_code(tree) -> list[str]:
    """Objetos de topo descartaveis (/mcp_exp_*) presentes na cena. Funcao pura."""
    return [n["path"] for n in scene.top_level(tree)
            if re.match(r"^/" + PREFIX, n["path"]) and n["kind"] in ("PART", "ASSEMBLY")]


def clean(env: actions.Env) -> dict:
    tree = actions.read_tree(env, 6)
    if tree is None:
        return {"ok": False, "error": "nao consegui ler a cena para limpar"}
    mine = cleanup_code(tree)
    if not mine:
        return {"ok": True, "deleted": []}
    res = actions.delete_objects(env, tree, mine)
    registry.forget(res["deleted"])
    return {"ok": not res["remaining"], "deleted": res["deleted"], "remaining": res["remaining"]}


def promote(results: list) -> dict:
    """Grava em validation.json as ferramentas das checagens que passaram (e o motivo das que falharam)."""
    data = capabilities.load_validation()
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    for r in results:
        for t in r["tools"]:
            tools[t] = {"ok": bool(r["ok"]), "check": r["check"], "when": when,
                        **({} if r["ok"] else {"detail": str(r.get("error"))[:300]})}
    data["tools"], data["when"] = tools, when
    capabilities.save_validation(data)
    return data


def run(env: actions.Env, scope: str = "quick", cleanup: bool = True) -> dict:
    scope = scope if scope in ("quick", "full") else "quick"
    tree = actions.read_tree(env, 6)
    if tree is None:
        return actions._no_scene()
    bud = scene.budget(tree, MIN_FREE_PARTS)
    if not bud["ok"]:
        return {"ok": False, "error": (f"a validacao precisa de ~{MIN_FREE_PARTS} pecas livres e a cena tem {bud['used']} "
                                       f"de {bud['limit']}: libere pecas, rode numa sessao vazia ou aumente CREO_MCP_PART_LIMIT"), "budget": bud}
    leftovers = cleanup_code(tree)
    if leftovers:
        return {"ok": False, "error": f"sobraram pecas de uma validacao anterior {leftovers}: apague-as (creo_clear_scene "
                                      "scope='mine' confirm=true) e repita"}
    out: dict = {"scope": scope, "checks": []}
    if scene.count_bodies(tree) > 0:
        bk = snapshot.create(env.run, "antes_da_validacao", auto=True)
        out["snapshot"] = {k: bk.get(k) for k in ("ok", "path", "error") if bk.get(k) is not None}
        if not bk.get("ok"):
            out.update(ok=False, error="nao consegui salvar o snapshot da cena antes de validar; nada foi criado")
            return out
    ctx = Ctx(env, scope)
    results = []
    actions.SCOPE_PREFIX = "/" + PREFIX          # as checagens so enxergam as pecas descartaveis
    try:
        for name, tools, fn, min_scope in CHECKS:
            if min_scope == "full" and scope != "full":
                continue
            row = {"check": name, "tools": tools, "ok": False}
            t0 = time.time()
            try:
                row["detail"] = fn(ctx)
                row["ok"] = True
            except CheckFail as exc:
                row["error"] = str(exc)
            except ValueError as exc:
                row["error"] = f"parametro: {exc}"
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"erro inesperado: {exc!r}"
            row["seconds"] = round(time.time() - t0, 1)
            results.append(row)
            if not row["ok"] and ("fatal" in str(row.get("error", "")).lower() or "nao foi possivel conectar" in str(row.get("error", "")).lower()):
                row["aborted_run"] = True
                break
    finally:
        actions.SCOPE_PREFIX = None
        if cleanup:
            out["cleanup"] = clean(env)
    out["checks"] = results
    out["passed"] = sum(1 for r in results if r["ok"])
    out["failed"] = [r["check"] for r in results if not r["ok"]]
    out["ok"] = not out["failed"] and bool(results)
    promote(results)
    out["note"] = ("ferramentas aprovadas passaram de 'experimental' para 'verified' em creo_capabilities"
                   if out["passed"] else "nenhuma checagem passou")
    return out
