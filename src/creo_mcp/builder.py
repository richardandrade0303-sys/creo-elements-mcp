"""Executa um `parts.Plan` no Creo, passo a passo, e AUTOVERIFICA o resultado (bbox + volume do STL).

Recarregado a quente (como recipes.py): corrigir aqui nao exige reiniciar o Claude Desktop.
Recebe `runner(code, timeout)` (a funcao `_run` do servidor) para manter o log e o hot-reload.
"""

from __future__ import annotations

import os
import time

from . import lisp, parts, recipes, stl, winui

HOME = os.path.join(os.path.expanduser("~"), ".creo_mcp")
EXPORT_DIR = os.path.join(HOME, "exports")


def settle(seconds: float = 10.0, until=None) -> list[dict]:
    """Depois de um comando enfileirado (sd-put-buffer): espera e fecha dialogos que surgirem.

    `until`: funcao; enquanto devolver False (ex.: arquivo ainda nao existe) continua esperando ate `seconds`.
    """
    seen: list[dict] = []
    t_end = time.time() + seconds
    quiet_from = None
    while time.time() < t_end:
        wins = winui.dialogs()
        if wins:
            quiet_from = None
            for w in wins:
                info = winui.describe_dialog(w)
                info["action"] = "nao fechado (fatal)" if winui.is_fatal(info) else winui.dismiss(w, drain_s=0)
                seen.append(info)
        else:
            quiet_from = quiet_from or time.time()
            if time.time() - quiet_from > 2.0 and (until is None or until()):
                break
        time.sleep(0.2)
    return lisp.summarize_dialogs(seen)


def _failed(res: dict) -> bool:
    return (not res["ok"]) or bool(res.get("dialogs"))


def _parse(res: dict):
    return lisp.parse_sexp(res["result"]) if res.get("ok") and res.get("result") else None


def top_level_paths(runner) -> list[str]:
    tree = _parse(runner(recipes.object_paths(), 20))
    return [t for t in tree] if isinstance(tree, list) else []


def part_bbox(runner, part: str):
    box = _parse(runner(recipes.part_info("/" + part.lstrip("/")), 30))
    if isinstance(box, list) and len(box) == 2 and all(isinstance(p, list) and len(p) == 3 for p in box):
        return box
    return None


def _group_steps(steps):
    """Agrupa passos consecutivos 'extrude' no mesmo z0 (1 chamada ao Creo por grupo)."""
    groups = []
    for st in steps:
        if (groups and st.kind == "extrude" and groups[-1][0].kind == "extrude"
                and abs(groups[-1][0].z0 - st.z0) < 1e-9 and groups[-1][0].axis == st.axis):
            groups[-1].append(st)
        else:
            groups.append([st])
    return groups


def _set_wp_z(runner, z: float, report: dict, axis: str = "z") -> bool:
    """Garante o plano /wz perpendicular a `axis` na posicao z (sd-put-buffer e assincrono: confere por consulta)."""
    r = runner(recipes.ensure_wp(), 20)
    if _failed(r):
        report["problems"].append(f"criar /wz falhou: {r.get('error') or r.get('dialogs')}")
        return False
    r = runner(recipes.position_wp(z, axis=axis), 20)
    settle(3.0)
    if _failed(r):
        report["problems"].append(f"position_wp falhou: {r.get('error') or r.get('dialogs')}")
        return False
    want = recipes.plane_points(axis, z)[0]
    for _ in range(6):
        org = _parse(runner(recipes.wp_origin(), 15))
        if isinstance(org, list) and len(org) == 3 and all(abs(o - w) < 1e-6 for o, w in zip(org, want)):
            if axis != "z":      # confere a orientacao (normal) dos planos laterais
                nrm = _parse(runner(recipes.wp_w_dir(), 15))
                exp = recipes.PLANE_NORMAL[axis]
                if not (isinstance(nrm, list) and len(nrm) == 3
                        and all(abs(a - b) < 1e-6 for a, b in zip(nrm, exp))):
                    report["problems"].append(f"normal de /wz = {nrm}, esperado {list(exp)} (plano {axis})")
                    return False
            return True
        time.sleep(0.8)
    report["problems"].append(f"/wz nao chegou em {axis}={z} (confira com creo_screenshot)")
    return False


EDGE_KINDS = {"chamfer": "chamfer", "fillet": "blend", "chamfer2": "chamfer2", "chamfer_angle": "chamfer_angle"}


