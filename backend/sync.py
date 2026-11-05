"""Sincroniza el almacén SQLite (store.py) con las fuentes existentes.

- Contactos: BASE_SEGMENTADA.csv (vía contacts.py) → upsert por csv_key.
- Actividades históricas: JSONL de bulk_connect/results (LinkedIn + email),
  webhook_events (SendGrid), call_log.json y connection_status.json.

Idempotente: cada actividad lleva ext_id único; correrlo N veces no duplica.
"""
from __future__ import annotations

import json
from pathlib import Path

import calls_store
import contacts as contacts_mod
import store
from contacts import _norm_li
from db import fetch_all as tracking_fetch_all, table_exists
from runs import all_email_events, all_linkedin_events

DATA_DIR = Path(__file__).resolve().parent / "data"
CONNECTION_STATUS = DATA_DIR / "connection_status.json"

# status de los runners → tipo de actividad del store
_LI_TYPE = {
    "sent": "invite_sent",
    "pending_invite": "invite_sent",
    "already": "accepted",       # ya estaba conectado = aceptación implícita
    "connected": "accepted",
    "message_sent": "followup_sent",
}
_WEBHOOK_TYPE = {
    "open": "open", "opened": "open",
    "click": "click", "clicked": "click",
    "bounce": "bounce", "bounced": "bounce", "dropped": "bounce",
    "spamreport": "spam", "spam": "spam", "complained": "spam",
}


def _seat_por_run() -> dict[str, str]:
    """run_id → asiento que lo envió, leído de los parámetros del job.

    Sin esto, una actividad de LinkedIn no dice de qué perfil salió: se podía
    inferir por `contacts.li_seat`, pero eso es el asiento de HOY. Si el
    contacto se reasigna (o el asiento se apaga), la historia quedaría contada
    como si la hubiera enviado alguien que nunca la envió.
    """
    fuera: dict[str, str] = {}
    for r in store.fetch_all(
            "SELECT run_id, params_json FROM jobs_log WHERE params_json LIKE '%seat%'"):
        try:
            seat = (json.loads(r["params_json"] or "{}") or {}).get("seat", "")
        except json.JSONDecodeError:
            seat = ""
        if seat:
            fuera[r["run_id"]] = seat
    return fuera


def sync_contacts() -> dict:
    """CSV → contacts (upsert). Devuelve conteos."""
    contacts_mod.clear_cache()
    added = 0
    for c in contacts_mod._load_base():
        key = calls_store.call_key(c["email"], c["linkedin"], c["nombre"], c["empresa"])
        before = store.fetch_one("SELECT id FROM contacts WHERE csv_key=?", (key,))
        store.upsert_contact(key, c, source="csv")
        if not before:
            added += 1
    total = store.fetch_one("SELECT COUNT(*) n FROM contacts")["n"]
    return {"csv_rows": len(contacts_mod._load_base()), "added": added, "total": total}


def _contact_id_by_email(email: str, cache: dict) -> int | None:
    e = (email or "").strip().lower()
    if not e:
        return None
    if e not in cache:
        row = store.find_contact(email=e)
        cache[e] = row["id"] if row else None
    return cache[e]


def _contact_id_by_slug(slug: str, cache: dict) -> int | None:
    if not slug:
        return None
    if slug not in cache:
        row = store.find_contact(linkedin_slug=slug)
        cache[slug] = row["id"] if row else None
    return cache[slug]


def backfill_activities() -> dict:
    """Vuelca el histórico de todas las fuentes a activities (idempotente)."""
    by_email: dict = {}
    by_slug: dict = {}
    counts = {"linkedin": 0, "email": 0, "webhook": 0, "calls": 0, "accepts": 0}

    # 1) Eventos LinkedIn (conexiones + follow-ups) desde JSONL.
    seat_de = _seat_por_run()
    for e in all_linkedin_events():
        type_ = _LI_TYPE.get(e.get("status", ""))
        if not type_:
            continue
        slug = _norm_li(e.get("url", ""))
        cid = _contact_id_by_slug(slug, by_slug) or _contact_id_by_email(e.get("email", ""), by_email)
        if not cid:
            continue
        ext = f"jsonl:{e.get('run_id','')}:{e.get('channel','')}:{e.get('idx','')}"
        if store.add_activity(cid, "linkedin", type_, e.get("ts", ""), e.get("run_id", ""),
                              ext_id=ext, meta={"detail": e.get("detail", ""),
                                                "raw_status": e.get("status", ""),
                                                "seat": seat_de.get(e.get("run_id", ""), "")}):
            counts["linkedin"] += 1

    # 2) Envíos de email desde JSONL (solo envíos reales).
    for e in all_email_events():
        if e.get("status") != "sent":
            continue
        cid = _contact_id_by_email(e.get("email", ""), by_email)
        if not cid:
            continue
        ext = f"jsonl:{e.get('run_id','')}:mail:{e.get('idx','')}"
        if store.add_activity(cid, "email", "sent", e.get("ts", ""), e.get("run_id", ""),
                              ext_id=ext, meta={"detail": e.get("detail", "")}):
            counts["email"] += 1

    # 3) Webhook SendGrid (opens/clicks/bounces/spam) desde mundo_orquestador.db.
    if table_exists("webhook_events"):
        from webhooks import _to_local
        rows = tracking_fetch_all(
            "SELECT id, event_type, contact_id, timestamp, data_json FROM webhook_events")
        for r in rows:
            type_ = _WEBHOOK_TYPE.get((r.get("event_type") or "").lower())
            if not type_:
                continue
            try:
                data = json.loads(r.get("data_json") or "{}")
            except json.JSONDecodeError:
                data = {}
            addr = data.get("email") or r.get("contact_id", "")
            cid = _contact_id_by_email(addr, by_email)
            if not cid:
                continue
            if store.add_activity(cid, "email", type_, _to_local(r.get("timestamp", "")),
                                  data.get("run_id", ""), ext_id=f"sg:{r['id']}",
                                  meta={"url": data.get("url", "")}):
                counts["webhook"] += 1

    # 4) Llamadas registradas a mano.
    for key, call in calls_store.load("leads").items():
        if not call.get("called"):
            continue
        row = store.fetch_one("SELECT id FROM contacts WHERE csv_key=?", (key,))
        cid = row["id"] if row else _contact_id_by_email(key, by_email)
        if not cid:
            continue
        ts = call.get("updated_at", "")
        if store.add_activity(cid, "call", "call", ts, ext_id=f"call:{key}:{ts}",
                              meta={"outcome": call.get("outcome", ""),
                                    "notes": call.get("notes", "")}):
            counts["calls"] += 1

    # 5) Aceptaciones detectadas por accept_checker.
    if CONNECTION_STATUS.exists():
        try:
            data = json.loads(CONNECTION_STATUS.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        for slug, st in data.items():
            if st.get("status") != "accepted":
                continue
            cid = _contact_id_by_slug(slug, by_slug)
            if not cid:
                continue
            ts = st.get("ts", "")
            if store.add_activity(cid, "linkedin", "accepted", ts,
                                  ext_id=f"accept:{slug}:{ts}"):
                counts["accepts"] += 1

    return counts


def run_full_sync() -> dict:
    contacts_result = sync_contacts()
    activities_result = backfill_activities()
    return {"ok": True, "contacts": contacts_result, "activities": activities_result}
