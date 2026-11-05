"""Confirmación de asistencia con verificación de correo y celular.

Por qué verificar los dos: el registro es el KPI del test, así que un correo mal
escrito o un celular inventado no solo pierde al invitado — ensucia la métrica.
Verificar obliga a que los dos datos sean reales antes de contar el registro.

Cómo se cuenta un registro:

  1. El invitado llega con su código de atribución en la URL (`?c=…`), el mismo
     que emitió `links.py`. Eso ata el registro al contacto sin pedirle nada.
  2. Escribe correo y celular → se emiten dos códigos de 6 dígitos.
  3. Verifica los dos → el contacto pasa a `registrado`, se cierra su cadencia y
     queda la actividad en el pipeline.

**Entrega de los códigos.** El correo sale por SMTP si está configurado; el SMS
no tiene proveedor todavía, así que se registra en la bitácora y la respuesta lo
dice. `RSVP_DEBUG_CODES=1` devuelve los códigos en la respuesta para poder probar
el flujo completo sin proveedor — nunca dejarlo puesto en producción.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional

import store
from config import DEFAULT_FROM_EMAIL, DEFAULT_FROM_NAME

CODIGO_TTL_MIN = 15
MAX_INTENTOS = 5
REENVIO_MIN_SEG = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rsvp (
  id INTEGER PRIMARY KEY,
  campaign TEXT NOT NULL,
  contact_id INTEGER REFERENCES contacts(id),
  attribution_code TEXT DEFAULT '',
  nombre TEXT DEFAULT '',
  empresa TEXT DEFAULT '',
  email TEXT DEFAULT '',
  telefono TEXT DEFAULT '',
  email_verificado INTEGER DEFAULT 0,
  telefono_verificado INTEGER DEFAULT 0,
  acompanante TEXT DEFAULT '',
  estado TEXT DEFAULT 'iniciado',     -- iniciado | verificado | confirmado
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(campaign, email)
);
CREATE TABLE IF NOT EXISTS rsvp_codigos (
  id INTEGER PRIMARY KEY,
  rsvp_id INTEGER NOT NULL REFERENCES rsvp(id),
  canal TEXT NOT NULL,               -- email | sms
  hash TEXT NOT NULL,                -- nunca se guarda el código en claro
  expira_at TEXT NOT NULL,
  intentos INTEGER DEFAULT 0,
  usado INTEGER DEFAULT 0,
  enviado_at TEXT,
  entregado INTEGER DEFAULT 0        -- 0 = no salió (sin proveedor configurado)
);
CREATE INDEX IF NOT EXISTS idx_rsvp_cod ON rsvp_codigos(rsvp_id, canal, usado);
"""

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def init() -> None:
    with store._LOCK, store.connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def debug_codes() -> bool:
    return os.environ.get("RSVP_DEBUG_CODES", "").strip() in ("1", "true", "yes")


# ------------------------------------------------------------ normalización
def normaliza_email(v: str) -> str:
    return (v or "").strip().lower()


def normaliza_tel(v: str) -> str:
    """E.164 para Colombia. Sin esto, «300 123 4567» y «+573001234567» son dos
    personas distintas para la base y una sola en la vida real."""
    d = re.sub(r"[^\d+]", "", (v or "").strip())
    if d.startswith("+"):
        return "+" + re.sub(r"\D", "", d[1:])
    d = re.sub(r"\D", "", d)
    if len(d) == 10 and d.startswith("3"):
        return "+57" + d
    if len(d) == 12 and d.startswith("57"):
        return "+" + d
    return "+" + d if d else ""


def valida(email: str, telefono: str) -> list[str]:
    errores = []
    if not _EMAIL_RE.match(email or ""):
        errores.append("El correo no parece válido.")
    tel = normaliza_tel(telefono)
    if len(re.sub(r"\D", "", tel)) < 10:
        errores.append("El número de celular está incompleto.")
    return errores


# ------------------------------------------------------------------ códigos
def _secreto() -> str:
    s = store.kv_get("rsvp_secret", "")
    if not s:
        s = secrets.token_hex(32)
        store.kv_set("rsvp_secret", s)
    return s


def _hash(codigo: str, rsvp_id: int, canal: str) -> str:
    return hmac.new(_secreto().encode(), f"{rsvp_id}:{canal}:{codigo}".encode(),
                    hashlib.sha256).hexdigest()


