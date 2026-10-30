# Exemplo: mancal bipartido com eixo escalonado, dois rolamentos e roscas

Modelo feito ao vivo no Creo Elements/Direct Modeling Express 9.0 com este servidor (v3.0.0), pedido em linguagem natural e refinado em
várias rodadas. Serve de roteiro e de **teste de regressão em outra máquina** (veja [TESTE_LICENCA_COMPLETA.md](TESTE_LICENCA_COMPLETA.md)).
Lista de materiais gerada pelo servidor: [`examples/mancal/bom_mancal.csv`](../examples/mancal/bom_mancal.csv) (52 peças).

> Honestidade primeiro: as dimensões abaixo são do modelo feito; **não é um projeto verificado por cálculo** (carga, vida do rolamento,
> ajustes). Os anéis elásticos e os retentores são nominais.

## O que o conjunto tem
| Peça | Detalhe |
|---|---|
| `/eixo` | Escalonado, z 0–240, Ø máx. 35, com canais e rasgo de chaveta; `/chaveta` 6×6×16 (DIN 6885) |
| `/mancal` + `/mancal.1` | Carcaça bipartida no plano y=0 (metade inferior y −36..0, superior y 0..36), furo Ø52 H7 para os rolamentos, passagem Ø44 |
| `/rola_interno`, `/rola_externo` + `/e0_0`..`/e8_0` | Rolamento 6205 (25×52×15) em z 35–50: dois anéis e 9 esferas Ø7,938 (**facetadas**, pilha de discos: o Express não tem REVOLVE) |
| `/rolb_ai`, `/rolb_ae`, `/rolb_r1`..`r12` | Rolamento de rolos NU205 em z 190–205 (12 rolos) |
| `/tampa_esq`, `/tampa_dir` | Tampas com furos em círculo de parafusos, sede de retentor Ø32 |
| `/ret_esq`, `/ret_dir` | Retentores 20×32×7 (nominais) |
| `/pm6_1..8` | Parafusos M6×20 (tampas), furos roscados M6×1 no mancal |
| `/pm8_1..4` | Parafusos M8×50 que unem as duas metades; furo roscado M8 na inferior, passante Ø9 na superior |
| `/pino`, `/pino.1` | Pinos de posicionamento Ø6 entre as metades |
| `/graxeira_a`, `/graxeira_b` | Graxeiras M6 (parafuso sextavado M6×12 modificado) |
| `/anel_esq`, `/anel_dir` | Anéis elásticos Ø33/Ø23,2 × 1,2 em z 50–51,2 e 188,8–190 (nominais; **as posições reais dos canais do eixo não foram conferidas**) |

## Como foi feito (ferramentas)
1. Eixo, chaveta, rolamentos e carcaça: `creo_build_part` / `creo_bearing_std` / `creo_extrude_profile` (um passo por chamada, bbox conferido).
2. Divisão do mancal: `creo_section` no plano y=0 (a metade nova ganha o sufixo `.1`).
3. Furos: `creo_holes` só funciona nas faces z; os laterais (M8 em y) foram feitos com cortadores (`creo_extrude_profile` com o eixo girado 90° por `POSITION_PA`) e `subtract_3d`.
4. **Roscas helicoidais**, uma por vez, com **um workplane novo por rosca** (o perfil fica no workplane e causa "Overlapping loops (Error 291)"):
   ```lisp
   (create_workplane :new :name "wt2")
   (oli:sd-put-buffer "POSITION_WP \"/wt2\" :PT_PT_PT 0,0,30 1,0,30 0,1,30 COMPLETE")   ; plano z=30; assíncrono, confira a origem
   (current_wp "/wt2")
   (polygon x0,z0 x0,z1 x1,zm :close)            ; triângulo 60° do perfil, FORA do eixo do furo
   (helix_dia :remove_material :blank_part "/mancal" :wp "/wt2" :axis :two_pta x,y,z1 x,y,z2 :pitch 1.0 :turns 10)
   ```
   Passo ≥ 1,02 × a altura do perfil. Perfil M6: raio 2,4/3,3 → 3,0/4,0 (furo na broca de macho Ø5, rosca M6×1).
5. Esferas: polígono de 20 lados extrudado em cilindros aninhados a partir do plano médio do rolamento, depois `unite_3d :blank ... :tools ... :keep_tools :no`.
6. Fechamento: `creo_snapshot`, `creo_bom` (CSV), `creo_audit_scene`.

## O que não ficou provado (leia antes de reaproveitar)
- **Encaixe das roscas**: o parafuso e o furo têm hélices próprias, mas o encaixe **nunca foi verificado** por corte de seção nem por colisão exata (leva >5 min em peças roscadas; o servidor agora tem `time_budget_s`).
- **M6 do lado x−** removeram bem menos material que as do lado x+ (~10 contra ~35 mm³): causa desconhecida; refazer a rosca e conferir por seção.
- Leitura de **volume** depois de cortes helicoidais é pouco confiável; a **área** subiu, e foi o único sinal usado.
- STEP não exportou no Express (veja [LICOES.md](LICOES.md), seção 11); o STL por peça das peças roscadas falhou ou demorou.
- Material não atribuído: só o rolamento 6205 saiu com massa na BOM.
- Em uma edição completa, estes pontos são exatamente o que vale testar primeiro.
