"""Testes offline (sem Creo): geradores de Lisp, escape, PNG e registro das ferramentas."""

import asyncio
import struct
import zlib

from creo_mcp import lisp, recipes, winui


def test_lisp_str_escapes():
    assert lisp.lisp_str('a"b\\c') == '"a\\"b\\\\c"'


def test_lisp_path_uses_forward_slashes():
    assert lisp.lisp_path("C:\\Users\\x\\a.stl") == '"C:/Users/x/a.stl"'


def test_num_format():
    assert lisp.num(10) == "10"
    assert lisp.num(2.5) == "2.5"
    assert lisp.num(-0.0) == "0"
    assert "e" not in lisp.num(0.00000001).lower()


def test_cmd_file_wraps_code_and_writes_marker():
    body = lisp.build_cmd_file("(+ 1 2) ; comentario", "C:\\x\\o.out")
    assert body.startswith("(let ((mcp_val (progn\n(+ 1 2) ; comentario\n)))")
    assert '"C:/x/o.out"' in body and "#END#" in body
    assert "\r" not in body


def test_read_out_needs_marker(tmp_path):
    f = tmp_path / "r.out"
    f.write_bytes(b"42\n")
    assert lisp._read_out(str(f)) is None
    f.write_bytes(b"42\n#END#\n")
    assert lisp._read_out(str(f)) == "42"
    assert lisp._read_out(str(tmp_path / "nao_existe.out")) is None


def test_block_recipe():
    code = recipes.block(0, 0, 50, 30, 20, part="/p1", wp="/w1")
    assert "(rectangle 0,0 50,30)" in code
    assert '(extrude :part "/p1" :wp "/w1" :distance 20 :keep_profile :no)' in code


def test_cylinder_recipe():
    code = recipes.cylinder(5, 5, 10, 40, part="/p1", wp="/w1")
    assert "(circle :cen_rad 5,5 10)" in code
    assert ":distance 40" in code


def test_ensure_part_for_tool_parts():
    code = recipes.cylinder(30, 20, 8, 40, part="/tool")
    assert '(unless (oli:sd-pathname-to-obj "/tool") (create_part :name "tool"))' in code
    assert code.index("create_part") < code.index("(circle")
    assert ":keep_profile :no" in code
    assert "create_part" not in recipes.cylinder(0, 0, 1, 1, part="/a/b")  # so pecas de nivel raiz


def test_boolean_recipes():
    unite = recipes.boolean("unite", "/a", "/b")
    assert '(unite_3d :blank "/a" :tools "/b" :keep_tools :no)' in unite
    # blindagem (2026-10-19): peca inexistente volta :missing-part em vez de abrir dialogo de erro no Creo
    assert unite.startswith('(if (and (oli:sd-pathname-to-obj "/a")') and unite.rstrip().endswith(":missing-part)")
    assert ':keep_tool :yes' in recipes.boolean("subtract", "/a", "/b", True)
    assert "intersect_3d" in recipes.boolean("intersect", "/a", "/b")




def test_png_is_valid():
    data = winui._png(2, 2, bytes([0, 0, 255, 255] * 4))  # BGRA vermelho
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    idat = data[data.index(b"IDAT") + 4:]
    length = struct.unpack(">I", data[data.index(b"IDAT") - 4:data.index(b"IDAT")])[0]
    raw = zlib.decompress(idat[:length])
    assert raw == (b"\x00" + b"\xff\x00\x00" * 2) * 2


def test_server_registers_tools():
    from creo_mcp.server import server

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"creo_get_status", "creo_execute_lisp", "creo_self_test", "creo_lisp_apropos",
            "creo_dismiss_dialogs", "creo_screenshot", "creo_create_block", "creo_create_cylinder",
            "creo_boolean", "creo_export_stl", "creo_run_command", "creo_cancel_command",
            "creo_get_model_tree", "creo_get_part_info", "creo_build_part", "creo_extrude_profile",
            "creo_clear_scene", "creo_job_status", "creo_edge_treatment", "creo_shell"} <= names


def test_run_without_creo_returns_error_not_exception():
    res = lisp.run("(+ 1 2)", timeout=2)
    assert res["ok"] is False and res["error"]


def test_run_flow_with_fake_creo(monkeypatch, tmp_path):
    """Simula o Creo: o 'load' grava o .out; run() deve ler o resultado e limpar os arquivos."""
    import re

    monkeypatch.setattr(lisp, "CMD_DIR", str(tmp_path))

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, command, timeout_ms=0):
            assert "\n" not in command and command.startswith("(load ")
            cmd_path = re.search(r'"(.+)"', command).group(1)
            body = open(cmd_path, encoding="utf-8").read()
            out_path = re.search(r':direction :output|\(mcp_s "([^"]+)"', body).group(1)
            with open(out_path, "w") as fh:
                fh.write("3\n#END#\n")

    monkeypatch.setattr(lisp.dde, "DdeClient", FakeClient)
    res = lisp.run("(+ 1 2)", timeout=5)
    assert res["ok"] is True and res["result"] == "3"
    assert list(tmp_path.iterdir()) == []


def test_run_reports_error_when_creo_never_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(lisp, "CMD_DIR", str(tmp_path))

    class SilentClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, command, timeout_ms=0):
            pass

    monkeypatch.setattr(lisp.dde, "DdeClient", SilentClient)
    res = lisp.run("(+ 1 2)", timeout=0.5)
    assert res["ok"] is False and "resultado nao apareceu" in res["error"]


def test_summarize_dialogs_groups_duplicates():
    d = {"title": "Error", "text": "Icon\nStatic\nYou must specify a part.  (Error 414)", "action": "clicou 'Ok'",
         "controls": ["x"] * 20}
    out = lisp.summarize_dialogs([d, dict(d), dict(d)])
    assert out == [{"title": "Error", "text": "You must specify a part. (Error 414)", "count": 3,
                    "action": "clicou 'Ok'"}]


def test_parse_sexp_tree_and_points():
    tree = lisp.parse_sexp('(("/w1" :WP NIL) ("/p1" :PART (("/p1/f" :OTHER NIL))))')
    assert tree[0] == ["/w1", ":WP", None]
    assert tree[1][2][0][0] == "/p1/f"
    box = lisp.parse_sexp("(0.0,0.0,0.0\n 50.0,30.0,20.0)")
    assert box == [[0.0, 0.0, 0.0], [50.0, 30.0, 20.0]]
    assert lisp.parse_sexp('"a \\"q\\" b"') == 'a "q" b'
    assert lisp.parse_sexp("()") == []
    assert lisp.parse_sexp("(3 T NIL)") == [3, True, None]


def test_new_recipes():
    assert recipes.export_stl("C:\\t\\a.stl", "/p1") == (
        '(oli:sd-put-buffer "STL :FILE \\"C:/t/a.stl\\" :OVERWRITE :PARTS_ASSEMBLIES \\"/p1\\" :BINARY COMPLETE")')
    assert '\\"/p1\\" \\"/p1.1\\"' in recipes.export_stl("a.stl", "/p1, /p1.1")
    assert recipes.cancel_command() == '(oli:sd-put-buffer "CANCEL")'
    assert "sd-inq-exact-box" in recipes.part_info("/p1")
    assert "labels" in recipes.model_tree()


def test_dialogs_ignore_non_dialog_windows(monkeypatch):
    """O 'Draw Panel' da extrusao (classe propria) nunca pode ser fechado com WM_CLOSE."""
    main = {"hwnd": 1, "title": "Creo", "class": "Main", "owner": 0, "enabled": False}
    err = {"hwnd": 2, "title": "Error", "class": "#32770", "owner": 1, "enabled": True}
    panel = {"hwnd": 3, "title": "SolidDesigner", "class": "SDPanel", "owner": 1, "enabled": True}
    monkeypatch.setattr(winui, "main_window", lambda windows: main)
    monkeypatch.setattr(winui, "children", lambda hwnd: [])
    assert winui.dialogs([main, err, panel]) == [err]
    assert winui._click_ok(panel).startswith("ignorado")


# ------------------------------------------------------------ v2.2: stl, parts, builder
def _write_box_stl(path, x, y, z):
    """STL binario de um paralelepipedo x*y*z (12 triangulos) com um canto na origem."""
    import struct

    v = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7),
             (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", len(faces)))
        for a, b, c in faces:
            fh.write(struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0))