def _nuevo_codigo() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _emitir(rsvp_id: int, canal: str) -> Optional[str]:
    """Emite un código nuevo e invalida los anteriores del mismo canal."""
    ultimo = store.fetch_one(
        "SELECT enviado_at FROM rsvp_codigos WHERE rsvp_id=? AND canal=? "
        "ORDER BY id DESC LIMIT 1", (rsvp_id, canal))
    if ultimo and ultimo["enviado_at"]:
        try:
            transcurrido = (datetime.now() -
                            datetime.fromisoformat(ultimo["enviado_at"])).total_seconds()
            if transcurrido < REENVIO_MIN_SEG:
                return None            # anti-spam: un código por minuto y canal
        except ValueError:
            pass
    codigo = _nuevo_codigo()
    expira = (datetime.now() + timedelta(minutes=CODIGO_TTL_MIN)).isoformat(timespec="seconds")
    # El hash se calcula ANTES de tomar el lock: _secreto() escribe en kv la
    # primera vez, y store._LOCK no es reentrante — hacerlo adentro cuelga el
    # proceso sin error, esperándose a sí mismo.
    h = _hash(codigo, rsvp_id, canal)
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE rsvp_codigos SET usado=1 WHERE rsvp_id=? AND canal=?",
                     (rsvp_id, canal))
        conn.execute(
            "INSERT INTO rsvp_codigos (rsvp_id, canal, hash, expira_at, enviado_at) "
            "VALUES (?,?,?,?,?)",
            (rsvp_id, canal, h, expira, store.now_iso()))
        conn.commit()
    return codigo


# ------------------------------------------------------------------- envío
def _smtp_config() -> tuple[str, str, str, int]:
    user = (os.environ.get("SMTP_USER") or "").strip()
    pwd = (os.environ.get("SMTP_PASSWORD") or "").strip()
    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT") or 587)
    return user, pwd, host, port


def enviar_email(destino: str, codigo: str, evento: str) -> tuple[bool, str]:
    user, pwd, host, port = _smtp_config()
    if not user or not pwd:
        return False, "SMTP sin configurar (falta contraseña de aplicación)"
    msg = EmailMessage()
    msg["Subject"] = f"Su código de confirmación: {codigo}"
    msg["From"] = f"{DEFAULT_FROM_NAME} <{DEFAULT_FROM_EMAIL}>"
    msg["To"] = destino
    msg.set_content(
        f"Su código para confirmar la asistencia a {evento} es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {CODIGO_TTL_MIN} minutos. Si no pidió este código, ignore "
        "este mensaje.\n")
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"


def enviar_sms(destino: str, codigo: str, evento: str) -> tuple[bool, str]:
    """Sin proveedor todavía. Se deja explícito en vez de simular un envío:
    un SMS que nadie manda pero que el sistema da por enviado deja al invitado
    esperando un código que no existe."""
    return False, "SMS sin proveedor configurado (falta telefonía)"


# ------------------------------------------------------------------- flujo
def iniciar(campaign_slug: str, evento: str, email: str, telefono: str,
            attribution_code: str = "", nombre: str = "",
            empresa: str = "") -> dict:
    init()
    email = normaliza_email(email)
    telefono = normaliza_tel(telefono)
    errores = valida(email, telefono)
    if errores:
        return {"ok": False, "errores": errores}

    # ¿De quién es este registro? El código de atribución lo dice sin preguntar.
    contact_id = None
    if attribution_code:
        import links
        fila = links.resolve(attribution_code)
        if fila:
            contact_id = fila["contact_id"]
    if contact_id is None:
        c = store.find_contact(email=email)
        contact_id = c["id"] if c else None

    fila = store.fetch_one("SELECT * FROM rsvp WHERE campaign=? AND email=?",
                           (campaign_slug, email))
    if fila:
        rsvp_id = fila["id"]
        with store._LOCK, store.connect() as conn:
            conn.execute(
                "UPDATE rsvp SET telefono=?, contact_id=COALESCE(?, contact_id), "
                "attribution_code=COALESCE(NULLIF(?,''), attribution_code), "
                "updated_at=? WHERE id=?",
                (telefono, contact_id, attribution_code, store.now_iso(), rsvp_id))
            conn.commit()
    else:
        with store._LOCK, store.connect() as conn:
            cur = conn.execute(
                "INSERT INTO rsvp (campaign, contact_id, attribution_code, nombre, "
                "empresa, email, telefono, estado, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'iniciado',?,?)",
                (campaign_slug, contact_id, attribution_code, nombre, empresa,
                 email, telefono, store.now_iso(), store.now_iso()))
            rsvp_id = cur.lastrowid
            conn.commit()

    salida: dict = {"ok": True, "rsvp_id": rsvp_id, "canales": {}}
    for canal, destino, envia in (("email", email, enviar_email),
                                  ("sms", telefono, enviar_sms)):
        codigo = _emitir(rsvp_id, canal)
        if codigo is None:
            salida["canales"][canal] = {"enviado": False,
                                        "motivo": "espera un minuto antes de pedir otro código"}
            continue
        entregado, motivo = envia(destino, codigo, evento)
        with store._LOCK, store.connect() as conn:
            conn.execute("UPDATE rsvp_codigos SET entregado=? WHERE rsvp_id=? AND canal=? "
                         "AND usado=0", (1 if entregado else 0, rsvp_id, canal))
            conn.commit()
        salida["canales"][canal] = {"enviado": entregado, "motivo": motivo}
        if debug_codes():
            salida["canales"][canal]["codigo_debug"] = codigo
    return salida


