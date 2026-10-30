"""Pontos de restauracao da cena: SAVE_PACKAGE antes de passos destrutivos e LOAD_PACKAGE depois de uma queda.

Verificado ao vivo (Creo Express 9.0, 22/10/2026):
  * SALVAR: (save_package :all_at_top :filename "C:/x/a.pk2" :overwrite) chamado DIRETO em Lisp grava o pacote.
    Um nome sem extensao ganha uma extensao; por isso usamos sempre ".pk2" explicito. Pacote = arquivo zip.
  * CARREGAR: so funciona como TEXTO de comando, com a extensao .pk2 explicita e numa pasta SEM componente que comece com
    ponto:   LOAD_PACKAGE :FILENAME "C:/Users/x/creo_mcp_snapshots/a.pk2" COMPLETE     (via sd-put-buffer, assincrono;
    leva de 15 a 40 s). Falham com "Error 64 - file name is not valid" e nao carregam nada: extensao .pkg, nome sem
    extensao, qualquer arquivo sob ~/.creo_mcp (pasta com ponto) e o dialogo nativo "Load" (digitado ou clicado).
    O chamado direto (load_package ...) abre o dialogo Load modal e trava o Lisp: NUNCA usar.
  * Um "Error 64" que aparece DEPOIS de uma carga bem-sucedida e ruido (sobrou um COMPLETE): o que vale e a arvore.
  * Carregar por cima de objetos com o mesmo nome renomeia os novos (w1 -> w1.1, p1 -> p1.1): so restauramos numa cena sem
    pecas, ou com `replace=True` (que salva a cena atual e a apaga antes).
  * NAO usar :parts_assemblies com lista de caminhos no save: derrubou o Creo uma vez.
Cada snapshot ganha um .json ao lado com rotulo, data, objetos, pecas, numero de corpos, bytes e SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time

from . import config, health, lisp, recipes, scene

EXT = ".pk2"
KEEP_AUTO = 20
_now = time.time


def snap_dir(directory: str | None = None) -> str:
    return directory or config.snapshot_dir()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text or "").strip("_").lower()
    return (s or "snap")[:40]


def new_path(label: str, directory: str | None = None) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(_now()))
    return os.path.join(snap_dir(directory), f"{stamp}_{_slug(label)}{EXT}").replace("\\", "/")


def save_lisp(path: str) -> str:
    d = os.path.dirname(path).replace("\\", "/")
    return (f"(progn (ensure-directories-exist {lisp.lisp_str(d + '/')}) "
            f"(save_package :all_at_top :filename {lisp.lisp_path(path)} :overwrite) :ok)")


def load_command(path: str) -> str:
    """Texto do comando de carga (o unico formato que carrega ao vivo)."""
    return f'LOAD_PACKAGE :FILENAME "{path.replace(chr(92), "/")}" COMPLETE'


def load_lisp(path: str) -> str:
    return recipes.send_command(load_command(path))


def plain_path_problem(path: str) -> str | None:
    """Motivo pelo qual o LOAD_PACKAGE recusaria o caminho, ou None."""
    if not path.lower().endswith(EXT):
        return f"a extensao precisa ser {EXT} (o Creo recusa .pkg e nomes sem extensao)"
    if config.has_dot_component(os.path.dirname(path)):
        return "a pasta tem um componente que comeca com ponto (ex.: .creo_mcp): o Creo recusa"
    return None


def _tree(runner, depth: int = 6):
    """Arvore do modelo: lista (vazia se a cena nao tem nada) ou None se nao foi possivel ler."""
    res = runner(recipes.model_tree(depth), 30)
    if not res.get("ok"):
        return None
    tree = lisp.parse_sexp(res["result"]) if res.get("result") else None
    return tree if isinstance(tree, list) else []


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _meta_path(pkg: str) -> str:
    base, ext = os.path.splitext(pkg)
    return base + ".json" if ext.lower() in (".pk2", ".pkg") else pkg + ".json"


def _objects(tree) -> list[dict]:
    return [{"path": n["path"], "kind": n["kind"]} for n in scene.top_level(tree)] if tree else []


def create(runner, label: str = "manual", auto: bool = False, directory: str | None = None,
           wait_s: float = 3.0) -> dict:
    """Salva o pacote da cena inteira e confere o arquivo (existe, tamanho > 0, mais novo que o inicio da chamada)."""
    t0 = _now()
    tree = _tree(runner)
    bodies = scene.count_bodies(tree) if tree is not None else None
    if tree is not None and bodies == 0:
        return {"ok": False, "skipped": True, "error": "cena vazia: nada a salvar", "bodies": 0}
    path = new_path(label, directory)
    res = runner(save_lisp(path), 120)
    out: dict = {"ok": False, "path": path, "label": label, "auto": auto, "bodies": bodies}
    if res.get("dialogs"):
        out["dialogs"] = res["dialogs"]
    local = path.replace("/", os.sep)
    deadline = _now() + wait_s            # o Creo pode gravar um instante depois (aviso do Express fecha pelo vigia)
    while not (os.path.exists(local) and os.path.getsize(local) > 0) and _now() < deadline:
        time.sleep(0.2)
    if not (os.path.exists(local) and os.path.getsize(local) > 0):
        out["error"] = res.get("error") or "o arquivo do pacote nao apareceu"
        health.record("snapshot_failed", out["error"], label=label)
        return out
    if os.path.getmtime(local) < t0 - 1:
        out["error"] = "o arquivo existente e mais antigo que esta chamada (nao foi regravado)"
        return out
    objs = _objects(tree)
    meta = {"name": os.path.basename(path), "label": label, "auto": auto, "format": "pk2",
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "bodies": bodies,
            "bytes": os.path.getsize(local), "sha256": _sha256(local), "objects": objs,
            "top_level": [o["path"] for o in objs], "parts": scene.parts_of(tree) if tree else None}
    try:
        with open(_meta_path(local), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass
    out.update({"ok": True, "bytes": meta["bytes"], "sha256": meta["sha256"], "top_level": meta["top_level"]})
    if auto:
        out["pruned"] = prune(directory)
    return out


def _read_meta(pkg: str) -> dict:
    try:
        with open(_meta_path(pkg), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def list_snapshots(directory: str | None = None) -> list[dict]:
    d = snap_dir(directory)
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.lower().endswith((".pk2", ".pkg")))
    except OSError:
        return out
    for n in names:
        full = os.path.join(d, n)
        meta = _read_meta(full)
        out.append({"name": n, "path": full.replace("\\", "/"), "bytes": os.path.getsize(full) if os.path.exists(full) else None,
                    "label": meta.get("label"), "auto": meta.get("auto"), "created": meta.get("created"),
                    "bodies": meta.get("bodies"), "top_level": meta.get("top_level"), "has_meta": bool(meta)})
    return out


def prune(directory: str | None = None, keep: int = KEEP_AUTO) -> list[str]:
    """Apaga os snapshots AUTOMATICOS mais antigos alem de `keep`. Manuais nunca sao apagados."""
    removed = []
    autos = [s for s in list_snapshots(directory) if s.get("auto")]
    for s in autos[:-keep] if keep > 0 else autos:
        for p in (s["path"], _meta_path(s["path"])):
            try:
                os.remove(p)
            except OSError:
                pass
        removed.append(s["name"])
    return removed


def resolve(name: str = "", directory: str | None = None) -> str | None:
    """Caminho do snapshot: vazio = o mais recente; aceita nome parcial ou caminho completo."""
    snaps = list_snapshots(directory)
    if not name:
        return snaps[-1]["path"] if snaps else None
    if os.path.isfile(name) and name.lower().endswith((".pk2", ".pkg")):
        return name.replace("\\", "/")
    matches = [s for s in snaps if name.lower() in s["name"].lower()]
    return matches[-1]["path"] if matches else None


def stage_for_load(path: str, directory: str | None = None) -> str:
    """Caminho que o LOAD_PACKAGE aceita: o proprio arquivo se ja for .pk2 em pasta sem ponto; senao uma copia .pk2 em
    <pasta de snapshots>/_load/. (Um .pkg antigo e um .pk2 renomeado: mesmo zip.)"""
    if plain_path_problem(path) is None:
        return path
    dest_dir = os.path.join(snap_dir(directory), "_load")
    os.makedirs(dest_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(dest_dir, stem + EXT).replace("\\", "/")
    shutil.copyfile(path.replace("/", os.sep), dest.replace("/", os.sep))
    return dest


def clear_all_code() -> str:
    """Apaga pecas e montagens de nivel raiz (workplanes ficam). Devolve os caminhos que restaram."""
    return (
        "(progn\n"
        "  (dolist (n (mapcar #'oli:sd-inq-obj-pathname\n"
        "                     (remove-if-not #'(lambda (o) (or (oli:sd-inq-part-p o) (oli:sd-inq-assembly-p o)))\n"
        "                                    (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\")))))\n"
        "    (delete_3d n))\n"
        "  (mapcar #'oli:sd-inq-obj-pathname (oli:sd-inq-obj-children (oli:sd-pathname-to-obj \"/\"))))"
    )


def _non_wp(tree) -> list[str]:
    return [n["path"] for n in scene.top_level(tree) if n["kind"] not in ("WP", "OTHER")]


def verify_against(tree, meta: dict) -> dict:
    """Compara a arvore atual com o que o snapshot guardou: pecas e objetos de topo que nao sao workplane."""
    have_parts = set(scene.parts_of(tree))
    have_top = set(_non_wp(tree))
    want_parts = meta.get("parts")
    objs = meta.get("objects")
    if isinstance(objs, list) and objs:
        want_top = [o["path"] for o in objs if o.get("kind") not in ("WP", "OTHER")]
    else:
        want_top = [p for p in (meta.get("top_level") or []) if not re.match(r"^/w\d*(\.\d+)?$", p)]
    missing_top = [p for p in want_top if p not in have_top]
    missing_parts = [p for p in (want_parts or []) if p not in have_parts]
    return {"missing_top": missing_top, "missing_parts": missing_parts, "have_parts": len(have_parts),
            "want_parts": len(want_parts) if want_parts is not None else None,
            "ok": not missing_top and not missing_parts and (bool(have_parts) or not want_top)}


def restore(runner, settle, name: str = "", directory: str | None = None, replace: bool = False,
            wait_s: float = 60.0, poll_s: float = 2.0, sleep=time.sleep) -> dict:
    """Carrega um snapshot. Cena com pecas: recusa, a menos que replace=True (salva a cena atual e a apaga antes)."""
    path = resolve(name, directory)
    if not path:
        return {"ok": False, "error": "nenhum snapshot encontrado (creo_snapshot action='list')"}
    out: dict = {"ok": False, "path": path}
    tree = _tree(runner)
    if tree is None:
        out["error"] = "nao consegui ler a arvore do modelo antes de restaurar (o Creo responde? creo_get_status)"
        return out
    meta = _read_meta(path)
    if scene.count_bodies(tree) > 0:
        if not replace:
            out["error"] = ("a cena atual tem pecas: carregar por cima renomeia tudo (p1 -> p1.1). Use replace=true "
                            "(salva a cena atual como snapshot e a apaga antes) ou limpe a cena.")
            out["bodies_now"] = scene.count_bodies(tree)
            return out
        bk = create(runner, "antes_de_restaurar", auto=True, directory=directory)
        out["backup"] = {k: bk.get(k) for k in ("ok", "path", "error") if bk.get(k) is not None}
        if not bk.get("ok"):
            out["error"] = "nao consegui salvar a cena atual antes de apagar; abortei sem apagar nada"
            return out
        runner(clear_all_code(), 60)
        tree = _tree(runner)
        left = _non_wp(tree) if tree is not None else ["?"]
        if left:
            out["error"] = f"depois de apagar ainda restam objetos ({left}); apague-os a mao e repita (backup salvo)"
            return out
    try:
        staged = stage_for_load(path, directory)
    except OSError as exc:
        out["error"] = f"nao consegui preparar a copia .pk2: {exc!r}"
        return out
    out["staged"] = staged
    res = runner(load_lisp(staged), 20)
    if not res.get("ok"):
        out["error"] = res.get("error") or "nao consegui enfileirar o comando de carga"
        return out
    out["dialogs"] = settle(5.0) or None
    waited = 0.0
    after = None
    check = {"ok": False}
    while waited <= wait_s:
        after = _tree(runner)
        if after is not None:
            check = verify_against(after, meta) if meta else {"ok": scene.count_bodies(after) > 0}
            if check["ok"]:
                break
        d = settle(poll_s)                     # fecha o Error 64 que sobra e outros avisos
        if d:
            out["dialogs"] = (out.get("dialogs") or []) + d
        waited += poll_s + 0.5
        sleep(0.5)
    out["waited_s"] = round(waited, 1)
    out["objects_now"] = [n["path"] for n in scene.top_level(after)] if after else []
    out["verify"] = check
    out["ok"] = bool(check.get("ok"))
    if not out["ok"]:
        out["error"] = ("depois da carga a arvore nao bate com o snapshot "
                        f"(faltam {len(check.get('missing_parts') or [])} pecas e {len(check.get('missing_top') or [])} objetos de topo)")
        health.record("restore_incomplete", out["error"], path=path)
    else:
        health.acknowledge()
    return out
