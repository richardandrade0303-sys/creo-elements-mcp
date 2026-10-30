"""Trava ENTRE clientes MCP (Claude Desktop, Antigravity, Claude Code ... sao processos diferentes).

O lock DDE de lisp.py vale so dentro de um processo. Aqui um arquivo (~/.creo_mcp/lease.json) diz quem esta usando o
Creo: {pid, client, started, last}. Quem chega e encontra outro processo VIVO com uso recente (menos de `ttl` segundos)
recebe uma recusa clara em vez de misturar comandos. A trava expira sozinha por inatividade ou quando o dono morre.

Python puro (so os.* e ctypes no Windows para checar se o PID existe) e testavel offline: os caminhos e o relogio podem
ser trocados nos testes.
"""

from __future__ import annotations

import json
import os
import time

from . import config

PATH = os.path.join(config.HOME, "lease.json")


def _now() -> float:
    return time.time()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32            # type: ignore[attr-defined]
            h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == 259     # STILL_ACTIVE
        except Exception:
            return True                               # na duvida, considera vivo (nao rouba a trava)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read() -> dict | None:
    try:
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, PATH)


def _stale(data: dict, ttl: float) -> bool:
    if not pid_alive(int(data.get("pid", 0) or 0)):
        return True
    return (_now() - float(data.get("last", 0) or 0)) > ttl


def _public(data: dict | None, ttl: float) -> dict | None:
    if not data:
        return None
    idle = _now() - float(data.get("last", 0) or 0)
    return {"pid": data.get("pid"), "client": data.get("client"), "idle_s": round(idle, 1),
            "held_for_s": round(_now() - float(data.get("started", 0) or 0), 1),
            "expires_in_s": round(max(0.0, ttl - idle), 1)}


def ensure(ttl: float | None = None, client: str | None = None) -> tuple[bool, dict]:
    """Garante a trava para ESTE processo. Devolve (ok, info). Se outro cliente vivo a segura, (False, info_do_dono)."""
    ttl = config.lease_ttl() if ttl is None else ttl
    me = os.getpid()
    cur = _read()
    if cur and int(cur.get("pid", 0) or 0) == me:
        cur["last"] = _now()
        if client:
            cur["client"] = client
        _write(cur)
        return True, _public(cur, ttl) or {}
    if cur and not _stale(cur, ttl):
        return False, _public(cur, ttl) or {}
    data = {"pid": me, "client": client or config.client_name(), "started": _now(), "last": _now()}
    _write(data)
    return True, _public(data, ttl) or {}


def status(ttl: float | None = None) -> dict:
    ttl = config.lease_ttl() if ttl is None else ttl
    cur = _read()
    pub = _public(cur, ttl)
    if cur and _stale(cur, ttl):
        return {"held": False, "stale": pub, "me": os.getpid()}
    return {"held": bool(cur), "holder": pub, "mine": bool(cur) and int(cur.get("pid", 0) or 0) == os.getpid(),
            "me": os.getpid()}


def release() -> dict:
    """Solta a trava, mas so se ela for deste processo."""
    cur = _read()
    if cur and int(cur.get("pid", 0) or 0) == os.getpid():
        try:
            os.remove(PATH)
        except OSError:
            pass
        return {"released": True}
    return {"released": False, "reason": "a trava nao e deste processo" if cur else "ninguem segura a trava"}


def take(client: str | None = None) -> dict:
    """Toma a trava mesmo de outro cliente vivo (uso consciente: o outro cliente vai ser recusado na proxima chamada)."""
    previous = _read()
    data = {"pid": os.getpid(), "client": client or config.client_name(), "started": _now(), "last": _now()}
    _write(data)
    return {"taken": True, "previous": _public(previous, config.lease_ttl())}


def refusal(holder: dict) -> dict:
    """Resposta padrao quando outro cliente esta usando o Creo."""
    return {
        "ok": False,
        "error": (f"o Creo esta em uso por outro cliente MCP ({holder.get('client')}, pid {holder.get('pid')}, "
                  f"ocioso ha {holder.get('idle_s')} s). Espere (a trava expira em ~{holder.get('expires_in_s')} s "
                  "sem uso), peca para o outro cliente soltar, ou use creo_lease(action='take') se for de proposito."),
        "holder": holder,
    }
