"""Une el lote de 200 con las rescatadas y deja un único entregable.

Las rescatadas se clasifican con el `_area_ext()` de `rescatar.py`, no con el
`_area()` del cockpit: son justamente las que el clasificador oficial mandaba a
«otro» («Jefe de Selección y Contratación», «Gerente Sênior de TI»). La columna
`clasificador` deja constancia de cuál se usó en cada fila.
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
sys.path.insert(0, str(HERE))

import apollo_prospect as ap                # noqa: E402
from candidatos import _raiz_dom            # noqa: E402
from rescatar import _area_ext              # noqa: E402

FINAL = DATA / "lote240_final.csv"
COLS = ["empresa", "dominio", "pais", "empleados", "industria", "area_icp",
        "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
        "origen", "clasificador", "verificacion"]
VERIF = ("fuera de HubSpot (dominio+TLDs+nombre+correo) y "
         "fuera del cockpit (dominio+nombre+LinkedIn)")


def cockpit():
    con = sqlite3.connect(HERE.parent / "backend" / "platzi_cockpit.db")
    doms, nombres, lis = set(), set(), set()
    for email, empresa, li in con.execute("SELECT email, empresa, linkedin FROM contacts"):
        if email and "@" in email:
            doms.add(_raiz_dom(email.split("@", 1)[1]))
        if empresa:
            nombres.add(" ".join(empresa.lower().split()))
        if li:
            lis.add(li.strip().lower().rstrip("/"))
    con.close()
    return {d for d in doms if d}, nombres, lis


def main() -> int:
    doms_ck, nombres_ck, lis_ck = cockpit()
    fuera, vistos, out = Counter(), set(), []

    base = list(csv.DictReader((DATA / "lote200_final.csv").open(encoding="utf-8")))
    resc = list(csv.DictReader((DATA / "rescate_verificados.csv").open(encoding="utf-8")))

    for f in base:
        vistos.add(_raiz_dom(f["dominio"]))
        out.append({**{k: f.get(k, "") for k in COLS},
                    "origen": "lote-200", "clasificador": "cockpit",
                    "verificacion": VERIF})

    for f in resc:
        if "No está en HubSpot" not in f.get("hubspot_reason", ""):
            fuera["ya_figura_en_hubspot"] += 1
            continue
        raiz = _raiz_dom(f["dominio"])
        li = f["dm_linkedin"].strip().lower().rstrip("/")
        if raiz in vistos:
            fuera["repetida_del_lote_200"] += 1
            continue
        if raiz in doms_ck or " ".join(f["empresa"].lower().split()) in nombres_ck:
            fuera["ya_en_cockpit"] += 1
            continue
        if li in lis_ck:
            fuera["decisor_ya_en_cockpit"] += 1
            continue
        vistos.add(raiz)
        oficial = ap._area(f["dm_cargo"])
        out.append({
            "empresa": f["empresa"], "dominio": f["dominio"], "pais": f["pais"],
            "empleados": f["empleados"], "industria": f["industria"],
            "area_icp": _area_ext(f["dm_cargo"]),
            "dm_nombre": f["dm_nombre"], "dm_cargo": f["dm_cargo"],
            "dm_linkedin": f["dm_linkedin"], "dm_email": f["dm_email"],
            "dm_email_estado": f["dm_email_estado"], "origen": "rescate",
            "clasificador": "cockpit" if oficial != "otro" else "extendido",
            "verificacion": VERIF,
        })

    out.sort(key=lambda d: (d["origen"] != "lote-200", -ap._score(d["dm_cargo"])))
    with FINAL.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)

    print(f"→ {FINAL.name}: {len(out)} empresas · descartes {dict(fuera)}")
    print(f"origen: {dict(Counter(d['origen'] for d in out))}")
    print(f"clasificador: {dict(Counter(d['clasificador'] for d in out))}")
    print(f"área: {dict(Counter(d['area_icp'] for d in out))}")
    print(f"país: {dict(Counter(d['pais'] for d in out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
