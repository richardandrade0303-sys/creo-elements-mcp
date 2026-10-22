# O limite de 60 peças do Creo Elements/Direct Modeling Express

Este guia explica o limite, como ele aparece, como contar o que você já tem e como planejar um modelo para caber.
Ele **não** ensina a contornar a licença: o limite é da edição gratuita, e a edição completa não o tem
([PTC, página do Express](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express): "assemblies with up to 60 unique parts" no Express).

## 1. O que se sabe e o que não se sabe

| Fato | Origem | Estado |
|---|---|---|
| A edição Express permite montagens com até 60 peças únicas; a completa não tem o teto | Página oficial da PTC | Fonte pública |
| Ao **salvar o pacote** (`save_package`) com mais de 60 corpos sólidos, o Creo mostra uma caixa modal: "You have exceeded the 60 part limit of the Express version (NN parts)" | Sessões do projeto | Observado |
| Uma cena chegou a 66 peças antes de salvar (a caixa apareceu no salvamento) | Sessões do projeto | Observado |
| O limite também bloqueia a **criação** de peças além de 60 | — | **Não verificado** |
| O Creo descontaria peças idênticas ("únicas" no texto da PTC) | — | **Não verificado**: conte cada `:PART` |
| O servidor funciona do mesmo jeito numa edição licenciada | — | **Não testado**: só o Express 9.0 foi usado |

## 2. Como contar as peças
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

## 3. De onde vêm as peças que você não pediu
1. **`unite` entre sólidos que não se tocam.** O Creo não funde corpos disjuntos: mantém todos e dá sufixos (`/p1.1`, `/p1.2`...). O `creo_boolean`
   avisa por `objects_now`; confira que a peça-ferramenta sumiu e que o total não subiu.
2. **Peças-ferramenta.** `creo_extrude_profile` com contornos disjuntos cria `/cuttool`, `/cuttool.1`... O servidor subtrai todas e apaga as
   sobras, mas se um corte falha no meio, elas ficam. Sinal de alerta: qualquer `/cuttool.N` na árvore.
3. **Peças de teste esquecidas.** `stl_teste`, `canhao_t`, `p1`... Apague com `delete_3d` ou limpe a cena com `creo_clear_scene` (apaga **todas**
   as peças; sem desfazer).
4. **Montagem por coordenadas.** Cada corpo é uma peça própria (é o que preserva a cinemática). Um mecanismo de 40 elos soltos são 40 peças.
5. **Cortes de seção.** `SECTION` divide a peça em duas metades e a nova ganha sufixo `.1`: **dobra** a contagem das peças cortadas.

## 4. Como planejar um modelo para caber
**Orçamento de peças.** Antes de modelar, faça uma lista de corpos (uma lista de materiais) e some. Exemplo para um conjunto articulado:

| Subconjunto | Corpos se tudo ficar solto | Corpos se fundir por contato |
|---|:---:|:---:|
| 10 elos de corrente (2 placas + 2 pinos + 2 buchas cada) | 60 | 10 |
| 4 sapatas e seus suportes | 8 | 4 |
| 2 terminais | 2 | 2 |
| **Total** | **70** | **16** |

(Os números são ilustrativos; os do projeto real estão no próximo item.)

**Funda o que não precisa se mover.** Pino, placas e rolete de um mesmo elo que se interceptam viram **um** sólido contínuo
(basta que se toquem ou se sobreponham). Nas sessões do projeto, um conjunto articulado de ~393 mm passou de 66 para menos de 60 peças
assim, e uma bacia de concreto com canaletas, grades, tubos, guarda-corpo e escada ficou em 15 peças, unindo cada elemento logo depois
de extrudado. **Esses números foram relatados nas sessões e não foram reverificados ao vivo** para este documento.

**Não funda o que precisa se mover.** Unir peças tangentes de um mecanismo destrói a cinemática (lição da morsa de bancada). Aqui a saída é
simplificar: menos rolos num rolamento (`rollers`), menos parafusos (`bolts`), um bloco no lugar de uma montagem figurativa.

**Divida em subconjuntos.** Se o produto tem mais de 60 corpos que precisam existir, modele um subconjunto por sessão, exporte (STL por peça
ou pacote) e junte o resultado fora do Creo (viewer, outro CAD ou software de impressão). Um STL por peça é a regra de qualquer forma.

**Passe para uma edição licenciada** quando o modelo precisa mesmo de mais de 60 peças reais no mesmo conjunto.

## 5. Checklist rápido
- [ ] `creo_get_model_tree` (`depth=1`, ou mais com montagens) e anotar o total de `:PART`.
- [ ] Antes de pedir uma peça multicorpo do catálogo, somar o que ela gera.
- [ ] Depois de cada `creo_boolean` / `creo_extrude_profile` com vários contornos: o total subiu mais do que o esperado? há `/cuttool.N`?
- [ ] Apagar peças de teste (`delete_3d`).
- [ ] Salvar um pacote parcial **antes** de passar de ~50 peças (se a caixa de limite aparecer só no salvamento, você descobre o problema com a cena ainda utilizável).
- [ ] Se for passar de 60 mesmo: dividir em subconjuntos ou migrar de edição.

## 6. O que este projeto não faz
Não remove, não esconde e não burla a verificação de licença do Creo. Se você precisa de mais de 60 peças, a solução é organizar o
modelo (itens acima) ou usar uma edição licenciada.
