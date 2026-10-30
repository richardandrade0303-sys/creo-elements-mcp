"""Blindagem: Lisp perigoso recusado antes de ir ao Creo, entradas obrigatorias, limite de tempo do clash."""
import time

import pytest

from creo_mcp import mesh, safety
from test_roadmap import iso  # noqa: F401  (fixture que isola o HOME do servidor)


@pytest.mark.parametrize("code", [
    "(describe 'oli:sd-call-cmds)",
    "(oli::documentation 'oli:extrude 'function)",
    "(function-lambda-expression #'oli:extrude)",
    "(oli:sd-get-faces-along-ray p 0 -100 20 1 0 0)",
    "(quit)",
])
def test_risky_lisp_is_refused(code, monkeypatch):
    monkeypatch.delenv("CREO_MCP_ALLOW_RISKY_LISP", raising=False)
    r = safety.refusal(code)
    assert r and r["ok"] is False and r["refused"] and r["violations"]


@pytest.mark.parametrize("code", [
    '(oli:extrude :part "/p" :wp "/w" :distance 5 :keep_profile :no)',
    '(print "describe quit documentation")',
    "; (quit)\n(+ 1 2)",
    "#| (describe x) |# (oli:sd-inq-curr-part)",
    "(let ((exit-code 1)) exit-code)",
])
def test_normal_lisp_is_allowed(code, monkeypatch):
    monkeypatch.delenv("CREO_MCP_ALLOW_RISKY_LISP", raising=False)
    assert safety.refusal(code) is None


def test_opt_out_env(monkeypatch):
    monkeypatch.setenv("CREO_MCP_ALLOW_RISKY_LISP", "1")
    assert safety.refusal("(describe x)") is None


def test_every_internal_recipe_passes_the_guard(monkeypatch):
    """As receitas internas nao podem ser barradas pela propria guarda (falso positivo quebraria uma ferramenta)."""
    import inspect
    from creo_mcp import recipes
    monkeypatch.delenv("CREO_MCP_ALLOW_RISKY_LISP", raising=False)
    src = inspect.getsource(recipes)
    assert safety.check_lisp(src) == []


def _box_tris(x0, y0, z0, s, n=1):
    # caixa de 12 triangulos subdividida n vezes por face (malha grande para provocar demora)
    pts = []
    for i in range(n):
        for j in range(n):
            a, b = x0 + s * i / n, x0 + s * (i + 1) / n
            c, d = y0 + s * j / n, y0 + s * (j + 1) / n
            for z in (z0, z0 + s):
                pts.append(((a, c, z), (b, c, z), (b, d, z)))
                pts.append(((a, c, z), (b, d, z), (a, d, z)))
    return pts


def test_interference_deadline_returns_uncertain_not_clean():
    a = _box_tris(0, 0, 0, 10, 40)
    b = _box_tris(5, 5, 5, 10, 40)
    r = mesh.interference(a, b, deadline=time.time() - 1)
    assert r["truncated"] and r.get("timed_out")
    assert r["intersects"] is False       # o chamador deve tratar timed_out como "incerto", nao como "sem interferencia"


# ------------------------------------------------------------------ ferramentas (via servidor MCP em memoria)
def _tool(name, args):
    import test_roadmap
    return test_roadmap._call(name, args)


def test_fasteners_requires_a_position(iso):
    r = _tool("creo_fasteners", {"size": 10, "grip": 25})
    assert r["ok"] is False and "at" in r["error"] and "positions" in r["error"]


def test_validate_needs_confirmation(iso):
    r = _tool("creo_validate", {"scope": "quick"})
    assert r["ok"] is False and r["dry_run"] and "confirm" in r["error"]


def test_execute_lisp_refuses_introspection(iso):
    import asyncio
    import json
    import test_roadmap
    srv = test_roadmap._server()

    async def go():
        r = await srv.server.call_tool("creo_execute_lisp", {"code": "(describe 'oli:extrude)"})
        return r

    r = asyncio.run(go())
    text = json.dumps(r, default=str)
    assert "recusado" in text and "describe" in text
