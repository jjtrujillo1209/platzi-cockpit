"""Base de NUESTROS CLIENTES (BEAM/KLIK) — SEPARADA de la base de leads.

Lee el CSV consolidado (cabecera real en la fila 4 / índice 3). Cruza estado de
correo (por email, solo lectura) y de llamada/notas desde el almacén "clients"
(archivo independiente). No comparte datos con la base de prospección.
"""
from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from pathlib import Path

import calls_store
from config import AI_PROGRAMAS_DIR
from runs import all_email_events

CLIENTS_CSV = Path(os.environ.get(
    "PLATZI_CLIENTS_CSV",
    str(AI_PROGRAMAS_DIR / "KLIK-COMPLETO" / "BASE_CONSOLIDADA_RD_v3 - ✅ NUESTROS CLIENTES.csv"),
))
HEADER_ROW = 3  # fila 4 (0-indexed) tiene PRIORIDAD, EMPRESA, ...
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _num(s: str) -> float:
    s = re.sub(r"[^\d.]", "", (s or "").replace(",", ""))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


@lru_cache(maxsize=1)
def _load() -> list[dict]:
    if not CLIENTS_CSV.exists():
        return []
    rows = list(csv.reader(CLIENTS_CSV.open(encoding="utf-8-sig")))
    if len(rows) <= HEADER_ROW:
        return []
    hdr = rows[HEADER_ROW]
    idx = {h.strip(): i for i, h in enumerate(hdr)}

    def g(r, col):
        i = idx.get(col)
        return (r[i].strip() if i is not None and i < len(r) else "")

    out = []
    for n, r in enumerate(rows[HEADER_ROW + 1:]):
        empresa = g(r, "EMPRESA")
        if not empresa:
            continue
        email = g(r, "EMAIL") or g(r, "EMAIL 2") or g(r, "EMAIL 3")
        out.append({
            "id": str(n),
            "empresa": empresa,
            "nit": g(r, "NIT"),
            "representante_rd": g(r, "REPRESENTANTE RD ACTUAL"),
            "beam_klik": g(r, "¿BEAM/KLIK?"),
            "consumo_kwh": _num(g(r, "CONSUMO kWh/mes")),
            "valor_energia": _num(g(r, "VALOR ENERGÍA $")),
            "clase": g(r, "CLASE"),
            "sector": g(r, "SECTOR"),
            "actividad": g(r, "ACTIVIDAD ECONÓMICA"),
            "ciudad": g(r, "CIUDAD"),
            "departamento": g(r, "DEPARTAMENTO"),
            "direccion": g(r, "DIRECCIÓN"),
            "web": g(r, "WEB"),
            "nombre": g(r, "NOMBRE"),
            "cargo": g(r, "CARGO"),
            "celular": g(r, "CELULAR"),
            "email": email,
            "nombre2": g(r, "NOMBRE 2"),
            "email2": g(r, "EMAIL 2"),
            "tel2": g(r, "TEL. 2"),
            "rep_legal": g(r, "REP. LEGAL"),
            "tel3": g(r, "TEL. 3"),
            "email3": g(r, "EMAIL 3"),
            "ingresos": g(r, "INGRESOS / VENTAS"),
            "empleados": g(r, "EMPLEADOS"),
            "estado_gestion": g(r, "ESTADO GESTIÓN"),
            "ult_contacto": g(r, "ÚLT. CONTACTO"),
            "observaciones": g(r, "OBSERVACIONES"),
        })
    return out


def clear_cache() -> None:
    _load.cache_clear()


def _email_status() -> dict:
    em: dict[str, dict] = {}
    for e in all_email_events():
        addr = (e.get("email") or "").strip().lower()
        if not addr:
            continue
        ts = e.get("ts", "")
        if addr not in em or ts >= em[addr].get("ts", ""):
            em[addr] = {"status": e.get("status", ""), "ts": ts}
    return em


def list_clients() -> list[dict]:
    base = _load()
    em = _email_status()
    calls = calls_store.load("clients")
    out = []
    for c in base:
        addr = c["email"].lower()
        key = "cli:" + (addr or c["nit"] or c["empresa"].lower())
        call = calls.get(key, {})
        out.append({
            **c,
            "call_key": key,
            "email_status": em.get(addr, {}).get("status", ""),
            "email_ts": em.get(addr, {}).get("ts", ""),
            "llamada": call.get("called", False),
            "llamada_outcome": call.get("outcome", ""),
            "llamada_notes": call.get("notes", ""),
            "llamada_ts": call.get("updated_at", ""),
        })
    return out


def facets() -> dict:
    base = _load()
    return {
        "total": len(base),
        "sectores": sorted({c["sector"] for c in base if c["sector"]}),
        "ciudades": sorted({c["ciudad"] for c in base if c["ciudad"]}),
        "estados": sorted({c["estado_gestion"] for c in base if c["estado_gestion"]}),
    }


def stats() -> dict:
    rows = list_clients()
    total = len(rows)
    con_email = sum(1 for c in rows if EMAIL_RE.match(c["email"].lower()))
    con_tel = sum(1 for c in rows if c["celular"])
    consumo = sum(c["consumo_kwh"] for c in rows)
    valor = sum(c["valor_energia"] for c in rows)
    emailed = sum(1 for c in rows if c["email_status"] == "sent")
    llamados = sum(1 for c in rows if c["llamada"])

    by_sector: dict[str, float] = {}
    for c in rows:
        s = c["sector"] or "Sin sector"
        by_sector[s] = by_sector.get(s, 0) + c["consumo_kwh"]
    top_sector = sorted(by_sector.items(), key=lambda x: -x[1])[:8]

    outcomes: dict[str, int] = {}
    for c in rows:
        if c["llamada"] and c["llamada_outcome"]:
            outcomes[c["llamada_outcome"]] = outcomes.get(c["llamada_outcome"], 0) + 1

    top_consumo = sorted(rows, key=lambda c: -c["consumo_kwh"])[:10]
    return {
        "total": total,
        "con_email": con_email,
        "con_telefono": con_tel,
        "consumo_total": round(consumo),
        "valor_energia_total": round(valor),
        "emailed": emailed,
        "llamados": llamados,
        "por_sector": [{"sector": s, "consumo": round(v)} for s, v in top_sector],
        "call_outcomes": [{"outcome": k, "n": v} for k, v in outcomes.items()],
        "top_consumo": [{"empresa": c["empresa"], "consumo": c["consumo_kwh"],
                         "ciudad": c["ciudad"]} for c in top_consumo],
    }