def _dialog_step(runner, st, report, define: str, setup: str, command: str, result_code: str, unset: str,
                 noun: str) -> dict:
    """Passo por dialogo de usuario (chanfro/filete/casca): define o dialogo, configura, chama por texto e le o resultado
    `(total selecionados :OK)`."""
    label = st.label or st.kind
    entry = {"step": label, "part": st.part, "ok": False, "result": None}
    for code in (define, setup):
        r = runner(code, 30)
        if _failed(r):
            entry["error"] = r.get("error")
            entry["dialogs"] = r.get("dialogs")
            return entry
    r = runner(command, 20)
    entry["dialogs"] = settle(3.0) or None
    if _failed(r):
        entry["error"] = r.get("error")
        return entry
    res = None
    for _ in range(30):                       # a acao roda de forma assincrona na fila de comandos do Creo
        raw = runner(result_code, 15)
        res = _parse(raw)
        if not (raw.get("ok") and (res is None or str(res).upper() == unset)):
            break
        time.sleep(0.7)
    entry["result"] = res
    if isinstance(res, list) and len(res) == 3 and str(res[2]).upper() == ":OK" and res[1]:
        entry["ok"] = True
        entry[f"{noun}_selected"] = res[1]
        entry[f"{noun}_total"] = res[0]
        if not entry.get("dialogs"):
            entry.pop("dialogs", None)
    else:
        entry["error"] = f"{st.kind} falhou ou nao selecionou {noun}: {res!r}"
    return entry


def _edge_step(runner, st, report) -> dict:
    """Chanfro/filete: define o dialogo, configura seletores e tamanho, chama por texto e le o resultado."""
    return _dialog_step(runner, st, report, recipes.edge_dialog_define(),
                        recipes.edge_op_setup(EDGE_KINDS[st.kind], st.dz, st.loops, st.part, st.dz2),
                        recipes.edge_op_command(), recipes.edge_op_result(), recipes.EDGE_UNSET, "edges")


def _shell_step(runner, st, report) -> dict:
    """Casca (SHELL_3D): st.loops = seletores de face abertas, st.dz = espessura da parede (para dentro)."""
    return _dialog_step(runner, st, report, recipes.shell_dialog_define(),
                        recipes.shell_op_setup(st.dz, st.loops, st.part),
                        recipes.shell_op_command(), recipes.shell_op_result(), recipes.EDGE_UNSET, "faces")


