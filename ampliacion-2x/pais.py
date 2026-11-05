"""Resuelve el PAÍS de cada contacto del cockpit y lo guarda en su propia columna.

Hacía falta porque la base quedó sin país utilizable: las 569 originales nunca lo
guardaron y los 407 nuevos lo traen en `ciudad`, que es el campo equivocado (la UI
muestra «Mexico» donde debería ir una ciudad).

Cadena de resolución, de la señal más fuerte a la más débil. Se registra en
`pais_fuente` para poder auditar cada fila:
  1 empresa en la base madre (`base_2000`), cruzando por el dominio del correo y
    por el mapa correo→dominio del CSV de revelados;
  2 TLD del dominio corporativo (.com.co, .mx, .com.ar…);
  3 prefijo telefónico, pero SOLO de LATAM: hay 25 ejecutivos de la región con
    móvil +1, y el país de la empresa pesa más que el del teléfono;
  4 voto por empresa: si otro contacto de la misma compañía ya tiene país, hereda.

Uso:
  python3 pais.py --dry-run
  python3 pais.py
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "backend" / "platzi_cockpit.db"
P2000 = HERE.parent.parent / "prospeccion-2000"

PAISES_LATAM = {"Colombia", "Mexico", "Peru", "Argentina", "Chile", "Brazil",
                "Ecuador", "Uruguay", "Bolivia", "Paraguay", "Costa Rica",
                "Panama", "Guatemala", "El Salvador", "Honduras",
                "República Dominicana"}

# Solo prefijos de LATAM: +1 y +34 se ignoran a propósito (ver docstring).
PREFIJO = {"57": "Colombia", "52": "Mexico", "51": "Peru", "54": "Argentina",
           "56": "Chile", "55": "Brazil", "593": "Ecuador", "598": "Uruguay",
           "591": "Bolivia", "595": "Paraguay", "506": "Costa Rica",
           "507": "Panama", "502": "Guatemala", "503": "El Salvador",
           "504": "Honduras", "809": "República Dominicana"}

TLD = {".co": "Colombia", ".com.co": "Colombia", ".mx": "Mexico",
       ".com.mx": "Mexico", ".com.pe": "Peru", ".pe": "Peru",
       ".com.ar": "Argentina", ".ar": "Argentina", ".cl": "Chile",
       ".com.br": "Brazil", ".br": "Brazil", ".com.ec": "Ecuador",
       ".com.uy": "Uruguay", ".com.bo": "Bolivia", ".com.py": "Paraguay",
       ".cr": "Costa Rica", ".com.pa": "Panama", ".com.gt": "Guatemala",
       ".com.do": "República Dominicana"}


def _dom(email: str) -> str:
    return (email or "").split("@")[-1].strip().lower()


def por_tld(email: str) -> str:
    d = _dom(email)
    for suf in sorted(TLD, key=len, reverse=True):
        if d.endswith(suf):
            return TLD[suf]
    return ""


def por_telefono(tel: str) -> str:
    d = re.sub(r"[^0-9+]", "", tel or "")
    if not d.startswith("+"):
        return ""
    d = d[1:]
    for n in (3, 2):
        if d[:n] in PREFIJO:
            return PREFIJO[d[:n]]
    return ""


def mapas_base_madre() -> tuple[dict, dict]:
    """(dominio→país, correo→país) a partir de la base de 2.000 y sus revelados."""
    pais_dom, pais_email = {}, {}
    f = P2000 / "base_2000_empresas_icp_platzi_no_hubspot.csv"
    if f.exists():
        for r in csv.DictReader(f.open(encoding="utf-8")):
            if r.get("pais"):
                pais_dom[r["dominio"].strip().lower()] = r["pais"].strip()
    fr = P2000 / "base_2000_revelada_calificadas.csv"
    if fr.exists():
        for r in csv.DictReader(fr.open(encoding="utf-8")):
            p = pais_dom.get(r["dominio"].strip().lower())
            if p and r.get("dm_email"):
                pais_email[r["dm_email"].strip().lower()] = p
    return pais_dom, pais_email


def resolver(filas: list[sqlite3.Row]) -> tuple[dict, dict]:
    pais_dom, pais_email = mapas_base_madre()
    pais, fuente = {}, {}

    for f in filas:
        email = (f["email"] or "").strip().lower()
        ciudad = (f["ciudad"] or "").strip()
        if ciudad in PAISES_LATAM:
            pais[f["id"]], fuente[f["id"]] = ciudad, "ya registrado"
        elif email in pais_email:
            pais[f["id"]], fuente[f["id"]] = pais_email[email], "correo en base madre"
        elif _dom(email) in pais_dom:
            pais[f["id"]], fuente[f["id"]] = pais_dom[_dom(email)], "dominio en base madre"
        elif por_tld(email):
            pais[f["id"]], fuente[f["id"]] = por_tld(email), "TLD del correo"
        elif por_telefono(f["celular"]):
            pais[f["id"]], fuente[f["id"]] = por_telefono(f["celular"]), "prefijo telefónico"
        else:
            pais[f["id"]], fuente[f["id"]] = "", "sin resolver"

    # voto por empresa: hereda de un colega de la misma compañía ya resuelto
    votos: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for f in filas:
        if pais[f["id"]]:
            votos[(f["empresa"] or "").strip().lower()][pais[f["id"]]] += 1
    for f in filas:
        if not pais[f["id"]]:
            v = votos.get((f["empresa"] or "").strip().lower())
            if v:
                pais[f["id"]], fuente[f["id"]] = v.most_common(1)[0][0], "voto por empresa"
    return pais, fuente


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    cols = {r["name"] for r in db.execute("PRAGMA table_info(contacts)")}
    filas = list(db.execute(
        "SELECT id, email, celular, ciudad, empresa, campaign FROM contacts"))
    pais, fuente = resolver(filas)

    print(f"Contactos: {len(filas)}")
    for p, n in collections.Counter(v or "(sin país)" for v in pais.values()).most_common():
        print(f"   {p:26} {n:4}")
    print("\nfuente:", dict(collections.Counter(fuente.values())))

    if args.dry_run:
        print("\n(dry-run: no se escribió nada)")
        return 0

    if "pais" not in cols:
        db.execute("ALTER TABLE contacts ADD COLUMN pais TEXT DEFAULT ''")
    if "pais_fuente" not in cols:
        db.execute("ALTER TABLE contacts ADD COLUMN pais_fuente TEXT DEFAULT ''")
    for f in filas:
        # el país sale de `ciudad`: se limpia para que la UI no muestre un país
        # donde va una ciudad. El dato no se pierde, se muda a su columna.
        nueva_ciudad = "" if (f["ciudad"] or "").strip() in PAISES_LATAM else f["ciudad"]
        db.execute("UPDATE contacts SET pais=?, pais_fuente=?, ciudad=? WHERE id=?",
                   (pais[f["id"]], fuente[f["id"]], nueva_ciudad, f["id"]))
    db.commit()
    print(f"\nEscrito: columnas pais + pais_fuente en {len(filas)} contactos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
