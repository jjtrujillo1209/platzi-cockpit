"""Lee quién tiene la sesión abierta en un Chrome dado. Imprime JSON en stdout.

Se ejecuta como subproceso desde `li_login.capturar()` por la misma razón que
los demás motores: Playwright vive fuera del proceso de la API, así que una
página colgada no cuelga el backend.

Va a `linkedin.com/in/me/`, que redirige al perfil de quien está logueado. De
ahí salen las tres cosas que el cockpit necesita para dejar de preguntar:
el nombre real, el titular (para saber quién es en la cola de aprobación) y la
URL del perfil (para no confundir dos asientos de la misma persona).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys

from playwright.async_api import async_playwright

FUERA = ("/login", "/checkpoint", "/authwall", "/uas/login")

# El nombre sale del TÍTULO del documento, no del DOM: la maqueta nueva del
# perfil no tiene un solo `h1` —comprobado contra la página real— y las clases
# de LinkedIn cambian sin avisar, mientras que «Nombre | LinkedIn» lleva años
# igual y lo pinta el servidor. Los selectores quedan como respaldo.
SEL_NOMBRE = ["main h1", "h1.text-heading-xlarge", ".pv-text-details__left-panel h1",
              "section.artdeco-card h1", "h1"]
SEL_TITULAR = ["section:first-of-type p",
               "main .text-body-medium.break-words",
               ".pv-text-details__left-panel .text-body-medium",
               "main div.text-body-medium"]
SUFIJO_TITULO = " | LinkedIn"


# LinkedIn le mete el contador de no leídos al título: «(30) Jefferson Vargas
# Camacho | LinkedIn». Quitar solo el sufijo dejaba el «(30) » pegado al nombre,
# y ese nombre es el que firma en la UI, en el correo diario y en el chequeo de
# «este LinkedIn ya está en otro asiento». Se ve solo cuando el VP tiene
# notificaciones sin leer, así que los dos primeros asientos no lo destaparon.
_BADGE = re.compile(r"^\(\d+\+?\)\s*")


async def _nombre_del_titulo(page) -> str:
    """«(30) Jess Harris | LinkedIn» → «Jess Harris»."""
    t = (await page.title() or "").strip()
    if not t.endswith(SUFIJO_TITULO):
        return ""
    nombre = _BADGE.sub("", t[: -len(SUFIJO_TITULO)].strip()).strip()
    # Deslogueado el título es solo «LinkedIn», y en algunas pantallas trae
    # texto de producto: nada de eso es el nombre de una persona.
    if not nombre or nombre.lower().startswith(("linkedin", "iniciar sesión", "sign")):
        return ""
    return " ".join(nombre.split())


async def _primer_texto(page, selectores: list[str]) -> str:
    for sel in selectores:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                t = (await loc.inner_text()).strip()
                if t:
                    return " ".join(t.split())
        except Exception:
            continue
    return ""


async def run(cdp: str) -> dict:
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp, timeout=15000)
        except Exception as e:
            return {"ok": False, "error": f"no me pude conectar a la ventana ({e.__class__.__name__})"}
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto("https://www.linkedin.com/in/me/",
                            wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            if any(x in page.url for x in FUERA):
                return {"ok": False, "error": "esa ventana no tiene sesión de LinkedIn iniciada"}
            nombre = await _nombre_del_titulo(page) or await _primer_texto(page, SEL_NOMBRE)
            titular = (await _primer_texto(page, SEL_TITULAR))[:200]
            url = page.url.split("?")[0].rstrip("/")
            if "/in/" not in url:
                # /in/me redirige al perfil propio; si no acabamos en un perfil,
                # lo que hay al frente no es una sesión utilizable.
                return {"ok": False, "error": "no llegué al perfil: revisa la sesión de esa ventana"}
            if not nombre:
                # Sin nombre no vale la pena guardar: el asiento quedaría
                # "configurado" con un hueco y nadie sabría de quién es.
                return {"ok": False, "error": "entré al perfil pero no pude leer el nombre; "
                                              "vuelve a intentar en unos segundos"}
            return {"ok": True, "nombre": nombre, "titular": titular, "url": url}
        except Exception as e:
            return {"ok": False, "error": f"{e.__class__.__name__} al abrir el perfil"}
        finally:
            # Solo se cierra la pestaña que abrimos: el Chrome es del VP.
            try:
                await page.close()
            except Exception:
                pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cdp", required=True)
    a = p.parse_args()
    res = asyncio.run(run(a.cdp))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
