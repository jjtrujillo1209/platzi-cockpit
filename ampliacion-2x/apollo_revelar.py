"""Revela contacto vía Apollo para las empresas que Amplemarket dejó a medias.

Contexto: Amplemarket se quedó sin créditos con 906 empresas ya calificadas
(>300 empleados reales) y 561 con decisor identificado. El usuario eligió
completarlas con Apollo, aceptando que Apollo NO entrega teléfono sin un webhook
público: estos contactos entran con correo corporativo + LinkedIn y se marcan
`sin_telefono` para rellenarlos después.

Lo que sí gana Apollo: `email_status` viene en la misma respuesta, así que la
entregabilidad sale gratis (en Amplemarket cuesta 1 crédito por correo).

Presupuesto: respeta apollo_budget del cockpit, que existe porque en jul-2026 un
job desatendido quemó 2.9K créditos reales. Un people/match exitoso descuenta 1.
Si se agota, el script PARA — no lo sube nadie por su cuenta: el saldo real lo
reporta el usuario desde el dashboard de Apollo.

Uso:
  python3 apollo_revelar.py --dry-run
  python3 apollo_revelar.py [--objetivo 394]
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
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

import apollo_budget                      # noqa: E402
import apollo_prospect as ap              # noqa: E402
from candidatos import es_corporativo     # noqa: E402

ESTADO = DATA / "estado.json"
ESTADO_APOLLO = DATA / "estado_apollo.json"
SALIDA = DATA / "revelados_apollo.csv"

HILOS = 4                # Apollo tolera ~4 peticiones/s en people/match
_LOCK = threading.Lock()

OUT_FIELDS = [
    "fase", "empresa", "dominio", "pais", "empleados", "industria",
    "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
    "dm_email_catch_all", "dm_telefono", "origen_dm",
]


def _cargar(path: Path, defecto: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else defecto


def _guardar(path: Path, d: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def es_icp(cargo: str) -> bool:
    """El cargo cae en uno de los 5 vectores del ICP de Platzi Business.

    Hace falta porque `top_decision_makers` de Amplemarket, si no encuentra a
    nadie del ICP, cae a «cualquiera de la empresa»: así entraron cargos como
    «Vendedor en una tienda» o «Chief Nurse». Con créditos escasos no se gasta
    en ellos, y además desviarían el estándar de las 569 que ya están."""
    return ap._area(cargo or "") != "otro"


def pendientes(st: dict, apollo: dict, candidatos: list[dict]) -> list[dict]:
    """Empresas con decisor DEL ICP identificado y sin correo corporativo aún.

    Se saltan las que Amplemarket ya resolvió: si su DM #1 quedó con correo
    corporativo, esa empresa ya está servida (le falte o no el teléfono) y
    volver a revelarla en Apollo sería pagar dos veces por lo mismo.
    Se ordena por puntaje del cargo: primero L&D y RR.HH., que es donde el ICP
    dice que está el comprador."""
    meta = {c["dominio"]: c for c in candidatos}
    fuera = set(apollo.get("hechos", {}))
    out = []
    for dom, dms in st["dms"].items():
        if not dms or dom not in meta:
            continue
        if any(es_corporativo(st["emails"].get(d["linkedin"], {}).get("email", ""))
               for d in dms if d["linkedin"]):
            continue
        elegibles = [d for d in dms
                     if (d.get("linkedin") or "").strip() not in fuera
                     and (d.get("linkedin") or "").strip() and es_icp(d.get("cargo"))]
        if not elegibles:
            continue
        mejor = max(elegibles, key=lambda d: ap._score(d.get("cargo") or ""))
        out.append({**meta[dom], "dm_nombre": mejor["nombre"],
                    "dm_cargo": mejor["cargo"],
                    "dm_linkedin": mejor["linkedin"].strip()})
    out.sort(key=lambda f: -ap._score(f["dm_cargo"]))
    return out


def revelar(fila: dict, apollo: dict) -> None:
    """people/match por LinkedIn. Sin webhook NO se pide teléfono: pedirlo
    devuelve HTTP 400 y tumba también el correo (ver apollo_prospect)."""
    li = fila["dm_linkedin"]
    try:
        r = requests.post(f"{ap.APOLLO_BASE}/people/match",
                          headers=ap._headers(ap._key()),
                          json={"linkedin_url": li, "reveal_personal_emails": True},
                          timeout=45)
    except requests.RequestException as e:
        print(f"   {li[-32:]}: red {e.__class__.__name__}")
        return
    if r.status_code in (402, 403, 429):
        with _LOCK:
            apollo["freno"] = f"HTTP {r.status_code}: {r.text[:120]}"
        return
    if r.status_code != 200:
        return
    p = (r.json() or {}).get("person") or {}
    # Cobro real del match: 0 si Apollo no encontró a nadie, 1 por
    # demografía/correo y 9 si además devolvió celular (doc people-enrichment).
    # Antes se descontaba 1 fijo, así que el presupuesto iba corto justo en los
    # reveals caros y sobraba en los que no encontraron nada.
    creditos = _ap.creditos_de(p)
    if p and not _ap.confianza_suficiente(p):
        with _LOCK:
            apollo_budget.consume(creditos)
            apollo["hechos"][li] = {"email": "", "email_estado": "",
                                    "confianza": _ap.confianza(p),
                                    "nombre": fila["dm_nombre"],
                                    "cargo": fila["dm_cargo"], "empresa_apollo": ""}
        return
    with _LOCK:
        apollo_budget.consume(creditos)
        apollo["hechos"][li] = {
            "email": (p.get("email") or "").strip().lower(),
            "email_estado": p.get("email_status") or "",
            "nombre": p.get("name") or fila["dm_nombre"],
            "cargo": p.get("title") or fila["dm_cargo"],
            "empresa_apollo": (p.get("organization") or {}).get("name", ""),
            "confianza": _ap.confianza(p),
        }


def escribir(candidatos: list[dict], apollo: dict) -> dict:
    filas, stats = [], Counter()
    meta = {c["dominio"]: c for c in candidatos}
    st = _cargar(ESTADO, {"dms": {}})
    for dom, dms in st.get("dms", {}).items():
        if dom not in meta:
            continue
        for d in dms:
            got = apollo["hechos"].get((d.get("linkedin") or "").strip())
            if not got or not es_icp(d.get("cargo")):
                continue
            if not es_corporativo(got["email"]):
                stats["sin_correo_corporativo"] += 1
                continue
            c = meta[dom]
            filas.append({
                "fase": c["fase"], "empresa": c["empresa"], "dominio": dom,
                "pais": c["pais"], "empleados": c["empleados"],
                "industria": c["industria"],
                "dm_nombre": got["nombre"], "dm_cargo": got["cargo"],
                "dm_linkedin": d["linkedin"], "dm_email": got["email"],
                "dm_email_estado": got["email_estado"], "dm_email_catch_all": "",
                "dm_telefono": "",              # Apollo no lo da sin webhook
                "origen_dm": "apollo_sin_telefono",
            })
            stats["con_correo_corporativo"] += 1
            break
    with SALIDA.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(filas)
    return dict(stats)


def main() -> int:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--dry-run", action="store_true")
    ap_.add_argument("--objetivo", type=int, default=394)
    args = ap_.parse_args()

    if not ap._key():
        print("ERROR: falta APOLLO_API_KEY", file=sys.stderr)
        return 2

    st = _cargar(ESTADO, {"dms": {}, "emails": {}})
    apollo = _cargar(ESTADO_APOLLO, {"hechos": {}, "freno": ""})
    apollo.setdefault("hechos", {})
    candidatos = list(csv.DictReader((DATA / "candidatos.csv").open(encoding="utf-8")))
    pend = pendientes(st, apollo, candidatos)
    saldo = apollo_budget.status()

    print(f"Empresas con decisor y sin correo aún: {len(pend)}")
    print(f"Presupuesto Apollo: {saldo['remaining']} de {saldo['budget']} "
          f"(reportado {saldo['updated_at'][:10]})")
    print(f"Meta de contactos: {args.objetivo}")

    if args.dry_run:
        for f in pend[:5]:
            print(f"   ej · {f['dm_nombre']} · {f['dm_cargo']} · {f['empresa']}")
        print("\n(dry-run: no se gastó nada)")
        return 0

    cupo = min(len(pend), saldo["remaining"])
    if cupo <= 0:
        print("Sin presupuesto de Apollo. Reporta tu saldo real del dashboard "
              "antes de seguir.", file=sys.stderr)
        return 1
    if cupo < len(pend):
        print(f"AVISO: el presupuesto solo alcanza para {cupo} de {len(pend)}.")

    lote = pend[:cupo]
    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        for i in range(0, len(lote), 40):
            if apollo.get("freno"):
                print(f"   Apollo frenó: {apollo['freno']}")
                break
            list(pool.map(lambda f: revelar(f, apollo), lote[i:i + 40]))
            _guardar(ESTADO_APOLLO, apollo)
            corp = sum(1 for v in apollo["hechos"].values()
                       if es_corporativo(v["email"]))
            print(f"   {min(i + 40, len(lote))}/{len(lote)} · con correo "
                  f"corporativo: {corp}", flush=True)
            time.sleep(1)
    _guardar(ESTADO_APOLLO, apollo)

    stats = escribir(candidatos, apollo)
    print(f"\n→ {SALIDA.name}\n{json.dumps(stats, indent=1, ensure_ascii=False)}")
    print(f"Presupuesto Apollo restante: {apollo_budget.status()['remaining']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
