"""Login con Google restringido al dominio @platzi.com.

Flujo: el frontend usa Google Identity Services (botón "Sign in with Google"),
obtiene un ID token (JWT) y lo manda a /api/auth/google. Aquí lo verificamos
(firma + audiencia) y exigimos que el correo sea del dominio permitido. Si pasa,
emitimos un token de sesión propio (HMAC) que el frontend guarda y envía en cada
request; un middleware lo valida.

La autenticación SOLO se activa si GOOGLE_CLIENT_ID está definido (en el .env de
prospector-flow). Si no está, el cockpit corre abierto (modo desarrollo).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# Se leen EN VIVO de os.environ para que actualizarlos desde la UI (Ajustes)
# active/desactive el login sin reiniciar el backend.
def _client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def _allowed_domain() -> str:
    return os.environ.get("PLATZI_ALLOWED_DOMAIN", "platzi.com").strip().lower()


SESSION_TTL = int(os.environ.get("PLATZI_SESSION_TTL", "86400"))  # 24h
_SECRET = os.environ.get("PLATZI_SESSION_SECRET") or secrets.token_hex(32)


def enabled() -> bool:
    return bool(_client_id())


def public_config() -> dict:
    return {"enabled": enabled(), "client_id": _client_id(),
            "allowed_domain": _allowed_domain()}


# ----------------------------- ID token de Google -----------------------
def verify_google_credential(credential: str) -> dict:
    """Verifica el ID token de Google y exige el dominio permitido.

    Devuelve {"email","name","picture"} o lanza ValueError.
    """
    from google.oauth2 import id_token  # import perezoso (dep opcional)
    from google.auth.transport import requests as g_requests

    info = id_token.verify_oauth2_token(
        credential, g_requests.Request(), _client_id()
    )
    domain = _allowed_domain()
    email = (info.get("email") or "").lower()
    hd = (info.get("hd") or "").lower()
    if not info.get("email_verified"):
        raise ValueError("correo no verificado en Google")
    if not (email.endswith("@" + domain) or hd == domain):
        raise ValueError(f"solo se permite el dominio @{domain}")
    return {"email": email, "name": info.get("name", ""), "picture": info.get("picture", "")}


# ----------------------------- sesión propia (HMAC) ---------------------
def _sign(msg: bytes) -> str:
    return hmac.new(_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]


def issue_session(email: str) -> str:
    payload = {"email": email, "exp": int(time.time()) + SESSION_TTL}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"{raw}.{_sign(raw.encode())}"


def verify_session(token: str) -> dict | None:
    try:
        raw, sig = token.rsplit(".", 1)
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sign(raw.encode())):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload
