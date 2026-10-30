# Creo Elements/Direct MCP

A Python [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Desktop, Claude Code, Antigravity or any stdio MCP client)
**model in 3D inside PTC Creo Elements/Direct Modeling**. It talks to Creo through its native DDE server
(service `PESD`, topic `GENERAL`) using only `ctypes`, with no dependency other than the `mcp` SDK.
Version **3.0.0**: 64 tools, with guards against the errors that have frozen Creo in real sessions.

> **Notice.** Independent project, not affiliated with PTC. "Creo" and "Creo Elements/Direct" are PTC trademarks. Tested **only with
> Express 9.0 on Windows**. A full-license edition has **not been tested yet**: follow [`docs/TESTE_LICENCA_COMPLETA.md`](docs/TESTE_LICENCA_COMPLETA.md)
> (Portuguese) and open an issue with the result. Read [Known limitations](#known-limitations) before relying on it.

[Versão em português](README.md) (the Portuguese docs under `docs/` are more detailed)

## What it does
You describe a part in plain language and the assistant calls the tools. Coverage: a **27-part industrial catalog** with automatic verification
(exact bounding box + STL volume vs. the analytic value), free profile extrusion, booleans, chamfers and fillets, shell, helices (real springs and
threads), assemblies by coordinates, `:ASSEMBLY` nodes, RGB color, move/rotate, section, mirror, sweep, exact volume/mass, and free Lisp.

## Requirements
| Item | Notes |
|---|---|
| OS | **Windows** (DDE exists only there) |
| Creo | **Creo Elements/Direct Modeling Express 9.0**, running while you use the server ([PTC page](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express)) |
| Python | 3.10+ (tested with 3.13 and 3.14) |
| MCP SDK | `mcp>=2.3.0` (uses `mcp.server.mcpserver`; tested with 2.3.0) |
| Client | One MCP client at a time (Claude Desktop **or** Claude Code **or** Antigravity) |
| Optional | Node.js + your own `delegar-gemini.mjs` script + the `agy` or Gemini CLI, for the delegation tools |

## Install
```bat
git clone https://github.com/richardandrade0303-sys/creo-elements-mcp.git
cd creo-elements-mcp
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**1. Enable Creo's DDE server.** Add `(oli:sd-dde-enable :on)` to
`%APPDATA%\PTC\Creo Elements Direct Modeling Express 9.0\90.0\sd_customize.lsp` (see [`examples/sd_customize.lsp`](examples/sd_customize.lsp)) and restart Creo.
Without restarting, type the same line in Creo's command line.

**2. Register the server in your MCP client.** The command is the venv's Python running `creo_mcp`, with `PYTHONPATH` pointing to `src`:

- **Claude Desktop** (`%APPDATA%\Claude\claude_desktop_config.json`, template in [`examples/`](examples/claude_desktop_config.json)):
  ```json
  {
    "mcpServers": {
      "creo-elements-direct": {
        "command": "C:\\path\\to\\creo-elements-mcp\\.venv\\Scripts\\python.exe",
        "args": ["-m", "creo_mcp"],
        "env": { "PYTHONPATH": "C:\\path\\to\\creo-elements-mcp\\src" }
      }
    }
  }
  ```
  Then quit Claude Desktop **from the tray icon** and reopen it.
- **Claude Code** (options go *before* the server name; see the [docs](https://code.claude.com/docs/en/mcp)):
  ```bat
  claude mcp add --env PYTHONPATH=C:\path\to\creo-elements-mcp\src --transport stdio creo-elements-direct -- C:\path\to\creo-elements-mcp\.venv\Scripts\python.exe -m creo_mcp
  ```
- **Antigravity**: same `mcpServers` entry in its `mcp_config.json` ([template](examples/antigravity_mcp_config.json)); then *Manage MCP Servers*.

**3. Check.** With Creo open, ask the assistant to call `creo_get_status`. Expected: `"state": "ready"`, `"server_version": "3.0.0"`.

## Tools (64)
| Group | Tools |
|---|---|
| State and health | `creo_get_status`, `creo_self_test`, `creo_health`, `creo_lease`, `creo_capabilities`, `creo_units`, `creo_screenshot`, `creo_dismiss_dialogs`, `creo_cancel_command` |
| Lisp / commands (guarded) | `creo_execute_lisp`, `creo_run_command`, `creo_lisp_apropos`, `creo_dde_raw` (off by default) |
| Query and measure | `creo_get_model_tree`, `creo_get_part_info`, `creo_inspect`, `creo_measure`, `creo_audit_scene`, `creo_part_budget`, `creo_clash_check`, `creo_mesh_qa` |
| Modeling | `creo_create_block`, `creo_create_cylinder`, `creo_extrude_profile`, `creo_boolean`, `creo_edge_treatment`, `creo_shell`, `creo_section`, `creo_mirror`, `creo_transform`, `creo_holes`, `creo_sweep_path` (experimental) |
| Parts and components | `creo_build_part` (27-part catalog), `creo_profile_std`, `creo_frame`, `creo_pipe`, `creo_fasteners`, `creo_bearing_std`, `creo_transmission`, `creo_fit` (ISO 286), `creo_pattern`, `creo_variants` |
| Assemblies and data | `creo_build_assembly`, `creo_group`, `creo_color`, `creo_set_attributes`, `creo_bom` |
| Scene safety | `creo_snapshot`, `creo_restore`, `creo_clear_scene`, `creo_journal`, `creo_replay`, `creo_validate` |
| Jobs and batch | `creo_job_status`, `creo_job_cancel`, `creo_batch` |
| Export and reports | `creo_export` (STL per part; STEP/SAT experimental), `creo_export_stl`, `creo_import` (experimental), `creo_report`, `creo_views` |
| Delegation (no Creo) | `delegar_agy`, `delegar_resultado`, `delegar_diagnostico` |

**Guards (v3).** Free Lisp (`creo_execute_lisp`, `creo_run_command`, `creo_dde_raw`) refuses, before sending anything to Creo, function introspection
(`documentation`, `describe`, `function-lambda-expression`...: fatal stack overflow), `quit`/`exit`, and `sd-get-faces-along-ray` (froze Creo; cause not isolated).
Override at your own risk with `CREO_MCP_ALLOW_RISKY_LISP=1`. After 3 consecutive DDE failures the server enters `degraded` and refuses free Lisp and scene-changing steps until
Creo answers again (`creo_get_status` is a harmless probe). `creo_validate` needs `confirm=true`; `creo_fasteners` needs `at` or `positions`; the exact `creo_clash_check` has a time budget (`time_budget_s`).

Notes: fixed-input commands (`rectangle`, `circle`, `extrude`, `create_part`...) go through `creo_execute_lisp`; variable-input commands (`STL`,
`POSITION_PA`, `POSITION_WP`) go through `creo_run_command` and end with `COMPLETE`. `creo_build_part` and `creo_extrude_profile` run as **background jobs**
(MCP clients cut calls at ~60 s): if the answer is `{"state": "running", "job": ...}`, do **not** repeat the call, use `creo_job_status(job)`.
Export **one STL per part**: with several names in `:PARTS_ASSEMBLIES` Creo exports only the first.

Catalog: flange, stepped_shaft, hex_bolt, hex_nut, spur_gear, i_beam, angle_bar, mounting_plate, heat_sink, roller_bearing, rounded_plate, l_bracket, pipe_tee,
bearing_housing, tslot_profile, machined_block, manifold_block, socket_head_screw, bolted_joint (5 parts), flanged_joint (11), enclosure (2),
compression_spring, threaded_rod, lead_screw, worm_shaft, chain_sprocket, bench_vise (7). Parts per kind are listed in the [Portuguese README](README.md#catálogo-de-peças).

## Part count (no limit by default)
**The server does not limit the number of parts.** The 60-unique-part cap belongs to the free Express edition; the full edition has none
([PTC](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express)). On Express, set `CREO_MCP_PART_LIMIT=60` in the server environment to turn on the
warning and the refusal before building; `0` or unset = no limit. `creo_part_budget` shows the count. Where extra parts come from (`/cuttool.N`, `/p1.1`, section cuts)
and counting tips: [`docs/CONTAGEM_DE_PECAS.md`](docs/CONTAGEM_DE_PECAS.md) (Portuguese).

## Rules that keep Creo from freezing
Each one has frozen or crashed a real session. Details in [`docs/LICOES.md`](docs/LICOES.md).
- Creo must be running; opening/closing/restarting it is the user's decision. The server never starts or kills the process.
- **One MCP client at a time** (the DDE lock is per process). After changing `server.py`/`delegar.py`, fully restart the client.
- One step per call; check `ok`, `error`, `dialogs` and the effect (bbox/tree) before the next.
- Never invent function names (`creo_lisp_apropos` first). **Never introspect OLI functions** (`documentation`, `describe`...): fatal stack overflow.
- `extrude` always with `:keep_profile :no`. Never `WM_CLOSE` a window that is not a `#32770` dialog.
- Helix pitch must exceed the profile height (≥ 1.02×), otherwise Creo loops on errors and can close itself.
- `sd-defdialog` never with a `:part` variable. Assemblies by coordinates, not `MOVE`/`unite`.
- If the same dialog comes back after 2 cleanups, stop and ask the user to restart Creo.

## Security
- The server only talks to the **local** Creo (DDE) and opens no network ports.
- `creo_execute_lisp` / `creo_run_command` run **arbitrary Lisp inside Creo**: keep per-call approval on if you do not trust what the assistant reads.
- Files are created under `%USERPROFILE%\.creo_mcp\` (`cmd\` temporary scripts, `logs\`, `screenshots\`, `exports\`, `reports\`).
- The delegation tools send prompt text and attachments **to Google's service** (optional feature; see [`docs/DELEGACAO.md`](docs/DELEGACAO.md)).

## Known limitations
- Tested only on Express 9.0 / Windows; full-license editions are untested ([`docs/TESTE_LICENCA_COMPLETA.md`](docs/TESTE_LICENCA_COMPLETA.md)). Express has no Annotation or Sheet Metal modules, no `REVOLVE` and no draft on `EXTRUDE`
  (revolved shapes become stacked cylinders).
- STL export: only the first object of a multi-name list is exported; circles are tessellated (cylinders lose ~2 % volume). The docstring of `creo_export_stl`
  still says it accepts several names; in practice only the first one is written.
- **Unexplained STL failure:** in one eight-part assembly built by a long chain of calls, four parts refused the `STL` command (Error 414 + Error 69, no file)
  while fresh test parts exported fine; Creo then crashed twice with `0xc00000fd` after read-only Lisp queries. Root cause **not proven**. Export each part's STL right after
  creating and verifying it, and stop introspecting the scene after two STL failures.
- `cancel_active_command_dialogs` is implemented and unit-tested but was **not proven** against a really pending extrusion panel.
- `GET_VOL_PROP` with the `sd-call-cmds ... :success :ok` template returned only `:OK`; the form without `:success` returns the value but has not been repeated live since.
- Helical threads (`HELIX_DIA`): volume readings after helical cuts are unreliable (they can even go up); use the **area** change and section cuts. STEP export failed on Express 9.0 (retest on a full edition).
- Sweep was only tested with a vertical edge as spine; `ADD_LOFT` was not proven.

## Development
```bat
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
```
196 **offline** tests (no Creo needed), passing on Linux (checked while preparing this package) and Windows. See the structure and details in the [Portuguese README](README.md#estrutura-do-repositório).

## License
[MIT](LICENSE). Also check the Creo Elements/Direct Modeling Express terms of use with PTC.
