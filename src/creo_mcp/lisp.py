"""Execucao de Lisp no Creo via DDE, com captura de resultado e de dialogos de erro.

Protocolo (v2.1, "file-load"):
  1. O codigo do usuario vai para um arquivo  ~/.creo_mcp/cmd/cmd_<ts>.lsp  que grava o
     valor da ultima forma (impresso com ~S) em  cmd_<ts>.out  seguido do marcador #END#.
  2. Por DDE Execute enviamos UMA linha:  (load "<arquivo>.lsp")
     (strings multi-linha nao sao confiaveis no Execute do Creo).
  3. Lemos o .out. Nao usamos DDE Request: pedir uma variavel nao definida abre um
     dialogo de erro modal no Creo.
Se o Lisp der erro, o Creo abre um dialogo modal "LISP error": um vigia (thread) detecta,
le o texto, fecha o dialogo e o texto vai no resultado.
"""

from __future__ import annotations

import os
import re
import threading
import time

from . import dde, winui

LAYER = "2.1-file-load"
END_MARK = "#END#"
SETTLE_AFTER_DIALOG = 6.0  # apos aparecer um dialogo, espera tanto tempo pelo resultado antes de desistir

HOME = os.path.join(os.path.expanduser("~"), ".creo_mcp")
CMD_DIR = os.path.join(HOME, "cmd")

_LOCK = threading.Lock()  # uma conversacao DDE por vez


# ----------------------------------------------------------- helpers de Lisp
def lisp_str(value: str) -> str:
    """Literal de string Lisp."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lisp_path(path: str) -> str:
    """Literal de string Lisp para um caminho (barras normais)."""
    return lisp_str(path.replace("\\", "/"))


def num(value: float) -> str:
    """Numero em notacao decimal simples (sem expoente)."""
    text = f"{float(value):.8f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def build_cmd_file(code: str, out_path: str) -> str:
    """Conteudo do arquivo .lsp que avalia `code` e grava o resultado em `out_path`."""
    return (
        f"(let ((mcp_val (progn\n{code}\n)))\n"
        f"  (with-open-file (mcp_s {lisp_path(out_path)} :direction :output :if-exists :supersede)\n"
        f'    (format mcp_s "~S~%{END_MARK}~%" mcp_val)))\n'
    )


def _cleanup(max_age_s: float = 3600.0) -> None:
    try:
        now = time.time()
        for name in os.listdir(CMD_DIR):
            path = os.path.join(CMD_DIR, name)
            if now - os.path.getmtime(path) > max_age_s:
                os.remove(path)
    except OSError:
        pass


def _read_out(path: str) -> str | None:
    """Le o .out; devolve None enquanto o marcador final nao aparecer."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    for enc in ("utf-8", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n")
    marker = "\n" + END_MARK
    if marker not in text:
        return None
    return text.split(marker, 1)[0]


# ------------------------------------------------------------------- vigia
class _Watchdog(threading.Thread):
    """Enquanto um comando roda, detecta e (opcionalmente) fecha dialogos do Creo."""

    def __init__(self, auto_dismiss: bool):
        super().__init__(daemon=True)
        self.auto_dismiss = auto_dismiss
        self.seen: dict[int, dict] = {}
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.wait(0.4):
            try:
                self.scan()
            except Exception:
                pass

    def scan(self) -> None:
        for win in winui.dialogs():
            if win["hwnd"] in self.seen:
                continue
            info = winui.describe_dialog(win)
            if self.auto_dismiss and not winui.is_fatal(info):
                info["action"] = winui.dismiss(win, drain_s=0)
            else:
                info["action"] = "nao fechado"
            self.seen[win["hwnd"]] = info

    def finish(self) -> list[dict]:
        self._stop_evt.set()
        self.join(timeout=2)
        try:
            self.scan()
        except Exception:
            pass
        return list(self.seen.values())


_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')


def _atom(tok: str):
    if tok.startswith('"'):
        return re.sub(r"\\(.)", r"\1", tok[1:-1])
    up = tok.upper()
    if up == "NIL":
        return None
    if up == "T":
        return True
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    if "," in tok:  # ponto 3D/2D do Creo, ex.: 0.0,0.0,20.0
        try:
            return [float(p) for p in tok.split(",")]
        except ValueError:
            pass
    return tok  # simbolo / keyword (ex.: :PART)


def parse_sexp(text: str):
    """Le o texto Lisp impresso (~S) e devolve listas/strings/numeros Python. NIL -> None, () -> []."""
    tokens = _TOKEN.findall(text or "")
    pos = 0

    def read():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            items = []
            while pos < len(tokens) and tokens[pos] != ")":
                items.append(read())
            pos += 1  # ")"
            return items
        return _atom(tok)

    if not tokens:
        return None
    return read()


