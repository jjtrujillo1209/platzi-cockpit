"""Rescata las empresas que quedaron fuera del lote por no tener decisor.

207 empresas pasan el ICP (>=300 empleados reales, fuera de HubSpot y del
cockpit) pero se cayeron porque ni Amplemarket ni Apollo les encontraron un
decision maker del ICP. La causa no es que no lo tengan: es cómo se buscó.

  · Apollo se consultó con `person_titles=TARGET_TITLES`, una lista fija de 75
    cargos. Si el jefe de RR.HH. de esa empresa se titula distinto —y en LATAM
    se titula distinto todo el tiempo— la búsqueda devuelve cero. Aquí se busca
    por **seniority** (C-suite, VP, head, director, manager…) sin restringir el
    título, y se clasifica después con `_area()`.
  · Amplemarket sí trajo gente en 62 de ellas, pero el filtro ICP la descartó:
    entre lo descartado hay «Gerente de RRHH» y «Presidente», que son
    compradores de manual. Ese material ya pagado se recupera aquí.
  · El `_area()` del cockpit tampoco reconoce el vocabulario real de RR.HH. en
    LATAM («Capital Humano», «Selección y Contratación», «Compensaciones y
    Beneficios») ni «Seguridad de la Información» como tecnología. Aquí se
    extiende con `_area_ext()`, que NO toca el clasificador de producción:
    primero pregunta al oficial y solo si dice «otro» aplica el vocabulario
    extra. Portar esto a `apollo_prospect._area()` arreglaría de paso los ~260
    contactos que el cockpit tiene sin clasificar, pero es otra decisión.

Todo lo de este script es gratis: `mixed_people/api_search` no cobra créditos.
Solo se guardan perfiles con `has_email: true`, que es el aviso de que el
revelado posterior sí va a devolver correo.

Salida: data/rescate.json  {dominio: [{apollo_id, cargo, nombre_parcial, area}]}
Uso: python3 rescatar.py [--limite N]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent / "backend"))

import apollo_prospect as ap          # noqa: E402

SIN_DM = DATA / "sin_dm.json"
SALIDA = DATA / "rescate.json"

# Los cargos que deciden una compra de formación corporativa viven de "manager"
# para arriba. Apollo acepta este filtro sin exigir título, que es justo lo que
# hacía fallar la búsqueda anterior.
SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head",
               "director", "manager"]
HILOS = 4
_LOCK = threading.Lock()

# Vocabulario que el clasificador oficial no cubre, por vector del ICP.
EXTRA = {
    "rrhh": ("capital humano", "seleccion", "reclutamiento", "contratacion",
             "compensaciones", "beneficios", "desarrollo organizacional",
             "administracion de personal", "nomina", "relaciones laborales",
             "bienestar", "clima organizacional", "organizational psycholog",
             "psicolog", "human capital", "people & culture", "workforce"),
    "tecnologia": ("seguridad de la informacion", "seguridad de informacion",
                   "seguranca da informacao", "information security", "sistemas",
                   "informatica", "software", "infraestructura tecnologica",
                   "ciberseguridad", "data officer", "datos"),
    "ventas": ("preventa", "key account", "business development",
               "desarrollo de negocio", "desenvolvimento de negocios",
               "account manager"),
    "direccion": ("presidente", "president", "socio gerente", "managing director",
                  "country manager", "diretor executivo", "director ejecutivo"),
}
_SIGLAS = {"rrhh": "rrhh", "rr hh": "rrhh", "rh": "rrhh", "gm": "direccion",
           "ti": "tecnologia", "it": "tecnologia"}


def _area_ext(cargo: str) -> str:
    """`_area()` del cockpit y, si dice «otro», el vocabulario de arriba."""
    base = ap._area(cargo or "")
    if base != "otro":
        return base
    t = ap._norm(cargo)
    for area, claves in EXTRA.items():
        if any(k in t for k in claves):
            return area
    for sigla, area in _SIGLAS.items():
        if ap._has_word(t, sigla):
            return area
    return "otro"


def _cargar(p: Path, d):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def _guardar(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def buscar_amplio(dom: str) -> list[dict]:
    r = requests.post(f"{ap.APOLLO_BASE}/mixed_people/api_search",
                      json={"page": 1, "per_page": 25,
                            "person_seniorities": SENIORITIES,
                            "q_organization_domains_list": [dom]},
                      headers=ap._headers(ap._key()), timeout=30)
    ap._check_limits(r)
    if not r.ok:
        return []
    return r.json().get("people", [])


def procesar(dom: str, out: dict) -> None:
    try:
        gente = buscar_amplio(dom)
    except Exception as e:                       # noqa: BLE001
        print(f"   {dom}: {e.__class__.__name__} {str(e)[:70]}", flush=True)
        return
    filas = []
    for p in gente:
        cargo = (p.get("title") or "").strip()
        area = _area_ext(cargo)
        if not p.get("id") or area == "otro":
            continue
        filas.append({"apollo_id": p["id"], "cargo": cargo, "area": area,
                      "has_email": bool(p.get("has_email")),
                      "nombre_parcial": " ".join(
                          x for x in [(p.get("first_name") or "").strip(),
                                      (p.get("last_name_obfuscated") or "").strip()] if x)})
    # primero los que traen correo en ficha: son los únicos que vale la pena
    # revelar, el resto se guarda solo como constancia de que existe el decisor
    filas.sort(key=lambda d: (not d["has_email"], -ap._score(d["cargo"])))
    with _LOCK:
        out[dom] = filas[:3]


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--limite", type=int, default=0)
    args = a.parse_args()

    objetivo = [d for d in json.loads(SIN_DM.read_text(encoding="utf-8"))]
    out = _cargar(SALIDA, {})
    objetivo = [d for d in objetivo if d not in out]
    if args.limite:
        objetivo = objetivo[:args.limite]
    print(f"Empresas sin decisor por rescatar: {len(objetivo)}", flush=True)

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        for i in range(0, len(objetivo), 40):
            list(pool.map(lambda d: procesar(d, out), objetivo[i:i + 40]))
            _guardar(SALIDA, out)
            con = sum(1 for v in out.values() if any(x["has_email"] for x in v))
            print(f"   {min(i + 40, len(objetivo))}/{len(objetivo)} · "
                  f"rescatadas con correo en ficha: {con}", flush=True)
            time.sleep(1)
    _guardar(SALIDA, out)
    con = sum(1 for v in out.values() if any(x["has_email"] for x in v))
    solo = sum(1 for v in out.values() if v and not any(x["has_email"] for x in v))
    print(f"\n→ {SALIDA.name}: {len(out)} consultadas · {con} rescatables "
          f"(decisor ICP con correo en ficha) · {solo} con decisor pero sin correo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