def verificar(rsvp_id: int, canal: str, codigo: str) -> dict:
    init()
    fila = store.fetch_one(
        "SELECT * FROM rsvp_codigos WHERE rsvp_id=? AND canal=? AND usado=0 "
        "ORDER BY id DESC LIMIT 1", (rsvp_id, canal))
    if not fila:
        return {"ok": False, "error": "No hay ningún código pendiente para ese canal."}
    if fila["intentos"] >= MAX_INTENTOS:
        return {"ok": False, "error": "Demasiados intentos. Pida un código nuevo."}
    if store.now_iso() > fila["expira_at"]:
        return {"ok": False, "error": "El código venció. Pida uno nuevo."}

    entrada = re.sub(r"\D", "", codigo or "")
    correcto = hmac.compare_digest(fila["hash"], _hash(entrada, rsvp_id, canal))
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE rsvp_codigos SET intentos=intentos+1, usado=? WHERE id=?",
                     (1 if correcto else 0, fila["id"]))
        if correcto:
            col = "email_verificado" if canal == "email" else "telefono_verificado"
            conn.execute(f"UPDATE rsvp SET {col}=1, updated_at=? WHERE id=?",
                         (store.now_iso(), rsvp_id))
        conn.commit()
    if not correcto:
        restantes = MAX_INTENTOS - (fila["intentos"] + 1)
        return {"ok": False, "error": f"Código incorrecto. Le quedan {max(restantes, 0)} intentos."}

    r = store.fetch_one("SELECT * FROM rsvp WHERE id=?", (rsvp_id,))
    completo = bool(r["email_verificado"]) and bool(r["telefono_verificado"])
    if completo and r["estado"] != "confirmado":
        _confirmar(r)
    return {"ok": True, "verificado": canal, "completo": completo}


def _confirmar(r: dict) -> None:
    """Los dos canales verificados: esto ya es un registro de verdad."""
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE rsvp SET estado='confirmado', updated_at=? WHERE id=?",
                     (store.now_iso(), r["id"]))
        conn.commit()
    if not r["contact_id"]:
        return
    store.set_stage(r["contact_id"], "registrado")
    store.add_activity(r["contact_id"], "email", "registro", store.now_iso(),
                       ext_id=f"rsvp:{r['id']}",
                       meta={"campaign": r["campaign"], "email": r["email"],
                             "telefono": r["telefono"],
                             "atribuido": bool(r["attribution_code"])})
    # Una vez que dijo que sí, los recordatorios de invitación sobran.
    store.pause_sequences_for(r["contact_id"], "completed")


def acompanante(rsvp_id: int, nombre: str) -> dict:
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE rsvp SET acompanante=?, updated_at=? WHERE id=?",
                     ((nombre or "").strip()[:200], store.now_iso(), rsvp_id))
        conn.commit()
    return {"ok": True}


def resumen(campaign_slug: str) -> dict:
    init()
    filas = store.fetch_all("SELECT * FROM rsvp WHERE campaign=?", (campaign_slug,))
    confirmados = [f for f in filas if f["estado"] == "confirmado"]
    return {
        "iniciados": len(filas),
        "confirmados": len(confirmados),
        "atribuidos": sum(1 for f in confirmados if f["attribution_code"]),
        "con_acompanante": sum(1 for f in confirmados if (f["acompanante"] or "").strip()),
        "registros": [{"nombre": f["nombre"], "empresa": f["empresa"],
                       "email": f["email"], "telefono": f["telefono"],
                       "acompanante": f["acompanante"], "estado": f["estado"],
                       "atribuido": bool(f["attribution_code"]),
                       "contact_id": f["contact_id"]} for f in filas],
    }
