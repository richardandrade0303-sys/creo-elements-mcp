"""Registro das pecas que ESTE servidor criou (base do creo_clear_scene seguro).

Cada ferramenta que cria pecas (creo_build_part, creo_extrude_profile, create_block ...) registra os caminhos de topo
aqui. O apagamento seguro so toca em caminhos registrados e que ainda existem na cena: o que o usuario modelou a mao
(ou criou noutra sessao) nunca entra na lista. Arquivo: ~/.creo_mcp/created.json
"""

from __future__ import annotations

import json
import os
import time

from . import config, state

PATH = os.path.join(config.HOME, "created.json")
MAX_ENTRIES = 2000
_now = time.time


def _norm(path: str) -> str:
    p = "/" + path.strip().lstrip("/")
    return p


def top_of(path: str) -> str:
    """'/conj/peca' -> '/conj' (o registro guarda o objeto de topo)."""
    p = _norm(path)
    return "/" + p.strip("/").split("/", 1)[0]


def _load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("objects"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"objects": {}}


def _save(data: dict) -> None:
    objs = data["objects"]
    if len(objs) > MAX_ENTRIES:
        for k in sorted(objs, key=lambda k: objs[k].get("ts", 0))[:len(objs) - MAX_ENTRIES]:
            del objs[k]
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, PATH)
    except OSError:
        pass


def add(paths, tool: str = "") -> list[str]:
    """Registra objetos de topo criados. Devolve os caminhos gravados."""
    data = _load()
    out = []
    for p in paths or []:
        top = top_of(p)
        if top == "/":
            continue
        data["objects"][top] = {"tool": tool, "ts": round(_now(), 1), "session": state.SESSION_ID}
        out.append(top)
    if out:
        _save(data)
    return out


def known() -> dict:
    return dict(_load()["objects"])


def forget(paths) -> None:
    data = _load()
    for p in paths or []:
        data["objects"].pop(top_of(p), None)
    _save(data)


def plan_clear(existing_top: list[str], parts_only: bool = True, tree_kinds: dict | None = None) -> dict:
    """Separa o que o servidor pode apagar do que nao pode.

    existing_top: caminhos de topo presentes na cena. tree_kinds: {caminho: 'PART'|'ASSEMBLY'|'WP'|...} (opcional).
    Devolve {'deletable': [...], 'protected': [...]}: 'protected' = existe na cena mas nao foi criado por este servidor
    (ou nao e peca/montagem quando parts_only).
    """
    reg = known()
    deletable, protected = [], []
    for p in existing_top:
        kind = (tree_kinds or {}).get(p, "PART")
        if parts_only and kind in ("WP", "OTHER"):
            continue                     # workplanes ficam sempre
        (deletable if p in reg else protected).append(p)
    return {"deletable": deletable, "protected": protected}
