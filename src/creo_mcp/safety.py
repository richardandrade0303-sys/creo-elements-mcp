"""Guarda do Lisp livre: recusa, ANTES de enviar ao Creo, formas que ja travaram ou derrubaram uma sessao real.

Aplica-se so ao que o agente escreve (creo_execute_lisp, creo_run_command). As receitas internas sao testadas e nao passam
por aqui. Liberar por conta e risco: CREO_MCP_ALLOW_RISKY_LISP=1.

Cada regra tem a origem: (a) COMPROVADO = derrubou o Creo numa sessao real; (b) SUSPEITO = travou o Creo e nao foi
possivel isolar a causa; mantido bloqueado por precaucao.
"""

from __future__ import annotations

import os
import re

# simbolo (sem pacote) -> (classe, motivo)
RISKY: dict[str, tuple[str, str]] = {
    "documentation": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal (0xc00000fd) derruba o Creo"),
    "function-lambda-expression": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "describe": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "inspect": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "disassemble": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "arglist": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "function-lambda-list": ("COMPROVADO", "introspeccao de funcao OLI: estouro de pilha fatal"),
    "sd-get-faces-along-ray": ("SUSPEITO", "chamada com argumentos adivinhados deixou o Lisp sem responder ate reiniciar o Creo"),
    "quit": ("COMPROVADO", "encerra o Creo"),
    "exit": ("COMPROVADO", "encerra o Creo"),
    "sd-exit": ("SUSPEITO", "encerra o Creo"),
    "sd-quit": ("SUSPEITO", "encerra o Creo"),
}

_STRING = re.compile(r'"(?:\\.|[^"\\])*"', re.S)
_COMMENT = re.compile(r";[^\n]*")
_BLOCK = re.compile(r"#\|.*?\|#", re.S)
_SYMBOL = re.compile(r"(?<![\w:*+\-/<>=!?.$%&])(?:[a-z0-9_\-]+::?)?([a-z][\w\-*+/<>=!?.$%&]*)", re.I)


def allow_risky() -> bool:
    return os.environ.get("CREO_MCP_ALLOW_RISKY_LISP", "0").strip().lower() in ("1", "true", "yes", "sim")


def _strip(code: str) -> str:
    code = _BLOCK.sub(" ", code)
    code = _STRING.sub('""', code)
    return _COMMENT.sub(" ", code)


def check_lisp(code: str) -> list[dict]:
    """Lista de violacoes [{symbol, class, why}] (vazia = pode enviar). Ignora texto dentro de strings e comentarios."""
    found: list[dict] = []
    seen: set[str] = set()
    for m in _SYMBOL.finditer(_strip(code or "")):
        sym = m.group(1).lower()
        if sym in RISKY and sym not in seen:
            seen.add(sym)
            cls, why = RISKY[sym]
            found.append({"symbol": sym, "class": cls, "why": why})
    return found


def refusal(code: str) -> dict | None:
    """Resposta de recusa pronta para as ferramentas, ou None se o codigo pode ir."""
    if allow_risky():
        return None
    bad = check_lisp(code)
    if not bad:
        return None
    names = ", ".join(b["symbol"] for b in bad)
    return {"ok": False, "refused": True, "violations": bad,
            "error": (f"recusado antes de enviar ao Creo: {names}. " + "; ".join(f"{b['symbol']} ({b['class']}): {b['why']}"
                                                                                for b in bad)
                      + ". Para saber a assinatura use a documentacao PTC ou creo_lisp_apropos. Liberar por sua conta: "
                        "CREO_MCP_ALLOW_RISKY_LISP=1 no ambiente do servidor.")}
