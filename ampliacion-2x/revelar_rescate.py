"""Revela el decisor de las empresas rescatadas por `rescatar.py`.

Una sola persona por empresa —la mejor rankeada del ICP con correo en ficha—,
igual que el lote de 200. La llave del match es el `apollo_id` porque la
búsqueda ofuscada no entrega `linkedin_url`; el reveal sí lo devuelve.

Uso: python3 revelar_rescate.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent / "backend"))

import apollo_budget                              # noqa: E402
import apollo_prospect as ap                      # noqa: E402
from candidatos import es_corporativo             # noqa: E402

RESCATE = DATA / "rescate.json"
ESTADO = DATA / "estado_rescate.json"
SALIDA = DATA / "rescate_revelados.csv"

HILOS = 4
_LOCK = threading.Lock()
OUT_FIELDS = ["fase", "empresa", "dominio", "pais", "empleados", "industria",
              "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
              "dm_email_catch_all", "dm_telefono", "origen_dm"]


def _cargar(p: Path, d):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def _guardar(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def objetivos(rescate: dict, hechos: dict, meta: dict) -> list[dict]:
    out = []
    for dom, perfiles in rescate.items():
        if dom in hechos:
            continue
        elegibles = [p for p in perfiles if p.get("has_email")]
        if not elegibles:
            continue
        mejor = max(elegibles, key=lambda p: ap._score(p["cargo"]))
        c = meta.get(dom, {})
        out.append({"dominio": dom, "empresa": c.get("empresa", dom),
                    "pais": c.get("pais", ""), "empleados": c.get("empleados", ""),
                    "industria": c.get("industria", ""), "fase": "rescate",
                    "apollo_id": mejor["apollo_id"], "cargo": mejor["cargo"],
                    "area": mejor["area"], "nombre": mejor.get("nombre_parcial", "")})
    out.sort(key=lambda d: -ap._score(d["cargo"]))
    return out


def revelar(f: dict, hechos: dict, freno: dict) -> None:
    try:
        r = requests.post(f"{ap.APOLLO_BASE}/people/match",
                          headers=ap._headers(ap._key()),
                          json={"id": f["apollo_id"], "reveal_personal_emails": True},
                          timeout=45)
    except requests.RequestException as e:
        print(f"   {f['dominio']}: red {e.__class__.__name__}", flush=True)
        return
    if r.status_code in (402, 403, 429):
        with _LOCK:
            freno["motivo"] = f"HTTP {r.status_code}: {r.text[:140]}"
        return
    if r.status_code != 200:
        return
    p = (r.json() or {}).get("person") or {}
    org = p.get("organization") or {}
    with _LOCK:
        apollo_budget.consume(1)
        hechos[f["dominio"]] = {
            "email": (p.get("email") or "").strip().lower(),
            "email_estado": p.get("email_status") or "",
            "nombre": p.get("name") or f["nombre"],
            "cargo": p.get("title") or f["cargo"],
            "linkedin": (p.get("linkedin_url") or "").strip(),
            "pais": p.get("country") or org.get("country") or "",
            "empresa_apollo": org.get("name", ""),
            "industria_apollo": org.get("industry", "") or "",
        }


def escribir(objs: list[dict], hechos: dict) -> dict:
    stats, filas = Counter(), []
    for f in objs:
        got = hechos.get(f["dominio"])
        if not got:
            continue
        if not es_corporativo(got["email"]):
            stats["sin_correo_corporativo"] += 1
            continue
        if not got["linkedin"]:
            stats["sin_linkedin"] += 1
            continue
        stats["utiles"] += 1
        filas.append({"fase": "rescate",
                      "empresa": got["empresa_apollo"] or f["empresa"],
                      "dominio": f["dominio"], "pais": f["pais"] or got["pais"],
                      "empleados": f["empleados"],
                      "industria": f["industria"] or got["industria_apollo"],
                      "dm_nombre": got["nombre"], "dm_cargo": got["cargo"],
                      "dm_linkedin": got["linkedin"], "dm_email": got["email"],
                      "dm_email_estado": got["email_estado"], "dm_email_catch_all": "",
                      "dm_telefono": "", "origen_dm": "apollo_rescate"})
    with SALIDA.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(filas)
    return dict(stats)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--dry-run", action="store_true")
    args = a.parse_args()

    rescate = _cargar(RESCATE, {})
    hechos = _cargar(ESTADO, {})
    meta = {r["dominio"]: r for r in
            csv.DictReader((DATA / "candidatos.csv").open(encoding="utf-8"))}
    objs = objetivos(rescate, hechos, meta)
    print(f"Empresas rescatadas por revelar: {len(objs)}")
    print(f"Presupuesto: {apollo_budget.status()['remaining']}")
    print(f"Área: {dict(Counter(o['area'] for o in objs))}")
    if args.dry_run:
        print("\n(dry-run: no se gastó nada)")
        return 0

    freno = {"motivo": ""}
    for i in range(0, len(objs), 40):
        cupo = min(40, apollo_budget.status()["remaining"], len(objs) - i)
        if freno["motivo"] or cupo <= 0:
            break
        with ThreadPoolExecutor(max_workers=HILOS) as pool:
            list(pool.map(lambda f: revelar(f, hechos, freno), objs[i:i + cupo]))
        _guardar(ESTADO, hechos)
        print(f"   {len(hechos)}/{len(objs)} · créditos restantes "
              f"{apollo_budget.status()['remaining']}", flush=True)
        time.sleep(1)
    if freno["motivo"]:
        print(f"   Apollo frenó: {freno['motivo']}")
    _guardar(ESTADO, hechos)
    print(f"\n→ {SALIDA.name}\n{json.dumps(escribir(objs, hechos), ensure_ascii=False)}")
    print(f"Créditos restantes: {apollo_budget.status()['remaining']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
