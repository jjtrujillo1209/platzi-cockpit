"""Verifica qué invitaciones de LinkedIn fueron ACEPTADAS.

Re-visita los perfiles que invitamos y lee el estado del menú (reutiliza la
lógica de bulk_connect/runner.py). Escribe un mapa {slug: {status, ts}} a --out.

status: accepted (ya conectados) | pending (invitación aún pendiente) |
        not_connected (sin conexión: retirada/rechazada) | error | rate_limited
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from config import BULK_CONNECT_DIR

sys.path.insert(0, str(BULK_CONNECT_DIR))
from runner import (  # noqa: E402  (reusa código existente y probado)
    load_cookies, normalize_url, open_more_menu, menu_state, close_open_menu,
)
from playwright.async_api import async_playwright  # noqa: E402


def _slug(u: str) -> str:
    m = re.search(r"/in/([^/?#]+)", (u or "").lower())
    return m.group(1) if m else (u or "")


STATE_MAP = {"already": "accepted", "pending": "pending",
             "connect_ready": "not_connected", "none": "not_connected"}


async def run(urls, cookies_path, out_path, *, headless, min_delay, max_delay, cap,
              cdp_endpoint: str = ""):
    """Hay que mirar desde la sesión de QUIEN invitó.

    Una invitación aceptada solo se ve conectada desde el perfil que la mandó.
    Si se revisa desde otro, el perfil aparece como «Conectar» y se registraría
    como no aceptado: un falso negativo que además congela la cadencia, porque
    el paso que escribe al que aceptó nunca se dispara.
    """
    result: dict[str, dict] = {}
    if out_path.exists():
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {}
    using_cdp = bool(cdp_endpoint)
    async with async_playwright() as pw:
        if using_cdp:
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()
        else:
            browser = await pw.chromium.launch(headless=headless,
                args=["--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1366, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            await ctx.add_cookies(load_cookies(cookies_path))
            page = await ctx.new_page()

        async def _cerrar():
            try:
                await (page.close() if using_cdp else browser.close())
            except Exception:
                pass
        try:
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=25000)
        except Exception:
            pass
        if any(x in page.url for x in ("login", "checkpoint", "authwall")):
            result["__error__"] = {"status": "rate_limited", "ts": datetime.now().isoformat(timespec="seconds")}
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            await _cerrar(); return

        done = 0
        for raw in urls:
            if cap and done >= cap:
                break
            url = normalize_url(raw) or raw
            slug = _slug(url)
            status = "error"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                if any(x in page.url for x in ("login", "checkpoint", "authwall")):
                    result["__error__"] = {"status": "rate_limited", "ts": datetime.now().isoformat(timespec="seconds")}
                    break
                if await open_more_menu(page):
                    st = await menu_state(page)
                    status = STATE_MAP.get(st, "error")
                    await close_open_menu(page)
                else:
                    status = "error"
            except Exception:
                status = "error"
            result[slug] = {"status": status, "url": url,
                            "ts": datetime.now().isoformat(timespec="seconds")}
            done += 1
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            await asyncio.sleep(random.uniform(min_delay, max_delay))
        await _cerrar()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--urls-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cookies", required=True)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--min-delay", type=float, default=5.0)
    p.add_argument("--max-delay", type=float, default=10.0)
    p.add_argument("--cap", type=int, default=80)
    p.add_argument("--cdp-endpoint", default="",
                   help="Chrome ya logueado del perfil que invitó")
    a = p.parse_args()
    urls = [l.strip() for l in Path(a.urls_file).read_text().splitlines() if l.strip()]
    asyncio.run(run(urls, Path(a.cookies), Path(a.out),
        headless=a.headless, min_delay=a.min_delay, max_delay=a.max_delay, cap=a.cap,
        cdp_endpoint=a.cdp_endpoint))
    print("OK", len(urls))


if __name__ == "__main__":
    main()
