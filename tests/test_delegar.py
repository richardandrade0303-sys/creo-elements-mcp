"""Testes offline da delegacao ao agy: comando fixo, pastas permitidas, codigos de saida, jobs, ferramentas MCP."""

import asyncio
import json
import os
import sys
import time

import pytest

from creo_mcp import delegar

FAKE_SCRIPT = r'''
import json, os, sys, time
args = sys.argv[1:]
files = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
prompt = open(files[0], encoding="utf-8").read()
with open(os.path.join(os.path.dirname(files[0]), "argv.json"), "w", encoding="utf-8") as fh:
    fh.write(json.dumps(args))
if "EXIT3" in prompt:
    sys.stderr.write("cota"); sys.exit(3)
if "BLOQ" in prompt:
    sys.stderr.write("Ferramenta bloqueada: read_file"); sys.exit(1)
if "USO" in prompt:
    sys.stderr.write("uso: node delegar-gemini.mjs instrucao"); sys.exit(2)
if "SLEEP" in prompt:
    time.sleep(30)
if "BIG" in prompt:
    sys.stdout.write("x" * 100000); sys.exit(0)
if "STDIN" in prompt:
    sys.stdout.write("stdin=" + repr(sys.stdin.read())); sys.exit(0)
sys.stdout.write("resposta: %d arquivos; motor=%s" % (len(files) - 1, "gemini" if "gemini" in args else "agy"))
'''


@pytest.fixture
def cfg(tmp_path):
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    script = tmp_path / "fake_delegar.py"
    script.write_text(FAKE_SCRIPT, encoding="utf-8")
    return delegar.Config(project=proj, script=script, roots=[proj], node=sys.executable)


def _argv(res):
    return json.loads(open(os.path.join(res["run_dir"], "argv.json"), encoding="utf-8").read())


# ---------------------------------------------------------------- comando e prompt
def test_build_command_is_fixed_and_prompt_goes_in_a_file(cfg):
    cmd = delegar.build_command(cfg, "P/prompt.txt", ["A/x.py"], "agy")
    assert cmd == [sys.executable, str(cfg.script), delegar.INSTRUCAO_FIXA, "-f", "P/prompt.txt", "-f", "A/x.py"]
    assert delegar.build_command(cfg, "P/prompt.txt", [], "gemini")[-2:] == ["--motor", "gemini"]


def test_script_timeout_is_passed_before_the_local_kill(cfg):
    assert delegar.script_timeout_for(600) == 560 and delegar.script_timeout_for(60) == 20
    assert delegar.script_timeout_for(59) is None and delegar.script_timeout_for(30) is None
    assert delegar.script_timeout_for(1800) == 1760
    cmd = delegar.build_command(cfg, "P/prompt.txt", ["A/x.py"], "gemini", script_timeout=560)
    assert cmd[-4:] == ["--timeout", "560", "--motor", "gemini"]
    assert "--timeout" not in delegar.build_command(cfg, "P/prompt.txt", [], "agy")
    res = delegar.execute(cfg, "ola", timeout_s=300)
    args = _argv(res)
    assert args[args.index("--timeout") + 1] == "260"
    assert "--timeout" not in _argv(delegar.execute(cfg, "ola", timeout_s=40))


def test_prompt_never_reaches_the_command_line(cfg):
    secret = 'texto "com aspas" e $(comando) & | > saida'
    res = delegar.execute(cfg, secret)
    assert res["ok"] and res["status"] == "ok" and res["exit_code"] == 0
    args = _argv(res)
    assert secret not in " ".join(args) and args[0] == delegar.INSTRUCAO_FIXA
    assert open(os.path.join(res["run_dir"], "prompt.txt"), encoding="utf-8").read() == secret


def test_artifacts_and_sentinel(cfg):
    res = delegar.execute(cfg, "ola")
    d = res["run_dir"]
    assert {"prompt.txt", "saida.txt", "erro.txt", "exit.txt"} <= set(os.listdir(d))
    assert open(os.path.join(d, "exit.txt")).read() == "0"
    assert res["output"] == "resposta: 0 arquivos; motor=agy" and "aviso" in res
    assert ".delegacoes" in d


def test_gemini_motor_and_files(cfg):
    f = cfg.project / "a.txt"
    f.write_text("conteudo", encoding="utf-8")
    res = delegar.execute(cfg, "resuma", files=["a.txt"], motor="Gemini")
    assert res["ok"] and res["output"] == "resposta: 1 arquivos; motor=gemini"
    assert _argv(res)[-2:] == ["--motor", "gemini"]


def test_child_stdin_is_closed(cfg):
    t0 = time.time()
    res = delegar.execute(cfg, "STDIN")
    assert res["output"] == "stdin=''" and time.time() - t0 < 10


# ---------------------------------------------------------------- validacao
@pytest.mark.parametrize("prompt", ["", "   ", None, 5])
def test_bad_prompt(cfg, prompt):
    with pytest.raises(delegar.DelegarError):
        delegar.execute(cfg, prompt)


