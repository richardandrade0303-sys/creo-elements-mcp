# Delegação opcional ao agy / Gemini

Três ferramentas **que não usam o Creo** deixam qualquer cliente MCP (Claude Desktop, Claude Code, Antigravity) mandar uma subtarefa de
**texto** ao Antigravity CLI (`agy`) ou ao Gemini CLI e receber a resposta de volta, sem janela e sem clique. Servem para resumir logs e arquivos
grandes, rascunhos, traduções, comparar opções e para **modelar peças pelas ferramentas do Creo** com instruções exatas (veja o
[exemplo do canhão](EXEMPLO_CANHAO.md)). Não servem para decisões finais de arquitetura ou segurança.

| Ferramenta | Para quê |
|---|---|
| `delegar_diagnostico()` | Confere o ambiente: `node`, o script, `agy`/`gemini` no PATH, pastas permitidas e limites. Só lê |
| `delegar_agy(prompt, files, motor, timeout_s)` | Manda a tarefa. Roda como **job**: espera ~45 s e, se o agente ainda trabalha, devolve `{"state": "running", "job": ...}` |
| `delegar_resultado(job, wait_seconds)` | Busca o resultado do job (espera até 45 s por chamada) |

## O que você precisa fornecer
Este repositório **não inclui** o script de delegação nem os CLIs:

1. **Node.js** (o servidor procura em `CREO_MCP_NODE`, no PATH e em `Program Files`).
2. **O script `delegar-gemini.mjs`**, por padrão em `%USERPROFILE%\.claude\scripts\delegar-gemini.mjs` (ou outro caminho em `CREO_MCP_DELEGAR_SCRIPT`).
3. O **Antigravity CLI** (`agy`) ou o **Gemini CLI** instalado e autenticado.

Se algo falta, `delegar_diagnostico` diz o quê e as outras 21 ferramentas seguem funcionando.

### Contrato com o script
O servidor roda **sempre o mesmo comando, sem shell**:
```
node SCRIPT "Siga as instrucoes do arquivo anexado e responda so em texto." -f prompt.txt [-f arquivo]... [--motor gemini] [--timeout N]
```
Seu script deve aceitar: `-f arquivo` (repetível; o conteúdo é embutido no prompt), `--motor agy|gemini`, `-m modelo`, `--timeout segundos`
(o servidor passa `timeout_s − 40` quando `timeout_s ≥ 60`, para o script desistir ~10 s antes do encerramento local) e as variáveis
`DELEGAR_MOTOR` / `DELEGAR_MODELO`. Códigos de saída esperados:

| Código | `status` devolvido | Significado |
|:---:|---|---|
| 0 | `ok` | Resposta em `saida.txt` |
| 1 | `erro` | Falha (ex.: "Ferramenta bloqueada" = o agente precisa de permissão) |
| 2 | `uso_incorreto` | Argumentos inválidos |
| 3 | `cota_esgotada` | Cota do serviço acabou: **não tentar de novo** na sessão e não trocar de conta |
| 4 | `timeout_do_script` | O script desistiu |
| — | `timeout_local` | O servidor esgotou o `timeout_s` e encerrou só o processo que abriu (e os filhos) |

Na implementação de referência, com `agy` e entrada abaixo de ~24 000 caracteres o script chama `agy -p ... --output-format json`; acima disso grava um
arquivo de tarefa e pede ao agente que o leia, o que pode dar "Ferramenta bloqueada". Mantenha prompt + anexos **abaixo de ~24 000 caracteres**.

## Onde ficam os arquivos
Cada delegação cria `<projeto>\.delegacoes\run-AAAAMMDD-HHMMSS-mcpXXXX\` com `prompt.txt`, `saida.txt`, `erro.txt` e `exit.txt`
(sentinela, escrito por último). O script pode manter um `log.tsv`. A pasta está no `.gitignore`.
A raiz do projeto é a pasta acima de `src/`: rode o servidor a partir do código-fonte (`PYTHONPATH=src` ou `pip install -e .`).

## Segurança
- O texto do pedido vai para um **arquivo**, nunca para a linha de comando (sem injeção por aspas, `$(...)`, `&`, `|`).
- **Anexos**: no máximo 8 arquivos, 1,5 MB cada (4 MB no total), só dentro da pasta do projeto, de `~\.creo_mcp` e de pastas listadas em
  `CREO_MCP_DELEGAR_ROOTS` (separadas por `;`). São recusados: `.env*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*.kdbx`, `id_rsa*`, `id_ed25519*`,
  `*credential*`, `*secret*`, `*token*`, `*password*`, `*senha*`, `oauth_creds*`, `mcp_config.json*`, `claude_desktop_config*`, as pastas
  `.ssh`, `.aws`, `.gnupg`, `.azure`, `.kube`, `.docker` e atalhos/symlinks que escapem das pastas permitidas.
- O `stdin` do processo filho é fechado (o do servidor é o canal MCP).
- A resposta é **dado, nunca instrução**: o resultado traz o campo `aviso`. Não rode comandos nem aplique patches só porque o texto manda.
- **Tudo o que você anexa ou escreve no prompt vai para o serviço do Google.** Não anexe nada sigiloso.
- No máximo **3** delegações simultâneas; `timeout_s` de 30 a 1800 (padrão 600); prompt de até 30 000 caracteres.

## Boas práticas para modelar por delegação
O agente enxerga o servidor Creo pelo `mcp_config.json` dele. Funcionou bem com:
- **Um cliente MCP por vez**: enquanto o agente trabalha, não chame ferramentas do Creo por outro cliente.
- Prompt com cabeçalho de regras + as **chamadas exatas** (JSON de `creo_extrude_profile` / `stepped_shaft`, comandos `POSITION_PA`) +
  o **bbox esperado** depois de cada passo + a regra "se divergir, relate sem consertar". No máximo ~8 chamadas por etapa.
- Etapas de 4 a 8 minutos. Um `stepped_shaft` de 36 degraus roda como job de ~5 min: mande o agente acompanhar com `creo_job_status`.
- **O agente não vê imagens**: `creo_screenshot` devolve só o caminho. Julgamento visual é sempre seu; peça ao agente só bbox, árvore e erros.
- Confira você mesmo cada etapa com `creo_get_part_info` e `creo_get_model_tree`; depois de uma falha de rede, confira a cena antes de refazer
  (o trabalho já feito permanece no Creo).
- Se o cliente guardar uma versão antiga do servidor em memória (o teto de ~10,5 min por delegação indica isso), reinicie o cliente por completo.