def _run_plan_impl(plan: parts.Plan, runner, verify: bool = True) -> dict:
    """Executa o plano. Devolve um relatorio enxuto (ok, passos, bbox, volume, problemas)."""
    t0 = time.time()
    report: dict = {"ok": False, "part": plan.name, "steps": [], "problems": []}
    existing = set(top_level_paths(runner))
    clash = [p for p in plan.parts() if "/" + p in existing]
    if clash:
        report["problems"].append(f"ja existe(m) no Creo: {clash}. Use creo_clear_scene ou outro 'name'.")
        return report

    z_done = None
    for group in _group_steps(plan.steps):
        z0, axis = group[0].z0, group[0].axis
        if group[0].kind in ("extrude", "cut", "helix_add", "helix_cut") and recipes.needs_wz(axis, z0) \
                and z_done != (axis, z0):
            if not _set_wp_z(runner, z0, report, axis):
                return report
            z_done = (axis, z0)
        batches = [group] if group[0].kind == "extrude" else [[st] for st in group]
        for batch in batches:
            if batch[0].kind in EDGE_KINDS or batch[0].kind == "shell":
                entry = (_shell_step if batch[0].kind == "shell" else _edge_step)(runner, batch[0], report)
                report["steps"].append(entry)
                if not entry["ok"]:
                    runner(recipes.cancel_command(), 15)
                    settle(4.0)
                    report["problems"].append(f"passo '{entry['step']}' falhou; comando cancelado.")
                    return report
                continue
            codes = []
            for st in batch:
                if st.kind == "extrude":
                    codes.append(recipes.sketch_extrude(st.part, st.loops, st.dz, st.direction, None, st.z0, st.axis))
                elif st.kind in ("helix_add", "helix_cut"):
                    # helice: dz = passo, dz2 = voltas; os contornos estao no plano y=0 (axis "y", coordenadas (x, z))
                    try:
                        codes.append(recipes.helix_sweep(st.part, st.loops, st.dz, st.dz2, st.kind == "helix_cut"))
                    except ValueError as exc:
                        report["steps"].append({"step": st.label or st.kind, "part": st.part, "ok": False,
                                                "error": str(exc)})
                        report["problems"].append(f"passo '{st.label or st.kind}' recusado antes de ir ao Creo: {exc}")
                        return report
                else:
                    codes.append(recipes.cut_with_loops(st.part, st.loops, st.dz, st.z0, axis=st.axis, direction=st.direction))
            code = codes[0] if len(codes) == 1 else "(progn\n" + "\n".join(codes) + "\n)"
            res = runner(code, 120)
            label = batch[0].label or batch[0].kind
            if len(batch) > 1:
                label = f"{len(batch)} extrusoes ({batch[0].label or batch[0].part} ...)"
            entry = {"step": label, "part": batch[0].part, "ok": not _failed(res), "result": res.get("result")}
            report["steps"].append(entry)
            if _failed(res):
                entry["error"] = res.get("error")
                entry["dialogs"] = res.get("dialogs")
                runner(recipes.cancel_command(), 15)
                settle(4.0)
                report["problems"].append(f"passo '{label}' falhou; comando cancelado.")
                return report
            if batch[0].kind == "cut" and res.get("result") and "TOOL-LEFT" in str(res["result"]).upper():
                report["problems"].append(f"'{label}': a ferramenta nao intersectou a peca (nada foi cortado).")

    report["objects"] = top_level_paths(runner)
    if plan.main_part:
        box = part_bbox(runner, plan.main_part)
        report["bbox"] = {"measured": box, "expected": [list(plan.expected_bbox[0]), list(plan.expected_bbox[1])]
                          if plan.expected_bbox else None}
    if plan.part_bboxes:                                   # montagens: confere a posicao de cada peca
        wrong = []
        report["part_bboxes"] = {}
        for pn, exp in plan.part_bboxes.items():
            box = part_bbox(runner, pn)
            diffs = ([abs(a - b) for a, b in zip(list(exp[0]) + list(exp[1]), list(box[0]) + list(box[1]))]
                     if box else None)
            ok_box = bool(diffs) and max(diffs) <= 0.05
            report["part_bboxes"][pn] = {"measured": box, "ok": ok_box}
            if not ok_box:
                wrong.append(pn)
        if wrong:
            report["problems"].append(f"bbox fora do esperado em: {wrong}")
    measured_volume = None
    if verify and (plan.volume_parts or plan.export_parts):
        os.makedirs(EXPORT_DIR, exist_ok=True)
        # (arquivo, [pecas]) - com volume_parts cada peca vai num STL proprio (STL multi-peca so exporta a 1a)
        jobs = ([(os.path.join(EXPORT_DIR, f"{plan.name}__{pn}.stl"), [pn]) for pn, _v in plan.volume_parts]
                if plan.volume_parts else [(os.path.join(EXPORT_DIR, plan.name + ".stl"), list(plan.export_parts))])
        total = 0.0
        infos = []
        for path, plist in jobs:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            runner(recipes.export_stl(path, ",".join("/" + x for x in plist), True), 30)
            settle(45.0, until=lambda p=path: os.path.exists(p))
            if os.path.exists(path):
                time.sleep(0.6)
                try:
                    info = stl.read_stl(path)
                    total += info["volume"]
                    infos.append({"file": path, "parts": plist, "triangles": info["triangles"],
                                  "volume_mm3": info["volume"], "size": info["size"]})
                except Exception as exc:  # arquivo ainda sendo gravado ou formato inesperado
                    report["problems"].append(f"STL ilegivel ({path}): {exc!r}")
            else:
                report["problems"].append(f"STL nao apareceu ({path}); a verificacao por volume foi pulada.")
        if infos and len(infos) == len(jobs):
            measured_volume = round(total, 3)
            exp = round(plan.expected_volume, 1) if plan.expected_volume else None
            if len(infos) == 1:
                report["stl"] = {"file": infos[0]["file"], "triangles": infos[0]["triangles"],
                                 "volume_mm3": infos[0]["volume_mm3"], "expected_volume_mm3": exp,
                                 "size": infos[0]["size"]}
            else:
                report["stl"] = {"files": infos, "volume_mm3": measured_volume, "expected_volume_mm3": exp}
    checks = parts.check_result(plan, (report.get("bbox") or {}).get("measured"), measured_volume)
    report["checks"] = checks
    report["ok"] = bool(checks.get("ok", True)) and not any(not s["ok"] for s in report["steps"]) \
        and (not verify or measured_volume is not None or not plan.expected_volume) \
        and all(v["ok"] for v in report.get("part_bboxes", {}).values())
    if plan.notes:
        report["notes"] = plan.notes
    report["seconds"] = round(time.time() - t0, 1)
    return report


REPORT_DIR = os.path.join(HOME, "reports")


def run_plan(plan: parts.Plan, runner, verify: bool = True) -> dict:
    """Executa o plano e GRAVA o relatorio em ~/.creo_mcp/reports/<peca>.json.

    O Claude Desktop corta chamadas em 60 s (a construcao continua, mas a resposta se perde): o arquivo permite
    recuperar o resultado de uma chamada cuja resposta nao chegou.
    """
    report = _run_plan_impl(plan, runner, verify)
    try:
        import json

        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(os.path.join(REPORT_DIR, f"{plan.name}.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    except OSError:
        pass
    return report
