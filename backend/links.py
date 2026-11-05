"""Links de atribución por contacto.

Sin esto el test no se puede validar: el registro ocurre en una página que no es
nuestra, así que si todos los invitados reciben el mismo link, el día del evento
hay asistentes pero ninguna forma de demostrar que llegaron por el outreach. Es
la misma lección de los CTA que salían sin parámetros y dejaban los leads
entrantes sin origen.

Cada contacto recibe un código corto propio. Al abrirlo queda una actividad
`click` con su nombre y de ahí sale la señal más caliente del flujo: alguien que
hizo clic y no se registró es una llamada para hoy.
"""
from __future__ import annotations

import secrets
import sqlite3
from typing import Optional
from urllib.parse import urlencode

import store
from config import PUBLIC_BASE_URL

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attribution_links (
  code TEXT PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  campaign TEXT NOT NULL,
  created_at TEXT,
  UNIQUE(contact_id, campaign)
);
"""
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # sin l/i/o/0/1: se leen en voz alta


def init() -> None:
    with store._LOCK, store.connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(7))


def ensure(contact_id: int, campaign: str) -> str:
    """Código del contacto para esta campaña; lo crea si no existe."""
    init()
    row = store.fetch_one(
        "SELECT code FROM attribution_links WHERE contact_id=? AND campaign=?",
        (contact_id, campaign))
    if row:
        return row["code"]
    for _ in range(8):                      # colisión de 7 chars: reintentar
        code = _new_code()
        try:
            with store._LOCK, store.connect() as conn:
                conn.execute(
                    "INSERT INTO attribution_links (code, contact_id, campaign, "
                    "created_at) VALUES (?,?,?,?)",
                    (code, contact_id, campaign, store.now_iso()))
                conn.commit()
            return code
        except sqlite3.IntegrityError:
            row = store.fetch_one(
                "SELECT code FROM attribution_links WHERE contact_id=? AND campaign=?",
                (contact_id, campaign))
            if row:
                return row["code"]
    return ""


def url_for(contact_id: int, campaign: str) -> str:
    code = ensure(contact_id, campaign)
    return f"{PUBLIC_BASE_URL}/r/{code}" if code else ""


def resolve(code: str) -> Optional[dict]:
    init()
    return store.fetch_one(
        "SELECT * FROM attribution_links WHERE code=?", ((code or "").strip().lower(),))


def destination(event_url: str, campaign: str, code: str) -> str:
    """URL final con UTM, para que el equipo de Marketing también lo vea."""
    sep = "&" if "?" in event_url else "?"
    return event_url + sep + urlencode({
        "utm_source": "outbound-clevel",
        "utm_medium": "cockpit",
        "utm_campaign": campaign,
        "utm_content": code,
    })


def register_click(code: str, user_agent: str = "") -> Optional[dict]:
    """Deja la actividad `click` del contacto dueño del código.

    Idempotente por día: un presidente que abre el correo tres veces no genera
    tres señales, pero sí queda registrado que volvió otro día.
    """
    row = resolve(code)
    if not row:
        return None
    day = store.now_iso()[:10]
    store.add_activity(row["contact_id"], "email", "click", store.now_iso(),
                       ext_id=f"link:{code}:{day}",
                       meta={"campaign": row["campaign"], "source": "link_propio",
                             "user_agent": user_agent[:180]})
    return row


def clicked_contacts(campaign: str) -> list[dict]:
    """Contactos de la campaña con clic propio registrado."""
    return store.fetch_all(
        "SELECT DISTINCT c.id, c.nombre, c.empresa, c.email, c.celular, c.stage "
        "FROM attribution_links l "
        "JOIN activities a ON a.contact_id = l.contact_id AND a.type='click' "
        "JOIN contacts c ON c.id = l.contact_id "
        "WHERE l.campaign=?", (campaign,))
