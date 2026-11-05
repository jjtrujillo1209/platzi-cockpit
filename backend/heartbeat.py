"""Heartbeat diario: valida open rate de correos y conexiones aceptadas.

- Open rate: barato, desde webhook_events (vía stats).
- Conexiones aceptadas: re-visita los perfiles invitados con Playwright
  (accept_checker.py) y cuenta los que ya están conectados.

Guarda un snapshot por día en data/heartbeat.json (historial para la tendencia)
y el estado de conexiones en data/connection_status.json.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import seats as seats_mod
import stats as stats_mod
import store
from config import COOKIES_PATH, PYTHON
from runs import all_linkedin_events

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(parents=True, exist_ok=True)
HB_STORE = DATA / "heartbeat.json"
CONN_STORE = DATA / "connection_status.json"
ACCEPT_CHECKER = Path(__file__).resolve().parent / "accept_checker.py"

_running = False  # evita chequeos de LinkedIn solapados


def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _slug(u: str) -> str:
    m = re.search(r"/in/([^/?#]+)", (u or "").lower())
    return m.group(1) if m else (u or "")


def invited_urls() -> list[str]:
    """Perfiles a los que enviamos invitación (sent o pendiente)."""
    seen, urls = set(), []
    for e in all_linkedin_events():
        if e.get("channel") == "followup":
            continue
        if e.get("status") in ("sent", "pending_invite"):
            u = e.get("url", "")
            s = _slug(u)
            if s and s not in seen:
                seen.add(s)
                urls.append(u)
    return urls


def connection_metrics() -> dict:
    invited = invited_urls()
    conn = _load(CONN_STORE)
    accepted = sum(1 for s in (_slug(u) for u in invited)
                   if conn.get(s, {}).get("status") == "accepted")
    pending = sum(1 for s in (_slug(u) for u in invited)
                  if conn.get(s, {}).get("status") == "pending")
    total = len(invited)
    return {
        "invitadas": total,
        "aceptadas": accepted,
        "pendientes": pending,
        "acceptance_rate": round(accepted / total * 100, 1) if total else 0.0,
        "checked": sum(1 for u in invited if _slug(u) in conn),
    }


def invited_por_asiento() -> dict[str, list[str]]:
    """Reparte los perfiles invitados según QUIÉN los invitó.

    Es obligatorio con varios asientos: LinkedIn solo muestra la conexión al
    perfil que la hizo. Revisar una invitación de un VP desde la sesión de otro
    la reporta como no conectada — un falso negativo que congela la cadencia,
    porque el paso que escribe al que aceptó nunca llega a dispararse.

    La clave "" son las invitaciones viejas del asiento único, que se siguen
    revisando con la sesión suelta de siempre.
    """
    por_slug = {}
    for c in store.fetch_all(
            "SELECT linkedin, li_seat FROM contacts WHERE linkedin<>'' AND li_seat<>''"):
        por_slug[_slug(c["linkedin"])] = c["li_seat"]
    grupos: dict[str, list[str]] = {}
    for u in invited_urls():
        grupos.setdefault(por_slug.get(_slug(u), ""), []).append(u)
    return grupos


async def _correr_checker(urls: list[str], etiqueta: str, cookies: str,
                          cdp: str, headless: bool, cap: int) -> bool:
    urls_file = DATA / f"_invited_urls_{etiqueta or 'suelto'}.txt"
    urls_file.write_text("\n".join(urls), encoding="utf-8")
    cmd = [PYTHON, str(ACCEPT_CHECKER),
           "--urls-file", str(urls_file), "--out", str(CONN_STORE),
           "--cookies", cookies, "--cap", str(cap)]
    if cdp:
        cmd += ["--cdp-endpoint", cdp]
    if headless:
        cmd.append("--headless")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(ACCEPT_CHECKER.parent),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return proc.returncode == 0


async def check_connections_accepted(headless: bool = True, cap: int = 80) -> dict:
    """Re-visita los perfiles invitados, una pasada por asiento.

    Las pasadas van EN SERIE y no en paralelo: tres navegadores recorriendo
    perfiles al mismo tiempo desde la misma IP es justo el patrón que LinkedIn
    mira, y esto es un chequeo de fondo que no tiene ninguna prisa.
    """
    global _running
    if _running:
        return {"skipped": "ya hay un chequeo en curso"}
    grupos = invited_por_asiento()
    if not grupos:
        return {"invitadas": 0}
    _running = True
    revisados, saltados, ok_total = {}, [], True
    try:
        for seat_id, urls in sorted(grupos.items()):
            if seat_id:
                seat = seats_mod.get(seat_id)
                if seat is None:
                    saltados.append(f"{seat_id}: el asiento ya no existe")
                    continue
                listo = seats_mod.asegurar_envio(seat)
                if not listo.get("ok"):
                    # Sin la sesión de quien invitó no se puede saber si
                    # aceptaron. Se dice; no se adivina con otra sesión.
                    saltados.append(f"{seat.etiqueta}: {listo.get('error') or 'sin sesión'} — "
                                    f"{len(urls)} invitaciones sin verificar")
                    continue
                cookies, cdp, etiqueta = seat.cookies, seat.cdp, seat.etiqueta
            else:
                cookies, cdp, etiqueta = str(COOKIES_PATH), "", "sesión suelta"
            ok = await _correr_checker(urls, seat_id, cookies, cdp, headless, cap)
            revisados[etiqueta] = len(urls)
            ok_total = ok_total and ok
    finally:
        _running = False
    # antes no se miraba returncode: un accept_checker que crasheaba (sesión
    # LinkedIn vencida, Playwright roto) dejaba CONN_STORE intacto y esta
    # función devolvía las mismas métricas de ayer como si fueran de hoy.
    return {**connection_metrics(), "check_ok": ok_total,
            "revisados_por_asiento": revisados, "sin_verificar": saltados}


def email_metrics() -> dict:
    em = stats_mod.email_metrics()
    return {
        "enviados": em["sent"],
        "entregados": em["delivered"],
        "abiertos_unicos": em["opened_unique"],
        "open_rate": em["open_rate"],
        "click_rate": em["click_rate"],
        "bounce_rate": em["bounce_rate"],
        "spam_rate": em["spam_rate"],
    }


async def run_snapshot(check_linkedin: bool = True, headless: bool = True) -> dict:
    """Toma un snapshot diario; opcionalmente re-chequea LinkedIn."""
    linkedin_check_error = ""
    sin_verificar: list[str] = []
    if check_linkedin:
        try:
            res = await check_connections_accepted(headless=headless)
            sin_verificar = res.get("sin_verificar", [])
        except Exception as e:
            # antes se tragaba en silencio y el snapshot quedaba con las
            # métricas de conexiones de un día anterior, indistinguibles de
            # datos frescos.
            linkedin_check_error = f"{e.__class__.__name__}: {e}"
    snap = {
        "date": datetime.now().date().isoformat(),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "email": email_metrics(),
        "conexiones": connection_metrics(),
        "linkedin_check_error": linkedin_check_error,
        "sin_verificar": sin_verificar,
    }
    hist = _load(HB_STORE)
    hist[snap["date"]] = snap  # un snapshot por día (se sobrescribe el del día)
    HB_STORE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap


def history() -> dict:
    hist = _load(HB_STORE)
    days = sorted(hist.values(), key=lambda s: s.get("date", ""))
    return {
        "latest": days[-1] if days else None,
        "running": _running,
        "history": days,
        # vista en vivo (no espera al próximo tick)
        "live": {"email": email_metrics(), "conexiones": connection_metrics()},
    }
