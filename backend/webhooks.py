"""Receptor del Event Webhook de SendGrid -> tabla webhook_events.

SendGrid envía un array de eventos. Cada uno trae sg_event_id, event,
timestamp y los custom_args que inyectamos al enviar (contact_id, campaign_id,
run_id). Guardamos el payload crudo en data_json para no perder nada.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import sendgrid_stats
import store
from config import TRACKING_DB
from db import connect, fetch_all, table_exists

# event de SendGrid → tipo de actividad del store
_ACTIVITY_TYPE = {
    "processed": "delivered", "delivered": "delivered",
    "open": "open", "opened": "open",
    "click": "click", "clicked": "click",
    "bounce": "bounce", "bounced": "bounce", "dropped": "bounce",
    "spamreport": "spam", "spam": "spam", "complained": "spam",
}


def _to_local(ts_iso: str) -> str:
    """ISO UTC (webhook) → hora local, consistente con el resto del store."""
    try:
        return datetime.fromisoformat(ts_iso).astimezone().replace(tzinfo=None) \
            .isoformat(timespec="seconds")
    except ValueError:
        return ts_iso


def _is_cockpit_event(ev: dict) -> bool:
    """¿Este evento pertenece a la categoría del cockpit? El webhook es a
    nivel de CUENTA SendGrid: también llegan notificaciones de otras apps de
    otras apps que comparten la cuenta (calendario, desconexiones,
    liquidaciones). Se verifica en la escritura (no solo confiar en que el
    email coincida con un contacto) para que `activities` nunca dependa de
    que esas otras apps jamás compartan una dirección con la base."""
    cats = ev.get("category")
    cats = cats if isinstance(cats, list) else ([cats] if cats else [])
    return sendgrid_stats.CATEGORY in cats


def _mirror_to_store(eid: str, event_type: str, contact_ref: str, ts: str,
                     ev: dict) -> bool:
    """Refleja el evento en activities y pausa la secuencia si es bounce.

    Solo eventos de la categoría del cockpit que además matchean un contacto
    de la base (el webhook es a nivel de cuenta SendGrid). Devuelve True si
    se agregó una actividad nueva (idempotente por ext_id vía
    store.add_activity — reprocesar el mismo evento no duplica nada, así que
    es seguro llamar esta función más de una vez sobre el mismo evento, algo
    que backfill_missing() aprovecha)."""
    type_ = _ACTIVITY_TYPE.get(event_type)
    if not type_ or not _is_cockpit_event(ev):
        return False
    email = (ev.get("email") or contact_ref or "").strip().lower()
    contact = store.find_contact(email=email)
    if not contact:
        return False
    added = store.add_activity(contact["id"], "email", type_, _to_local(ts),
                               run_id=ev.get("run_id", ""), ext_id=f"sg:{eid}",
                               meta={"url": ev.get("url", "")})
    if added and type_ == "bounce":
        store.pause_sequences_for(contact["id"], status="paused")
    return added


def _ensure_table() -> None:
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS webhook_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                step_id TEXT,
                contact_id TEXT,
                timestamp TEXT NOT NULL,
                data_json TEXT DEFAULT '{}',
                processed BOOLEAN DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        conn.commit()


def ingest(events: list[dict]) -> dict:
    """Devuelve {received, ok, error, mirror_errors}. `ok=False` cuando la
    base de tracking no está disponible — antes se devolvía silenciosamente
    `0`, indistinguible de "SendGrid no mandó eventos": el caller respondía
    200 igual, SendGrid nunca reintentaba, y los eventos de ese período se
    perdían para siempre sin ninguna alerta."""
    if not TRACKING_DB.exists():
        return {"received": 0, "ok": False,
                "error": f"TRACKING_DB no existe: {TRACKING_DB}"}
    _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    mirror_errors = 0
    with connect() as conn:
        for ev in events:
            event_type = (ev.get("event") or "").lower()
            if not event_type:
                continue
            eid = ev.get("sg_event_id") or str(uuid.uuid4())
            ts_raw = ev.get("timestamp")
            try:
                ts = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat()
            except (TypeError, ValueError):
                ts = now
            contact_id = ev.get("contact_id") or ev.get("email") or ""
            step_id = ev.get("campaign_id") or ev.get("run_id") or ""
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO webhook_events "
                    "(id, event_type, step_id, contact_id, timestamp, data_json, processed, created_at) "
                    "VALUES (?,?,?,?,?,?,0,?)",
                    (eid, event_type, step_id, contact_id, ts,
                     json.dumps(ev, ensure_ascii=False), now),
                )
                n += 1
            except Exception:
                continue
            try:
                _mirror_to_store(eid, event_type, contact_id, ts, ev)
            except Exception:
                # el evento SÍ quedó en webhook_events (línea arriba); lo que
                # falló es reflejarlo en activities/pausar la secuencia si es
                # bounce. Antes esto se tragaba sin dejar rastro.
                mirror_errors += 1
        conn.commit()
    return {"received": n, "ok": True, "error": "", "mirror_errors": mirror_errors}


def backfill_missing() -> dict:
    """Reintenta reflejar en `activities` eventos ya guardados en
    webhook_events que no matchearon un contacto la primera vez.

    Causa real medida: los eventos de apertura/click llegan en segundos,
    pero store.db solo sincroniza contactos nuevos cada 15 min (el tick del
    scheduler) — un contacto recién prospectado podía recibir su primer
    "open" ANTES de que store.db supiera que existía, y ese evento se perdía
    para siempre (se verificó: 199 opens reales en el webhook, solo 49
    reflejados). webhook_events es el buffer crudo permanente — nunca se
    borra — así que reprocesarlo aquí recupera lo que se perdió, y es seguro
    llamarlo en cada ciclo (idempotente por ext_id, ver _mirror_to_store)."""
    if not TRACKING_DB.exists():
        return {"ok": False, "error": f"TRACKING_DB no existe: {TRACKING_DB}"}
    if not table_exists("webhook_events"):
        return {"ok": True, "scanned": 0, "added": 0}
    rows = fetch_all(
        "SELECT id, event_type, contact_id, timestamp, data_json FROM webhook_events"
    )
    added = 0
    for r in rows:
        try:
            ev = json.loads(r.get("data_json") or "{}")
        except json.JSONDecodeError:
            continue
        try:
            if _mirror_to_store(r["id"], r["event_type"], r["contact_id"],
                                r["timestamp"], ev):
                added += 1
        except Exception:
            continue
    return {"ok": True, "scanned": len(rows), "added": added}
