"""Carga al Platzi Cockpit los contactos verificados.

Entra por `POST /api/contacts` del backend (:8532) y no por SQL directo, a
propósito: ese endpoint es el que aplica el dedup en vivo (slug de LinkedIn,
correo y nombre+empresa), fija los valores por defecto de una fila nueva y
dispara el cruce con HubSpot. Escribir en la tabla a mano se saltaría las tres
cosas y dejaría la base inconsistente con las 569 que ya están.

Solo sube 🟢 verde. Amarillo (ya es lead de alguien) y rojo (cliente o deal
abierto) quedan reportados aparte para que ventas decida, no en outbound frío.

Uso:
  python3 subir.py --dry-run     # qué entraría, sin tocar nada
  python3 subir.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ENTRADA = DATA / "verificados.csv"
ESTADO = DATA / ".subidos.txt"
NO_APTOS = DATA / "no_aptos_para_frio.csv"
API = "http://127.0.0.1:8532/api/contacts"
LOTE = 50
BACKEND = HERE.parent / "backend"
CAMPAIGN = "ampliacion-2x"


def _key(r: dict) -> str:
    return (r.get("dm_linkedin") or r.get("dm_email") or "").strip().lower()


def _post(contactos: list[dict]) -> dict:
    """Prefiere llamar a contacts.add_contacts en proceso: es exactamente el
    mismo código que sirve el endpoint (dedup + defaults + cruce HubSpot), pero
    no obliga a levantar uvicorn ni despierta el scheduler. Si el import falla,
    cae al endpoint HTTP."""
    try:
        if str(BACKEND) not in sys.path:
            sys.path.insert(0, str(BACKEND))
        import contacts as mod
        return mod.add_contacts(contactos)
    except ImportError:
        data = json.dumps({"contacts": contactos}).encode()
        req = urllib.request.Request(API, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--in", dest="entrada", default=str(ENTRADA))
    ap.add_argument("--campaign", default=CAMPAIGN,
                    help="etiqueta del lote; distingue los que traen teléfono "
                         "de los que entraron por Apollo sin él")
    args = ap.parse_args()
    entrada = Path(args.entrada)

    if not entrada.exists():
        print(f"ERROR: falta {entrada} (¿corrió verificar.py?)", file=sys.stderr)
        return 2

    filas = list(csv.DictReader(entrada.open(encoding="utf-8")))
    hechos = set(ESTADO.read_text().split()) if ESTADO.exists() else set()
    por_veredicto = Counter(f.get("hubspot_verdict", "?") for f in filas)

    aptos = [f for f in filas
             if f.get("hubspot_verdict") == "verde" and _key(f) and _key(f) not in hechos]
    if args.limite:
        aptos = aptos[:args.limite]

    print(f"Verificados: {len(filas)} {dict(por_veredicto)}")
    print(f"Aptos para subir (verde, no subidos aún): {len(aptos)}")

    otros = [f for f in filas if f.get("hubspot_verdict") in ("amarillo", "rojo")]
    if otros:
        with NO_APTOS.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
            w.writeheader()
            w.writerows(otros)
        print(f"→ {NO_APTOS.name}: {len(otros)} para que ventas decida (no van en frío)")

    if args.dry_run:
        for f in aptos[:5]:
            print(f"   ej · {f['dm_nombre']} · {f['dm_cargo']} · {f['empresa']} "
                  f"· {f['dm_email']}")
        print("\n(dry-run: no se subió nada)")
        return 0
    if not aptos:
        print("Nada que subir.")
        return 0

    carga = [{
        "nombre": f.get("dm_nombre", ""), "cargo": f.get("dm_cargo", ""),
        "linkedin": f.get("dm_linkedin", ""), "empresa": f.get("empresa", ""),
        "email": f.get("dm_email", ""), "celular": f.get("dm_telefono", ""),
        "ciudad": f.get("pais", ""), "sector": f.get("industria", ""),
        "validacion_empresa": f.get("empresa", ""), "campaign": args.campaign,
    } for f in aptos]

    agregados, saltados = 0, 0
    for i in range(0, len(carga), LOTE):
        bloque = carga[i:i + LOTE]
        try:
            res = _post(bloque)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  error en el lote {i}: {e.__class__.__name__}: {e}")
            print("  ¿está arriba el backend en :8532?")
            break
        agregados += res.get("added", 0)
        saltados += len(res.get("skipped", []))
        for f in aptos[i:i + LOTE]:
            hechos.add(_key(f))
        ESTADO.write_text("\n".join(sorted(hechos)))
        print(f"  {min(i + LOTE, len(carga))}/{len(carga)} · agregados={agregados} "
              f"· saltados={saltados}")

    print(f"\nAgregados al cockpit: {agregados} · saltados por dedup: {saltados}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
