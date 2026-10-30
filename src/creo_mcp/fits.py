"""Ajustes e tolerancias ISO 286-1 (furo-base e eixo-base) para diametros de 1 a 500 mm.

Tabelas em micrometros (um): graus IT3-IT12 e desvios fundamentais das letras mais usadas em projeto mecanico
(eixos d e f g h js k m n p; furos D E F G H JS K M N P). Letras de interferencia pesada (r, s, t, u...) nao estao
nesta tabela: `fit()` recusa em vez de adivinhar. Resultados em mm.
"""

from __future__ import annotations

import re

from .parts import PartError

# limites superiores das faixas de diametro nominal (mm): acima do anterior ate este valor, inclusive
RANGES = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500]

IT = {  # grau -> tolerancia (um) por faixa
    3: [2, 2.5, 2.5, 3, 4, 4, 5, 6, 8, 10, 12, 13, 15],
    4: [3, 4, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20],
    5: [4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27],
    6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40],
    7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63],
    8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97],
    9: [25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155],
    10: [40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400],
    12: [100, 120, 150, 180, 210, 250, 300, 350, 400, 460, 520, 570, 630],
}

# desvio fundamental de EIXO (um): "es" (limite superior) para d..h, "ei" (limite inferior) para k..p
SHAFT_ES = {
    "d": [-20, -30, -40, -50, -65, -80, -100, -120, -145, -170, -190, -210, -230],
    "e": [-14, -20, -25, -32, -40, -50, -60, -72, -85, -100, -110, -125, -135],
    "f": [-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68],
    "g": [-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20],
    "h": [0] * 13,
}
SHAFT_EI = {
    "k": [0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5],      # graus IT4-IT7; fora disso k = 0
    "m": [2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23],
    "n": [4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40],
    "p": [6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68],
}
HOLE_EI = {  # FURO: desvio inferior EI = -es da letra de eixo correspondente
    "D": SHAFT_ES["d"], "E": SHAFT_ES["e"], "F": SHAFT_ES["f"], "G": SHAFT_ES["g"], "H": SHAFT_ES["h"],
}
_SHAFT_LETTERS = set(SHAFT_ES) | set(SHAFT_EI) | {"js"}
_HOLE_FROM_SHAFT = {"K": "k", "M": "m", "N": "n", "P": "p"}     # ES = -ei + delta (regra geral, ate IT8)


def _range_index(size: float) -> int:
    if not 1.0 <= size <= 500.0:
        raise PartError(f"diametro nominal {size:g} fora de 1 a 500 mm")
    for i, hi in enumerate(RANGES):
        if size <= hi:
            return i
    raise PartError("faixa nao encontrada")


def it_grade(size: float, grade: int) -> float:
    """Tolerancia padrao IT`grade` em mm."""
    g = int(grade)
    if g not in IT:
        raise PartError(f"grau IT{g} fora da tabela (IT3 a IT12)")
    return IT[g][_range_index(size)] / 1000.0


_RX = re.compile(r"^\s*([A-Za-z]{1,2})\s*(\d{1,2})\s*$")


def parse_class(designation: str):
    """'H7' -> ('H', 7, 'hole'); 'g6' -> ('g', 6, 'shaft'); 'JS9'/'js9' tratados por maiuscula/minuscula."""
    m = _RX.match(str(designation))
    if not m:
        raise PartError(f"classe de tolerancia invalida: {designation!r} (exemplos: H7, g6, k6, P7, JS9)")
    letter, grade = m.group(1), int(m.group(2))
    if grade not in IT:
        raise PartError(f"grau IT{grade} fora da tabela (3 a 12)")
    if letter.upper() == "JS":
        return ("JS" if letter.isupper() else "js"), grade, ("hole" if letter.isupper() else "shaft")
    if letter.isupper():
        if letter not in HOLE_EI and letter not in _HOLE_FROM_SHAFT:
            raise PartError(f"letra de furo {letter!r} fora da tabela (D E F G H JS K M N P)")
        return letter, grade, "hole"
    if letter not in _SHAFT_LETTERS:
        raise PartError(f"letra de eixo {letter!r} fora da tabela (d e f g h js k m n p)")
    return letter, grade, "shaft"


