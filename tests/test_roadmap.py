"""Testes offline das ferramentas dos niveis 0-4 (sem Creo): modulos puros + orquestracao contra um Creo simulado."""

import asyncio
import json
import os
import re
import struct
import time

import pytest

from creo_mcp import (actions, analysis, capabilities, config, exchange, fits, health, journal, lease, library, lisp, mesh,
                      ops, registry, scene, snapshot, tools_ext, validate)
from creo_mcp.parts import PartError


# ------------------------------------------------------------------ infraestrutura de teste
@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isola todos os arquivos de estado (~/.creo_mcp) numa pasta temporaria."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config, "HOME", str(home))
    monkeypatch.setattr(registry, "PATH", str(home / "created.json"))
    monkeypatch.setattr(journal, "DIR", str(home / "journal"))
    monkeypatch.setattr(lease, "PATH", str(home / "lease.json"))
    monkeypatch.setattr(health, "INCIDENT_FILE", str(home / "incidents.jsonl"))
    monkeypatch.setattr(health, "HEALTH_FILE", str(home / "health.json"))
    monkeypatch.setattr(exchange, "ATTR_FILE", str(home / "attributes.json"))
    monkeypatch.setattr(capabilities, "VALIDATION_FILE", str(home / "validation.json"))
    monkeypatch.setattr(actions, "EXPORT_DIR", str(home / "exports"))
    monkeypatch.setattr(actions, "BOM_STORE", str(home / "bom_lines.json"))
    monkeypatch.setattr(actions, "SYNTAX_FILE", str(home / "command_syntax.json"))
    monkeypatch.setenv("CREO_MCP_SNAPSHOT_DIR", str(tmp_path / "snaps"))
    monkeypatch.delenv("CREO_MCP_EXPORT_ROOTS", raising=False)
    health.reset()
    journal.reset()
    return home


