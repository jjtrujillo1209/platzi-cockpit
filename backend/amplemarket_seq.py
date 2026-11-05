"""Amplemarket como motor de envío: inscribir leads en una secuencia.

Lo que la API permite y lo que no — verificado contra la cuenta «Platzi»:

  ✓  GET  /sequences                 lista las secuencias
  ✓  POST /sequences/{id}/leads      inscribe leads, con `data` por lead
  ✗  GET  /sequences/{id}            404 — no se puede leer una secuencia
  ✗  crear una secuencia             solo desde la web app

De ahí salen las dos reglas de este módulo:

1. **El copy no lo controlamos.** Vive en la plantilla de Amplemarket y no se
   puede leer por API. Lo único que auditamos es lo que nosotros generamos: el
   gancho y el link. Es el mismo contrato implícito que con las secuencias de
   HubSpot — quien edite la plantilla puede romperlo sin que nos enteremos.
2. **Los campos de `data` solo sirven si la plantilla los nombra**, y eso no se
   puede verificar por API. `dynamic_fields_used` solo enumera los campos
   NATIVOS de Amplemarket (first_name, company_name, sender_first_name…): una
   secuencia que usa `{{ gancho }}` y `{{ link_invitacion }}` los reporta igual
   que una que no — comprobado el 19-08-2026 contra la secuencia del desayuno,
   escrita a mano con los dos campos y reportando solo `first_name` y
   `sender_first_name`. Por eso `preflight()` los devuelve como **advertencia**
   y no como bloqueo: bloquear con esa señal deja una secuencia buena sin poder
   enviar. Lo que sí es autoritativo es `status` — en draft no sale nada.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests

BASE = "https://api.amplemarket.com"
TIMEOUT = 30

# Campos que la plantilla de la secuencia tiene que nombrar para que el envío
# quede personalizado y medible.
CAMPO_GANCHO = "gancho"
CAMPO_LINK = "link_invitacion"

_ENV_CANDIDATES = (
    Path(__file__).resolve().parents[1] / ".env",                  # cockpit/.env
    Path(__file__).resolve().parents[2] / ".env",                  # PLATZI/.env
    Path(__file__).resolve().parents[2] / "prospeccion-2000" / ".env",
)


class AmpleError(Exception):
    pass


def _key() -> str:
    v = (os.environ.get("AMPLEMARKET_API_KEY") or "").strip()
    if v:
        return v
    for env in _ENV_CANDIDATES:
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("AMPLEMARKET_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def configured() -> bool:
    return bool(_key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def _request(method: str, path: str, **kw) -> requests.Response:
    if not _key():
        raise AmpleError("falta AMPLEMARKET_API_KEY")
    return requests.request(method, f"{BASE}{path}", headers=_headers(),
                            timeout=TIMEOUT, **kw)


def status() -> dict:
    if not _key():
        return {"configured": False}
    try:
        r = _request("GET", "/account-info")
        if r.status_code == 200:
            return {"configured": True, **r.json()}
        return {"configured": True, "error": f"HTTP {r.status_code}"}
    except (requests.RequestException, AmpleError) as e:
        return {"configured": True, "error": e.__class__.__name__}


def list_sequences() -> list[dict]:
    r = _request("GET", "/sequences")
    if r.status_code != 200:
        raise AmpleError(f"GET /sequences → HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    return j if isinstance(j, list) else (j.get("data") or j.get("sequences") or [])


def find_sequence(sequence_id: str) -> Optional[dict]:
    """No hay endpoint de detalle (404): se busca en la lista."""
    for s in list_sequences():
        if s.get("id") == sequence_id:
            return s
    return None


def build_lead(contact: dict, hook: str, link: str,
               extra: Optional[dict] = None) -> dict:
    """Un lead con sus campos personalizados.

    Los nombres de `data` tienen que ser minúsculas/guión bajo y ≤1024
    caracteres (límite de Amplemarket, máx. 50 campos por lead).
    """
    nombre = (contact.get("nombre") or "").strip()
    partes = nombre.split(" ")
    data = {
        "first_name": partes[0] if nombre else "",
        "last_name": partes[-1] if len(partes) > 1 else "",
        "full_name": nombre,
        "company_name": (contact.get("empresa") or "").strip(),
        CAMPO_GANCHO: (hook or "").strip()[:1024],
        CAMPO_LINK: (link or "").strip()[:1024],
    }
    data.update(extra or {})
    lead: dict = {"data": {k: v for k, v in data.items() if v}}
    email = (contact.get("email") or "").strip()
    li = (contact.get("linkedin") or "").strip()
    if email:
        lead["email"] = email
    if li:
        lead["linkedin_url"] = li
    return lead


def add_leads(sequence_id: str, leads: list[dict],
              mailboxes: Optional[list[str]] = None) -> dict:
    """Inscribe leads en la secuencia.

    `mailboxes` fija desde qué buzones sale (leads_distribution=custom_mailboxes);
    sin él, Amplemarket reparte entre los remitentes de la secuencia.

    La respuesta trae los saltados por motivo (duplicado, lista de exclusión,
    contactado hace poco, ya en la secuencia). Ese detalle se devuelve tal cual:
    es la única forma de saber a quién NO se le escribió y por qué.
    """
    if not leads:
        return {"total": 0, "total_added_to_sequence": 0, "skipped": {}}
    body: dict = {"leads": leads}
    if mailboxes:
        body["settings"] = {"leads_distribution": "custom_mailboxes",
                            "mailboxes": mailboxes}
    r = _request("POST", f"/sequences/{sequence_id}/leads", json=body)
    if r.status_code >= 400:
        raise AmpleError(f"POST /sequences/{sequence_id}/leads → "
                         f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def preflight(sequence_id: str) -> dict:
    """¿Se puede inscribir en esta secuencia, y va a personalizar de verdad?

    `problems` frena el envío; `warnings` no, porque no se pueden comprobar por
    API y frenar por una sospecha es peor que enviar con ella anotada.
    """
    problems: list[str] = []
    if not configured():
        return {"ok": False, "problems": ["falta AMPLEMARKET_API_KEY"], "warnings": []}
    if not sequence_id:
        return {"ok": False, "warnings": [], "problems": [
            "no hay secuencia configurada (kv: campaign:<slug>:amplemarket_sequence_id). "
            "La secuencia se crea en app.amplemarket.com: la API no puede crearla."]}
    try:
        seq = find_sequence(sequence_id)
    except (AmpleError, requests.RequestException) as e:
        return {"ok": False, "problems": [f"no pude listar secuencias: {e}"], "warnings": []}
    if seq is None:
        return {"ok": False, "warnings": [],
                "problems": [f"la secuencia {sequence_id} no existe en la cuenta"]}

    if seq.get("status") != "active":
        problems.append(f"la secuencia está en «{seq.get('status')}»: en draft no envía nada")

    # Los campos propios no aparecen en `dynamic_fields_used` ni cuando la
    # plantilla los usa, así que esto se queda en advertencia: la única
    # verificación real es abrir la secuencia en la web app.
    usados = set(seq.get("dynamic_fields_used") or [])
    warnings = [
        f"no puedo verificar que la plantilla use {{{{ {campo} }}}} ({para_que}): "
        "la API no reporta los campos propios. Si no está, se ignora en silencio."
        for campo, para_que in ((CAMPO_GANCHO, "el gancho por compañía"),
                                (CAMPO_LINK, "el link de atribución por contacto"))
        if campo not in usados]
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "sequence": seq, "dynamic_fields_used": sorted(usados)}
