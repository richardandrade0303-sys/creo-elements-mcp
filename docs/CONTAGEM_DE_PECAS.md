# Contagem de peças e o limite do Express

**O servidor não limita o número de peças.** O teto de 60 peças únicas é da edição gratuita (Express) do Creo Elements/Direct
Modeling ([PTC](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express)); a edição completa não tem esse teto.
Por padrão, `creo_part_budget`, `creo_audit_scene`, os geradores e `creo_validate` não recusam nada por quantidade.

Se você usa o **Express**, ligue o aviso (e a recusa antes de construir) definindo no ambiente do servidor:

```
CREO_MCP_PART_LIMIT=60
```

`0` (padrão) ou ausente = sem limite. Valor inválido ou negativo = sem limite.

> Estado verificado: só o Express 9.0 foi usado nas sessões do projeto. Numa edição licenciada o servidor **não foi testado**:
> veja [docs/TESTE_LICENCA_COMPLETA.md](TESTE_LICENCA_COMPLETA.md).

Mesmo sem limite, contar peças continua útil (cada `:PART` pesa na abertura, no STL por peça e na lista de materiais).

## 1. Como contar as peças
```
creo_get_model_tree(depth=1)
```
Devolve `[caminho, tipo, filhos]`; o tipo é `:PART`, `:WP` (workplane), `:ASSEMBLY` ou `:OTHER`. Conte as entradas `:PART`. Se você usa montagens
reais (`create_assembly`), as peças ficam dentro dos nós `:ASSEMBLY`: use `depth=2` (ou mais) para enxergá-las. Faça a contagem
**antes de cada etapa grande e depois dela**.

Se você pede peças ao catálogo, some o que cada tipo gera:

| Tipo | Peças |
|---|:---:|
| A maioria dos tipos (`flange`, `stepped_shaft`, `hex_bolt`, `spur_gear`, perfis, placas...) | 1 |
| `enclosure` (caixa + tampa) | 2 |
| `bolted_joint` | 5 |
| `bench_vise` | 7 |
| `flanged_joint` (2 flanges + junta + N parafusos + N porcas; N = 4 por padrão) | 3 + 2N |
| `roller_bearing` (2 anéis + `rollers` rolos; 12 por padrão) | 2 + `rollers` |

Exemplo de conta: 4 rolamentos de rolos (14 cada) já são 56 peças; sobram 4 para tudo o mais.

## 2. De onde vêm as peças que você não pediu
1. **`unite` entre sólidos que não se tocam.** O Creo não funde corpos disjuntos: mantém todos e dá sufixos (`/p1.1`, `/p1.2`...). O `creo_boolean`
   avisa por `objects_now`; confira que a peça-ferramenta sumiu e que o total não subiu.
2. **Peças-ferramenta.** `creo_extrude_profile` com contornos disjuntos cria `/cuttool`, `/cuttool.1`... O servidor subtrai todas e apaga as
   sobras, mas se um corte falha no meio, elas ficam. Sinal de alerta: qualquer `/cuttool.N` na árvore.
3. **Peças de teste esquecidas.** `stl_teste`, `canhao_t`, `p1`... Apague com `delete_3d` ou limpe a cena com `creo_clear_scene` (apaga **todas**
   as peças; sem desfazer).
4. **Montagem por coordenadas.** Cada corpo é uma peça própria (é o que preserva a cinemática). Um mecanismo de 40 elos soltos são 40 peças.
5. **Cortes de seção.** `SECTION` divide a peça em duas metades e a nova ganha sufixo `.1`: **dobra** a contagem das peças cortadas.
