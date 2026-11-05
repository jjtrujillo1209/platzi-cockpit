"""Amplía GRATIS el pozo de decisores, sin gastar un crédito de revelado.

Contexto: para el lote de 200 empresas nuevas el pozo con decisor ICP ya
identificado se quedó en 238 empresas (~160 contactos útiles tras la merma de
Apollo). Faltan decisores, no créditos.

`mixed_people/api_search` de Apollo NO consume créditos de exportación (solo
`people/match` los cobra), así que buscar decisores en las empresas que
Amplemarket dejó sin DM del ICP es gratis. Amplemarket `people/search` también
es gratis, pero la cuenta está en HTTP 402 para cualquier revelado, así que se
usa Apollo, que es quien va a revelar después.

La búsqueda devuelve el perfil OFUSCADO: hay `id`, `title` y `has_email`, pero
NO el apellido ni el `linkedin_url` (llegan con el reveal). Por eso aquí se
guarda el `apollo_id` y se filtra por **`has_email: true`**: es el aviso gratis
de que ese reveal sí va a devolver correo, y evita quemar créditos en perfiles
que volverían vacíos.

Salida: data/dms_apollo_extra.json  {dominio: [{apollo_id, cargo, nombre_parcial}]}
Es reanudable: cada dominio consultado queda cacheado (aunque vuelva vacío).

Uso: python3 ampliar_pool.py [--limite N]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent / "backend"))

import apollo_prospect as ap                      # noqa: E402
from candidatos import ya_en_cockpit, _raiz_dom   # noqa: E402

ESTADO = DATA / "estado.json"
ESTADO_APOLLO = DATA / "estado_apollo.json"
SALIDA = DATA / "dms_apollo_extra.json"

MIN_EMPLEADOS = 300
HILOS = 4
_LOCK = threading.Lock()


def _cargar(p: Path, d):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def _guardar(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def objetivos(st: dict, apollo: dict, extra: dict, doms_cockpit: set[str]) -> list[str]:
    """Empresas >=300 empleados reales que todavía NO tienen decisor ICP usable."""
    firm, dms = st["firmografia"], st["dms"]
    hechos = set(apollo.get("hechos", {}))
    out = []
    for dom, f in firm.items():
        if (f.get("empleados") or 0) < MIN_EMPLEADOS:
            continue
        if _raiz_dom(dom) in doms_cockpit or dom in extra:
            continue
        # ¿ya tiene un DM del ICP, con LinkedIn y sin revelar por Apollo?
        usable = [d for d in dms.get(dom, [])
                  if (d.get("linkedin") or "").strip()
                  and ap._area(d.get("cargo") or "") != "otro"
                  and (d.get("linkedin") or "").strip() not in hechos]
        if usable:
            continue
        out.append(dom)
    return out


def buscar(dom: str, extra: dict) -> None:
    try:
        gente = ap._search(ap._key(), dom)
    except Exception as e:                       # noqa: BLE001
        print(f"   {dom}: {e.__class__.__name__} {str(e)[:80]}", flush=True)
        return
    filas = []
    for p in gente:
        cargo = (p.get("title") or "").strip()
        if not p.get("id") or ap._area(cargo) == "otro":
            continue
        if not p.get("has_email"):        # sin correo el reveal no sirve de nada
            continue
        filas.append({"apollo_id": p["id"], "cargo": cargo,
                      "nombre_parcial": " ".join(
                          x for x in [(p.get("first_name") or "").strip(),
                                      (p.get("last_name_obfuscated") or "").strip()] if x)})
    filas.sort(key=lambda d: -ap._score(d["cargo"]))
    with _LOCK:
        extra[dom] = filas[:3]


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--limite", type=int, default=0)
    args = a.parse_args()

    st = _cargar(ESTADO, {"firmografia": {}, "dms": {}})
    apollo = _cargar(ESTADO_APOLLO, {"hechos": {}})
    extra = _cargar(SALIDA, {})
    _, _, doms_cockpit = ya_en_cockpit()

    objs = objetivos(st, apollo, extra, doms_cockpit)
    if args.limite:
        objs = objs[:args.limite]
    print(f"Empresas >={MIN_EMPLEADOS} sin decisor ICP usable: {len(objs)}", flush=True)

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        for i in range(0, len(objs), 40):
            list(pool.map(lambda d: buscar(d, extra), objs[i:i + 40]))
            _guardar(SALIDA, extra)
            con = sum(1 for v in extra.values() if v)
            print(f"   {min(i + 40, len(objs))}/{len(objs)} · con decisor ICP: {con}",
                  flush=True)
            time.sleep(1)
    _guardar(SALIDA, extra)
    con = sum(1 for v in extra.values() if v)
    print(f"\n→ {SALIDA.name}: {len(extra)} dominios consultados · {con} con decisor ICP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