def test_stl_reader_volume_and_bbox(tmp_path):
    from creo_mcp import stl

    f = tmp_path / "box.stl"
    _write_box_stl(str(f), 2, 3, 4)
    info = stl.read_stl(str(f))
    assert info["triangles"] == 12 and info["volume"] == 24.0 and info["size"] == [2.0, 3.0, 4.0]
    assert info["area"] == 2 * (6 + 8 + 12)


def test_catalog_plans_are_consistent():
    from creo_mcp import parts

    for kind in parts.CATALOG:
        plan = parts.build(kind, {})
        assert plan.steps and plan.expected_volume and plan.expected_volume > 0
        lo, hi = plan.expected_bbox
        assert all(h > l for l, h in zip(lo, hi)), kind
        assert plan.main_part in plan.export_parts


def test_flange_volume_and_validation():
    import math

    from creo_mcp import parts

    p = parts.flange(od=100, bore=20, thickness=10, hub_od=0, hub_height=0, bolts=4, pcd=70, bolt_d=10)
    assert len(p.steps) == 1 and len(p.steps[0].loops) == 2 + 4
    assert abs(p.expected_volume - (math.pi * (50 ** 2 - 10 ** 2 - 4 * 5 ** 2) * 10)) < 1e-6
    with __import__("pytest").raises(parts.PartError):
        parts.flange(od=100, bore=20, bolts=4, pcd=110, bolt_d=10)      # furos fora do disco


def test_gear_outline_is_a_simple_polygon_with_sensible_radii():
    from creo_mcp import parts

    pts, info = parts.involute_gear_outline(2.0, 24)
    assert abs(info["pitch_radius"] - 24) < 1e-9 and abs(info["tip_radius"] - 26) < 1e-9
    radii = [(x * x + y * y) ** 0.5 for x, y in pts]
    assert 21.4 < min(radii) and max(radii) <= 26.0 + 1e-9
    area = parts.poly_area(pts)
    assert 3.14159 * 21.5 ** 2 < area < 3.14159 * 26 ** 2


def test_hex_dimensions_and_unknown_size():
    from creo_mcp import parts

    plan = parts.hex_nut(12)
    (xmin, ymin, _), (xmax, ymax, zmax) = plan.expected_bbox
    assert abs((ymax - ymin) - 18) < 1e-9 and zmax == 10.8        # chave 18 mm
    with __import__("pytest").raises(parts.PartError):
        parts.hex_bolt(7)


def test_build_rejects_bad_params():
    from creo_mcp import parts

    with __import__("pytest").raises(parts.PartError):
        parts.build("spur_gear", {"modulus": 2})
    with __import__("pytest").raises(parts.PartError):
        parts.build("nao_existe", {})
    assert parts.safe_name("Minha Peça-1", "x") == "minha_pe_a_1"


def test_loop_lisp_and_workplane_recipes():
    assert recipes.loop_lisp(("c", 1, 2, 3)) == "(circle :cen_rad 1,2 3)"
    assert recipes.loop_lisp(("r", 0, 0, 5, 6)) == "(rectangle 0,0 5,6)"
    assert recipes.loop_lisp(("p", [(0, 0), (1, 0), (1, 1)])).endswith(":close)")
    code = recipes.sketch_extrude("/flange", [("c", 0, 0, 5)], 10, "-w", None, 30)
    assert '(current_wp "/wz")' in code and ":direction :-w" in code and ":keep_profile :no" in code
    assert code.index("create_part") < code.index("(circle")
    assert "POSITION_WP \\\"/wz\\\" :PT_PT_PT 0,0,30 1,0,30 0,1,30 COMPLETE" in recipes.position_wp(30)
    assert "subtract_3d" in recipes.cut_with_loops("/eixo", [("r", 1, 1, 2, 2)], 5, 3)