def test_prompt_too_long_and_bad_motor(cfg):
    with pytest.raises(delegar.DelegarError, match="grande demais"):
        delegar.execute(cfg, "a" * (delegar.MAX_PROMPT_CHARS + 1))
    with pytest.raises(delegar.DelegarError, match="motor"):
        delegar.execute(cfg, "ola", motor="bash")
    with pytest.raises(delegar.DelegarError, match="timeout_s"):
        delegar.execute(cfg, "ola", timeout_s="x")


def test_files_must_be_inside_allowed_roots(cfg, tmp_path):
    fora = tmp_path / "fora.txt"
    fora.write_text("x", encoding="utf-8")
    with pytest.raises(delegar.DelegarError, match="fora das pastas permitidas"):
        delegar.validate_files([str(fora)], cfg)
    with pytest.raises(delegar.DelegarError, match="fora das pastas permitidas"):
        delegar.validate_files(["../fora.txt"], cfg)


def test_symlink_cannot_escape_roots(cfg, tmp_path):
    alvo = tmp_path / "alvo.txt"
    alvo.write_text("x", encoding="utf-8")
    link = cfg.project / "atalho.txt"
    try:
        link.symlink_to(alvo)
    except (OSError, NotImplementedError):
        pytest.skip("sem symlink neste sistema")
    with pytest.raises(delegar.DelegarError, match="fora das pastas permitidas"):
        delegar.validate_files([str(link)], cfg)


@pytest.mark.parametrize("nome", [".env", ".env.local", "chave.pem", "api_token.txt", "mcp_config.json",
                                  "claude_desktop_config.json", "minha_senha.txt", "id_rsa", "client_secret.json"])
def test_sensitive_names_are_refused(cfg, nome):
    (cfg.project / nome).write_text("x", encoding="utf-8")
    with pytest.raises(delegar.DelegarError, match="sensivel"):
        delegar.validate_files([nome], cfg)


def test_sensitive_folder_is_refused(cfg):
    d = cfg.project / ".ssh"
    d.mkdir()
    (d / "config").write_text("x", encoding="utf-8")
    with pytest.raises(delegar.DelegarError, match="pasta sensivel"):
        delegar.validate_files([".ssh/config"], cfg)


