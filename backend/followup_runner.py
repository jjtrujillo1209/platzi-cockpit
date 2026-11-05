"""Follow-up de LinkedIn: envía un mensaje a contactos YA conectados.

Modelado sobre bulk_connect/runner.py (misma sesión Playwright, cookies, pacing
y eventos JSONL por fila), pero en vez de conectar abre el chat del perfil y
escribe un mensaje. Escribe `results/<run_id>-followup-events.jsonl` para que la
UI muestre progreso en vivo.

Eventos (status): message_sent | not_connected | already_messaged | not_found |
                  rate_limited | error | skipped
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, TimeoutError as PWTimeout, async_playwright

# --- selectores -----------------------------------------------------------
MESSAGE_BUTTONS = [
    "main button[aria-label^='Enviar mensaje']",
    "main button[aria-label*='Mensaje a']",
    "main button[aria-label^='Message']",
    "main a[aria-label*='Mensaje']",
    "main button.message-anywhere-button",
    "main button:has-text('Mensaje')",
    "main button:has-text('Message')",
]
MORE_SELECTORS = [
    "main section button[aria-label='Más']",
    "main button[aria-label='More']",
]
MENU_MESSAGE = [
    "div[role='menu']:visible [role='menuitem']:has-text('mensaje')",
    "div[role='menu']:visible [role='menuitem']:has-text('message')",
]
MSG_OVERLAY = "div.msg-overlay-conversation-bubble, div.msg-form"
MSG_TEXTBOX = [
    "div.msg-form__contenteditable[contenteditable='true']",
    "div.msg-overlay-conversation-bubble div[role='textbox'][contenteditable='true']",
    "div[contenteditable='true'][role='textbox']",
]
MSG_SEND = [
    "button.msg-form__send-button:not([disabled])",
    "div.msg-form button[type='submit']:not([disabled])",
    "button.msg-form__send-button",
]
MSG_CLOSE = [
    "button[aria-label*='Cerrar la conversación']",
    "button[aria-label*='Close your conversation']",
    "div.msg-overlay-bubble-header button[aria-label*='Cerrar']",
]


@dataclass
class Event:
    idx: int
    total: int
    url: str
    status: str
    detail: str = ""
    ts: str = ""

    def to_dict(self):
        return {
            "idx": self.idx, "total": self.total, "url": self.url,
            "status": self.status, "detail": self.detail,
            "ts": self.ts or datetime.now().isoformat(timespec="seconds"),
        }


def write_event(path: Path, ev: Event) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")


def normalize_url(raw: str) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.startswith("linkedin.com"):
        raw = "https://www." + raw
    if raw.startswith("www.linkedin.com"):
        raw = "https://" + raw
    m = re.match(r"https?://[^/]*linkedin\.com/in/([^/?#]+)", raw, re.I)
    if not m:
        return None
    return f"https://www.linkedin.com/in/{m.group(1)}/"


def load_cookies(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("cookies", [])
    cleaned = []
    for c in data:
        c = {k: v for k, v in c.items() if k in {
            "name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}}
        if c.get("sameSite") not in {"Strict", "Lax", "None"}:
            c["sameSite"] = "Lax"
        cleaned.append(c)
    return cleaned


async def try_click(page: Page, selectors: list[str], timeout: int = 1800) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def _visible(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            if await page.locator(sel).first.is_visible():
                return True
        except Exception:
            continue
    return False


async def send_followup(page: Page, url: str, message: str) -> tuple[str, str]:
    if not message.strip():
        return "skipped", "sin-mensaje"

    # Abrir el chat: botón directo o vía menú "Más".
    opened = await try_click(page, MESSAGE_BUTTONS, timeout=4000)
    if not opened:
        if await try_click(page, MORE_SELECTORS, timeout=2500):
            await asyncio.sleep(random.uniform(0.4, 0.9))
            opened = await try_click(page, MENU_MESSAGE, timeout=2000)
    if not opened:
        # Sin botón de mensaje suele significar que aún no hay conexión 1er grado.
        return "not_connected", "sin-boton-mensaje"

    # Esperar el overlay de conversación.
    try:
        await page.locator(MSG_OVERLAY).first.wait_for(state="visible", timeout=5000)
    except PWTimeout:
        return "not_found", "overlay-no-aparecio"

    await asyncio.sleep(random.uniform(0.6, 1.2))

    # Escribir el mensaje en la caja de texto.
    box = None
    for sel in MSG_TEXTBOX:
        loc = page.locator(sel).last
        try:
            if await loc.is_visible():
                box = loc
                break
        except Exception:
            continue
    if box is None:
        return "not_found", "caja-texto-no-encontrada"

    try:
        await box.click()
        await box.type(message, delay=random.uniform(12, 32))
    except Exception as e:
        return "error", f"escritura:{e.__class__.__name__}"

    await asyncio.sleep(random.uniform(0.5, 1.0))

    if not await try_click(page, MSG_SEND, timeout=3000):
        return "error", "boton-enviar-no-disponible"

    await asyncio.sleep(random.uniform(1.0, 1.8))
    # Cerrar el overlay para dejar limpio el siguiente perfil.
    await try_click(page, MSG_CLOSE, timeout=1500)
    return "message_sent", "ok"


async def run(urls: list[str], cookies_path: Path, events_path: Path, *,
             headless: bool, daily_cap: int, min_delay: float, max_delay: float,
             messages: dict[str, str], default_message: str,
             cdp_endpoint: str = "") -> None:
    """Con `cdp_endpoint` el mensaje sale del Chrome ya logueado de ese perfil.

    Es la única vía que aguanta cuando el perfil es de otra persona (los VP que
    prestan su LinkedIn): inyectarle cookies a un Chromium limpio termina en
    checkpoint y el VP recibe la alerta de seguridad, no nosotros.
    """
    total = len(urls)
    sent = 0
    using_cdp = bool(cdp_endpoint)
    async with async_playwright() as pw:
        if using_cdp:
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
        else:
            browser = await pw.chromium.launch(
                headless=headless, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(
                viewport={"width": 1366, "height": 800},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
            await context.add_cookies(load_cookies(cookies_path))
            page = await context.new_page()

        async def _cerrar() -> None:
            # En CDP se cierra solo la pestaña: el Chrome es del VP y sigue vivo.
            try:
                await (page.close() if using_cdp else browser.close())
            except Exception:
                pass

        try:
            await page.goto("https://www.linkedin.com/feed/",
                           wait_until="domcontentloaded", timeout=25000)
        except Exception:
            pass
        if any(x in page.url for x in ("login", "checkpoint", "authwall")):
            pista = ("no hay sesión de LinkedIn en ese Chrome (ábrelo y que el dueño "
                     "del perfil inicie sesión)." if using_cdp
                     else "sesion-invalida. Refresca cookies.")
            write_event(events_path, Event(0, total, page.url, "rate_limited", pista))
            await _cerrar()
            return

        for idx, raw in enumerate(urls, 1):
            url = normalize_url(raw) or raw
            if sent >= daily_cap:
                write_event(events_path, Event(idx, total, url, "skipped",
                                              f"cap-diario ({daily_cap})"))
                continue
            msg = messages.get(raw) or messages.get(url) or default_message
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                status, detail = await send_followup(page, url, msg)
            except Exception as e:
                status, detail = "error", f"perfil:{e.__class__.__name__}"
            write_event(events_path, Event(idx, total, url, status, detail))
            if status == "message_sent":
                sent += 1
            if status == "rate_limited":
                break
            await asyncio.sleep(random.uniform(min_delay, max_delay))
        await _cerrar()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--urls-file", required=True)
    p.add_argument("--events-file", required=True)
    p.add_argument("--messages-file", default="")
    p.add_argument("--cookies", required=True)
    p.add_argument("--headless", action="store_true")
    p.add_argument("--daily-cap", type=int, default=30)
    p.add_argument("--min-delay", type=float, default=6.0)
    p.add_argument("--max-delay", type=float, default=14.0)
    p.add_argument("--default-message", default="")
    p.add_argument("--cdp-endpoint", default="",
                   help="Chrome ya logueado de ese perfil, p.ej. http://127.0.0.1:9335")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    urls = [l.strip() for l in Path(args.urls_file).read_text().splitlines() if l.strip()]
    messages: dict[str, str] = {}
    if args.messages_file and Path(args.messages_file).exists():
        try:
            messages = json.loads(Path(args.messages_file).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            messages = {}
    events_path = Path(args.events_file)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("")
    write_event(events_path, Event(0, len(urls), "", "skipped",
                                  f"arranque follow-ups={len(urls)}"))
    asyncio.run(run(
        urls, Path(args.cookies), events_path,
        headless=args.headless, daily_cap=args.daily_cap,
        min_delay=args.min_delay, max_delay=args.max_delay,
        messages=messages, default_message=args.default_message,
        cdp_endpoint=args.cdp_endpoint))
    return 0


if __name__ == "__main__":
    sys.exit(main())
