# Exemplo completo: canhão de brinquedo (conjunto de 8 corpos)

Um exemplo de ponta a ponta com o que o servidor faz bem e com o que o Express 9.0 não faz (revolução). O modelo foi feito a partir de um
desenho A4 de terceiros (não incluído aqui), **8 etapas, uma ferramenta por chamada, com o bbox esperado conferido a cada passo**. Na
sessão real, cada etapa foi delegada a um agente (`delegar_agy`, ~44 min no total); as mesmas chamadas funcionam digitadas por você ou por
qualquer assistente. O conjunto ocupa **8 peças** (veja o [limite de 60](LIMITE_60_PECAS.md)).

> Estado honesto: o bbox de cada peça foi conferido e a árvore ficou sem peças-ferramenta sobrando, mas o **volume não foi medido**
> (a exportação do STL falhou nessas peças; veja a seção 10 de [`LICOES.md`](LICOES.md)).

## Sistema de coordenadas global
`X` = direção do eixo das rodas · `Y` = direção do cano (boca para +Y) · `Z` = para cima. Origem no plano da base do berço, sobre o eixo
vertical do parafuso M6 (x = 0, y = 0). O centro do eixo das rodas fica **1 mm abaixo** desse plano (z = −1): o topo do eixo (Ø14) encosta no
teto do canal do berço.

## Técnica
- **Sem `REVOLVE`**: peças redondas são **pilhas de cilindros** com `creo_build_part(kind="stepped_shaft", params='{"sections": [[Ø, comprimento], ...]}')`
  (de baixo para cima, eixo z). Uma pilha de 36 degraus leva ~5 min (job: acompanhe com `creo_job_status`).
- `creo_extrude_profile` desenha **só no plano z**; para furos laterais, **gire primeiro** e fure depois.
- Girar/mover: `creo_run_command` com `POSITION_PA`, um por chamada, conferindo o bbox antes do próximo. A rotação é em torno da **origem global**
  e segue a **regra da mão direita**: +90° em Y leva z→x; +90° em X leva y→z (e z→−y); −90° em X leva z→y.
- Furos e cortes: peça-ferramenta + `creo_boolean(subtract)`; confirme que a ferramenta **sumiu** da árvore (`objects_now`).

## Etapa 1 — eixo (`canhao_eixo`)
Pontas Ø10×14 e corpo Ø14×70, ao longo de X; furo Ø6 passante e rebaixo Ø11×1,5 por baixo.
1. `creo_build_part(kind="stepped_shaft", params='{"sections": [[10,14],[14,70],[10,14]]}', name="canhao_eixo", verify=false)` → bbox `[-7,-7,0]..[7,7,98]`
2. `POSITION_PA "/canhao_eixo" :ROTATE :AXIS :TWO_PTA 0,0,0 0,1,0 :ROTATION_ANGLE 90 COMPLETE` → `x[0,98] y[-7,7] z[-7,7]`
3. `POSITION_PA "/canhao_eixo" :TRANSLATE -49,0,-1 COMPLETE` → `[-49,-7,-8]..[49,7,6]`
4. Furo: `creo_extrude_profile(loops='[["c",0,0,3]]', height=16, part="canhao_eixo_furo", z0=-9)` e `creo_boolean(subtract, blank="/canhao_eixo", tool="/canhao_eixo_furo")`
5. Rebaixo: `creo_extrude_profile(loops='[["c",0,0,5.5]]', height=2.5, part="canhao_eixo_rebaixo", z0=-9)` e subtrair
6. Final: `[-49,-7,-8]..[49,7,6]`, sem `*_furo` nem `*_rebaixo` na árvore.

## Etapa 2 — berço (`canhao_berco`)
Bloco 60×18×18 com laterais chanfradas, sela cilíndrica R12 no topo (fundo da sela em z = 10), canal inferior 14×6 passante em X e furo vertical Ø6.
1. Perfil lateral (sela em 10 segmentos), extrudado 18:
   `creo_extrude_profile(loops='[["p",[[30,0],[30,7],[16.5,18],[11.314,18],[9.998,15.363],[8.079,13.127],[5.673,11.425],[2.925,10.362],[0,10],[-2.925,10.362],[-5.673,11.425],[-8.079,13.127],[-9.998,15.363],[-11.314,18],[-16.5,18],[-30,7],[-30,0]]]]', height=18, part="canhao_berco", z0=0)` → `[-30,0,0]..[30,18,18]`