def test_file_limits(cfg, monkeypatch):
    (cfg.project / "a.txt").write_text("12345", encoding="utf-8")
    with pytest.raises(delegar.DelegarError, match="nao encontrado"):
        delegar.validate_files(["nao_existe.txt"], cfg)
    with pytest.raises(delegar.DelegarError, match="lista"):
        delegar.validate_files("a.txt", cfg)
    monkeypatch.setattr(delegar, "MAX_FILE_BYTES", 3)
    with pytest.raises(delegar.DelegarError, match="grande demais"):
        delegar.validate_files(["a.txt"], cfg)
    monkeypatch.setattr(delegar, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(delegar, "MAX_FILES", 1)
    with pytest.raises(delegar.DelegarError, match="no maximo"):
        delegar.validate_files(["a.txt", "a.txt"], cfg)
    assert delegar.validate_files(["a.txt"], cfg) == [str((cfg.project / "a.txt").resolve())]


def test_missing_node_or_script(cfg, tmp_path):
    cfg.node = None
    with pytest.raises(delegar.DelegarError, match="node nao encontrado"):
        delegar.execute(cfg, "ola")
    cfg.node = sys.executable
    cfg.script = tmp_path / "nao_existe.mjs"
    with pytest.raises(delegar.DelegarError, match="script de delegacao nao encontrado"):
        delegar.execute(cfg, "ola")


def test_find_node_honors_env(tmp_path):
    fake = tmp_path / "meu_node.exe"
    fake.write_text("", encoding="utf-8")
    assert delegar.find_node({"CREO_MCP_NODE": str(fake)}) == str(fake)


# ---------------------------------------------------------------- codigos de saida
def test_quota_exit_code_3(cfg):
    res = delegar.execute(cfg, "EXIT3")
    assert res["ok"] is False and res["status"] == "cota_esgotada" and res["exit_code"] == 3
    assert "nao tente de novo" in res["hint"] and "nao troque de conta" in res["hint"]


def test_blocked_tool_hint(cfg):
    res = delegar.execute(cfg, "BLOQ")
    assert res["status"] == "erro" and "bloqueou uma ferramenta" in res["hint"]


def test_usage_error_code_2(cfg):
    res = delegar.execute(cfg, "USO")
    assert res["status"] == "uso_incorreto" and "delegar_diagnostico" in res["hint"]


def test_local_timeout_kills_child_and_returns(cfg, monkeypatch):
    monkeypatch.setattr(delegar, "TIMEOUT_MIN", 1)
    t0 = time.time()
    res = delegar.execute(cfg, "SLEEP", timeout_s=1)
    assert res["status"] == "timeout_local" and res["exit_code"] is None and res["ok"] is False
    assert time.time() - t0 < 15 and "Tempo esgotado" in res["hint"]
    assert open(os.path.join(res["run_dir"], "exit.txt")).read() == "timeout"


def test_timeout_is_clamped(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(delegar, "_execute", lambda c, p, f, m, t: seen.setdefault("t", t) and {"ok": True})
    delegar.execute(cfg, "ola", timeout_s=1)
    assert seen["t"] == delegar.TIMEOUT_MIN
    seen.clear()
    delegar.execute(cfg, "ola", timeout_s=10**6)
    assert seen["t"] == delegar.TIMEOUT_MAX


def test_output_is_capped(cfg):
    res = delegar.execute(cfg, "BIG")
    assert res["truncated"] is True and len(res["output"]) == delegar.MAX_OUTPUT_CHARS


# ---------------------------------------------------------------- jobs
def test_job_runs_in_background_and_reports(cfg):
    delegar._jobs.clear()
    job = delegar.start(cfg, "ola")
    res = delegar.wait(job, 15)
    assert res["ok"] and res["job"] == job["id"] and res["output"].startswith("resposta")
    assert delegar.get(job["id"]) is job and delegar.get("") is job and delegar.get("zzz") is None


def test_wait_returns_running_state_for_slow_jobs(cfg, monkeypatch):
    delegar._jobs.clear()
    monkeypatch.setattr(delegar, "TIMEOUT_MIN", 1)
    job = delegar.start(cfg, "SLEEP", timeout_s=2)
    quick = delegar.wait(job, 0.5)
    assert quick["state"] == "running" and quick["job"] == job["id"]
    final = delegar.wait(job, 20)
    assert final["status"] == "timeout_local"


def test_concurrency_cap(cfg, monkeypatch):
    delegar._jobs.clear()
    monkeypatch.setattr(delegar, "TIMEOUT_MIN", 1)
    monkeypatch.setattr(delegar, "MAX_RUNNING", 1)
    job = delegar.start(cfg, "SLEEP", timeout_s=2)
    with pytest.raises(delegar.DelegarError, match="em andamento"):
        delegar.start(cfg, "ola")
    delegar.wait(job, 20)
    assert delegar.wait(delegar.start(cfg, "ola"), 15)["ok"]


def test_validation_errors_are_synchronous_in_start(cfg):
    delegar._jobs.clear()
    with pytest.raises(delegar.DelegarError):
        delegar.start(cfg, "")
    assert not delegar._jobs


# ---------------------------------------------------------------- diagnostico e ferramentas MCP
def test_diagnostico_redacts_secret_looking_lines(cfg):
    cfg.script.write_text('// uso: node x.mjs "instrucao" -f arquivo\nconst API_KEY = "abc123";\nconst a = 1;\n',
                          encoding="utf-8")
    d = delegar.diagnostico(cfg)
    assert d["script_existe"] and d["node"] == sys.executable
    assert "abc123" not in d["script_inicio"] and "possivel segredo" in d["script_inicio"]
    assert "uso: node x.mjs" in d["script_inicio"] and str(cfg.project) in d["pastas_permitidas"]


def test_diagnostico_reports_what_is_missing(cfg, tmp_path):
    cfg.node = None
    cfg.script = tmp_path / "nada.mjs"
    d = delegar.diagnostico(cfg)
    assert d["pronto"] is False and any("node" in x for x in d["falta"]) and any("delegar-gemini" in x for x in d["falta"])


def test_server_registers_delegation_tools():
    from creo_mcp.server import server

    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert {"delegar_agy", "delegar_resultado", "delegar_diagnostico"} <= set(tools)
    def hint(tool, snake, camel):  # o nome do atributo varia entre versoes do pacote mcp
        ann = tools[tool].annotations
        assert ann is not None
        return getattr(ann, snake) if hasattr(ann, snake) else getattr(ann, camel)

    assert hint("delegar_agy", "read_only_hint", "readOnlyHint") is False
    assert hint("delegar_agy", "open_world_hint", "openWorldHint") is True
    assert hint("delegar_resultado", "read_only_hint", "readOnlyHint") is True


def test_server_tool_end_to_end(cfg, monkeypatch):
    from creo_mcp import server

    delegar._jobs.clear()
    monkeypatch.setattr(server.delegar, "default_config", lambda: cfg)
    out = json.loads(server.delegar_agy("ola", files="[]", motor="agy", timeout_s=60))
    assert out["ok"] is True and out["output"].startswith("resposta")
    bad = json.loads(server.delegar_agy("ola", files='["../fora.txt"]'))
    assert bad["ok"] is False and "fora das pastas permitidas" in bad["error"]
    bad_json = json.loads(server.delegar_agy("ola", files="nao e json"))
    assert bad_json["ok"] is False
    again = json.loads(server.delegar_resultado(""))
    assert again["ok"] is True
    assert json.loads(server.delegar_resultado("zzz"))["ok"] is False
    assert json.loads(server.delegar_diagnostico())["node"] == sys.executable
