"""Troca de dados: exportacao com manifesto (SHA-256), validacao de arquivos, lista de materiais (CSV/XLSX/JSON), relatorio
HTML e atributos de peca (arquivo auxiliar). Funcoes puras: quem fala com o Creo e `actions.py`.

STL e verificado ao vivo. STEP e SAT (ACIS) existem no Express 9.0 como STEP_EXPORT e SAVE_SAT, mas a sintaxe dos argumentos
so e conhecida pelo `:VARIABLES-UI` do simbolo (lido com `symbol-plist`, seguro): `variables_from_plist` extrai os nomes e
`export_command` monta o comando a partir deles. Tudo que depende disso e marcado `experimental` ate o `creo_validate`.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import html
import io
import json
import os
import re
import zipfile

from . import config
from .parts import PartError

MAX_STEP_NAME = 65          # mensagem do Creo: "Step File Name Exceeds 65 Characters"
FORMATS = {"stl": ".stl", "step": ".stp", "sat": ".sat"}
ATTR_FILE = os.path.join(config.HOME, "attributes.json")


# ------------------------------------------------------------------ arquivos
def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def file_info(path: str) -> dict:
    st = os.stat(path)
    return {"path": path, "bytes": st.st_size, "sha256": sha256_file(path),
            "modified": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


def check_name(path: str, fmt: str) -> None:
    """Valida o destino: extensao coerente e limite de 65 caracteres do nome do arquivo STEP."""
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise PartError(f"formato {fmt!r}: use {sorted(FORMATS)}")
    base = os.path.basename(path)
    if not base or base.startswith("."):
        raise PartError("informe um arquivo de destino")
    if fmt == "step" and len(base) > MAX_STEP_NAME:
        raise PartError(f"o Creo recusa nomes de arquivo STEP com mais de {MAX_STEP_NAME} caracteres ({len(base)})")
    ext = os.path.splitext(base)[1].lower()
    ok = {".stl"} if fmt == "stl" else ({".stp", ".step"} if fmt == "step" else {".sat"})
    if ext not in ok:
        raise PartError(f"extensao {ext or '(nenhuma)'} nao combina com {fmt}: use {sorted(ok)[0]}")


def validate_file(path: str, fmt: str) -> dict:
    """Confere o conteudo do arquivo exportado (cabecalho e contagens). Devolve {ok, checks...}."""
    fmt = fmt.lower()
    out: dict = {"format": fmt, "ok": False}
    if not os.path.isfile(path):
        out["error"] = "arquivo nao existe"
        return out
    size = os.path.getsize(path)
    out["bytes"] = size
    if size == 0:
        out["error"] = "arquivo vazio"
        return out
    if fmt == "step":
        with open(path, "rb") as fh:
            head = fh.read(4096).decode("latin-1", "replace")
            fh.seek(max(size - 4096, 0))
            tail = fh.read().decode("latin-1", "replace")
        out["header_ok"] = head.lstrip().startswith("ISO-10303-21")
        out["end_ok"] = "END-ISO-10303-21" in tail
        m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", head)
        out["schema"] = m.group(1) if m else None
        text = open(path, "rb").read().decode("latin-1", "replace")
        out["products"] = len(re.findall(r"=\s*PRODUCT\s*\(", text))
        out["solids"] = len(re.findall(r"=\s*(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(", text))
        out["ok"] = bool(out["header_ok"] and out["end_ok"] and out["solids"] >= 1)
    elif fmt == "sat":
        with open(path, "rb") as fh:
            head = fh.read(2048).decode("latin-1", "replace")
        first = head.splitlines()[0].split() if head.splitlines() else []
        out["version_line"] = " ".join(first[:3])
        out["header_ok"] = len(first) >= 3 and all(re.fullmatch(r"\d+", t) for t in first[:3])
        out["bodies"] = len(re.findall(r"(?m)^\s*body\b", open(path, "rb").read().decode("latin-1", "replace")))
        out["ok"] = bool(out["header_ok"] and out["bodies"] >= 1)
    elif fmt == "stl":
        from . import stl as _stl

        info = _stl.read_stl(path)
        out.update(triangles=info["triangles"], volume_mm3=info["volume"], size=info["size"])
        out["ok"] = info["triangles"] > 0
    else:
        out["error"] = f"formato {fmt!r} sem validador"
    return out


def manifest(entries: list, extra: dict | None = None) -> dict:
    """Manifesto de um lote de exportacao: servidor, sessao, unidades e, por arquivo, tamanho e SHA-256."""
    from . import __version__
    from .state import SESSION_ID

    return {"generated": _dt.datetime.now().isoformat(timespec="seconds"), "server": f"creo-elements-mcp {__version__}",
            "session": SESSION_ID, "units": {"length": "mm", "mass": "g"}, "files": entries, **(extra or {})}


def write_json(path: str, data) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    return path


# ------------------------------------------------------------------ sintaxe de comandos (symbol-plist)
def plist_code(command: str) -> str:
    """DESATIVADO. Imprimir o `symbol-plist` de um comando OLI estoura a pilha do Creo (Fatal exception 0xc00000fd) e
    derruba o programa (aconteceu em 2026-10-22). A sintaxe dos comandos vem da documentacao e de
    personality/sd_customize/sd_avail_cmds.cmd, e as tentativas rodam em pecas descartaveis."""
    raise PartError("symbol-plist de comandos OLI derruba o Creo (estouro de pilha 0xc00000fd): nao e permitido. "
                    "Use sd_avail_cmds.cmd, a documentacao PTC ou tentativas em pecas descartaveis.")


def variables_from_plist(text: str) -> list:
    """Nomes das variaveis de interface (:VARIABLES-UI) de um comando, na ordem. Tolerante a simbolos nao padrao."""
    s = str(text or "")
    i = s.upper().find(":VARIABLES-UI")
    if i < 0:
        return []
    j = s.find("(", i)
    if j < 0:
        return []
    depth, k, n = 0, j, len(s)
    names: list = []
    expect_key = True
    in_str = False
    tok = ""
    while k < n:
        ch = s[k]
        if in_str:
            if ch == "\\":
                k += 2
                continue
            if ch == '"':
                in_str = False
            k += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            if tok:
                tok = ""
            depth += 1
            if depth == 2:
                expect_key = False          # o valor da variavel (lista) comeca aqui
        elif ch == ")":
            if tok and depth == 1 and expect_key and tok.startswith(":"):
                names.append(tok)
            tok = ""
            depth -= 1
            if depth == 1:
                expect_key = True
            if depth == 0:
                break
        elif ch.isspace():
            if tok and depth == 1:
                if expect_key and tok.startswith(":"):
                    names.append(tok)
                    expect_key = False
                elif not expect_key:
                    expect_key = True
            tok = ""
        else:
            tok += ch
        k += 1
    return [n.upper() for n in names]


_PATH_VARS = (":FILE", ":FILENAME", ":FILE_NAME", ":PATHNAME", ":FILE_PATH")
_PART_VARS = (":PARTS_ASSEMBLIES", ":PARTS", ":PART_ASSEMBLY", ":PARTS_AND_ASSEMBLIES", ":PA")


def export_command(command: str, path: str, parts_: list, variables: list, overwrite: bool = True) -> str:
    """Texto de linha de comando para exportar `parts_` em `path` com `command` (STEP_EXPORT / SAVE_SAT), usando os nomes de
    variavel descobertos. Recusa se nao achar com seguranca o argumento de arquivo e o de pecas."""
    if not parts_:
        raise PartError("informe ao menos uma peca")
    path_var = next((v for v in _PATH_VARS if v in variables), None)
    part_var = next((v for v in _PART_VARS if v in variables), None)
    if not path_var or not part_var:
        raise PartError(f"nao identifiquei os argumentos de {command}: variaveis encontradas {variables or 'nenhuma'}")
    pth = path.replace("\\", "/")
    quoted = " ".join('"' + (p if p.startswith("/") else "/" + p) + '"' for p in parts_)
    tail = " :OVERWRITE" if overwrite and ":OVERWRITE" in variables else ""
    return f'{command.upper()} {path_var} "{pth}"{tail} {part_var} {quoted} COMPLETE'


# ------------------------------------------------------------------ lista de materiais
BOM_COLUMNS = ["item", "descricao", "qtd", "material", "massa_unit_g", "massa_total_g", "dimensoes_mm", "volume_mm3", "pecas",
               "observacao"]


def bom_rows(lines: list, group: bool = True) -> list:
    """Lista de materiais a partir de linhas {item, qty, unit_mass_g, material, parts, note, size, volume}. Linhas iguais
    (mesmo item+material) somam a quantidade."""
    rows: dict = {}
    for ln in lines:
        key = (ln.get("item", ""), ln.get("material", ""), ln.get("size", ""), round(float(ln.get("volume_mm3") or 0), 1)) \
            if group else (id(ln),)
        cur = rows.get(key)
        qty = int(ln.get("qty", 1))
        if cur is None:
            rows[key] = {"item": ln.get("item", ""), "descricao": ln.get("category", ""), "qtd": qty,
                         "material": ln.get("material", ""), "massa_unit_g": float(ln.get("unit_mass_g") or 0.0),
                         "dimensoes_mm": ln.get("size", ""), "volume_mm3": ln.get("volume_mm3"),
                         "pecas": list(ln.get("parts", [])), "observacao": ln.get("note", "")}
        else:
            cur["qtd"] += qty
            cur["pecas"] += [p for p in ln.get("parts", []) if p not in cur["pecas"]]
    out = []
    for n, r in enumerate(rows.values(), 1):
        r["massa_total_g"] = round(r["massa_unit_g"] * r["qtd"], 2)
        r["pos"] = n
        out.append(r)
    return out


def bom_totals(rows: list) -> dict:
    return {"linhas": len(rows), "itens": sum(r["qtd"] for r in rows), "massa_total_g": round(sum(r["massa_total_g"] for r in rows), 2),
            "massa_total_kg": round(sum(r["massa_total_g"] for r in rows) / 1000.0, 3)}


def _cell(r: dict, col: str):
    v = r.get(col, "")
    if col == "pecas":
        return ", ".join(v) if isinstance(v, list) else v
    return "" if v is None else v


def bom_csv(rows: list, sep: str = ";") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
    w.writerow(["pos"] + BOM_COLUMNS)
    for r in rows:
        w.writerow([r.get("pos", "")] + [_cell(r, c) for c in BOM_COLUMNS])
    tot = bom_totals(rows)
    w.writerow([])
    w.writerow(["", "TOTAL", "", tot["itens"], "", "", tot["massa_total_g"]])
    return "﻿" + buf.getvalue()           # BOM UTF-8: o Excel abre os acentos certos


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def bom_xlsx(rows: list, path: str, title: str = "Lista de materiais") -> str:
    """Grava um .xlsx minimo (so biblioteca padrao): cabecalho em negrito, numeros como numeros e a linha de total."""
    header = ["pos"] + BOM_COLUMNS
    data = [[r.get("pos", "")] + [_cell(r, c) for c in BOM_COLUMNS] for r in rows]
    tot = bom_totals(rows)
    data.append([""] * len(header))
    total_row = [""] * len(header)
    total_row[1], total_row[3], total_row[6] = "TOTAL", tot["itens"], tot["massa_total_g"]
    data.append(total_row)

    def cell_xml(ref: str, v, style: int = 0) -> str:
        if isinstance(v, bool) or v is None or v == "":
            return f'<c r="{ref}" s="{style}"/>' if style else ""
        if isinstance(v, (int, float)):
            return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{html.escape(str(v))}</t></is></c>'

    sheet_rows = ['<row r="1">' + "".join(cell_xml(f"{_col_letter(c)}1", h, 1) for c, h in enumerate(header)) + "</row>"]
    for r_i, row in enumerate(data, 2):
        st = 1 if r_i == len(data) + 1 else 0
        sheet_rows.append(f'<row r="{r_i}">' + "".join(cell_xml(f"{_col_letter(c)}{r_i}", v, st) for c, v in enumerate(row)) + "</row>")
    widths = [5, 34, 14, 6, 18, 12, 12, 22, 14, 40, 30]
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths[:len(header)]))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs></styleSheet>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets><sheet name="{html.escape(title[:28])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
              '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
              '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


# ------------------------------------------------------------------ atributos (arquivo auxiliar)
def _attr_load() -> dict:
    try:
        with open(ATTR_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _attr_save(data: dict) -> None:
    os.makedirs(os.path.dirname(ATTR_FILE), exist_ok=True)
    tmp = ATTR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, ATTR_FILE)


STD_ATTRS = ("material", "density_g_cm3", "part_number", "revision", "description", "supplier", "finish", "notes", "standard")


def set_attributes(path: str, attrs: dict, replace: bool = False) -> dict:
    p = "/" + str(path).strip().lstrip("/")
    if not isinstance(attrs, dict) or not attrs:
        raise PartError("informe attrs como objeto nao vazio")
    clean = {}
    for k, v in attrs.items():
        key = re.sub(r"[^a-z0-9_]", "_", str(k).lower()).strip("_")
        if not key:
            raise PartError(f"nome de atributo invalido: {k!r}")
        if isinstance(v, (dict, list)):
            raise PartError(f"atributo {key!r}: use texto ou numero")
        clean[key] = v
    data = _attr_load()
    cur = {} if replace else dict(data.get(p, {}))
    cur.update(clean)
    data[p] = cur
    _attr_save(data)
    return cur


def get_attributes(path: str | None = None) -> dict:
    data = _attr_load()
    if path is None:
        return data
    return data.get("/" + str(path).strip().lstrip("/"), {})


def clear_attributes(path: str, keys: list | None = None) -> dict:
    p = "/" + str(path).strip().lstrip("/")
    data = _attr_load()
    cur = data.get(p, {})
    if keys:
        for k in keys:
            cur.pop(str(k).lower(), None)
        data[p] = cur
        if not cur:
            data.pop(p, None)
    else:
        data.pop(p, None)
    _attr_save(data)
    return data.get(p, {})


# ------------------------------------------------------------------ relatorio HTML
_CSS = ("body{font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:960px;color:#1d2330;padding:0 1rem}"
        "h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.1rem;margin:1.6rem 0 .5rem;border-bottom:1px solid #d5d9e0;padding-bottom:.2rem}"
        "table{border-collapse:collapse;width:100%;margin:.4rem 0}th,td{border:1px solid #d5d9e0;padding:.3rem .5rem;text-align:left;"
        "vertical-align:top}th{background:#f1f3f7}.ok{color:#17703a}.warn{color:#9a6700}.err{color:#b42318}.muted{color:#5b6475;font-size:.85rem}"
        "img{max-width:100%;border:1px solid #d5d9e0;margin:.4rem 0}code{background:#f1f3f7;padding:0 .25rem}")


def _tbl(headers: list, rows: list) -> str:
    h = "".join(f"<th>{html.escape(str(x))}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(c)) if not isinstance(c, _Raw) else c.html}</td>" for c in r) + "</tr>"
                   for r in rows)
    return f"<table><tr>{h}</tr>{body}</table>"


class _Raw:
    def __init__(self, text: str):
        self.html = text


def report_html(title: str, sections: list, images: list | None = None, footer: str = "") -> str:
    """Relatorio autonomo (um arquivo): `sections` = [{"title", "text"?, "table": {"headers", "rows"}?, "items"?: [str], "status"?}]."""
    parts_ = [f"<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>{html.escape(title)}</title><style>{_CSS}</style>",
              f"<h1>{html.escape(title)}</h1><div class='muted'>{html.escape(_dt.datetime.now().strftime('%d/%m/%Y %H:%M'))}</div>"]
    for sec in sections:
        status = sec.get("status")
        cls = {"ok": "ok", "warn": "warn", "error": "err"}.get(status, "")
        parts_.append(f"<h2 class='{cls}'>{html.escape(str(sec.get('title', '')))}</h2>")
        if sec.get("text"):
            parts_.append(f"<p>{html.escape(str(sec['text']))}</p>")
        if sec.get("items"):
            parts_.append("<ul>" + "".join(f"<li>{html.escape(str(i))}</li>" for i in sec["items"]) + "</ul>")
        tb = sec.get("table")
        if tb:
            parts_.append(_tbl(tb["headers"], tb["rows"]))
    for img in images or []:
        try:
            import base64

            with open(img, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            parts_.append(f"<img alt='{html.escape(os.path.basename(img))}' src='data:image/png;base64,{b64}'>")
        except OSError:
            continue
    if footer:
        parts_.append(f"<p class='muted'>{html.escape(footer)}</p>")
    return "".join(parts_)


# ------------------------------------------------------------------ vistas
STD_VIEWS = {   # nome -> (direcao do olho para o alvo, vetor "para cima")
    "front": ((0, 1, 0), (0, 0, 1)), "back": ((0, -1, 0), (0, 0, 1)), "left": ((1, 0, 0), (0, 0, 1)),
    "right": ((-1, 0, 0), (0, 0, 1)), "top": ((0, 0, -1), (0, 1, 0)), "bottom": ((0, 0, 1), (0, -1, 0)),
    "iso": ((-1, 1, -1), (0, 0, 1)),
}


def view_names(names) -> list:
    out = [str(n).lower() for n in (names or ["iso"])]
    bad = [n for n in out if n not in STD_VIEWS]
    if bad:
        raise PartError(f"vista(s) desconhecida(s) {bad}: {sorted(STD_VIEWS)}")
    return out


def camera_points(view: str, center, distance: float):
    """Pontos 'from' e 'to' e vetor 'up' de uma vista padrao centrada em `center` a `distance` mm."""
    d, up = STD_VIEWS[view]
    n = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
    to = tuple(float(c) for c in center)
    eye = tuple(to[i] - d[i] / n * distance for i in range(3))
    return eye, to, up