2. `POSITION_PA "/canhao_berco" :ROTATE :AXIS :TWO_PTA 0,0,0 1,0,0 :ROTATION_ANGLE 90 COMPLETE` → `x[-30,30] y[-18,0] z[0,18]`
3. `POSITION_PA "/canhao_berco" :TRANSLATE 0,9,0 COMPLETE` → `[-30,-9,0]..[30,9,18]`
4. Canal: `creo_extrude_profile(loops='[["r",-31,-7,31,7]]', height=7, part="canhao_berco_canal", z0=-1)` e subtrair
5. Furo: `creo_extrude_profile(loops='[["c",0,0,3]]', height=13, part="canhao_berco_furo", z0=-1)` e subtrair
6. Final: `[-30,-9,0]..[30,9,18]`.

## Etapas 3 e 4 — rodas (`canhao_roda_d`, `canhao_roda_e`)
Roda Ø70: alma Ø62×8 com 6 furos Ø10 em círculo de Ø40 e furo central Ø10, aro Ø70/Ø60×12 e cubo Ø20/Ø10×14 (cubo encaixa na ponta Ø10 do eixo).
1. Alma (é o blank final; contornos aninhados viram furos):
   `creo_extrude_profile(loops='[["c",0,0,31],["c",0,0,5],["c",0,20,5],["c",-17.321,10,5],["c",-17.321,-10,5],["c",0,-20,5],["c",17.321,-10,5],["c",17.321,10,5]]', height=8, part="canhao_roda_d", z0=3)` → `[-31,-31,3]..[31,31,11]`
2. Aro: `creo_extrude_profile(loops='[["c",0,0,35],["c",0,0,30]]', height=12, part="canhao_roda_d_aro", z0=1)`
3. Cubo: `creo_extrude_profile(loops='[["c",0,0,10],["c",0,0,5]]', height=14, part="canhao_roda_d_cubo", z0=0)`
4. `creo_boolean(unite, blank="/canhao_roda_d", tool="/canhao_roda_d_aro")` e depois com `..._cubo` (as ferramentas somem) → `[-35,-35,0]..[35,35,14]`
5. `POSITION_PA "/canhao_roda_d" :ROTATE :AXIS :TWO_PTA 0,0,0 0,1,0 :ROTATION_ANGLE 90 COMPLETE` → `x[0,14] y[-35,35] z[-35,35]`
6. `POSITION_PA "/canhao_roda_d" :TRANSLATE 35,0,-1 COMPLETE` → `[35,-35,-36]..[49,35,34]`

A roda esquerda é idêntica, com o translado `-49,0,-1` → `[-49,-35,-36]..[-35,35,34]`.

## Etapa 5 — corpo externo do cano (`canhao_cano`)
Revolução com botão esférico Ø6, pescoço Ø2, culatra semiesférica R12, contas Ø26, corpo Ø24, trecho cônico de 3° por lado (Ø24 → Ø20,1) e boca evasada de 6°.
Total de 158 mm em **36 degraus** (job de ~5 min):
```
creo_build_part(kind="stepped_shaft", name="canhao_cano", verify=false, params=
{"sections": [[3.32,1],[5.2,1],[5.92,1],[5.92,1],[5.2,1],[3.32,1],[2,3],[9.59,2],[15.87,2],[19.49,2],[21.82,2],[23.24,2],[23.81,1],[26,2],[24,16],[26,2],[24,38],[26,2],[24,18],[26,2],[23.7,3.8],[23.3,3.8],[22.9,3.8],[22.5,3.8],[22.1,3.8],[21.7,3.8],[21.31,3.8],[20.91,3.8],[20.51,3.8],[20.11,3.8],[21.81,2],[20.44,4],[21.28,4],[22.12,4],[22.96,4],[25.8,2]]})
```
Bbox esperado `[-13,-13,0]..[13,13,158]` (a soma dos comprimentos é 158).

## Etapa 6 — furo interno do cano
Ferramenta de revolução (eixo z) com fundo semiesférico R9, furo Ø18 e boca evasada, em 12 degraus; sobe 12 mm e é subtraída:
```
creo_build_part(kind="stepped_shaft", name="canhao_cano_furo", verify=false, params=
{"sections": [[7.19,1.5],[11.91,1.5],[14.62,1.5],[16.36,1.5],[17.43,1.5],[17.94,1.5],[18,119],[18.63,4],[19.47,4],[20.31,4],[21.15,4],[21.99,4]]})
```
→ `[-11,-11,0]..[11,11,148]`; depois `POSITION_PA "/canhao_cano_furo" :TRANSLATE 0,0,12 COMPLETE` → `[-11,-11,12]..[11,11,160]`;
`creo_boolean(subtract, blank="/canhao_cano", tool="/canhao_cano_furo")`. O bbox externo do cano não muda.

