# Changelog

## 3.0.0
- Base: servidor 3.0.0 (64 ferramentas): snapshot/restore, BOM, auditoria, clash, exportação com manifesto, furos tipados, fixadores,
  rolamentos e polias normalizados, perfis, ISO 286, jornal, replay, lote, relatórios, lease entre clientes e saúde do servidor.
- **Sem limite de peças por padrão.** O teto de 60 é do Express; `CREO_MCP_PART_LIMIT=60` liga o aviso e a recusa.
- **Guarda de Lisp livre** (`safety.py`): recusa introspecção de funções OLI, `quit`/`exit` e `sd-get-faces-along-ray` antes de enviar ao Creo
  (`creo_execute_lisp`, `creo_run_command`, `creo_dde_raw`). `CREO_MCP_ALLOW_RISKY_LISP=1` libera.
- Lisp livre e comandos recusam em estado `degraded` (3 falhas seguidas de DDE); `creo_get_status` limpa quando o Creo responde.
- `creo_validate` exige `confirm=true`; `creo_fasteners` exige `at` ou `positions`.
- `creo_clash_check` ganhou `time_budget_s` (padrão 240); par que estoura o tempo volta `interferes: null`, nunca "sem interferência".
- Limites de tempo nos parâmetros de `creo_execute_lisp`, `creo_run_command` e `creo_dde_raw`.
- Removidos módulos obsoletos da v1 (`client.py`, `models.py`, `lisp_generator.py`) e os testes que os usavam.
- Docs: `CONTAGEM_DE_PECAS.md`, `EXEMPLO_MANCAL.md`, `TESTE_LICENCA_COMPLETA.md`; `LICOES.md` seção 11.
- 196 testes offline.

## 2.8.0
- Primeira versão publicada (24 ferramentas, catálogo de 27 peças, delegação ao agy/Gemini).
