"""Ferramentas MCP dos niveis 0-4 (confianca, analise, troca de dados, modelagem tipada, escala).

`register(server, api)` define as ferramentas no servidor. `api` (SimpleNamespace) traz o que mora em server.py:
run, settle, shot, pids, log, start_job, running_job, cancel_job, reload, legacy (ferramentas antigas, nome -> funcao),
base_tools (nomes das ferramentas antigas) e as anotacoes READ_ONLY / WRITES / DESTRUCTIVE.

Regras comuns (cada ferramenta passa por `_gate` antes de tocar no Creo):
  - trava entre clientes (lease): outro cliente MCP vivo usando o Creo -> recusa clara;
  - saude do servidor: 'degraded'/'down' bloqueia ferramentas que alteram a cena;
  - job em andamento bloqueia ferramentas que alteram a cena;
  - toda chamada que altera a cena vai para o diario (journal) com o resultado.
A resposta e sempre um dict com `ok` (saida estruturada do MCP).
"""

from __future__ import annotations

import functools
import inspect
import json
from typing import Any

from . import (actions, capabilities, config, exchange, fits, hardware, health, journal, lease, library, ops, place,
               profiles, registry, scene, snapshot)

EXPERIMENTAL_FLAG = "CREO_MCP_EXPERIMENTAL"


