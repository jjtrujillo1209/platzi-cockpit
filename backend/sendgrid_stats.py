"""Estadísticas reales de SendGrid por categoría (platzi-cockpit).

Consulta GET /v3/categories/stats para que el Dashboard muestre los números
que SendGrid reporta para la categoría con la que envía el cockpit, sin
depender del Event Webhook (ngrok). Con caché de 10 min para no golpear el
API en cada refresco del Dashboard.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, timedelta

import requests
from dotenv import dotenv_values

from config import ENV_FILE

CATEGORY = os.environ.get("PLATZI_SENDGRID_CATEGORY", "platzi-cockpit")
DAYS_BACK = int(os.environ.get("PLATZI_SENDGRID_STATS_DAYS", "120"))
_CACHE_TTL = 600  # seg

_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "data": None}

_METRICS = ("requests", "delivered", "opens", "unique_opens", "clicks",
            "unique_clicks", "bounces", "spam_reports", "blocks")


def _api_key() -> str:
    env = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    return (env.get("SENDGRID_API_KEY") or os.environ.get("SENDGRID_API_KEY") or "").strip()


def _fetch(category: str, days: int) -> dict:
    key = _api_key()
    if not key:
        return {"ok": False, "error": "SENDGRID_API_KEY no configurada"}
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        r = requests.get(
            "https://api.sendgrid.com/v3/categories/stats",
            headers={"Authorization": f"Bearer {key}"},
            params={"start_date": start, "end_date": date.today().isoformat(),
                    "categories": category, "aggregated_by": "day"},
            timeout=20,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"conexión SendGrid: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"SendGrid {r.status_code}: {r.text[:200]}"}

    totals = {m: 0 for m in _METRICS}
    daily: list[dict] = []
    for day in r.json():
        row = {"date": day.get("date", "")}
        got = False
        for s in day.get("stats", []):
            metrics = s.get("metrics", {})
            for m in _METRICS:
                v = int(metrics.get(m, 0) or 0)
                totals[m] += v
                row[m] = row.get(m, 0) + v
                got = got or v > 0
        if got:
            daily.append(row)

    delivered = totals["delivered"] or totals["requests"] or 1
    return {
        "ok": True,
        "category": category,
        "since": start,
        "totals": totals,
        "open_rate": round(totals["unique_opens"] / delivered * 100, 1),
        "click_rate": round(totals["unique_clicks"] / delivered * 100, 1),
        "bounce_rate": round(totals["bounces"] / (totals["requests"] or 1) * 100, 1),
        "daily": daily,
    }


def category_stats(force: bool = False) -> dict:
    """Stats de la categoría con caché (TTL 10 min)."""
    with _lock:
        if not force and _cache["data"] is not None \
                and time.time() - _cache["ts"] < _CACHE_TTL:
            return _cache["data"]
        data = _fetch(CATEGORY, DAYS_BACK)
        # no cachear errores por el TTL completo: reintenta en 60s
        _cache["data"] = data
        _cache["ts"] = time.time() if data.get("ok") else time.time() - _CACHE_TTL + 60
        return data


# ----------------------------- suppressions --------------------------------
# Bounces / blocks / spam por DESTINATARIO (con razón). Disponible en todos
# los planes (scope suppression.read) — no requiere el addon de Email Activity.
_supp_cache: dict = {"ts": 0.0, "data": None}


def _fetch_suppressions() -> dict:
    key = _api_key()
    if not key:
        return {"ok": False, "error": "SENDGRID_API_KEY no configurada"}
    h = {"Authorization": f"Bearer {key}"}
    out = {"ok": True, "bounces": [], "blocks": [], "spam_reports": []}
    for kind, path in (("bounces", "bounces"), ("blocks", "blocks"),
                       ("spam_reports", "spam_reports")):
        try:
            r = requests.get(f"https://api.sendgrid.com/v3/suppression/{path}",
                             headers=h, timeout=20)
        except requests.RequestException as e:
            out["ok"] = False
            out["error"] = f"{path}: {e}"
            continue
        if r.status_code != 200:
            # HTTP no-200 (401/429/500…) NO es "cero rebotes" — es que la
            # llamada falló. Antes se ignoraba en silencio y el caller
            # (ingest_suppressions) lo trataba como "sin novedades", dejando
            # de pausar secuencias a direcciones que sí rebotaron/marcaron spam.
            out["ok"] = False
            out["error"] = f"{path}: HTTP {r.status_code}"
            continue
        out[kind] = [{
            "email": (x.get("email") or "").lower(),
            "reason": x.get("reason", ""),
            "status": x.get("status", ""),
            "created": int(x.get("created") or 0),
        } for x in r.json()]
    return out


def suppressions(force: bool = False) -> dict:
    with _lock:
        if not force and _supp_cache["data"] is not None \
                and time.time() - _supp_cache["ts"] < _CACHE_TTL:
            return _supp_cache["data"]
        data = _fetch_suppressions()
        _supp_cache["data"] = data
        _supp_cache["ts"] = time.time() if data.get("ok") else time.time() - _CACHE_TTL + 60
        return data
