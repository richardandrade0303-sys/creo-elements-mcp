"""Biblioteca unificada de componentes e montagem por especificacao (Python puro, testavel offline).

`component(kind, params)` constroi qualquer item do catalogo de pecas (flange, eixo, engrenagem...) ou da biblioteca de
ferragens/perfis/transmissao e devolve um `place.Built` (plano de execucao + lista de materiais + dados de projeto).
`assemble(spec)` junta varios componentes, cada um com eixo/origem proprios e, se pedido, repetido num padrao.

Nada aqui fala com o Creo: quem executa o plano e `actions.run_built`.
"""

from __future__ import annotations

import inspect
import itertools
import math

from . import hardware, parts, place, profiles, transmission
from .parts import PartError, safe_name
from .place import Built

MAX_ITEMS = 500                # teto de seguranca por chamada (nao e o limite de pecas do Creo)
MAX_VARIANTS = 24

LIBRARY_KINDS = ("fastener", "bearing", "key", "v_pulley", "timing_pulley", "coupling", "profile", "frame", "pipe")


def _check_params(fn, params: dict, kind: str) -> None:
    allowed = set(inspect.signature(fn).parameters)
    bad = sorted(set(params) - allowed)
    if bad:
        raise PartError(f"parametros desconhecidos para {kind}: {bad}. Validos: {sorted(allowed)}")


def _call(fn, kind: str, params: dict):
    _check_params(fn, params, kind)
    try:
        return fn(**params)
    except TypeError as exc:                       # argumento obrigatorio faltando etc.
        raise PartError(f"parametros invalidos para {kind}: {exc}")


def _xyz(value, what: str, default=(0.0, 0.0, 0.0)):
    if value is None:
        return tuple(default)
    try:
        v = tuple(float(x) for x in value)
    except (TypeError, ValueError):
        raise PartError(f"{what} deve ser [x, y, z]")
    if len(v) != 3:
        raise PartError(f"{what} deve ter 3 coordenadas")
    return v


# ------------------------------------------------------------------ perfis, estruturas e tubos
def _profile_bom(profile: str, length: float, names: list, qty: int = 1) -> dict:
    sec = profiles.parse_profile(profile)
    return {"item": f"Perfil {sec.name} L={length:g}", "category": "perfil", "qty": qty, "parts": list(names),
            "unit_mass_g": round(sec.mass_per_m_kg * length, 1), "material": "Aço carbono", "size": f"L={length:g}",
            "note": f"{sec.family}, area {sec.area:.1f} mm2"}


def profile_member(profile: str, length: float, axis: str = "z", start=(0.0, 0.0, 0.0), roll: int = 0,
                   name: str = "perfil", end_trim=(0.0, 0.0), origin=None) -> Built:
    """Barra de perfil. `origin` e apelido de `start` (assim a mesma especificacao serve a todos os componentes)."""
    if origin is not None:
        start = origin
    plan = profiles.member_plan(profile, float(length), axis, _xyz(start, "start"), int(roll), name,
                                (float(end_trim[0]), float(end_trim[1])))
    net = float(length) - float(end_trim[0]) - float(end_trim[1])
    sec = profiles.parse_profile(profile)
    return Built(plan, [_profile_bom(profile, net, plan.parts())], dict(sec.info(), length=net, axis=axis))


def frame(members=None, box=None, prefix: str = "estrutura") -> Built:
    """Estrutura de barras de perfil: `members` (lista) ou `box` {length,width,height,columns,beams,origin,...}."""
    if members is None and box is None:
        raise PartError("informe members (lista de barras) ou box {length, width, height}")
    if members is None:
        b = dict(box)
        _check_params(profiles.frame_box, dict(b, prefix=prefix), "frame.box")
        members = _call(profiles.frame_box, "frame.box", dict(b, prefix=prefix))
    plan = profiles.frame_members(members, prefix)
    bom_groups: dict = {}
    for m in members:
        p0, p1 = [float(v) for v in m["from"]], [float(v) for v in m["to"]]
        L = max(abs(p1[k] - p0[k]) for k in range(3))
        trim = m.get("trim") or [0.0, 0.0]
        net = round(L - float(trim[0]) - float(trim[1]), 3)
        nm = safe_name(m.get("name") or "", "barra")
        bom_groups.setdefault((m["profile"], net), []).append(nm)
    bom = [dict(_profile_bom(pf, ln, nms, qty=len(nms))) for (pf, ln), nms in bom_groups.items()]
    return Built(plan, bom, {"members": len(members), "parts": len(plan.parts())})


