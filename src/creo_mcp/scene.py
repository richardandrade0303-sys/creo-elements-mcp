"""Leitura da arvore de modelo (Python puro): contagem de pecas, orcamento (limite opcional), nomes suspeitos.

A arvore vem de recipes.model_tree(depth) -> lisp.parse_sexp: lista de [caminho, tipo, filhos|None], com tipo em
":PART" | ":ASSEMBLY" | ":WP" | ":OTHER". Tudo aqui e funcao pura sobre essa estrutura.
"""

from __future__ import annotations

import os
import re

EXPRESS_PART_LIMIT = 60          # limite da edicao Express 9.0 (referencia; NAO e mais aplicado por padrao)


def part_limit() -> int:
    """Limite de pecas aplicado pelo servidor. Padrao 0 = SEM limite (licenca completa).
    Para a edicao Express 9.0, defina CREO_MCP_PART_LIMIT=60 no ambiente do servidor."""
    try:
        return max(0, int(os.environ.get("CREO_MCP_PART_LIMIT", "0") or 0))
    except ValueError:
        return 0

# Pecas auxiliares que os geradores criam e apagam; se sobrarem, algo falhou no meio de um passo.
_LEFTOVER = re.compile(r"^/(cuttool|hjunk|mcp_[a-z0-9_]*tool)(\.\d+)?$", re.I)
# Parte disjunta de uma peca: o Creo nomeia sobras como /p1.1, /p1.2 ...
_SPLIT = re.compile(r"^(?P<base>.+)\.(?P<n>\d+)$")


def kind_of(node) -> str:
    return str(node[1]).upper().lstrip(":") if isinstance(node, list) and len(node) > 1 else ""


def flatten(tree, parent: str | None = None, depth: int = 1) -> list[dict]:
    """Lista plana de {path, kind, parent, depth, children}."""
    out: list[dict] = []
    for node in tree or []:
        if not isinstance(node, list) or not node:
            continue
        kids = node[2] if len(node) > 2 and isinstance(node[2], list) else []
        out.append({"path": str(node[0]), "kind": kind_of(node), "parent": parent, "depth": depth,
                    "children": [str(k[0]) for k in kids if isinstance(k, list) and k]})
        out.extend(flatten(kids, str(node[0]), depth + 1))
    return out


def parts_of(tree) -> list[str]:
    """Caminhos de TODAS as pecas (inclusive dentro de montagens)."""
    return [n["path"] for n in flatten(tree) if n["kind"] == "PART"]


def top_level(tree) -> list[dict]:
    return [n for n in flatten(tree) if n["depth"] == 1]


def assemblies_of(tree) -> list[str]:
    return [n["path"] for n in flatten(tree) if n["kind"] == "ASSEMBLY"]


def count_bodies(tree) -> int:
    return len(parts_of(tree))


def budget(tree, planned: int = 0, limit: int | None = None) -> dict:
    """Orcamento de pecas: usadas agora, previstas e o que sobra. Sem limite configurado (padrao) `limit` e `left`
    vem None e `ok` e sempre True; com CREO_MCP_PART_LIMIT=N, `ok` se usadas+previstas <= N."""
    used = count_bodies(tree)
    limit = part_limit() if limit is None else int(limit)
    if limit <= 0:
        return {"limit": None, "used": used, "planned": planned, "left": None, "ok": True,
                "note": "sem limite de pecas (licenca completa); defina CREO_MCP_PART_LIMIT=60 para a edicao Express"}
    left = limit - used - planned
    return {"limit": limit, "used": used, "planned": planned, "left": left, "ok": left >= 0,
            "note": ("conta todos os corpos :PART (inclusive dentro de :ASSEMBLY); nao verificado se copias "
                     "de uma mesma peca contam uma vez so (experimento E3)")}


def base_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def leftovers(tree) -> list[str]:
    """Pecas auxiliares que sobraram (ferramentas de corte, peca descartavel da helice)."""
    return [n["path"] for n in flatten(tree) if n["kind"] == "PART" and _LEFTOVER.match("/" + base_name(n["path"]))]


def split_pieces(tree) -> list[dict]:
    """Pecas `x.N` cujo `x` tambem existe: o Creo separou um solido disjunto em outra peca."""
    paths = {n["path"] for n in flatten(tree) if n["kind"] == "PART"}
    out = []
    for p in sorted(paths):
        m = _SPLIT.match(p)
        if m and m.group("base") in paths:
            out.append({"path": p, "of": m.group("base")})
    return out


def similar_names(tree) -> list[list[str]]:
    """Grupos de nomes que diferem so por caixa, '-' x '_' ou espacos (provavel duplicata)."""
    groups: dict[str, list[str]] = {}
    for p in parts_of(tree):
        key = re.sub(r"[\s_\-]+", "", base_name(p)).lower()
        groups.setdefault(key, []).append(p)
    return [v for v in groups.values() if len(v) > 1]


def empty_assemblies(tree) -> list[str]:
    return [n["path"] for n in flatten(tree) if n["kind"] == "ASSEMBLY" and not n["children"]]
