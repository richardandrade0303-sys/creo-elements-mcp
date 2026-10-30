"""Chaves de configuracao por variavel de ambiente (lidas a cada chamada, nada de estado global).

Tudo aqui e Python puro e testavel offline. Nomes das variaveis:

  CREO_MCP_SAFE_MODE=1          so ferramentas tipadas: desliga Lisp livre (creo_execute_lisp / creo_run_command)
  CREO_MCP_ALLOW_LISP=0         desliga so o Lisp livre (padrao: ligado, como nas versoes anteriores)
  CREO_MCP_ALLOW_DDE_RAW=1      liga creo_dde_raw (diagnostico; envia texto cru ao Creo; padrao: desligado)
  CREO_MCP_ALLOW_DELEGAR=0      desliga delegar_* (o conteudo anexado vai para o servico do Google)
  CREO_MCP_LEASE=0              desliga a trava entre clientes (padrao: ligada)
  CREO_MCP_LEASE_TTL=120        segundos de inatividade para a trava expirar
  CREO_MCP_CLIENT=Antigravity   nome mostrado a quem encontrar o Creo ocupado
  CREO_MCP_EXPORT_ROOTS=D:/x;E:/y   pastas extras onde exportar/importar (alem de ~/.creo_mcp)
  CREO_MCP_HOT_RELOAD=0|1       recarga a quente dos modulos (padrao: ligada se rodar da pasta do projeto, desligada se
                                instalado com pip; nunca durante um job)
  CREO_MCP_SNAPSHOT_DIR=D:/x    pasta dos pontos de restauracao (padrao: ~/creo_mcp_snapshots; SEM componente que comece
                                com ponto: o LOAD_PACKAGE do Creo recusa caminhos como C:/Users/x/.creo_mcp/...)
"""

from __future__ import annotations

import os

HOME = os.path.join(os.path.expanduser("~"), ".creo_mcp")

_TRUE = {"1", "true", "yes", "on", "sim", "s"}
_FALSE = {"0", "false", "no", "off", "nao", "n"}


def flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def safe_mode() -> bool:
    return flag("CREO_MCP_SAFE_MODE", False)


def free_lisp_allowed() -> bool:
    return (not safe_mode()) and flag("CREO_MCP_ALLOW_LISP", True)


def dde_raw_allowed() -> bool:
    return (not safe_mode()) and flag("CREO_MCP_ALLOW_DDE_RAW", False)


def delegar_allowed() -> bool:
    return flag("CREO_MCP_ALLOW_DELEGAR", True)


def lease_enabled() -> bool:
    return flag("CREO_MCP_LEASE", True)


def lease_ttl() -> float:
    return max(5.0, number("CREO_MCP_LEASE_TTL", 120.0))


def client_name() -> str:
    return os.environ.get("CREO_MCP_CLIENT", "").strip() or f"cliente-pid-{os.getpid()}"


def installed_package() -> bool:
    """True se o pacote foi instalado com pip (site-packages): ai nao ha recarga a quente por padrao."""
    return "site-packages" in os.path.abspath(__file__).replace("\\", "/")


def hot_reload() -> bool:
    """Recarga a quente dos modulos: ligada por padrao so quando o servidor roda a partir da pasta do projeto (desenvolvimento).
    CREO_MCP_HOT_RELOAD=1/0 forca. Nunca recarrega durante um job."""
    return flag("CREO_MCP_HOT_RELOAD", not installed_package())


def export_roots() -> list[str]:
    """Pastas onde exportacao/importacao e permitida: ~/.creo_mcp e as de CREO_MCP_EXPORT_ROOTS (separadas por ';')."""
    roots = [HOME]
    extra = os.environ.get("CREO_MCP_EXPORT_ROOTS", "")
    for item in extra.replace(os.pathsep, ";").split(";"):
        item = item.strip()
        if item:
            roots.append(item)
    return roots


def snapshot_dir() -> str:
    """Pasta dos snapshots (.pk2). Fica FORA de ~/.creo_mcp de proposito: o LOAD_PACKAGE do Creo Express 9.0 recusa
    (Error 64 'file name is not valid') arquivos dentro de pastas cujo nome comeca com ponto."""
    raw = os.environ.get("CREO_MCP_SNAPSHOT_DIR", "").strip()
    return raw if raw else os.path.join(os.path.expanduser("~"), "creo_mcp_snapshots")


def has_dot_component(path: str) -> bool:
    """True se algum componente do caminho comeca com ponto (ex.: .creo_mcp), exceto '.' e '..'."""
    parts = [p for p in os.path.normpath(path).replace("\\", "/").split("/") if p]
    return any(p.startswith(".") and p not in (".", "..") for p in parts)


def path_allowed(path: str, roots: list[str] | None = None) -> bool:
    """True se `path` (sem seguir ..) fica dentro de alguma pasta permitida."""
    try:
        target = os.path.normcase(os.path.abspath(path))
    except (OSError, ValueError):
        return False
    for root in roots if roots is not None else export_roots():
        base = os.path.normcase(os.path.abspath(root))
        if target == base or target.startswith(base.rstrip("\\/") + os.sep):
            return True
    return False


def policy_summary() -> dict:
    """Resumo do que esta ligado (para creo_health / creo_capabilities)."""
    return {
        "safe_mode": safe_mode(),
        "free_lisp": free_lisp_allowed(),
        "dde_raw": dde_raw_allowed(),
        "delegar": delegar_allowed(),
        "lease": lease_enabled(),
        "lease_ttl_s": lease_ttl(),
        "hot_reload": hot_reload(),
        "export_roots": export_roots(),
        "snapshot_dir": snapshot_dir(),
    }
