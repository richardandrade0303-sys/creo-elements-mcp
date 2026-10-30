"""Sem CREO_MCP_PART_LIMIT o servidor nao limita o numero de pecas (licenca completa)."""
import pytest

from creo_mcp import analysis, lisp, scene
from creo_mcp.hardware import fastener_layout
from creo_mcp.parts import PartError


def _tree(n):
    return lisp.parse_sexp("(" + " ".join(f'("/p{i}" :PART NIL)' for i in range(n)) + ")")


def test_default_is_unlimited():
    assert scene.part_limit() == 0
    b = scene.budget(_tree(500), 200)
    assert b["ok"] and b["limit"] is None and b["left"] is None and b["used"] == 500


def test_env_enables_express_limit(monkeypatch):
    monkeypatch.setenv("CREO_MCP_PART_LIMIT", "60")
    b = scene.budget(_tree(65), 0)
    assert not b["ok"] and b["left"] == -5 and b["limit"] == 60


def test_bad_env_value_means_unlimited(monkeypatch):
    monkeypatch.setenv("CREO_MCP_PART_LIMIT", "abc")
    assert scene.part_limit() == 0
    monkeypatch.setenv("CREO_MCP_PART_LIMIT", "-5")
    assert scene.part_limit() == 0


def test_audit_has_no_part_limit_finding_by_default():
    a = analysis.audit(_tree(120))
    assert not [f for f in a["findings"] if f["code"].startswith("part_limit")]


def test_fastener_layout_accepts_many_positions():
    built = fastener_layout([(i * 20.0, 0.0) for i in range(61)], size=6, length=0, grip=10)
    assert len(built.plan.parts()) >= 61
