"""Prospección masiva: lista de empresas → decisores → base de outreach.

Corre en un hilo (las llamadas son HTTP puras, no hace falta subproceso). Por
cada empresa usa prospect_fallback.find_decision_makers (máx. 2 decisores):
Apollo primero y Amplemarket cuando Apollo no encuentra a nadie, se queda
corto o su cuenta se cae. Si auto_add está activo, persiste los perfiles
(LinkedIn + correo + teléfono) en la base con contacts.add_contacts (dedup
incluido) empresa por empresa, para que un stop a mitad de camino no pierda lo
ya encontrado. El job solo se detiene por cuenta cuando los dos proveedores
están caídos.

El guarda de clientes no vive aquí: `contacts.add_contacts()` cruza cada
prospecto nuevo contra HubSpot (🟢/🟡/🔴) al darlo de alta, y el enroll bloquea
a los 🔴 antes de secuenciar.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import apollo_budget
import apollo_prospect
import contacts
import prospect_fallback
import store
import validar_hubspot

_PAUSE_BETWEEN = 0.6  # respiro entre empresas (Apollo ya duerme entre reveals)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start(companies: list[dict], auto_add: bool = True) -> dict:
    """companies: [{empresa, domain?, nit?, sector?, ciudad?}, ...]"""
    companies = [c for c in companies
                 if (c.get("empresa") or "").strip() or (c.get("domain") or "").strip()]
    if not companies:
        return {"ok": False, "error": "Lista vacía: indica al menos una empresa"}
    apollo_listo = (apollo_prospect.status()["configured"]
                    and apollo_budget.has_budget())
    if not apollo_listo and not prospect_fallback.amplemarket_configurado():
        return {"ok": False, "error": (
            "Ni Apollo ni Amplemarket pueden prospectar: Apollo sin API key o con "
            "el presupuesto local en 0 (revisa el saldo real en su dashboard y "
            "actualízalo en Ajustes) y Amplemarket sin AMPLEMARKET_API_KEY.")}

    job_id = datetime.now().strftime("bulk-%Y%m%d-%H%M%S")
    state = {
        "job_id": job_id, "status": "running",
        "total": len(companies), "done": 0,
        "profiles_found": 0, "added": 0, "skipped_dup": 0,
        "por_fuente": {"apollo": 0, "amplemarket": 0}, "circuito": {},
        "auto_add": auto_add,
        "started_at": _now(), "finished_at": "",
        "results": [], "stop": False,
    }
    with _lock:
        _jobs[job_id] = state
    threading.Thread(target=_run, args=(state, companies, auto_add),
                     daemon=True).start()
    return {"ok": True, **public(state)}


def _run(state: dict, companies: list[dict], auto_add: bool) -> None:
    circuito = prospect_fallback.Circuito()
    altas: list[tuple[str, str]] = []
    if not apollo_prospect.status()["configured"]:
        circuito.cortar_apollo("Apollo no configurado (falta API key)")
    # empresa por empresa
    for i, c in enumerate(companies):
        if state["stop"]:
            state["status"] = "stopped"
            break
        if not circuito.vivo():
            # los dos proveedores con la cuenta caída: todo lo que sigue va a
            # fallar igual, así que se detiene en vez de recorrer la lista.
            state["status"] = "sin_creditos"
            break
        empresa = (c.get("empresa") or "").strip()
        entry = {"empresa": empresa or c.get("domain", ""), "status": "",
                 "profiles": [], "added": 0, "detail": ""}

        try:
            r = prospect_fallback.find_decision_makers(
                company=empresa, domain=(c.get("domain") or "").strip(),
                circuito=circuito)
        except Exception as e:  # red / proveedor caído: no tumba el job
            r = {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
        state["circuito"] = circuito.estado()
        if not r.get("ok"):
            entry["status"] = "error"
            entry["detail"] = r.get("error", "")
        else:
            profiles = r.get("profiles", [])
            entry["profiles"] = profiles
            entry["detail"] = r.get("note", "")
            state["profiles_found"] += len(profiles)
            for p in profiles:
                fuente = p.get("fuente", "apollo")
                state["por_fuente"][fuente] = state["por_fuente"].get(fuente, 0) + 1
            if not profiles:
                entry["status"] = "sin_perfiles"
            elif auto_add:
                add = contacts.add_contacts([
                    {"nombre": p["nombre"], "cargo": p["cargo"],
                     "linkedin": p.get("linkedin", ""),
                     "email": p.get("email", ""),
                     "celular": p.get("celular", ""),
                     "validacion": p.get("validacion", ""),
                     "fuente": p.get("fuente", "apollo"),
                     "empresa": r.get("company") or empresa,
                     "ciudad": c.get("ciudad", ""), "sector": c.get("sector", ""),
                     "validacion_empresa": r.get("company", "")}
                    for p in profiles
                ])
                entry["added"] = add.get("added", 0)
                state["added"] += add.get("added", 0)
                state["skipped_dup"] += len(add.get("skipped", []))
                entry["status"] = "agregado" if entry["added"] else "duplicado"
            else:
                entry["status"] = "encontrado"

            if entry["added"] and r.get("domain"):
                altas.extend((p.get("linkedin", ""), r["domain"]) for p in profiles)

        state["results"].append(entry)
        state["done"] += 1
        time.sleep(_PAUSE_BETWEEN)

    if altas:
        state["hubspot_empresa"] = _cruzar_empresas(altas, state["started_at"][:10])
    if state["status"] == "running":
        state["status"] = "done"
    state["finished_at"] = _now()


def _cruzar_empresas(altas: list[tuple[str, str]], desde: str) -> dict:
    """Cruce por EMPRESA de los contactos dados de alta en el job.

    `add_contacts` solo cruza por correo, así que un perfil sin correo (todos
    los de Amplemarket mientras su pozo de correo esté en cero, y ~45% de los
    de Apollo) queda con el veredicto en blanco, y en blanco es invitable.
    Un solo `domain IN` por cada 100 dominios al cerrar el job.
    """
    por_slug = {contacts._norm_li(li): d for li, d in altas if li and d}
    if not por_slug:
        return {}
    veredictos = validar_hubspot.empresas_en_lote(list(set(por_slug.values())))
    filas = store.fetch_all(
        "SELECT id, linkedin, COALESCE(hubspot_verdict,'') v FROM contacts "
        "WHERE COALESCE(linkedin,'')<>'' AND created_at >= ?", (desde,))
    cuenta: dict[str, int] = {}
    for f in filas:
        dominio = por_slug.get(contacts._norm_li(f["linkedin"]))
        v = veredictos.get(dominio or "") or {}
        if not v.get("ok"):
            continue
        # Un verde de empresa no tapa un rojo o amarillo de persona.
        if f["v"] and v["veredicto"] == "verde":
            continue
        if f["v"] == "rojo":
            continue
        validar_hubspot._guardar(f["id"], v["veredicto"], v["motivo"])
        cuenta[v["veredicto"]] = cuenta.get(v["veredicto"], 0) + 1
    return cuenta


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