def test_run_plan_with_fake_creo(monkeypatch, tmp_path):
    """Executa uma porca sextavada contra um 'Creo' simulado e confere a autoverificacao por volume/bbox."""
    import re

    from creo_mcp import builder, parts

    plan = parts.hex_nut(12)
    monkeypatch.setattr(builder, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    box = [list(plan.expected_bbox[0]), list(plan.expected_bbox[1])]
    calls = []

    def runner(code, timeout=30):
        calls.append(code)
        ok = {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        if "sd-inq-obj-children" in code and "labels" not in code:
            return {**ok, "result": '("/w1")'}
        if "sd-inq-exact-box" in code:
            return {**ok, "result": f"({box[0][0]},{box[0][1]},{box[0][2]}\n {box[1][0]},{box[1][1]},{box[1][2]})"}
        if "sd-put-buffer" in code:
            path = re.search(r'FILE \\"([^"\\]+)\\"', code).group(1)
            side = plan.expected_volume ** (1 / 3)
            _write_box_stl(path, side, side, side)
        return ok

    rep = builder.run_plan(plan, runner, verify=True)
    assert rep["ok"] is True, rep
    assert rep["checks"]["bbox_ok"] and rep["checks"]["volume_ok"]
    assert any("(polygon" in c for c in calls) and any("(circle" in c for c in calls)


def test_run_plan_aborts_and_cancels_on_creo_error(monkeypatch):
    from creo_mcp import builder, parts

    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    sent = []

    def runner(code, timeout=30):
        sent.append(code)
        if "sd-inq-obj-children" in code:
            return {"ok": True, "result": '("/w1")', "dialogs": [], "error": None}
        if "(extrude" in code:
            return {"ok": False, "result": None, "error": "boom",
                    "dialogs": [{"title": "Error", "text": "Overlapping loops (Error 291)", "count": 1}]}
        return {"ok": True, "result": ":OK", "dialogs": [], "error": None}

    rep = builder.run_plan(parts.hex_nut(12), runner, verify=False)
    assert rep["ok"] is False and "falhou" in rep["problems"][0]
    assert any('"CANCEL"' in c for c in sent)


def test_run_plan_refuses_existing_part():
    from creo_mcp import builder, parts

    rep = builder.run_plan(parts.hex_nut(12), lambda code, t=30: {"ok": True, "result": '("/w1" "/porca")',
                                                                  "dialogs": [], "error": None})
    assert rep["ok"] is False and "ja existe" in rep["problems"][0]


def test_background_job_returns_running_then_final(monkeypatch):
    import time as _t

    from creo_mcp import server

    server._jobs.clear()
    monkeypatch.setattr(server, "JOB_WAIT", 0.05)
    first = server._start_job("lento", lambda: (_t.sleep(0.5), {"ok": True, "valor": 7})[1])
    assert first["state"] == "running" and first["job"]
    # segundo job enquanto o primeiro roda e recusado
    second = server._start_job("outro", lambda: {"ok": True})
    assert second["ok"] is False and "em andamento" in second["error"]
    import json as _j

    final = _j.loads(server.creo_job_status(first["job"], 5))
    assert final["ok"] is True and final["valor"] == 7 and final["job"] == first["job"]
    # depois de terminar, outro job pode comecar
    assert server._start_job("rapido", lambda: {"ok": True, "x": 1})["x"] == 1


def test_background_job_exception_becomes_result(monkeypatch):
    from creo_mcp import server

    server._jobs.clear()

    def boom():
        raise RuntimeError("falhou")

    res = server._start_job("erro", boom)
    assert res["ok"] is False and "falhou" in res["error"]
    assert server._running_job() is None


def test_roller_bearing_volume_by_sample():
    from creo_mcp import parts

    plan = parts.build("roller_bearing", {})
    assert [n for n, _ in plan.volume_parts] == ["rolamento_ai", "rolamento_ae", "rolamento_r1"]
    assert abs(plan.expected_volume - sum(v for _, v in plan.volume_parts)) < 1e-6
    assert len(plan.export_parts) == 14


def test_run_plan_exports_each_sample_part_separately(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    exported = []

    def fake_runner(code, timeout=30, **kw):
        if "sd-put-buffer" in code and "STL" in code:
            exported.append(code)
        return {"ok": True, "result": ":OK", "dialogs": []}

    monkeypatch.setattr(builder, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    monkeypatch.setattr(builder, "top_level_paths", lambda r: [])
    monkeypatch.setattr(builder, "part_bbox", lambda r, p: None)
    monkeypatch.setattr(builder, "REPORT_DIR", str(tmp_path / "rep"))
    monkeypatch.setattr(builder, "_set_wp_z", lambda runner, z, report, axis="z": True)
    plan = parts.build("roller_bearing", {"rollers": 3})
    builder.run_plan(plan, fake_runner, verify=True)
    assert len(exported) == 3
    assert all(c.count('\\"/rolamento_') == 1 for c in exported)


# ----------------------------------------------------------- planos laterais e pecas v2.3
def _segments_intersect(a, b, c, d):
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])
    return ccw(a, c, d) * ccw(b, c, d) < -1e-12 and ccw(a, b, c) * ccw(a, b, d) < -1e-12


def _is_simple(poly):
    n = len(poly)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(i - j) in (1, n - 1):
                continue
            if _segments_intersect(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                return False
    return True


def test_axis_planes_and_directions():
    assert recipes.needs_wz("z", 0) is False and recipes.needs_wz("z", 5) and recipes.needs_wz("y", 0) and recipes.needs_wz("x", 0)
    # plano y: normal -y => sentido global +y usa :direction :-w
    up_y = recipes.sketch_extrude("/p", [("c", 0, 0, 2)], 5, "+w", None, 0, "y")
    dn_y = recipes.sketch_extrude("/p", [("c", 0, 0, 2)], 5, "-w", None, 0, "y")
    pl_x = recipes.sketch_extrude("/p", [("c", 0, 0, 2)], 5, "+w", None, 0, "x")
    assert ":direction :-w" in up_y and ":direction :-w" not in dn_y and ":direction :-w" not in pl_x
    assert '(current_wp "/wz")' in up_y and '(current_wp "/wz")' in pl_x
    assert "0,7,0 1,7,0 0,7,1" in recipes.position_wp(7, axis="y")
    assert "7,0,0 7,1,0 7,0,1" in recipes.position_wp(7, axis="x")
    assert "0,0,7 1,0,7 0,1,7" in recipes.position_wp(7)
    import pytest as _pt
    with _pt.raises(ValueError):
        recipes.position_wp(1, axis="w")
    cut = recipes.cut_with_loops("/p", [("c", 0, 0, 2)], 5, -1, axis="x")
    assert "subtract_3d" in cut and '(current_wp "/wz")' in cut
    # Varios furos disjuntos viram /cuttool, /cuttool.1...: o corte precisa subtrair TODAS as pecas-ferramenta.
    multi = recipes.cut_with_loops("/p", [("c", 0, 0, 2), ("c", 9, 0, 2)], 5, -1)
    assert "dolist" in multi and '"/cuttool"' in multi and "(subseq n 0 8)" in multi
    assert multi.count("(") == multi.count(")")


def test_new_parts_catalog_and_geometry():
    from creo_mcp import parts

    for kind in ("rounded_plate", "l_bracket", "pipe_tee", "bearing_housing", "tslot_profile"):
        plan = parts.build(kind, {})
        assert plan.expected_volume and plan.expected_volume > 0 and plan.expected_bbox and plan.export_parts
        assert all(st.axis in ("x", "y", "z") for st in plan.steps)
    t = parts.build("tslot_profile", {})
    outline = t.steps[0].loops[0][1]
    assert len(outline) == 36 and _is_simple(outline)
    expected = (parts.poly_area(outline) - parts.circle_area(4.25)) * 200
    assert abs(t.expected_volume - expected) < 1e-6
    rp = parts.build("rounded_plate", {})
    assert _is_simple(rp.steps[0].loops[0][1])
    lb = parts.build("l_bracket", {})
    assert [s.axis for s in lb.steps] == ["x", "z", "y"]
    assert abs(lb.expected_volume - ((50 * 5 + 35 * 5) * 60 - 4 * parts.circle_area(3.25) * 5)) < 1e-6
    tee = parts.build("pipe_tee", {})
    assert tee.expected_bbox == ((-100.0, -30.0, -30.0), (100.0, 30.0, 100.0))


def test_new_parts_reject_bad_params():
    import pytest as _pt

    from creo_mcp import parts

    with _pt.raises(parts.PartError):
        parts.build("tslot_profile", {"cavity_w": 30})
    with _pt.raises(parts.PartError):
        parts.build("rounded_plate", {"corner_r": 50})
    with _pt.raises(parts.PartError):
        parts.build("l_bracket", {"hole_d": 40})
    with _pt.raises(parts.PartError):
        parts.build("pipe_tee", {"bore": 60})
    with _pt.raises(parts.PartError):
        parts.build("bearing_housing", {"boss_od": 100})


def _fake_plane_runner(monkeypatch, tmp_path, wrong_normal=False):
    import re

    from creo_mcp import builder

    state = {"pts": None, "calls": []}
    monkeypatch.setattr(builder, "EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(builder, "REPORT_DIR", str(tmp_path / "rep"))
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    monkeypatch.setattr(builder.time, "sleep", lambda *_a, **_k: None)

    def runner(code, timeout=30):
        state["calls"].append(code)
        ok = {"ok": True, "result": ":OK", "dialogs": [], "error": None}
        if "POSITION_WP" in code:
            nums = re.findall(r"(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", code)
            state["pts"] = [tuple(float(v) for v in n) for n in nums[:3]]
            return ok
        if "sd-inq-wp-origin" in code:
            return {**ok, "result": "{},{},{}".format(*state["pts"][0])}
        if "sd-inq-wp-w-dir" in code:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = state["pts"]
            u = (bx - ax, by - ay, bz - az)
            v = (cx - ax, cy - ay, cz - az)
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            if wrong_normal:
                n = (-n[0], -n[1], -n[2])
            return {**ok, "result": "{},{},{}".format(*n)}
        if "sd-inq-obj-children" in code and "labels" not in code:
            return {**ok, "result": '("/w1")'}
        return ok

    return runner, state


def test_run_plan_with_lateral_planes_checks_origin_and_normal(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, state = _fake_plane_runner(monkeypatch, tmp_path)
    plan = parts.build("l_bracket", {})
    rep = builder.run_plan(plan, runner, verify=False)
    assert rep["problems"] == [], rep
    assert [s["ok"] for s in rep["steps"]] == [True, True, True]
    assert sum("POSITION_WP" in c for c in state["calls"]) == 3      # x, z(-1) e y(-1)


def test_run_plan_aborts_when_plane_normal_is_wrong(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, _state = _fake_plane_runner(monkeypatch, tmp_path, wrong_normal=True)
    rep = builder.run_plan(parts.build("l_bracket", {}), runner, verify=False)
    assert rep["ok"] is False and any("normal de /wz" in p for p in rep["problems"])
    assert rep["steps"] == []


# ------------------------------------------------------------------ chanfro e filete (v2.4)
def test_edge_selectors_and_commands():
    import pytest as _pt

    from creo_mcp import recipes

    pred = recipes.edge_pred_lisp([("z", 20), ("zr", 30, 10.5), ("vertical", 5), ("all",)])
    assert pred.count("(") == pred.count(")")
    assert "(< (abs (- z1 20)) 0.01)" in pred and "110.25" in pred and "(>= (+ (* x1 x1) (* y1 y1)) 25)" in pred
    with _pt.raises(ValueError):
        recipes.edge_pred_lisp([("diagonal",)])
    with _pt.raises(ValueError):
        recipes.edge_pred_lisp([])
    setup = recipes.edge_op_setup("blend", 3, [("vertical",)], "/blk")
    assert ":blend" in setup and "*mcp-eo-size* 3.0" in setup and ":unset" in setup
    assert '(setf *mcp-eo-part* "/blk")' in setup
    with _pt.raises(ValueError):
        recipes.edge_op_setup("round", 3, [("all",)], "/p")
    with _pt.raises(ValueError):
        recipes.edge_op_setup("chamfer", 0, [("all",)], "/p")
    assert recipes.edge_op_command() == '(oli:sd-put-buffer "MCP_EDGE_OP2 COMPLETE")'
    d = recipes.edge_dialog_define()
    assert d.count("(") == d.count(")")
    assert "oli:chamfer :distance" in d and "oli:blend :const_radius" in d and ":failure" in d
    assert ":value-type :part" not in d and "*mcp-eo-part*" in d      # peca por global, nunca pela peca corrente


def test_machined_block_volume_matches_montecarlo():
    import math
    import random

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("machined_block", {})
    assert [s.kind for s in plan.steps] == ["extrude", "fillet", "chamfer", "cut", "chamfer"]
    L, W, H, R, c, B = 80.0, 60.0, 30.0, 6.0, 2.0, 20.0

    def inside(x, y, z):
        d = max(0.0, z - (H - c))
        hx, hy, r = L / 2 - d, W / 2 - d, R - d
        if abs(x) > hx or abs(y) > hy:
            return False
        if abs(x) > hx - r and abs(y) > hy - r and (abs(x) - (hx - r)) ** 2 + (abs(y) - (hy - r)) ** 2 > r * r:
            return False
        return math.hypot(x, y) >= B / 2 + d

    rnd = random.Random(7)
    n = 200000
    hit = sum(inside(rnd.uniform(-L / 2, L / 2), rnd.uniform(-W / 2, W / 2), rnd.uniform(0, H)) for _ in range(n))
    assert abs(hit / n * L * W * H - plan.expected_volume) / plan.expected_volume < 0.005
    assert parts.build("machined_block", {"bore": 0}).steps[-1].kind == "chamfer"      # sem furo: so o chanfro do contorno
    for bad in ({"chamfer": 7}, {"corner_r": 40}, {"bore": 52}):
        with _pt.raises(parts.PartError):
            parts.build("machined_block", bad)


def test_run_plan_with_edge_steps(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, state = _fake_plane_runner(monkeypatch, tmp_path)
    seen = []

    def edge_runner(code, timeout=30):
        seen.append(code)
        if code.strip() == "*mcp-eo-result*":
            return {"ok": True, "result": "(12 4 :OK)", "dialogs": [], "error": None}
        return runner(code, timeout)

    rep = builder.run_plan(parts.build("machined_block", {}), edge_runner, verify=False)
    assert rep["problems"] == [], rep
    kinds = [s["step"] for s in rep["steps"]]
    assert kinds[1] == "filete dos cantos verticais" and rep["steps"][1]["edges_selected"] == 4
    assert sum("MCP_EDGE_OP2 COMPLETE" in c for c in seen) == 3
    assert any(":blend" in c and "3.0" not in c and "6.0" in c for c in seen)


def test_run_plan_aborts_when_edge_op_fails(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, _state = _fake_plane_runner(monkeypatch, tmp_path)

    def failing(code, timeout=30):
        if code.strip() == "*mcp-eo-result*":
            return {"ok": True, "result": '(12 4 (:FAIL "SD-CALL-ACTION-ERROR" "boom"))', "dialogs": [], "error": None}
        return runner(code, timeout)

    rep = builder.run_plan(parts.build("machined_block", {}), failing, verify=False)
    assert rep["ok"] is False and "falhou" in rep["problems"][0]
    assert len(rep["steps"]) == 2          # bloco ok, filete falhou, nada depois


def test_edge_treatment_tool_validation_and_run(monkeypatch):
    import json as _j

    from creo_mcp import builder, server

    server._jobs.clear()
    monkeypatch.setattr(server, "_top_level_parts", lambda: ["/w1", "/bloco"])
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    monkeypatch.setattr(builder.time, "sleep", lambda *_a, **_k: None)
    seen = []

    def fake_run(code, timeout=30.0, auto_dismiss=True):
        seen.append(code)
        ok = {"ok": True, "result": ":READY", "dialogs": [], "error": None}
        if code.strip() == "*mcp-eo-result*":
            return {**ok, "result": "(12 4 :OK)"}
        return ok

    monkeypatch.setattr(server, "_run", fake_run)
    out = _j.loads(server.creo_edge_treatment("/bloco", "fillet", 3, '[["vertical"]]'))
    assert out["ok"] is True and out["edges_selected"] == 4
    assert any(":blend" in c and '"/bloco"' in c for c in seen)
    for bad in (("/bloco", "round", 3, '[["all"]]'), ("/bloco", "chamfer", -1, '[["all"]]'),
                ("/bloco", "chamfer", 1, '[["diagonal"]]'), ("/bloco", "chamfer", 1, "nao json"),
                ("/inexistente", "chamfer", 1, '[["all"]]')):
        assert _j.loads(server.creo_edge_treatment(*bad))["ok"] is False


def test_stepped_shaft_end_chamfer():
    import math

    import pytest as _pt

    from creo_mcp import parts

    base = parts.build("stepped_shaft", {})
    ch = parts.build("stepped_shaft", {"end_chamfer": 2})
    assert [s.kind for s in ch.steps[-2:]] == ["chamfer", "chamfer"] and ch.steps[-1].loops == [("z", 270.0)]
    r = 15.0
    assert abs((base.expected_volume - ch.expected_volume) - 2 * math.pi * (r * 4 - 8 / 3)) < 1e-6
    with _pt.raises(parts.PartError):
        parts.build("stepped_shaft", {"end_chamfer": 20})


def test_flange_fillet_and_rim_chamfer():
    import math

    import pytest as _pt

    from creo_mcp import parts, recipes

    base = parts.build("flange", {})
    fl = parts.build("flange", {"hub_fillet": 6, "rim_chamfer": 2})
    assert [s.kind for s in fl.steps[-3:]] == ["fillet", "chamfer", "chamfer"]
    # filete concavo por integracao numerica: area entre o canto e o arco, ponderada por 2*pi*r
    rf, rh = 6.0, 45.0
    n = 4000
    add = 0.0
    for i in range(n):                       # fatias em z (0..rf) acima do disco
        z = (i + 0.5) * rf / n
        inner = rf - math.sqrt(max(rf * rf - (rf - z) ** 2, 0.0))        # largura radial do filete na altura z
        add += math.pi * ((rh + inner) ** 2 - rh ** 2) * (rf / n)
    rim = 2 * math.pi * (80 * 4 - 8 / 3)
    assert abs((fl.expected_volume - base.expected_volume) - (add - rim)) < 1.0
    sel = fl.steps[-3].loops[0]
    assert sel == ("zr", 18.0, 45.5, 44.5)
    pred = recipes.edge_pred_lisp([sel])
    assert pred.count("(") == pred.count(")") and "2070.25" in pred and "1980.25" in pred
    for bad in ({"hub_fillet": 50}, {"rim_chamfer": 12}, {"hub_od": 0, "hub_fillet": 3}):
        with _pt.raises(parts.PartError):
            parts.build("flange", bad)


def test_manifold_block_geometry_and_directions():
    import math
    import random

    import pytest as _pt

    from creo_mcp import parts, recipes

    plan = parts.build("manifold_block", {})
    L, W, H = 100.0, 60.0, 50.0

    def inside(x, y, z):
        if math.hypot(x - 50, y - 30) < 5 and z > H - 30:
            return False
        if math.hypot(x - 50, y - 30) < 8 and z > H - 8:
            return False
        if math.hypot(x - 25, z - 25) < 4 and y < 22:
            return False
        if math.hypot(x - 75, z - 25) < 4 and y > W - 22:
            return False
        if math.hypot(y - 30, z - 25) < 4 and x < 22:
            return False
        if math.hypot(y - 30, z - 25) < 4 and x > L - 22:
            return False
        return True

    rnd = random.Random(3)
    n = 300000
    hit = sum(inside(rnd.uniform(0, L), rnd.uniform(0, W), rnd.uniform(0, H)) for _ in range(n))
    assert abs(hit / n * L * W * H - plan.expected_volume) / plan.expected_volume < 0.004
    # sentidos: plano y tem normal -y (global +y precisa de :direction :-w); plano x/z seguem a normal
    cuts = {s.label: s for s in plan.steps if s.kind == "cut"}
    code = {k: recipes.sketch_extrude("/t", s.loops, s.dz, s.direction, None, s.z0, s.axis) for k, s in cuts.items()}
    assert ":direction :-w" in code["furo do topo"] and ":direction :-w" in code["furo direito (x=L)"]
    assert ":direction :-w" in code["furo frontal (y=0)"]          # +y global no plano y
    assert ":direction :-w" not in code["furo traseiro (y=W)"]     # -y global = normal do plano y
    assert ":direction :-w" not in code["furo esquerdo (x=0)"]
    for bad in ({"cbore_d": 8}, {"top_depth": 48}, {"side_depth": 30}, {"length": 40}):
        with _pt.raises(parts.PartError):
            parts.build("manifold_block", bad)


def test_socket_head_screw_volume_and_selectors():
    import math

    import pytest as _pt

    from creo_mcp import parts, recipes

    plan = parts.build("socket_head_screw", {})
    exp = (math.pi * 25 * 40 + math.pi * 64 * 10 - math.sqrt(3) / 2 * 64 * 5
           - math.pi * (5 * 1 - 1 / 3) - math.pi * (8 * 0.64 - 0.512 / 3))
    assert abs(plan.expected_volume - exp) < 1e-6
    assert plan.expected_bbox == ((-8.0, -8.0, 0), (8.0, 8.0, 50.0))
    sels = [s.loops[0] for s in plan.steps if s.kind == "chamfer"]
    assert sels == [("zr", 0.0, 5.5, 4.5), ("zr", 50.0, 8.5, 7.5)]
    assert recipes.edge_pred_lisp(sels).count("(") == recipes.edge_pred_lisp(sels).count(")")
    hexpts = plan.steps[2].loops[0][1]
    assert len(hexpts) == 6 and abs(parts.poly_area(hexpts) - math.sqrt(3) / 2 * 64) < 1e-6
    for bad in ({"size": 7}, {"tip_chamfer": 6}, {"head_chamfer": 4}):
        with _pt.raises(parts.PartError):
            parts.build("socket_head_screw", bad)


# ------------------------------------------------------------------ montagem (v2.5)
def test_bolted_joint_geometry_and_stack():
    import math

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("bolted_joint", {})
    assert [p for p, _v in plan.volume_parts] == ["junta_chapa1", "junta_chapa2", "junta_arruela", "junta_parafuso", "junta_porca"]
    vols = dict(plan.volume_parts)
    hole = 14.0
    assert abs(vols["junta_chapa1"] - (80 * 60 - math.pi * 49) * 15) < 1e-6
    assert abs(vols["junta_arruela"] - math.pi * (144 - 42.25) * 2.5) < 1e-6
    assert abs(sum(vols.values()) - plan.expected_volume) < 1e-6
    bb = plan.part_bboxes
    # empilhamento: porca -> chapa1 -> chapa2 -> arruela -> cabeca do parafuso, sem sobreposicao nem vao
    assert bb["junta_porca"][0][2] == 4.0 and bb["junta_porca"][1][2] == bb["junta_chapa1"][0][2]
    assert bb["junta_chapa1"][1][2] == bb["junta_chapa2"][0][2]
    assert bb["junta_chapa2"][1][2] == bb["junta_arruela"][0][2]
    assert abs(bb["junta_arruela"][1][2] - 47.3) < 1e-9 and bb["junta_parafuso"][0][2] == 0
    assert abs(bb["junta_parafuso"][1][2] - (47.3 + 7.5)) < 1e-9
    # a haste (Ø12) passa folgada nos furos Ø14 das chapas e da arruela
    loops = {s.part: s.loops for s in plan.steps if s.kind == "extrude"}
    assert ("c", 0, 0, hole / 2) in loops["junta_chapa1"] and ("c", 0, 0, 6.5) in loops["junta_arruela"]
    # deslocar o plano nao altera o original (hex_nut e dataclass independente)
    assert parts.build("hex_nut", {}).steps[0].z0 == 0
    for bad in ({"size": 7}, {"plate_w": 29}, {"plate_d": 25}, {"t1": 1}, {"protrusion": 0}):
        with _pt.raises(parts.PartError):
            parts.build("bolted_joint", bad)


def test_run_plan_checks_every_part_bbox(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, _state = _fake_plane_runner(monkeypatch, tmp_path)
    plan = parts.build("bolted_joint", {})

    def make(shift):
        def r(code, timeout=30):
            if "sd-inq-exact-box" in code:
                for name, (lo, hi) in plan.part_bboxes.items():
                    if f'"/{name}"' in code:
                        dz = shift if name == "junta_arruela" else 0.0
                        return {"ok": True, "result": "((%s %s %s) (%s %s %s))" % (lo[0], lo[1], lo[2] + dz, hi[0], hi[1], hi[2] + dz),
                                "dialogs": [], "error": None}
            return runner(code, timeout)
        return r

    good = builder.run_plan(plan, make(0.0), verify=False)
    assert good["problems"] == [] and all(v["ok"] for v in good["part_bboxes"].values())
    bad = builder.run_plan(plan, make(1.0), verify=False)
    assert bad["ok"] is False and "junta_arruela" in bad["problems"][0]
    assert bad["part_bboxes"]["junta_arruela"]["ok"] is False and bad["part_bboxes"]["junta_porca"]["ok"] is True


def test_flanged_joint_geometry_and_validation():
    import math

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("flanged_joint", {})
    assert len(plan.part_bboxes) == 11 and len(plan.steps) == 15 and len(plan.export_parts) == 11
    assert [p for p, _v in plan.volume_parts] == ["juncao_flange1", "juncao_flange2", "juncao_junta", "juncao_parafuso1", "juncao_porca1"]
    # empilhamento sem vaos: porca -> flange1 -> junta -> flange2 -> cabeca
    bb = plan.part_bboxes
    assert bb["juncao_flange1"][0][2] == 4.0 + 14.8 and bb["juncao_flange1"][1][2] == bb["juncao_junta"][0][2]
    assert bb["juncao_junta"][1][2] == bb["juncao_flange2"][0][2]
    assert abs(bb["juncao_parafuso1"][1][2] - (bb["juncao_flange2"][1][2] + 10.0)) < 1e-9
    # parafusos no circulo de passo, a 45 graus (4 parafusos), e cada furo do flange coincide com o parafuso
    R = 24 / math.sqrt(3)
    c = 125 / 2 * math.cos(math.radians(45))
    lo, hi = bb["juncao_parafuso1"]
    assert abs((lo[0] + hi[0]) / 2 - c) < 1e-9 and abs((lo[1] + hi[1]) / 2 - c) < 1e-9
    assert abs(hi[0] - lo[0] - 2 * R) < 1e-9 and abs(hi[1] - lo[1] - 24) < 1e-9
    holes = [lp for lp in plan.steps[8].loops if lp[0] == "c"][2:]          # flange 1: apos o contorno e o furo central
    assert len(holes) == 4 and all(abs(h[3] - 9.0) < 1e-9 for h in holes)
    assert abs(holes[0][1] - c) < 1e-9 and abs(holes[0][2] - c) < 1e-9
    # volume da amostra
    fl = (math.pi * (82.5 ** 2 - 30 ** 2 - 4 * 81)) * 20
    assert abs(dict(plan.volume_parts)["juncao_flange1"] - fl) < 1e-6
    # contornos transladados nao alteram o gabarito
    assert parts.build("hex_bolt", {}).steps[0].loops == [("c", 0, 0, 6.0)]
    for bad in ({"size": 7}, {"bolts": 2}, {"od": 100}, {"bore": 100}, {"bolts": 16, "pcd": 70}, {"pcd": 60}):
        with _pt.raises(parts.PartError):
            parts.build("flanged_joint", bad)
    six = parts.build("flanged_joint", {"bolts": 6, "pcd": 140, "od": 180})
    assert len(six.part_bboxes) == 15 and len(six.steps) == 3 + 18


def test_move_loops_all_kinds():
    import pytest as _pt

    from creo_mcp import parts

    out = parts._move_loops([("c", 1, 2, 3), ("r", 0, 0, 4, 5), ("p", [(0, 0), (1, 0), (1, 1)])], 10, 20)
    assert out == [("c", 11, 22, 3), ("r", 10, 20, 14, 25), ("p", [(10, 20), (11, 20), (11, 21)])]
    with _pt.raises(parts.PartError):
        parts._move_loops([("x", 1)], 0, 0)


# ------------------------------------------------------------------ chanfro assimetrico (v2.5)
def test_asymmetric_chamfer_commands_and_selector():
    import math

    import pytest as _pt

    from creo_mcp import recipes

    d = recipes.edge_dialog_define()
    assert d.count("(") == d.count(")")
    assert ":dist_dist sel :new_distance1 *mcp-eo-size* :new_distance2 *mcp-eo-size2*" in d
    assert ":dist_angle sel :new_angle_dist *mcp-eo-size* :new_angle *mcp-eo-size2*" in d
    two = recipes.edge_op_setup("chamfer2", 2, [("all",)], "/b", 5)
    assert ":chamfer2" in two and "*mcp-eo-size* 2.0" in two and "*mcp-eo-size2* 5.0" in two
    ang = recipes.edge_op_setup("chamfer_angle", 4, [("all",)], "/b", 30)       # o Creo recebe radianos
    assert "*mcp-eo-size2* %s" % recipes.num(math.radians(30)) in ang
    for bad in (("chamfer2", 2, 0), ("chamfer_angle", 2, 90), ("chamfer_angle", 2, 0), ("round", 2, 1)):
        with _pt.raises(ValueError):
            recipes.edge_op_setup(bad[0], bad[1], [("all",)], "/b", bad[2])
    sel = recipes.edge_pred_lisp([("edge", 40, 0, 20, 40, 30, 20)])
    assert sel.count("(") == sel.count(")") and sel.count("(or (and") == 1
    with _pt.raises(ValueError):
        recipes.edge_pred_lisp([("edge", 1, 2, 3)])
    lst = recipes.edge_list_define()
    assert lst.count("(") == lst.count(")") and "MCP_EDGE_LIST COMPLETE" in recipes.edge_list_command()


def test_machined_block_bore_lead_in():
    import math

    import pytest as _pt

    from creo_mcp import parts

    sym = parts.build("machined_block", {})
    asym = parts.build("machined_block", {"bore_lead_in": 5})
    last = asym.steps[-1]
    assert last.kind == "chamfer2" and last.dz == 5.0 and last.dz2 == 2.0 and last.loops == [("zr", 30.0, 10.5)]   # d1 axial, d2 radial
    # diferenca de volume = triangulo 2x5 (raio 10) menos triangulo 2x2, girados (Pappus)
    dv = math.pi * 2 * 5 * (10 + 2 / 3) - math.pi * 2 * 2 * (10 + 2 / 3)
    assert abs((sym.expected_volume - asym.expected_volume) - dv) < 1e-6
    for bad in ({"bore_lead_in": 15}, {"bore_lead_in": 3, "bore": 0}):
        with _pt.raises(parts.PartError):
            parts.build("machined_block", bad)


def test_edge_treatment_tool_asymmetric_modes(monkeypatch):
    import json as _j

    from creo_mcp import builder, server

    server._jobs.clear()
    monkeypatch.setattr(server, "_top_level_parts", lambda: ["/w1", "/bloco"])
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    monkeypatch.setattr(builder.time, "sleep", lambda *_a, **_k: None)
    seen = []

    def fake_run(code, timeout=30.0, auto_dismiss=True):
        seen.append(code)
        if code.strip() == "*mcp-eo-result*":
            return {"ok": True, "result": "(12 1 :OK)", "dialogs": [], "error": None}
        return {"ok": True, "result": ":READY", "dialogs": [], "error": None}

    monkeypatch.setattr(server, "_run", fake_run)
    out = _j.loads(server.creo_edge_treatment("/bloco", "chamfer2", 2, '[["edge",40,0,20,40,30,20]]', 5))
    assert out["ok"] is True and out["edges_selected"] == 1
    assert any(":chamfer2" in c and "*mcp-eo-size2* 5.0" in c for c in seen)
    out = _j.loads(server.creo_edge_treatment("/bloco", "chamfer_angle", 4, '[["all"]]', 30))
    assert out["ok"] is True
    for bad in (("/bloco", "chamfer2", 2, '[["all"]]', 0), ("/bloco", "chamfer_angle", 2, '[["all"]]', 95)):
        assert _j.loads(server.creo_edge_treatment(*bad))["ok"] is False


# ------------------------------------------------------------------ casca (SHELL_3D) e caixa (v2.5)
def test_face_selectors_and_shell_recipes():
    import pytest as _pt

    from creo_mcp import recipes

    pred = recipes.face_pred_lisp([("face_z", 30), ("face_x", -5)])
    assert pred.count("(") == pred.count(")") and "(< (abs (- z1 30)) 0.01)" in pred and "(< (abs (- x2 -5)) 0.01)" in pred
    for bad in ([("face_w", 1)], [("face_z",)], []):
        with _pt.raises(ValueError):
            recipes.face_pred_lisp(bad)
    d = recipes.shell_dialog_define()
    assert d.count("(") == d.count(")") and "oli:shell_3d :select_part part-item :offset *mcp-sh-offset* :open_face open" in d
    assert ":value-type :part" not in d and "*mcp-eo-part*" in d
    st = recipes.shell_op_setup(3, [("face_z", 30)], "/blk")
    assert '(setf *mcp-eo-part* "/blk")' in st and "*mcp-sh-offset* 3.0" in st
    with _pt.raises(ValueError):
        recipes.shell_op_setup(0, [("face_z", 30)], "/blk")
    assert recipes.shell_op_command() == '(oli:sd-put-buffer "MCP_SHELL_OP COMPLETE")'
    f = recipes.face_list_define()
    assert f.count("(") == f.count(")") and "MCP_FACE_LIST COMPLETE" in recipes.face_list_command()


def test_run_plan_with_shell_step(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, _state = _fake_plane_runner(monkeypatch, tmp_path)
    seen = []

    def shell_runner(code, timeout=30):
        seen.append(code)
        if code.strip() == "*mcp-sh-result*":
            return {"ok": True, "result": "(10 1 :OK)", "dialogs": [], "error": None}
        if code.strip() == "*mcp-eo-result*":
            return {"ok": True, "result": "(12 4 :OK)", "dialogs": [], "error": None}
        return runner(code, timeout)

    rep = builder.run_plan(parts.build("enclosure", {}), shell_runner, verify=False)
    assert rep["problems"] == [] or all("bbox" in p for p in rep["problems"]), rep["problems"]
    shell = [s for s in rep["steps"] if s["step"] == "casca (topo aberto)"][0]
    assert shell["ok"] and shell["faces_selected"] == 1 and shell["faces_total"] == 10
    assert any("MCP_SHELL_OP COMPLETE" in c for c in seen) and any("oli:shell_3d" in c for c in seen)


def test_enclosure_volume_montecarlo_and_rejects():
    import math
    import random

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("enclosure", {})
    L, W, H, w, R, bd, bhd, bh, bdep, cd = 120, 80, 50, 3, 8, 9, 3.4, 12, 10, 16
    rc = R - w
    bx, by = L / 2 - w - bd / 2 - 2, W / 2 - w - bd / 2 - 2

    def in_rrect(x, y, hx, hy, r):
        ax, ay = abs(x), abs(y)
        if ax > hx or ay > hy:
            return False
        if ax > hx - r and ay > hy - r:
            return math.hypot(ax - (hx - r), ay - (hy - r)) <= r
        return True

    random.seed(3)
    n, hit = 400000, 0
    for _ in range(n):
        x, y, z = random.uniform(-L / 2, L / 2), random.uniform(-W / 2, W / 2), random.uniform(0, H)
        if not in_rrect(x, y, L / 2, W / 2, R):
            continue
        solid = not (z > w and in_rrect(x, y, L / 2 - w, W / 2 - w, rc))
        if not solid and w <= z <= w + bh and (abs(abs(x) - bx) ** 2 + abs(abs(y) - by) ** 2) <= (bd / 2) ** 2:
            solid = not (z >= w + bh - bdep and (abs(abs(x) - bx) ** 2 + abs(abs(y) - by) ** 2) <= (bhd / 2) ** 2)
        if solid and y < -W / 2 + w and x * x + (z - H / 2) ** 2 <= (cd / 2) ** 2:
            solid = False
        hit += solid
    body = dict(plan.volume_parts)["caixa"]
    assert abs(hit / n * L * W * H - body) / body < 0.02
    assert plan.part_bboxes["caixa_tampa"][0][2] == 50 and abs(plan.expected_volume - sum(v for _p, v in plan.volume_parts)) < 1e-6
    shell = [s for s in plan.steps if s.kind == "shell"][0]
    assert shell.loops == [("face_z", 50.0)] and shell.dz == 3.0
    cable = [s for s in plan.steps if s.label.startswith("furo do cabo")][0]
    assert cable.axis == "y" and cable.z0 == -41.0
    for bad in ({"corner_r": 3}, {"boss_d": 40}, {"boss_hole_depth": 20}, {"cable_hole": 60}, {"wall": 0.5, "corner_r": 0.4}):
        with _pt.raises(parts.PartError):
            parts.build("enclosure", bad)


def test_shell_tool_validation_and_run(monkeypatch):
    import json as _j

    from creo_mcp import builder, server

    server._jobs.clear()
    monkeypatch.setattr(server, "_top_level_parts", lambda: ["/w1", "/bloco"])
    monkeypatch.setattr(builder, "settle", lambda *a, **k: [])
    monkeypatch.setattr(builder.time, "sleep", lambda *_a, **_k: None)
    seen = []

    def fake_run(code, timeout=30.0, auto_dismiss=True):
        seen.append(code)
        if code.strip() == "*mcp-sh-result*":
            return {"ok": True, "result": "(6 1 :OK)", "dialogs": [], "error": None}
        return {"ok": True, "result": ":READY", "dialogs": [], "error": None}

    monkeypatch.setattr(server, "_run", fake_run)
    out = _j.loads(server.creo_shell("/bloco", 3, '[["face_z", 30]]'))
    assert out["ok"] is True and out["faces_selected"] == 1 and out["faces_total"] == 6
    assert any("*mcp-sh-offset* 3.0" in c for c in seen) and any("MCP_SHELL_OP COMPLETE" in c for c in seen)
    for bad in (("/bloco", 0, '[["face_z", 30]]'), ("/bloco", 3, '[["face_q", 1]]'), ("/bloco", 3, "nao json"),
                ("/inexistente", 3, '[["face_z", 30]]'), ("/bloco", 3, "[]")):
        assert _j.loads(server.creo_shell(*bad))["ok"] is False


# ------------------------------------------------------------------ helice: mola e rosca (v2.7)
def test_helix_sweep_recipe_add_and_remove():
    add = recipes.helix_sweep("mola", [("c", 10, 2.5, 2.5)], 8.0, 5.0)
    assert add.count("(") == add.count(")")
    assert "(create_part :name \"mola\")" in add and ":remove_material" not in add
    assert "(helix_dia :blank_part \"/mola\" :wp \"/wz\" :axis :two_pta 0,0,0 0,0,10 :pitch 8 :turns 5)" in add
    assert ":keep_profile :no" in add and "(delete_3d n)" in add and "\"/hjunk\"" in add    # limpeza do perfil que sobra
    rem = recipes.helix_sweep("/eixo", [("p", [(6.3, 9.2), (4.9, 10), (6.3, 10.8)])], 1.75, 10.0, remove=True)
    assert "(helix_dia :remove_material :blank_part \"/eixo\"" in rem and "create_part :name \"eixo\"" not in rem
    assert "nao existe" in rem and "(polygon" in rem


def test_thread_groove_matches_pappus_and_iso_depth():
    import math

    from creo_mcp import parts

    P, R, h3, w, per_mm = parts.thread_groove(12.0)
    assert P == 1.75 and R == 6.0 and abs(h3 - 1.07345) < 1e-9
    assert abs(per_mm * 17.5 - 235.8) < 0.2            # 10 voltas medidas ao vivo: 235,8 mm3 analiticos
    assert abs(w - 2 * h3 * math.tan(math.radians(30))) < 1e-12 and w < P        # sulco mais estreito que o passo
    for bad in (7.3, 100.0):
        with __import__("pytest").raises(parts.PartError):
            parts.thread_groove(bad)
    assert parts.thread_groove(7.3, 1.0)[0] == 1.0       # passo informado dispensa a tabela


def test_thread_step_geometry_start_outside_and_limits():
    import pytest as _pt

    from creo_mcp import parts

    st, vol, P = parts.thread_step("x", 12.0, 60.0, 30.0)
    assert st.kind == "helix_cut" and st.axis == "y" and st.dz == 1.75 and st.z0 == 0.0
    assert abs(st.dz2 - (30.0 + P) / P) < 1e-9
    pts = st.loops[0][1]
    assert len(pts) == 3 and pts[0][0] == pts[2][0] == 6.2625 and abs(pts[1][0] - (6 - 0.6134 * P)) < 1e-9     # ext = 0,15 P
    assert pts[1][1] == -P and max(z for _x, z in pts) < 0           # perfil inicial fora da peca (abaixo de z=0)
    st_full, vol_full, _p = parts.thread_step("x", 12.0, 60.0, 0.0, through=True)
    assert abs(st_full.dz2 - (60.0 + 2 * P) / P) < 1e-9 and vol_full > vol
    for bad in (dict(thread_length=1.0), dict(thread_length=59.9), dict(thread_length=0.0)):      # curto, bate na cabeca, sem through
        with _pt.raises(parts.PartError):
            parts.thread_step("x", 12.0, 60.0, **bad)


def test_compression_spring_geometry_and_rejects():
    import math

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("compression_spring", {"wire_d": 5, "mean_d": 20, "pitch": 8, "turns": 5})
    st = plan.steps[0]
    assert st.kind == "helix_add" and st.axis == "y" and st.dz == 8 and st.dz2 == 5
    assert st.loops == [("c", 10.0, 2.5, 2.5)]
    assert plan.expected_bbox == ((-12.5, -12.5, 0), (12.5, 12.5, 45.0))      # medido ao vivo: exato
    assert abs(plan.expected_volume - math.pi * 2.5 ** 2 * 2 * math.pi * 10 * 5) < 1e-6
    assert plan.parts() == ["mola"]
    for bad in ({"mean_d": 4}, {"pitch": 3}, {"turns": 0.5}, {"wire_d": 0.1}):
        with _pt.raises(parts.PartError):
            parts.build("compression_spring", bad)


def test_threaded_rod_and_threaded_fasteners():
    import math

    import pytest as _pt

    from creo_mcp import parts

    rod = parts.build("threaded_rod", {"size": 12, "length": 60, "end_chamfer": 1.0})
    kinds = [s.kind for s in rod.steps]
    assert kinds == ["extrude", "chamfer", "chamfer", "helix_cut"]                # chanfros antes da rosca
    assert rod.expected_bbox == ((-6.0, -6.0, 0), (6.0, 6.0, 60.0))
    plain = parts.build("threaded_rod", {"size": 12, "length": 60})
    assert plain.expected_volume < math.pi * 36 * 60 and plain.steps[-1].dz2 == (60 + 2 * 1.75) / 1.75
    part = parts.build("threaded_rod", {"size": 12, "length": 60, "thread_length": 30})
    assert part.expected_volume > plain.expected_volume                        # menos rosca, mais material
    bolt = parts.build("hex_bolt", {"size": 12, "length": 50, "thread_length": 30})
    assert bolt.steps[-1].kind == "helix_cut" and bolt.steps[0].kind == "extrude"
    assert bolt.expected_volume < parts.build("hex_bolt", {"size": 12, "length": 50}).expected_volume
    assert parts.build("hex_bolt", {}).steps[-1].kind == "extrude"             # padrao: sem rosca (comportamento antigo)
    screw = parts.build("socket_head_screw", {"size": 10, "length": 40, "thread_length": 25})
    assert screw.steps[-1].kind == "helix_cut" and "M10x1.5" in screw.notes
    for kind, bad in (("hex_bolt", {"thread_length": 49.9}), ("socket_head_screw", {"thread_length": 40}),
                      ("threaded_rod", {"end_chamfer": 4}), ("threaded_rod", {"size": 7})):
        with _pt.raises(parts.PartError):
            parts.build(kind, bad)


def test_run_plan_with_helix_steps(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, state = _fake_plane_runner(monkeypatch, tmp_path)
    rep = builder.run_plan(parts.build("threaded_rod", {"size": 12, "length": 40}), runner, verify=False)
    assert rep["problems"] == [] or all("bbox" in p for p in rep["problems"]), rep["problems"]
    assert [s["step"] for s in rep["steps"]] == ["haste", "rosca M12x1.75"] and all(s["ok"] for s in rep["steps"])
    # plano y=0 posicionado uma vez para a hélice, depois da haste (plano z=0 e /w1)
    assert sum("POSITION_WP" in c for c in state["calls"]) == 1
    helix = [c for c in state["calls"] if "helix_dia" in c]
    assert len(helix) == 1 and ":remove_material" in helix[0] and ":pitch 1.75" in helix[0]
    spring = builder.run_plan(parts.build("compression_spring", {"name": "m2"}), runner, verify=False)
    assert spring["steps"][0]["ok"] and any("(create_part :name \"m2\")" in c and "helix_dia :blank_part" in c
                                            for c in state["calls"])
    # a mola e criada pela propria hélice: o plano de pecas a conhece (conflito com existentes e detectado)
    assert parts.build("compression_spring", {}).parts() == ["mola"]


def test_run_plan_aborts_when_helix_fails(monkeypatch, tmp_path):
    from creo_mcp import builder, parts

    runner, _state = _fake_plane_runner(monkeypatch, tmp_path)

    def failing(code, timeout=30):
        if "helix_dia" in code:
            return {"ok": False, "result": None, "dialogs": [{"title": "Error", "text": "x"}], "error": "boom"}
        return runner(code, timeout)

    rep = builder.run_plan(parts.build("threaded_rod", {"size": 12, "length": 40}), failing, verify=False)
    assert rep["ok"] is False and any("rosca" in p for p in rep["problems"])


def test_threaded_nut_internal_thread():
    import math

    import pytest as _pt

    from creo_mcp import parts

    plain = parts.build("hex_nut", {"size": 12})
    nut = parts.build("hex_nut", {"size": 12, "threaded": True})
    assert [s.kind for s in nut.steps] == ["extrude", "helix_cut"] and nut.main_part == "porca"
    bore = nut.steps[0].loops[1]
    assert bore[0] == "c" and abs(bore[3] - (6 - 0.5413 * 1.75)) < 1e-9          # furo no diametro menor D1 = 10,105
    helix = nut.steps[1]
    assert helix.axis == "y" and helix.dz == 1.75 and abs(helix.dz2 - (10.8 + 2 * 1.75) / 1.75) < 1e-9
    tri = helix.loops[0][1]
    assert abs(tri[1][0] - 6.0) < 1e-9 and tri[0][0] < bore[3] and tri[2][0] == tri[0][0]    # ponta no nominal, base dentro do furo
    assert tri[1][1] == -1.75 and max(z for _x, z in tri) < 0                        # comeca fora da peca
    assert plain.steps[-1].kind == "extrude" and plain.expected_volume != nut.expected_volume
    # volume: sextavado - furo D1 - sulco (Pappus) deve ficar entre o furo nominal e o furo D1
    hexa = math.sqrt(3) / 2 * 18 * 18 * 10.8
    assert hexa - math.pi * 36 * 10.8 < nut.expected_volume < hexa - math.pi * bore[3] ** 2 * 10.8
    with _pt.raises(parts.PartError):
        parts.build("hex_nut", {"size": 12, "threaded": True, "name": "n"}) and parts.internal_thread_step("n", 12.0, 10.0, 6.0)


def test_helix_check_refuses_self_intersecting_pitch(monkeypatch, tmp_path):
    import pytest as _pt

    from creo_mcp import builder, parts

    assert recipes.loops_z_extent([("p", [(31, 4.5), (20, 10), (31, 15.5)])]) == 11.0
    assert recipes.loops_z_extent([("c", 10, 2.5, 2.5)]) == 5.0
    recipes.helix_check([("c", 10, 2.5, 2.5)], 8.0, 5.0)                      # ok
    for pitch, turns in ((0.01, 1.0), (5.0, 3.0), (4.9, 3.0), (8.0, 0.0)):    # passo <= altura: Creo entra em laco de erro
        with _pt.raises(ValueError):
            recipes.helix_check([("c", 10, 2.5, 2.5)], pitch, turns)
    with _pt.raises(ValueError):
        recipes.helix_sweep("pul", [("p", [(31, 4.5), (20, 10), (31, 15.5)])], 0.01, 1.0, remove=True)
    # nenhuma rosca ISO do catalogo gera um perfil mais alto que ~0,94 do passo (externa e interna)
    for d in (3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 27, 30):
        st, _v, P = parts.thread_step("x", float(d), 80.0, 40.0)
        st2, _v2, P2, _r = parts.internal_thread_step("n", float(d), 20.0)
        assert recipes.loops_z_extent(st.loops) < 0.9 * P and recipes.loops_z_extent(st2.loops) < 0.9 * P2
    # o builder recusa o passo ANTES de ir ao Creo e nao deixa nada pela metade
    runner, state = _fake_plane_runner(monkeypatch, tmp_path)
    bad = parts.build("threaded_rod", {"size": 12, "length": 40})
    bad.steps[-1].dz = 0.5                                                      # passo menor que o perfil (1,5 mm)
    rep = builder.run_plan(bad, runner, verify=False)
    assert rep["ok"] is False and any("recusado" in p for p in rep["problems"])
    assert not any("helix_dia" in c for c in state["calls"])


def test_lead_screw_trapezoid_groove_and_plan():
    import math

    import pytest as _pt

    from creo_mcp import parts

    P, R, h3, g_R, g_r, per_mm = parts.trapezoid_groove(20.0)
    assert P == 4.0 and R == 10.0 and abs(h3 - 2.25) < 1e-9                    # Tr20x4: h3 = 0,5 P + ac(0,25)
    assert g_R > g_r > 0 and abs((g_R - g_r) / 2 / h3 - math.tan(math.radians(15))) < 1e-9     # flancos a 15 graus
    assert 0.1 < per_mm / (math.pi * R * R) < 0.35                              # sulco = fracao razoavel da secao
    plan = parts.build("lead_screw", {"size": 20, "length": 60, "end_chamfer": 1.0})
    assert [s.kind for s in plan.steps] == ["extrude", "chamfer", "chamfer", "helix_cut"]
    helix = plan.steps[-1]
    assert helix.axis == "y" and helix.dz == 4.0 and abs(helix.dz2 - (60 + 2 * 4.0) / 4.0) < 1e-9
    pts = helix.loops[0][1]
    assert len(pts) == 4 and pts[0][0] == pts[3][0] > 10 and pts[1][0] == pts[2][0] == 10 - h3
    assert plan.expected_bbox == ((-10.0, -10.0, 0), (10.0, 10.0, 60.0)) and plan.expected_volume < math.pi * 100 * 60
    with _pt.raises(parts.PartError):
        parts.build("lead_screw", {"size": 7})                                 # fora da tabela
    for bad in ({"thread_length": 5}, {"length": 60, "thread_length": 59.9}, {"end_chamfer": 6}, {"length": 10}):
        with _pt.raises(parts.PartError):
            parts.build("lead_screw", bad)


def test_worm_shaft_geometry_and_validation():
    import math

    import pytest as _pt

    from creo_mcp import parts

    plan = parts.build("worm_shaft", {})
    core, helix = plan.steps
    assert core.kind == "extrude" and helix.kind == "helix_add" and helix.axis == "y"
    rf, ra, px = 20 / 2 - 2.4, 20 / 2 + 2, math.pi * 2
    assert abs(core.loops[0][3] - rf) < 1e-9 and abs(helix.dz - px) < 1e-9
    prof = helix.loops[0][1]
    assert abs(prof[1][0] - ra) < 1e-9 and prof[1][0] == prof[2][0] and prof[0][0] < rf     # base entra no nucleo
    assert plan.expected_bbox == ((-ra, -ra, 0), (ra, ra, 80.0))
    assert plan.expected_volume > math.pi * rf ** 2 * 80
    from creo_mcp import recipes

    recipes.helix_check(helix.loops, helix.dz, helix.dz2)                       # passo maior que a altura do perfil
    for bad in ({"length": 15}, {"module": 0.1}, {"q": 2}):
        with _pt.raises(parts.PartError):
            parts.build("worm_shaft", bad)


def test_chain_sprocket_outline_and_plan():
    import math

    import pytest as _pt

    from creo_mcp import parts

    pts, info = parts.sprocket_outline(19, 12.7, 7.92)
    assert _is_simple(pts)
    radii = [math.hypot(x, y) for x, y in pts]
    assert abs(min(radii) - info["root_d"] / 2) < 0.02 and abs(max(radii) - info["tip_d"] / 2) < 1e-9
    assert abs(info["pitch_d"] - 12.7 / math.sin(math.pi / 19)) < 1e-9 and info["tip_d"] > info["pitch_d"] > info["root_d"]
    plan = parts.build("chain_sprocket", {})
    kinds = [(s.kind, s.axis) for s in plan.steps]
    assert kinds == [("extrude", "z"), ("extrude", "z"), ("cut", "z"), ("cut", "y")]
    plate, hub, key, screw = plan.steps
    assert plate.dz == 7.0 and hub.z0 == 7.0 and hub.dz == 15.0 and key.dz == 22.0
    assert screw.direction == "-w" and abs(screw.z0 - (hub.loops[0][3] + 1.0)) < 1e-9       # sai de fora do cubo, para dentro
    assert screw.loops == [("c", 0, 7.0 + 7.5, 2.5)]
    # volume = placa + cubo - chaveta - furo de fixacao (valor exato do poligono)
    hub_r, rb = hub.loops[0][3], 8.0
    v = (parts.poly_area(pts) - math.pi * rb ** 2) * 7 + math.pi * (hub_r ** 2 - rb ** 2) * 15
    assert 0.97 * plan.expected_volume < v < 1.03 * plan.expected_volume and plan.expected_volume < v
    assert plan.expected_bbox[1][2] == 22.0 and plan.expected_bbox[0][2] == 0
    nohub = parts.build("chain_sprocket", {"hub_len": 0, "set_screw_d": 0})
    assert [s.kind for s in nohub.steps] == ["extrude", "cut"] and nohub.expected_bbox[1][2] == 7.0
    for bad in ({"teeth": 5}, {"bore": 70}, {"roller_d": 9}, {"hub_od": 80}, {"hub_od": 20}, {"set_screw_d": 14}):
        with _pt.raises(parts.PartError):
            parts.build("chain_sprocket", bad)
