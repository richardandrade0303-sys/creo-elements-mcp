"""O que ESTA instalacao do Creo oferece (sondagem so de leitura) + o que o servidor ja validou ao vivo.

Sondagem: uma unica forma Lisp que, por `find-symbol` + `fboundp`, confere comandos e funcoes sem chama-los (nome
inexistente nunca vira erro de leitura) e le unidades, licenca e modulos. Nada aqui altera o modelo.

Status das ferramentas: "verified" = rodou ao vivo e conferiu o resultado; "experimental" = a receita Lisp foi escrita a
partir da documentacao/log e ainda nao foi validada neste Creo. `creo_validate` (validate.py) promove ferramentas de
experimental para verified gravando ~/.creo_mcp/validation.json.
"""

from __future__ import annotations

import json
import os

from . import config, lisp

VALIDATION_FILE = os.path.join(config.HOME, "validation.json")

# Limites do Express 9.0 (observados / documentados)
EXPRESS_LIMITS = {
    "max_unique_parts": 60,          # so da edicao Express; o servidor nao aplica (veja CREO_MCP_PART_LIMIT)
    "activation_every_hours": 72,
    "no_revolve": True,
    "no_draft": True,
    "no_annotation": True,
}

# Modulos do Creo Elements/Direct (ModuleNumberNameMapping.txt) que interessam ao servidor
MODULES = ["Machining", "Rendering", "Surfacing", "Adv Assembly", "Parametrics", "Part Library", "Inspection",
           "Simplification", "Annotation", "3D Documentation", "Sheet Metal", "IGES", "STEP", "SAT", "STL",
           "Assembly", "Design Analysis"]

# Comandos (pacote OLI) que as ferramentas usam. `True` = precisa existir para a ferramenta funcionar.
COMMANDS = {
    "EXTRUDE": True, "RECTANGLE": True, "CIRCLE": True, "POLYGON": True, "CREATE_PART": True, "CREATE_WORKPLANE": True,
    "CREATE_ASSEMBLY": True, "CHANGE_PA_OWNER": True, "DELETE_3D": True, "UNITE_3D": True, "SUBTRACT_3D": True,
    "INTERSECT_3D": True, "CURRENT_WP": True, "STL": True, "SAVE_PACKAGE": True, "LOAD_PACKAGE": True,
    "SD-PUT-BUFFER": True, "SD-DEFDIALOG": True, "SD-CALL-CMDS": True, "GET_SELECTION": True, "GET_VOL_PROP": True,
    "POSITION_PA": True, "POSITION_WP": True, "SECTION": False, "MIRROR_3D": False, "GENERAL_SWEEP_DIA": False,
    "HELIX_DIA": False, "CHAMFER": False, "BLEND": False, "SHELL_3D": False, "SCALE_PA": False, "SCALE_3D": False,
    "COPY_PA": False, "COPY_3D": False, "PART_PROP": False, "AREA_PROPS": False, "SAVE_SAT": False, "LOAD_SAT": False,
    "STEP_EXPORT": False, "STEP_IMPORT": False, "SAVE_IGES": False, "LOAD_IGES": False, "CLASH_3D": False,
    "LOAD_STL": False, "SD-SET-VP-CAMERA": False, "SD-INQ-VP-CAMERA": False, "UNDO": False, "REMOVE_FROM_VP_DRAWLIST": False, "ADD_TO_VP_DRAWLIST": False,
    "SET_PART_COLOR": False, "ADD_LOFT": False, "RENAME_3D": False, "SET_PA_NAME": False,
    "SD-SET-OBJ-PROPERTY": False, "SD-INQ-OBJ-PROPERTY": False, "SD-INQ-PART-CLASH-MODE": False,
}

# nomes de funcoes de inquerito usadas nas sondagens
_UNIT_FNS = {"length": "SD-CURRENT-LENGTH-UNIT-STRING", "mass": "SD-CURRENT-MASS-UNIT-STRING",
             "angle": "SD-CURRENT-ANGLE-UNIT-STRING"}


def probe_code(units_only: bool = False) -> str:
    """Forma Lisp so de leitura. Resultado: (:lisp v :units ((:length "mm") ...) :expiry n :modules ((nome free active))
    :commands ((nome T/NIL))). Cada chamada usa find-symbol + fboundp (sem erro de leitura); NAO usa ignore-errors (ao vivo ele zera o resultado).
    `units_only`: so versao e unidades (resposta rapida para creo_units)."""
    if units_only:
        units = " ".join(f"(cons {lisp.lisp_str(k)} {lisp.lisp_str(v)})" for k, v in _UNIT_FNS.items())
        return (
            "(flet ((call (name &rest args)\n"
            "         (let ((s (find-symbol name \"OLI\")))\n"
            "           (if (and s (fboundp s)) (car (multiple-value-list (apply s args))) :no-function))))\n"
            "  (list :lisp (lisp-implementation-version)\n"
            f"        :units (mapcar #'(lambda (u) (list (car u) (call (cdr u)))) (list {units}))))"
        )
    mods = " ".join(lisp.lisp_str(m) for m in MODULES)
    cmds = " ".join(lisp.lisp_str(c) for c in COMMANDS)
    units = " ".join(f"(cons {lisp.lisp_str(k)} {lisp.lisp_str(v)})" for k, v in _UNIT_FNS.items())
    return (
        "(flet ((call (name &rest args)\n"
        "         (let ((s (find-symbol name \"OLI\")))\n"
        "           (if (and s (fboundp s)) (car (multiple-value-list (apply s args))) :no-function))))\n"
        "  (list :lisp (lisp-implementation-version)\n"
        f"        :units (mapcar #'(lambda (u) (list (car u) (call (cdr u)))) (list {units}))\n"
        "        :expiry (call \"SD-LICENSE-EXPIRY-TIME\")\n"
        f"        :modules (mapcar #'(lambda (m) (list m (call \"SD-LICENSE-FREE-MODULE-P\" m)\n"
        f"                                              (call \"SD-LICENSE-FREE-MODULE-ACTIVE-P\" m))) (list {mods}))\n"
        f"        :commands (mapcar #'(lambda (n) (list n (let ((s (or (find-symbol n \"OLI\") (find-symbol n \"ELAN\"))))\n"
        "                                                  (and s (fboundp s) t))))\n"
        f"                          (list {cmds}))))"
    )