# ------------------------------------------------------------------ utilidades puras (testaveis offline)
def jparse(value, what: str, default=None):
    """Texto JSON -> objeto. Vazio -> default; objeto ja decodificado passa direto."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ValueError(f"{what}: JSON invalido ({exc})")


def parse_spec(text: str, what: str = "spec"):
    """JSON ou, se houver PyYAML, YAML. Devolve dict/lista."""
    s = (text or "").strip()
    if not s:
        raise ValueError(f"{what} vazio")
    try:
        return json.loads(s)
    except ValueError:
        pass
    try:
        import yaml  # type: ignore
    except ImportError:
        raise ValueError(f"{what}: nao e JSON valido (YAML so se o PyYAML estiver instalado: pip install pyyaml)")
    try:
        data = yaml.safe_load(s)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{what}: nem JSON nem YAML valido ({exc})")
    if not isinstance(data, (dict, list)):
        raise ValueError(f"{what}: precisa ser um objeto ou uma lista")
    return data


def vec3(value, what: str, default=None):
    v = jparse(value, what, default)
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError(f"{what} deve ser [x, y, z]")
    return [float(x) for x in v]


def clean(res):
    """Deixa o resultado serializavel (tuplas viram listas, objetos estranhos viram texto) e de tamanho razoavel."""
    return json.loads(json.dumps(res, default=str, ensure_ascii=False))


def summary_of(res) -> str:
    if not isinstance(res, dict):
        return str(res)[:300]
    for key in ("error", "message"):
        if res.get(key):
            return str(res[key])[:300]
    keep = {k: res[k] for k in ("parts_created", "file", "deleted", "moved", "imported_parts", "format") if k in res}
    return str(keep or {k: res[k] for k in list(res)[:3]})[:300]


def experimental_enabled() -> bool:
    return config.flag(EXPERIMENTAL_FLAG, False)


# ------------------------------------------------------------------ registro
def register(server, api):
    DISPATCH: dict = {}
    NAMES: list = []

    def env() -> actions.Env:
        return actions.Env(api.run, api.settle, pids=api.pids, shot=api.shot)

    def all_tool_names() -> list:
        return sorted(set(api.base_tools) | set(NAMES))

    def gate(creo: bool, model: bool) -> dict | None:
        if not creo:
            return None
        if config.lease_enabled():
            ok, holder = lease.ensure(client=config.client_name())
            if not ok:
                return lease.refusal(holder)
        if model:
            why = health.blocks_modeling()
            if why:
                return {"ok": False, "error": why, "health": health.status()}
            busy = api.running_job()
            if busy:
                return {"ok": False, "error": f"job em andamento ({busy['label']}, id {busy['id']}); espere o fim "
                                              "(creo_job_status) ou cancele (creo_job_cancel)."}
        return None

    def deftool(kind: str = "read", creo: bool = True, model: bool = False, jobbed: bool = False):
        """kind: read | write | destructive. creo: toca o Creo (trava). model: altera a cena (saude/job)."""
        ann = {"read": api.READ_ONLY, "write": api.WRITES, "destructive": api.DESTRUCTIVE}[kind]

        def deco(fn):
            name = fn.__name__
            sig = inspect.signature(fn)

            @functools.wraps(fn)
            def wrapper(*a, **kw):
                api.reload()
                refusal = gate(creo, model)
                if refusal:
                    return clean(refusal)
                try:
                    res = fn(*a, **kw)
                except ValueError as exc:                    # PartError e erros de parametro
                    res = {"ok": False, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    api.log.exception("tool %s falhou", name)
                    res = {"ok": False, "error": f"erro inesperado: {exc!r}"}
                if not isinstance(res, dict):
                    res = {"ok": True, "result": res}
                res = clean(res)
                if not jobbed and kind != "read" and not res.get("dry_run"):
                    try:
                        args = dict(sig.bind_partial(*a, **kw).arguments)
                    except TypeError:
                        args = dict(kw)
                    journal.record(name, args, None, bool(res.get("ok")), summary_of(res))
                return res

            server.tool(name=name, annotations=ann, structured_output=True)(wrapper)
            DISPATCH[name] = wrapper
            NAMES.append(name)
            return wrapper

        return deco

    def job(label: str, tool: str, args: dict, fn):
        """Roda `fn` como job (o cliente corta chamadas em 60 s). O diario e gravado no fim, com o resultado real."""
        def go():
            try:
                res = fn()
            except ValueError as exc:
                res = {"ok": False, "error": str(exc)}
            res = clean(res if isinstance(res, dict) else {"ok": True, "result": res})
            if tool in journal.WRITE_TOOLS and not res.get("dry_run"):
                journal.record(tool, args, None, bool(res.get("ok")), summary_of(res))
            return res

        return api.start_job(label, go)

    # ================================================================ NIVEL 0: confianca
    @deftool("write", creo=True)
    def creo_snapshot(action: str = "create", label: str = "manual", keep: int = 20) -> dict[str, Any]:
        """Save, list or prune restore points of the whole scene (SAVE_PACKAGE .pk2 + a JSON with the tree).

        action: 'create' (default; label = short name), 'list', 'prune' (deletes the oldest AUTOMATIC snapshots beyond
        `keep`; manual ones are never deleted). The folder is CREO_MCP_SNAPSHOT_DIR (default ~/creo_mcp_snapshots, never
        inside a dot-folder: Creo refuses to load from those). Use before risky operations; creo_restore loads one back.
        """
        a = (action or "create").lower()
        if a == "list":
            snaps = snapshot.list_snapshots()
            return {"ok": True, "directory": snapshot.snap_dir(), "snapshots": snaps}
        if a == "prune":
            return {"ok": True, "removed": snapshot.prune(None, int(keep))}
        if a != "create":
            raise ValueError("action deve ser create, list ou prune")
        res = snapshot.create(api.run, label or "manual", auto=False)
        if res.get("ok"):
            health.acknowledge()
        return res

    @deftool("destructive", creo=True, model=True, jobbed=True)
    def creo_restore(name: str = "", replace: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Load a snapshot back (name '' = the latest; partial names work). Loading over existing parts renames
        everything (p1 -> p1.1), so with parts in the scene it refuses unless replace=true, which first saves the current
        scene as an automatic snapshot and then deletes it. dry_run=true only says what would be loaded.
        Takes 15-60 s; runs as a background job."""
        path = snapshot.resolve(name)
        if not path:
            return {"ok": False, "error": "nenhum snapshot encontrado (creo_snapshot action='list')"}
        if dry_run:
            meta = snapshot._read_meta(path)
            return {"ok": True, "dry_run": True, "snapshot": path, "meta": meta}
        return job("restore", "creo_restore", {"name": name, "replace": replace},
                   lambda: snapshot.restore(api.run, api.settle, name, None, bool(replace)))

    @deftool("read", creo=True)
    def creo_capabilities(probe: bool = True) -> dict[str, Any]:
        """What this Creo installation offers: units, license/modules, which commands exist (checked with find-symbol, no
        command is called), the Express 9.0 limits and which server tools are 'verified' vs 'experimental' (not yet
        validated live on this Creo; creo_validate promotes them). probe=false skips the Creo query."""
        return actions.capabilities_report(env(), all_tool_names(), bool(probe))

    @deftool("read", creo=True)
    def creo_part_budget(planned: int = 0) -> dict[str, Any]:
        """Part budget: used now, planned, left. No limit by default (full license); set CREO_MCP_PART_LIMIT=60 for Express 9.0. Call it BEFORE building
        something big (planned = number of parts you are about to create)."""
        return actions.budget(env(), int(planned))

    @deftool("destructive", creo=True, model=True)
    def creo_clear_scene(scope: str = "mine", confirm: bool = False, snapshot_first: bool = True) -> dict[str, Any]:
        """Delete parts/assemblies (workplanes stay) - SAFE version.

        scope 'mine' (default): only objects THIS server created (registry); anything else is listed as 'protected'.
        scope 'all': every part and assembly. Without confirm=true nothing is deleted: you get the plan (would_delete,
        protected). With confirm=true a snapshot is saved first (snapshot_first=true) and abort happens if it fails."""
        return actions.clear_scene(env(), scope, bool(confirm), bool(snapshot_first))

    @deftool("write", creo=False)
    def creo_lease(action: str = "status") -> dict[str, Any]:
        """Cross-client lock: only one MCP client (Claude Desktop, Antigravity, Claude Code...) should drive Creo at a time.
        action: 'status', 'take' (take it from another live client - it will be refused on its next call) or 'release'."""
        a = (action or "status").lower()
        if a == "status":
            return {"ok": True, "enabled": config.lease_enabled(), **lease.status()}
        if a == "take":
            return {"ok": True, **lease.take(config.client_name())}
        if a == "release":
            return {"ok": True, **lease.release()}
        raise ValueError("action deve ser status, take ou release")

    @deftool("read", creo=False)
    def creo_health() -> dict[str, Any]:
        """Server health: Creo process up/restarted, consecutive DDE failures, recent STL failures (degraded state blocks
        modeling tools), active policy (safe mode, free Lisp, dde_raw, lease) and recent incidents."""
        return actions.health_report(env())

    @deftool("read", creo=True)
    def creo_units() -> dict[str, Any]:
        """Creo's current length/mass/angle units. All recipes assume mm and g: anything else gives wrong sizes/volumes."""
        return actions.units_report(env())

    # ================================================================ NIVEL 1: analise
    @deftool("read", creo=True)
    def creo_audit_scene(with_volumes: bool = False) -> dict[str, Any]:
        """Audit the scene: part budget, leftover helper parts (cuttool, hjunk), split pieces (p1.1), similar names,
        empty assemblies, duplicates at the same position, parts far from the rest, and (with_volumes) zero volumes.
        Returns findings[] with level (error|warn|info), code, items, message."""
        return actions.audit(env(), bool(with_volumes))

    @deftool("read", creo=True)
    def creo_measure(a: str, b: str = "", point: str = "") -> dict[str, Any]:
        """Distances from bounding boxes (exact boxes in global mm): a alone = size/center; a and b = min distance,
        center distance, per-axis gap/overlap; a and point '[x,y,z]' = distance from the point to the box. Not the true
        minimum distance between curved surfaces (use creo_clash_check for interference)."""
        pt = vec3(point, "point", None)
        return actions.measure(env(), a, b, pt)

    @deftool("read", creo=True, jobbed=True)
    def creo_clash_check(parts: str = "", tol: float = 0.01, exact: bool = True,
                         include_touching: bool = False, time_budget_s: float = 240.0) -> dict[str, Any]:
        """Interference check. Step 1: bounding boxes of all pairs. Step 2 (exact=true): the candidate pairs are exported
        to STL and the meshes are tested for triangle crossings/containment (so a bolt inside a hole is NOT a clash).
        parts: JSON list or comma-separated paths ('' = every part). Limits: 24 candidate pairs / 16 parts for the exact
        step. The mesh is tessellated: clashes smaller than the chord error can be missed. time_budget_s (default 240) caps
        the whole exact step: pairs not finished in time come back as interferes=null (never as "no clash").
        Runs as a background job."""
        names = actions.norm_list(parts)
        return job("clash", "creo_clash_check", {"parts": parts}, lambda: actions.clash_check(
            env(), names or None, float(tol), bool(exact), bool(include_touching), float(time_budget_s)))

    @deftool("read", creo=True, jobbed=True)
    def creo_mesh_qa(part: str) -> dict[str, Any]:
        """Quality of the STL mesh of one part: triangles, watertight (open/non-manifold edges), degenerate triangles,
        volume, area, bounding box, shells. Exports the part to a temp STL (background job)."""
        return job("mesh_qa", "creo_mesh_qa", {"part": part}, lambda: actions.mesh_qa(env(), part))

    @deftool("read", creo=True)
    def creo_inspect(part: str, material: str = "", counts: bool = False) -> dict[str, Any]:
        """One part in detail: exact bounding box, exact volume and area (GET_VOL_PROP), center, and mass when a material
        (steel, aluminum, cast_iron, brass, copper, titanium, abs, nylon...) or a density in g/cm3 is given or stored
        with creo_set_attributes. counts=true also counts faces and edges."""
        return actions.inspect_part(env(), part, material or None, bool(counts))

    # ================================================================ NIVEL 2: troca de dados
    @deftool("write", creo=True, jobbed=True)
    def creo_export(parts: str = "", fmt: str = "stl", folder: str = "", name: str = "", manifest: bool = True,
                    validate: bool = True) -> dict[str, Any]:
        """Export parts to files with validation and a JSON manifest (size + SHA-256 per file).

        fmt: 'stl' (one file per part), 'step' or 'sat' (one file for all the listed parts; EXPERIMENTAL: the command
        syntax is tried in order and the one that works is remembered). parts: JSON list or comma-separated ('' = all).
        folder must be inside ~/.creo_mcp or CREO_MCP_EXPORT_ROOTS (default: ~/.creo_mcp/exports/<name>_<time>)."""
        names = actions.norm_list(parts)
        return job(f"export {fmt}", "creo_export", {"parts": parts, "fmt": fmt}, lambda: actions.export(
            env(), names or None, fmt, folder or None, name, bool(manifest), bool(validate)))

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_import(path: str, fmt: str = "step", dry_run: bool = False) -> dict[str, Any]:
        """Import a STEP file into the scene (EXPERIMENTAL: Express documents STEP as save-only). Checks the part budget,
        the file name (max 65 chars) and that new parts really appeared. dry_run=true only validates the file."""
        if dry_run:
            return actions.import_file(env(), path, fmt, True)
        return job("import", "creo_import", {"path": path, "fmt": fmt}, lambda: actions.import_file(env(), path, fmt, False))

    @deftool("write", creo=False)
    def creo_set_attributes(part: str = "", attributes: str = "{}", action: str = "set", replace: bool = False,
                            keys: str = "") -> dict[str, Any]:
        """Attributes of parts (material, density_g_cm3, part_number, description, supplier, notes...) stored in a sidecar
        file (~/.creo_mcp/attributes.json): Express has no attribute API. They feed creo_inspect, creo_bom and creo_report.
        action: 'set' (merge, or replace=true), 'get' (part '' = all), 'clear' (optionally only `keys`, JSON list)."""
        a = (action or "set").lower()
        if a == "get":
            return {"ok": True, "attributes": exchange.get_attributes(actions.norm(part) if part else None)}
        if not part:
            raise ValueError("informe a peca (part)")
        p = actions.norm(part)
        if a == "clear":
            return {"ok": True, "attributes": exchange.clear_attributes(p, jparse(keys, "keys", None))}
        if a != "set":
            raise ValueError("action deve ser set, get ou clear")
        attrs = jparse(attributes, "attributes", {})
        if not isinstance(attrs, dict) or not attrs:
            raise ValueError("attributes deve ser um objeto JSON nao vazio")
        return {"ok": True, "part": p, "attributes": exchange.set_attributes(p, attrs, bool(replace))}

    @deftool("write", creo=True, jobbed=True)
    def creo_bom(fmt: str = "json", folder: str = "", name: str = "bom", material: str = "",
                 with_volumes: bool = True) -> dict[str, Any]:
        """Bill of materials of the scene: library parts (fasteners, bearings, profiles) with their catalog data plus loose
        parts (size from the bounding box, volume/mass from GET_VOL_PROP and the material set with creo_set_attributes
        or `material`). fmt 'json' (returned), 'csv' or 'xlsx' (written under folder, default ~/.creo_mcp/exports/bom)."""
        return job("bom", "creo_bom", {"fmt": fmt}, lambda: actions.bom(
            env(), fmt, folder or None, name, material or None, bool(with_volumes)))

    @deftool("write", creo=True, jobbed=True)
    def creo_views(views: str = "front,iso", folder: str = "", target: str = "", fit: bool = True) -> dict[str, Any]:
        """Screenshots of standard views (front, back, left, right, top, bottom, iso) and restores the original camera
        afterwards. EXPERIMENTAL until creo_validate confirms the camera call (oli:sd-set-vp-camera) on this Creo."""
        names = [v.strip() for v in str(views).split(",") if v.strip()]
        return job("views", "creo_views", {"views": views}, lambda: actions.views(
            env(), names, folder or None, actions.norm_list(target) or None, bool(fit)))

    @deftool("write", creo=True, jobbed=True)
    def creo_report(title: str = "Relatorio do modelo", folder: str = "", name: str = "relatorio",
                    screenshot: bool = True) -> dict[str, Any]:
        """Self-contained HTML report: part budget, scene audit, bill of materials with masses, server health and a
        screenshot. (HTML opens everywhere and prints to PDF.)"""
        return job("report", "creo_report", {"name": name}, lambda: actions.report(
            env(), title, folder or None, name, bool(screenshot)))

    @deftool("read", creo=False)
    def creo_fit(size: float, designation: str = "", purpose: str = "") -> dict[str, Any]:
        """ISO 286 limits and fits (no Creo needed). designation 'H7/g6' = hole/shaft fit (clearances, type, limits);
        'H7' or 'g6' = deviations and size limits for that class; purpose ('bearing seat', 'sliding', 'press'...) suggests
        a fit. size = nominal diameter in mm (up to 500)."""
        d = (designation or "").strip()
        if d and "/" in d:
            return {"ok": True, **fits.fit(float(size), d)}
        if d:
            return {"ok": True, **fits.deviations(float(size), d)}
        if purpose:
            return {"ok": True, **fits.suggest(purpose, float(size))}
        raise ValueError("informe designation ('H7/g6' ou 'H7') ou purpose")

    # ================================================================ NIVEL 3: modelagem tipada
    def _need_part(p: str) -> str:
        return actions.norm(p)

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_transform(part: str, steps: str) -> dict[str, Any]:
        """Move/rotate a part in place, in order. steps: JSON list of {"translate": [dx,dy,dz]} or
        {"rotate": {"axis": "z"|"x"|"y"|[x,y,z], "origin": [x,y,z], "angle": degrees}}. The result is checked against
        the expected bounding box (exact for blocks/cylinders about their axis)."""
        st = jparse(steps, "steps")
        if not isinstance(st, list) or not st:
            raise ValueError("steps deve ser uma lista JSON nao vazia")
        p = _need_part(part)
        return job("transform", "creo_transform", {"part": part, "steps": steps},
                   lambda: ops.transform(api.run, api.settle, p, st))

    @deftool("write", creo=True, model=True)
    def creo_group(name: str, parts: str) -> dict[str, Any]:
        """Move parts into a real assembly node (:ASSEMBLY, name WITHOUT slash). Positions in 3D do not change; paths
        become /<name>/<part>. Checks every part arrived."""
        names = actions.norm_list(parts)
        if not names:
            raise ValueError("informe as pecas (parts)")
        return actions.group_parts(env(), name.strip().lstrip("/"), names)

    @deftool("write", creo=True, model=True)
    def creo_color(parts: str, color: str) -> dict[str, Any]:
        """Set part color. color: a name (red, green, blue, yellow, orange, gray, silver, steel, brass, black, white...),
        '#RRGGBB' or '[r,g,b]' (0-1, or 0-255). Verified by reading the color back."""
        names = actions.norm_list(parts)
        if not names:
            raise ValueError("informe as pecas (parts)")
        rgb = ops.parse_color(jparse(color, "color", color) if str(color).strip().startswith("[") else color)
        return ops.color(api.run, names, rgb)

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_mirror(part: str, plane: str = "x", offset: float = 0.0, point: str = "", normal: str = "") -> dict[str, Any]:
        """Mirror a part IN PLACE (it becomes its mirror image). plane 'x'|'y'|'z' = the plane x|y|z = offset, or give a
        point and a normal ('[x,y,z]'). Axis-aligned planes are checked against the reflected bounding box."""
        p = _need_part(part)
        return job("mirror", "creo_mirror", {"part": part, "plane": plane}, lambda: ops.mirror(
            api.run, api.settle, p, plane, float(offset), vec3(point, "point"), vec3(normal, "normal")))

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_section(part: str, plane: str = "x", offset: float = 0.0, point: str = "", normal: str = "",
                     hide_half: bool = False) -> dict[str, Any]:
        """Cut a part by a plane into two parts (the new half is '<part>.1'). hide_half=true hides the new half to see
        inside. Checks that the two bounding boxes cover the original. Uses up one more part of the part budget (if a limit is configured)."""
        p = _need_part(part)

        def go():
            before = set(scene.parts_of(actions.read_tree(env(), 6) or []))
            res = ops.section(api.run, api.settle, p, plane, float(offset), vec3(point, "point"), vec3(normal, "normal"),
                              bool(hide_half))
            after = set(scene.parts_of(actions.read_tree(env(), 6) or []))
            new = sorted(after - before)
            if new:
                registry.add(new, "creo_section")
                res["new_parts"] = new
            return res

        return job("section", "creo_section", {"part": part, "plane": plane}, go)

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_holes(part: str, holes: str, face: str = "top", from_corner: bool = False, name: str = "",
                   tol_pct: float = 3.0) -> dict[str, Any]:
        """Typed holes in an existing part, volume-checked (removed volume vs expected, tolerance tol_pct %).

        holes: JSON list of {"kind": "through"|"blind"|"cbore"|"csink"|"tapped", "d": diameter mm  OR  "size": "M8"
        (clearance diameter, or the ISO tap-drill diameter for "tapped"), "depth" (blind/tapped; omitted = through),
        "at": [a, b], "pattern": {"kind": "linear"|"grid"|"circular", ...} (replaces "at"), "fit": "medium"|"fine",
        "cbore_d", "cbore_depth" (cbore; defaults from the size), "csink_d" (csink: 90 deg countersink, top/bottom
        faces only)}. face: top|bottom|front|back|left|right; `at` = [a, b] in that face's plane (top/bottom: x,y;
        front/back: x,z; left/right: y,z), ABSOLUTE global coordinates, or measured from the face's minimum corner
        with from_corner=true. The thread of "tapped" holes is cosmetic (drilled at the tap diameter, thread in the BOM).
        """
        h = jparse(holes, "holes")
        if not isinstance(h, list) or not h:
            raise ValueError("holes deve ser uma lista JSON nao vazia")
        p = _need_part(part)
        return job("holes", "creo_holes", {"part": part, "holes": holes, "face": face}, lambda: actions.holes(
            env(), p, h, face, bool(from_corner), name or None, float(tol_pct)))

    def _build(tool: str, label: str, make, verify: bool, group: str, dry_run: bool, args: dict):
        built = make()
        if dry_run:
            d = built.to_dict()
            d.update(ok=True, dry_run=True, parts_count=len(d["parts"]))
            return d
        return job(label, tool, args, lambda: actions.run_built(env(), built, bool(verify), tool, group or None))

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_pattern(component: str, params: str = "{}", pattern: str = "", name: str = "", verify: bool = True,
                     group: str = "", dry_run: bool = False) -> dict[str, Any]:
        """Repeat a component (any catalog kind or library kind: fastener, bearing, key, v_pulley, timing_pulley, coupling,
        profile, frame, pipe) in a pattern. pattern: JSON {"kind": "linear", "count", "dx", "dy", "x0", "y0"} |
        {"kind": "grid", "nx", "ny", "dx", "dy"} | {"kind": "circular", "count", "pcd", "cx", "cy", "start_deg",
        "sweep_deg"}; positions are added to the component's origin. component='points' with a pattern returns the
        point list only. Every copy is a separate part (counts against the part limit, if one is configured)."""
        pat = jparse(pattern, "pattern")
        if not isinstance(pat, dict):
            raise ValueError("pattern deve ser um objeto JSON")
        if component == "points":
            q = dict(pat)
            pts = place.pattern_points(q.pop("kind", "linear"), **q)
            return {"ok": True, "dry_run": True, "points": [list(x) for x in pts], "count": len(pts)}
        spec = {"name": name or component, "group": bool(group),
                "items": [{"component": component, "params": jparse(params, "params", {}), "name": name or component,
                           "pattern": pat}]}
        return _build("creo_pattern", f"pattern {component}", lambda: library.assemble(spec), verify, group or "", dry_run,
                      {"component": component, "pattern": pattern})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_profile_std(profile: str, length: float = 1000.0, axis: str = "z", start: str = "", roll: int = 0,
                         name: str = "perfil", end_trim: str = "", verify: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Standard structural profile cut to length: IPE/HEA/HEB/UPN/L-angle/SHS/RHS/CHS/flat/round (e.g. 'IPE200',
        'HEA160', 'UPN100', 'L50x5', 'SHS60x3', 'CHS60.3x3.2'; profile='list' shows the families). axis x|y|z = direction
        of the length; `start` = [x,y,z] of the section center at the start end; end_trim [a, b] = mm removed at each end
        (square cut). Mass/length in the BOM."""
        if profile == "list":
            return {"ok": True, "dry_run": True, **profiles.list_profiles()}
        st = vec3(start, "start", [0.0, 0.0, 0.0])
        et = jparse(end_trim, "end_trim", [0.0, 0.0])
        return _build("creo_profile_std", f"profile {profile}", lambda: library.profile_member(
            profile, float(length), axis, st, int(roll), name, et), verify, "", dry_run, {"profile": profile, "length": length})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_frame(members: str = "", box: str = "", prefix: str = "estrutura", verify: bool = True,
                   group: str = "", dry_run: bool = False) -> dict[str, Any]:
        """Frame of profile bars. Either members: JSON list of {"profile","from":[x,y,z],"to":[x,y,z],"roll","name",
        "trim":[a,b]} (each bar follows one axis), or box: {"length","width","height","columns":"SHS50x3",
        "beams":"SHS40x3","origin":[x,y,z],"bottom_beams":true,"top_beams":true} for a table/skid frame. Cut list
        (bars grouped by profile and length with total mass) goes to the BOM. Each bar is a part."""
        m = jparse(members, "members", None)
        b = jparse(box, "box", None)
        return _build("creo_frame", "frame", lambda: library.frame(m, b, prefix), verify, group, dry_run,
                      {"members": members, "box": box})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_pipe(points: str, od: float, wall: float, name: str = "tubo", verify: bool = True,
                  dry_run: bool = False) -> dict[str, Any]:
        """Piping run: one straight tube per leg between consecutive points (each leg along ONE axis). Bends are NOT
        modeled (no REVOLVE/free sweep in Express 9.0): legs meet at the node centers. Good for routing, cut lengths
        and the BOM. points: JSON list of [x,y,z]."""
        pts = jparse(points, "points")
        return _build("creo_pipe", "pipe", lambda: library.pipe(pts, float(od), float(wall), name), verify, "", dry_run,
                      {"points": points, "od": od, "wall": wall})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_fasteners(size: int = 12, grip: float = 20.0, length: float = 0.0, head: str = "hex",
                       washer_top: bool = True, washer_bottom: bool = True, nut: str = "auto", name: str = "fix",
                       at: str = "", positions: str = "", axis: str = "z", origin: str = "", engagement: float = 0.0,
                       verify: bool = True, group: str = "", dry_run: bool = False) -> dict[str, Any]:
        """ISO bolt + washers + nut stack through a clamped package of thickness `grip` (mm). size = metric M (6, 8, 10,
        12...); head 'hex' (ISO 4017/4014, with nut) | 'socket' (ISO 4762, threaded hole in the bottom part by default);
        nut 'auto' | 'yes' | 'no'; length 0 = shortest standard length that fits (2 thread pitches protrude);
        engagement = thread engagement without a nut. at [x,y] = one stack; positions [[x,y],...] = several identical
        stacks (names fix1, fix2...). Local frame: z=0 = bottom face of the clamped package, z=grip = top face (head
        side); axis/origin move it (head toward the axis direction). Everything goes to the BOM with ISO designations."""
        p: dict = {"size": int(size), "grip": float(grip), "length": float(length), "head": head,
                   "washer_top": bool(washer_top), "washer_bottom": bool(washer_bottom), "name": name, "axis": axis,
                   "engagement": float(engagement)}
        n = str(nut).strip().lower()
        if n in ("yes", "true", "1", "sim"):
            p["nut"] = True
        elif n in ("no", "false", "0", "nao"):
            p["nut"] = False
        elif n != "auto":
            raise ValueError("nut deve ser auto, yes ou no")
        if origin:
            p["origin"] = vec3(origin, "origin")
        pos = jparse(positions, "positions", None)
        if pos:
            p["positions"] = pos
        elif at:
            p["at"] = jparse(at, "at")
        elif not dry_run:
            raise ValueError("informe `at` ([x,y]) ou `positions` ([[x,y],...]): sem posicao o conjunto cairia na origem "
                             "e viraria peca perdida na cena. Use dry_run=true para so ver o plano.")
        return _build("creo_fasteners", "fasteners", lambda: library.component("fastener", p), verify, group, dry_run,
                      {"size": size, "grip": grip, "name": name})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_bearing_std(designation: str = "6205", mode: str = "body", rolling: int = 0, name: str = "rolamento",
                         axis: str = "z", origin: str = "", verify: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Deep-groove ball bearing 60xx/62xx/63xx with the standard bore/OD/width (designation='list' shows the table).
        mode 'body' = one solid envelope; 'rings' = inner + outer ring (+ `rolling` balls, each one a part)."""
        if designation == "list":
            return {"ok": True, "dry_run": True, "designations": sorted(hardware.BEARINGS)}
        o = vec3(origin, "origin", [0.0, 0.0, 0.0])
        return _build("creo_bearing_std", f"bearing {designation}", lambda: library.component(
            "bearing", {"designation": designation, "mode": mode, "rolling": int(rolling), "name": name, "axis": axis,
                        "origin": o}), verify, "", dry_run, {"designation": designation, "mode": mode})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_transmission(kind: str, params: str = "{}", verify: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Power-transmission parts. kind 'v_pulley' {profile SPZ/SPA/SPB/SPC, pitch_d, grooves, bore, hub_len, hub_od,
        keyway, layers, name, axis, origin} | 'timing_pulley' {belt GT2/GT3/HTD, teeth, belt_width, bore, flanges, hub_len,
        hub_od, set_screw_d, keyway} | 'coupling' {kind rigid/flange/jaw, bore1, bore2, od, length, keyway, set_screw_d,
        bolts, bolt_size, jaws} | 'key' {shaft_d, length, form A/B} (DIN 6885-1). Hubs get the standard keyway; pulley
        grooves and belt teeth follow the profile data (see the returned info)."""
        if kind not in ("v_pulley", "timing_pulley", "coupling", "key"):
            raise ValueError("kind deve ser v_pulley, timing_pulley, coupling ou key")
        p = jparse(params, "params", {})
        return _build("creo_transmission", f"transmission {kind}", lambda: library.component(kind, p), verify, "",
                      dry_run, {"kind": kind, "params": params})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_sweep_path(part: str, profile: str, spine: str, name: str = "varredura", remove: bool = False) -> dict[str, Any]:
        """EXPERIMENTAL (enable with CREO_MCP_EXPERIMENTAL=1; not validated live): sweep a profile along an existing edge
        with GENERAL_SWEEP_DIA. profile: JSON loops (["c",x,y,r] ...) drawn on a workplane at the spine start; spine:
        JSON ["edge", x1,y1,z1,x2,y2,z2] of an existing straight edge of `part`. The profile plane must not be parallel
        to the spine tangent. For bent pipes without this, use creo_pipe (straight legs)."""
        if not experimental_enabled():
            return {"ok": False, "experimental": True,
                    "error": "creo_sweep_path e experimental e esta desligada: defina CREO_MCP_EXPERIMENTAL=1 no servidor "
                             "MCP e valide com creo_validate numa peca descartavel antes de usar."}
        p = _need_part(part)
        loops = jparse(profile, "profile")
        sp = jparse(spine, "spine")
        if not isinstance(loops, list) or not loops or not isinstance(sp, list) or sp[:1] != ["edge"] or len(sp) != 7:
            raise ValueError('profile = lista de contornos; spine = ["edge", x1,y1,z1,x2,y2,z2]')
        return job("sweep", "creo_sweep_path", {"part": part}, lambda: actions.sweep_path(
            env(), p, loops, [float(v) for v in sp[1:]], name, bool(remove)))

    # ================================================================ NIVEL 4: escala
    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_build_assembly(spec: str, verify: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Build a whole assembly from a specification (JSON, or YAML if PyYAML is installed):
        {"name": "conj", "group": true, "items": [{"component": "flange", "name": "f1", "params": {...},
        "axis": "z", "origin": [0,0,0], "pattern": {"kind": "circular", "count": 6, "pcd": 120}}, ...]}.
        component = any catalog kind (creo_build_part kind='list') or library kind (fastener, bearing, key, v_pulley,
        timing_pulley, coupling, profile, frame, pipe). Each item sits in its own local frame (axis +x/-x/+y/-y/+z/-z and
        origin); pattern repeats it. The part budget is checked before anything is sent; every part gets its own bounding
        box check. group=true moves everything into a real assembly node. dry_run=true returns the plan (parts, BOM)."""
        data = parse_spec(spec)
        if not isinstance(data, dict):
            raise ValueError("spec deve ser um objeto com 'items'")
        group_name = ""
        if data.get("group"):
            group_name = str(data.get("group")) if isinstance(data.get("group"), str) else str(data.get("name") or "conjunto")
        return _build("creo_build_assembly", f"assembly {data.get('name', '')}", lambda: library.assemble(data), verify,
                      group_name, dry_run, {"spec": spec})

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_variants(kind: str, vary: str, base: str = "{}", mode: str = "grid", name_prefix: str = "",
                      export_fmt: str = "", folder: str = "", keep: bool = True, spacing: float = 0.0,
                      verify: bool = True, dry_run: bool = False) -> dict[str, Any]:
        """Build a family of the same component with different parameters (max 24). vary: JSON {param: [values]}
        (mode 'grid' = every combination, 'zip' = in parallel); base = common parameters. Each variant is named
        <prefix>_<param><value>... Set export_fmt ('stl'|'step'|'sat') and keep=false to build -> export -> delete one by
        one, so any number of variants fits in the part budget. spacing > 0 lays the variants out along x."""
        v = jparse(vary, "vary")
        b = jparse(base, "base", {})
        plist = library.variant_params(b, v, mode)
        keys = list(v)
        prefix = (name_prefix or kind).strip()
        planned = []
        for i, p in enumerate(plist):
            q = dict(p)
            label = library.variant_label(keys, q)
            q["name"] = f"{prefix}_{label}".replace(".", "p").replace("-", "m")
            if spacing:
                o = list(q.get("origin") or [0.0, 0.0, 0.0])
                o[0] = float(o[0]) + i * float(spacing)
                q["origin"] = o
            planned.append((q["name"], q))
        if dry_run:
            out = []
            for nm, q in planned:
                bt = library.component(kind, q)
                out.append({"name": nm, "parts": bt.plan.parts(), "info": bt.info})
            return {"ok": True, "dry_run": True, "variants": out, "count": len(out)}

        def go():
            rows, ok_all = [], True
            for nm, q in planned:
                row = {"variant": nm}
                try:
                    built = library.component(kind, q)
                except ValueError as exc:
                    row.update(ok=False, error=str(exc))
                    rows.append(row)
                    ok_all = False
                    continue
                res = actions.run_built(env(), built, bool(verify), "creo_variants", None)
                row.update(ok=bool(res.get("ok")), parts=res.get("parts_created"), info=built.info)
                if not res.get("ok"):
                    row["error"] = res.get("error") or res.get("report", {}).get("problems")
                    ok_all = False
                    rows.append(row)
                    continue
                if export_fmt:
                    ex = actions.export(env(), res.get("parts_created") or None, export_fmt, folder or None, nm, True, True)
                    row["export"] = {k: ex.get(k) for k in ("ok", "folder", "manifest", "error", "failures")}
                    ok_all = ok_all and bool(ex.get("ok"))
                if not keep and res.get("parts_created"):
                    tree = actions.read_tree(env(), 6)
                    dl = actions.delete_objects(env(), tree or [], res["parts_created"])
                    registry.forget(dl["deleted"])
                    row["deleted"] = dl["deleted"]
                    if dl["remaining"]:
                        row["ok"] = False
                        row["error"] = f"nao consegui apagar {dl['remaining']}"
                        ok_all = False
                rows.append(row)
            return {"ok": ok_all, "variants": rows, "count": len(rows)}

        return job("variants", "creo_variants", {"kind": kind, "vary": vary}, go)

    @deftool("read", creo=False)
    def creo_journal(limit: int = 50, session: str = "", days: int = 2, ok_only: bool = False) -> dict[str, Any]:
        """Journal of every call that CHANGED the scene (tool, arguments, result summary), newest last. session '' = all
        sessions of the last `days` days; 'this' = the current server run."""
        from .state import SESSION_ID

        sess = SESSION_ID if session == "this" else (session or None)
        rows = journal.entries(int(limit), sess, int(days), bool(ok_only))
        return {"ok": True, "count": len(rows), "entries": rows, "directory": journal.DIR}

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_replay(action: str = "plan", session: str = "", days: int = 2, include_raw_lisp: bool = False,
                    stop_on_error: bool = True, allow_dirty: bool = False) -> dict[str, Any]:
        """Rebuild a model from the journal. action 'plan' = the script (successful typed calls in order; clear/restore
        are left out; raw Lisp only with include_raw_lisp=true); 'run' = execute it (background job) - the scene must
        be empty unless allow_dirty=true, and the part budget is checked by every step."""
        from .state import SESSION_ID

        sess = SESSION_ID if session == "this" else (session or None)
        rows = journal.entries(0, sess, int(days), True)
        steps = [s for s in journal.build_replay(rows, bool(include_raw_lisp)) if s["tool"] in DISPATCH or s["tool"] in api.legacy]
        if (action or "plan") == "plan":
            return {"ok": True, "dry_run": True, "steps": steps, "count": len(steps)}
        if action != "run":
            raise ValueError("action deve ser plan ou run")
        if not steps:
            return {"ok": False, "error": "nenhum passo repetivel no diario"}
        tree = actions.read_tree(env(), 6)
        if tree is None:
            return actions._no_scene()
        if scene.count_bodies(tree) > 0 and not allow_dirty:
            return {"ok": False, "error": f"a cena tem {scene.count_bodies(tree)} peca(s): repetir por cima duplica nomes. "
                                          "Limpe a cena (creo_clear_scene) ou use allow_dirty=true."}
        table = dict(api.legacy)
        table.update({k: v for k, v in DISPATCH.items() if k not in ("creo_replay", "creo_batch")})
        return job("replay", "creo_replay", {"action": "run"},
                   lambda: journal.run_replay(steps, table, bool(stop_on_error)))

    @deftool("write", creo=True, model=True, jobbed=True)
    def creo_batch(steps: str, stop_on_error: bool = True, snapshot_first: bool = False) -> dict[str, Any]:
        """Run several tool calls in one request (one background job). steps: JSON list of {"tool": "creo_holes",
        "args": {...}}. Allowed: the typed modeling tools and creo_build_part / create_block / create_cylinder / boolean /
        extrude_profile / edge_treatment / shell, plus read tools. Not allowed: restore, clear_scene, free Lisp, nested
        batches. Stops at the first failure unless stop_on_error=false. snapshot_first=true saves a restore point first."""
        st = jparse(steps, "steps")
        if not isinstance(st, list) or not st or len(st) > 100:
            raise ValueError("steps deve ser uma lista JSON com 1 a 100 passos")
        banned = {"creo_restore", "creo_clear_scene", "creo_execute_lisp", "creo_run_command", "creo_dde_raw",
                  "creo_batch", "creo_replay", "creo_import"}
        table = dict(api.legacy)
        table.update(DISPATCH)
        for i, s in enumerate(st):
            if not isinstance(s, dict) or "tool" not in s:
                raise ValueError(f"passo {i + 1}: use {{\"tool\": nome, \"args\": {{...}}}}")
            if s["tool"] in banned:
                raise ValueError(f"passo {i + 1}: a ferramenta {s['tool']} nao e permitida em lote")
            if s["tool"] not in table:
                raise ValueError(f"passo {i + 1}: ferramenta desconhecida {s['tool']!r}")
            s.setdefault("args", {})

        def go():
            if snapshot_first:
                bk = snapshot.create(api.run, "antes_do_lote", auto=True)
                if not bk.get("ok"):
                    return {"ok": False, "error": "nao consegui salvar o snapshot; lote nao executado", "snapshot": bk}
            return journal.run_replay(st, {k: table[k] for k in {s["tool"] for s in st}}, bool(stop_on_error))

        return job("batch", "creo_batch", {"n": len(st)}, go)

    @deftool("write", creo=False)
    def creo_job_cancel(job: str = "") -> dict[str, Any]:
        """Cancel a running background job (job '' = the running one). Cooperative: the step in progress finishes, the next
        Creo call of the job is refused, pending command panels are cancelled. Parts already built stay in the scene
        (creo_audit_scene / creo_clear_scene tell what to clean)."""
        return api.cancel_job(job)

    @deftool("write", creo=True, jobbed=True)
    def creo_validate(scope: str = "quick", cleanup: bool = True, confirm: bool = False) -> dict[str, Any]:
        """Live self-validation of the new tools on DISPOSABLE parts (/mcp_exp_*, never your parts): builds small blocks,
        runs each tool and compares with analytic values, then deletes them. scope 'quick' (core tools) or 'full'
        (also STEP/SAT export, views, snapshot). Passing tools are promoted from 'experimental' to 'verified'
        (creo_capabilities). Run it once per Creo install and after Creo updates; takes a few minutes (background job).
        Needs confirm=true: without it nothing is created and the plan is returned (a cancelled run leaves /mcp_exp_* parts)."""
        from . import validate

        if not confirm:
            return {"ok": False, "dry_run": True,
                    "error": ("creo_validate cria pecas descartaveis /mcp_exp_* e leva alguns minutos. Chame de novo com "
                              "confirm=true, de preferencia numa cena vazia e depois de creo_snapshot."),
                    "scope": scope, "cleanup": bool(cleanup)}

        return job("validate", "creo_validate", {"scope": scope}, lambda: validate.run(env(), scope, bool(cleanup)))

    # ================================================================ recursos e prompts
    @server.resource("creo://catalog", mime_type="application/json")
    def res_catalog() -> str:
        """Component kinds (catalog + library), standard profiles and bearing designations."""
        return json.dumps({"kinds": library.kinds(), "profiles": profiles.list_profiles(),
                           "bearings": sorted(hardware.BEARINGS)}, ensure_ascii=False, indent=1)

    @server.resource("creo://limits", mime_type="application/json")
    def res_limits() -> str:
        """Express 9.0 limits, safe policy and where files go."""
        return json.dumps({"limits": capabilities.EXPRESS_LIMITS, "policy": config.policy_summary(),
                           "units": {"length": "mm", "mass": "g"}}, ensure_ascii=False, indent=1)

    @server.resource("creo://snapshots", mime_type="application/json")
    def res_snapshots() -> str:
        """Saved restore points (newest last)."""
        return json.dumps(snapshot.list_snapshots(), ensure_ascii=False, indent=1)

    @server.resource("creo://journal/today", mime_type="application/json")
    def res_journal() -> str:
        """Today's journal of scene-changing calls."""
        return json.dumps(journal.entries(200, None, 1), ensure_ascii=False, indent=1, default=str)

    @server.prompt()
    def industrial_part(description: str) -> str:
        """Plan and build an industrial part safely."""
        return ("Build this part in Creo Elements/Direct Express 9.0: " + description + "\n"
                "1) creo_get_status, creo_units, creo_part_budget. 2) Prefer creo_build_part / creo_profile_std / "
                "creo_fasteners / creo_bearing_std / creo_transmission over free Lisp (use dry_run=true to preview). "
                "3) creo_snapshot before risky steps. 4) Verify: creo_inspect, creo_audit_scene, creo_screenshot. "
                "5) Deliver with creo_export (+ manifest) and creo_bom.")

    @server.prompt()
    def assembly_from_spec(spec: str) -> str:
        """Build an assembly from a specification."""
        return ("Build this assembly with creo_build_assembly (dry_run=true first to check part count and BOM):\n" + spec +
                "\nThen creo_clash_check, creo_audit_scene, creo_bom(fmt='xlsx') and creo_report.")

    @server.prompt()
    def safe_cleanup() -> str:
        """Clean the scene without losing the user's work."""
        return ("1) creo_audit_scene. 2) creo_snapshot(label='antes_da_limpeza'). 3) creo_clear_scene(scope='mine') "
                "-> read would_delete/protected. 4) Only with the user's OK: confirm=true. Never use scope='all' unless "
                "the user asked for it.")

    return DISPATCH
