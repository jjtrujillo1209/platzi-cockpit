"""Ingesta de señales entrantes hacia el store unificado.

- Respuestas de email: usa bulk_connect/email_tracking (Gmail readonly). El
  checker escribe replies_db.json; aquí lo volcamos a activities, pausamos la
  secuencia del contacto, clasificamos la intención con Claude y creamos una
  tarea de revisión.
- Aceptaciones de LinkedIn y eventos nuevos de runners/webhook: reutiliza el
  backfill idempotente de sync.py.

Todo es idempotente: correrlo cada 15 min no duplica nada.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import ai
import store
import sync
from config import BULK_CONNECT_DIR

# email_tracking vive en bulk_connect/ (paquete con __init__.py)
if str(BULK_CONNECT_DIR) not in sys.path:
    sys.path.insert(0, str(BULK_CONNECT_DIR))

GMAIL_CRED_DIR = BULK_CONNECT_DIR / "email_tracking" / "credentials"
GMAIL_CLIENT_SECRET = GMAIL_CRED_DIR / "client_secret.json"
GMAIL_TOKEN = GMAIL_CRED_DIR / "token.json"
REPLIES_DB = BULK_CONNECT_DIR / "email_tracking" / "data" / "replies_db.json"


def gmail_status() -> dict:
    """Estado de la detección de respuestas. `state` distingue "aún no
    configurado" (neutral) de un error real, para no mostrar alertas falsas."""
    has_secret = GMAIL_CLIENT_SECRET.exists()
    has_token = GMAIL_TOKEN.exists()
    err = store.kv_get("gmail_last_error", "")
    if not has_secret:
        state = "not_configured"
    elif not has_token:
        state = "needs_connect"
    elif err:
        state = "error"
    else:
        state = "connected"
    return {
        "state": state,
        "client_secret_exists": has_secret,
        "token_exists": has_token,
        "connected_email": store.kv_get("gmail_email", ""),
        "replies_db_exists": REPLIES_DB.exists(),
        "last_error": err if state == "error" else "",
        "last_check": store.kv_get("gmail_last_check", ""),
    }


