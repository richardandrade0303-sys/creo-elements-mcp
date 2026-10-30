"""Utilitarios Win32 (ctypes) para enxergar e controlar as janelas do Creo.

Serve para: achar o processo, listar janelas, ler e fechar dialogos de erro
("LISP error") que bloqueiam o Creo, e tirar screenshot da janela principal.
Tudo best-effort e sem dependencias externas.
"""

from __future__ import annotations

import csv
import os
import struct
import subprocess
import sys
import time
import zlib

IS_WINDOWS = sys.platform == "win32"
PROCESS_NAMES = ("soliddesigner.exe",)

WM_GETTEXT = 0x000D
WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
SMTO_ABORTIFHUNG = 0x0002
GW_OWNER = 4

OK_LABELS = ("ok", "yes", "sim", "close", "fechar", "continue", "continuar", "abort", "ignore")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    _user32.SendMessageTimeoutW.restype = ctypes.c_size_t
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _H = ctypes.c_void_p
    _user32.GetDC.argtypes = [_H]
    _user32.GetDC.restype = _H
    _user32.ReleaseDC.argtypes = [_H, _H]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _gdi32.CreateCompatibleDC.argtypes = [_H]
    _gdi32.CreateCompatibleDC.restype = _H
    _gdi32.CreateCompatibleBitmap.argtypes = [_H, ctypes.c_int, ctypes.c_int]
    _gdi32.CreateCompatibleBitmap.restype = _H
    _gdi32.SelectObject.argtypes = [_H, _H]
    _gdi32.SelectObject.restype = _H
    _gdi32.BitBlt.argtypes = [_H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              _H, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    _gdi32.GetDIBits.argtypes = [_H, _H, wintypes.UINT, wintypes.UINT, ctypes.c_char_p, _H, wintypes.UINT]
    _gdi32.DeleteObject.argtypes = [_H]
    _gdi32.DeleteDC.argtypes = [_H]
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------- processo
_PID_CACHE: tuple[float, list[int]] = (0.0, [])
_PID_TTL = 5.0  # tasklist e lento (~1 s); o PID do Creo quase nunca muda


def creo_pids(use_cache: bool = True) -> list[int]:
    """PIDs de SolidDesigner.exe em execucao (com cache curto)."""
    global _PID_CACHE
    if not IS_WINDOWS:
        return []
    ts, cached = _PID_CACHE
    if use_cache and cached and time.time() - ts < _PID_TTL:
        return list(cached)
    pids = _creo_pids_uncached()
    _PID_CACHE = (time.time(), pids)
    return list(pids)


def _creo_pids_uncached() -> list[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout
    except Exception:
        return []
    pids = []
    for row in csv.reader(out.splitlines()):
        if len(row) >= 2 and row[0].lower() in PROCESS_NAMES:
            try:
                pids.append(int(row[1]))
            except ValueError:
                pass
    return pids


# ----------------------------------------------------------------- janelas
def _text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    res = ctypes.c_size_t(0)
    ok = _user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, 1024, ctypes.cast(buf, ctypes.c_void_p).value or 0,
                                     SMTO_ABORTIFHUNG, 500, ctypes.byref(res))
    return buf.value if ok else ""


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def top_windows(pids: list[int] | None = None) -> list[dict]:
    """Janelas de topo visiveis dos processos do Creo."""
    if not IS_WINDOWS:
        return []
    pids = pids if pids is not None else creo_pids()
    found: list[dict] = []

    def cb(hwnd, _lp):
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and _user32.IsWindowVisible(hwnd):
            found.append({
                "hwnd": int(hwnd),
                "pid": pid.value,
                "title": _text(hwnd),
                "class": _class(hwnd),
                "enabled": bool(_user32.IsWindowEnabled(hwnd)),
                "owner": int(_user32.GetWindow(hwnd, GW_OWNER) or 0),
                "rect": _rect(hwnd),
            })
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return found


def main_window(windows: list[dict] | None = None) -> dict | None:
    """Janela principal (sem dono, maior area, com titulo)."""
    windows = windows if windows is not None else top_windows()
    cands = [w for w in windows if not w["owner"] and w["title"]]
    if not cands:
        return None

    def area(w):
        l, t, r, b = w["rect"]
        return max(r - l, 0) * max(b - t, 0)

    return max(cands, key=area)


def dialogs(windows: list[dict] | None = None) -> list[dict]:
    """Dialogos modais: so existem quando a janela principal esta desabilitada.

    (Paletas/barras flutuantes do Creo tambem sao janelas com dono, mas nao
    desabilitam a principal - por isso nunca sao tratadas como dialogo.)
    """
    windows = windows if windows is not None else top_windows()
    main = main_window(windows)
    if main is not None and main["enabled"]:
        return []
    out = []
    for w in windows:
        if main is not None and w["hwnd"] == main["hwnd"]:
            continue
        # Somente dialogos padrao do Windows (#32770). Janelas como o "Draw Panel" da extrusao
        # (titulo 'SolidDesigner', classe propria) fazem parte do comando ativo: fecha-las com WM_CLOSE
        # deixa o Creo preso numa cadeia infinita de 'Workplane is not specified'.
        if w["class"] == "#32770":
            out.append(w)
    return out


def children(hwnd: int) -> list[dict]:
    """Controles filhos (classe + texto) de uma janela."""
    found: list[dict] = []

    def cb(h, _lp):
        found.append({"hwnd": int(h), "class": _class(h), "text": _text(h)})
        return True

    _user32.EnumChildWindows(hwnd, _WNDENUMPROC(cb), 0)
    return found


def describe_dialog(win: dict) -> dict:
    kids = children(win["hwnd"])
    texts = [k["text"].strip() for k in kids if k["text"].strip() and "button" not in k["class"].lower()]
    buttons = [k for k in kids if "button" in k["class"].lower() and k["text"].strip()]
    return {
        "hwnd": win["hwnd"],
        "title": win["title"],
        "class": win["class"],
        "text": "\n".join(texts),
        "buttons": [b["text"] for b in buttons],
        "controls": [f'{k["class"]}:{k["text"][:40]}' for k in kids][:30],
    }


def is_fatal(info: dict) -> bool:
    blob = (info["title"] + " " + info["text"]).lower()
    return "fatal" in blob


VK_ESCAPE = 0x1B
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
DEFAULT_DRAIN = 20.0  # segundos que `dismiss` continua fechando dialogos encadeados


# ------------------------------------------------- dialogo de arquivo (Load/Open/Save)
WM_SETTEXT = 0x000C
FILE_DIALOG_TITLES = ("load", "open", "save", "save as", "abrir", "carregar", "salvar", "import", "export")
FILE_BUTTON_LABELS = ("load", "open", "abrir", "carregar", "save", "salvar")
PENDING_FILL = os.path.join(os.path.expanduser("~"), ".creo_mcp", "dialog_fill.json")
FILL_RESULT = os.path.join(os.path.expanduser("~"), ".creo_mcp", "dialog_fill_result.json")
FILL_TTL_S = 180.0


def _ancestor_classes(hwnd, stop) -> list[str]:
    out: list[str] = []
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    h = _user32.GetParent(hwnd)
    n = 0
    while h and int(h) != int(stop) and n < 12:
        out.append(_class(h).lower())
        h = _user32.GetParent(h)
        n += 1
    return out


def file_dialog_info(win: dict) -> dict:
    """Controles do dialogo de arquivo com id, classe, texto e pais (para diagnostico)."""
    _user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    rows = []
    for k in children(win["hwnd"]):
        rows.append({"hwnd": k["hwnd"], "class": k["class"], "text": k["text"][:60],
                     "id": int(_user32.GetDlgCtrlID(k["hwnd"])), "visible": bool(_user32.IsWindowVisible(k["hwnd"])),
                     "parents": _ancestor_classes(k["hwnd"], win["hwnd"])})
    return {"title": win["title"], "class": win["class"], "controls": rows}


def _read_pending() -> dict | None:
    import json

    try:
        with open(PENDING_FILL, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("path"):
        return None
    if time.time() - float(data.get("created", 0)) > FILL_TTL_S:
        return None
    return data


def _write_fill_result(info: dict) -> None:
    import json

    try:
        os.makedirs(os.path.dirname(FILL_RESULT), exist_ok=True)
        with open(FILL_RESULT, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def is_file_dialog(win: dict) -> bool:
    """Dialogo comum de arquivo: classe #32770 com titulo de abrir/salvar e um campo Edit."""
    if win.get("class") != "#32770":
        return False
    if (win.get("title") or "").strip().lower() not in FILE_DIALOG_TITLES:
        return False
    return any(k["class"].lower() == "edit" for k in children(win["hwnd"]))


def _dblclick_first_item(win: dict, x: int = 30, y: int = 12) -> bool:
    """Duplo clique (mensagens de mouse) no primeiro item da lista de arquivos do dialogo."""
    lists = [k for k in children(win["hwnd"]) if k["class"].lower() == "syslistview32" and _user32.IsWindowVisible(k["hwnd"])]
    if not lists:
        return False
    h = lists[0]["hwnd"]
    lp = (y << 16) | x
    for msg, wp in ((0x0201, 1), (0x0202, 0), (0x0203, 1), (0x0202, 0)):   # DOWN, UP, DBLCLK, UP
        _user32.PostMessageW(h, msg, wp, lp)
        time.sleep(0.05)
    return True


def fill_file_dialog(win: dict, path: str, press: bool = True, mode: str = "text") -> dict:
    """Digita `path` no campo 'File name' e aperta Load/Open/Save (mode 'text'); com mode 'dblclick' digita e depois da duplo
    clique no primeiro item da lista. Devolve o relatorio (tambem gravado em FILL_RESULT)."""
    kids = children(win["hwnd"])
    edits = [k for k in kids if k["class"].lower() == "edit" and _user32.IsWindowVisible(k["hwnd"])]
    best = None
    for k in edits:
        if any(c.startswith("combobox") for c in _ancestor_classes(k["hwnd"], win["hwnd"])):
            best = k
            break
    if best is None and edits:
        best = edits[0]
    report: dict = {"ok": False, "path": path, "title": win["title"], "edits": len(edits)}
    if best is None:
        report["error"] = "campo 'File name' nao encontrado"
        report["info"] = file_dialog_info(win)
        _write_fill_result(report)
        return report
    if mode == "dblclick_only":          # so seleciona na lista (sem digitar): o nome vem do proprio dialogo
        report["ok"] = _dblclick_first_item(win)
        report["button"] = "duplo clique no 1o item (sem digitar)"
        time.sleep(0.3)
        report["after_text"] = _text(best["hwnd"])
        _write_fill_result(report)
        return report
    buf = ctypes.create_unicode_buffer(path)
    res = ctypes.c_size_t(0)
    _user32.SendMessageTimeoutW(best["hwnd"], WM_SETTEXT, 0, ctypes.addressof(buf), SMTO_ABORTIFHUNG, 2000, ctypes.byref(res))
    time.sleep(0.2)
    got = _text(best["hwnd"])
    report["typed"] = got
    buttons = [k for k in kids if "button" in k["class"].lower()]
    target = None
    for k in buttons:
        if k["text"].replace("&", "").strip().lower() in FILE_BUTTON_LABELS:
            target = k
            break
    report["button"] = target["text"] if target else None
    if got.replace("\\", "/") != path.replace("\\", "/"):
        report["error"] = "o texto digitado nao confere com o caminho"
        report["info"] = file_dialog_info(win)
        _write_fill_result(report)
        return report
    if press and mode == "dblclick":
        report["ok"] = _dblclick_first_item(win)
        report["button"] = "duplo clique no 1o item"
    elif press and target is not None:
        _user32.PostMessageW(target["hwnd"], BM_CLICK, 0, 0)
        report["ok"] = True
    elif press:
        _user32.PostMessageW(win["hwnd"], 0x0111, 1, 0)  # WM_COMMAND IDOK
        report["ok"] = True
        report["button"] = "IDOK"
    else:
        report["ok"] = True
    report["info"] = file_dialog_info(win)
    _write_fill_result(report)
    return report


def _consume_pending_fill(win: dict) -> str | None:
    """Se houver pedido de preenchimento valido e `win` for um dialogo de arquivo, preenche e apaga o pedido."""
    data = _read_pending()
    if not data or not is_file_dialog(win):
        return None
    wanted = [str(t).lower() for t in data.get("titles", [])]
    if wanted and (win.get("title") or "").strip().lower() not in wanted:
        return None
    try:
        os.remove(PENDING_FILL)
    except OSError:
        pass
    rep = fill_file_dialog(win, str(data["path"]), press=bool(data.get("press", True)), mode=str(data.get("mode", "text")))
    return ("preencheu o dialogo de arquivo e apertou '%s'" % rep.get("button")) if rep.get("ok") else ("falha: %s" % rep.get("error"))


def _click_ok(win: dict) -> str:
    if IS_WINDOWS:
        done = _consume_pending_fill(win)
        if done:
            return done
        if is_file_dialog(win):        # sem pedido de preenchimento: fecha (Cancel faz o Creo reabrir o dialogo em laco; IDOK confirma)
            _user32.PostMessageW(win["hwnd"], WM_CLOSE, 0, 0)
            return "fechou o dialogo de arquivo (WM_CLOSE)"
    kids = children(win["hwnd"])
    buttons = [k for k in kids if "button" in k["class"].lower()]

    def label(k):
        return k["text"].replace("&", "").strip().lower()

    for wanted in OK_LABELS:
        for b in buttons:
            if label(b) == wanted:
                res = ctypes.c_size_t(0)
                _user32.SendMessageTimeoutW(b["hwnd"], BM_CLICK, 0, 0, SMTO_ABORTIFHUNG, 500, ctypes.byref(res))
                _user32.PostMessageW(win["hwnd"], 0x0111, 1, b["hwnd"])
                _user32.PostMessageW(win["hwnd"], WM_CLOSE, 0, 0)
                return f"clicou '{b['text']}'"
    if win.get("class") == "#32770":
        _user32.PostMessageW(win["hwnd"], 0x0111, 1, 0)
        _user32.PostMessageW(win["hwnd"], WM_CLOSE, 0, 0)
        return "enviou IDOK+WM_CLOSE"
    return "ignorado (nao e um dialogo padrao)"


def send_escape() -> bool:
    """Envia ESC real via keybd_event e PostMessage para cancelar o comando ativo."""
    main = main_window()
    if not main:
        return False
    try:
        _user32.keybd_event(VK_ESCAPE, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(VK_ESCAPE, 0, 2, 0)
    except Exception:
        pass
    _user32.PostMessageW(main["hwnd"], WM_KEYDOWN, VK_ESCAPE, 0)
    _user32.PostMessageW(main["hwnd"], WM_KEYUP, VK_ESCAPE, 0xC0000001)
    return True


def cancel_active_command_dialogs() -> int:
    """Cancela ferramentas ativas do Creo (ex.: Extrude, Draw Panel) enviando ESC e fechando paineis."""
    pids = creo_pids()
    windows = top_windows(pids)
    main = main_window(windows)
    main_hwnd = main["hwnd"] if main else 0
    canceled = 0
    for w in windows:
        if w["hwnd"] == main_hwnd:
            continue
        title = w["title"].strip()
        if title and w["class"] != "#32770":
            for k in children(w["hwnd"]):
                if "button" in k["class"].lower():
                    lbl = k["text"].strip().lower()
                    if lbl in ("cancel", "cancelar", "close", "fechar", "x"):
                        _user32.PostMessageW(k["hwnd"], BM_CLICK, 0, 0)
                        canceled += 1
            _user32.PostMessageW(w["hwnd"], WM_KEYDOWN, VK_ESCAPE, 0)
            _user32.PostMessageW(w["hwnd"], WM_KEYUP, VK_ESCAPE, 0xC0000001)
            canceled += 1
    if main_hwnd:
        try:
            _user32.SetForegroundWindow(main_hwnd)
            time.sleep(0.03)
            for _ in range(3):
                _user32.keybd_event(VK_ESCAPE, 0, 0, 0)
                time.sleep(0.02)
                _user32.keybd_event(VK_ESCAPE, 0, 2, 0)
                time.sleep(0.02)
        except Exception:
            pass
        _user32.PostMessageW(main_hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
        _user32.PostMessageW(main_hwnd, WM_KEYUP, VK_ESCAPE, 0xC0000001)
    return canceled


DRAIN_ESC = False  # Desativado: ESC entre dialogos reabre prompts e gera lacos infinitos de erro


def drain(seconds: float = DEFAULT_DRAIN, esc: bool = DRAIN_ESC, quiet_s: float = 1.0) -> dict:
    """Fecha dialogos em sequencia rapida (cadeias de erro).

    Para quando ficar `quiet_s` segundos sem nenhum dialogo ou quando `seconds` acabar.
    """
    t_end = time.time() + seconds
    closed = 0
    escs = 0
    quiet_since = None
    while time.time() < t_end:
        wins = dialogs()
        fatal = [w for w in wins if is_fatal(describe_dialog(w))]
        if fatal:
            return {"closed": closed, "escapes": escs, "stopped": "dialogo fatal - nao fechado"}
        if wins:
            quiet_since = None
            for w in wins:
                _click_ok(w)
                closed += 1
            cancel_active_command_dialogs()
            time.sleep(0.2)
        else:
            if esc and escs < 2:
                escs += int(send_escape())
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= quiet_s:
                return {"closed": closed, "escapes": escs, "stopped": "sem mais dialogos"}
            time.sleep(0.1)
    return {"closed": closed, "escapes": escs, "stopped": "tempo esgotado (ainda pode haver dialogos)"}


def dismiss(win: dict, drain_s: float | None = None) -> str:
    """Fecha o dialogo (OK/Yes/Close ou WM_CLOSE); depois drena a cadeia de dialogos por `drain_s` s."""
    action = _click_ok(win)
    cancel_active_command_dialogs()
    seconds = DEFAULT_DRAIN if drain_s is None else drain_s
    if seconds > 0:
        action += f"; drain={drain(seconds)}"
    return action


# --------------------------------------------------------------- screenshot
def _png(width: int, height: int, bgra: bytes) -> bytes:
    n = width * height
    rgb = bytearray(3 * n)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def screenshot(path: str, hwnd: int | None = None) -> dict:
    """Copia a area da janela principal do Creo da tela e salva como PNG."""
    if not IS_WINDOWS:
        return {"ok": False, "error": "screenshot so funciona no Windows"}
    win = None
    if hwnd is None:
        win = main_window()
        if win is None:
            return {"ok": False, "error": "janela do Creo nao encontrada"}
        hwnd = win["hwnd"]
    try:
        _user32.keybd_event(0x12, 0, 0, 0)
        _user32.keybd_event(0x12, 0, 2, 0)
    except Exception:
        pass
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _user32.BringWindowToTop(hwnd)
    _user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    l, t, r, b = _rect(hwnd)
    w, h = max(r - l, 1), max(b - t, 1)

    hdc_screen = _user32.GetDC(0)
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = _gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    _gdi32.SelectObject(hdc_mem, hbmp)
    _gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, l, t, 0x00CC0020 | 0x40000000)  # SRCCOPY|CAPTUREBLT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(0, hdc_screen)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(_png(w, h, buf.raw))
    return {"ok": True, "path": path, "width": w, "height": h}
