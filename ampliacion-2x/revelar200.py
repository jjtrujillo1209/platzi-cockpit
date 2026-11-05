"""Lote de 200 empresas nuevas: revela correo corporativo + LinkedIn vía Apollo.

Qué lo distingue de `apollo_revelar.py` (que fue el que armó la ampliación 2x):

  1. **Excluye lo que ya está en el cockpit.** El requisito de este lote es que
     la empresa no exista ni en HubSpot ni en el cockpit; `apollo_revelar` solo
     miraba HubSpot porque en ese momento el cockpit era el destino.
  2. **Prioriza los perfiles con `has_email: true`.** La búsqueda ofuscada de
     Apollo avisa gratis si tiene correo en ficha; revelar primero esos sube el
     rendimiento por crédito muy por encima del 71% de la corrida anterior.
  3. **Para al llegar a la meta.** No gasta el presupuesto completo si ya juntó
     las 200 empresas con correo corporativo.

Un solo decisor por empresa = un solo crédito por empresa. Apollo no entrega
teléfono sin webhook público, pero este lote no lo pide.

Uso:
  python3 revelar200.py --dry-run
  python3 revelar200.py [--meta 200]
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
from candidatos import es_corporativo, _raiz_dom  # noqa: E402

ESTADO = DATA / "estado.json"
EXTRA = DATA / "dms_apollo_extra.json"
HECHOS = DATA / "estado_lote200.json"
SALIDA = DATA / "lote200_revelados.csv"

HILOS = 4
_LOCK = threading.Lock()

OUT_FIELDS = [
    "fase", "empresa", "dominio", "pais", "empleados", "industria",
    "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
    "dm_email_catch_all", "dm_telefono", "origen_dm",
]


def _cargar(p: Path, d):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d


def _guardar(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _cockpit() -> tuple[set[str], set[str]]:
    """(raíces de dominio, nombres normalizados) que ya viven en el cockpit."""
    import sqlite3
    con = sqlite3.connect(HERE.parent / "backend" / "platzi_cockpit.db")
    doms, nombres = set(), set()
    for email, empresa in con.execute("SELECT email, empresa FROM contacts"):
        if email and "@" in email:
            doms.add(_raiz_dom(email.split("@", 1)[1]))
        if empresa:
            nombres.add(" ".join((empresa or "").lower().split()))
    con.close()
    return {d for d in doms if d}, nombres


def objetivos(st: dict, extra: dict, hechos: dict, meta_csv: dict) -> list[dict]:
    """Un decisor por empresa, el mejor del ICP, sin repetir lo ya revelado."""
    firm, dms = st["firmografia"], st["dms"]
    doms_ck, nombres_ck = _cockpit()
    ya_am = _cargar(DATA / "estado_apollo.json", {"hechos": {}})["hechos"]
    emails_am = st.get("emails", {})
    out = []

    for dom, f in firm.items():
        if (f.get("empleados") or 0) < 300:
            continue
        c = meta_csv.get(dom, {})
        nombre = (c.get("empresa") or f.get("nombre") or dom).strip()
        if _raiz_dom(dom) in doms_ck or " ".join(nombre.lower().split()) in nombres_ck:
            continue
        if dom in hechos:
            continue

        # pozo B · perfil de Apollo con correo en ficha (llave = apollo_id)
        cands = [{"llave": {"id": d["apollo_id"]}, "ref": d["apollo_id"],
                  "cargo": d["cargo"], "nombre": d.get("nombre_parcial", ""),
                  "linkedin": "", "prio": 0}
                 for d in extra.get(dom, [])]
        # pozo A · decisor que ya trajo Amplemarket (llave = linkedin_url)
        for d in dms.get(dom, []):
            li = (d.get("linkedin") or "").strip()
            if not li or li in ya_am or ap._area(d.get("cargo") or "") == "otro":
                continue
            if es_corporativo(emails_am.get(li, {}).get("email", "")):
                continue          # Amplemarket ya lo resolvió: no pagar dos veces
            cands.append({"llave": {"linkedin_url": li}, "ref": li,
                          "cargo": d.get("cargo") or "", "nombre": d.get("nombre") or "",
                          "linkedin": li, "prio": 1})
        if not cands:
            continue
        mejor = min(cands, key=lambda d: (d["prio"], -ap._score(d["cargo"])))
        out.append({
            "dominio": dom, "empresa": nombre,
            "pais": c.get("pais", ""), "empleados": f.get("empleados", ""),
            "industria": c.get("industria") or f.get("industria", ""),
            "fase": c.get("fase", "?"), **mejor,
        })
    out.sort(key=lambda d: (d["prio"], -ap._score(d["cargo"])))
    return out


def revelar(fila: dict, hechos: dict, freno: dict) -> None:
    try:
        r = requests.post(f"{ap.APOLLO_BASE}/people/match",
                          headers=ap._headers(ap._key()),
                          json={**fila["llave"], "reveal_personal_emails": True},
                          timeout=45)
    except requests.RequestException as e:
        print(f"   {fila['dominio']}: red {e.__class__.__name__}", flush=True)
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
        apollo_budget.consume(1)         # match exitoso = 1 crédito real
        hechos[fila["dominio"]] = {
            "email": (p.get("email") or "").strip().lower(),
            "email_estado": p.get("email_status") or "",
            "nombre": p.get("name") or fila["nombre"],
            "cargo": p.get("title") or fila["cargo"],
            "linkedin": (p.get("linkedin_url") or fila["linkedin"] or "").strip(),
            "pais": p.get("country") or org.get("country") or "",
            "ciudad": p.get("city") or "",
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
        filas.append({
            "fase": f["fase"], "empresa": got["empresa_apollo"] or f["empresa"],
            "dominio": f["dominio"], "pais": f["pais"] or got["pais"],
            "empleados": f["empleados"],
            "industria": f["industria"] or got["industria_apollo"],
            "dm_nombre": got["nombre"], "dm_cargo": got["cargo"],
            "dm_linkedin": got["linkedin"], "dm_email": got["email"],
            "dm_email_estado": got["email_estado"], "dm_email_catch_all": "",
            "dm_telefono": "", "origen_dm": "apollo_lote200",
        })
    with SALIDA.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(filas)
    return dict(stats)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--meta", type=int, default=200)
    args = a.parse_args()

    if not ap._key():
        print("ERROR: falta APOLLO_API_KEY", file=sys.stderr)
        return 2

    st = _cargar(ESTADO, {"firmografia": {}, "dms": {}, "emails": {}})
    extra = _cargar(EXTRA, {})
    hechos = _cargar(HECHOS, {})
    meta_csv = {r["dominio"]: r for r in
                csv.DictReader((DATA / "candidatos.csv").open(encoding="utf-8"))}
    objs = objetivos(st, extra, hechos, meta_csv)
    saldo = apollo_budget.status()

    pozoB = sum(1 for o in objs if o["prio"] == 0)
    print(f"Empresas candidatas (fuera de cockpit, >=300 empl., con decisor ICP): {len(objs)}")
    print(f"   con `has_email` de Apollo: {pozoB} · solo LinkedIn de Amplemarket: {len(objs) - pozoB}")
    print(f"Ya reveladas en corridas previas de este lote: {len(hechos)}")
    print(f"Presupuesto Apollo: {saldo['remaining']} de {saldo['budget']} · meta {args.meta}")

    if args.dry_run:
        for o in objs[:6]:
            print(f"   ej · {o['cargo']} · {o['empresa']} ({o['empleados']} empl.)")
        print("\n(dry-run: no se gastó nada)")
        return 0
    if saldo["remaining"] <= 0:
        print("Sin presupuesto de Apollo.", file=sys.stderr)
        return 1

    freno = {"motivo": ""}
    utiles = sum(1 for v in hechos.values()
                 if es_corporativo(v["email"]) and v["linkedin"])
    for i in range(0, len(objs), 40):
        if freno["motivo"] or utiles >= args.meta:
            break
        cupo = min(40, apollo_budget.status()["remaining"], len(objs) - i)
        if cupo <= 0:
            print("   presupuesto agotado")
            break
        with ThreadPoolExecutor(max_workers=HILOS) as pool:
            list(pool.map(lambda f: revelar(f, hechos, freno), objs[i:i + cupo]))
        _guardar(HECHOS, hechos)
        utiles = sum(1 for v in hechos.values()
                     if es_corporativo(v["email"]) and v["linkedin"])
        print(f"   revelados {len(hechos)} · con correo corporativo + LinkedIn: "
              f"{utiles}/{args.meta} · créditos restantes "
              f"{apollo_budget.status()['remaining']}", flush=True)
        time.sleep(1)
    if freno["motivo"]:
        print(f"   Apollo frenó: {freno['motivo']}")
    _guardar(HECHOS, hechos)

    stats = escribir(objs, hechos)
    print(f"\n→ {SALIDA.name}\n{json.dumps(stats, indent=1, ensure_ascii=False)}")
    print(f"Créditos Apollo restantes: {apollo_budget.status()['remaining']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
