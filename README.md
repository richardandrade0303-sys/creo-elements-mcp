# Creo Elements/Direct MCP

Servidor [MCP](https://modelcontextprotocol.io) em Python que deixa um assistente de IA (Claude Desktop, Claude Code,
Antigravity ou qualquer cliente MCP via stdio) **modelar em 3D dentro do PTC Creo Elements/Direct Modeling Express 9.0**.
Ele fala com o Creo pelo servidor DDE nativo (serviço `PESD`, tópico `GENERAL`), usando só `ctypes`: sem ponte em Lisp,
sem caixa de correio de arquivos, sem dependência além do SDK `mcp`.

> **Aviso.** Projeto independente, sem vínculo com a PTC. "Creo" e "Creo Elements/Direct" são marcas da PTC.
> Só foi testado com o **Express 9.0 no Windows**. Veja [Limitações conhecidas](#limitações-conhecidas) antes de usar em trabalho que importa.

[English version](README.en.md)

## Sumário
1. [O que dá para fazer](#o-que-dá-para-fazer)
2. [Requisitos](#requisitos)
3. [Instalação](#instalação)
4. [Primeira sessão](#primeira-sessão)
5. [Ferramentas](#ferramentas)
6. [Catálogo de peças](#catálogo-de-peças)
7. [Limite de 60 peças do Express](#limite-de-60-peças-do-express)
8. [Como funciona por dentro](#como-funciona-por-dentro)
9. [Regras que evitam travar o Creo](#regras-que-evitam-travar-o-creo)
10. [Delegação opcional (agy / Gemini)](#delegação-opcional-agy--gemini)
11. [Segurança e privacidade](#segurança-e-privacidade)
12. [Solução de problemas](#solução-de-problemas)
13. [Limitações conhecidas](#limitações-conhecidas)
14. [Desenvolvimento e testes](#desenvolvimento-e-testes)
15. [Estrutura do repositório](#estrutura-do-repositório)

## O que dá para fazer
Você descreve a peça em linguagem natural e o assistente chama as ferramentas. Exemplos de pedidos:

- "Crie uma flange com 12 furos de Ø18 em círculo de passo 125 e me diga o bbox."
- "Faça um eixo escalonado Ø30/Ø40/Ø50 com rasgo de chaveta e chanfro de 1 mm nas pontas."
- "Monte uma junta parafusada M12 com duas chapas, arruela, parafuso e porca (5 peças, cada uma na sua cota)."
- "Extrude este perfil (polígono com um furo) 12 mm e exporte o STL."
- "Faça a casca desta caixa com parede de 3 mm e o topo aberto."
- "Mostre a árvore do modelo e tire um screenshot."

Cobertura: catálogo de **27 peças industriais** com verificação automática (bbox exato + volume do STL contra o
valor analítico), perfis livres extrudados, booleanas, chanfro e filete, casca, hélice (molas e roscas reais), montagens
por coordenadas, nós `:ASSEMBLY`, cor RGB, mover/girar peças, seção, espelho, varredura, volume/massa exatos e Lisp livre.

## Requisitos
| Item | Versão / observação |
|---|---|
| Sistema | **Windows** (o DDE só existe nele) |
| Creo | **Creo Elements/Direct Modeling Express 9.0**, aberto durante o uso ([página da PTC](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express)) |
| Python | 3.10 ou superior (testado com 3.13 e 3.14) |
| SDK MCP | `mcp>=2.3.0` (usa `mcp.server.mcpserver`; testado com 2.3.0) |
| Cliente MCP | Um por vez: Claude Desktop **ou** Claude Code **ou** Antigravity (veja [Regras](#regras-que-evitam-travar-o-creo)) |
| Delegação (opcional) | Node.js + o script `delegar-gemini.mjs` e o Antigravity CLI (`agy`) ou Gemini CLI. Não fazem parte deste repositório |

## Instalação

### 1. Baixe o projeto e crie o ambiente
```bat
git clone https://github.com/richardandrade0303-sys/creo-elements-mcp.git
cd creo-elements-mcp
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
Opcional, para rodar os testes: `.venv\Scripts\python.exe -m pip install pytest`.

### 2. Habilite o servidor DDE no Creo
O servidor MCP conversa com o Creo por DDE, e o Creo só liga o DDE se alguém mandar `(oli:sd-dde-enable :on)`.
Faça isso uma vez, no arquivo de customização do usuário:

1. Abra (ou crie) `%APPDATA%\PTC\Creo Elements Direct Modeling Express 9.0\90.0\sd_customize.lsp`.
2. Acrescente a linha de [`examples/sd_customize.lsp`](examples/sd_customize.lsp):
   ```lisp
   (oli:sd-dde-enable :on)
   ```
3. Feche e reabra o Creo. Sem reiniciar, dá para ligar na hora digitando a mesma linha na linha de comando do Creo.

### 3. Registre o servidor no seu cliente MCP
Em todos os casos o comando é o Python da `.venv` rodando o módulo `creo_mcp`, com `PYTHONPATH` apontando para `src`
(use o caminho completo da sua pasta; barras invertidas duplas no JSON).

**Claude Desktop** — edite `%APPDATA%\Claude\claude_desktop_config.json` (modelo em
[`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)):
```json
{
  "mcpServers": {
    "creo-elements-direct": {
      "command": "C:\\caminho\\para\\creo-elements-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "creo_mcp"],
      "env": { "PYTHONPATH": "C:\\caminho\\para\\creo-elements-mcp\\src" }
    }
  }
}
```
Depois **feche o Claude Desktop pelo ícone da bandeja** (não só a janela) e abra de novo.

**Claude Code**:
```bat
claude mcp add --env PYTHONPATH=C:\caminho\para\creo-elements-mcp\src --transport stdio creo-elements-direct -- C:\caminho\para\creo-elements-mcp\.venv\Scripts\python.exe -m creo_mcp
```
(As opções vêm **antes** do nome do servidor; `--transport stdio` entre `--env` e o nome evita que o nome seja lido como mais um par `CHAVE=valor`.
Sintaxe conforme a [documentação do Claude Code](https://code.claude.com/docs/en/mcp).)

**Antigravity** — mesma entrada `mcpServers` no `mcp_config.json` do Antigravity (modelo em
[`examples/antigravity_mcp_config.json`](examples/antigravity_mcp_config.json); o caminho do arquivo depende da versão, e numa instalação
testada ficava em `%USERPROFILE%\.gemini\config\mcp_config.json`). Depois, *Manage MCP Servers* ou reinicie o Antigravity.
Para liberar sem pedir aprovação a cada uso, a regra é `mcp(creo-elements-direct/*)`.

**Outros clientes stdio**: o servidor é `python -m creo_mcp` (ou o script `creo-mcp` se você instalar o pacote com `pip install -e .`).

> A delegação grava em `<projeto>\.delegacoes`, partindo da pasta acima de `src/`. Por isso o servidor deve rodar a partir
> do código-fonte (`PYTHONPATH=src` ou `pip install -e .`), não de uma instalação copiada para `site-packages`.

### 4. Confira
Com o Creo aberto, peça ao assistente: **"chame `creo_get_status`"**. O esperado:
```json
{ "server_version": "2.8.0", "lisp_layer": "2.1-file-load", "state": "ready", "...": "..." }
```
Se vier outro `state`, vá para [Solução de problemas](#solução-de-problemas). Depois rode `creo_self_test` uma vez por sessão.

## Primeira sessão
Um fluxo seguro, **um passo por chamada**, lendo `ok`, `error` e `dialogs` antes do próximo:

1. `creo_get_status` → `state: ready`.
2. `creo_get_model_tree` → veja o que já existe antes de criar (não crie `/p1` sem checar).
3. Peça pronta: `creo_build_part(kind="list")` mostra o catálogo; depois, por exemplo,
   `creo_build_part(kind="flange", params='{"od": 200, "bolts": 12}', name="minha_flange")`.
4. Se a resposta for `{"state": "running", "job": "..."}`, **não repita a chamada**: use `creo_job_status(job)` até vir o relatório.
5. Forma livre: `creo_extrude_profile(loops='[["p",[[0,0],[100,0],[100,60],[0,60]]],["c",50,30,8]]', height=12, part="placa")`
   (o círculo dentro do polígono vira furo).
6. Acabamento: `creo_edge_treatment` (chanfro/filete) e `creo_shell` (casca).
7. Confira: `creo_get_part_info` (bbox de **cada** peça deve bater) e `creo_screenshot` quando a forma importar.
8. Exporte: `creo_export_stl(file_path="C:/Users/SEU_USUARIO/.creo_mcp/exports/placa.stl", part="/placa")` — **um arquivo por peça**
   (veja [lições](docs/LICOES.md)). A pasta de destino precisa existir.

## Ferramentas
O servidor registra **24 ferramentas**.

| Grupo | Ferramentas |
|---|---|
| Estado | `creo_get_status`, `creo_self_test`, `creo_screenshot`, `creo_dismiss_dialogs` |
| Lisp e comandos | `creo_execute_lisp`, `creo_run_command`, `creo_cancel_command`, `creo_lisp_apropos`, `creo_dde_raw` |
| Consulta | `creo_get_model_tree` (árvore), `creo_get_part_info` (bounding box global) |
| Modelagem básica | `creo_create_block`, `creo_create_cylinder`, `creo_boolean` (unite / subtract / intersect), `creo_export_stl`, `creo_clear_scene` |
| Peças industriais | `creo_build_part` (catálogo de 27; `kind="list"` é a fonte da verdade), `creo_extrude_profile` (contornos livres), `creo_job_status` |
| Acabamento | `creo_edge_treatment` (chanfro simétrico/assimétrico, filete), `creo_shell` (casca) |
| Delegação (não usa o Creo) | `delegar_agy`, `delegar_resultado`, `delegar_diagnostico` |

Pontos que costumam confundir:

- **`creo_execute_lisp`**: para comandos de **entrada fixa** (`rectangle`, `circle`, `extrude`, `create_part`, `unite_3d`...). Use nomes com pacote (`oli:sd-...`) e confirme nomes com `creo_lisp_apropos` antes de usar.
- **`creo_run_command`**: para comandos de **entrada variável** (`STL`, `POSITION_PA`, `POSITION_WP`...), digitados na linha de comando e terminando em `COMPLETE`. Chamados como função Lisp eles ficam abertos esperando entrada e geram uma cadeia de "Error 414".
- **`creo_extrude_profile`** desenha no plano z (`z0` = cota do plano; `direction` `+w` ou `-w`). Contornos aninhados viram furos; contornos disjuntos viram **várias** peças-ferramenta (o servidor subtrai todas).
- **`creo_boolean`** só une sólidos que se **tocam** ou se sobrepõem; sólidos disjuntos não são unidos e o Creo não reclama. Confira `objects_now` (a ferramenta some da árvore no sucesso).
- **`creo_edge_treatment`**: arestas escolhidas por seletores em coordenadas globais (`["all"]`, `["z", nível]`, `["zr", nível, rmax, rmin]`, `["vertical"]`, `["edge", x1,y1,z1,x2,y2,z2]`). Modos `chamfer`, `fillet`, `chamfer2` (duas distâncias) e `chamfer_angle` (distância + ângulo em **graus**).
- **`creo_shell`**: parede para **dentro**; faces abertas por `["face_z", nível]` (também `face_x` / `face_y`).
- **`creo_clear_scene`** apaga **todas** as peças da sessão (os workplanes ficam) e não tem desfazer. Recusa se houver job rodando.
- **Jobs**: `creo_build_part` e `creo_extrude_profile` rodam em segundo plano porque os clientes MCP cortam chamadas em ~60 s. Um job por vez.

### Mover, girar, cor e medir (sem ferramenta própria)
Funcionam por `creo_run_command` / `creo_execute_lisp`; receitas completas e verificadas em [`docs/RECEITAS_LISP.md`](docs/RECEITAS_LISP.md).

| Recurso | Comando |
|---|---|
| Mover peça | `POSITION_PA "/p" :TRANSLATE dx,dy,dz COMPLETE` |
| Girar peça | `POSITION_PA "/p" :ROTATE :AXIS :TWO_PTA x1,y1,z1 x2,y2,z2 :ROTATION_ANGLE graus COMPLETE` (regra da mão direita; ângulo em **graus**) |
| Montagem real | `(create_assembly :name "conjunto")` (**sem** barra) + `(change_pa_owner :new_owner "/conjunto" :parts_assemblies "/peca")` |
| Cor RGB | `(funcall (find-symbol "SET_PART_COLOR" "ELAN") obj :rgb (oli:make-gpnt3d :x r :y g :z b) :done)` |
| Volume / área / massa exatos | `GET_VOL_PROP` dentro de um `sd-defdialog` (analítico, sem a tesselação do STL) |
| Corte, espelho, varredura | `SECTION`, `MIRROR_3D`, `GENERAL_SWEEP_DIA`, também dentro de `sd-defdialog` |

## Catálogo de peças
`creo_build_part(kind=..., params='{...}', name="...")` monta a peça por passos e **se autoverifica** (bbox exato do Creo e
volume do STL contra o analítico; círculos tesselados perdem ~2 %, fio redondo ~10 %, só retas dão 0,00 %).

| Tipo | O que gera | Peças no Creo |
|---|---|:---:|
| `flange` | Flange com pescoço, furo central e furos em círculo de passo (`hub_fillet`, `rim_chamfer`) | 1 |
| `stepped_shaft` | Eixo escalonado `sections=[[Ø, comprimento],...]`, chaveta, chanfro de ponta | 1 |
| `hex_bolt`, `hex_nut` | Parafuso e porca sextavados; `thread_length` / `threaded=true` fazem rosca helicoidal real | 1 |
| `socket_head_screw` | Parafuso Allen ISO 4762 | 1 |
| `spur_gear` | Engrenagem de dentes retos (evolvente) com rasgo de chaveta | 1 |
| `i_beam`, `angle_bar`, `tslot_profile` | Perfil I, cantoneira, perfil T-slot 40×40 | 1 |
| `mounting_plate`, `rounded_plate`, `l_bracket` | Placas e suporte em L | 1 |
| `heat_sink` | Dissipador com aletas | 1 |
| `pipe_tee`, `bearing_housing` | Tê de tubo e mancal | 1 |
| `machined_block`, `manifold_block` | Bloco usinado (filete + chanfro) e bloco de manifold | 1 |
| `compression_spring`, `threaded_rod`, `lead_screw`, `worm_shaft` | Mola, varão roscado, fuso trapezoidal (ISO 2904), sem-fim | 1 |
| `chain_sprocket` | Roda dentada de corrente (ISO 606) | 1 |
| `enclosure` | Caixa com casca + tampa | 2 |
| `bolted_joint` | Junta parafusada (2 chapas, arruela, parafuso, porca) | 5 |
| `bench_vise` | Morsa funcional | 7 |
| `flanged_joint` | 2 flanges, junta, N parafusos e N porcas (11 com N = 4) | 11 |
| `roller_bearing` | Rolamento de rolos (2 anéis + `rollers` rolos; 12 por padrão) | 14 |

A última coluna importa por causa do [limite de 60 peças](#limite-de-60-peças-do-express): um único rolamento de rolos já
ocupa 14 das 60.

## Limite de 60 peças do Express
O **Creo Elements/Direct Modeling Express** é a edição gratuita. Segundo a [própria PTC](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express),
ela permite montagens com **até 60 peças únicas**; a versão completa não tem esse teto. Este projeto **não contorna** a licença:
ele ensina a trabalhar dentro do limite e, se você precisa de mais, o caminho é uma edição licenciada.

### O que acontece ao passar de 60
Nas sessões do projeto o Creo mostrou uma caixa modal ao **salvar o pacote** (`save_package`) com mais de 60 corpos sólidos:
*"You have exceeded the 60 part limit of the Express version (NN parts)."* A cena em si chegou a ter 66 peças antes de salvar
(a caixa aparece no salvamento). Não foi verificado se o limite também bloqueia outras operações.

### O que conta como "peça"
Cada objeto `:PART` na raiz do modelo. Para contar:
```
creo_get_model_tree(depth=1)   →   conte as entradas do tipo :PART  (workplanes são :WP; com :ASSEMBLY use depth=2 ou mais)
```
Faça isso **antes e depois** de cada etapa grande. Não foi verificado se o Creo desconta peças idênticas ("únicas" no texto da PTC);
trate cada `:PART` como uma peça.

### De onde vêm peças a mais
| Causa | Como evitar |
|---|---|
| Peças do catálogo com vários corpos (`roller_bearing` = 14, `flanged_joint` = 11, `bench_vise` = 7) | Some o custo antes de pedir; reduza `rollers`, `bolts`, etc. quando a peça é só figurativa |
| Montagem "por coordenadas": cada corpo é uma peça própria | Funda (`unite`) só o que **não** precisa se mexer |
| `unite` entre sólidos que **não se tocam**: o Creo não funde e deixa corpos separados (`/p.1`, `/p.2`...) | Garanta contato/interseção antes de unir; confira `objects_now` |
| Contornos disjuntos num `creo_extrude_profile` viram várias peças-ferramenta (`/cuttool`, `/cuttool.1`...) | O servidor subtrai todas e apaga as sobras; **sinal de alerta**: objetos `/cuttool.N` na árvore |
| Peças de teste esquecidas na cena | Apague com `delete_3d` ou `creo_clear_scene` entre testes |

### Estratégias para ficar abaixo de 60
1. **Orçamento de peças.** Antes de modelar, liste os corpos e some (use a tabela do [catálogo](#catálogo-de-peças)).
2. **Funda por contato.** Pino + placas + rolete de um mesmo elo que se interceptam viram **um** sólido contínuo. Em dois conjuntos
   relatados nas sessões do projeto (uma corrente articulada de ~393 mm, que passou de 66 para menos de 60 peças, e uma bacia com
   canaletas e grades, com 15 peças), essa foi a solução. Esses relatos não foram reverificados ao vivo.
3. **Não funda peças que precisam se mover entre si.** Unir peças tangentes de um mecanismo destrói a cinemática; nesse caso
   simplifique (menos rolos, menos elos modelados) em vez de fundir.
4. **Divida em subconjuntos.** Modele e exporte (STL por peça ou pacote) cada subconjunto numa sessão e junte o resultado fora do Creo.
5. **Precisa de mais de 60 peças reais num único modelo?** Use uma edição licenciada do Creo Elements/Direct Modeling. O servidor usa
   os mesmos comandos Lisp e o mesmo DDE, mas **só foi testado no Express 9.0**.

Mais detalhes, exemplos de contagem e a lista do que foi (e não foi) verificado: [`docs/LIMITE_60_PECAS.md`](docs/LIMITE_60_PECAS.md).

## Como funciona por dentro
1. O Creo roda Common Lisp ("HCL") com o Integration Kit (pacote `OLI`). `(oli:sd-dde-enable :on)` liga o servidor DDE `PESD/GENERAL`.
2. **Protocolo "file-load"**: o servidor grava o código em `~/.creo_mcp/cmd/cmd_<ts>.lsp` (que escreve o resultado em `cmd_<ts>.out`
   seguido de `#END#`) e manda por DDE Execute **uma única linha**: `(load "<arquivo>.lsp")`. Depois lê o `.out`. DDE Execute de várias
   linhas e DDE Request de variável não definida não são confiáveis (o Request abre um diálogo de erro modal).
3. Se o Lisp falha, o Creo abre um diálogo modal `#32770`; um vigia (thread) lê o texto, clica em Ok e devolve o texto no campo `dialogs`.
4. `dde.py`, `winui.py`, `lisp.py`, `recipes.py`, `parts.py`, `stl.py` e `builder.py` são **recarregados a cada chamada** (corrigir esses
   arquivos não exige reiniciar o cliente). `server.py` e `delegar.py` exigem reiniciar o cliente MCP.

## Regras que evitam travar o Creo
Cada regra abaixo já travou ou derrubou uma sessão de verdade (as causas e os sintomas estão em [`docs/LICOES.md`](docs/LICOES.md)).

- **O Creo precisa estar aberto.** Abrir, fechar e reiniciar o Creo é decisão do usuário: o servidor e a IA nunca abrem nem matam o processo.
- **Um cliente MCP por vez.** O bloqueio do DDE vale só dentro de um processo; dois clientes mandando comandos juntos não se serializam.
- **Mudou `server.py` ou `delegar.py`?** Feche o cliente por completo (Claude Desktop: ícone da bandeja) e abra de novo.
- **Um passo por chamada**, conferindo `ok`, `error`, `dialogs` e o efeito (bbox/árvore) antes do próximo.
- **Nunca invente nome de função.** Rode `creo_lisp_apropos` antes. Símbolo inventado abre uma cadeia de diálogos de erro.
- **Nunca introspecte funções OLI** (`documentation`, `function-lambda-expression`, `describe`): estouro de pilha fatal derruba o Creo.
- **`extrude` sempre com `:keep_profile :no`**, senão o perfil 2D antigo é extrudado de novo (alturas erradas, "Overlapping loops (Error 291)"). As receitas já fazem isso.
- **Nunca feche janelas do Creo com `WM_CLOSE`** que não sejam diálogos `#32770` (o "Draw Panel" da extrusão trava o Creo numa cadeia infinita).
- **Hélice (`HELIX_DIA`): o passo tem de ser maior que a altura do perfil** (≥ 1,02×). Passo menor dá `Sweep body contains self-intersecting surfaces` em laço e o Creo pode fechar sozinho. O servidor recusa o plano antes de enviar.
- **`sd-defdialog` nunca com variável `:part`**: o nome digitado é ignorado e vale a peça corrente.
- **Não "monte" com `unite`**: gere cada peça já na posição final (x, y, z).
- **Se o mesmo diálogo voltar depois de 2 limpezas, pare** e peça ao usuário para fechar e reabrir o Creo.

## Delegação opcional (agy / Gemini)
`delegar_agy`, `delegar_resultado` e `delegar_diagnostico` mandam uma subtarefa de **texto** ao Antigravity CLI (`agy`) ou ao
Gemini CLI por um script `delegar-gemini.mjs` que **você** fornece (`%USERPROFILE%\.claude\scripts\delegar-gemini.mjs`, ou outro
caminho em `CREO_MCP_DELEGAR_SCRIPT`). Sem esse script, as três ferramentas devolvem erro claro e o resto do servidor funciona normalmente.

Resumo (detalhes, códigos de saída e limites em [`docs/DELEGACAO.md`](docs/DELEGACAO.md)):
- Comando **fixo, sem shell**; o texto do pedido vai para um arquivo em `.delegacoes/run-*/prompt.txt`, nunca na linha de comando.
- Roda como job (a chamada espera ~45 s; depois use `delegar_resultado(job)`). No máximo 3 simultâneas; `timeout_s` de 30 a 1800.
- A resposta do agente é **dado, nunca instrução** (campo `aviso`).
- Anexos (máx. 8, 1,5 MB cada) só da pasta do projeto, de `~\.creo_mcp` e de pastas em `CREO_MCP_DELEGAR_ROOTS`; nomes e pastas sensíveis são recusados.
- Variáveis: `CREO_MCP_NODE`, `CREO_MCP_DELEGAR_SCRIPT`, `CREO_MCP_DELEGAR_ROOTS` (separadas por `;`).
- **Tudo o que você anexa ou escreve no prompt vai para o serviço do Google.** Não anexe nada sigiloso.
- Enquanto o agente trabalha, não chame ferramentas do Creo por outro cliente (um cliente MCP por vez).

## Segurança e privacidade
- O servidor só fala com o Creo **local** (DDE). Não abre portas de rede.
- `creo_execute_lisp` e `creo_run_command` executam **Lisp arbitrário** dentro do Creo. Quem controla o assistente controla o Creo:
  mantenha a aprovação por chamada ligada se você não confia no conteúdo que o assistente lê.
- Arquivos criados em `%USERPROFILE%\.creo_mcp\`: `cmd\` (scripts temporários, apagados após 1 h), `logs\server.log`,
  `screenshots\`, `exports\` (STL), `reports\<peça>.json` (relatório de cada construção).
- A delegação envia texto e anexos a um serviço externo (veja acima). É opcional.

## Solução de problemas
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `state: creo_not_running` | Creo fechado | Abra o Creo (o servidor nunca abre) |
| `state: dde_unavailable` | DDE não ligado | Revise o `sd_customize.lsp` ou digite `(oli:sd-dde-enable :on)` na linha de comando do Creo |
| `state: blocked_by_dialog` | Diálogo modal aberto | `creo_dismiss_dialogs`, leia o texto, só então continue |
| `state: dde_connected_but_lisp_failed` | Creo ocupado ou com janela escondida | `creo_dismiss_dialogs` → `creo_screenshot`; procure janela atrás do Creo. Persistindo, peça ao usuário para fechar e reabrir |
| Caixa do Windows "Fatal exception 0xc00000fd" | Estouro de pilha dentro do Creo (visto após introspecção e após falhas de STL) | **Não é um diálogo do Creo** e o `creo_dismiss_dialogs` não a vê. O aviso pede salvar em arquivo **novo**; depois feche e reabra o Creo |
| Timeout de ~60 s do cliente (ou resposta cortada) | O cliente corta chamadas longas; a ferramenta segue rodando e a peça é feita | Não repita. Use `creo_job_status`, ou confira `creo_get_model_tree` e `creo_get_part_info` |
| Ferramenta nova não aparece | O cliente guarda o esquema | Reinicie o cliente por completo |
| Cadeia de "Error 414" ao exportar STL | `STL` foi chamado como função Lisp | Use `creo_export_stl` ou `creo_run_command` com `COMPLETE` |
| "Error 414" **único** depois do STL | Normal; o servidor fecha | Nada |
| "Error 414" + "Error 69 :YES/:NO" **sem arquivo** | Falha do STL (vista em peças construídas em cadeias longas; causa desconhecida) | Tente no máximo 2 vezes; veja [Limitações](#limitações-conhecidas) |
| "Illegal call to action routine" | `get_selection`, `GET_VOL_PROP`, `SECTION`, `MIRROR_3D`... fora de uma ação de usuário | Encapsule num `sd-defdialog` chamado por `sd-put-buffer "NOME COMPLETE"` |
| "positions are equal" | Polígono com dois vértices consecutivos iguais | Remova pontos repetidos (distância < 1e-4 mm) antes de `polygon` |
| "Cannot find the external symbol ..." | Nome de função inventado | `creo_lisp_apropos` |
| Caixa de 60 peças | Limite da edição Express | [Seção dedicada](#limite-de-60-peças-do-express) |
| Mesmo diálogo volta depois do Ok | Painel de ferramenta ainda aberto reabre o erro | Depois de 2 limpezas, pare e reinicie o Creo |

Logs: `%USERPROFILE%\.creo_mcp\logs\server.log`.

## Limitações conhecidas
Honestas e atuais; o que não foi provado está marcado como tal.

- **Só testado no Express 9.0 e no Windows.** Outras versões do Creo Elements/Direct e a edição licenciada não foram testadas.
- **Express 9.0 não tem** os módulos Annotation (desenho 2D) e Sheet Metal, nem `REVOLVE`, nem ângulo de saída (draft) no `EXTRUDE`.
  Peças de revolução viram pilhas de cilindros (`stepped_shaft`) ou polígonos densos; não dá para imitar revolução com hélice.
- **Limite de 60 peças** (veja a [seção](#limite-de-60-peças-do-express)).
- **STL**: com vários nomes em `:PARTS_ASSEMBLIES` só o primeiro objeto é exportado. Exporte **um arquivo por peça**
  (a docstring de `creo_export_stl` ainda diz que aceita vários nomes; na prática só o primeiro sai). Círculos saem tesselados.
- **Falha de STL sem causa conhecida**: em um conjunto de oito peças feito por uma cadeia longa de chamadas, quatro peças recusaram
  o comando `STL` (Error 414 + Error 69, nenhum arquivo), enquanto peças de teste novas exportaram normalmente. Em seguida o Creo
  caiu duas vezes com `0xc00000fd` depois de consultas Lisp de leitura. A causa **não foi provada**. Recomendação: exporte o STL de cada
  peça logo depois de criada e verificada, e não rode consultas de introspecção na cena depois de duas falhas de STL.
- **A rotina `cancel_active_command_dialogs` está implementada e coberta por testes offline, mas não foi provada** contra um painel de
  extrusão realmente pendente.
- **`GET_VOL_PROP`**: o molde com `sd-call-cmds ... :success :ok` devolveu só `:OK` (sem o volume). A forma sem `:success` devolve o valor;
  ainda não foi repetida ao vivo depois desse achado.
- **Sweep** só foi testado com uma aresta vertical como espinha; `ADD_LOFT` (interativo) não foi provado.
- `delegar_*` depende de um script externo e de contas/cotas do serviço do Google (status `cota_esgotada` = não tentar de novo).

## Desenvolvimento e testes
```bat
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
```
109 testes **offline** (não precisam do Creo): geração dos planos e do Lisp, receitas, leitura de STL, diálogos simulados, execução
com um Creo falso, e toda a delegação com `node` e script falsos. Passam em Linux (verificado na preparação do pacote) e no Windows
(onde o projeto é desenvolvido); o código de DDE e de janelas só é chamado no Windows.
O teste ao vivo exige o Creo aberto com o DDE ligado.

Ao mudar `recipes.py` ou `parts.py`, rode a suíte: a checagem prévia de `recipes.boolean` já quebrou um teste que esperava a string antiga.
Ao adicionar parâmetro a um gerador de peças, ponha-o **no fim** da assinatura (os geradores de montagem passam `name` por posição).

## Estrutura do repositório
```
src/creo_mcp/
  server.py     ferramentas MCP (24) e instruções para o modelo
  dde.py        cliente DDE em ctypes (serviço PESD, tópico GENERAL)
  lisp.py       execução de Lisp: arquivo .lsp + resultado .out, vigia de diálogos
  winui.py      janelas do Creo: diálogos #32770, painéis de ferramenta, screenshot
  recipes.py    receitas de modelagem em Lisp (extrusão, booleana, chanfro, casca, hélice...)
  parts.py      catálogo de 27 peças e planos de construção
  builder.py    executor de planos + autoverificação (bbox, volume do STL)
  stl.py        leitor de STL (binário/ASCII) para conferir volume
  delegar.py    delegação opcional ao agy / Gemini
tests/          testes offline
examples/       sd_customize.lsp e modelos de configuração dos clientes MCP
docs/           lições aprendidas, receitas Lisp, limite de 60 peças, delegação, exemplo de modelagem
scripts/        start_server.bat (depuração manual)
```

## Documentação adicional
- [`docs/LICOES.md`](docs/LICOES.md) — erros, causas e regras (o que já travou o Creo e por quê)
- [`docs/RECEITAS_LISP.md`](docs/RECEITAS_LISP.md) — receitas Lisp verificadas ao vivo
- [`docs/LIMITE_60_PECAS.md`](docs/LIMITE_60_PECAS.md) — guia do limite de 60 peças
- [`docs/DELEGACAO.md`](docs/DELEGACAO.md) — como funciona a delegação ao agy / Gemini
- [`docs/EXEMPLO_CANHAO.md`](docs/EXEMPLO_CANHAO.md) — exemplo completo: conjunto de 8 corpos com peças de revolução

## Licença
[MIT](LICENSE). Verifique também os termos de uso do Creo Elements/Direct Modeling Express com a PTC.