## Etapa 7 — cano na posição final e furo M6
1. Deitar: `POSITION_PA "/canhao_cano" :ROTATE :AXIS :TWO_PTA 0,0,0 1,0,0 :ROTATION_ANGLE -90 COMPLETE` → `x[-13,13] y[0,158] z[-13,13]`
2. `POSITION_PA "/canhao_cano" :TRANSLATE 0,-89,22 COMPLETE` → `[-13,-89,9]..[13,69,35]` (o furo M6, a 89 mm do botão, fica em y = 0; o eixo do cano fica em z = 22)
3. Furo M6 só na parede de baixo: `creo_extrude_profile(loops='[["c",0,0,3]]', height=5, part="canhao_cano_m6", z0=9)` e subtrair. Bbox inalterado.

## Etapa 8 — parafuso M6 de cabeça abaulada (`canhao_parafuso`)
Cabeça abaulada em 6 degraus (Ø3,5 a Ø10,5) + haste Ø6×20, sextavado interno de 4 mm:
1. `creo_build_part(kind="stepped_shaft", name="canhao_parafuso", verify=false, params=` `{"sections": [[3.5,0.55],[6,0.55],[7.5,0.55],[8.7,0.55],[9.5,0.55],[10.5,0.55],[6,20]]})` → `[-5.25,-5.25,0]..[5.25,5.25,23.3]`
2. Sextavado (circunscrito Ø4,62):
   `creo_extrude_profile(loops='[["p",[[2.309,0],[1.155,2],[-1.155,2],[-2.309,0],[-1.155,-2],[1.155,-2]]]]', height=3, part="canhao_parafuso_sext", z0=-1)` e subtrair
3. `POSITION_PA "/canhao_parafuso" :TRANSLATE 0,0,-9.8 COMPLETE`

Bbox medido `[-5.25,-5.25,-9.25]..[5.25,5.25,13.5]` — **o z mínimo é −9,25 e não −9,8**: o raio circunscrito do sextavado (2,309) é maior que o do
primeiro degrau (Ø3,5 → 1,75), então o corte engoliu esse degrau. É efeito dos números do exemplo, não erro do Creo.

## Resultado conferido (mm)
| Peça | Bbox global |
|---|---|
| `/canhao_eixo` | `[-49,-7,-8]..[49,7,6]` |
| `/canhao_berco` | `[-30,-9,0]..[30,9,18]` |
| `/canhao_roda_d` | `[35,-35,-36]..[49,35,34]` |
| `/canhao_roda_e` | `[-49,-35,-36]..[-35,35,34]` |
| `/canhao_cano` | `[-13,-89,9]..[13,69,35]` |
| `/canhao_parafuso` | `[-5.25,-5.25,-9.25]..[5.25,5.25,13.5]` |

## Aproximações e limitações
- Esferas e cones são **escadas de degraus** (botão a cada 1 mm, culatra a cada 2 mm, cone a cada 3,8 mm, boca a cada 4 mm); o perfil é serrilhado.
- A roda não tem os chanfros do desenho original (cantos côncavos entre aro, alma e cubo).
- O furo M6 é um furo **liso** Ø6, sem rosca.
- Cano oco confirmado só pelo aspecto na tela; volume interno e espessuras não foram medidos.
- Sem verificação de interferência entre as peças além dos bbox (há contatos por tangência: eixo × teto do canal, cano × fundo da sela).

## Lições deste exemplo
1. `POSITION_PA :ROTATE` segue a regra da mão direita: preveja o bbox antes de girar.
2. Ferramentas só em plano z: fure depois de girar a peça para a posição final.
3. Prompt que funciona para delegar: cabeçalho de regras + chamadas **exatas** + bbox esperado por passo + "se divergir, relate sem consertar".
4. Etapas de 4 a 8 minutos, no máximo ~8 chamadas por etapa; uma pilha de 36 degraus é um job de ~5 min.
5. Ao cortar um sextavado numa cabeça escalonada, compare o raio circunscrito com os degraus que ele atravessa.
