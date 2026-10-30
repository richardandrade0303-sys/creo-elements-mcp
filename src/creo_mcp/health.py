"""Saude do Creo/servidor: incidentes em JSON, queda detectada (PID mudou) e disjuntor depois de falhas repetidas.

Estados:
  ok          tudo normal
  degraded    2+ falhas de STL em 30 min, ou 3+ falhas seguidas de conversa com o Creo: pare e peca reinicio
  restarted   o PID do Creo mudou desde a ultima vez: a cena anterior foi perdida (use creo_restore)
  down        o Creo nao esta rodando

Este modulo NAO e recarregado a quente (guarda estado). Python puro; relogio e pastas podem ser trocados nos testes.
"""

from __future__ import annotations

import json
import os
import time

from . import config, state

INCIDENT_FILE = os.path.join(config.HOME, "incidents.jsonl")
HEALTH_FILE = os.path.join(config.HOME, "health.json")

STL_WINDOW_S = 1800.0
STL_LIMIT = 2
CONSECUTIVE_LIMIT = 3
INFRA_MARKERS = ("conectar", "NO_CONV", "DATAACKTIMEOUT", "resultado nao apareceu", "nao respondeu", "timeout")

_now = time.time
_s: dict = {"consecutive": 0, "stl": [], "restart": None, "last_pids": None, "down": False}


def reset() -> None:
    """So para testes."""
    _s.update({"consecutive": 0, "stl": [], "restart": None, "last_pids": None, "down": False})


def record(kind: str, detail: str = "", **extra) -> dict:
    """Acrescenta um incidente ao arquivo JSONL e devolve a linha gravada."""
    row = {"ts": round(_now(), 3), "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now())),
           "session": state.SESSION_ID, "kind": kind, "detail": str(detail)[:500]}
    row.update(extra)
    try:
        os.makedirs(os.path.dirname(INCIDENT_FILE), exist_ok=True)
        with open(INCIDENT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    return row


def recent_incidents(limit: int = 10) -> list[dict]:
    try:
        with open(INCIDENT_FILE, encoding="utf-8") as fh:
            lines = fh.readlines()[-max(limit, 0):] if limit else []
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _load_saved() -> dict:
    try:
        with open(HEALTH_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
        with open(HEALTH_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def observe_pids(pids: list[int]) -> None:
    """Compara os PIDs atuais do Creo com os da ultima observacao (mesmo entre execucoes do servidor)."""
    pids = sorted(int(p) for p in pids or [])
    if not pids:
        if not _s["down"]:
            record("creo_not_running", "nenhum processo do Creo")
        _s["down"] = True
        return
    _s["down"] = False
    previous = _s["last_pids"]
    if previous is None:
        saved = _load_saved().get("pids")
        previous = sorted(saved) if isinstance(saved, list) else None
    if previous and pids != previous:
        _s["restart"] = {"since": _now(), "old": previous, "new": pids}
        record("creo_restarted", f"PID {previous} -> {pids}: a cena anterior foi perdida", old=previous, new=pids)
    _s["last_pids"] = pids
    _save({"pids": pids, "seen": _now()})


def observe_run(res: dict) -> None:
    """Chamado depois de cada lisp.run: conta falhas de infraestrutura seguidas (nao erros de Lisp comuns)."""
    if res.get("ok"):
        _s["consecutive"] = 0
        return
    text = str(res.get("error") or "")
    if any(m.lower() in text.lower() for m in INFRA_MARKERS):
        _s["consecutive"] += 1
        record("dde_failure", text, consecutive=_s["consecutive"])
    # erro de Lisp (dialogos) nao conta: e parte do trabalho normal


def stl_failure(detail: str = "", part: str = "") -> None:
    now = _now()
    _s["stl"] = [t for t in _s["stl"] if now - t < STL_WINDOW_S] + [now]
    record("stl_failure", detail, part=part, in_window=len(_s["stl"]))


def stl_success() -> None:
    _s["stl"] = []


def acknowledge() -> None:
    """Depois de restaurar a cena (ou do usuario confirmar), limpa o estado 'restarted' e as contagens."""
    _s["restart"] = None
    _s["consecutive"] = 0
    _s["stl"] = []


def status() -> dict:
    now = _now()
    stl_recent = [t for t in _s["stl"] if now - t < STL_WINDOW_S]
    reasons = []
    st = "ok"
    if _s["down"]:
        st = "down"
        reasons.append("o Creo nao esta rodando")
    elif _s["restart"]:
        st = "restarted"
        reasons.append("o Creo foi reiniciado: a cena anterior foi perdida (veja creo_restore)")
    elif len(stl_recent) >= STL_LIMIT or _s["consecutive"] >= CONSECUTIVE_LIMIT:
        st = "degraded"
        if len(stl_recent) >= STL_LIMIT:
            reasons.append(f"{len(stl_recent)} falhas de STL nos ultimos {int(STL_WINDOW_S / 60)} min")
        if _s["consecutive"] >= CONSECUTIVE_LIMIT:
            reasons.append(f"{_s['consecutive']} falhas seguidas ao falar com o Creo")
    out = {"state": st, "reasons": reasons, "creo_pids": _s["last_pids"], "consecutive_failures": _s["consecutive"],
           "stl_failures_recent": len(stl_recent), "session": state.SESSION_ID}
    if st == "degraded":
        out["advice"] = ("Pare os lotes. Salve a cena (creo_snapshot), peca ao usuario para reiniciar o Creo e use "
                         "creo_restore. Nao insista nos mesmos comandos.")
    if st == "restarted":
        out["advice"] = "Chame creo_snapshot(action='list') e creo_restore para recuperar a ultima cena salva."
    return out


def blocks_modeling() -> str | None:
    """Motivo (texto) para recusar passos que alteram a cena, ou None. So 'degraded' e 'down' bloqueiam."""
    st = status()
    if st["state"] in ("degraded", "down"):
        return f"servidor em estado '{st['state']}': " + "; ".join(st["reasons"])
    return None