def pipe(points, od: float, wall: float, name: str = "tubo") -> Built:
    """Trecho de tubulacao: uma barra tubular reta por trecho entre pontos consecutivos (cada trecho segue UM eixo), de
    centro de no a centro de no. As curvas NAO sao modeladas (sem REVOLVE/varredura livre no Express 9.0): os trechos se
    cruzam no no (interferencia esperada nas curvas). Util para traçado, comprimentos e lista de materiais."""
    pts = [_xyz(p, f"ponto {i + 1}") for i, p in enumerate(points or [])]
    if len(pts) < 2:
        raise PartError("informe ao menos 2 pontos [x,y,z]")
    od, wall = float(od), float(wall)
    if od <= 0 or wall <= 0 or 2 * wall >= od:
        raise PartError("od e wall devem ser > 0 e 2*wall < od")
    prof = f"CHS{od:g}x{wall:g}"
    base = safe_name(name, "tubo")
    n = len(pts) - 1
    plans, bom_groups = [], {}
    for i in range(n):
        a, b = pts[i], pts[i + 1]
        diff = [b[k] - a[k] for k in range(3)]
        axes = [k for k in range(3) if abs(diff[k]) > 1e-9]
        if len(axes) != 1:
            raise PartError(f"trecho {i + 1}: de {a} a {b} deve seguir um unico eixo (x, y ou z)")
        k = axes[0]
        L = abs(diff[k])
        start = list(a)
        start[k] = min(a[k], b[k])
        nm = f"{base}{i + 1}"
        plans.append(profiles.member_plan(prof, L, "xyz"[k], start, 0, nm))
        bom_groups.setdefault(round(L, 3), []).append(nm)
    plan = profiles.merge_plans(base, plans, f"{n} trecho(s) de tubo Ø{od:g}x{wall:g}; curvas nao modeladas "
                                             "(trechos se cruzam nos nos)")
    bom = [dict(_profile_bom(prof, ln, nms, qty=len(nms))) for ln, nms in bom_groups.items()]
    return Built(plan, bom, {"legs": n, "od": od, "wall": wall, "bends_modeled": False,
                             "length_total_mm": round(sum(ln * len(v) for ln, v in bom_groups.items()), 3)})


# ------------------------------------------------------------------ ferragens
def _fastener(**params) -> Built:
    p = dict(params)
    positions = p.pop("positions", None)
    if positions:
        pos = [tuple(float(v) for v in pt) for pt in positions]
        if any(len(pt) != 2 for pt in pos):
            raise PartError("positions deve ser uma lista de [x, y]")
        _check_params(hardware.fastener_stack, dict(p, at=(0.0, 0.0)), "fastener")
        try:
            return hardware.fastener_layout(pos, **p)
        except TypeError as exc:
            raise PartError(f"parametros invalidos para fastener: {exc}")
    return _call(hardware.fastener_stack, "fastener", p)


def _profile_kind(**params) -> Built:
    return _call(profile_member, "profile", params)


def _frame_kind(**params) -> Built:
    return _call(frame, "frame", params)


def _pipe_kind(**params) -> Built:
    return _call(pipe, "pipe", params)


LIBRARY = {
    "fastener": _fastener,
    "bearing": lambda **p: _call(hardware.bearing, "bearing", p),
    "key": lambda **p: _call(hardware.key_part, "key", p),
    "v_pulley": lambda **p: _call(transmission.v_pulley, "v_pulley", p),
    "timing_pulley": lambda **p: _call(transmission.timing_pulley, "timing_pulley", p),
    "coupling": lambda **p: _call(transmission.coupling, "coupling", p),
    "profile": _profile_kind,
    "frame": _frame_kind,
    "pipe": _pipe_kind,
}


def kinds() -> dict:
    """Tudo que `component()` aceita: catalogo de pecas + biblioteca."""
    return {"catalog": sorted(parts.CATALOG), "library": list(LIBRARY_KINDS)}


def component(kind: str, params: dict | None = None) -> Built:
    """Constroi um componente pelo nome. Parametros `axis` e `origin` (catalogo) levam a peca ao lugar; nas pecas da
    biblioteca vao para o proprio construtor."""
    k = str(kind).strip().lower()
    p = dict(params or {})
    if k in LIBRARY:
        return LIBRARY[k](**p)
    if k in parts.CATALOG:
        axis = p.pop("axis", "z")
        origin = _xyz(p.pop("origin", None), "origin")
        plan = parts.build(k, p)
        ident = place.norm_axis(axis) == "+z" and origin == (0.0, 0.0, 0.0)
        if not ident:
            plan = place.orient(plan, axis, origin, name=plan.name)
        return Built(plan, [], {"kind": k, "axis": place.norm_axis(axis), "origin": list(origin)})
    raise PartError(f"componente desconhecido {kind!r}. Catalogo: {sorted(parts.CATALOG)}; biblioteca: {list(LIBRARY_KINDS)}")