def box_stl(path, lo, hi):
    """STL binario de um paralelepipedo de lo a hi."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7),
             (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", len(faces)))
        for a, b, c in faces:
            fh.write(struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0))


class FakeCreo:
    """Creo simulado. parts: {caminho: (lo, hi)}; volumes pelo produto da caixa. Responde as formas Lisp que actions usa."""

    def __init__(self, parts, assemblies=None, wps=("/w1", "/wz")):
        self.parts = {k: (tuple(v[0]), tuple(v[1])) for k, v in parts.items()}
        self.assemblies = dict(assemblies or {})          # {"/asm": ["/asm/p1", ...]} (as pecas ja estao em parts)
        self.wps = list(wps)
        self.calls = []
        self.vol_parts = []
        self.settles = 0

    # arvore
    def top_names(self):
        inside = {p for ch in self.assemblies.values() for p in ch}
        return self.wps + list(self.assemblies) + [p for p in self.parts if p not in inside]

    def tree(self):
        rows = []
        for w in self.wps:
            rows.append(f'("{w}" :WP NIL)')
        for a, ch in self.assemblies.items():
            kids = " ".join(f'("{c}" :PART NIL)' for c in ch if c in self.parts)
            rows.append(f'("{a}" :ASSEMBLY ({kids}))')
        inside = {p for ch in self.assemblies.values() for p in ch}
        for p in self.parts:
            if p not in inside:
                rows.append(f'("{p}" :PART NIL)')
        return "(" + " ".join(rows) + ")"

    def vol(self, p):
        lo, hi = self.parts[p]
        return (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])

    def run(self, code, timeout=30, *a):
        self.calls.append(code)
        ok = {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        if "labels" in code and "sd-inq-obj-children" in code:
            return {**ok, "result": self.tree()}
        if "sd-inq-exact-box" in code:
            names = re.findall(r'"(/[^"]*)"', code)
            assert len(names) == 1, "uma peca por chamada"
            n = names[0]
            if n not in self.parts:
                return {**ok, "result": ":PART-NOT-FOUND"}
            lo, hi = self.parts[n]
            return {**ok, "result": f"({lo[0]},{lo[1]},{lo[2]}\n {hi[0]},{hi[1]},{hi[2]})"}
        if "*mcp-vp-parts*" in code and "setf *mcp-vp-parts*" in code:
            self.vol_parts = list(dict.fromkeys(re.findall(r'"(/[^"]*)"', code)))
            assert len(self.vol_parts) == 1 and "(current_part " in code, "uma peca por leitura, tornada corrente"
            return ok
        if code.strip() == "*mcp-vp-result*":
            rows = [f'("{p}" {self.vol(p)} {2 * self.vol(p) ** (2 / 3)} "{p}")' if p in self.parts else f'("{p}" :MISSING)'
                    for p in self.vol_parts]
            return {**ok, "result": "(" + " ".join(rows) + ")"}
        if "delete_3d" in code and "(dolist (n (list" in code:
            for n in re.findall(r'"(/[^"]*)"', code):
                for key in [k for k in self.parts if k == n or k.startswith(n + "/")]:
                    self.parts.pop(key)
                self.assemblies.pop(n, None)
                for ch in self.assemblies.values():
                    if n in ch:
                        ch.remove(n)
            return {**ok, "result": "(" + " ".join(f'"{n}"' for n in self.top_names()) + ")"}
        return ok

    def settle(self, seconds=10.0, until=None):
        self.settles += 1
        return []

    def env(self, shot=None):
        return actions.Env(self.run, self.settle, sleep=lambda s: None, pids=lambda: [100], shot=shot)


CUBE = ((0, 0, 0), (10, 10, 10))


# ------------------------------------------------------------------ cena, orcamento, registro
def test_scene_budget_and_leftovers():
    tree = lisp.parse_sexp('(("/w1" :WP NIL) ("/a" :ASSEMBLY (("/a/p1" :PART NIL) ("/a/p2" :PART NIL))) ("/cuttool" :PART NIL)'
                           ' ("/x" :PART NIL) ("/x.1" :PART NIL))')
    assert scene.count_bodies(tree) == 5 and scene.assemblies_of(tree) == ["/a"]
    assert scene.leftovers(tree) == ["/cuttool"]
    assert scene.split_pieces(tree) == [{"path": "/x.1", "of": "/x"}]
    b = scene.budget(tree, 60)
    assert not b["ok"] and b["left"] == -5 and b["limit"] == 60
    assert scene.budget(tree, 10)["ok"]


def test_registry_plan_clear_only_mine(iso):
    registry.add(["/mine", "/asm/inner"], "t")
    assert registry.top_of("/asm/inner") == "/asm"
    plan = registry.plan_clear(["/w1", "/mine", "/other", "/asm"], True, {"/w1": "WP", "/mine": "PART", "/other": "PART", "/asm": "ASSEMBLY"})
    assert plan["deletable"] == ["/mine", "/asm"] and plan["protected"] == ["/other"]
    registry.forget(["/mine"])
    assert "/mine" not in registry.known()


def test_actions_budget_reports_leftovers(iso):
    f = FakeCreo({"/a": CUBE, "/cuttool": CUBE})
    r = actions.budget(f.env(), 59)
    assert r["used"] == 2 and not r["ok"] and r["leftovers"] == ["/cuttool"] and "limite" in r["advice"]
    assert actions.budget(f.env(), 5)["ok"]


def test_clear_scene_is_safe_by_default(iso, monkeypatch):
    f = FakeCreo({"/mine": CUBE, "/other": CUBE})
    registry.add(["/mine"], "t")
    env = f.env()
    dry = actions.clear_scene(env)
    assert dry["dry_run"] and dry["would_delete"] == ["/mine"] and dry["protected"] == ["/other"]
    assert set(f.parts) == {"/mine", "/other"}                      # nada apagado sem confirm
    monkeypatch.setattr(snapshot, "create", lambda *a, **k: {"ok": True, "path": "x.pk2"})
    done = actions.clear_scene(env, "mine", True)
    assert done["ok"] and done["deleted"] == ["/mine"] and set(f.parts) == {"/other"}
    assert done["snapshot"]["ok"] and "/mine" not in registry.known()
    assert actions.clear_scene(env, "bogus")["ok"] is False


def test_clear_scene_aborts_if_snapshot_fails(iso, monkeypatch):
    f = FakeCreo({"/mine": CUBE})
    registry.add(["/mine"], "t")
    monkeypatch.setattr(snapshot, "create", lambda *a, **k: {"ok": False, "error": "disco cheio"})
    r = actions.clear_scene(f.env(), "mine", True)
    assert r["ok"] is False and "nada foi apagado" in r["error"] and "/mine" in f.parts


def test_clear_scene_all_removes_assemblies(iso, monkeypatch):
    f = FakeCreo({"/a/p1": CUBE, "/a/p2": CUBE, "/solo": CUBE}, {"/a": ["/a/p1", "/a/p2"]})
    monkeypatch.setattr(snapshot, "create", lambda *a, **k: {"ok": True, "path": "x"})
    r = actions.clear_scene(f.env(), "all", True)
    assert r["ok"] and not f.parts and not f.assemblies, r


# ------------------------------------------------------------------ analise
def test_audit_measure_and_inspect(iso):
    f = FakeCreo({"/a": ((0, 0, 0), (60, 40, 20)), "/b": ((200, 0, 0), (260, 40, 20)), "/hjunk": CUBE})
    env = f.env()
    aud = actions.audit(env, True)
    codes = {x["code"] for x in aud["findings"]}
    assert "leftover" in codes or any("hjunk" in str(x["items"]) for x in aud["findings"]), aud
    m = actions.measure(env, "a", "/b")
    assert m["ok"] and m["gap_mm"] == 140.0 and m["boxes_intersect"] is False
    assert actions.measure(env, "/a", "", [100, 0, 0])["ok"]
    ins = actions.inspect_part(env, "/a", "steel")
    assert ins["ok"] and ins["volume_mm3"] == 48000 and ins["mass_g"] == pytest.approx(376.8, abs=0.01)
    nomat = actions.inspect_part(env, "/a")
    assert "mass_note" in nomat and nomat.get("mass_g") is None
    assert actions.inspect_part(env, "/nope")["ok"] is False
    bad = actions.inspect_part(env, "/a", "unobtainium")
    assert "warning" in bad


def test_inspect_uses_density_from_attributes(iso):
    f = FakeCreo({"/a": ((0, 0, 0), (10, 10, 10))})
    exchange.set_attributes("/a", {"density_g_cm3": 2.7})
    assert actions.inspect_part(f.env(), "/a")["mass_g"] == pytest.approx(2.7, abs=0.001)


def test_analysis_density_and_clash_candidates():
    assert analysis.density_of("Aço") is None or isinstance(analysis.density_of("Aço"), float)
    assert analysis.density_of("steel") == 7.85 and analysis.density_of(2.2) == 2.2 and analysis.density_of("xyz") is None
    a, b = ((0, 0, 0), (10, 10, 10)), ((5, 5, 5), (15, 15, 15))
    cands = analysis.clash_candidates({"/a": a, "/b": b, "/c": ((100, 0, 0), (110, 10, 10))})
    assert len(cands) == 1 and cands[0]["overlap_volume_mm3"] == 125
    touch = analysis.clash_candidates({"/a": a, "/t": ((10, 0, 0), (20, 10, 10))})
    assert touch == []
    assert len(analysis.clash_candidates({"/a": a, "/t": ((10, 0, 0), (20, 10, 10))}, include_touching=True)) == 1


def test_clash_check_exact_uses_mesh_not_boxes(iso, monkeypatch):
    # /bolt esta DENTRO do furo de /plate: caixas sobrepostas, malhas sem interferencia -> nao e clash
    f = FakeCreo({"/p1": ((0, 0, 0), (40, 40, 40)), "/p2": ((20, 20, 0), (60, 60, 40)), "/far": ((500, 0, 0), (510, 10, 10))})
    stls = {"/p1": ((0, 0, 0), (40, 40, 40)), "/p2": ((20, 20, 0), (60, 60, 40)), "/far": ((500, 0, 0), (510, 10, 10))}

    def fake_export(env, part, path, wait_s=45.0):
        box_stl(path.replace("/", os.sep), *stls[part])
        return {"part": part, "file": path, "ok": True, "bytes": 684}

    monkeypatch.setattr(actions, "export_stl_file", fake_export)
    r = actions.clash_check(f.env(), None, 0.01, True)
    assert r["ok"] and r["candidate_pairs"] == 1 and len(r["clashes"]) == 1 and r["clashes"][0]["interferes"], r
    # caixa sobreposta mas malhas separadas (p2 deslocado so em z): nao interfere
    stls["/p2"] = ((20, 20, 41), (60, 60, 80))
    f2 = FakeCreo({"/p1": ((0, 0, 0), (40, 40, 40)), "/p2": ((20, 20, 0), (60, 60, 40))})
    r2 = actions.clash_check(f2.env(), None, 0.01, True)
    assert r2["ok"] and r2["clashes"] == [] and r2["pairs"][0]["interferes"] is False
    only_boxes = actions.clash_check(f.env(), None, 0.01, False)
    assert only_boxes["method"] == "caixa envolvente" and only_boxes["candidate_pairs"] == 1
    assert actions.clash_check(f.env(), ["/zzz"])["ok"] is False


def test_clash_check_limits(iso, monkeypatch):
    parts = {f"/p{i}": ((0, 0, 0), (10, 10, 10)) for i in range(10)}      # 45 pares candidatos: acima de 24
    r = actions.clash_check(FakeCreo(parts).env(), None, 0.01, True)
    assert r["ok"] is False and "24" in r["error"]


def test_mesh_qa_reports_watertight(iso, monkeypatch):
    f = FakeCreo({"/a": CUBE})

    def fake_export(env, part, path, wait_s=45.0):
        box_stl(path.replace("/", os.sep), *CUBE)
        return {"part": part, "file": path, "ok": True}

    monkeypatch.setattr(actions, "export_stl_file", fake_export)
    r = actions.mesh_qa(f.env(), "a")
    assert r["part"] == "/a" and r.get("watertight") is True and r.get("triangles") == 12, r
    assert actions.mesh_qa(f.env(), "/zzz")["ok"] is False


def test_mesh_interference_primitives():
    from creo_mcp import stl

    a = [t for t in _tris(((0, 0, 0), (10, 10, 10)))]
    b = [t for t in _tris(((5, 5, 5), (15, 15, 15)))]
    c = [t for t in _tris(((20, 0, 0), (30, 10, 10)))]
    inner = [t for t in _tris(((2, 2, 2), (4, 4, 4)))]
    assert mesh.interference(a, b)["intersects"] is True
    assert mesh.interference(a, c)["intersects"] is False
    r = mesh.interference(a, inner)
    assert r["intersects"] is True and r["contained"] == "b_inside_a"
    # contato nao e interferencia: face com face, aresta com aresta, vertice com vertice
    face = _tris(((10, 0, 0), (20, 10, 10)))
    edge = _tris(((10, 10, 0), (20, 20, 10)))
    corner = _tris(((10, 10, 10), (20, 20, 20)))
    assert mesh.interference(a, face)["intersects"] is False
    assert mesh.interference(a, edge)["intersects"] is False
    assert mesh.interference(a, corner)["intersects"] is False
    # caso simetrico (aresta do pino exatamente sobre a diagonal da face): tem de ser detectado
    plate = _tris(((0, 0, 0), (40, 40, 10)))
    pin = _tris(((15, 15, 5), (25, 25, 30)))
    assert mesh.interference(plate, pin)["intersects"] is True
    assert mesh.interference(pin, plate)["intersects"] is True
    pin_beside = _tris(((15, 15, 10), (25, 25, 30)))                # apoiado em cima: so contato
    assert mesh.interference(plate, pin_beside)["intersects"] is False


def _tris(box):
    import tempfile

    from creo_mcp import stl

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.stl")
        box_stl(p, *box)
        return stl.load_triangles(p)


# ------------------------------------------------------------------ troca de dados
def test_export_stl_writes_manifest_with_sha256(iso, monkeypatch):
    f = FakeCreo({"/a": ((0, 0, 0), (10, 20, 30)), "/b": CUBE})

    def fake_export(env, part, path, wait_s=45.0):
        box_stl(path.replace("/", os.sep), *f.parts[part])
        return {"part": part, "file": path, "ok": True, "bytes": os.path.getsize(path.replace("/", os.sep))}

    monkeypatch.setattr(actions, "export_stl_file", fake_export)
    r = actions.export(f.env(), None, "stl", None, "lote", True, True)
    assert r["ok"] and len(r["files"]) == 2 and r["manifest"]
    man = json.load(open(r["manifest"].replace("/", os.sep), encoding="utf-8"))
    import hashlib

    for e in man["files"]:
        data = open(e["path"] if os.path.exists(e["path"]) else e["path"].replace("/", os.sep), "rb").read()
        assert e["sha256"] == hashlib.sha256(data).hexdigest()
        assert e["validation"]["ok"] and e["bbox"]
    assert man["units"] == {"length": "mm", "mass": "g"}


def test_export_refuses_folder_outside_roots_and_unknown_part(iso, tmp_path):
    f = FakeCreo({"/a": CUBE})
    r = actions.export(f.env(), None, "stl", str(tmp_path / "fora"), "x", True, True)
    assert r["ok"] is False and "pasta fora" in r["error"]
    assert actions.export(f.env(), ["/zzz"], "stl", None, "x", True, True)["ok"] is False
    assert actions.export(f.env(), None, "iges", None, "x", True, True)["ok"] is False


def test_export_step_tries_syntaxes_and_remembers_the_one_that_works(iso):
    f = FakeCreo({"/a": CUBE})
    sent = []

    def run(code, timeout=30, *a):
        if "STEP_EXPORT" in code:
            sent.append(code)
            if ":FILENAME" in code:           # so este modelo de comando "funciona" no Creo simulado
                path = re.search(r'FILENAME \\"([^"\\]+)\\"', code).group(1)
                with open(path.replace("/", os.sep), "w") as fh:
                    fh.write("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n"
                             "#1=PRODUCT('a','a','',(#2));\n#9=MANIFOLD_SOLID_BREP('a',#3);\nENDSEC;\nEND-ISO-10303-21;\n")
            return {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        return f.run(code, timeout)

    env = actions.Env(run, f.settle, sleep=lambda s: None, pids=lambda: [1])
    r = actions.export(env, None, "step", None, "peca", False, True)
    assert r["ok"], r
    assert len(sent) >= 2 and ":FILENAME" in sent[-1]
    assert actions.load_syntax()["export_step"]["template"].count(":FILENAME") == 1
    sent.clear()
    r2 = actions.export(env, None, "step", None, "peca2", False, True)
    assert r2["ok"] and len(sent) == 1                       # o modelo que funcionou passa a ser o primeiro


def test_export_step_fails_with_attempts_when_nothing_works(iso):
    f = FakeCreo({"/a": CUBE})
    env = f.env()
    r = actions.export(env, None, "step", None, "peca", False, True)
    assert r["ok"] is False and r.get("experimental") and len(r["attempts"]) == len(actions.EXPORT_TEMPLATES["step"])


def test_import_dry_run_and_validation(iso, tmp_path):
    f = FakeCreo({"/a": CUBE})
    ok_file = os.path.join(config.HOME, "x.stp")
    open(ok_file, "w").write("ISO-10303-21;")
    r = actions.import_file(f.env(), ok_file, "step", True)
    assert r["ok"] and r["dry_run"] and r["parts_before"] == 1
    assert actions.import_file(f.env(), ok_file, "iges")["ok"] is False
    assert actions.import_file(f.env(), str(tmp_path / "x.stp"), "step")["ok"] is False        # fora das pastas
    assert actions.import_file(f.env(), os.path.join(config.HOME, "nao.stp"), "step")["ok"] is False


def test_import_detects_new_parts_and_registers_them(iso):
    f = FakeCreo({"/a": CUBE})
    path = os.path.join(config.HOME, "x.stp")
    open(path, "w").write("ISO-10303-21;")

    def run(code, timeout=30, *a):
        if "STEP_IMPORT" in code:
            f.parts["/importada"] = CUBE
            return {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        return f.run(code, timeout)

    env = actions.Env(run, f.settle, sleep=lambda s: None, pids=lambda: [1])
    r = actions.import_file(env, path, "step", False)
    assert r["ok"] and r["imported_parts"] == ["/importada"] and "/importada" in registry.known()


def test_bom_lines_group_library_and_loose_parts(iso):
    stored = [{"item": "Parafuso M8", "category": "parafuso", "qty": 2, "parts": ["/p_1", "/p_2"], "unit_mass_g": 10.0,
               "material": "Aço", "size": ""}]
    lines = actions.build_bom_lines(["/p_1", "/p_2", "/placa"], {"/placa": ((0, 0, 0), (100, 50, 10))},
                                    {"/placa": {"volume_mm3": 50000.0, "area_mm2": 1.0}}, {"/placa": {"material": "steel"}}, stored)
    assert len(lines) == 2
    placa = next(x for x in lines if x["parts"] == ["/placa"])
    assert placa["unit_mass_g"] == pytest.approx(392.5, abs=0.01) and placa["size"] == "100x50x10"
    # peca da biblioteca que nao existe mais sai da lista; quantidade nunca passa das pecas que existem
    lines2 = actions.build_bom_lines(["/p_1"], {}, {}, {}, stored)
    assert lines2[0]["qty"] == 1 and lines2[0]["parts"] == ["/p_1"]


def test_bom_csv_xlsx_json_and_masses(iso):
    f = FakeCreo({"/placa": ((0, 0, 0), (100, 50, 10)), "/pino": ((0, 0, 0), (10, 10, 10))})
    exchange.set_attributes("/placa", {"material": "aluminum", "part_number": "PL-1"})
    r = actions.bom(f.env(), "json", None, "bom", "steel", True)
    assert r["ok"] and r["totals"]["itens"] == 2
    placa = next(x for x in r["rows"] if "/placa" in x["pecas"])
    assert placa["item"] == "PL-1" and placa["massa_unit_g"] == pytest.approx(135.0, abs=0.01)
    pino = next(x for x in r["rows"] if "/pino" in x["pecas"])
    assert pino["massa_unit_g"] == pytest.approx(7.85, abs=0.01)             # material padrao informado
    csv_r = actions.bom(f.env(), "csv", None, "lista", None, True)
    assert csv_r["file"].endswith("lista.csv") and "PL-1" in open(csv_r["file"].replace("/", os.sep), encoding="utf-8").read()
    x_r = actions.bom(f.env(), "xlsx", None, "lista", None, True)
    import zipfile

    assert zipfile.is_zipfile(x_r["file"].replace("/", os.sep))
    assert actions.bom(f.env(), "pdf")["ok"] is False
    assert actions.bom(FakeCreo({}).env())["ok"] is False                     # cena vazia


def test_bom_store_roundtrip(iso):
    actions.bom_store([{"item": "x", "qty": 1, "parts": ["a"]}], "creo_fasteners", ["/a"])
    got = actions._bom_load()
    assert got[0]["parts"] == ["/a"] and got[0]["tool"] == "creo_fasteners"


def test_attributes_sidecar_and_report_html(iso):
    exchange.set_attributes("/a", {"material": "steel"})
    exchange.set_attributes("/a", {"supplier": "ACME"})
    assert exchange.get_attributes("/a") == {"material": "steel", "supplier": "ACME"}
    exchange.set_attributes("/a", {"x": 1}, replace=True)
    assert exchange.get_attributes("/a") == {"x": 1}
    exchange.clear_attributes("/a")
    assert exchange.get_attributes("/a") == {}
    page = exchange.report_html("T <b>", [{"title": "S", "status": "ok", "items": ["i"], "table": {"headers": ["h"], "rows": [["<x>"]]}}])
    assert "&lt;b&gt;" in page and "&lt;x&gt;" in page and "<b>" not in page


def test_report_html_file(iso):
    f = FakeCreo({"/a": CUBE})
    r = actions.report(f.env(), "Teste", None, "rel", False)
    assert r["ok"] and os.path.exists(r["file"].replace("/", os.sep))
    assert "Teste" in open(r["file"].replace("/", os.sep), encoding="utf-8").read()


def test_validate_file_formats(tmp_path):
    step = tmp_path / "a.stp"
    step.write_text("ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('X'));\nENDSEC;\nDATA;\n#1=MANIFOLD_SOLID_BREP('a',#2);\nENDSEC;\nEND-ISO-10303-21;\n")
    assert exchange.validate_file(str(step), "step")["ok"]
    bad = tmp_path / "b.stp"
    bad.write_text("lixo")
    assert not exchange.validate_file(str(bad), "step")["ok"]
    sat = tmp_path / "a.sat"
    sat.write_text("700 0 1 0\nbody $-1 -1\n")
    assert exchange.validate_file(str(sat), "sat")["ok"]
    empty = tmp_path / "e.stl"
    empty.write_bytes(b"")
    assert exchange.validate_file(str(empty), "stl")["error"] == "arquivo vazio"
    with pytest.raises(PartError):
        exchange.check_name("x" * 70 + ".stp", "step")
    with pytest.raises(PartError):
        exchange.check_name("a.txt", "step")


def test_views_camera_helpers_and_flow(iso):
    cam = actions.parse_camera("((0.0 0.0 10.0) (0.0 0.0 0.0) (0.0 1.0 0.0) :PARALLEL 100.0)")
    assert cam["from"] == [0.0, 0.0, 10.0] and cam["projection"] == ":PARALLEL"
    assert actions.parse_camera("(:NO-CAMERA)") is None
    code = actions.camera_set_code(cam)
    assert code.count("(") == code.count(")") and "sd-set-vp-camera" in code
    f = FakeCreo({"/a": ((0, 0, 0), (100, 100, 100))})
    sent = []

    def run(code, timeout=30, *a):
        if "sd-set-vp-camera" in code:
            sent.append(code)
            return {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        if "sd-inq-vp-camera" in code and "camera-struct-from-pt" in code:
            return {"ok": True, "result": "((0.0 0.0 10.0) (0.0 0.0 0.0) (0.0 1.0 0.0) :PARALLEL 100.0)", "dialogs": [], "error": None}
        return f.run(code, timeout)

    shots = []

    def shot(path):
        shots.append(path)
        open(path, "wb").write(b"png")
        return {"ok": True, "path": path}

    env = actions.Env(run, f.settle, sleep=lambda s: None, pids=lambda: [1], shot=shot)
    r = actions.views(env, ["front", "iso"], None, None, True)
    assert r["ok"] and [v["view"] for v in r["views"]] == ["front", "iso"] and len(shots) == 2
    assert len(sent) == 3 and r["camera_restored"]                       # 2 vistas + restauracao da camera original
    assert actions.views(env, ["lado-de-baixo"], None, None)["ok"] is False


# ------------------------------------------------------------------ operacoes tipadas
def test_group_parts_checks_arrival(iso, monkeypatch):
    f = FakeCreo({"/a": CUBE, "/b": CUBE})

    def fake_group(runner, name, parts):
        f.assemblies["/" + name] = []
        for p in parts:
            new = f"/{name}/{p.lstrip('/')}"
            f.parts[new] = f.parts.pop(p)
            f.assemblies["/" + name].append(new)
        return {"ok": True}

    monkeypatch.setattr(ops, "group", fake_group)
    r = actions.group_parts(f.env(), "conj", ["a", "/b"])
    assert r["ok"] and r["moved"] == ["/a", "/b"] and "/conj" in registry.known()
    miss = actions.group_parts(f.env(), "outro", ["/nao_existe"])
    assert miss["ok"] is False and "inexistentes" in miss["error"]


def test_holes_volume_check(iso, monkeypatch):
    from creo_mcp import builder

    f = FakeCreo({"/placa": ((0, 0, 0), (60, 40, 20))})
    import math

    want = math.pi * 4 ** 2 * 20                                           # furo passante D8 em 20 mm
    state = {"removed": want}

    def fake_run_plan(plan, runner, verify=True):
        f.parts["/placa"] = ((0, 0, 0), (60, 40, 20))
        return {"ok": True, "steps": [{"ok": True}], "problems": []}

    vols = iter([{"/placa": {"volume_mm3": 48000.0, "area_mm2": 1.0}}, {"/placa": {"volume_mm3": 48000.0 - state["removed"], "area_mm2": 1.0}}])
    monkeypatch.setattr(builder, "run_plan", fake_run_plan)
    monkeypatch.setattr(actions, "read_volumes", lambda env, paths: (next(vols), {"ok": True}))
    r = actions.holes(f.env(), "placa", [{"kind": "through", "d": 8, "at": [30, 20]}], "top", True, None, 3.0)
    assert r["ok"] and abs(r["volume"]["error_pct"]) < 0.5, r
    # volume removido diferente do esperado (>3%) -> falha com mensagem
    vols2 = iter([{"/placa": {"volume_mm3": 48000.0, "area_mm2": 1.0}}, {"/placa": {"volume_mm3": 47900.0, "area_mm2": 1.0}}])
    monkeypatch.setattr(actions, "read_volumes", lambda env, paths: (next(vols2), {"ok": True}))
    bad = actions.holes(f.env(), "placa", [{"kind": "through", "d": 8, "at": [30, 20]}], "top", True, None, 3.0)
    assert bad["ok"] is False and "difere do esperado" in bad["error"]
    assert actions.holes(f.env(), "/nao", [{"kind": "through", "d": 8, "at": [1, 1]}])["ok"] is False
    assert actions.holes(f.env(), "placa", [{"kind": "blind", "d": 8, "at": [1, 1]}], "top", True)["ok"] is False   # cego sem depth


def test_run_built_budget_registry_and_bom(iso, monkeypatch):
    from creo_mcp import builder

    built = library.component("fastener", {"size": 8, "grip": 20, "name": "fx"})
    f = FakeCreo({})
    names = ["/" + n for n in built.plan.parts()]

    def fake_run_plan(plan, runner, verify=True):
        for n in names:
            f.parts[n] = CUBE
        return {"ok": True, "steps": [{"ok": True}] * 3, "problems": [], "bbox": [[0, 0, 0], [1, 1, 1]]}

    monkeypatch.setattr(builder, "run_plan", fake_run_plan)
    monkeypatch.setattr(builder, "top_level_paths", lambda runner: f.top_names())
    r = actions.run_built(f.env(), built, True, "creo_fasteners")
    assert r["ok"] and set(r["parts_created"]) == set(names) and set(names) <= set(registry.known())
    assert r["bom"] and actions._bom_load()
    # orcamento: 60 pecas ja usadas -> recusa antes de enviar qualquer coisa
    full = FakeCreo({f"/q{i}": CUBE for i in range(58)})
    refused = actions.run_built(full.env(), built, True, "creo_fasteners")
    assert refused["ok"] is False and "limite" in refused["error"] and refused["budget"]["left"] < 0


def test_sweep_frame_is_orthonormal_and_normal_along_spine():
    org, pu, pv = actions.sweep_frame([0, 0, 0], [0, 0, 100])
    u = [pu[i] - org[i] for i in range(3)]
    v = [pv[i] - org[i] for i in range(3)]
    assert sum(a * b for a, b in zip(u, v)) == pytest.approx(0, abs=1e-9)
    cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    assert cross == pytest.approx([0, 0, 1], abs=1e-9)
    with pytest.raises(PartError):
        actions.sweep_frame([1, 1, 1], [1, 1, 1])


# ------------------------------------------------------------------ biblioteca
def test_library_components_and_assembly_spec():
    kinds = library.kinds()
    assert "flange" in kinds["catalog"] and "fastener" in kinds["library"]
    b = library.component("fastener", {"size": 10, "grip": 25, "positions": [[0, 0], [50, 0]]})
    assert len(b.plan.parts()) == 8 and any(x["qty"] == 2 for x in b.bom)
    with pytest.raises(PartError):
        library.component("fastener", {"tamanho": 10})
    with pytest.raises(PartError):
        library.component("desconhecido", {})
    spec = {"name": "conj", "items": [
        {"component": "profile", "name": "viga", "params": {"profile": "SHS40x3", "length": 300}, "origin": [0, 0, 0]},
        {"component": "fastener", "name": "p", "params": {"size": 8, "grip": 10},
         "pattern": {"kind": "linear", "count": 3, "dx": 50}}]}
    a = library.assemble(spec)
    assert len(a.plan.parts()) == 1 + 3 * 4 and a.info["parts"] == 13
    with pytest.raises(PartError):
        library.assemble({"items": []})
    with pytest.raises(PartError):
        library.assemble({"items": [{"component": "profile", "name": "x", "params": {"profile": "SHS40x3", "length": 100}},
                                    {"component": "profile", "name": "x", "params": {"profile": "SHS40x3", "length": 100}}]})
    big = {"items": [{"component": "profile", "name": "b", "params": {"profile": "SHS40x3", "length": 100},
                      "pattern": {"kind": "linear", "count": 61, "dx": 50}}]}
    with pytest.raises(PartError):
        library.assemble(big)


def test_library_frame_pipe_variants():
    fr = library.frame(None, {"length": 1000, "width": 600, "height": 500})
    assert len(fr.plan.parts()) == 12 and sum(x["qty"] for x in fr.bom) == 12
    pp = library.pipe([[0, 0, 0], [0, 0, 400], [300, 0, 400]], 48.3, 3.2)
    assert pp.info["legs"] == 2 and pp.info["bends_modeled"] is False
    with pytest.raises(PartError):
        library.pipe([[0, 0, 0], [10, 10, 0]], 48, 3)                         # trecho diagonal
    with pytest.raises(PartError):
        library.pipe([[0, 0, 0]], 48, 3)
    vp = library.variant_params({"od": 100}, {"bolts": [4, 6], "pcd": [80, 90]}, "grid")
    assert len(vp) == 4 and vp[0] == {"od": 100, "bolts": 4, "pcd": 80}
    assert len(library.variant_params({}, {"a": [1, 2], "b": [3, 4]}, "zip")) == 2
    with pytest.raises(PartError):
        library.variant_params({}, {"a": [1, 2], "b": [3]}, "zip")
    with pytest.raises(PartError):
        library.variant_params({}, {"a": list(range(30))}, "grid")


def test_fits_iso286():
    r = fits.fit(25, "H7/g6")
    assert r["kind"] == "folga" and r["hole"]["upper_mm"] == pytest.approx(0.021) and r["clearance_min_mm"] == pytest.approx(0.007)
    assert fits.fit(25, "H7/p6")["kind"] == "interferencia"
    assert fits.fit(25, "H7/k6")["kind"].startswith("incerto")
    with pytest.raises(PartError):
        fits.fit(25, "g6/H7")


# ------------------------------------------------------------------ confianca: saude, trava, diario, capacidades
def test_health_states(iso):
    assert health.status()["state"] == "ok" and health.blocks_modeling() is None
    health.observe_pids([10])
    health.observe_pids([11])                                  # pid mudou: Creo reiniciado, cena perdida
    assert health.status()["state"] == "restarted" and health.blocks_modeling() is None
    health.acknowledge()
    assert health.status()["state"] == "ok"
    health.stl_failure("x", "/a")
    health.stl_failure("x", "/a")
    assert health.status()["state"] == "degraded" and "STL" in health.blocks_modeling()
    health.stl_success()
    assert health.blocks_modeling() is None
    health.observe_pids([])
    assert health.status()["state"] == "down" and health.blocks_modeling()


def test_lease_between_clients(iso, monkeypatch):
    monkeypatch.setattr(lease, "pid_alive", lambda pid: True)
    ok, info = lease.ensure(client="A")
    assert ok and info["client"] == "A"
    # outro processo vivo segura a trava
    now = time.time()
    json.dump({"pid": os.getpid() + 1, "client": "Antigravity", "started": now, "last": now}, open(lease.PATH, "w"))
    ok, holder = lease.ensure(client="B")
    assert not ok and holder["client"] == "Antigravity"
    ref = lease.refusal(holder)
    assert ref["ok"] is False and "Antigravity" in ref["error"] and "creo_lease" in ref["error"]
    # dono morto: a trava passa para quem chegou
    monkeypatch.setattr(lease, "pid_alive", lambda pid: False)
    assert lease.ensure(client="B")[0] is True
    monkeypatch.setattr(lease, "pid_alive", lambda pid: True)
    assert lease.take("C")["taken"] and lease.status()["holder"]["client"] == "C" and lease.status()["mine"]
    assert lease.release()["released"] is True


def test_journal_and_replay(iso):
    journal.record("creo_holes", {"part": "/a"}, None, True, "ok")
    journal.record("creo_holes", {"part": "/b"}, None, False, "falhou")
    journal.record("creo_clear_scene", {}, None, True, "")
    journal.record("creo_fit", {"size": 1}, None, True, "")                 # leitura: nao entra
    journal.record("creo_execute_lisp", {"code": "(+ 1 2)"}, "(+ 1 2)", True, "")
    rows = journal.entries(0, None, 1)
    assert [r["tool"] for r in rows] == ["creo_holes", "creo_holes", "creo_clear_scene", "creo_execute_lisp"]
    steps = journal.build_replay([r for r in rows if r["ok"]], include_raw_lisp=False)
    assert [s["tool"] for s in steps] == ["creo_holes"]
    seen = []
    res = journal.run_replay([{"tool": "creo_holes", "args": {"part": "/a"}}, {"tool": "x", "args": {}}],
                             {"creo_holes": lambda **k: seen.append(k) or {"ok": True}}, True)
    assert seen == [{"part": "/a"}] and res["ok"] is False and res["results"][1]["error"] == "ferramenta nao repetivel"


def test_capabilities_parse_units_and_status(iso):
    text = ('(:LISP "1.0" :UNITS (("length" "mm") ("mass" "g") ("angle" :NO-FUNCTION)) :EXPIRY 5 '
            ':MODULES (("STEP" T NIL)) :COMMANDS (("EXTRUDE" T) ("STEP_EXPORT" NIL) ("CLASH_3D" T)))')
    p = capabilities.parse(text)
    assert p["units"]["length"] == "mm" and p["unit_warnings"] == []
    assert p["commands"]["CLASH_3D"] and "STEP_EXPORT" in p["missing_optional"]
    assert capabilities.unit_warnings({"length": "in", "mass": "kg"}) and capabilities.unit_warnings({})
    d = capabilities.describe(p, ["creo_get_status", "creo_holes"])
    assert d["tools"] == {"creo_get_status": "verified", "creo_holes": "experimental"}
    assert d["can_export"]["clash_3d"] is True and d["can_export"]["step"] is False
    capabilities.save_validation({"tools": {"creo_holes": {"ok": True}}, "when": "x"})
    assert capabilities.tool_status(["creo_holes"])["creo_holes"] == "verified"


def test_actions_units_report_and_capabilities_with_fake(iso):
    def run(code, timeout=30, *a):
        return {"ok": True, "result": '(:LISP "1.0" :UNITS (("length" "in") ("mass" "g")))', "dialogs": [], "error": None}

    env = actions.Env(run, lambda *a, **k: [], sleep=lambda s: None)
    r = actions.units_report(env)
    assert r["ok"] is False and "mm" in r["advice"] and r["warnings"]
    cap = actions.capabilities_report(env, ["creo_get_status"], True)
    assert cap["ok"] and cap["probe"]["units"]["length"] == "in"
    bad = actions.capabilities_report(actions.Env(lambda *a, **k: {"ok": False, "error": "x", "dialogs": []}, lambda *a, **k: []),
                                      ["creo_get_status"], True)
    assert "probe_error" in bad


def test_health_report_includes_policy(iso):
    r = actions.health_report(actions.Env(lambda *a, **k: {}, lambda *a, **k: [], pids=lambda: [5]))
    assert r["health"]["state"] == "ok" and r["policy"]["lease"] in (True, False) and "server_version" in r


def test_config_flags_and_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("CREO_MCP_SAFE_MODE", "1")
    assert config.safe_mode() and not config.free_lisp_allowed() and not config.dde_raw_allowed()
    monkeypatch.setenv("CREO_MCP_SAFE_MODE", "0")
    monkeypatch.setenv("CREO_MCP_ALLOW_LISP", "0")
    assert not config.free_lisp_allowed()
    monkeypatch.delenv("CREO_MCP_ALLOW_LISP")
    assert config.free_lisp_allowed() and not config.dde_raw_allowed()
    monkeypatch.setenv("CREO_MCP_ALLOW_DDE_RAW", "sim")
    assert config.dde_raw_allowed()
    monkeypatch.setenv("CREO_MCP_EXPORT_ROOTS", str(tmp_path))
    assert config.path_allowed(str(tmp_path / "a" / "b.stl")) and not config.path_allowed(str(tmp_path / ".." / "x"))
    assert config.has_dot_component("C:/Users/x/.creo_mcp/a.pk2") and not config.has_dot_component("C:/Users/x/snap/a.pk2")
    monkeypatch.setenv("CREO_MCP_HOT_RELOAD", "0")
    assert config.hot_reload() is False


# ------------------------------------------------------------------ snapshot
def test_snapshot_paths_listing_prune_and_verify(iso, tmp_path):
    assert snapshot.plain_path_problem("C:/Users/x/.creo_mcp/a.pk2")
    assert snapshot.plain_path_problem("C:/snaps/a.pkg")
    assert snapshot.plain_path_problem("C:/snaps/a.pk2") is None
    d = str(tmp_path / "s")
    os.makedirs(d)
    for i in range(5):
        p = os.path.join(d, f"auto_{i}.pk2")
        open(p, "wb").write(b"x")
        json.dump({"label": "a", "auto": True, "created": i}, open(os.path.splitext(p)[0] + ".json", "w"))
    open(os.path.join(d, "manual.pk2"), "wb").write(b"x")
    names = [s["name"] for s in snapshot.list_snapshots(d)]
    assert "manual.pk2" in names and len(names) == 6
    removed = snapshot.prune(d, 2)
    assert len(removed) == 3 and "manual.pk2" in [s["name"] for s in snapshot.list_snapshots(d)]
    assert snapshot.resolve("manual", d).endswith("manual.pk2")
    tree = lisp.parse_sexp('(("/w1" :WP NIL) ("/a" :PART NIL))')
    ok = snapshot.verify_against(tree, {"parts": ["/a"], "top_level": ["/w1", "/a"]})
    assert ok["ok"]
    bad = snapshot.verify_against(tree, {"parts": ["/a", "/b"], "top_level": ["/w1", "/a", "/b"]})
    assert not bad["ok"] and bad["missing_parts"] == ["/b"]
    cmd = snapshot.load_command("C:/snaps/a.pk2")
    assert cmd.startswith("LOAD_PACKAGE") and "COMPLETE" in cmd


def test_snapshot_restore_refuses_over_existing_parts(iso, tmp_path):
    d = str(tmp_path / "snaps")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "a.pk2"), "wb").write(b"x")
    f = FakeCreo({"/p": CUBE})
    r = snapshot.restore(f.run, f.settle, "", d, False)
    assert r["ok"] is False and "replace" in r["error"] and f.parts == {"/p": CUBE}
    assert snapshot.restore(f.run, f.settle, "zzz", d)["ok"] is False


def test_snapshot_restore_flow_with_fake_creo(iso, tmp_path, monkeypatch):
    d = str(tmp_path / "snaps")
    os.makedirs(d, exist_ok=True)
    pk = os.path.join(d, "estado.pk2")
    open(pk, "wb").write(b"x")
    json.dump({"parts": ["/p1", "/p2"], "top_level": ["/w1", "/p1", "/p2"]}, open(os.path.join(d, "estado.json"), "w"))
    f = FakeCreo({})

    def run(code, timeout=30, *a):
        if code.startswith("(oli:sd-put-buffer") and "LOAD_PACKAGE" in code:
            f.parts.update({"/p1": CUBE, "/p2": CUBE})
            return {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        return f.run(code, timeout)

    monkeypatch.setattr(health, "acknowledge", health.acknowledge)
    r = snapshot.restore(run, f.settle, "estado", d, False, wait_s=4, poll_s=1, sleep=lambda s: None)
    assert r["ok"] and r["verify"]["ok"] and set(f.parts) == {"/p1", "/p2"}, r


# ------------------------------------------------------------------ ferramentas MCP (servidor)
def _server():
    import creo_mcp.server as srv

    return srv


def _call(name, args=None):
    srv = _server()

    async def go():
        r = await srv.server.call_tool(name, args or {})
        sc = getattr(r, "structured_content", None)
        if sc is None and isinstance(r, tuple):
            sc = r[1]
        return sc

    return asyncio.run(go())


def test_server_registers_every_new_tool():
    srv = _server()
    names = {t.name for t in asyncio.run(srv.server.list_tools())}
    expected = {"creo_snapshot", "creo_restore", "creo_capabilities", "creo_part_budget", "creo_clear_scene", "creo_lease",
                "creo_health", "creo_units", "creo_audit_scene", "creo_measure", "creo_clash_check", "creo_mesh_qa",
                "creo_inspect", "creo_export", "creo_import", "creo_set_attributes", "creo_bom", "creo_views", "creo_report",
                "creo_fit", "creo_transform", "creo_group", "creo_color", "creo_mirror", "creo_section", "creo_holes",
                "creo_pattern", "creo_profile_std", "creo_frame", "creo_pipe", "creo_fasteners", "creo_bearing_std",
                "creo_transmission", "creo_sweep_path", "creo_build_assembly", "creo_variants", "creo_journal", "creo_replay",
                "creo_batch", "creo_job_cancel", "creo_validate"}
    assert expected <= names, expected - names
    legacy = set(srv._api.base_tools)
    assert legacy <= names, legacy - names
    resources = {str(r.uri) for r in asyncio.run(srv.server.list_resources())}
    assert {"creo://catalog", "creo://limits", "creo://snapshots", "creo://journal/today"} <= resources
    prompts = {p.name for p in asyncio.run(srv.server.list_prompts())}
    assert {"industrial_part", "assembly_from_spec", "safe_cleanup"} <= prompts


def test_new_tools_return_structured_output(iso):
    r = _call("creo_fit", {"size": 25, "designation": "H7/g6"})
    assert r["ok"] and r["fit"] == "H7/g6"
    r = _call("creo_pattern", {"component": "points", "pattern": json.dumps({"kind": "circular", "count": 4, "pcd": 100})})
    assert r["count"] == 4
    r = _call("creo_fasteners", {"size": 10, "grip": 25, "positions": "[[0,0],[50,0]]", "dry_run": True})
    assert r["dry_run"] and r["parts_count"] == 8 and r["bom"]
    r = _call("creo_build_assembly", {"spec": json.dumps({"name": "c", "items": [{"component": "flange", "name": "f"}]}), "dry_run": True})
    assert r["dry_run"] and r["parts"] == ["f"]
    r = _call("creo_build_assembly", {"spec": "{ruim", "dry_run": True})
    assert r["ok"] is False and "JSON" in r["error"]
    r = _call("creo_profile_std", {"profile": "list"})
    assert r["ok"]
    r = _call("creo_profile_std", {"profile": "XYZ999", "dry_run": True})
    assert r["ok"] is False
    r = _call("creo_holes", {"part": "/a", "holes": "nao-json"})
    assert r["ok"] is False and "JSON" in r["error"]


def test_set_attributes_tool_and_journal(iso):
    r = _call("creo_set_attributes", {"part": "/a", "attributes": json.dumps({"material": "steel"})})
    assert r["ok"] and r["attributes"] == {"material": "steel"}
    assert _call("creo_set_attributes", {"part": "/a", "action": "get"})["attributes"] == {"material": "steel"}
    assert _call("creo_set_attributes", {"action": "set"})["ok"] is False
    j = _call("creo_journal", {"limit": 10})
    assert any(e["tool"] == "creo_set_attributes" and e["ok"] for e in j["entries"])


def test_tool_gate_refuses_when_another_client_holds_the_lease(iso, monkeypatch):
    monkeypatch.setattr(lease, "pid_alive", lambda pid: True)
    now = time.time()
    json.dump({"pid": os.getpid() + 7, "client": "Antigravity", "started": now, "last": now}, open(lease.PATH, "w"))
    r = _call("creo_part_budget", {})
    assert r["ok"] is False and "Antigravity" in r["error"]
    r = _call("creo_create_block", {"width": 1, "depth": 1, "height": 1})
    assert "Antigravity" in json.loads(r) ["error"] if isinstance(r, str) else True


def test_tool_gate_blocks_modeling_when_degraded(iso):
    health.stl_failure("x")
    health.stl_failure("x")
    r = _call("creo_holes", {"part": "/a", "holes": json.dumps([{"kind": "through", "d": 5, "at": [1, 1]}])})
    assert r["ok"] is False and "degraded" in r["error"]


def test_safe_mode_and_dde_raw_off_by_default(iso, monkeypatch):
    srv = _server()
    monkeypatch.setenv("CREO_MCP_SAFE_MODE", "1")
    out = json.loads(srv.creo_execute_lisp("(+ 1 2)"))
    assert out["ok"] is False and "desligado" in out["error"]
    assert json.loads(srv.creo_run_command("STL COMPLETE"))["ok"] is False
    monkeypatch.delenv("CREO_MCP_SAFE_MODE")
    raw = json.loads(srv.creo_dde_raw("(+ 1 2)"))
    assert raw["ok"] is False and "desligada" in raw["error"]


def test_export_stl_tool_limits_folder_and_parts(iso, tmp_path):
    srv = _server()
    out = json.loads(srv.creo_export_stl(str(tmp_path / "x" / "a.stl"), "/p1"))
    assert out["ok"] is False and "pasta fora" in out["error"]
    assert json.loads(srv.creo_export_stl(os.path.join(config.HOME, "a.txt"), "/p1"))["ok"] is False
    multi = json.loads(srv.creo_export_stl(os.path.join(config.HOME, "a.stl"), "/p1,/p2"))
    assert multi["ok"] is False and "uma peca" in multi["error"]


def test_job_cancel_stops_at_next_creo_call(iso, monkeypatch):
    srv = _server()
    calls = {"n": 0}

    def fake_run(code, timeout=30.0, auto_dismiss=True):
        calls["n"] += 1
        time.sleep(0.05)
        return {"ok": True, "result": "1", "error": None, "dialogs": []}

    monkeypatch.setattr(srv.lisp, "run", fake_run)
    monkeypatch.setattr(srv, "_settle", lambda *a, **k: [])
    monkeypatch.setattr(srv, "JOB_WAIT", 0.1)
    monkeypatch.setattr(srv.config, "hot_reload", lambda: False)

    def long_job():
        for _ in range(500):
            srv._run("(+ 1 2)")
        return {"ok": True}

    first = srv._start_job("longo", long_job)
    assert first["state"] == "running"
    res = srv._cancel_job(first["job"])
    assert res["ok"] and res["state"] == "cancelled", res
    final = json.loads(srv.creo_job_status(first["job"], 1))
    assert final["ok"] is False and final["cancelled"] is True and calls["n"] < 500
    assert srv._cancel_job("")["ok"] is False                      # nenhum job em andamento


def test_nested_job_runs_inline(iso, monkeypatch):
    srv = _server()
    monkeypatch.setattr(srv, "JOB_WAIT", 5.0)
    out = srv._start_job("externo", lambda: srv._start_job("interno", lambda: {"ok": True, "n": 1}))
    assert out == {"ok": True, "n": 1}


def test_legacy_tools_are_journaled_and_dispatchable(iso, monkeypatch):
    srv = _server()
    monkeypatch.setattr(srv.lisp, "run", lambda *a, **k: {"ok": True, "result": "(\"/w1\")", "error": None, "dialogs": []})
    monkeypatch.setattr(srv.config, "hot_reload", lambda: False)
    out = json.loads(srv.creo_create_block(10, 10, 10))
    assert out["ok"]
    rows = journal.entries(0, None, 1)
    assert rows and rows[-1]["tool"] == "creo_create_block" and rows[-1]["args"]["width"] == 10
    assert "creo_create_block" in srv.LEGACY


def test_batch_validation_and_dry_run_steps(iso):
    r = _call("creo_batch", {"steps": "[]"})
    assert r["ok"] is False
    r = _call("creo_batch", {"steps": json.dumps([{"tool": "creo_restore", "args": {}}])})
    assert r["ok"] is False and "nao e permitida" in r["error"]
    r = _call("creo_batch", {"steps": json.dumps([{"tool": "creo_nao_existe"}])})
    assert r["ok"] is False and "desconhecida" in r["error"]


def test_replay_plan_lists_typed_calls(iso):
    journal.record("creo_holes", {"part": "/a", "holes": "[]"}, None, True, "")
    journal.record("creo_fit", {"size": 1}, None, True, "")
    r = _call("creo_replay", {"action": "plan"})
    assert r["ok"] and [s["tool"] for s in r["steps"]] == ["creo_holes"]


def test_sweep_path_is_off_unless_experimental_flag(iso, monkeypatch):
    monkeypatch.delenv("CREO_MCP_EXPERIMENTAL", raising=False)
    r = _call("creo_sweep_path", {"part": "/a", "profile": '[["c",0,0,5]]', "spine": '["edge",0,0,0,0,0,10]'})
    assert r["ok"] is False and r["experimental"] is True


def test_exchange_plist_introspection_is_disabled():
    with pytest.raises(PartError):
        exchange.plist_code("EXTRUDE")


def test_hot_reload_list_matches_real_modules():
    srv = _server()
    here = os.path.dirname(os.path.abspath(srv.__file__))
    for name in srv.HOT_MODULES:
        assert os.path.exists(os.path.join(here, name + ".py")), name
    # modulos com estado em arquivo/processo nao podem ser recarregados (perderiam o estado)
    assert not {"health", "journal", "lease", "registry", "state", "config"} & set(srv.HOT_MODULES)


def test_tools_ext_helpers():
    assert tools_ext.jparse("", "x", 5) == 5 and tools_ext.jparse("[1]", "x") == [1] and tools_ext.jparse([2], "x") == [2]
    with pytest.raises(ValueError):
        tools_ext.jparse("{", "campo")
    assert tools_ext.vec3("[1,2,3]", "p") == [1.0, 2.0, 3.0] and tools_ext.vec3("", "p") is None
    with pytest.raises(ValueError):
        tools_ext.vec3("[1,2]", "p")
    assert tools_ext.parse_spec('{"a": 1}') == {"a": 1}
    try:
        import yaml  # noqa: F401
    except ImportError:                                   # YAML e opcional: sem PyYAML, so JSON, com mensagem clara
        with pytest.raises(ValueError, match="PyYAML"):
            tools_ext.parse_spec("a: 1")
    else:
        assert tools_ext.parse_spec("a: 1\nb: [1, 2]") == {"a": 1, "b": [1, 2]}
    with pytest.raises(ValueError):
        tools_ext.parse_spec("")
    assert actions.norm_list('["a", "/b", "a"]') == ["/a", "/b"] and actions.norm_list("a, b") == ["/a", "/b"]
    assert actions.norm_list(None) == [] and actions.norm_list("") == []


# ------------------------------------------------------------------ validacao ao vivo (so a parte offline)
def test_validate_cleanup_list_and_promotion(iso):
    tree = lisp.parse_sexp('(("/w1" :WP NIL) ("/mcp_exp_a" :PART NIL) ("/mcp_exp_grp" :ASSEMBLY (("/mcp_exp_grp/x" :PART NIL))) ("/p1" :PART NIL))')
    assert validate.cleanup_code(tree) == ["/mcp_exp_a", "/mcp_exp_grp"]
    validate.promote([{"check": "holes", "tools": ["creo_holes"], "ok": True},
                      {"check": "mirror", "tools": ["creo_mirror"], "ok": False, "error": "x"}])
    st = capabilities.tool_status(["creo_holes", "creo_mirror"])
    assert st == {"creo_holes": "verified", "creo_mirror": "experimental"}


def test_validate_refuses_without_budget_or_with_leftovers(iso):
    full = FakeCreo({f"/q{i}": CUBE for i in range(50)})
    r = validate.run(full.env(), "quick", True)
    assert r["ok"] is False and "livres" in r["error"]
    left = FakeCreo({"/mcp_exp_a": CUBE})
    r = validate.run(left.env(), "quick", True)
    assert r["ok"] is False and "anterior" in r["error"]


def test_validate_aborts_if_snapshot_fails(iso, monkeypatch):
    monkeypatch.setattr(snapshot, "create", lambda *a, **k: {"ok": False, "error": "sem disco"})
    f = FakeCreo({"/user": CUBE})
    r = validate.run(f.env(), "quick", True)
    assert r["ok"] is False and "snapshot" in r["error"] and set(f.parts) == {"/user"}


def test_validate_checks_reference_real_tools():
    srv = _server()
    names = {t.name for t in asyncio.run(srv.server.list_tools())}
    for check, tools, fn, scope in validate.CHECKS:
        for t in tools:
            assert t.split(":")[0] in names, (check, t)
        assert scope in ("quick", "full") and callable(fn)


def test_boxes_are_read_one_part_per_call_and_validate_scope_hides_user_parts(iso, monkeypatch):
    # a leitura em lote (mapcar + ignore-errors) derrubou o Creo: agora e uma peca por chamada, na forma verificada
    f = FakeCreo({f"/p{i}": CUBE for i in range(40)})
    seen = []
    orig = f.run

    def spy(code, timeout=30, *a):
        if "sd-inq-exact-box" in code:
            seen.append(code)
        return orig(code, timeout)

    env = actions.Env(spy, f.settle, sleep=lambda s: None)
    boxes = actions.read_boxes(env, list(f.parts) + ["/ausente"])
    assert len(boxes) == 41 and boxes["/ausente"] is None and boxes["/p3"] == CUBE
    assert len(seen) == 41 and all("mapcar" not in c and "ignore-errors" not in c for c in seen)
    with pytest.raises(ValueError):
        analysis.boxes_code(["/a", "/b"])
    # no modo de validacao so as pecas /mcp_exp_* existem para as ferramentas
    f2 = FakeCreo({"/user1": CUBE, "/user2": CUBE, "/mcp_exp_a": CUBE})
    monkeypatch.setattr(actions, "SCOPE_PREFIX", "/mcp_exp_")
    assert actions._all_parts(actions.read_tree(f2.env())) == ["/mcp_exp_a"]
    seen.clear()
    spy_env = actions.Env(lambda code, timeout=30, *a: (seen.append(code) or f2.run(code, timeout)), f2.settle, sleep=lambda s: None)
    actions.audit(spy_env, False)
    assert not any('"/user1"' in c for c in seen if "sd-inq-exact-box" in c)


def _fan_box(x0, y0, z0, x1, y1, z1):
    """Cubo triangulado como o Creo exporta: cada face em leque de 4 triangulos em torno do centro da face."""
    tris = []
    def face(c, corners):
        for i in range(4):
            tris.append((c, corners[i], corners[(i + 1) % 4]))
    X, Y, Z = (x0, x1), (y0, y1), (z0, z1)
    for x in X:
        face((x, (y0 + y1) / 2, (z0 + z1) / 2), [(x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)])
    for y in Y:
        face(((x0 + x1) / 2, y, (z0 + z1) / 2), [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)])
    for z in Z:
        face(((x0 + x1) / 2, (y0 + y1) / 2, z), [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
    return tris


def test_mesh_interference_fan_triangulated_cubes_with_coincident_vertices():
    # caso real da validacao ao vivo: cubos 40 com deslocamento (20,20,0) e mesma altura, centros de face coincidentes
    a = _fan_box(0, 0, 0, 40, 40, 40)
    assert mesh.interference(a, _fan_box(20, 20, 0, 60, 60, 40))["intersects"] is True
    # encostados por uma face e afastados: nao interferem
    assert mesh.interference(a, _fan_box(40, 0, 0, 80, 40, 40))["intersects"] is False
    assert mesh.interference(a, _fan_box(100, 0, 0, 140, 40, 40))["intersects"] is False


def test_volume_read_makes_the_part_current_and_rejects_a_stale_current_part():
    # ao vivo: GET_VOL_PROP ignora :for_part e mede a peca CORRENTE
    code = analysis.vol_setup(["/a"])
    assert '(current_part "/a")' in code
    with pytest.raises(ValueError):
        analysis.vol_setup(["/a", "/b"])
    ok = analysis.parse_vol(lisp.parse_sexp('(("/a" 1000.0 600.0 "/a"))'))
    assert ok["/a"] == {"volume_mm3": 1000.0, "area_mm2": 600.0}
    stale = analysis.parse_vol(lisp.parse_sexp('(("/a" 64000.0 9600.0 "/c3"))'))
    assert stale["/a"] is None
