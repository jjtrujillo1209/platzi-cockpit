"""Enriquecimiento masivo: contactos YA EN LA BASE con LinkedIn pero sin correo
→ Apollo people/match por linkedin_url → actualiza la fila existente (no crea
contactos nuevos, a diferencia de prospect_bulk).

Corre en un hilo (llamadas HTTP puras). Actualiza en la base con
contacts.enrich_contacts empresa por empresa, para que un stop a mitad de
camino no pierda lo ya encontrado.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import apollo_budget
import apollo_prospect
import contacts

_PAUSE_BETWEEN = 0.3  # match directo por linkedin_url, más barato que una búsqueda

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pending_contacts() -> list[dict]:
    """Contactos con LinkedIn pero sin correo — candidatos a enriquecer."""
    rows = contacts.list_contacts()
    return [r for r in rows if r.get("linkedin") and not r.get("email")]


def start(items: list[dict] | None = None) -> dict:
    """items opcional: lista explícita de contactos [{linkedin, nombre, empresa}];
    si no se pasa, usa automáticamente pending_contacts()."""
    targets = items if items is not None else pending_contacts()
    targets = [t for t in targets if (t.get("linkedin") or "").strip()]
    if not targets:
        return {"ok": False, "error": "No hay contactos con LinkedIn y sin correo para enriquecer"}
    if not apollo_prospect.status()["configured"]:
        return {"ok": False, "error": "Apollo no configurado (falta API key)"}
    if not apollo_budget.has_budget():
        return {"ok": False, "error": (
            "Presupuesto local de créditos Apollo en 0. Revisa tu saldo real en "
            "el dashboard de Apollo y actualízalo en Ajustes antes de enriquecer "
            "(la API de Apollo no expone el saldo, así que no podemos leerlo solos).")}

    job_id = datetime.now().strftime("enrich-%Y%m%d-%H%M%S")
    state = {
        "job_id": job_id, "status": "running",
        "total": len(targets), "done": 0,
        "found": 0, "updated": 0, "sin_datos": 0, "error_count": 0,
        "started_at": _now(), "finished_at": "",
        "results": [], "stop": False,
    }
    with _lock:
        _jobs[job_id] = state
    threading.Thread(target=_run, args=(state, targets), daemon=True).start()
    return {"ok": True, **public(state)}


def _run(state: dict, targets: list[dict]) -> None:
    for c in targets:
        if state["stop"]:
            state["status"] = "stopped"
            break
        if not apollo_budget.has_budget():
            # presupuesto local en 0 (defensa adicional: aunque Apollo no haya
            # devuelto todavía un 422 explícito, no seguimos gastando a ciegas)
            state["status"] = "sin_creditos"
            state["finished_at"] = _now()
            return
        nombre = c.get("nombre", "")
        empresa = c.get("empresa", "")
        li = (c.get("linkedin") or "").strip()
        entry = {"nombre": nombre, "empresa": empresa, "linkedin": li, "status": "", "detail": ""}

        try:
            r = apollo_prospect.enrich_by_linkedin(li)
        except Exception as e:  # red / Apollo caído: no tumba el job
            r = {"ok": False, "error": f"{e.__class__.__name__}: {e}"}

        if not r.get("ok"):
            entry["status"] = "error"
            entry["detail"] = r.get("error", "")
            state["error_count"] += 1
            if apollo_prospect.is_account_error(entry["detail"]):
                # créditos agotados / plan / rate-limit: todo lo que sigue va a
                # fallar igual — detener el job en vez de quemar el resto de
                # la lista contra el mismo muro.
                state["results"].append(entry)
                state["done"] += 1
                state["status"] = "sin_creditos"
                state["finished_at"] = _now()
                return
        elif not r.get("found") or not (r.get("email") or r.get("celular")):
            entry["status"] = "sin_datos"
            state["sin_datos"] += 1
        else:
            state["found"] += 1
            up = contacts.enrich_contacts([{
                "linkedin": li, "email": r.get("email", ""),
                "celular": r.get("celular", ""), "validacion": "verificada",
            }])
            if up.get("updated"):
                entry["status"] = "actualizado"
                entry["detail"] = ", ".join(x for x in [
                    r.get("email") and "correo", r.get("celular") and "teléfono"] if x)
                state["updated"] += 1
            else:
                entry["status"] = "no_encontrado_en_base"
                entry["detail"] = "el LinkedIn ya no matchea ninguna fila"

        state["results"].append(entry)
        state["done"] += 1
        time.sleep(_PAUSE_BETWEEN)

    if state["status"] == "running":
        state["status"] = "done"
    state["finished_at"] = _now()


def public(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "stop"}


def get(job_id: str) -> dict | None:
    st = _jobs.get(job_id)
    return public(st) if st else None


def stop(job_id: str) -> dict | None:
    st = _jobs.get(job_id)
    if not st:
        return None
    if st["status"] == "running":
        st["stop"] = True
    return public(st)


def list_jobs() -> list[dict]:
    return [{k: v for k, v in st.items() if k not in ("stop", "results")}
            for st in _jobs.values()]
