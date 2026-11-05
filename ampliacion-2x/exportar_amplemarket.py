"""Deja el lote listo para cargar en Amplemarket.

Los nombres de las columnas NO son decorativos: las 20 secuencias de la cuenta
usan exactamente tres campos dinámicos —`first_name` (20 de 20), `company_name`
(17) y `email` (1)—, así que las columnas se llaman así para que los merge tags
resuelvan sin remapear nada a mano al importar. El resto viaja como variables de
contexto (Amplemarket admite hasta 50 por lead, clave en minúscula).

Se exportan las dos llaves válidas de Amplemarket, `email` y `linkedin_url`, en
ese orden: si un correo rebota, el lead sigue siendo alcanzable por LinkedIn.

Nombres: el primer token es el `first_name` con el que saluda la secuencia. Se
descartan prefijos que no son nombre («Ferrum-Ing Daniel Quesada» → Daniel).

Salida: PLATZI/240_amplemarket_import.csv
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRADA = HERE / "data" / "lote240_final.csv"
SALIDA = HERE.parent.parent / "240_amplemarket_import.csv"

COLS = ["email", "linkedin_url", "first_name", "last_name", "full_name", "title",
        "company_name", "company_domain", "country", "employees", "industry",
        "icp_area", "email_status", "source"]

AREA = {"rrhh": "RRHH / People", "ventas": "Ventas / Revenue",
        "tecnologia": "Tecnologia", "ld": "L&D / Capacitacion",
        "direccion": "Direccion"}


def partir(nombre: str) -> tuple[str, str]:
    """(first_name, last_name). Descarta prefijos que no son nombre de pila."""
    tokens = [t for t in nombre.split() if t]
    while len(tokens) > 2 and (("-" in tokens[0]) or any(c.isdigit() for c in tokens[0])):
        tokens.pop(0)
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return tokens[0], ""
    return tokens[0], " ".join(tokens[1:])


def main() -> int:
    filas = list(csv.DictReader(ENTRADA.open(encoding="utf-8")))
    out = []
    for f in filas:
        first, last = partir(f["dm_nombre"])
        out.append({
            "email": f["dm_email"],
            "linkedin_url": f["dm_linkedin"],
            "first_name": first,
            "last_name": last,
            "full_name": f["dm_nombre"],
            "title": re.sub(r"\s+", " ", f["dm_cargo"]).strip(),
            "company_name": f["empresa"],
            "company_domain": f["dominio"],
            "country": f["pais"],
            "employees": f["empleados"],
            "industry": f["industria"],
            "icp_area": AREA.get(f["area_icp"], f["area_icp"]),
            "email_status": f["dm_email_estado"],
            "source": f"platzi-lote240-{f['origen']}",
        })
    with SALIDA.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)

    sin_first = [r for r in out if not r["first_name"]]
    largos = [r for r in out if len(r["title"]) > 1024]
    print(f"→ {SALIDA}")
    print(f"   {len(out)} leads · {len({r['email'] for r in out})} correos únicos")
    print(f"   sin first_name: {len(sin_first)} · title >1024 car.: {len(largos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
