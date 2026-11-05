"""Corte final del lote: deja exactamente 200 empresas listas para entregar.

Tres filtros sobre `lote200_verificados.csv`:
  1. Fuera las que HubSpot reconoció aunque sea como Prospect. `verificar.py`
     las marca verdes (son aptas para outbound), pero el pedido era «que no
     estén en HubSpot», y eso es más estricto que «aptas».
  2. Rededup contra el cockpit por raíz de dominio, nombre de empresa Y URL de
     LinkedIn. El revelado ya excluía dominio y nombre, pero Apollo puede
     devolver una organización distinta a la consultada, así que el LinkedIn
     del decisor es la última red.
  3. Una sola empresa por raíz de dominio.

Ordena por puntaje del cargo (L&D y RR.HH. primero, que es donde el ICP dice
que está el comprador) y parte en 200 + excedente.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent / "backend"))

import apollo_prospect as ap                # noqa: E402
from candidatos import _raiz_dom            # noqa: E402

ENTRADA = DATA / "lote200_verificados.csv"
FINAL = DATA / "lote200_final.csv"
EXCEDENTE = DATA / "lote200_excedente.csv"

COLS = ["empresa", "dominio", "pais", "empleados", "industria", "area_icp",
        "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
        "verificacion"]


def cockpit() -> tuple[set[str], set[str], set[str]]:
    con = sqlite3.connect(HERE.parent / "backend" / "platzi_cockpit.db")
    doms, nombres, lis = set(), set(), set()
    for email, empresa, li in con.execute(
            "SELECT email, empresa, linkedin FROM contacts"):
        if email and "@" in email:
            doms.add(_raiz_dom(email.split("@", 1)[1]))
        if empresa:
            nombres.add(" ".join(empresa.lower().split()))
        if li:
            lis.add(li.strip().lower().rstrip("/"))
    con.close()
    return {d for d in doms if d}, nombres, lis


def main() -> int:
    filas = list(csv.DictReader(ENTRADA.open(encoding="utf-8")))
    doms_ck, nombres_ck, lis_ck = cockpit()
    fuera, vistos, out = Counter(), set(), []

    for f in filas:
        if "No está en HubSpot" not in f.get("hubspot_reason", ""):
            fuera["ya_figura_en_hubspot"] += 1
            continue
        raiz = _raiz_dom(f["dominio"])
        li = f["dm_linkedin"].strip().lower().rstrip("/")
        if raiz in doms_ck or " ".join(f["empresa"].lower().split()) in nombres_ck:
            fuera["ya_en_cockpit"] += 1
            continue
        if li in lis_ck:
            fuera["decisor_ya_en_cockpit"] += 1
            continue
        if raiz in vistos:
            fuera["dominio_repetido"] += 1
            continue
        vistos.add(raiz)
        out.append({
            "empresa": f["empresa"], "dominio": f["dominio"], "pais": f["pais"],
            "empleados": f["empleados"], "industria": f["industria"],
            "area_icp": ap._area(f["dm_cargo"]),
            "dm_nombre": f["dm_nombre"], "dm_cargo": f["dm_cargo"],
            "dm_linkedin": f["dm_linkedin"], "dm_email": f["dm_email"],
            "dm_email_estado": f["dm_email_estado"],
            "verificacion": "fuera de HubSpot (dominio+TLDs+nombre+correo) y "
                            "fuera del cockpit (dominio+nombre+LinkedIn)",
        })

    out.sort(key=lambda d: -ap._score(d["dm_cargo"]))
    for destino, trozo in ((FINAL, out[:200]), (EXCEDENTE, out[200:])):
        with destino.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            w.writerows(trozo)

    print(f"entradas {len(filas)} · descartes {dict(fuera)}")
    print(f"→ {FINAL.name}: {len(out[:200])} empresas")
    print(f"→ {EXCEDENTE.name}: {len(out[200:])} de reserva")
    print(f"área: {dict(Counter(d['area_icp'] for d in out[:200]))}")
    print(f"país: {dict(Counter(d['pais'] for d in out[:200]))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
