"""Exporta la base del cockpit en dos CSV, partida por idioma de trabajo.

  latam_sin_brasil.csv  → todo LATAM en español
  solo_brasil.csv       → Brasil, que necesita portugués

El corte es por idioma, no por geografía: es la partición que sirve para repartir
los remitentes (un remitente que no habla portugués no puede trabajar Brasil).

El área del ICP la decide `sdr-voz/backend/importer.py` (432 cargos etiquetados a
mano, 98,8% de exactitud medida) y NO el `_area()` del cockpit, que tiene un bug
de substring: «informacion» contiene «formacion», así que clasificaba todo cargo
de «Tecnologías de la Información» como L&D. El código del cockpit ya excluye
«transformacion» por esa misma razón, pero olvidó «informacion». El cockpit queda
solo como respaldo cuando sdr-voz no puede clasificar. Se corren en procesos
separados porque ambos módulos tienen un `config.py` que colisiona en sys.path.

Los 15 contactos sin país quedan FUERA de los dos archivos: no tienen correo, sus
prefijos son de Europa/África/Asia y sus empresas se llaman «TBD» o «Yo». Salen
aparte en `sin_pais_revisar.csv` para borrarlos, no para trabajarlos.

Uso:
  python3 ../../sdr-voz/backend/… (ver LEER.md)  # genera los JSON de clasificación
  python3 exportar_latam.py
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DB = HERE.parent / "backend" / "platzi_cockpit.db"
P2000 = HERE.parent.parent / "prospeccion-2000"

AREA_COCKPIT = Path("/tmp/_area_cockpit.json")   # respaldo
AREA_SDR = Path("/tmp/_sdr_todos.json")          # primario

NOMBRE = {"ld": "L&D / Capacitación", "rrhh": "RR.HH. / Talento",
          "tecnologia": "Tecnología", "ventas": "Ventas / Revenue",
          "direccion": "Dirección / C-Level"}

# Orden de prioridad del ICP: quién se trabaja primero dentro de cada país.
PESO = {"L&D / Capacitación": 5, "RR.HH. / Talento": 4, "Tecnología": 3,
        "Ventas / Revenue": 2, "Dirección / C-Level": 1,
        "fuera del ICP": 0, "ilegible / vacío": 0}

CAMPOS = ["pais", "nombre", "cargo", "area_icp", "empresa", "empleados",
          "email", "telefono", "linkedin", "hubspot", "lote"]


def empleados_por_dominio() -> dict[str, int]:
    out: dict[str, int] = {}
    f = P2000 / "base_2000_validada_amplemarket.csv"
    if f.exists():
        for r in csv.DictReader(f.open(encoding="utf-8")):
            try:
                n = int(float(r.get("am_empleados") or 0))
            except ValueError:
                n = 0
            if n:
                out[r["dominio"].strip().lower()] = n
    est = DATA / "estado.json"
    if est.exists():
        for dom, v in json.loads(est.read_text(encoding="utf-8")).get("firmografia", {}).items():
            if v.get("empleados"):
                out.setdefault(dom.strip().lower(), v["empleados"])
    return out


def area_de(cid: int, cockpit: dict, sdr: dict) -> str:
    r = sdr.get(str(cid))
    if r and r[1] == "vector":
        return NOMBRE[r[0]]
    if r and r[1] == "fuera_icp":
        return "fuera del ICP"
    a = cockpit.get(str(cid), "otro")          # respaldo
    return NOMBRE[a] if a != "otro" else "ilegible / vacío"


def escribir(ruta: Path, filas: list[dict]) -> None:
    filas.sort(key=lambda r: (r["pais"], -PESO.get(r["area_icp"], 0), r["empresa"]))
    with ruta.open("w", newline="", encoding="utf-8-sig") as fh:   # BOM: Excel en es-CO
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def resumen(nombre: str, filas: list[dict]) -> None:
    icp = [r for r in filas if r["area_icp"] not in ("fuera del ICP", "ilegible / vacío")]
    print(f"\n{nombre}: {len(filas)} contactos · {len({r['empresa'].lower() for r in filas})} empresas")
    print(f"  dentro del ICP: {len(icp)} ({len(icp)/max(len(filas),1):.0%})")
    print(f"  con correo: {sum(1 for r in filas if r['email'])} · "
          f"con teléfono: {sum(1 for r in filas if r['telefono'])} · "
          f"verdes: {sum(1 for r in filas if r['hubspot'] == 'verde')}")
    print(f"  países: {dict(Counter(r['pais'] for r in filas).most_common())}")
    print(f"  áreas: {dict(Counter(r['area_icp'] for r in filas).most_common())}")


def main() -> int:
    for f in (AREA_COCKPIT, AREA_SDR):
        if not f.exists():
            print(f"ERROR: falta {f} — ver la cabecera de este archivo", file=sys.stderr)
            return 2
    cockpit = json.loads(AREA_COCKPIT.read_text(encoding="utf-8"))["area"]
    sdr = json.loads(AREA_SDR.read_text(encoding="utf-8"))
    empl = empleados_por_dominio()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    latam, brasil, sin_pais = [], [], []
    for c in db.execute("SELECT * FROM contacts"):
        dom = (c["email"] or "").split("@")[-1].lower()
        fila = {
            "pais": c["pais"] or "",
            "nombre": c["nombre"] or "", "cargo": c["cargo"] or "",
            "area_icp": area_de(c["id"], cockpit, sdr),
            "empresa": c["empresa"] or "", "empleados": empl.get(dom, ""),
            "email": c["email"] or "", "telefono": c["celular"] or "",
            "linkedin": c["linkedin"] or "", "hubspot": c["hubspot_verdict"] or "",
            "lote": c["campaign"] or "base original",
        }
        if not fila["pais"]:
            sin_pais.append(fila)
        elif fila["pais"] == "Brazil":
            fila["pais"] = "Brasil"
            brasil.append(fila)
        else:
            latam.append(fila)

    escribir(DATA / "latam_sin_brasil.csv", latam)
    escribir(DATA / "solo_brasil.csv", brasil)
    escribir(DATA / "sin_pais_revisar.csv", sin_pais)

    resumen("latam_sin_brasil.csv", latam)
    resumen("solo_brasil.csv", brasil)
    print(f"\nsin_pais_revisar.csv: {len(sin_pais)} registros sin país "
          f"(basura de la base original, no van a outreach)")
    print(f"\nsuma: {len(latam)} + {len(brasil)} + {len(sin_pais)} = "
          f"{len(latam)+len(brasil)+len(sin_pais)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