def save_client_secret(content: str) -> dict:
    """Guarda el client_secret.json (validado) que el usuario descarga de
    Google Cloud Console."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"ok": False, "error": "El contenido no es JSON válido"}
    node = data.get("installed") or data.get("web")
    if not node or not node.get("client_id"):
        return {"ok": False, "error": "No parece un OAuth client de Google. "
                "Debe ser el JSON de tipo 'Desktop app' (con clave 'installed')."}
    GMAIL_CRED_DIR.mkdir(parents=True, exist_ok=True)
    GMAIL_CLIENT_SECRET.write_text(json.dumps(data, indent=2), encoding="utf-8")
    store.kv_set("gmail_last_error", "")
    cid = node["client_id"]
    return {"ok": True, "client_id": cid[:24] + "…" if len(cid) > 24 else cid,
            "type": "installed" if "installed" in data else "web"}


def connect_gmail() -> dict:
    """Corre el flujo OAuth (abre el navegador en este Mac) y guarda el token.
    Bloquea hasta que el usuario autoriza en el navegador."""
    if not GMAIL_CLIENT_SECRET.exists():
        return {"ok": False, "error": "Primero sube el client_secret.json"}
    try:
        from email_tracking.gmail_client import GmailClient
        gc = GmailClient()
        profile = gc.service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "")
        store.kv_set("gmail_email", email)
        store.kv_set("gmail_last_error", "")
        return {"ok": True, "email": email,
                "messages_total": profile.get("messagesTotal")}
    except Exception as e:
        store.kv_set("gmail_last_error", f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


def check_gmail(since_days: int = 21) -> dict:
    """Corre el reply-checker de Gmail. Si aún no está conectado, lo salta en
    silencio (no es un error). Nunca lanza."""
    if not GMAIL_CLIENT_SECRET.exists() or not GMAIL_TOKEN.exists():
        return {"ok": False, "skipped": "gmail no conectado"}
    try:
        from email_tracking.replies_checker import check_and_update
        result = check_and_update(since_days=since_days)
        store.kv_set("gmail_last_check", store.now_iso())
        store.kv_set("gmail_last_error", "")
        return {"ok": True, **result}
    except Exception as e:
        store.kv_set("gmail_last_error", f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


def _domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else ""


def ingest_replies(classify: bool = True) -> dict:
    """Vuelca replies_db.json a activities + pausa secuencias + crea tareas."""
    if not REPLIES_DB.exists():
        return {"new": 0}
    try:
        db = json.loads(REPLIES_DB.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"new": 0, "error": "replies_db ilegible"}

    new = 0
    for mid, r in db.items():
        ext_id = f"gmail:{mid}"
        if store.fetch_one("SELECT id FROM activities WHERE ext_id=?", (ext_id,)):
            continue
        from_email = (r.get("from_email") or "").strip().lower()
        contact = store.find_contact(email=from_email) \
            or store.find_contact_by_domain(_domain(from_email))
        if not contact:
            continue
        cid = contact["id"]
        meta = {"from": from_email, "subject": r.get("subject", ""),
                "snippet": r.get("snippet", ""), "match_kind": r.get("match_kind", "")}

        if classify:
            cls = ai.classify_reply(contact, r.get("subject", ""), r.get("snippet", ""))
            meta["intent"] = cls.get("intent", "otro")
        else:
            cls = None

        if not store.add_activity(cid, "email", "reply", r.get("date_iso", ""),
                                  ext_id=ext_id, meta=meta):
            continue
        new += 1

        paused = store.pause_sequences_for(cid, status="replied")
        # OOO no debe frenar la secuencia: solo posponer una semana.
        if cls and cls.get("intent") == "fuera_oficina" and paused:
            for cs in store.fetch_all(
                    "SELECT id FROM contact_sequences WHERE contact_id=? AND status='replied'",
                    (cid,)):
                store.set_sequence_status(cs["id"], "active",
                                          _in_days(7))
        elif not store.open_task_exists(cid, "reply_review"):
            store.add_task(cid, "reply_review", store.now_iso(),
                           notes=f"Respondió: {r.get('subject', '')}",
                           meta={"reply": meta, "classification": cls or {}})
    return {"new": new, "total_db": len(db)}


def _in_days(days: int) -> str:
    from datetime import timedelta
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


def ingest_suppressions() -> dict:
    """Bounces/blocks/spam de SendGrid (por destinatario) → activities.

    Un bounce o spam pausa la secuencia del contacto: no tiene sentido seguir
    escribiéndole a un correo que rebota.
    """
    import sendgrid_stats
    data = sendgrid_stats.suppressions()
    if not data.get("ok") and not any(data.get(k) for k in ("bounces", "blocks", "spam_reports")):
        return {"new": 0, "error": data.get("error", "")}
    new = 0
    for kind, type_ in (("bounces", "bounce"), ("blocks", "bounce"),
                        ("spam_reports", "spam")):
        for s in data.get(kind, []):
            contact = store.find_contact(email=s["email"])
            if not contact:
                continue
            ts = datetime.fromtimestamp(s["created"]).isoformat(timespec="seconds") \
                if s["created"] else store.now_iso()
            if store.add_activity(contact["id"], "email", type_, ts,
                                  ext_id=f"sgsupp:{kind}:{s['email']}:{s['created']}",
                                  meta={"reason": s["reason"], "kind": kind,
                                        "status": s["status"]}):
                new += 1
                store.pause_sequences_for(contact["id"], status="paused")
    result = {"new": new}
    if not data.get("ok"):
        # falla PARCIAL (algún endpoint de suppression falló pero otro sí
        # trajo datos): igual se procesa lo real, pero no se esconde el
        # error — antes se perdía en silencio y parecía un ciclo sin novedades.
        result["error"] = data.get("error", "")
    return result


def run(check_gmail_first: bool = True) -> dict:
    """Ciclo completo de ingesta (lo corre el scheduler y POST /api/ingest/run)."""
    result: dict = {"ts": store.now_iso()}
    if check_gmail_first:
        result["gmail"] = check_gmail()
    result["replies"] = ingest_replies()
    result["suppressions"] = ingest_suppressions()
    # Eventos nuevos de runners, webhook SendGrid, llamadas y accepts.
    result["activities"] = sync.backfill_activities()
    return result