# ------------------------------------------------------------------ montagem por especificacao
def _expand_item(item: dict, idx: int) -> list[tuple]:
    """Um item da especificacao -> lista de (nome, kind, params). `pattern` repete o item em pontos (x, y) somados a origem."""
    if not isinstance(item, dict):
        raise PartError(f"item {idx}: deve ser um objeto")
    kind = item.get("component") or item.get("kind")
    if not kind:
        raise PartError(f"item {idx}: informe 'component' (catalogo ou biblioteca)")
    params = dict(item.get("params") or {})
    for key in ("axis", "origin"):
        if key in item:
            params[key] = item[key]
    base = safe_name(item.get("name") or f"{kind}{idx}", f"item{idx}")
    pat = item.get("pattern")
    if not pat:
        return [(base, kind, dict(params, name=base))]
    pat = dict(pat)
    pk = pat.pop("kind", "linear")
    pts = place.pattern_points(pk, **pat)
    out = []
    origin = _xyz(params.get("origin"), f"item {idx}: origin")
    for j, (px, py) in enumerate(pts, 1):
        q = dict(params, name=f"{base}{j}")
        q["origin"] = (origin[0] + px, origin[1] + py, origin[2])
        out.append((f"{base}{j}", kind, q))
    return out


def assemble(spec: dict) -> Built:
    """spec: {"name": "conj", "items": [{"component", "params", "name", "axis", "origin", "pattern"?}, ...], "group": bool}.
    Devolve um Built com todas as pecas; nomes repetidos sao recusados."""
    if not isinstance(spec, dict) or not spec.get("items"):
        raise PartError("spec precisa de 'items' (lista nao vazia)")
    name = safe_name(spec.get("name") or "conjunto", "conjunto")
    flat = []
    for i, it in enumerate(spec["items"], 1):
        flat.extend(_expand_item(it, i))
    if len(flat) > MAX_ITEMS:
        raise PartError(f"{len(flat)} componentes: acima de {MAX_ITEMS} por chamada; divida em varias montagens")
    names = [n for n, _k, _p in flat]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise PartError(f"nomes de componente repetidos: {dup}")
    built = []
    for nm, kind, p in flat:
        try:
            built.append(component(kind, p))
        except PartError as exc:
            raise PartError(f"componente '{nm}' ({kind}): {exc}")
    merged = place.merge_built(name, built, notes=f"montagem '{name}': {len(built)} componente(s)",
                               info={"name": name, "components": [{"name": n, "kind": k} for n, k, _p in flat]})
    n_parts = len(merged.plan.parts())
    merged.info["parts"] = n_parts
    merged.info["group"] = bool(spec.get("group"))
    if parts_limit() and n_parts > parts_limit():
        raise PartError(f"{n_parts} pecas: acima do limite configurado de {parts_limit()}")
    return merged


def parts_limit() -> int:
    from .scene import part_limit

    return part_limit()


# ------------------------------------------------------------------ variantes
def variant_params(base: dict | None, vary: dict, mode: str = "grid", limit: int = MAX_VARIANTS) -> list[dict]:
    """Lista de dicts de parametros: `vary` = {param: [valores]}. mode 'grid' (produto cartesiano) ou 'zip' (em paralelo)."""
    if not isinstance(vary, dict) or not vary:
        raise PartError("vary deve ser um objeto {parametro: [valores]}")
    for k, v in vary.items():
        if not isinstance(v, (list, tuple)) or not v:
            raise PartError(f"vary[{k!r}] deve ser uma lista nao vazia")
    keys = list(vary)
    if mode == "zip":
        n = len(vary[keys[0]])
        if any(len(vary[k]) != n for k in keys):
            raise PartError("mode='zip' exige listas do mesmo tamanho")
        combos = list(zip(*[vary[k] for k in keys]))
    elif mode == "grid":
        combos = list(itertools.product(*[vary[k] for k in keys]))
    else:
        raise PartError("mode deve ser 'grid' ou 'zip'")
    if len(combos) > limit:
        raise PartError(f"{len(combos)} variantes: acima de {limit} por chamada")
    out = []
    for c in combos:
        d = dict(base or {})
        d.update(dict(zip(keys, c)))
        out.append(d)
    return out


def variant_label(vary_keys: list, params: dict) -> str:
    return "_".join(f"{k}{params[k]:g}" if isinstance(params[k], (int, float)) else f"{k}{params[k]}" for k in vary_keys)


def round_nice(v: float) -> float:
    return round(v, 4) if math.isfinite(v) else v
