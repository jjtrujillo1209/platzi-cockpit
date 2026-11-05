"""Inscribe el lote en una secuencia de Amplemarket vía API.

`POST /sequences/{id}/leads` admite hasta 250 leads por petición (30 pet./min),
con `email` o `linkedin_url` como llave y un objeto `data` de variables por lead.
Aquí van los dos identificadores y, en `data`, los campos dinámicos que la
secuencia realmente usa (`first_name`, `company_name`) más el contexto que sirve
para personalizar a mano.

El cliente `amplemarket.py` no cubre este endpoint; esto es lo que faltaba.

Uso:
  python3 cargar_amplemarket.py --secuencia <id> --limite 3 --dry-run
  python3 cargar_amplemarket.py --secuencia <id> [--limite N]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
P2000 = HERE.parent.parent / "prospeccion-2000"
sys.path.insert(0, str(P2000))

import amplemarket as am                    # noqa: E402

ENTRADA = HERE.parent.parent / "240_amplemarket_import.csv"
REGISTRO = HERE / "data" / "carga_amplemarket.json"
LOTE = 250

# Todo lo que no sea llave viaja como variable del lead.
VARIABLES = ["first_name", "last_name", "full_name", "title", "company_name",
             "company_domain", "country", "employees", "industry", "icp_area",
             "source"]


def construir(filas: list[dict]) -> list[dict]:
    leads = []
    for f in filas:
        datos = {k: (f.get(k) or "").strip()[:1024] for k in VARIABLES}
        lead = {"data": {k: v for k, v in datos.items() if v}}
        if f.get("email"):
            lead["email"] = f["email"].strip().lower()
        if f.get("linkedin_url"):
            lead["linkedin_url"] = f["linkedin_url"].strip()
        leads.append(lead)
    return leads


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--secuencia", required=True)
    a.add_argument("--limite", type=int, default=0)
    a.add_argument("--desde", type=int, default=0)
    a.add_argument("--dry-run", action="store_true")
    args = a.parse_args()

    filas = list(csv.DictReader(ENTRADA.open(encoding="utf-8")))
    filas = filas[args.desde:]
    if args.limite:
        filas = filas[:args.limite]
    leads = construir(filas)
    print(f"Leads a inscribir: {len(leads)} · secuencia {args.secuencia}")
    if args.dry_run:
        print(json.dumps({"leads": leads[:2]}, ensure_ascii=False, indent=1))
        print("\n(dry-run: no se envió nada)")
        return 0

    registro = json.loads(REGISTRO.read_text(encoding="utf-8")) if REGISTRO.exists() else []
    for i in range(0, len(leads), LOTE):
        trozo = leads[i:i + LOTE]
        r = am._post(f"/sequences/{args.secuencia}/leads",
                     {"leads": trozo,
                      "settings": {"leads_distribution": "sequence_senders"}})
        cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        print(f"  lote {i}–{i + len(trozo)}: HTTP {r.status_code}")
        print("  " + json.dumps(cuerpo, ensure_ascii=False)[:700])
        registro.append({"desde": args.desde + i, "n": len(trozo),
                         "http": r.status_code, "respuesta": cuerpo})
        REGISTRO.write_text(json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8")
        if r.status_code >= 400:
            print("  → se detiene: revisa el error antes de seguir")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
