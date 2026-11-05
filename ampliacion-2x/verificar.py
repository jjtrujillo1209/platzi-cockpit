"""Verifica en vivo contra HubSpot los contactos revelados, en dos niveles.

  Nivel EMPRESA · ¿la compañía ya existe en el CRM? Reusa el verificador de
    prospeccion-2000 (dominio + variantes de TLD + contactos con ese dominio +
    segunda pasada por nombre). Es el que da la garantía dura de la base madre.
  Nivel PERSONA · ¿ese correo ya está en el CRM? Reusa hubspot_check del cockpit,
    que además mira lifecyclestage y deals abiertos:
      🟢 verde apto · 🟡 amarillo coordinar · 🔴 rojo no tocar.

Solo lectura sobre HubSpot: search y batch/read, ni una escritura.

Salida: data/verificados.csv (todo, con veredicto) + data/expulsados.csv
Uso: python3 verificar.py
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAIZ = HERE.parent.parent
sys.path.insert(0, str(RAIZ / "prospeccion-2000"))
sys.path.insert(0, str(RAIZ / "cockpit" / "backend"))

import verify_no_hubspot as vh          # noqa: E402  nivel empresa
import hubspot_check as hc              # noqa: E402  nivel persona

ENTRADA = DATA / "revelados.csv"
SALIDA = DATA / "verificados.csv"
EXPULSADOS = DATA / "expulsados.csv"

# La base madre era CO/MX; con LATAM completo hay que mirar los TLD del resto de
# la región, o «acme.com.ar» pasaría como empresa nueva estando ya en el CRM.
vh.TLDS = (".com", ".com.co", ".co", ".com.mx", ".mx", ".com.ar", ".ar",
           ".cl", ".com.pe", ".pe", ".com.br", ".com.ec", ".ec", ".com.uy",
           ".com.py", ".com.bo", ".cr", ".com.pa", ".com.gt", ".com.do",
           ".net", ".io", ".app")

LOTE = 100


def _headers() -> dict:
    return {"Authorization": f"Bearer {hc._token()}", "Content-Type": "application/json"}


def correos_en_crm(emails: list[str]) -> set[str]:
    """Cuáles de estos correos ya existen en HubSpot (búsqueda por lotes de 100)."""
    dentro: set[str] = set()
    for i in range(0, len(emails), LOTE):
        lote = emails[i:i + LOTE]
        try:
            r = requests.post(
                f"https://api.hubapi.com/crm/v3/objects/contacts/search",
                headers=_headers(),
                json={"filterGroups": [{"filters": [
                          {"propertyName": "email", "operator": "IN", "values": lote}]}],
                      "properties": ["email"], "limit": LOTE}, timeout=30)
            if r.status_code != 200:
                print(f"  lote {i}: HTTP {r.status_code} {r.text[:120]}")
                continue
            for c in r.json().get("results", []):
                e = (c.get("properties", {}).get("email") or "").strip().lower()
                if e:
                    dentro.add(e)
        except requests.RequestException as e:
            print(f"  lote {i}: red {e.__class__.__name__}")
        time.sleep(0.3)
        print(f"  correos {min(i + LOTE, len(emails))}/{len(emails)} · en el CRM: {len(dentro)}")
    return dentro


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="entrada", default=str(ENTRADA),
                    help="CSV de entrada (por defecto data/revelados.csv)")
    ap.add_argument("--out", dest="salida", default=str(SALIDA))
    args = ap.parse_args()
    entrada, salida = Path(args.entrada), Path(args.salida)
    if not hc.configured():
        print("ERROR: falta HUBSPOT_PRIVATE_APP_TOKEN", file=sys.stderr)
        return 2
    if not entrada.exists():
        print(f"ERROR: no existe {entrada}", file=sys.stderr)
        return 2

    filas = list(csv.DictReader(entrada.open(encoding="utf-8")))
    print(f"Contactos revelados a verificar: {len(filas)}\n")

    # ------------------------------ nivel empresa ---------------------------
    print("[1/2] nivel EMPRESA (dominio, variantes de TLD y nombre)…")
    limpias, expulsadas = vh.verificar(filas)
    fuera = {e["dominio"]: e["motivo"] for e in expulsadas}
    print(f"   empresas limpias: {len(limpias)} · expulsadas: {len(expulsadas)}\n")

    # ------------------------------ nivel persona ---------------------------
    print("[2/2] nivel PERSONA (correo, lifecyclestage y deals abiertos)…")
    correos = sorted({f["dm_email"].strip().lower() for f in limpias if f.get("dm_email")})
    existentes = correos_en_crm(correos)
    print(f"   correos ya en el CRM: {len(existentes)}/{len(correos)}")

    detalle = {}
    for i, e in enumerate(sorted(existentes), 1):
        detalle[e] = hc.check(email=e)     # solo para los que sí existen
        if i % 20 == 0 or i == len(existentes):
            print(f"   veredicto {i}/{len(existentes)}")

    # -------------------------------- salida --------------------------------
    cols = list(filas[0].keys()) + ["hubspot_verdict", "hubspot_reason"]
    resumen = Counter()
    with salida.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in limpias:
            e = f.get("dm_email", "").strip().lower()
            d = detalle.get(e)
            if d and d.get("ok"):
                f["hubspot_verdict"] = d["verdict"]
                f["hubspot_reason"] = d["reason"]
            else:
                f["hubspot_verdict"] = "verde"
                f["hubspot_reason"] = "No está en HubSpot — apto para outbound"
            f.pop("estado_hubspot", None)
            resumen[f["hubspot_verdict"]] += 1
            w.writerow({k: f.get(k, "") for k in cols})

    with EXPULSADOS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["empresa", "dominio", "motivo"])
        w.writeheader()
        w.writerows(expulsadas)

    print(f"\n→ {salida.name}: {sum(resumen.values())} contactos {dict(resumen)}")
    print(f"→ {EXPULSADOS.name}: {len(expulsadas)} empresas que sí estaban en el CRM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