def deviations(size: float, designation: str) -> dict:
    """Desvios (superior, inferior) em mm e limites de dimensao da classe, ex.: 25 H7 -> +0.021 / 0."""
    letter, grade, kind = parse_class(designation)
    i = _range_index(size)
    t = IT[grade][i]
    if kind == "shaft":
        if letter == "js":
            es, ei = t / 2.0, -t / 2.0
        elif letter in SHAFT_ES:
            es = SHAFT_ES[letter][i]
            ei = es - t
        else:
            if letter == "k" and not 4 <= grade <= 7:
                ei = 0
            else:
                ei = SHAFT_EI[letter][i]
            es = ei + t
        upper, lower = es, ei
    else:
        if letter == "JS":
            upper, lower = t / 2.0, -t / 2.0
        elif letter in HOLE_EI:
            lower = -SHAFT_ES[letter.lower()][i] if letter != "H" else 0
            upper = lower + t
        else:
            base = SHAFT_EI[_HOLE_FROM_SHAFT[letter]][i]
            if letter == "K" and not 4 <= grade <= 7:
                base = 0 if grade > 8 else base
            delta = (IT[grade][i] - IT[grade - 1][i]) if 3 < grade <= 8 else 0
            upper = -base + delta
            lower = upper - t
    return {"class": f"{letter}{grade}", "type": kind, "nominal": size, "upper_um": upper, "lower_um": lower,
            "upper_mm": round(upper / 1000.0, 4), "lower_mm": round(lower / 1000.0, 4),
            "max_size": round(size + upper / 1000.0, 4), "min_size": round(size + lower / 1000.0, 4),
            "tolerance_um": t}


def fit(size: float, designation: str) -> dict:
    """Ajuste 'H7/g6' (furo/eixo) para o diametro nominal: folgas, tipo e limites de fabricacao."""
    parts_ = str(designation).replace(" ", "").split("/")
    if len(parts_) != 2:
        raise PartError("ajuste deve ter a forma 'H7/g6' (furo/eixo)")
    h, s = deviations(size, parts_[0]), deviations(size, parts_[1])
    if h["type"] != "hole" or s["type"] != "shaft":
        raise PartError("use furo/eixo: letra maiuscula para o furo e minuscula para o eixo (ex.: H7/g6)")
    cl_min = (h["lower_um"] - s["upper_um"]) / 1000.0           # folga minima (negativa = interferencia maxima)
    cl_max = (h["upper_um"] - s["lower_um"]) / 1000.0
    if cl_min >= 0:
        kind = "folga"
    elif cl_max <= 0:
        kind = "interferencia"
    else:
        kind = "incerto (transicao)"
    return {"nominal": size, "fit": f"{h['class']}/{s['class']}", "kind": kind,
            "hole": {k: h[k] for k in ("class", "upper_mm", "lower_mm", "max_size", "min_size")},
            "shaft": {k: s[k] for k in ("class", "upper_mm", "lower_mm", "max_size", "min_size")},
            "clearance_min_mm": round(cl_min, 4), "clearance_max_mm": round(cl_max, 4),
            "system": "furo-base (H)" if h["class"].startswith("H") else ("eixo-base (h)" if s["class"].startswith("h") else "misto")}


# ajustes preferenciais (ISO 286 / DIN 7157) por finalidade
PREFERRED = {
    "livre": ["H11/c11 (nao tabelado aqui)", "H9/d9", "H8/e8"],
    "deslizante": ["H7/f7", "H8/f7", "H7/g6"],
    "centragem": ["H7/h6", "H7/g6", "H8/h7"],
    "transicao": ["H7/k6", "H7/m6", "H7/n6"],
    "prensado_leve": ["H7/p6"],
    "rolamento_eixo": ["k5", "m5", "j5 (js5)"],
    "rolamento_alojamento": ["H7", "J7 (JS7)", "K7"],
}


def suggest(purpose: str, size: float | None = None) -> dict:
    key = str(purpose).strip().lower().replace(" ", "_")
    if key not in PREFERRED:
        raise PartError(f"finalidade {purpose!r}; use uma de {sorted(PREFERRED)}")
    out: dict = {"purpose": key, "options": PREFERRED[key]}
    if size:
        rows = []
        for spec in PREFERRED[key]:
            token = spec.split(" ")[0]
            try:
                rows.append(fit(size, token) if "/" in token else deviations(size, token))
            except PartError:
                continue
        out["computed"] = rows
    return out
