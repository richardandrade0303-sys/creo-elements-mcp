"""Os testes existentes foram escritos para a edicao Express (limite de 60 pecas): ligam o limite por padrao.
Os testes de `tests/test_part_limit.py` removem a variavel para provar o comportamento sem limite (licenca completa)."""
import pytest


@pytest.fixture(autouse=True)
def _express_limit(monkeypatch, request):
    if request.module.__name__.endswith("test_part_limit"):
        monkeypatch.delenv("CREO_MCP_PART_LIMIT", raising=False)
    else:
        monkeypatch.setenv("CREO_MCP_PART_LIMIT", "60")