def _plist(items) -> dict:
    out = {}
    if isinstance(items, list):
        for i in range(0, len(items) - 1, 2):
            out[str(items[i]).lstrip(":").lower()] = items[i + 1]
    return out


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.upper() == "T")


def parse(result_text: str) -> dict:
    """Interpreta o resultado impresso da sondagem."""
    data = _plist(lisp.parse_sexp(result_text))
    out: dict = {"lisp_version": data.get("lisp")}
    units = {}
    for entry in data.get("units") or []:
        if isinstance(entry, list) and len(entry) == 2:
            units[str(entry[0])] = entry[1] if not str(entry[1]).startswith(":") else None
    out["units"] = units
    out["license_expiry_raw"] = data.get("expiry")
    out["lisp_version"] = out["lisp_version"] if out["lisp_version"] is not None else data.get("lisp")
    out["modules"] = {str(m[0]): {"free": _truthy(m[1]), "active": _truthy(m[2])}
                      for m in data.get("modules") or [] if isinstance(m, list) and len(m) == 3}
    cmds = {str(c[0]): _truthy(c[1]) for c in data.get("commands") or [] if isinstance(c, list) and len(c) == 2}
    out["commands"] = cmds
    out["missing_required"] = sorted(n for n, need in COMMANDS.items() if need and not cmds.get(n))
    out["missing_optional"] = sorted(n for n, need in COMMANDS.items() if not need and not cmds.get(n))
    out["unit_warnings"] = unit_warnings(units)
    return out


def unit_warnings(units: dict) -> list[str]:
    """Todas as receitas assumem mm e g (o log mostrou 'g' no Express). Outra unidade muda volumes e massas."""
    warns = []
    length = (units.get("length") or "").strip().lower()
    if length and length not in ("mm", "millimeter", "millimeters"):
        warns.append(f"unidade de comprimento do Creo e '{units.get('length')}', nao mm: cotas e volumes das "
                     "ferramentas estarao em outra escala")
    if not length:
        warns.append("nao consegui ler a unidade de comprimento; as ferramentas assumem mm")
    mass = (units.get("mass") or "").strip().lower()
    if mass and mass not in ("g", "gram", "grams"):
        warns.append(f"unidade de massa do Creo e '{units.get('mass')}' (as ferramentas assumem g)")
    return warns


# ------------------------------------------------------------ status das ferramentas
# Ferramentas que o servidor 2.8.0 ja entregava e foram verificadas ao vivo (ver Licoes_e_Erros).
VERIFIED_BASE = {
    "creo_get_status", "creo_execute_lisp", "creo_run_command", "creo_cancel_command", "creo_lisp_apropos",
    "creo_self_test", "creo_dismiss_dialogs", "creo_screenshot", "creo_get_model_tree", "creo_get_part_info",
    "creo_create_block", "creo_create_cylinder", "creo_boolean", "creo_export_stl", "creo_build_part",
    "creo_extrude_profile", "creo_edge_treatment", "creo_shell", "creo_clear_scene", "creo_job_status",
}


def load_validation() -> dict:
    try:
        with open(VALIDATION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_validation(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(VALIDATION_FILE), exist_ok=True)
        with open(VALIDATION_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def tool_status(names: list[str]) -> dict:
    """verified | experimental para cada ferramenta (validation.json sobrepoe a lista base)."""
    val = load_validation().get("tools", {})
    out = {}
    for n in names:
        if n in VERIFIED_BASE or (isinstance(val.get(n), dict) and val[n].get("ok")):
            out[n] = "verified"
        else:
            out[n] = "experimental"
    return out


def describe(parsed: dict | None, tool_names: list[str]) -> dict:
    """Resposta de creo_capabilities: politica, limites, sondagem (se houve), estado das ferramentas."""
    out: dict = {"policy": config.policy_summary(), "express_limits": EXPRESS_LIMITS,
                 "tools": tool_status(tool_names)}
    if parsed is not None:
        out["probe"] = parsed
        out["can_export"] = {
            "stl": bool(parsed["commands"].get("STL")),
            "sat": bool(parsed["commands"].get("SAVE_SAT")),
            "step": bool(parsed["commands"].get("STEP_EXPORT")),
            "step_import": bool(parsed["commands"].get("STEP_IMPORT")),
            "iges": bool(parsed["commands"].get("SAVE_IGES")),
            "clash_3d": bool(parsed["commands"].get("CLASH_3D")),
        }
    val = load_validation()
    if val.get("when"):
        out["validated_at"] = val["when"]
    return out
