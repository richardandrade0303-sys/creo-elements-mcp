# Creo Elements/Direct MCP

A Python [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Desktop, Claude Code, Antigravity or any stdio MCP client)
**model in 3D inside PTC Creo Elements/Direct Modeling Express 9.0**. It talks to Creo through its native DDE server
(service `PESD`, topic `GENERAL`) using only `ctypes`: no Lisp bridge, no mailbox files, no dependency other than the `mcp` SDK.

> **Notice.** Independent project, not affiliated with PTC. "Creo" and "Creo Elements/Direct" are PTC trademarks. Tested **only with
> Express 9.0 on Windows**. Read [Known limitations](#known-limitations) before relying on it.

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

**3. Check.** With Creo open, ask the assistant to call `creo_get_status`. Expected: `"state": "ready"`, `"server_version": "2.8.0"`.

## Tools (24)
| Group | Tools |
|---|---|
| State | `creo_get_status`, `creo_self_test`, `creo_screenshot`, `creo_dismiss_dialogs` |
| Lisp / commands | `creo_execute_lisp`, `creo_run_command`, `creo_cancel_command`, `creo_lisp_apropos`, `creo_dde_raw` |
| Query | `creo_get_model_tree`, `creo_get_part_info` (global bounding box) |
| Basic modeling | `creo_create_block`, `creo_create_cylinder`, `creo_boolean`, `creo_export_stl`, `creo_clear_scene` |
| Industrial parts | `creo_build_part` (27-part catalog; `kind="list"` is the source of truth), `creo_extrude_profile`, `creo_job_status` |
| Finishing | `creo_edge_treatment` (chamfer/fillet), `creo_shell` |
| Delegation (no Creo) | `delegar_agy`, `delegar_resultado`, `delegar_diagnostico` |

Notes: fixed-input commands (`rectangle`, `circle`, `extrude`, `create_part`...) go through `creo_execute_lisp`; variable-input commands (`STL`,
`POSITION_PA`, `POSITION_WP`) go through `creo_run_command` and end with `COMPLETE`. `creo_build_part` and `creo_extrude_profile` run as **background jobs**
(MCP clients cut calls at ~60 s): if the answer is `{"state": "running", "job": ...}`, do **not** repeat the call, use `creo_job_status(job)`.
Export **one STL per part**: with several names in `:PARTS_ASSEMBLIES` Creo exports only the first.

Catalog: flange, stepped_shaft, hex_bolt, hex_nut, spur_gear, i_beam, angle_bar, mounting_plate, heat_sink, roller_bearing, rounded_plate, l_bracket, pipe_tee,
bearing_housing, tslot_profile, machined_block, manifold_block, socket_head_screw, bolted_joint (5 parts), flanged_joint (11), enclosure (2),
compression_spring, threaded_rod, lead_screw, worm_shaft, chain_sprocket, bench_vise (7). Parts per kind are listed in the [Portuguese README](README.md#catálogo-de-peças).

## The 60-part limit of Creo Elements/Direct Modeling Express
Express is the free edition. According to [PTC](https://www.ptc.com/pt/products/cad/elements-direct/modeling/express), it allows assemblies with
**up to 60 unique parts**; the full edition has no such cap. **This project does not bypass the license**: it teaches how to work within the limit; if you need
more, use a licensed edition (the server uses the same Lisp/DDE commands, but it has **only been tested on Express 9.0**).

- **Observed:** when saving a package (`save_package`) with more than 60 solid bodies, Creo shows a modal box:
  *"You have exceeded the 60 part limit of the Express version (NN parts)."* A scene reached 66 parts before saving.
  **Not verified:** whether the limit also blocks creating parts, and whether identical parts count once.
- **Count:** `creo_get_model_tree(depth=1)` and count `:PART` entries (use `depth=2`+ with `:ASSEMBLY`). Do it before and after every big step.
- **Where extra parts come from:** multi-body catalog kinds (`roller_bearing` = 2 + `rollers` = 14 by default, `flanged_joint` = 11, `bench_vise` = 7, `bolted_joint` = 5);
  assemblies by coordinates (one body = one part); `unite` between solids that do **not** touch (Creo keeps them separate as `/p.1`, `/p.2`...);
  leftover tool parts (`/cuttool.N`); forgotten test parts; each `SECTION` cut (doubles the cut part).
- **Staying under 60:** (1) make a part budget first; (2) fuse by contact what does not need to move (pin + plates + roller of one link become one solid);
  (3) do **not** fuse parts that must move relative to each other (it destroys the kinematics): simplify instead (fewer rollers/bolts);
  (4) split big products into sub-assemblies, one session each, export per-part STLs and combine outside Creo;
  (5) if you truly need more than 60 real parts in one model, use a licensed edition.

Full guide (with a counting checklist and what was and was not verified): [`docs/LIMITE_60_PECAS.md`](docs/LIMITE_60_PECAS.md) (Portuguese).

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
- Tested only on Express 9.0 / Windows. Express has no Annotation or Sheet Metal modules, no `REVOLVE` and no draft on `EXTRUDE`
  (revolved shapes become stacked cylinders).
- STL export: only the first object of a multi-name list is exported; circles are tessellated (cylinders lose ~2 % volume). The docstring of `creo_export_stl`
  still says it accepts several names; in practice only the first one is written.
- **Unexplained STL failure:** in one eight-part assembly built by a long chain of calls, four parts refused the `STL` command (Error 414 + Error 69, no file)
  while fresh test parts exported fine; Creo then crashed twice with `0xc00000fd` after read-only Lisp queries. Root cause **not proven**. Export each part's STL right after
  creating and verifying it, and stop introspecting the scene after two STL failures.
- `cancel_active_command_dialogs` is implemented and unit-tested but was **not proven** against a really pending extrusion panel.
- `GET_VOL_PROP` with the `sd-call-cmds ... :success :ok` template returned only `:OK`; the form without `:success` returns the value but has not been repeated live since.
- Sweep was only tested with a vertical edge as spine; `ADD_LOFT` was not proven.

## Development
```bat
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
```
109 **offline** tests (no Creo needed), passing on Linux (checked while preparing this package) and Windows. See the structure and details in the [Portuguese README](README.md#estrutura-do-repositório).

## License
[MIT](LICENSE). Also check the Creo Elements/Direct Modeling Express terms of use with PTC.
