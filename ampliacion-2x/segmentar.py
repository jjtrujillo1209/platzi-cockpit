"""Segmenta la base completa del cockpit por país y exporta un CSV por país.

Cortes por país: contactos, empresas, cobertura de los 3 datos (correo
corporativo · teléfono · LinkedIn), área del ICP del cargo, tamaño de empresa y
veredicto de HubSpot. Los «accionables» son los que tienen correo: sin correo no
hay outbound posible, tengan o no teléfono.

El tamaño de empresa no vive en el cockpit: se cruza por dominio contra
`base_2000_validada_amplemarket.csv` (empleados reales) y contra la firmografía
que dejó el revelado en `data/estado.json`.

Uso: python3 segmentar.py
"""
from __future__ import annotations

import collections
import csv
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DB = HERE.parent / "backend" / "platzi_cockpit.db"
P2000 = HERE.parent.parent / "prospeccion-2000"
sys.path.insert(0, str(HERE.parent / "backend"))
import apollo_prospect as ap                      # noqa: E402
from candidatos import es_corporativo             # noqa: E402

SALIDA = DATA / "segmentacion_paises.json"
POR_PAIS = DATA / "por_pais"

AREA_NOMBRE = {"ld": "L&D / Capacitación", "rrhh": "RR.HH. / Talento",
               "tecnologia": "Tecnología", "ventas": "Ventas / Revenue",
               "direccion": "Dirección / C-Level", "otro": "sin clasificar"}


def empleados_por_dominio() -> dict[str, int]:
    """Empleados REALES por dominio, de las dos fuentes que los midieron."""
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


def tramo(n: int) -> str:
    if not n:
        return "sin dato"
    if n < 300:
        return "<300"
    if n < 1000:
        return "300–999"
    if n < 5000:
        return "1.000–4.999"
    return "5.000+"


def main() -> int:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    filas = list(db.execute("SELECT * FROM contacts"))
    empl = empleados_por_dominio()
    POR_PAIS.mkdir(exist_ok=True)

    por_pais: dict[str, list] = collections.defaultdict(list)
    for f in filas:
        por_pais[f["pais"] or "sin país"].append(f)

    resumen = []
    for pais, rs in sorted(por_pais.items(), key=lambda kv: -len(kv[1])):
        dominios = {(r["email"] or "").split("@")[-1].lower() for r in rs if r["email"]}
        tam = collections.Counter(
            tramo(empl.get((r["email"] or "").split("@")[-1].lower(), 0)) for r in rs)
        resumen.append({
            "pais": pais,
            "contactos": len(rs),
            "empresas": len({(r["empresa"] or "").strip().lower() for r in rs if r["empresa"]}),
            "con_correo_corporativo": sum(1 for r in rs if es_corporativo(r["email"] or "")),
            "con_telefono": sum(1 for r in rs if (r["celular"] or "").strip()),
            "con_linkedin": sum(1 for r in rs if (r["linkedin"] or "").strip()),
            "completos_3_datos": sum(1 for r in rs
                                     if es_corporativo(r["email"] or "")
                                     and (r["celular"] or "").strip()
                                     and (r["linkedin"] or "").strip()),
            "verde": sum(1 for r in rs if r["hubspot_verdict"] == "verde"),
            "amarillo": sum(1 for r in rs if r["hubspot_verdict"] == "amarillo"),
            "sin_cruzar": sum(1 for r in rs if not (r["hubspot_verdict"] or "")),
            "areas": {AREA_NOMBRE[k]: v for k, v in
                      collections.Counter(ap._area(r["cargo"] or "") for r in rs).most_common()},
            "tamano": dict(tam),
            "lotes": dict(collections.Counter(r["campaign"] or "base original" for r in rs)),
        })

        arch = POR_PAIS / f"{pais.lower().replace(' ', '_')}.csv"
        with arch.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["nombre", "cargo", "area_icp", "empresa", "empleados",
                        "email", "telefono", "linkedin", "hubspot", "lote"])
            for r in sorted(rs, key=lambda x: -ap._score(x["cargo"] or "")):
                dom = (r["email"] or "").split("@")[-1].lower()
                w.writerow([r["nombre"], r["cargo"], AREA_NOMBRE[ap._area(r["cargo"] or "")],
                            r["empresa"], empl.get(dom, ""), r["email"], r["celular"],
                            r["linkedin"], r["hubspot_verdict"],
                            r["campaign"] or "base original"])

    SALIDA.write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'país':<14}{'cont':>6}{'empr':>6}{'correo':>8}{'tel':>6}{'3 datos':>9}{'verde':>7}")
    print("-" * 56)
    for r in resumen:
        print(f"{r['pais']:<14}{r['contactos']:>6}{r['empresas']:>6}"
              f"{r['con_correo_corporativo']:>8}{r['con_telefono']:>6}"
              f"{r['completos_3_datos']:>9}{r['verde']:>7}")
    t = {k: sum(r[k] for r in resumen) for k in
         ("contactos", "empresas", "con_correo_corporativo", "con_telefono",
          "completos_3_datos", "verde")}
    print("-" * 56)
    print(f"{'TOTAL':<14}{t['contactos']:>6}{t['empresas']:>6}"
          f"{t['con_correo_corporativo']:>8}{t['con_telefono']:>6}"
          f"{t['completos_3_datos']:>9}{t['verde']:>7}")
    print(f"\n→ {SALIDA.name} · {POR_PAIS.name}/ ({len(resumen)} CSV)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
