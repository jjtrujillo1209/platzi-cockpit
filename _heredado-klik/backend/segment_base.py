"""Fusiona la validación inicial (217) con la re-validación V2 (dudosos) y
segmenta la base SIN_ENERXIA por ESTADO DE VALIDACIÓN.

Veredicto final por contacto = el de V2 si fue re-validado; si no, el original.
Segmentos:
  Verificado    -> match | partial   (link OK, contactar)
  Corregir link -> mismatch          (el perfil es de otra empresa)
  Link muerto   -> not_found | empty_profile | error
Sin LinkedIn  -> contactos sin URL (no aplican a validación)

Salida en KLIK-COMPLETO/:
  BASE_VERIFICADO.csv, BASE_CORREGIR_LINK.csv, BASE_LINK_MUERTO.csv,
  BASE_SEGMENTADA.csv (base completa + columnas VALIDACION_ESTADO/EMPRESA_LI/SCORE)
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

DIR = Path("/Users/juanjosetrujillo/Documents/Ai programas/KLIK-COMPLETO")
BASE = DIR / "LINKEDIN_OUTREACH_BASE_SIN_ENERXIA.csv"
V1 = DIR / "VALIDACION_LINKEDIN.csv"
V2 = DIR / "VALIDACION_REVISAR_V2.csv"

SEG = {
    "match": "Verificado", "partial": "Verificado",
    "mismatch": "Corregir link",
    "not_found": "Link muerto", "empty_profile": "Link muerto", "error": "Link muerto",
    "rate_limited": "Link muerto",
}


def slug(u: str) -> str:
    m = re.search(r"/in/([^/?#]+)", (u or "").lower())
    return m.group(1) if m else ""


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {slug(r["url"]): r for r in csv.DictReader(open(path, encoding="utf-8-sig"))}


def main() -> None:
    v1, v2 = load(V1), load(V2)
    base = list(csv.DictReader(open(BASE, encoding="utf-8-sig")))
    fields = list(base[0].keys()) + ["VALIDACION_ESTADO", "VALIDACION_EMPRESA_LI",
                                     "VALIDACION_SCORE", "VALIDACION_VEREDICTO"]

    buckets: dict[str, list[dict]] = {"Verificado": [], "Corregir link": [],
                                      "Link muerto": [], "Sin LinkedIn": []}
    full = []
    for r in base:
        s = slug(r.get("LINKEDIN_PROFILE", ""))
        if not s:
            estado, ver, emp, sc = "Sin LinkedIn", "", "", ""
        else:
            res = v2.get(s) or v1.get(s)  # V2 manda sobre V1
            if not res:
                estado, ver, emp, sc = "Link muerto", "no_validado", "", ""
            else:
                ver = res["veredicto"]
                estado = SEG.get(ver, "Link muerto")
                emp, sc = res.get("empresa_linkedin", ""), res.get("score", "")
        row = dict(r)
        row.update({"VALIDACION_ESTADO": estado, "VALIDACION_EMPRESA_LI": emp,
                    "VALIDACION_SCORE": sc, "VALIDACION_VEREDICTO": ver})
        buckets[estado].append(row)
        full.append(row)

    def dump(name: str, rows: list[dict]):
        with open(DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)

    dump("BASE_SEGMENTADA.csv", full)
    dump("BASE_VERIFICADO.csv", buckets["Verificado"])
    dump("BASE_CORREGIR_LINK.csv", buckets["Corregir link"])
    dump("BASE_LINK_MUERTO.csv", buckets["Link muerto"])

    print(f"TOTAL base: {len(base)}")
    for k, v in buckets.items():
        print(f"  {k:14} {len(v)}")
    print("\nArchivos: BASE_SEGMENTADA.csv + BASE_VERIFICADO / BASE_CORREGIR_LINK / BASE_LINK_MUERTO")


if __name__ == "__main__":
    main()
