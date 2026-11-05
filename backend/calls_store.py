"""Registro persistente de llamadas para trazabilidad del equipo comercial.

Dos almacenes SEPARADOS (no se mezclan las bases):
  - "leads"   -> data/call_log.json      (base de prospección)
  - "clients" -> data/client_calls.json  (NUESTROS CLIENTES)

Estado por contacto: called, outcome, notes, by, updated_at.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STORES = {
    "leads": DATA_DIR / "call_log.json",
    "clients": DATA_DIR / "client_calls.json",
}
_lock = threading.Lock()


def _path(ns: str) -> Path:
    return STORES.get(ns, STORES["leads"])


def call_key(email: str = "", linkedin: str = "", nombre: str = "", empresa: str = "") -> str:
    if email and email.strip():
        return email.strip().lower()
    m = re.search(r"/in/([^/?#]+)", (linkedin or "").lower())
    if m:
        return f"li:{m.group(1)}"
    return f"n:{(nombre or '').strip().lower()}|{(empresa or '').strip().lower()}"


def load(ns: str = "leads") -> dict:
    p = _path(ns)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def set_call(key: str, called: bool, outcome: str = "", notes: str = "",
             by: str = "", ns: str = "leads") -> dict:
    with _lock:
        p = _path(ns)
        data = load(ns)
        entry = data.get(key, {})
        entry.update({
            "called": bool(called),
            "outcome": outcome if outcome is not None else entry.get("outcome", ""),
            "notes": notes if notes is not None else entry.get("notes", ""),
            "by": by or entry.get("by", ""),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        data[key] = entry
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return entry
