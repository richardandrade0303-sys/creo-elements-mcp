"""Diario das chamadas que ALTERAM a cena + roteiro de repeticao (replay).

Cada chamada de ferramenta que modifica o modelo vira uma linha JSON em ~/.creo_mcp/journal/AAAAMMDD.jsonl com a
ferramenta, os argumentos, o Lisp enviado e o resumo do resultado. Dali sai um roteiro (lista de chamadas) que refaz o
modelo do zero: creo_replay executa o roteiro por um despachante de ferramentas tipadas.

Python puro e sem estado recarregavel (nao entra na recarga a quente).
"""

from __future__ import annotations

import json
import os
import time

from . import config, state

DIR = os.path.join(config.HOME, "journal")

# Ferramentas que alteram a cena e entram no diario/roteiro. As de leitura nunca entram.
WRITE_TOOLS = {
    "creo_execute_lisp", "creo_run_command", "creo_create_block", "creo_create_cylinder", "creo_boolean",
    "creo_build_part", "creo_extrude_profile", "creo_edge_treatment", "creo_shell", "creo_clear_scene",
    "creo_transform", "creo_group", "creo_color", "creo_mirror", "creo_section", "creo_holes", "creo_pattern",
    "creo_profile_std", "creo_frame", "creo_pipe", "creo_fasteners", "creo_bearing_std", "creo_transmission",
    "creo_sweep_path", "creo_import", "creo_set_attributes", "creo_restore", "creo_build_assembly",
}

_seq = 0
_now = time.time


def reset() -> None:
    global _seq
    _seq = 0


def _file(ts: float | None = None) -> str:
    return os.path.join(DIR, time.strftime("%Y%m%d", time.localtime(ts or _now())) + ".jsonl")


def record(tool: str, args: dict | None = None, lisp: str | None = None, ok: bool | None = None,
           summary: str | None = None) -> dict | None:
    """Grava uma linha. Ferramentas de leitura sao ignoradas (devolve None)."""
    global _seq
    if tool not in WRITE_TOOLS:
        return None
    _seq += 1
    row = {"ts": round(_now(), 3), "session": state.SESSION_ID, "seq": _seq, "tool": tool, "args": args or {},
           "ok": ok, "summary": (summary or "")[:400]}
    if lisp:
        row["lisp"] = lisp
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(_file(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    return row


def entries(limit: int = 50, session: str | None = None, days: int = 2, ok_only: bool = False) -> list[dict]:
    """Ultimas `limit` entradas (mais antigas primeiro), opcionalmente de uma sessao do servidor."""
    out: list[dict] = []
    try:
        names = sorted(n for n in os.listdir(DIR) if n.endswith(".jsonl"))[-max(1, days):]
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(DIR, name), encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        continue
                    if session and row.get("session") != session:
                        continue
                    if ok_only and not row.get("ok"):
                        continue
                    out.append(row)
        except OSError:
            continue
    return out[-limit:] if limit else out


def build_replay(rows: list[dict], include_raw_lisp: bool = True) -> list[dict]:
    """Roteiro: lista de {"tool", "args"} so com chamadas que deram certo, na ordem. `creo_clear_scene` e
    `creo_restore` ficam de fora (nao fazem parte de refazer o modelo)."""
    steps: list[dict] = []
    for r in rows:
        if not r.get("ok"):
            continue
        tool = r.get("tool")
        if tool in ("creo_clear_scene", "creo_restore"):
            continue
        if tool == "creo_execute_lisp":
            if include_raw_lisp and r.get("lisp"):
                steps.append({"tool": tool, "args": {"code": r["lisp"]}, "seq": r.get("seq")})
            continue
        steps.append({"tool": tool, "args": r.get("args") or {}, "seq": r.get("seq")})
    return steps


def run_replay(steps: list[dict], dispatch: dict, stop_on_error: bool = True, progress=None) -> dict:
    """Executa o roteiro. `dispatch`: nome da ferramenta -> funcao(**args) que devolve dict ou texto JSON."""
    results: list[dict] = []
    ok_all = True
    for i, st in enumerate(steps):
        fn = dispatch.get(st["tool"])
        if fn is None:
            results.append({"i": i, "tool": st["tool"], "ok": False, "error": "ferramenta nao repetivel"})
            ok_all = False
            if stop_on_error:
                break
            continue
        try:
            raw = fn(**st["args"])
            res = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:  # noqa: BLE001 - qualquer falha encerra o passo
            res = {"ok": False, "error": repr(exc)}
        good = bool(res.get("ok", True)) if isinstance(res, dict) else True
        results.append({"i": i, "tool": st["tool"], "ok": good, "summary": _short(res)})
        if progress:
            progress(i + 1, len(steps), st["tool"], good)
        if not good:
            ok_all = False
            if stop_on_error:
                break
    return {"ok": ok_all, "steps_total": len(steps), "steps_run": len(results), "results": results}


def _short(res) -> str:
    if isinstance(res, dict):
        for key in ("error", "problems"):
            if res.get(key):
                return str(res[key])[:200]
        return str({k: res[k] for k in list(res)[:4]})[:200]
    return str(res)[:200]
