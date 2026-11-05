"""Platzi Cockpit — API FastAPI.

Reúne base de datos, jobs en vivo, estadísticas e historial de los canales de
outreach (invitaciones y mensajes de LinkedIn, correo, llamadas) sobre los
motores existentes, más las campañas con fecha de evento.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

import asyncio
import json
import os

import agentes as agentes_mod
import apollo_budget as apollo_budget_mod
import nube as nube_mod
import apollo_prospect as apollo_mod
import auth as auth_mod
import calls_store
import amplemarket_seq as ams_mod
import campaigns as campaigns_mod
import conexiones as conexiones_mod
import secuencias_am as secuencias_mod
import secuencias_hs as secuencias_hs_mod
import telefonos_apollo as tel_apollo_mod
import contacts as contacts_mod
import enrich_bulk as enrich_mod
import enrollment as enrollment_mod
import heartbeat as heartbeat_mod
import hubspot_check as hubspot_mod
import guard
import ingest as ingest_mod
import li_login as li_login_mod
import links as links_mod
import peticiones as peticiones_mod
import prospect_bulk as bulk_mod
import prospect_fallback
import rsvp as rsvp_mod
import scheduler as scheduler_mod
import seats as seats_mod
import settings as settings_mod
import stats as stats_mod
import store
import sync as sync_mod
import trazabilidad as trazabilidad_mod
import webhooks as webhooks_mod
from config import BACKEND_DIR, DEDUP_CSV, PUBLIC_BASE_URL, TEMPLATES_DIR, TRACKING_DB
from jobs import manager, tail_events
from runs import list_runs, run_detail

app = FastAPI(title="Platzi Cockpit API", version="1.0")
# Assets de la landing de invitación (logo). Público: lo abre el invitado.
app.mount("/static", StaticFiles(directory=BACKEND_DIR / "static"), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas abiertas aunque el login esté activo (config de login, el propio login,
# health y el webhook de SendGrid que llega desde fuera). El WS se valida aparte.
_OPEN_PATHS = ("/api/auth/", "/api/health", "/api/webhooks/")


# Lo único que el túnel de ngrok puede alcanzar. ngrok publica el puerto entero
# y el login está apagado sin GOOGLE_CLIENT_ID: sin esta lista, cualquiera con la
# URL pública podía leer contactos, abrir los asientos o disparar envíos.
_TUNEL_PREFIJOS = ("/api/webhooks/", "/r/", "/static/", "/api/rsvp/contexto",
                   "/api/rsvp/start", "/api/rsvp/verify", "/api/rsvp/acompanante")
_TUNEL_EXACTAS = ("/invitacion",)


def _desde_fuera(headers, host: str) -> bool:
    """¿Entró por el túnel? ngrok manda `X-Forwarded-For` y el Host público; el
    proxy de Vite (changeOrigin) llega como 127.0.0.1 y sin esa cabecera."""
    return "x-forwarded-for" in headers or host.split(":")[0] not in ("127.0.0.1", "localhost")


@app.middleware("http")
async def solo_lo_publico_por_el_tunel(request: Request, call_next):
    ruta = request.url.path
    if _desde_fuera(request.headers, request.headers.get("host", "")) \
            and ruta not in _TUNEL_EXACTAS \
            and not any(ruta.startswith(p) for p in _TUNEL_PREFIJOS):
        return JSONResponse({"error": "No existe esa ruta"}, status_code=404)
    return await call_next(request)


# Lo único que el túnel de ngrok puede alcanzar. ngrok publica el puerto entero
# y el login está apagado sin GOOGLE_CLIENT_ID: sin esta lista, cualquiera con la
# URL pública podía leer contactos, abrir los asientos o disparar envíos.
_TUNEL_PREFIJOS = ("/api/webhooks/", "/r/", "/static/", "/api/rsvp/contexto",
                   "/api/rsvp/start", "/api/rsvp/verify", "/api/rsvp/acompanante")
_TUNEL_EXACTAS = ("/invitacion",)


def _desde_fuera(headers, host: str) -> bool:
    """¿Entró por el túnel? ngrok manda `X-Forwarded-For` y el Host público; el
    proxy de Vite (changeOrigin) llega como 127.0.0.1 y sin esa cabecera."""
    return "x-forwarded-for" in headers or host.split(":")[0] not in ("127.0.0.1", "localhost")


@app.middleware("http")
async def solo_lo_publico_por_el_tunel(request: Request, call_next):
    ruta = request.url.path
    if _desde_fuera(request.headers, request.headers.get("host", "")) \
            and ruta not in _TUNEL_EXACTAS \
            and not any(ruta.startswith(p) for p in _TUNEL_PREFIJOS):
        return JSONResponse({"error": "No existe esa ruta"}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if auth_mod.enabled() and request.url.path.startswith("/api/"):
        if not any(request.url.path.startswith(p) for p in _OPEN_PATHS) \
                and request.method != "OPTIONS":
            token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not auth_mod.verify_session(token):
                return JSONResponse({"error": "no autenticado"}, status_code=401)
    return await call_next(request)


# ----------------------------- auth -------------------------------------
class GoogleLogin(BaseModel):
    credential: str


@app.get("/api/auth/config")
def auth_config() -> dict:
    return auth_mod.public_config()


@app.post("/api/auth/google")
def auth_google(body: GoogleLogin) -> dict:
    if not auth_mod.enabled():
        return {"ok": True, "enabled": False}
    try:
        user = auth_mod.verify_google_credential(body.credential)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=401)
    return {"ok": True, "token": auth_mod.issue_session(user["email"]), **user}


# ----------------------------- settings (SendGrid) ----------------------
class SendgridSettings(BaseModel):
    api_key: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    bcc: Optional[str] = None


@app.get("/api/settings/sendgrid")
def get_sendgrid() -> dict:
    return settings_mod.status()


@app.post("/api/settings/sendgrid")
def post_sendgrid(body: SendgridSettings) -> dict:
    return settings_mod.update(api_key=body.api_key, from_email=body.from_email,
                               from_name=body.from_name, bcc=body.bcc)


@app.post("/api/settings/sendgrid/test")
def test_sendgrid(body: SendgridSettings) -> dict:
    return settings_mod.test_connection(body.api_key)


class KeyBody(BaseModel):
    api_key: Optional[str] = None


class LinkedInCookies(BaseModel):
    cookies: str


class GoogleSettings(BaseModel):
    client_id: Optional[str] = None
    allowed_domain: Optional[str] = None


@app.get("/api/settings/all")
def settings_all() -> dict:
    return {
        "sendgrid": settings_mod.status(),
        "anthropic": settings_mod.anthropic_status(),
        "linkedin": settings_mod.linkedin_status(),
        "google": settings_mod.google_status(),
        "apollo": apollo_mod.status(),
        "apollo_budget": apollo_budget_mod.status(),
        "hubspot": hubspot_mod.status(),
    }


# ----------------------------- HubSpot check ----------------------------
class HubspotCheckReq(BaseModel):
    email: Optional[str] = None
    domain: Optional[str] = None


class HubspotBulkReq(BaseModel):
    items: list[HubspotCheckReq] = []


@app.get("/api/hubspot/status")
def hubspot_status() -> dict:
    return hubspot_mod.status()


@app.post("/api/hubspot/check")
def hubspot_check_one(body: HubspotCheckReq) -> dict:
    return hubspot_mod.check(body.email or "", body.domain or "")


@app.post("/api/hubspot/check-bulk")
def hubspot_check_bulk(body: HubspotBulkReq) -> dict:
    return hubspot_mod.check_many([{"email": i.email or "", "domain": i.domain or ""}
                                   for i in body.items])


@app.get("/api/contacts/{contact_id}/hubspot")
def hubspot_check_contact(contact_id: int) -> dict:
    """Cruza un contacto del Cockpit (por su email/empresa) contra HubSpot y
    persiste el veredicto para que el badge quede guardado."""
    c = store.get_contact(contact_id)
    if c is None:
        return {"ok": False, "error": "contacto no existe"}
    res = hubspot_mod.check(c.get("email", "") or "", c.get("empresa", "") or "")
    if res.get("ok"):
        store.set_hubspot(contact_id, res.get("verdict", ""), res.get("reason", ""))
    return res


@app.get("/api/hubspot/summary")
def hubspot_summary() -> dict:
    """Comparativa leads vs prospectos según el cruce con HubSpot."""
    return {"configured": hubspot_mod.configured(), **store.hubspot_summary()}


@app.post("/api/hubspot/backfill")
def hubspot_backfill(limit: int = 500) -> dict:
    """Cruza contra HubSpot todos los contactos que aún no tienen veredicto
    (los cargados antes de activar el cruce automático)."""
    if not hubspot_mod.configured():
        return {"ok": False, "error": "HubSpot no configurado"}
    rows = store.contacts_needing_hubspot(limit)
    summary = {"verde": 0, "amarillo": 0, "rojo": 0, "error": 0}
    # De a uno esto eran ~0,9 s por contacto (7,5 min para 500). En lote son
    # 3 llamadas por cada 100 correos.
    veredictos = hubspot_mod.check_lote([r.get("email", "") for r in rows])
    for r in rows:
        res = veredictos.get((r.get("email", "") or "").strip().lower(), {})
        if res.get("ok"):
            v = res.get("verdict", "error")
            store.set_hubspot(r["id"], v, res.get("reason", ""))
            summary[v] = summary.get(v, 0) + 1
        else:
            summary["error"] += 1
    return {"ok": True, "checked": len(rows), "summary": summary}


class ApolloBudgetBody(BaseModel):
    value: int


@app.get("/api/settings/apollo/budget")
def get_apollo_budget() -> dict:
    return apollo_budget_mod.status()


@app.post("/api/settings/apollo/budget")
def set_apollo_budget(body: ApolloBudgetBody) -> dict:
    """El usuario reporta su saldo real de Apollo (revisado en su dashboard —
    la API no lo expone). Esto reinicia el contador de consumo local a 0."""
    return apollo_budget_mod.set_budget(body.value)


@app.post("/api/settings/apollo")
def post_apollo(body: KeyBody) -> dict:
    return apollo_mod.update(body.api_key or "")


@app.post("/api/settings/apollo/test")
def test_apollo(body: KeyBody) -> dict:
    return apollo_mod.test_connection(body.api_key)


@app.post("/api/settings/anthropic")
def post_anthropic(body: KeyBody) -> dict:
    return settings_mod.anthropic_update(body.api_key or "")


@app.post("/api/settings/anthropic/test")
def test_anthropic(body: KeyBody) -> dict:
    return settings_mod.anthropic_test(body.api_key)


@app.post("/api/settings/linkedin")
def post_linkedin(body: LinkedInCookies) -> dict:
    return settings_mod.linkedin_update(body.cookies)


@app.post("/api/settings/google")
def post_google(body: GoogleSettings) -> dict:
    return settings_mod.google_update(body.client_id, body.allowed_domain)


# ----------------------------- Gmail (detección de respuestas) ----------
class GmailSecret(BaseModel):
    content: str


@app.get("/api/settings/gmail")
def gmail_settings() -> dict:
    return ingest_mod.gmail_status()


@app.post("/api/settings/gmail/client-secret")
def gmail_save_secret(body: GmailSecret) -> dict:
    return ingest_mod.save_client_secret(body.content)


@app.post("/api/settings/gmail/connect")
async def gmail_connect() -> dict:
    """Abre el navegador en este Mac para autorizar Gmail (solo lectura)."""
    return await asyncio.to_thread(ingest_mod.connect_gmail)


@app.post("/api/settings/gmail/check")
def gmail_check() -> dict:
    return ingest_mod.check_gmail()


# ----------------------------- agentes de launchd ------------------------
# La prospección no vive en esta API: corre en agentes de launchd aparte. Si uno
# se cae, aquí no se nota nada — y el tablero de 23-platzi-ventas tampoco sabe
# distinguir «sana y sin cola» de «apagada». Por eso el cockpit le pregunta a
# launchd directamente y pinta una banda cuando algo no está corriendo.
# Ver `agentes.py` para la avería del 21-sep-2026 que motivó esto.
AGENTES_VIGILANCIA_MIN = float(os.environ.get("PLATZI_AGENTES_VIGILANCIA_MIN", "5"))
AGENTES_AUTOREPARA = os.environ.get("PLATZI_AGENTES_AUTOREPARA", "1") == "1"


@app.get("/api/agentes")
def get_agentes() -> dict:
    return agentes_mod.salud()


@app.post("/api/agentes/reparar")
def post_agentes_reparar(label: Optional[str] = None) -> dict:
    return agentes_mod.reparar(solo=label)


async def _agentes_loop():
    """Mira a launchd cada pocos minutos y levanta lo que se haya caído.

    No se vigila a sí mismo (corre dentro de este proceso), pero eso está
    cubierto: `latido.py --vigilar` es un agente aparte que comprueba que esta
    API responda.
    """
    await asyncio.sleep(45)
    while True:
        try:
            salud = await asyncio.to_thread(agentes_mod.salud)
            if not salud["sano"] and AGENTES_AUTOREPARA:
                parte = await asyncio.to_thread(agentes_mod.reparar)
                for r in parte["reparados"]:
                    print(f"[agentes] {r['nombre']} venía de «{r['venia_de']}» → "
                          f"{'; '.join(r['acciones'])}", flush=True)
                store.kv_set("agentes_ultima_reparacion", json.dumps(parte["reparados"],
                                                                    ensure_ascii=False))
            for a in salud["avisos"]:
                print(f"[agentes] aviso · {a['nombre']}: {a['aviso']}", flush=True)
        except Exception as e:
            print(f"[agentes] la vigilancia falló: {e}", flush=True)
        await asyncio.sleep(AGENTES_VIGILANCIA_MIN * 60)


@app.on_event("startup")
async def _start_agentes():
    asyncio.create_task(_agentes_loop())


# ----------------------------- heartbeat --------------------------------
HEARTBEAT_HOURS = float(os.environ.get("PLATZI_HEARTBEAT_HOURS", "24"))
HEARTBEAT_LI = os.environ.get("PLATZI_HEARTBEAT_LINKEDIN", "1") != "0"


@app.get("/api/heartbeat")
def get_heartbeat() -> dict:
    return heartbeat_mod.history()


@app.post("/api/heartbeat/run")
async def run_heartbeat(check_linkedin: bool = True) -> dict:
    return await heartbeat_mod.run_snapshot(check_linkedin=check_linkedin, headless=True)


async def _heartbeat_loop():
    # Primer snapshot al arrancar (solo email, sin tocar LinkedIn de inmediato).
    await asyncio.sleep(20)
    try:
        await heartbeat_mod.run_snapshot(check_linkedin=False)
    except Exception:
        pass
    while True:
        await asyncio.sleep(HEARTBEAT_HOURS * 3600)
        try:
            await heartbeat_mod.run_snapshot(check_linkedin=HEARTBEAT_LI, headless=True)
        except Exception:
            pass


@app.on_event("startup")
async def _start_heartbeat():
    asyncio.create_task(_heartbeat_loop())


async def _nube_loop():
    # La nube encuentra decisores aunque el Mac duerma; cada minuto se recoge lo
    # que dejó en la bandeja. Al despertar, la primera vuelta vacía lo acumulado.
    await asyncio.sleep(15)
    while True:
        try:
            r = await asyncio.to_thread(nube_mod.sincronizar)
            if r.get("recibidos") or r.get("error"):
                print(f"[nube] {r}", flush=True)
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def _start_nube():
    if nube_mod.configurada():
        asyncio.create_task(_nube_loop())


@app.get("/api/nube")
def get_nube() -> dict:
    return {"configurada": nube_mod.configurada(), "ultimo": nube_mod.ultimo(),
            "libro": apollo_budget_mod.status()}


@app.post("/api/nube/sincronizar")
def post_nube_sincronizar() -> dict:
    return nube_mod.sincronizar()


@app.on_event("startup")
async def _start_agent():
    interrupted = store.mark_interrupted_jobs()
    if interrupted:
        print(f"[copiloto] {interrupted} job(s) quedaron corriendo en la sesión anterior")
    # Las secuencias las siembra cada campaña (campaigns.seed) al inscribir:
    # este cockpit no tiene una cadencia "por defecto" sin copy revisado.
    links_mod.init()
    rsvp_mod.init()
    heredado = seats_mod.adoptar_asiento_unico()
    if heredado:
        print(f"[asientos] la sesión de LinkedIn que había pasó a vp-1: {heredado}")
    asyncio.create_task(scheduler_mod.loop())


# ----------------------------- health ----------------------------------
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "dedup_csv": DEDUP_CSV.exists(),
        "tracking_db": TRACKING_DB.exists(),
        "templates": TEMPLATES_DIR.exists(),
    }


# ----------------------------- contacts ---------------------------------
@app.get("/api/contacts")
def get_contacts(
    q: Optional[str] = None,
    sector: Optional[str] = None,
    campaign: Optional[str] = None,
    tier: Optional[str] = None,
    estado: Optional[str] = None,
    validacion: Optional[str] = None,
    channel: Optional[str] = None,
    seat: Optional[str] = None,
    limit: int = 1000,
) -> dict:
    rows = contacts_mod.list_contacts()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r["nombre"] + r["empresa"] + r["email"]).lower()]
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    if campaign:
        rows = [r for r in rows if r.get("campaign") == campaign]
    if tier:
        rows = [r for r in rows if r.get("tier") == tier]
    if estado:
        rows = [r for r in rows if r["estado"] == estado]
    if validacion:
        rows = [r for r in rows if r.get("validacion") == validacion]
    if seat:
        rows = [r for r in rows if r.get("li_seat") == seat]
    if channel == "linkedin_pending":
        rows = [r for r in rows if r["linkedin"] and not r["linkedin_status"]]
    elif channel == "connected":
        rows = [r for r in rows if r["linkedin_status"] in ("sent", "already")]
    elif channel == "has_email":
        rows = [r for r in rows if r["email"]]
    elif channel == "email_pending":
        # con correo pero nunca enviados de verdad (dry-run no cuenta)
        rows = [r for r in rows if r["email"] and not r["email_enviado"]]
    elif channel == "email_sent":
        rows = [r for r in rows if r["email_enviado"]]
    return {"total": len(rows), "contacts": rows[:limit]}


@app.get("/api/contacts/facets")
def get_facets() -> dict:
    return contacts_mod.facets()


class ContactIn(BaseModel):
    nombre: Optional[str] = None
    cargo: Optional[str] = None
    linkedin: Optional[str] = None
    empresa: Optional[str] = None
    email: Optional[str] = None
    celular: Optional[str] = None
    ciudad: Optional[str] = None
    sector: Optional[str] = None
    campaign: Optional[str] = None
    tier: Optional[str] = None
    validacion_empresa: Optional[str] = None


class AddContactsReq(BaseModel):
    contacts: list[ContactIn]


@app.post("/api/contacts")
def add_contacts(body: AddContactsReq) -> dict:
    """Agrega prospectos nuevos a la base de outreach (con dedup)."""
    return contacts_mod.add_contacts([c.model_dump() for c in body.contacts])


@app.post("/api/contacts/reload")
def reload_contacts() -> dict:
    """Recarga la base desde disco (útil tras editar el CSV a mano)."""
    contacts_mod.clear_cache()
    return {"ok": True, "total": contacts_mod.facets()["total"]}


# ----------------------------- prospección (Apollo) ---------------------
class OrgChartReq(BaseModel):
    company: Optional[str] = None
    domain: Optional[str] = None


@app.post("/api/prospect/orgchart")
def prospect_orgchart(body: OrgChartReq) -> dict:
    """Dada una empresa (ICP oficial: >300 empleados), devuelve hasta 2 perfiles
    decisores de las personas clave de Platzi Business (L&D / People-Talento /
    Tecnología-Innovación (CTO-CIO) / Ventas-Sales Enablement / CEO-C-Level)
    validados, con LinkedIn, correo y teléfono. Apollo primero; Amplemarket
    completa cuando Apollo no encuentra o su cuenta está caída."""
    return prospect_fallback.find_decision_makers(company=body.company or "",
                                                  domain=body.domain or "")


class BulkCompany(BaseModel):
    empresa: Optional[str] = None
    domain: Optional[str] = None
    nit: Optional[str] = None
    sector: Optional[str] = None
    ciudad: Optional[str] = None


class BulkProspectReq(BaseModel):
    companies: list[BulkCompany]
    auto_add: bool = True


@app.post("/api/prospect/bulk")
def prospect_bulk_start(body: BulkProspectReq) -> dict:
    """Prospección masiva: empresas → decisores Apollo → base de outreach."""
    return bulk_mod.start([c.model_dump() for c in body.companies],
                          auto_add=body.auto_add)


@app.get("/api/prospect/bulk")
def prospect_bulk_list() -> dict:
    return {"jobs": bulk_mod.list_jobs()}


@app.get("/api/prospect/bulk/{job_id}")
def prospect_bulk_status(job_id: str) -> dict:
    st = bulk_mod.get(job_id)
    if not st:
        return JSONResponse({"error": "job no encontrado"}, status_code=404)
    return st


@app.post("/api/prospect/bulk/{job_id}/stop")
def prospect_bulk_stop(job_id: str) -> dict:
    st = bulk_mod.stop(job_id)
    if not st:
        return JSONResponse({"error": "job no encontrado"}, status_code=404)
    return st


# --------------------- enriquecimiento (correo/tel por LinkedIn) --------
@app.get("/api/enrich/pending")
def enrich_pending() -> dict:
    """Contactos con LinkedIn pero sin correo — candidatos a enriquecer."""
    rows = enrich_mod.pending_contacts()
    return {"total": len(rows), "contacts": rows}


@app.post("/api/enrich/bulk")
def enrich_bulk_start() -> dict:
    """Enriquece todos los contactos pendientes (LinkedIn sin correo) con Apollo."""
    return enrich_mod.start()


@app.get("/api/enrich/bulk")
def enrich_bulk_list() -> dict:
    return {"jobs": enrich_mod.list_jobs()}


@app.get("/api/enrich/bulk/{job_id}")
def enrich_bulk_status(job_id: str) -> dict:
    st = enrich_mod.get(job_id)
    if not st:
        return JSONResponse({"error": "job no encontrado"}, status_code=404)
    return st


@app.post("/api/enrich/bulk/{job_id}/stop")
def enrich_bulk_stop(job_id: str) -> dict:
    st = enrich_mod.stop(job_id)
    if not st:
        return JSONResponse({"error": "job no encontrado"}, status_code=404)
    return st


# ============================ COPILOTO (agente) ==========================
# Pipeline, timeline, secuencias, cola de aprobación, tareas y briefing.

@app.get("/api/pipeline")
def get_pipeline(limit: int = 100, campaign: Optional[str] = None) -> dict:
    return store.pipeline_summary(limit_per_stage=limit,
                                  campaigns=(campaign,) if campaign else None)


@app.get("/api/pipeline-ventas")
def get_pipeline_ventas(limit: int = 200) -> dict:
    """Tablero de lo que entra por Platzi Ventas (23-platzi-ventas.vercel.app).

    Es el Pipeline de siempre acotado a ese origen, más el estado del carril
    automático que lo mueve. Va en una sola respuesta porque la pregunta que
    contesta la pantalla es una sola: «lo que mandé desde Ventas, ¿ya salió?».
    """
    # Dos vías al mismo tablero: el origen (lo que llegó de la app de Ventas) y
    # el dueño (lo que se le asignó al equipo de Sales, que conserva su origen
    # real). Ver `store.pipeline_summary`.
    sales = tuple(a["seat_id"] for a in conexiones_mod.atienden_origen())
    return {"origenes": list(conexiones_mod.ORIGENES_VIA_RAPIDA),
            "asientos_sales": list(sales),
            "pipeline": store.pipeline_summary(
                limit_per_stage=limit,
                campaigns=conexiones_mod.ORIGENES_VIA_RAPIDA,
                seat_ids=sales or None),
            "via_rapida": conexiones_mod.estado_via_rapida()}


class ContactUpdate(BaseModel):
    stage: Optional[str] = None
    nombre: Optional[str] = None
    empresa: Optional[str] = None
    cargo: Optional[str] = None
    email: Optional[str] = None
    celular: Optional[str] = None
    linkedin: Optional[str] = None


@app.patch("/api/contacts/{contact_id}")
def patch_contact(contact_id: int, body: ContactUpdate) -> dict:
    """Actualiza el stage y/o los datos del lead. Se usa tanto para mover de
    etapa (payload {stage}) como para editar nombre/cargo/email/etc. desde el
    detalle del contacto."""
    if store.get_contact(contact_id) is None:
        return JSONResponse({"error": "contacto no encontrado"}, status_code=404)
    if body.stage is not None and not store.set_stage(contact_id, body.stage):
        return JSONResponse({"error": f"stage inválido: {body.stage}"}, status_code=400)
    fields = {k: v for k, v in body.dict(exclude={"stage"}).items() if v is not None}
    if fields:
        store.set_contact_fields(contact_id, fields)
    return {"ok": True, "contact": store.get_contact(contact_id)}


class NotesUpdate(BaseModel):
    notas: str


@app.patch("/api/contacts/{contact_id}/notes")
def patch_contact_notes(contact_id: int, body: NotesUpdate) -> dict:
    if not store.set_notes(contact_id, body.notas):
        return JSONResponse({"error": "contacto no encontrado"}, status_code=404)
    return {"ok": True, "contact": store.get_contact(contact_id)}


@app.post("/api/contacts/{contact_id}/notes/beacon")
async def beacon_contact_notes(contact_id: int, request: Request) -> dict:
    """Mismo guardado que el PATCH de arriba, pero por POST — `navigator.
    sendBeacon` (usado en beforeunload/pagehide para no perder notas al
    refrescar o cerrar la pestaña) solo puede mandar POST, nunca PATCH."""
    import json
    try:
        body = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        body = {}
    store.set_notes(contact_id, (body.get("notas") or "").strip())
    return {"ok": True}


@app.get("/api/contacts/{contact_id}/timeline")
def contact_timeline(contact_id: int) -> dict:
    """Ficha del prospecto: sus datos, su historial y QUIÉN hizo cada cosa.

    El historial venía sin autoría: una invitación de LinkedIn se veía igual
    saliera de Jess o de Patricia. Con varios perfiles firmando, eso es la mitad
    del dato — sobre todo cuando el que invitó es el único que después puede
    escribirle. Cada actividad trae ahora el asiento (de `meta.seat`, grabado en
    el momento del envío) y su nombre resuelto.
    """
    contact = store.get_contact(contact_id)
    if not contact:
        return JSONResponse({"error": "contacto no encontrado"}, status_code=404)

    remitentes = {s.id: s.etiqueta for s in seats_mod.all_seats()}
    timeline = store.timeline(contact_id)
    for a in timeline:
        # `meta.seat` es el asiento que la ejecutó. Si la actividad es vieja (de
        # antes de los asientos) no se inventa: se cae al asiento asignado hoy
        # solo cuando el canal es LinkedIn, y se marca como inferido.
        sid = (a.get("meta") or {}).get("seat", "")
        inferido = False
        if not sid and a.get("channel") == "linkedin":
            sid = (contact.get("li_seat") or "")
            inferido = bool(sid)
        a["seat_id"] = sid
        a["seat_nombre"] = remitentes.get(sid, "")
        a["seat_inferido"] = inferido

    return {"contact": contact, "timeline": timeline,
            "remitentes": remitentes,
            "seat_asignado": {"id": contact.get("li_seat") or "",
                              "nombre": remitentes.get(contact.get("li_seat") or "", "")},
            # Lo que está por salir para este prospecto: sin esto la ficha
            # muestra el pasado y esconde lo que ya está en la cola.
            "aprobaciones": store.fetch_all(
                "SELECT id, channel, status, draft_subject, draft_body, created_at, "
                " blocked_reasons, model FROM approval_queue "
                "WHERE contact_id=? ORDER BY id DESC LIMIT 10", (contact_id,)),
            "sequences": store.fetch_all(
                "SELECT cs.*, s.name FROM contact_sequences cs "
                "JOIN sequences s ON s.id=cs.sequence_id WHERE cs.contact_id=?",
                (contact_id,))}


@app.post("/api/sync/run")
def sync_run() -> dict:
    return sync_mod.run_full_sync()


@app.post("/api/ingest/run")
def ingest_run() -> dict:
    return ingest_mod.run()


@app.get("/api/agent/status")
def agent_status() -> dict:
    import json as _json
    last = store.kv_get("last_tick_result", "")
    return {
        "cadence": scheduler_mod.cadence_settings(),
        "budgets": scheduler_mod.budgets(),
        "seats": seats_mod.resumen(),
        "gmail": ingest_mod.gmail_status(),
        "ai_available": __import__("ai").available(),
        "pending_approvals": store.fetch_one(
            "SELECT COUNT(*) n FROM approval_queue WHERE status='pending'")["n"],
        "last_tick_result": _json.loads(last) if last else None,
    }


@app.post("/api/agent/tick")
def agent_tick() -> dict:
    """Corre un ciclo completo ya (ingesta + reconciliación + drafts)."""
    return scheduler_mod.tick(force=True)


class CadenceSettings(BaseModel):
    enabled: Optional[bool] = None
    cap_email: Optional[int] = None
    cap_linkedin: Optional[int] = None   # ya no existe: ver el 400 de abajo


@app.post("/api/settings/cadence")
def cadence_update(body: CadenceSettings) -> dict:
    # Un cupo global de LinkedIn con varios asientos miente: no dice cuál de los
    # perfiles se está gastando y deja que dos se coman el del tercero. Se
    # responde con un error en vez de aceptarlo y hacer otra cosa en silencio.
    if body.cap_linkedin is not None:
        return JSONResponse(
            {"error": "el cupo de LinkedIn es de cada asiento, no del cockpit",
             "usa": "POST /api/seats/{seat_id} con tope_dia / tope_semana / tope_msgs",
             "asientos": [b["seat_id"] for b in seats_mod.budgets()]},
            status_code=400)
    return scheduler_mod.update_cadence_settings(
        enabled=body.enabled, cap_email=body.cap_email)


# ------------------- ENVÍO DE CONEXIONES (sin nota) ---------------------
class EnvioDiario(BaseModel):
    activo: bool


@app.get("/api/conexiones/estado")
def conexiones_estado() -> dict:
    """Estado del envío automático y qué hay disponible por asiento."""
    return {**conexiones_mod.estado(),
            "toca_ahora": conexiones_mod.toca_ahora()[1],
            "asientos": [{
                "seat_id": s.id, "nombre": s.etiqueta,
                "cupo_libre": seats_mod.budget(s)["invites"]["remaining"],
                "candidatos": len(conexiones_mod.candidatos(s.id, 50)),
            } for s in seats_mod.en_uso() if s.activo]}


@app.post("/api/conexiones/diario")
def conexiones_diario(body: EnvioDiario) -> dict:
    conexiones_mod.set_activo(body.activo)
    return conexiones_mod.estado()


@app.post("/api/conexiones/correr")
def conexiones_correr(tope: int = 0) -> dict:
    """Corre el envío ya, sin esperar la mañana. `tope` limita por asiento."""
    return conexiones_mod.correr(tope_por_asiento=tope)


@app.get("/api/secuencias-am")
def secuencias_am_estado() -> dict:
    """Mapeo perfil → secuencia de Amplemarket y estado del carril automático."""
    return secuencias_mod.estado()


@app.post("/api/secuencias-am")
def secuencias_am_switch(body: EnvioDiario) -> dict:
    secuencias_mod.set_activa(body.activo)
    return secuencias_mod.estado()


class InscribirReq(BaseModel):
    contact_ids: list[int]


@app.post("/api/secuencias-am/inscribir")
def secuencias_am_inscribir(body: InscribirReq) -> dict:
    """Inscribe los contactos elegidos en la secuencia de SU perfil.

    Es lo que usa el botón «secuencia» de cada tarjeta del Pipeline. No pasa por
    `/api/sequences/enroll`, que exige una `Campaign` con copy en el cockpit y
    hoy devuelve 400 porque no hay ninguna: aquí el copy vive en la plantilla de
    Amplemarket y lo que se elige es en cuál de las dos secuencias entra.
    """
    if not body.contact_ids:
        return JSONResponse({"error": "no llegó ningún contacto"}, status_code=400)
    r = secuencias_mod.inscribir(ids=body.contact_ids)
    if r.get("error") and not r.get("inscritos"):
        return JSONResponse({"error": r["error"], **r}, status_code=400)
    return r


@app.post("/api/secuencias-am/correr")
def secuencias_am_correr(simular: bool = False, limite: int = 0) -> dict:
    """Inscribe ya. `simular` lista a quién, sin tocar Amplemarket."""
    return secuencias_mod.inscribir(limite=limite, simular=simular)


@app.get("/api/secuencias-hs")
def secuencias_hs_estado() -> dict:
    """Mapeo perfil → secuencia de HubSpot y estado del carril automático.

    El hermano del de Amplemarket, con otro motor: aquí el correo sale de la
    bandeja de Google del propio vendedor (`remitente_hs`). Los dos son
    excluyentes por asiento; `conflictos` lista los que tienen las dos puestas.
    """
    return secuencias_hs_mod.estado()


@app.post("/api/secuencias-hs")
def secuencias_hs_switch(body: EnvioDiario) -> dict:
    secuencias_hs_mod.set_activa(body.activo)
    return secuencias_hs_mod.estado()


@app.post("/api/secuencias-hs/inscribir")
def secuencias_hs_inscribir(body: InscribirReq) -> dict:
    """Inscribe los contactos elegidos en la secuencia de HubSpot de SU perfil.

    Al inscribir se envía el primer paso, y **HubSpot no tiene API para
    desinscribir**: lo que sale de aquí solo se detiene a mano en su UI.
    """
    if not body.contact_ids:
        return JSONResponse({"error": "no llegó ningún contacto"}, status_code=400)
    r = secuencias_hs_mod.inscribir(ids=body.contact_ids)
    if r.get("error") and not r.get("inscritos"):
        return JSONResponse({"error": r["error"], **r}, status_code=400)
    return r


@app.post("/api/secuencias-hs/correr")
def secuencias_hs_correr(simular: bool = False, limite: int = 0) -> dict:
    """Inscribe ya. `simular` lista a quién y cuántos habría que crear en el
    CRM, sin escribir nada en HubSpot."""
    return secuencias_hs_mod.inscribir(limite=limite, simular=simular)


@app.get("/api/conexiones/via-rapida")
def via_rapida_estado() -> dict:
    """Vía rápida: los prospectos que entran por Platzi Ventas se invitan en el
    tick siguiente, sin esperar la corrida de la mañana."""
    return conexiones_mod.estado_via_rapida()


@app.post("/api/conexiones/via-rapida")
def via_rapida_switch(body: EnvioDiario) -> dict:
    conexiones_mod.set_via_rapida(body.activo)
    return conexiones_mod.estado_via_rapida()


@app.post("/api/conexiones/via-rapida/correr")
def via_rapida_correr(simular: bool = False, forzar: bool = False) -> dict:
    """Dispara la vía rápida ya. `simular` muestra a quién le saldría sin
    enviar ni escribir el reparto; `forzar` ignora ventana e interruptor."""
    return conexiones_mod.correr_via_rapida(simular=simular, forzar=forzar)


@app.post("/api/conexiones/via-rapida/aviso")
def via_rapida_aviso() -> dict:
    """La máquina avisa que dejó decisores de Platzi Ventas en la base: la vía
    rápida corre ya, en segundo plano, sin esperar al tick de 15 min."""
    return conexiones_mod.avisar_via_rapida()


# ----------------------------- ASIENTOS DE LINKEDIN ---------------------
class SeatUpdate(BaseModel):
    nombre: Optional[str] = None
    rol: Optional[str] = None
    activo: Optional[bool] = None
    sales_nav: Optional[bool] = None
    es_anfitrion: Optional[bool] = None
    cdp: Optional[str] = None
    cookies: Optional[str] = None
    tope_dia: Optional[int] = None
    tope_semana: Optional[int] = None
    tope_msgs: Optional[int] = None
    cupo_fijo: Optional[int] = None    # 0 = volver a la rampa
    warmup_start: Optional[str] = None
    # Secuencia de Amplemarket que firma este VP (ver `secuencias_am`).
    secuencia_am: Optional[str] = None
    # Carril de correo por HubSpot: la secuencia y el buzón que la firma. Los
    # dos motores de correo son excluyentes por asiento (ver `secuencias_hs`).
    secuencia_hs: Optional[str] = None
    remitente_hs: Optional[str] = None


@app.get("/api/seats")
def seats_list() -> dict:
    """Los perfiles que firman LinkedIn, con su cupo de hoy y su sesión."""
    return seats_mod.resumen()


# Va ANTES de `/api/seats/{seat_id}`: FastAPI resuelve por orden de
# declaración y al revés esta ruta se la comería el parámetro (`seat_id="nuevo"`)
# y respondería 404 en vez de crear nada.
@app.post("/api/seats/nuevo")
def seats_crear() -> dict:
    """Abre un espacio para otro perfil, sin tocar código ni reiniciar.

    Si hay un cupo apagado y sin contactos lo estrena en vez de crear otro: cada
    asiento carga con su carpeta de Chrome y su puerto.
    """
    seat, motivo = seats_mod.crear()
    if seat is None:
        return JSONResponse({"ok": False, "error": motivo}, status_code=409)
    return {"ok": True, "seat": seats_mod.budget(seat)}


@app.post("/api/seats/{seat_id}")
def seats_update(seat_id: str, body: SeatUpdate) -> dict:
    seat = seats_mod.update(seat_id, **body.model_dump(exclude_none=True))
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    seats_mod.invalidate_probe(seat_id)
    return {"ok": True, "seat": seats_mod.budget(seat)}


class SeatCookies(BaseModel):
    cookies: str


@app.post("/api/seats/{seat_id}/cookies")
def seats_cookies(seat_id: str, body: SeatCookies) -> dict:
    """Guarda la sesión de ESTE asiento en su propio archivo.

    Una por asiento: si compartieran archivo, el último login borraría los otros
    dos y el cockpit seguiría creyendo que hay tres perfiles.
    """
    import json as _json
    seat = seats_mod.get(seat_id)
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    try:
        data = _json.loads(body.cookies)
    except _json.JSONDecodeError:
        return JSONResponse({"error": "JSON inválido"}, status_code=400)
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if not isinstance(cookies, list) or not any(c.get("name") == "li_at" for c in cookies):
        return JSONResponse({"error": "no se encontró la cookie 'li_at' en el JSON"},
                            status_code=400)
    destino = seats_mod.cookies_path(seat_id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    seats_mod.update(seat_id, cookies=str(destino))
    seats_mod.invalidate_probe(seat_id)
    return {"ok": True, "seat": seats_mod.budget(seats_mod.get(seat_id))}


# ---- Entrada de sesión del VP: abrir ventana → entra → se guarda solo ----
# Tres llamadas y ninguna le pide nada al VP más que su usuario y su clave, que
# escribe en un Chrome normal. El cockpit no ve la contraseña: lo único que
# queda guardado es la sesión, en la carpeta del asiento.
@app.post("/api/seats/{seat_id}/login/abrir")
def seat_login_abrir(seat_id: str) -> dict:
    """Abre la ventana de ese VP en la pantalla de acceso de LinkedIn."""
    if seats_mod.get(seat_id) is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    res = li_login_mod.abrir(seat_id, ventana=True)
    seats_mod.invalidate_probe(seat_id)
    if not res.get("ok"):
        return JSONResponse(res, status_code=500)
    return res


@app.get("/api/seats/{seat_id}/login/estado")
def seat_login_estado(seat_id: str) -> dict:
    """Sondeo barato para el bucle de la UI: ¿va en login, en verificación o ya
    entró? No abre páginas, solo lee las pestañas del Chrome."""
    if seats_mod.get(seat_id) is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    return li_login_mod.estado(seat_id)


@app.post("/api/seats/{seat_id}/login/guardar")
def seat_login_guardar(seat_id: str) -> dict:
    """Lee quién entró y lo guarda en el asiento.

    Aquí es donde el asiento deja de ser un hueco: el nombre y el titular salen
    del propio LinkedIn, así que nadie tiene que escribirlos ni puede
    equivocarse al hacerlo.
    """
    seat = seats_mod.get(seat_id)
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    datos = li_login_mod.capturar(seat_id)
    if not datos.get("ok"):
        return JSONResponse(datos, status_code=409)

    # Un mismo LinkedIn en dos asientos sería un cupo contado dos veces: el
    # cockpit creería que tiene dos perfiles y en realidad tendría uno.
    for otro in seats_mod.all_seats():
        if otro.id == seat_id:
            continue
        url_otro = store.kv_get(f"seat:{otro.id}:perfil_url", "").strip()
        if url_otro and url_otro == datos.get("url", ""):
            return JSONResponse(
                {"ok": False, "error": f"ese LinkedIn ya está en el asiento de "
                                       f"{otro.etiqueta}: serían dos asientos con la "
                                       "misma cuenta y el cupo se contaría doble"},
                status_code=409)

    # `activo=True` es parte de guardar, no un paso aparte: un cupo libre está
    # apagado (así lo distingue `seats.libres()`), y si guardar no lo enciende el
    # asiento queda con sesión viva, nombre puesto y cupo 0 — listo en apariencia
    # y sin enviar nada. Pasó con vp-4 y no había en pantalla nada que lo dijera.
    seats_mod.update(seat_id, nombre=datos["nombre"], rol=datos.get("titular", ""),
                     cdp=li_login_mod.endpoint(seat_id), activo=True)
    seats_mod.invalidate_probe(seat_id)
    return {"ok": True, "perfil": datos, "seat": seats_mod.budget(seats_mod.get(seat_id))}


@app.post("/api/seats/{seat_id}/login/cerrar-ventana")
def seat_login_cerrar(seat_id: str) -> dict:
    """Cierra la ventana. La sesión NO se pierde: vive en el perfil en disco."""
    res = li_login_mod.cerrar(seat_id)
    seats_mod.invalidate_probe(seat_id)
    return res


@app.post("/api/seats/{seat_id}/login/olvidar")
def seat_login_olvidar(seat_id: str) -> dict:
    """Borra la sesión de ese VP (se salió del programa).

    Deja el asiento sin nombre y sin cupo: después de esto el cockpit no puede
    entrar a su LinkedIn ni con la ventana abierta.
    """
    if seats_mod.get(seat_id) is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    res = li_login_mod.olvidar(seat_id)
    seats_mod.update(seat_id, nombre="", rol="", cdp="")
    seats_mod.invalidate_probe(seat_id)
    return res


@app.post("/api/seats/{seat_id}/cookies/respaldar")
def seat_cookies_respaldar(seat_id: str) -> dict:
    """Copia la sesión del asiento a su archivo de respaldo.

    Se hace solo tras cada login; esto es para forzarlo a mano (antes de mover
    la máquina, por ejemplo). NO es una forma de entrar: restaurar cookies en un
    navegador limpio termina en checkpoint, y el checkpoint le llega al VP.
    """
    seat = seats_mod.get(seat_id)
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    listo = seats_mod.asegurar_envio(seat)
    if not listo.get("ok"):
        return JSONResponse({"ok": False, "error": listo.get("error")}, status_code=409)
    res = li_login_mod.respaldar_cookies(seat_id)
    return res if res.get("ok") else JSONResponse(res, status_code=409)


@app.post("/api/seats/{seat_id}/despertar")
def seat_despertar(seat_id: str) -> dict:
    """Levanta la ventana con la sesión guardada, sin molestar al VP."""
    seat = seats_mod.get(seat_id)
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    res = seats_mod.asegurar_envio(seat)
    return res if res.get("ok") else JSONResponse(res, status_code=409)


@app.post("/api/seats/{seat_id}/test")
def seats_test(seat_id: str) -> dict:
    seat = seats_mod.get(seat_id)
    if seat is None:
        return JSONResponse({"error": f"no existe el asiento {seat_id}"}, status_code=404)
    return {"ok": True, "sesion": seats_mod.session(seat, refresh=True),
            "seat": seats_mod.budget(seats_mod.get(seat_id))}


class SeatAssign(BaseModel):
    seat_id: str


@app.post("/api/contacts/{contact_id}/seat")
def contact_seat(contact_id: int, body: SeatAssign) -> dict:
    """Reasignar a mano el perfil que firma a un contacto.

    Solo antes del primer toque de LinkedIn: después, el contacto solo aceptó a
    ESE perfil y mandarle un mensaje desde otro no es que se vea raro, es que
    LinkedIn no deja abrir el chat.
    """
    contact = store.get_contact(contact_id)
    if not contact:
        return JSONResponse({"error": "contacto no encontrado"}, status_code=404)
    if seats_mod.get(body.seat_id) is None:
        return JSONResponse({"error": f"no existe el asiento {body.seat_id}"},
                            status_code=404)
    if seats_mod.tiene_historia_li(contact_id):
        return JSONResponse(
            {"error": "este contacto ya tuvo contacto por LinkedIn con su asiento "
                      "actual: cambiarlo mandaría el siguiente mensaje desde un "
                      "perfil que nunca lo aceptó"}, status_code=409)
    store.set_seat(contact_id, body.seat_id)
    return {"ok": True, "contact_id": contact_id, "seat_id": body.seat_id}


class EnrollReq(BaseModel):
    contact_ids: list[int] = []
    # Sin default: apuntar a una campaña retirada devolvía un 400 confuso
    # («campaña desconocida: desayuno-28») en vez de decir que falta elegirla.
    campaign: str = ""              # slug en campaigns.py
    force: bool = False  # ignora el bloqueo de HubSpot (clientes / deals abiertos)


@app.post("/api/sequences/enroll")
def sequences_enroll(body: EnrollReq) -> dict:
    # Guard HubSpot: no secuenciar en frío a un cliente actual o a alguien con
    # un deal abierto (🔴). Se puede forzar con force=True. Si HubSpot no está
    # configurado o falla, no bloquea (fail-open) para no romper el flujo.
    blocked = []
    if hubspot_mod.configured() and not body.force:
        cs = [(cid, store.get_contact(cid)) for cid in body.contact_ids]
        cs = [(cid, c) for cid, c in cs if c and (c.get("email") or "").strip()]
        veredictos = hubspot_mod.check_lote([c.get("email", "") for _, c in cs])
        for cid, c in cs:
            res = veredictos.get((c.get("email", "") or "").strip().lower(), {})
            if res.get("ok") and res.get("verdict") == "rojo":
                blocked.append({"contact_id": cid, "nombre": c.get("nombre", ""),
                                "reason": res.get("reason", "")})
    allow = [cid for cid in body.contact_ids
             if cid not in {b["contact_id"] for b in blocked}]

    disponibles = [c["slug"] for c in campaigns_mod.list_all()]
    camp = campaigns_mod.get(body.campaign) if body.campaign else None
    if camp is None:
        return JSONResponse(
            {"error": ("no hay ninguna secuencia con copy aprobado para inscribir"
                       if not disponibles else
                       f"campaña desconocida: {body.campaign!r}"),
             "disponibles": disponibles}, status_code=400)
    seq_id = campaigns_mod.seed(camp)
    enrolled = 0
    for cid in allow:
        if store.enroll(cid, seq_id) is not None:
            store.set_contact_fields(cid, {"campaign": camp.slug})
            enrolled += 1
    return {"ok": True, "campaign": camp.slug, "enrolled": enrolled,
            "already": len(allow) - enrolled,
            "blocked": blocked,
            "blocked_count": len(blocked),
            "preflight": campaigns_mod.preflight(camp)}


@app.get("/api/approvals")
def approvals_list(status: str = "pending") -> dict:
    return {"approvals": store.list_approvals(status)}


class ApprovalEdit(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


@app.post("/api/approvals/{approval_id}/approve")
def approval_approve(approval_id: int, body: ApprovalEdit) -> dict:
    """Aprobar re-audita el texto final: si el humano editó el borrador, lo que
    se envía es lo editado, así que la compuerta tiene que verlo a él y no al
    original."""
    cur = store.fetch_one("SELECT * FROM approval_queue WHERE id=?", (approval_id,))
    if not cur:
        return JSONResponse({"error": "no existe esa aprobación"}, status_code=404)
    subject = body.subject if body.subject is not None else cur["draft_subject"]
    texto = body.body if body.body is not None else cur["draft_body"]
    razones = guard.check(texto, cur["channel"], subject=subject)
    if razones:
        return JSONResponse({"error": "el mensaje no pasa la compuerta",
                             "reasons": razones}, status_code=422)
    row = store.decide_approval(approval_id, "approved",
                                subject=body.subject, body=body.body)
    return {"ok": True, "approval": row}


@app.post("/api/approvals/{approval_id}/skip")
def approval_skip(approval_id: int) -> dict:
    return {"ok": True, "approval": store.decide_approval(approval_id, "skipped")}


class BulkApprove(BaseModel):
    ids: list[int] = []


@app.post("/api/approvals/bulk-approve")
def approvals_bulk(body: BulkApprove) -> dict:
    """Aprobar en lote SOLO lo que está limpio. Un borrador frenado por la
    compuerta se aprueba de a uno, después de corregirlo."""
    saltados = []
    for i in body.ids:
        row = store.fetch_one("SELECT status FROM approval_queue WHERE id=?", (i,))
        if not row or row["status"] != "pending":
            saltados.append(i)
            continue
        store.decide_approval(i, "approved")
    return {"ok": True, "approved": len(body.ids) - len(saltados),
            "skipped": saltados}


class SendApproved(BaseModel):
    dry_run_email: bool = False


@app.post("/api/approvals/send")
def approvals_send(body: SendApproved) -> dict:
    """Lanza los jobs con todo lo aprobado (la única puerta de envío)."""
    return scheduler_mod.send_approved(dry_run_email=body.dry_run_email)


# ----------------------------- CAMPAÑAS ---------------------------------
@app.get("/api/campaigns")
def campaigns_list() -> dict:
    """Campañas disponibles y qué le falta a cada una para poder enviar."""
    out = []
    for c in campaigns_mod.list_all():
        camp = campaigns_mod.get(c["slug"])
        out.append({**c, "preflight": campaigns_mod.preflight(camp) if camp else None})
    return {"campaigns": out}


@app.get("/api/campaigns/{slug}")
def campaign_detail(slug: str) -> dict:
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    inscritos = store.fetch_one(
        "SELECT COUNT(*) n FROM contact_sequences cs "
        "JOIN sequences s ON s.id = cs.sequence_id WHERE s.name=?", (camp.name,))
    return {
        "slug": camp.slug, "name": camp.name,
        "event_day": camp.event_day.isoformat(),
        "cutoff_day": camp.cutoff_day.isoformat(),
        "preflight": campaigns_mod.preflight(camp),
        "enrolled": (inscritos or {}).get("n", 0),
        "steps": [{"order": s.order, "channel": s.channel, "label": s.label,
                   "delay_days": s.delay_days,
                   "send_by": s.send_by(camp.event_day).isoformat()
                   if s.send_by(camp.event_day) else None,
                   "subject": s.subject, "body": s.body,
                   "needs_hook": s.needs_hook} for s in camp.steps],
    }


class CampaignConfig(BaseModel):
    values: dict[str, str] = {}


@app.post("/api/campaigns/{slug}/config")
def campaign_config(slug: str, body: CampaignConfig) -> dict:
    """Llena los datos que faltaban por confirmar (anfitrión, lugar, etc.).

    Mientras estén vacíos la compuerta frena los borradores, que es lo que
    queremos: nadie le manda a un presidente un mensaje con un hueco.
    """
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    for k, v in body.values.items():
        store.kv_set(f"campaign:{slug}:{k}", (v or "").strip())
    return {"ok": True, "preflight": campaigns_mod.preflight(camp)}


# --- Amplemarket: es quien envía; el cockpit decide a quién y con qué datos ---
@app.get("/api/campaigns/{slug}/amplemarket")
def campaign_amplemarket(slug: str) -> dict:
    """Estado de la secuencia que envía + la especificación para armarla.

    `spec` es lo que hay que copiar y pegar en la web app: la API de Amplemarket
    no puede crear secuencias ni leer las que existen.
    """
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    seq_id = campaigns_mod.amplemarket_sequence_id(camp)
    return {
        "configured": ams_mod.configured(),
        "sequence_id": seq_id,
        "preflight": ams_mod.preflight(seq_id),
        "variables_requeridas": campaigns_mod.lead_fields(),
        "spec": campaigns_mod.liquid_spec(camp),
    }


class AmsSequenceRef(BaseModel):
    sequence: str = ""      # id de Amplemarket o nombre exacto de la secuencia


@app.post("/api/campaigns/{slug}/amplemarket/sequence")
def campaign_set_ams_sequence(slug: str, body: AmsSequenceRef) -> dict:
    """Amarra la campaña a la secuencia que envía, resolviéndola por id o nombre.

    Se valida contra la lista de la cuenta antes de guardar: un id mal pegado se
    ve idéntico a uno bueno hasta el día del envío, cuando la inscripción falla
    y ya no hay tiempo de arreglarla. Guardar por nombre es lo que uno tiene a
    mano (es lo que se ve en la web app), y aquí se traduce al id.
    """
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    ref = (body.sequence or "").strip()
    if not ref:
        return JSONResponse({"error": "falta el id o el nombre de la secuencia"},
                            status_code=400)
    try:
        seqs = ams_mod.list_sequences()
    except Exception as e:
        return JSONResponse({"error": f"no pude listar las secuencias: {e}"},
                            status_code=502)

    exactos = [s for s in seqs if s.get("id") == ref]
    if not exactos:
        exactos = [s for s in seqs
                   if (s.get("name") or "").strip().lower() == ref.lower()]
    if not exactos:
        return JSONResponse({"error": f"no hay ninguna secuencia «{ref}» en la cuenta",
                             "disponibles": [s.get("name") for s in seqs]},
                            status_code=404)
    # Hay nombres repetidos en la cuenta (tres «Getting Started with AI Copy»):
    # elegir uno al azar mandaría el lote por la secuencia equivocada.
    if len(exactos) > 1:
        return JSONResponse(
            {"error": f"«{ref}» está repetido {len(exactos)} veces: pasa el id",
             "candidatos": [{"id": s.get("id"), "created_at": s.get("created_at"),
                             "created_by": s.get("created_by_user_email")}
                            for s in exactos]}, status_code=409)

    seq = exactos[0]
    store.kv_set(f"campaign:{slug}:amplemarket_sequence_id", seq["id"])
    return {"ok": True, "sequence": {k: seq.get(k) for k in
                                     ("id", "name", "status", "url",
                                      "dynamic_fields_used")},
            "preflight": ams_mod.preflight(seq["id"]),
            "variables_requeridas": campaigns_mod.lead_fields()}


class PlantillaVerificada(BaseModel):
    verificada: bool = True


@app.post("/api/campaigns/{slug}/amplemarket/plantilla-verificada")
def campaign_plantilla_verificada(slug: str, body: PlantillaVerificada) -> dict:
    """Constancia de que una persona abrió la secuencia y vio los campos.

    No es una verificación: la API no puede leer la plantilla. Es una firma con
    fecha, atada al id de la secuencia, para que el aviso deje de preguntar lo
    mismo. Si la campaña se amarra a otra secuencia, la firma caduca sola.
    """
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    clave = f"campaign:{slug}:plantilla_verificada"
    if not body.verificada:
        store.kv_set(clave, "")
        return {"ok": True, "verificada": ""}
    seq_id = campaigns_mod.amplemarket_sequence_id(camp)
    if not seq_id:
        return JSONResponse(
            {"error": "no hay secuencia amarrada: nada que firmar"}, status_code=400)
    from datetime import datetime as _dt
    hoy = _dt.now().date().isoformat()
    store.kv_set(clave, f"{hoy}|{seq_id}")
    return {"ok": True, "verificada": hoy, "sequence_id": seq_id}


@app.get("/api/campaigns/{slug}/trazabilidad")
def campaign_trazabilidad(slug: str) -> dict:
    """La cadena de atribución de la campaña, contacto por contacto."""
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    return trazabilidad_mod.reporte(camp)


@app.get("/api/amplemarket/sequences")
def amplemarket_sequences() -> dict:
    try:
        return {"sequences": ams_mod.list_sequences()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


class PrepareReq(BaseModel):
    contact_ids: list[int] = []
    force_hubspot: bool = False


@app.post("/api/campaigns/{slug}/amplemarket/prepare")
def campaign_prepare(slug: str, body: PrepareReq) -> dict:
    """Genera gancho + link por contacto y los deja en la cola. No inscribe."""
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    return enrollment_mod.prepare(camp, body.contact_ids,
                                  force_hubspot=body.force_hubspot)


class EnrollAmsReq(BaseModel):
    dry_run: bool = True


@app.post("/api/campaigns/{slug}/amplemarket/enroll")
def campaign_enroll_ams(slug: str, body: EnrollAmsReq) -> dict:
    """Inscribe en Amplemarket lo aprobado. `dry_run` por defecto a propósito."""
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    return enrollment_mod.send_approved(camp, dry_run=body.dry_run)


class WebhookSecret(BaseModel):
    secret: str


@app.post("/api/settings/amplemarket/webhook-secret")
def set_ams_webhook_secret(body: WebhookSecret) -> dict:
    """El secreto del webhook es de la CUENTA, no de una campaña: Amplemarket
    dispara un solo webhook para todas sus secuencias."""
    s = (body.secret or "").strip()
    if len(s) < 16:
        return JSONResponse({"error": "usa un secreto de al menos 16 caracteres"},
                            status_code=400)
    store.kv_set("amplemarket_webhook_secret", s)
    return {"ok": True, "url": f"{PUBLIC_BASE_URL}/api/webhooks/amplemarket/{s}"}


@app.post("/api/webhooks/amplemarket/{secret}")
async def webhook_amplemarket(secret: str, request: Request) -> dict:
    """Respuestas y avances de etapa que reporta Amplemarket.

    La documentación no define firma ni cabecera de verificación, así que el
    secreto va en la ruta: se guarda en kv (`amplemarket_webhook_secret`) y se
    registra esa URL completa en Amplemarket.
    """
    esperado = store.kv_get("amplemarket_webhook_secret", "").strip()
    if not esperado or secret != esperado:
        return JSONResponse({"error": "secreto inválido"}, status_code=403)
    payload = await request.json()
    tipo = (payload.get("type") or payload.get("event") or "").lower()
    if "stage" in tipo:
        return enrollment_mod.ingest_stage(payload)
    return enrollment_mod.ingest_reply(payload)


@app.post("/api/campaigns/{slug}/seed")
def campaign_seed(slug: str) -> dict:
    camp = campaigns_mod.get(slug)
    if camp is None:
        return JSONResponse({"error": "campaña desconocida"}, status_code=404)
    return {"ok": True, "sequence_id": campaigns_mod.seed(camp)}


@app.post("/api/contacts/{contact_id}/registrado")
def contact_registrado(contact_id: int) -> dict:
    """Marca que el contacto se registró: cierra la captación y lo saca de la
    cadencia. Un recordatorio de invitación después del sí quema el contacto."""
    c = store.get_contact(contact_id)
    if not c:
        return JSONResponse({"error": "contacto no encontrado"}, status_code=404)
    store.set_stage(contact_id, "registrado")
    store.add_activity(contact_id, "email", "registro", store.now_iso(),
                       ext_id=f"reg:{contact_id}")
    cerradas = store.pause_sequences_for(contact_id, "completed")
    return {"ok": True, "stage": "registrado", "sequences_closed": cerradas}


# ----------------------------- RSVP (público) ---------------------------
@app.get("/invitacion")
def landing_invitacion():
    """Landing de confirmación. Va fuera de /api/ porque la abre el invitado."""
    return FileResponse(BACKEND_DIR / "static" / "invitacion.html",
                        media_type="text/html")


@app.get("/api/rsvp/contexto")
def rsvp_contexto(k: str = "desayuno-28", c: str = "") -> dict:
    """Lo que la landing necesita saber sin exponer la base: la sede ya
    confirmada y, si el link trae código, a nombre de quién va la invitación."""
    camp = campaigns_mod.get(k)
    out: dict = {"lugar": "", "nombre": "", "empresa": "", "email": ""}
    if camp:
        out["lugar"] = camp.blanks().get("lugar", "")
    if c:
        fila = links_mod.resolve(c)
        if fila:
            contacto = store.get_contact(fila["contact_id"])
            if contacto:
                nombre = (contacto.get("nombre") or "").strip()
                out["nombre"] = nombre.split(" ")[0] if nombre else ""
                out["empresa"] = contacto.get("empresa") or ""
                out["email"] = contacto.get("email") or ""
    return out


class RsvpStart(BaseModel):
    campaign: str = "desayuno-28"
    email: str
    telefono: str
    attribution_code: str = ""


@app.post("/api/rsvp/start")
def rsvp_start(body: RsvpStart) -> dict:
    camp = campaigns_mod.get(body.campaign)
    if camp is None:
        return JSONResponse({"ok": False, "errores": ["campaña desconocida"]},
                            status_code=404)
    return rsvp_mod.iniciar(camp.slug, camp.event_name, body.email, body.telefono,
                            attribution_code=body.attribution_code)


class RsvpVerify(BaseModel):
    rsvp_id: int
    canal: str
    codigo: str


@app.post("/api/rsvp/verify")
def rsvp_verify(body: RsvpVerify) -> dict:
    if body.canal not in ("email", "sms"):
        return JSONResponse({"ok": False, "error": "canal inválido"}, status_code=400)
    return rsvp_mod.verificar(body.rsvp_id, body.canal, body.codigo)


class RsvpAcomp(BaseModel):
    rsvp_id: int
    nombre: str


@app.post("/api/rsvp/acompanante")
def rsvp_acompanante(body: RsvpAcomp) -> dict:
    return rsvp_mod.acompanante(body.rsvp_id, body.nombre)


@app.get("/api/rsvp/resumen")
def rsvp_resumen(campaign: str = "desayuno-28") -> dict:
    return rsvp_mod.resumen(campaign)


# ----------------------------- ATRIBUCIÓN (público) ---------------------
@app.get("/r/{code}")
def redirect_link(code: str, request: Request):
    """Link propio de cada contacto → registro del evento, con UTM.

    Va fuera de /api/ a propósito: lo abre el prospecto desde su correo, sin
    sesión. Si el código no existe, manda igual al evento en vez de mostrar un
    error — perder la atribución es malo, perder al invitado es peor.
    """
    row = links_mod.register_click(code, request.headers.get("user-agent", ""))
    slug = row["campaign"] if row else "desayuno-28"
    destino = f"{PUBLIC_BASE_URL}/invitacion?k={slug}&c={code}"
    return RedirectResponse(destino, status_code=302)


@app.get("/api/tasks")
def tasks_list(status: str = "open", due: Optional[str] = None) -> dict:
    return {"tasks": store.list_tasks(status, due_before=due)}


@app.post("/api/tasks/{task_id}/{action}")
def task_update(task_id: int, action: str) -> dict:
    status = {"done": "done", "dismiss": "dismissed"}.get(action)
    if not status:
        return JSONResponse({"error": "acción inválida"}, status_code=400)
    store.set_task_status(task_id, status)
    return {"ok": True}


@app.get("/api/briefing")
def get_briefing(force: bool = False) -> dict:
    return scheduler_mod.maybe_briefing(force=force)


# ----------------------------- llamadas (trazabilidad) ------------------
class CallUpdate(BaseModel):
    key: str
    called: bool = True
    outcome: Optional[str] = None
    notes: Optional[str] = None
    by: Optional[str] = None


@app.get("/api/calls")
def get_calls() -> dict:
    return calls_store.load("leads")


@app.post("/api/calls")
def post_call(body: CallUpdate) -> dict:
    entry = calls_store.set_call(body.key, body.called,
                                 outcome=body.outcome, notes=body.notes,
                                 by=body.by or "", ns="leads")
    # "Volver a llamar" ahora crea una tarea con fecha que aparece en "Hoy".
    if (body.outcome or "").strip().lower().startswith("volver"):
        row = store.fetch_one("SELECT id FROM contacts WHERE csv_key=?", (body.key,)) \
            or store.find_contact(email=body.key)
        if row and not store.open_task_exists(row["id"], "call"):
            from datetime import datetime, timedelta
            due = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
            store.add_task(row["id"], "call", due,
                           notes=body.notes or "Volver a llamar")
    return entry


# ----------------------------- templates --------------------------------
@app.get("/api/templates")
def get_templates() -> dict:
    import json
    out = []
    if TEMPLATES_DIR.exists():
        for f in sorted(TEMPLATES_DIR.glob("*.json")):
            try:
                tpl = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({
                "file": f.name,
                "nombre": tpl.get("nombre", f.stem),
                "subject": tpl.get("subject", ""),
                "cta": tpl.get("cta", {}),
            })
    return {"templates": out}


# ----------------------------- stats ------------------------------------
@app.get("/api/stats/overview")
def stats_overview() -> dict:
    return stats_mod.overview()


@app.get("/api/stats/email")
def stats_email() -> dict:
    return stats_mod.email_metrics()


@app.get("/api/stats/email/engagement")
def stats_email_engagement() -> dict:
    """Quién abrió, quién hizo click, quién respondió y quién rebotó."""
    return stats_mod.email_engagement()


# ----------------------------- history ----------------------------------
@app.get("/api/history")
def history() -> dict:
    return {"runs": list_runs()}


@app.get("/api/history/{run_id}")
def history_detail(run_id: str, channel: str = "linkedin") -> dict:
    return run_detail(run_id, channel)


# ----------------------------- jobs -------------------------------------
class LinkedInJob(BaseModel):
    urls: list[str] = []
    contacts: list[dict] = []
    daily_cap: int = 20
    min_delay: float = 8.0
    max_delay: float = 18.0
    headless: bool = True
    sales_nav: bool = False
    with_notes: bool = False
    # Nota personalizada de la INVITACIÓN (opcional). Si viene, tiene prioridad
    # sobre la nota autogenerada de with_notes. Soporta {nombre} y {empresa}.
    message: str = ""


class FollowupJob(BaseModel):
    urls: list[str] = []
    contacts: list[dict] = []
    message: str = ""
    default_message: str = ""
    daily_cap: int = 30
    min_delay: float = 6.0
    max_delay: float = 14.0
    headless: bool = True


class EmailJob(BaseModel):
    recipients: list[dict] = []
    template: Optional[str] = None
    subject: Optional[str] = None
    bcc: list[str] = []
    dry_run: bool = True
    from_email: Optional[str] = None
    from_name: Optional[str] = None


def _urls_from(payload) -> list[str]:
    urls = list(payload.urls)
    for c in payload.contacts:
        if c.get("linkedin"):
            urls.append(c["linkedin"])
    # dedup preservando orden
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _enforce_global_cap(pool: str, items: list) -> tuple[list, dict]:
    """Trunca `items` al remanente REAL del presupuesto diario — el mismo que
    usa el copiloto (scheduler.budgets(), lee activities+approval_queue de
    hoy en store.db). Antes cada página manual (Conexiones/Follow-ups/
    Correos) llevaba su propio daily_cap por-lanzamiento, ajeno al del
    copiloto y entre sí: se podía superar el tope real del día con solo
    relanzar la página varias veces (o, en Correos, sin ningún tope)."""
    b = scheduler_mod.budgets()[pool]
    remaining = b["remaining"]
    capped = items[:remaining] if remaining < len(items) else items
    info = {"cap": b["cap"], "usado_hoy": b["used"] + b["queued"],
            "remaining": remaining, "solicitados": len(items), "enviados": len(capped)}
    return capped, info


@app.post("/api/jobs/linkedin")
def job_linkedin(body: LinkedInJob) -> dict:
    urls = _urls_from(body)
    if not urls:
        return {"error": "sin URLs"}
    urls, cap_info = _enforce_global_cap("linkedin", urls)
    if not urls:
        return {"error": f"Tope diario de LinkedIn ya alcanzado "
                          f"({cap_info['usado_hoy']}/{cap_info['cap']} usado+en cola hoy, "
                          "compartido con el copiloto). Ajusta el tope en Ajustes o espera a mañana."}
    # Mensaje de invitación personalizado: si el usuario escribió uno, se usa
    # tal cual (con {nombre}/{empresa} por contacto) en vez de la nota auto.
    messages = None
    if body.message.strip():
        by_url = {c.get("linkedin"): c for c in body.contacts if c.get("linkedin")}
        messages = {}
        for u in urls:
            c = by_url.get(u, {})
            nombre = (c.get("nombre") or "").split(" ")[0].title()
            m = (body.message.replace("{nombre}", nombre)
                             .replace("{empresa}", c.get("empresa", "")))
            messages[u] = m[:300].strip()  # LinkedIn limita la nota de invitación
    job = manager.launch_linkedin(urls, body.model_dump(), body.contacts or None, messages)
    result = job.progress()
    result["cap_info"] = cap_info
    return result


@app.post("/api/jobs/followup")
def job_followup(body: FollowupJob) -> dict:
    urls = _urls_from(body)
    if not urls:
        return {"error": "sin URLs"}
    urls, cap_info = _enforce_global_cap("linkedin", urls)
    if not urls:
        return {"error": f"Tope diario de LinkedIn ya alcanzado "
                          f"({cap_info['usado_hoy']}/{cap_info['cap']} usado+en cola hoy, "
                          "compartido con el copiloto y con Conexiones). Ajusta el tope en Ajustes o espera a mañana."}
    job = manager.launch_followup(urls, body.model_dump(), body.contacts or None)
    result = job.progress()
    result["cap_info"] = cap_info
    return result


@app.post("/api/jobs/email")
def job_email(body: EmailJob) -> dict:
    if not body.recipients:
        return {"error": "sin destinatarios"}
    recipients, cap_info = body.recipients, None
    if not body.dry_run:  # una simulación no envía nada real: no cuenta contra el tope
        recipients, cap_info = _enforce_global_cap("email", body.recipients)
        if not recipients:
            return {"error": f"Tope diario de email ya alcanzado "
                              f"({cap_info['usado_hoy']}/{cap_info['cap']} usado+en cola hoy, "
                              "compartido con el copiloto). Ajusta el tope en Ajustes o espera a mañana."}
    job = manager.launch_email(recipients, body.model_dump())
    result = job.progress()
    if cap_info:
        result["cap_info"] = cap_info
    return result


@app.get("/api/jobs")
def jobs_list() -> dict:
    return {"jobs": manager.list()}


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str) -> dict:
    job = manager.get(job_id)
    return job.progress() if job else {"error": "no encontrado"}


@app.post("/api/jobs/{job_id}/stop")
def job_stop(job_id: str) -> dict:
    job = manager.get(job_id)
    if not job:
        return {"error": "no encontrado"}
    job.stop()
    return job.progress()


@app.websocket("/ws/jobs/{job_id}")
async def ws_job(ws: WebSocket, job_id: str):
    if _desde_fuera(ws.headers, ws.headers.get("host", "")):
        await ws.close(code=1008)
        return
    if auth_mod.enabled() and not auth_mod.verify_session(ws.query_params.get("token", "")):
        await ws.close(code=1008)
        return
    await ws.accept()
    job = manager.get(job_id)
    if not job:
        await ws.send_json({"type": "error", "data": "job no encontrado"})
        await ws.close()
        return
    try:
        await tail_events(job, ws.send_text)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# ------------------------- peticiones de prospección ----------------------
# Bandeja de pedidos: alguien escribe una empresa y la máquina de prospección
# la trabaja en su siguiente vuelta. Los estados están en peticiones.py.
class PeticionNueva(BaseModel):
    consulta: str = ""


def _quien(request: Request) -> str:
    """Correo de quien llama, sacado de la sesión. Vacío si el login está
    apagado — eso no es motivo para rechazar la petición."""
    tok = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    ses = auth_mod.verify_session(tok) if tok else None
    return (ses or {}).get("email", "")


@app.get("/api/peticiones")
def listar_peticiones(estado: str = "", limite: int = 100) -> dict:
    return peticiones_mod.listar(estado, limite)


@app.post("/api/peticiones")
def crear_peticion(body: PeticionNueva, request: Request):
    r = peticiones_mod.crear(body.consulta, _quien(request))
    if not r.get("ok"):
        return JSONResponse(r, status_code=400)
    return r


@app.post("/api/peticiones/{pid}/cancelar")
def cancelar_peticion(pid: int) -> dict:
    return peticiones_mod.cancelar(pid)


@app.post("/api/peticiones/{pid}/reintentar")
def reintentar_peticion(pid: int) -> dict:
    return peticiones_mod.reintentar(pid)


# ----------------------------- webhook ----------------------------------
@app.post("/api/webhooks/apollo/telefono")
async def apollo_telefono_webhook(request: Request) -> dict:
    """Recibe los teléfonos que Apollo revela de forma asíncrona.

    Se responde 200 pase lo que pase: «Apollo may retry webhook calls», y un
    reintento en bucle sobre un payload que ya se aplicó no arregla nada. El
    crudo queda en disco antes de tocar la base, así que un fallo al escribir no
    pierde el dato — que ya está pagado a ~8 créditos la persona.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"received": 0}
    try:
        r = tel_apollo_mod.recibir(payload)
    except Exception as e:  # noqa: BLE001
        print(f"[webhook apollo] {e.__class__.__name__}: {e}")
        return {"received": 0}
    print(f"[webhook apollo] personas={r['personas']} escritos={r['escritos']} "
          f"sin_numero={r['sin_numero']} sin_mapa={r['sin_mapa']}")
    return {"received": r["personas"], "escritos": r["escritos"]}


@app.get("/api/telefonos/apollo")
def telefonos_apollo_estado() -> dict:
    """Saldo, webhook y cuántos teléfonos están en vuelo."""
    return tel_apollo_mod.estado()


@app.post("/api/telefonos/apollo/pedir")
def telefonos_apollo_pedir(stage: str = "conectado", limite: int = 0,
                           simular: bool = False, forzar: bool = False) -> dict:
    """Pide teléfonos. Los números NO vuelven aquí: llegan por el webhook."""
    return tel_apollo_mod.pedir(stage=stage, limite=limite,
                                simular=simular, forzar=forzar)


@app.post("/api/webhooks/sendgrid")
async def sendgrid_webhook(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {"received": 0}
    events = payload if isinstance(payload, list) else [payload]
    result = webhooks_mod.ingest(events)
    if not result.get("ok"):
        # se responde 200 igual (SendGrid no debe reintentar en bucle), pero
        # queda en el log del servidor — antes esto desaparecía sin rastro.
        print(f"[webhook sendgrid] {result.get('error', 'fallo desconocido')}")
    return {"received": result.get("received", 0)}