def summarize_dialogs(dialogs: list[dict]) -> list[dict]:
    """Resumo enxuto (economiza tokens): agrupa dialogos iguais e tira os detalhes dos controles."""
    groups: dict[tuple, dict] = {}
    for d in dialogs:
        text = " ".join((d.get("text") or "").replace("Icon\nStatic\n", "").split())
        key = (d.get("title"), text)
        if key not in groups:
            groups[key] = {"title": d.get("title"), "text": text, "count": 0, "action": d.get("action")}
        groups[key]["count"] += 1
    return list(groups.values())


# --------------------------------------------------------------------- run
def run(code: str, timeout: float = 30.0, auto_dismiss: bool = True) -> dict:
    """Executa `code` (uma ou mais formas Lisp) no Creo e devolve um dict:

    ok, result (valor da ultima forma impresso com ~S), error, dialogs, elapsed, cmd_file
    """
    t0 = time.time()
    out: dict = {"ok": False, "result": None, "error": None, "dialogs": [], "elapsed": 0.0}
    marks: dict[str, float] = {}
    os.makedirs(CMD_DIR, exist_ok=True)
    _cleanup()
    stem = f"cmd_{int(t0 * 1000)}"
    cmd_path = os.path.join(CMD_DIR, stem + ".lsp")
    out_path = os.path.join(CMD_DIR, stem + ".out")
    with open(cmd_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_cmd_file(code, out_path))
    out["cmd_file"] = cmd_path

    with _LOCK:
        if auto_dismiss:  # dialogos antigos (ex.: cadeia de erros) nao podem contaminar este comando
            stale = winui.drain(8.0, esc=False, quiet_s=0.3)
            if stale["closed"]:
                out["stale_dialogs_closed"] = stale["closed"]
        marks["predrain"] = time.time() - t0
        dog = _Watchdog(auto_dismiss)
        dog.start()
        try:
            exec_error = None
            t_exec = time.time()
            try:
                with dde.DdeClient() as client:
                    client.execute(f"(load {lisp_path(cmd_path)})", int(timeout * 1000))
            except dde.DdeError as exc:
                exec_error = str(exc)
            marks["dde_execute"] = time.time() - t_exec
            if exec_error:  # sem conversacao/ack: so uma olhada rapida no arquivo
                deadline = time.time() + 0.5
            else:
                deadline = max(t0 + timeout, time.time() + 3.0)
            dialog_seen_at = None
            value = _read_out(out_path)
            while value is None and time.time() < deadline:
                time.sleep(0.1)
                value = _read_out(out_path)
                if dog.seen and dialog_seen_at is None:
                    dialog_seen_at = time.time()
                if dialog_seen_at and time.time() - dialog_seen_at > SETTLE_AFTER_DIALOG:
                    value = _read_out(out_path)
                    break
            if value is not None:
                out["result"], out["ok"] = value, True
            elif exec_error:
                out["error"] = exec_error
            elif dog.seen:
                out["error"] = "O Lisp deu erro (veja 'dialogs')."
            else:
                out["error"] = (
                    "O Creo aceitou o comando DDE mas o resultado nao apareceu a tempo "
                    f"(arquivo de comando: {cmd_path}). Veja creo_screenshot."
                )
        finally:
            t_fin = time.time()
            full = dog.finish()
            marks["watchdog_finish"] = time.time() - t_fin
            out["dialogs"] = summarize_dialogs(full)
            out["elapsed"] = round(time.time() - t0, 2)
            out["timing"] = {k: round(v, 2) for k, v in marks.items()}
    for path in (cmd_path, out_path):
        try:
            os.remove(path)
        except OSError:
            pass
    if out["dialogs"] and out["ok"]:
        out["note"] = "O comando terminou, mas apareceram dialogos do Creo (veja 'dialogs')."
    return out


def raw(execute: str = "", request: str = "", timeout: float = 20.0, auto_dismiss: bool = True) -> dict:
    """Diagnostico: envia um DDE Execute e/ou Request crus (sem arquivo/embrulho)."""
    t0 = time.time()
    out: dict = {"ok": False, "execute": execute, "request": request, "value": None, "error": None}
    with _LOCK:
        dog = _Watchdog(auto_dismiss)
        dog.start()
        try:
            with dde.DdeClient() as client:
                if execute:
                    client.execute(execute, int(timeout * 1000))
                if request:
                    out["value"] = client.request(request, int(timeout * 1000))
            out["ok"] = True
        except dde.DdeError as exc:
            out["error"] = str(exc)
        finally:
            out["dialogs"] = dog.finish()
            out["elapsed"] = round(time.time() - t0, 2)
    return out
