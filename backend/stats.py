"""Agrega KPIs de los tres canales para el Dashboard y Analytics."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache

from pathlib import Path

import sendgrid_stats
import store
from config import TEMPLATES_DIR
from runs import all_email_events, all_linkedin_events

_EMAIL_OVERRIDE = Path(__file__).resolve().parent / "data" / "email_stats_override.json"


def _email_override() -> dict | None:
    """Override manual de métricas — DESACTIVADO por defecto.

    Pisaba los valores reales con números fijos, lo que envenenaría cualquier
    decisión del copiloto. Solo se respeta si PLATZI_ALLOW_STATS_OVERRIDE=1.
    """
    import os
    if os.environ.get("PLATZI_ALLOW_STATS_OVERRIDE") != "1":
        return None
    if not _EMAIL_OVERRIDE.exists():
        return None
    try:
        return json.loads(_EMAIL_OVERRIDE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

# type (store.db activities) -> nombre de campo que expone esta API.
_TYPE_FIELD = {"delivered": "delivered", "open": "opened", "click": "clicked",
               "bounce": "bounced", "spam": "spam"}


@lru_cache(maxsize=1)
def _cta_urls() -> set[str]:
    urls: set[str] = set()
    if not TEMPLATES_DIR.exists():
        return urls
    for f in TEMPLATES_DIR.glob("*.json"):
        try:
            tpl = json.loads(f.read_text(encoding="utf-8"))
            cta = tpl.get("cta") or {}
            if cta.get("url"):
                urls.add(cta["url"].strip().lower())
        except (json.JSONDecodeError, OSError):
            continue
    return urls


def _is_cta(url: str) -> bool:
    if not url:
        return False
    u = url.strip().lower()
    return any(c in u or u in c for c in _cta_urls())


def _email_activity_rows() -> list[dict]:
    """Actividades de email desde store.db (contacts JOIN activities).

    Antes esta función leía `webhook_events` crudo directamente — la misma
    tabla en la que ATERRIZA cualquier evento de la cuenta de SendGrid,
    incluidas notificaciones de OTRAS apps que comparten la
    cuenta (calendario, desconexiones, liquidaciones). Eso obligaba a un
    parche de categoría para no contaminarse (se verificó con datos reales:
    delivered 640→270, clicked 130→61 una vez filtrado). `activities` no
    tiene ese problema por construcción: `webhooks._mirror_to_store` solo
    escribe ahí cuando el evento matchea un contacto real de la base — así
    que leer de aquí es la fuente correcta, no un parche.
    """
    return store.fetch_all(
        "SELECT a.type, c.email AS contact_id, a.ts, a.meta_json "
        "FROM activities a JOIN contacts c ON c.id = a.contact_id "
        "WHERE a.channel='email'"
    )


def email_metrics() -> dict:
    sent_events = [e for e in all_email_events() if e.get("status") == "sent"]
    sent = len(sent_events)

    by_type: Counter = Counter()
    opened_contacts: set[str] = set()
    clicked_contacts: set[str] = set()
    cta_clicks = 0
    per_contact: dict[str, dict] = {}
    rows = _email_activity_rows()

    for r in rows:
        kind = _TYPE_FIELD.get(r.get("type") or "")
        if not kind:
            continue
        by_type[kind] += 1
        cid = r.get("contact_id") or ""
        try:
            data = json.loads(r.get("meta_json") or "{}")
        except json.JSONDecodeError:
            data = {}
        if kind == "opened" and cid:
            opened_contacts.add(cid)
        if kind == "clicked":
            if cid:
                clicked_contacts.add(cid)
            if _is_cta(data.get("url", "")):
                cta_clicks += 1
        slot = per_contact.setdefault(cid, {"contact_id": cid, "events": []})
        slot["events"].append({"type": kind, "ts": r.get("ts"),
                               "url": data.get("url", "")})

    delivered = by_type.get("delivered", 0) or sent
    denom = delivered or sent or 1
    result = {
        "sent": sent,
        "delivered": by_type.get("delivered", 0),
        "opened": by_type.get("opened", 0),
        "opened_unique": len(opened_contacts),
        "clicked": by_type.get("clicked", 0),
        "clicked_unique": len(clicked_contacts),
        "cta_clicks": cta_clicks,
        "bounced": by_type.get("bounced", 0),
        "spam": by_type.get("spam", 0),
        "open_rate": round(len(opened_contacts) / denom * 100, 1),
        "click_rate": round(cta_clicks / denom * 100, 1),
        "bounce_rate": round(by_type.get("bounced", 0) / (sent or 1) * 100, 1),
        "spam_rate": round(by_type.get("spam", 0) / (sent or 1) * 100, 1),
        "funnel": [
            {"stage": "Enviados", "value": sent},
            {"stage": "Entregados", "value": by_type.get("delivered", 0) or sent},
            {"stage": "Abiertos", "value": len(opened_contacts)},
            {"stage": "Clicks CTA", "value": cta_clicks},
        ],
        "per_contact": list(per_contact.values()),
        "webhook_active": bool(rows),
        # Números reales que reporta SendGrid para la categoría platzi-cockpit
        # (no dependen del webhook/ngrok). El Dashboard los prefiere.
        "sendgrid": sendgrid_stats.category_stats(),
    }
    override = _email_override()
    if override:
        for k, v in override.items():
            result[k] = v
    return result


def email_engagement() -> dict:
    """Engagement de email POR CONTACTO desde el store unificado.

    Opens/clicks vienen del Event Webhook (en vivo cuando está registrado);
    bounces/spam vienen además de la suppression API de SendGrid (siempre
    disponibles). Ordena: primero quien hizo click en el CTA, luego quien
    hizo click en cualquier link, luego quien abrió.

    `clicks` cuenta CUALQUIER link (el CTA, pero también links de scanners de
    seguridad corporativos que "detonan" todo lo que trae el correo — ver
    hallazgo 2026-07-08). `cta_clicks` filtra solo los clicks al link del CTA
    de la plantilla (`_is_cta`) — la señal que de verdad indica interés real
    del prospecto en agendar/contactar, no ruido de un escáner.
    """
    rows = store.fetch_all(
        "SELECT c.id, c.nombre, c.empresa, c.cargo, c.email, c.stage, "
        " SUM(a.type='sent')  AS sends,  MAX(CASE WHEN a.type='sent'  THEN a.ts END) AS last_sent, "
        " SUM(a.type='open')  AS opens,  MAX(CASE WHEN a.type='open'  THEN a.ts END) AS last_open, "
        " SUM(a.type='click') AS clicks, MAX(CASE WHEN a.type='click' THEN a.ts END) AS last_click, "
        " SUM(a.type='reply') AS replies, "
        " SUM(a.type='bounce') AS bounces, "
        " SUM(a.type='spam')   AS spam, "
        " MAX(CASE WHEN a.type='bounce' THEN a.meta_json END) AS bounce_meta "
        "FROM contacts c JOIN activities a ON a.contact_id=c.id AND a.channel='email' "
        "GROUP BY c.id "
        "ORDER BY clicks DESC, opens DESC, replies DESC, last_sent DESC")

    # clicks en el CTA específicamente, por contacto (mismo criterio que ya
    # usa email_metrics() para el funnel — _is_cta contra las plantillas).
    click_rows = store.fetch_all(
        "SELECT contact_id, meta_json FROM activities WHERE channel='email' AND type='click'")
    cta_by_contact: dict[int, int] = {}
    for r in click_rows:
        try:
            url = json.loads(r.get("meta_json") or "{}").get("url", "")
        except json.JSONDecodeError:
            url = ""
        if _is_cta(url):
            cta_by_contact[r["contact_id"]] = cta_by_contact.get(r["contact_id"], 0) + 1

    for r in rows:
        try:
            r["bounce_reason"] = json.loads(r.pop("bounce_meta") or "{}").get("reason", "")
        except json.JSONDecodeError:
            r["bounce_reason"] = ""
        r["cta_clicks"] = cta_by_contact.get(r["id"], 0)
    rows.sort(key=lambda r: (r["cta_clicks"], r["clicks"], r["opens"], r["replies"]), reverse=True)
    return {
        "webhook_active": bool(rows),
        "total": len(rows),
        "clickers": sum(1 for r in rows if r["clicks"]),
        "cta_clickers": sum(1 for r in rows if r["cta_clicks"]),
        "openers": sum(1 for r in rows if r["opens"]),
        "bounced": sum(1 for r in rows if r["bounces"]),
        "rows": rows,
    }


def linkedin_metrics() -> dict:
    conn = Counter()
    follow = Counter()
    for e in all_linkedin_events():
        if e.get("channel") == "followup":
            follow[e.get("status", "")] += 1
        else:
            conn[e.get("status", "")] += 1
    return {
        "connections": {
            "sent": conn.get("sent", 0),
            "already": conn.get("already", 0),
            "pending": conn.get("pending_invite", 0),
            "by_status": dict(conn),
            "total": sum(conn.values()),
        },
        "followups": {
            "message_sent": follow.get("message_sent", 0),
            "by_status": dict(follow),
            "total": sum(follow.values()),
        },
    }


def timeseries() -> list[dict]:
    """Envíos por día y canal."""
    day: dict[str, dict] = defaultdict(lambda: {"linkedin": 0, "followup": 0, "email": 0})
    for e in all_linkedin_events():
        d = (e.get("ts") or "")[:10]
        if not d:
            continue
        ch = e.get("channel", "linkedin")
        if e.get("status") in ("sent", "message_sent"):
            day[d][ch] += 1
    for e in all_email_events():
        d = (e.get("ts") or "")[:10]
        if d and e.get("status") == "sent":
            day[d]["email"] += 1
    return [{"date": k, **v} for k, v in sorted(day.items())]


def overview() -> dict:
    li = linkedin_metrics()
    em = email_metrics()
    ts = timeseries()
    # Serie de email desde SendGrid (categoría platzi-cockpit): reemplaza los
    # conteos locales por los requests reales que reporta SendGrid por día.
    sg = em.get("sendgrid") or {}
    if sg.get("ok"):
        merged: dict[str, dict] = {r["date"]: {"date": r["date"],
                                               "linkedin": r["linkedin"],
                                               "followup": r["followup"],
                                               "email": 0} for r in ts}
        for day in sg["daily"]:
            row = merged.setdefault(day["date"], {"date": day["date"],
                                                  "linkedin": 0, "followup": 0,
                                                  "email": 0})
            row["email"] = day.get("requests", 0)
        ts = [merged[k] for k in sorted(merged)]
    return {
        "linkedin": li,
        "email": {k: v for k, v in em.items() if k != "per_contact"},
        "timeseries": ts,
    }
