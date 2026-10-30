# Roteiro de teste em computadores com licença completa

O servidor foi desenvolvido e testado **só com o Express 9.0 no Windows**. Este roteiro serve para quem tem outra edição
(Creo Elements/Direct Modeling com licença completa) rodar o mesmo conjunto de verificações e relatar o resultado.
Não há promessa de que funcione: o que não foi testado está marcado assim em todo o projeto.

## 0. Preparação (5 min)
1. Windows, Python 3.10+, `git clone https://github.com/richardandrade0303-sys/creo-elements-mcp` e a instalação do [README](../README.md#instalação).
2. Ligue o DDE no Creo (`(oli:sd-dde-enable :on)`, veja `examples/sd_customize.lsp`).
3. **Sem limite de peças**: não defina `CREO_MCP_PART_LIMIT` (o padrão é sem limite).
4. Use uma cena vazia e salve seu trabalho antes: os testes criam peças descartáveis `/mcp_exp_*`.
5. Rode os testes offline: `python -m pytest` (196 testes; não precisam do Creo).

## 1. Saúde e capacidades
| Passo | Ferramenta | Esperado |
|---|---|---|
| 1 | `creo_get_status` | `state: ready`, `server_version: 3.0.0` |
| 2 | `creo_self_test` | `ok`; anote a versão do Lisp |
| 3 | `creo_capabilities` | anote módulos de licença: **Annotation, Sheet Metal, STEP, SAT, IGES, Parametrics...** e se `REVOLVE` existe |
| 4 | `creo_units` | mm / g / graus (todas as receitas assumem isso) |
| 5 | `creo_validate(scope="quick", confirm=true)` | tudo `ok`; depois `scope="full"` |

## 2. Tabela de verificação (marque OK / FALHOU / não se aplica)
| Item | Como | No Express 9.0 foi... |
|---|---|---|
| Mais de 60 peças | `creo_part_budget` e construir >60 corpos (ex.: `creo_pattern` de parafusos) e **salvar** `creo_snapshot` | só o aviso ao salvar com 66; **não verificado** se bloqueia criar |
| REVOLVE | `creo_lisp_apropos` com `revolve` | inexistente (esferas e eixos viram pilhas de cilindros) |
| STEP | `creo_export fmt=step` | **falhou** (3 sintaxes recusadas) |
| SAT / IGES | `creo_export fmt=sat` | não testado com sucesso |
| STL por peça | `creo_export fmt=stl` | funciona; falhas isoladas em peças roscadas grandes |
| Hélice (molas, roscas) | `creo_build_part kind=compression_spring`, `threaded_rod` | funciona; passo ≥ 1,02 × altura do perfil |
| Volume exato | `creo_inspect` | confiável em peças simples; **não** depois de cortes helicoidais |
| Clash exato | `creo_clash_check exact=true` em 2 peças pequenas | funciona; lento com roscas |
| Casca, chanfro, filete | `creo_shell`, `creo_edge_treatment` | verificado |
| Montagem real (`:ASSEMBLY`) | `creo_group` | verificado |
| Snapshot/restore | `creo_snapshot` + `creo_restore name=...` | verificado (use **name**; `replace=true` com peças na cena) |
| Anotação / desenho 2D | `(oli:sd-license-free-module-p "Annotation")` | NIL (não disponível) |
| Mancal de exemplo | refazer [`EXEMPLO_MANCAL.md`](EXEMPLO_MANCAL.md) | ver o que não foi provado nele |

## 3. Como relatar
Abra uma issue com: edição e versão do Creo (`creo_capabilities`), versão do Lisp (`creo_self_test`), a tabela acima preenchida e, nas falhas,
o campo `error` e `dialogs` exatos mais o trecho de `~/.creo_mcp/logs/server.log`. **Não** cole caminhos pessoais ou licenças.

## 4. Se o Creo travar durante o teste
Estado `degraded` do servidor ou "Fatal exception 0xc00000fd": salve em arquivo novo se o Creo deixar, feche e reabra o Creo,
chame `creo_get_status` e depois `creo_restore` pelo **nome** do snapshot. Veja [`LICOES.md`](LICOES.md).
