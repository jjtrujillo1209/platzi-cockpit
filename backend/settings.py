"""Gestión de accesos/credenciales desde la UI (SendGrid, remitente).

Lee/escribe el .env de prospector-flow y actualiza os.environ del proceso para
que los envíos (subprocesos) usen la nueva key al instante, sin reiniciar.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import requests

from config import ENV_FILE, COOKIES_PATH, DEFAULT_FROM_EMAIL, DEFAULT_FROM_NAME, DEFAULT_BCC


def _read_env() -> dict:
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def set_env_value(key: str, value: str) -> None:
    """Actualiza (o agrega) KEY=value en el .env y en os.environ."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "•" * len(key)


def status() -> dict:
    env = _read_env()
    key = env.get("SENDGRID_API_KEY") or os.environ.get("SENDGRID_API_KEY", "")
    return {
        "configured": bool(key),
        "masked": _mask(key),
        "from_email": env.get("SENDGRID_FROM_EMAIL") or DEFAULT_FROM_EMAIL,
        "from_name": env.get("SENDGRID_FROM_NAME") or DEFAULT_FROM_NAME,
        "bcc": env.get("SENDGRID_BCC") or DEFAULT_BCC,
    }


def update(api_key: str | None = None, from_email: str | None = None,
           from_name: str | None = None, bcc: str | None = None) -> dict:
    if api_key:
        set_env_value("SENDGRID_API_KEY", api_key.strip())
    if from_email is not None:
        set_env_value("SENDGRID_FROM_EMAIL", from_email.strip())
    if from_name is not None:
        set_env_value("SENDGRID_FROM_NAME", from_name.strip())
    if bcc is not None:
        set_env_value("SENDGRID_BCC", bcc.strip())
    return status()


def test_connection(api_key: str | None = None) -> dict:
    """Valida la key contra SendGrid (/v3/scopes). Si no se pasa, usa la guardada."""
    key = (api_key or "").strip() or _read_env().get("SENDGRID_API_KEY") \
        or os.environ.get("SENDGRID_API_KEY", "")
    if not key:
        return {"ok": False, "error": "No hay API key configurada"}
    try:
        r = requests.get(
            "https://api.sendgrid.com/v3/scopes",
            headers={"Authorization": f"Bearer {key}"}, timeout=15,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"red: {e.__class__.__name__}"}
    if r.status_code == 200:
        scopes = r.json().get("scopes", [])
        return {"ok": True, "scopes": len(scopes),
                "can_send": "mail.send" in scopes,
                "has_stats": any(s.startswith("stats") for s in scopes)}
    if r.status_code == 401:
        return {"ok": False, "error": "API key inválida (401)"}
    return {"ok": False, "error": f"HTTP {r.status_code}"}


# ============================ ANTHROPIC =================================
def anthropic_status() -> dict:
    key = _read_env().get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    return {"configured": bool(key), "masked": _mask(key)}


def anthropic_update(api_key: str) -> dict:
    if api_key:
        set_env_value("ANTHROPIC_API_KEY", api_key.strip())
    return anthropic_status()


def anthropic_test(api_key: str | None = None) -> dict:
    key = (api_key or "").strip() or _read_env().get("ANTHROPIC_API_KEY") \
        or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"ok": False, "error": "No hay API key configurada"}
    try:
        r = requests.get("https://api.anthropic.com/v1/models",
                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                         timeout=15)
    except requests.RequestException as e:
        return {"ok": False, "error": f"red: {e.__class__.__name__}"}
    if r.status_code == 200:
        return {"ok": True, "models": len(r.json().get("data", []))}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "API key inválida"}
    return {"ok": False, "error": f"HTTP {r.status_code}"}


# ============================ LINKEDIN COOKIES =========================
def _read_cookies() -> list[dict]:
    if not COOKIES_PATH.exists():
        return []
    try:
        data = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
        return data.get("cookies", data) if isinstance(data, dict) else data
    except (json.JSONDecodeError, OSError):
        return []


def linkedin_status() -> dict:
    cookies = _read_cookies()
    li_at = next((c for c in cookies if c.get("name") == "li_at"), None)
    expires = ""
    valid = False
    if li_at and li_at.get("expires"):
        try:
            dt = datetime.fromtimestamp(int(li_at["expires"]), tz=timezone.utc)
            expires = dt.date().isoformat()
            valid = dt > datetime.now(timezone.utc)
        except (ValueError, TypeError):
            pass
    return {"configured": bool(li_at), "count": len(cookies),
            "li_at_expires": expires, "valid": valid,
            "path": str(COOKIES_PATH)}


def linkedin_update(cookies_text: str) -> dict:
    try:
        data = json.loads(cookies_text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "JSON inválido"}
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    if not isinstance(cookies, list) or not any(c.get("name") == "li_at" for c in cookies):
        return {"ok": False, "error": "No se encontró la cookie 'li_at' en el JSON"}
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, **linkedin_status()}


# ============================ GOOGLE LOGIN ============================
def google_status() -> dict:
    env = _read_env()
    cid = env.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")
    return {"configured": bool(cid), "client_id": cid,  # no es secreto
            "allowed_domain": env.get("PLATZI_ALLOWED_DOMAIN")
            or os.environ.get("PLATZI_ALLOWED_DOMAIN", "platzi.com")}


def google_update(client_id: str | None = None, allowed_domain: str | None = None) -> dict:
    if client_id is not None:
        set_env_value("GOOGLE_CLIENT_ID", client_id.strip())
    if allowed_domain:
        set_env_value("PLATZI_ALLOWED_DOMAIN", allowed_domain.strip())
    return google_status()
