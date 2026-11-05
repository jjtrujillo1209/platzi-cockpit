"""Motor de cadencia del copiloto (modo aprobación humana).

Cada tick (15 min por defecto, horario laboral Colombia):
  0. Sincroniza BASE_SEGMENTADA.csv → store.db (contactos + actividades). Sin
     este paso, cualquier cambio manual al CSV (prospección, enriquecimiento,
     ediciones directas) queda invisible para "Hoy" — el
     briefing y los presupuestos de cadencia se calculan sobre store.db, no
     sobre el CSV. Antes esto solo corría si alguien pulsaba el botón manual
     "Sincronizar" — un ciclo real llegó a acumular un desfase de 374 vs 838
     contactos (2026-07-08) porque nadie lo disparó.
  1. Ingesta señales (replies Gmail, accepts, eventos de runners, webhook).
  2. Reconcilia aprobaciones enviadas contra los eventos JSONL de los jobs y
     avanza las secuencias que se confirmaron.
  3. Genera drafts con Claude para los pasos vencidos → approval_queue.
     NUNCA envía solo: el envío ocurre solo vía send_approved() cuando el
     usuario aprueba en la página "Hoy".
  4. Primer tick del día: briefing matinal.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import date, datetime, timedelta
from typing import Optional

import ai
import campaigns
import conexiones
import hubspot_conexion
import secuencias_am
import secuencias_hs
import ingest
import links
import nube
import seats
import senales_li
import store
import sync
import webhooks
from config import RESULTS_DIR, TRACKING_DB
from contacts import _norm_li
from jobs import manager
from runs import read_jsonl

TICK_MINUTES = float(os.environ.get("PLATZI_AGENT_TICK_MIN", "15"))

# Presupuesto de correo (editable vía /api/settings/cadence → kv). LinkedIn ya
# no tiene un número aquí: el cupo es de cada perfil y lo lleva `seats.py`.
# Sumar tres perfiles en una sola bolsa fue justo lo que se quitó — con un tope
# global, dos asientos con cupo se comen el del tercero y el tercero es el que
# recibe la restricción.
DEFAULT_CAP_EMAIL = 50

_EVENTS_SUFFIX = {
    "linkedin_invite": "-events.jsonl",
    "linkedin_msg": "-followup-events.jsonl",
    "email": "-mail-events.jsonl",
}
_OK_STATUS = {
    "linkedin_invite": {"sent", "already", "pending_invite"},
    "linkedin_msg": {"message_sent"},
    "email": {"sent", "dry_run"},
}
_ACTIVITY_FOR = {
    "linkedin_invite": ("linkedin", "invite_sent"),
    "linkedin_msg": ("linkedin", "followup_sent"),
    "email": ("email", "sent"),
}


# ----------------------------- config -------------------------------------
def cadence_settings() -> dict:
    res = seats.resumen()
    return {
        "enabled": store.kv_get("cadence_enabled", "1") == "1",
        # Derivado, no configurable: es lo que suman hoy los asientos con sesión
        # viva, ya con su calentamiento aplicado. Se sigue publicando con este
        # nombre para no romper a quien lo lee.
        "cap_linkedin": res["cap_invites"] + res["cap_mensajes"],
        "cap_email": int(store.kv_get("cap_email", str(DEFAULT_CAP_EMAIL))),
        "seats_listos": res["listos"],
        "seats_total": res["total"],
        "tick_minutes": TICK_MINUTES,
        "last_tick": store.kv_get("last_tick", ""),
    }


def update_cadence_settings(enabled: Optional[bool] = None,
                            cap_email: Optional[int] = None) -> dict:
    if enabled is not None:
        store.kv_set("cadence_enabled", "1" if enabled else "0")
    if cap_email is not None:
        store.kv_set("cap_email", str(max(0, cap_email)))
    return cadence_settings()


# Festivos de Colombia. Sin esto la cadencia agenda toques en días muertos: el
# 17 de agosto de 2026 es Asunción corrida por Ley Emiliani, y cae justo en la
# ventana de la campaña del desayuno.
FESTIVOS_CO = {
    date(2026, 8, 7),    # Batalla de Boyacá
    date(2026, 8, 17),   # Asunción de la Virgen (Ley Emiliani)
    date(2026, 10, 12),  # Día de la Raza
    date(2026, 11, 2),   # Todos los Santos
    date(2026, 11, 16),  # Independencia de Cartagena
    date(2026, 12, 8),   # Inmaculada Concepción
    date(2026, 12, 25),  # Navidad
}


def es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in FESTIVOS_CO


def in_business_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return es_habil(now.date()) and 8 <= now.hour < 18


def _next_business_slot(days: int, not_after: Optional[date] = None) -> str:
    """now + N días, en horario laboral hábil, con jitter para no ser robótico.

    `not_after` es el tope de la campaña: si el espaciado normal se pasaría de
    la fecha del evento, el toque se comprime al último día hábil disponible en
    vez de agendarse para después — un recordatorio que llega el lunes de un
    desayuno del viernes no sirve de nada.
    """
    t = datetime.now() + timedelta(days=days)
    while not es_habil(t.date()):
        t += timedelta(days=1)
    if not_after and t.date() > not_after:
        d = not_after
        while d > datetime.now().date() and not es_habil(d):
            d -= timedelta(days=1)
        t = datetime.combine(max(d, datetime.now().date()), t.time())
    hour = random.randint(8, 16)
    t = t.replace(hour=hour, minute=random.randint(0, 59), second=0)
    return t.isoformat(timespec="seconds")


# ----------------------------- presupuestos --------------------------------
def budgets() -> dict:
    """Cupo del día. LinkedIn viene desglosado por asiento porque el total no
    es gastable: 6 libres en un perfil y 0 en otro no son 6 disponibles para
    cualquiera — el contacto de un asiento agotado espera aunque sobre cupo al
    lado."""
    cfg = cadence_settings()
    res = seats.resumen()
    used_em = store.count_activities_today("email", ("sent",))
    today = datetime.now().strftime("%Y-%m-%d")
    queued = {r["channel"]: r["n"] for r in store.fetch_all(
        "SELECT channel, COUNT(*) n FROM approval_queue "
        "WHERE status IN ('pending','approved','sent') AND substr(created_at,1,10)=? "
        "GROUP BY channel", (today,))}
    queued_em = queued.get("email", 0)
    li_cap = res["cap_invites"] + res["cap_mensajes"]
    li_used = res["used_invites"] + res["used_mensajes"]
    li_queued = res["queued_invites"] + res["queued_mensajes"]
    return {
        "linkedin": {"cap": li_cap, "used": li_used, "queued": li_queued,
                     "remaining": res["remaining_invites"] + res["remaining_mensajes"]},
        "linkedin_invites": {"cap": res["cap_invites"], "used": res["used_invites"],
                             "queued": res["queued_invites"],
                             "remaining": res["remaining_invites"]},
        "linkedin_mensajes": {"cap": res["cap_mensajes"], "used": res["used_mensajes"],
                              "queued": res["queued_mensajes"],
                              "remaining": res["remaining_mensajes"]},
        "seats": res["seats"],
        "seats_problemas": res["problemas"],
        "email": {"cap": cfg["cap_email"], "used": used_em, "queued": queued_em,
                  "remaining": max(0, cfg["cap_email"] - used_em - queued_em)},
    }


# ----------------------------- drafts --------------------------------------
def _has_accepted(contact_id: int) -> bool:
    return bool(store.fetch_one(
        "SELECT id FROM activities WHERE contact_id=? AND type='accepted' LIMIT 1",
        (contact_id,)))


def _history_summary(contact_id: int, limit: int = 6) -> str:
    rows = store.fetch_all(
        "SELECT channel, type, ts FROM activities WHERE contact_id=? "
        "ORDER BY ts DESC LIMIT ?", (contact_id, limit))
    return "; ".join(f"{r['ts'][:10]} {r['channel']}:{r['type']}" for r in reversed(rows))


def _effective_channel(step_channel: str, contact: dict) -> Optional[str]:
    """Adapta el canal del paso a lo que el contacto permite.

    linkedin_msg requiere conexión aceptada (si no, cae a email);
    linkedin_invite requiere perfil; email requiere correo; call requiere
    teléfono y no cae a ningún otro canal. None = saltar paso.
    """
    has_li = bool((contact.get("linkedin") or "").strip())
    has_email = bool((contact.get("email") or "").strip())
    has_tel = bool((contact.get("celular") or "").strip())
    if step_channel == "call":
        return "call" if has_tel else None
    if step_channel == "linkedin_invite":
        return "linkedin_invite" if has_li else ("email" if has_email else None)
    if step_channel == "linkedin_msg":
        if has_li and _has_accepted(contact["id"]):
            return "linkedin_msg"
        return "email" if has_email else None
    if step_channel == "email":
        if has_email:
            return "email"
        return "linkedin_msg" if has_li and _has_accepted(contact["id"]) else None
    return None


def _advance_after(cs: dict, campaign, next_order: int) -> None:
    """Agenda el paso siguiente respetando el tope de la campaña."""
    nxt = campaigns.step_for(campaign, next_order)
    if nxt is None:
        store.advance_sequence(cs["id"], store.now_iso())
        return
    store.advance_sequence(cs["id"], _next_business_slot(
        nxt.delay_days, not_after=nxt.send_by(campaign.event_day)))


def generate_drafts(limit: int = 40) -> dict:
    """Pasos vencidos → mensajes renderizados en approval_queue (sin enviar).

    El copy sale de la campaña; Claude solo aporta el gancho. Lo que no pasa la
    compuerta entra igual, pero como `blocked`: visible y no enviable.
    """
    b = budgets()
    remaining_email = b["email"]["remaining"]
    created = skipped = blocked = tasks = 0
    # Contactos que hoy no se pueden tocar y por qué. No es un detalle de log:
    # si los tres VP siguen sin entregar el login, esto es lo único que explica
    # por qué la cola amaneció vacía con 500 contactos inscritos.
    en_espera: dict[str, int] = {}
    hoy = datetime.now().date()

    for cs in store.due_sequences(limit=limit):
        if store.pending_approval_for(cs["id"]):
            continue
        # Doble envío: si el contacto ya va por Amplemarket, la cadencia local no
        # lo toca. Sin esto, un contacto inscrito en los dos motores recibe la
        # misma invitación dos veces, con dos remitentes distintos.
        if store.fetch_one(
                "SELECT id FROM approval_queue WHERE contact_id=? AND channel='amplemarket' "
                "AND status IN ('pending','blocked','approved','sent','confirmed') LIMIT 1",
                (cs["contact_id"],)):
            store.set_sequence_status(cs["id"], "paused")
            skipped += 1
            continue
        campaign = campaigns.for_sequence(cs["sequence_id"])
        if campaign is None:
            # Secuencia sin campaña dueña: no hay copy aprobado que renderizar y
            # este cockpit no improvisa mensajes. Se deja quieta.
            continue
        if hoy > campaign.cutoff_day:
            # Pasó el corte: invitar a dos días del evento no mueve a un
            # presidente y sí quema el contacto.
            store.set_sequence_status(cs["id"], "completed")
            continue

        db_steps = store.sequence_steps(cs["sequence_id"])
        if cs["current_step"] >= len(db_steps):
            store.set_sequence_status(cs["id"], "completed")
            continue
        db_step = db_steps[cs["current_step"]]
        step = campaigns.step_for(campaign, db_step["step_order"])
        contact = store.get_contact(cs["contact_id"])
        if not contact or step is None:
            continue

        channel = _effective_channel(step.channel, contact)
        if channel is None:
            store.advance_sequence(cs["id"], store.now_iso())
            skipped += 1
            continue

        if channel == "call":
            # La llamada no es un borrador: es una tarea para una persona. El
            # guion va renderizado en la nota para no tener que buscarlo. No
            # gasta cupo de ningún asiento: la hace el equipo, no un perfil.
            if not store.open_task_exists(contact["id"], "call"):
                link = (links.url_for(contact["id"], campaign.slug)
                        if "{{link}}" in step.body else "")
                msg = ai.build_message(contact, campaign, step, link=link)
                store.add_task(contact["id"], "call", store.now_iso(),
                               notes=msg["body"],
                               meta={"campaign": campaign.slug, "step": step.order,
                                     "label": step.label})
                tasks += 1
            _advance_after(cs, campaign, step.order + 1)
            continue

        # Qué perfil firma este toque. Para LinkedIn es obligatorio: sin asiento
        # con sesión y cupo no se genera el borrador. Encolarlo igual sería
        # fabricar trabajo que nadie puede enviar y, peor, dejar el cupo del
        # asiento comprometido con mensajes que envejecen en la cola.
        seat = None
        if channel in ("linkedin_invite", "linkedin_msg"):
            seat, motivo = seats.pick_for(contact, channel)
            if seat is None:
                en_espera[motivo] = en_espera.get(motivo, 0) + 1
                continue          # el paso sigue vencido: se reintenta al próximo tick
        elif remaining_email <= 0:
            continue

        firma_anfitrion = bool(seat is not None and seat.es_anfitrion)
        link = (links.url_for(contact["id"], campaign.slug)
                if "{{link}}" in step.cuerpo(firma_anfitrion) else "")

        msg = ai.build_message(contact, campaign, step, link=link, seat=seat)
        store.add_approval(contact["id"], channel, msg["subject"], msg["body"],
                           cs_id=cs["id"], step_id=db_step["id"],
                           model=msg["model"], reasons=msg["reasons"])
        if msg["blocked"]:
            blocked += 1
        else:
            if channel == "email":
                remaining_email -= 1
            created += 1

    return {"created": created, "blocked": blocked, "call_tasks": tasks,
            "skipped_steps": skipped, "en_espera": en_espera, "budgets": b}


def call_tasks_from_clicks() -> dict:
    """Clic sin registro → llamada para hoy.

    Es la señal más caliente del flujo: abrió, le interesó y algo lo interrumpió.
    Esperar al paso siguiente de la cadencia desperdicia el momento.
    """
    creadas = 0
    for camp in (campaigns.get(s["slug"]) for s in campaigns.list_all()):
        if camp is None:
            continue
        for c in links.clicked_contacts(camp.slug):
            if c["stage"] in ("registrado", "reunion", "cliente", "descartado"):
                continue
            if not (c.get("celular") or "").strip():
                continue
            if store.open_task_exists(c["id"], "call"):
                continue
            store.add_task(c["id"], "call", store.now_iso(),
                           notes=(f"Hizo clic en el link de {camp.name} y no se ha "
                                  "registrado. Llamar hoy: «Vi que le llegó la "
                                  "invitación — ¿le guardo el puesto?»"),
                           meta={"campaign": camp.slug, "trigger": "clic_sin_registro"})
            creadas += 1
    return {"created": creadas}


# ----------------------------- envío (tras aprobar) ------------------------
def _opts_para(seat, cap: int) -> dict:
    """Opciones del motor para un asiento concreto.

    El CDP manda sobre las cookies: contra un Chrome que ya tiene la sesión
    abierta el motor entra como el dueño del perfil, mientras que inyectar
    cookies en un navegador limpio termina en checkpoint. El `daily_cap` que se
    pasa es el del ASIENTO, no el del cockpit: es la última barrera si alguien
    aprueba de más.
    """
    opts = {"daily_cap": max(1, cap), "headless": True,
            "cookies": seat.cookies, "seat": seat.id,
            # Guardar en Sales Navigator sale gratis en la misma visita al
            # perfil, y en el asiento que lo tiene es la mitad del valor.
            "sales_nav": seat.sales_nav}
    if seat.cdp:
        opts["cdp_endpoint"] = seat.cdp
    return opts


def _cupo_envio(b: dict, canal: str) -> int:
    """Cuánto puede salir AHORA por este asiento.

    Se recalcula contra lo ya enviado hoy —no contra lo encolado— porque lo
    encolado es justamente lo que está por salir. Una aprobación de ayer que se
    envía hoy tiene que caber en el cupo de hoy.
    """
    if canal == "linkedin_msg":
        return max(0, b["mensajes"]["cap"] - b["mensajes"]["used"])
    return max(0, min(b["invites"]["cap"] - b["invites"]["used"],
                      b["semana"]["cap"] - b["semana"]["used"]))


def send_approved(dry_run_email: bool = False) -> dict:
    """Agrupa las aprobaciones 'approved' por canal y asiento, y lanza los jobs.
    Se invoca desde POST /api/approvals/send (acción humana).

    Un job por asiento: cada VP tiene su sesión, su cupo y su navegador. Y como
    mucho UN job por asiento en cada corrida — dos motores sobre el mismo perfil
    se pelean el navegador y dejan las pestañas trancadas, que es una falla que
    se ve como 'rate_limited' sin serlo.
    """
    approved = store.list_approvals("approved")
    out: dict = {"jobs": [], "sent": 0, "blocked": [], "diferidos": []}
    caps = cadence_settings()

    emails = [a for a in approved if a["channel"] == "email" and (a["email"] or "").strip()]
    if emails:
        recipients = [{
            "email": a["email"], "nombre": a["nombre"], "empresa": a["empresa"],
            "cargo": a["cargo"], "linkedin": a["linkedin"],
            "custom_subject": a["draft_subject"], "custom_body": a["draft_body"],
        } for a in emails]
        job = manager.launch_email(recipients, {"dry_run": dry_run_email,
                                                "campaign": "copiloto"})
        store.mark_approvals_sent([a["id"] for a in emails], job.job_id)
        out["jobs"].append(job.progress())
        out["sent"] += len(emails)

    # --- LinkedIn: por asiento ------------------------------------------
    por_asiento: dict[str, dict[str, list]] = {}
    for a in approved:
        if a["channel"] not in ("linkedin_invite", "linkedin_msg"):
            continue
        if not (a["linkedin"] or "").strip():
            continue
        grupo = por_asiento.setdefault((a.get("li_seat") or "").strip(), {})
        grupo.setdefault(a["channel"], []).append(a)

    for seat_id, grupos in sorted(por_asiento.items()):
        pendientes = sum(len(v) for v in grupos.values())
        seat = seats.get(seat_id) if seat_id else None
        if seat is None:
            out["blocked"].append(
                f"{pendientes} aprobación(es) de LinkedIn sin asiento asignado: "
                "no se sabe qué perfil las firma, así que no salen")
            continue
        # Levanta la ventana del VP si está cerrada y confirma que la sesión
        # sigue viva. Es lo que hace que el VP entre UNA vez: su Chrome se
        # despierta solo con la sesión que quedó en disco.
        listo = seats.asegurar_envio(seat)
        if not listo.get("ok"):
            out["blocked"].append(
                f"{seat.etiqueta}: {listo.get('error') or 'sin sesión de LinkedIn'} — sus "
                f"{pendientes} aprobación(es) quedan en cola, no se pierden")
            continue
        if listo.get("levantado"):
            out.setdefault("ventanas_levantadas", []).append(seat.etiqueta)

        b = seats.budget(seat)
        # Mensajes primero: van a alguien que acabó de aceptar, que es el momento
        # más caliente y el que menos aguanta esperar un día.
        canal = "linkedin_msg" if grupos.get("linkedin_msg") else "linkedin_invite"
        lote = grupos.get(canal, [])
        cupo = _cupo_envio(b, canal)
        if cupo <= 0:
            out["blocked"].append(
                f"{seat.etiqueta}: agotó su cupo de hoy ({'mensajes' if canal == 'linkedin_msg' else 'invitaciones'}) — "
                f"{len(lote)} aprobación(es) esperan a mañana")
            continue
        if len(lote) > cupo:
            out["diferidos"].append(
                f"{seat.etiqueta}: salen {cupo} de {len(lote)} {'mensajes' if canal == 'linkedin_msg' else 'invitaciones'}; "
                "el resto queda aprobado para la próxima corrida")
            lote = lote[:cupo]
        otro = "linkedin_invite" if canal == "linkedin_msg" else "linkedin_msg"
        if grupos.get(otro):
            out["diferidos"].append(
                f"{seat.etiqueta}: {len(grupos[otro])} "
                f"{'invitaciones' if otro == 'linkedin_invite' else 'mensajes'} quedan "
                "para la próxima corrida (un perfil no puede correr dos navegadores a la vez)")

        urls = [a["linkedin"] for a in lote]
        messages = {a["linkedin"]: a["draft_body"] for a in lote}
        opts = _opts_para(seat, cupo)
        job = (manager.launch_followup(urls, opts, messages=messages)
               if canal == "linkedin_msg"
               else manager.launch_linkedin(urls, opts, messages=messages))
        store.mark_approvals_sent([a["id"] for a in lote], job.job_id)
        # El reloj del calentamiento arranca con el primer envío real, no con la
        # configuración del asiento.
        seats.marcar_arranque(seat)
        out["jobs"].append({**job.progress(), "seat": seat.id,
                            "seat_nombre": seat.etiqueta})
        out["sent"] += len(lote)

    if not por_asiento and not emails:
        out["blocked"].extend(seats.preflight())
    out["cap_email"] = caps["cap_email"]
    return out


# ----------------------------- reconciliación ------------------------------
def _find_event(events: list[dict], approval: dict) -> Optional[dict]:
    if approval["channel"] == "email":
        addr = (approval["email"] or "").strip().lower()
        for e in events:
            if (e.get("email") or "").strip().lower() == addr:
                return e
    else:
        slug = _norm_li(approval["linkedin"] or "")
        for e in events:
            if _norm_li(e.get("url", "")) == slug:
                return e
    return None


def reconcile_sent() -> dict:
    """Cruza aprobaciones 'sent' con los eventos del job: confirma y avanza la
    secuencia, o marca fallo y reintenta mañana."""
    confirmed, failed = 0, 0
    for a in store.list_approvals("sent"):
        run_id = a["sent_run_id"]
        if not run_id:
            continue
        events_file = RESULTS_DIR / f"{run_id}{_EVENTS_SUFFIX[a['channel']]}"
        events = [e for e in read_jsonl(events_file) if e.get("idx", 1) != 0]
        ev = _find_event(events, a)
        job_row = store.fetch_one("SELECT status FROM jobs_log WHERE run_id=?", (run_id,))
        job_live = manager.get(run_id)
        job_status = job_live.status if job_live else (job_row or {}).get("status", "")

        if ev and ev.get("status") in _OK_STATUS[a["channel"]]:
            store.decide_approval(a["id"], "confirmed")
            chan, type_ = _ACTIVITY_FOR[a["channel"]]
            if ev.get("status") != "dry_run":
                store.add_activity(a["contact_id"], chan, type_,
                                   ev.get("ts", store.now_iso()), run_id=run_id,
                                   ext_id=f"appr:{a['id']}",
                                   meta={"raw_status": ev.get("status", ""),
                                         "seat": a.get("li_seat", "")})
            if ev.get("status") == "already":
                store.add_activity(a["contact_id"], "linkedin", "accepted",
                                   ev.get("ts", store.now_iso()),
                                   ext_id=f"appr-acc:{a['id']}")
            if a["contact_sequence_id"]:
                cs = store.fetch_one("SELECT * FROM contact_sequences WHERE id=?",
                                     (a["contact_sequence_id"],))
                if cs and cs["status"] == "active":
                    steps = store.sequence_steps(cs["sequence_id"])
                    nxt = cs["current_step"] + 1
                    campaign = campaigns.for_sequence(cs["sequence_id"])
                    if campaign and nxt < len(steps):
                        _advance_after(cs, campaign, steps[nxt]["step_order"])
                    else:
                        delay = steps[nxt]["delay_days"] if nxt < len(steps) else 0
                        store.advance_sequence(cs["id"], _next_business_slot(delay))
            confirmed += 1
        elif (ev and ev.get("status") in ("error", "rate_limited", "skipped")) \
                or job_status in ("done", "error", "stopped", "interrupted"):
            store.decide_approval(a["id"], "failed")
            if a["contact_sequence_id"]:
                store.set_sequence_status(a["contact_sequence_id"], "active",
                                          _next_business_slot(1))
            failed += 1
    return {"confirmed": confirmed, "failed": failed}


# ----------------------------- briefing ------------------------------------
def _briefing_context() -> dict:
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    acts = store.fetch_all(
        "SELECT channel, type, COUNT(*) n FROM activities WHERE substr(ts,1,10)=? "
        "GROUP BY channel, type", (yesterday,))
    replies = store.list_tasks("open")
    pend = store.fetch_one(
        "SELECT COUNT(*) n FROM approval_queue WHERE status='pending'")["n"]
    alerts = []
    g = ingest.gmail_status()
    if g["last_error"]:
        alerts.append(f"Gmail replies: {g['last_error']}")
    # Los asientos primero: si los tres VP siguen sin sesión, todo lo demás que
    # diga el briefing sobre LinkedIn es ruido.
    alerts.extend(seats.preflight())
    if not TRACKING_DB.exists():
        alerts.append("Base de tracking (mundo_orquestador.db) no disponible — "
                       "el webhook de SendGrid no está guardando opens/clicks/bounces")
    rate_limited = store.fetch_one(
        "SELECT COUNT(*) n FROM activities WHERE json_extract(meta_json,'$.raw_status')"
        "='rate_limited' AND ts >= ?", (yesterday,))
    if rate_limited and rate_limited["n"]:
        alerts.append("LinkedIn devolvió rate_limited — revisar cookies/límites")
    fallback = store.fetch_one(
        "SELECT COUNT(*) n FROM approval_queue WHERE model='fallback' AND created_at >= ?",
        (yesterday,))
    if fallback and fallback["n"]:
        alerts.append(f"{fallback['n']} gancho(s) usaron la frase de respaldo en vez "
                       "de Claude — revisar API key/cuota de Anthropic")
    bloqueados = store.fetch_one(
        "SELECT COUNT(*) n FROM approval_queue WHERE status='blocked'")["n"]
    if bloqueados:
        alerts.append(f"{bloqueados} borrador(es) frenados por la compuerta — "
                       "no se pueden enviar hasta corregirlos")
    # Estado de cada campaña con evento: lo que falta y cuánto queda.
    camps = []
    for c in campaigns.list_all():
        camp = campaigns.get(c["slug"])
        if camp:
            pf = campaigns.preflight(camp)
            camps.append({"campaña": camp.name, "dias_para_el_evento": pf["days_to_event"],
                          "listo": pf["ok"], "falta": pf["problems"]})
            for p in pf["problems"]:
                alerts.append(f"{camp.name}: {p}")
    res_seats = seats.resumen()
    return {
        "ayer": {f"{r['channel']}.{r['type']}": r["n"] for r in acts},
        "aprobaciones_pendientes": pend,
        "asientos_linkedin": [
            {"perfil": b["nombre"], "sesion": "viva" if b["listo"] else b["sesion"]["detalle"],
             "invitaciones_hoy": f"{b['invites']['used']}/{b['invites']['cap']}",
             "mensajes_hoy": f"{b['mensajes']['used']}/{b['mensajes']['cap']}",
             "contactos_asignados": b["asignados"]}
            for b in res_seats["seats"]],
        "borradores_bloqueados": bloqueados,
        "campañas": camps,
        "tareas_abiertas": [
            {"tipo": t["type"], "contacto": t.get("nombre", ""),
             "empresa": t.get("empresa", ""), "vence": t["due_at"],
             "intent": (t.get("meta") or {}).get("classification", {}).get("intent", "")}
            for t in replies[:15]],
        "presupuestos": budgets(),
    }


def maybe_briefing(force: bool = False) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and store.kv_get("briefing_date") == today:
        return {"date": store.kv_get("briefing_date"),
                "text": store.kv_get("briefing_text")}
    ctx = _briefing_context()
    text = ai.daily_briefing(ctx)
    if text or force:
        store.kv_set("briefing_date", today)
        store.kv_set("briefing_text", text)
    return {"date": today, "text": text, "context": ctx}


# ----------------------------- tick ----------------------------------------
def tick(force: bool = False) -> dict:
    """Un ciclo completo del copiloto. `force` ignora horario/enabled (botón
    manual en la UI)."""
    result: dict = {"ts": store.now_iso()}
    store.kv_set("last_tick", result["ts"])
    try:
        result["sync"] = sync.run_full_sync()
    except Exception as e:
        result["sync"] = {"error": str(e)}
    try:
        # recupera opens/clicks que llegaron por webhook antes de que
        # store.db supiera del contacto (ver webhooks.backfill_missing) —
        # va después de sync() para que los contactos ya estén al día.
        result["webhook_backfill"] = webhooks.backfill_missing()
    except Exception as e:
        result["webhook_backfill"] = {"error": str(e)}
    try:
        result["ingest"] = ingest.run()
    except Exception as e:
        result["ingest"] = {"error": str(e)}
    try:
        result["reconcile"] = reconcile_sent()
    except Exception as e:
        result["reconcile"] = {"error": str(e)}
    try:
        # Lo que la nube dejó en la bandeja entra a la base ANTES de la vía
        # rápida, para que salga invitado en esta misma vuelta. El bucle de 60 s
        # del cockpit ya lo hace; esto cubre un cockpit que arrancó sin él.
        result["nube"] = nube.sincronizar()
    except Exception as e:
        result["nube"] = {"error": str(e)}
    try:
        # Vía rápida: los prospectos que entran por Platzi Ventas. Va ANTES de
        # la corrida de la mañana y ese orden es lo que le da la prioridad — si
        # el backlog se lleva el cupo del día primero, la cuenta que el vendedor
        # está trabajando HOY sale mañana. Tiene su propia compuerta e
        # interruptor, así que se puede llamar en cada tick sin miedo.
        result["via_rapida"] = conexiones.correr_via_rapida()
    except Exception as e:
        result["via_rapida"] = {"error": str(e)}
    try:
        # Envío de conexiones de la mañana. Tiene su propia compuerta (una vez
        # al día, día hábil, 8–11) y su propio interruptor, así que se puede
        # llamar en cada tick sin miedo. No pasa por la cola de aprobación
        # porque una conexión sin nota no lleva texto que revisar.
        #
        # Corre después de la vía rápida y en serie con ella a propósito:
        # `correr()` espera a que sus jobs terminen e ingiere las actividades,
        # así que el cupo que ve aquí ya descuenta lo que acabó de salir.
        result["envio_diario"] = conexiones.correr_si_toca()
    except Exception as e:
        result["envio_diario"] = {"error": str(e)}
    try:
        # Aceptaciones y respuestas de LinkedIn, una vez por hora en horario
        # laboral. Es lo que mueve el contacto a «conectado» y lo que pausa la
        # secuencia cuando alguien contesta: sin esto el circuito solo ve el envío.
        result["senales_li"] = senales_li.correr_si_toca()
    except Exception as e:
        result["senales_li"] = {"error": str(e)}
    try:
        # Marcar en HubSpot quién aceptó la invitación. Va después de
        # `senales_li`, que es quien detecta la aceptación: así el positivo
        # aparece en el CRM en la misma vuelta en que se supo.
        #
        # Se salta solo y sin ruido si la propiedad todavía no existe: crearla
        # necesita un scope que el token no tiene, y un tick que falla cada 15
        # minutos por eso sería ruido, no información.
        if hubspot_conexion.existe_propiedad()[0]:
            result["hubspot_conexion"] = hubspot_conexion.marcar()
        else:
            result["hubspot_conexion"] = {"saltado": "la propiedad no existe"}
    except Exception as e:
        result["hubspot_conexion"] = {"error": str(e)}
    try:
        # Inscripción en la secuencia de Amplemarket del VP que firma. Va
        # después de las señales de LinkedIn a propósito: si alguien acabó de
        # aceptar, entra a la secuencia en esta misma vuelta.
        result["secuencias_am"] = secuencias_am.correr_si_toca()
    except Exception as e:
        result["secuencias_am"] = {"error": str(e)}
    try:
        # El otro carril de correo: secuencia de HubSpot enviada desde la bandeja
        # del propio vendedor. Va aquí por la misma razón que el de Amplemarket
        # —después de las señales de LinkedIn— y los dos son excluyentes por
        # asiento, así que nadie recibe los dos.
        result["secuencias_hs"] = secuencias_hs.correr_si_toca()
    except Exception as e:
        result["secuencias_hs"] = {"error": str(e)}
    cfg = cadence_settings()
    if force or (cfg["enabled"] and in_business_hours()):
        try:
            result["click_calls"] = call_tasks_from_clicks()
        except Exception as e:
            result["click_calls"] = {"error": str(e)}
        try:
            result["drafts"] = generate_drafts()
        except Exception as e:
            result["drafts"] = {"error": str(e)}
        if datetime.now().hour >= 7:
            try:
                result["briefing"] = {"date": maybe_briefing().get("date")}
            except Exception as e:
                result["briefing"] = {"error": str(e)}
    else:
        result["drafts"] = {"skipped": "fuera de horario o cadencia desactivada"}
    store.kv_set("last_tick_result", json.dumps(result, ensure_ascii=False, default=str))
    return result


async def loop() -> None:
    await asyncio.sleep(30)  # deja arrancar el server
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            pass
        await asyncio.sleep(TICK_MINUTES * 60)
