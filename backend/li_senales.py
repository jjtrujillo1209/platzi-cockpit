"""Lee las dos señales de LinkedIn que faltaban: aceptaciones y respuestas.

Imprime JSON en stdout. Se ejecuta como subproceso —igual que el resto de los
motores— contra el Chrome del asiento, así que lee la bandeja de ESE VP.

## Por qué se parsea el texto y no el DOM

Se intentó por selectores y no aguanta: en esta maqueta las listas de
`/mynetwork` no son `li` dentro de `main`, los botones del top card miden 0×0 y
las clases son hashes que cambian solos. El TEXTO renderizado, en cambio, tiene
una estructura estable y verificable:

    Armando Rodriguez Córdova     ← nombre
    Gerente Corporativo de RR.HH. ← titular
    Sent 2 minutes ago            ← ancla fiable
    Withdraw

y en mensajería:

    Tamara Burgos
    1:10 PM
    1:10 PM
    Tamara: Hola Jess muchas gracias!   ← ENTRANTE (no empieza por «You:»)

Es más feo que un selector y bastante más difícil de romper.

## Dos páginas, no N perfiles

La aceptación se deduce de UNA página por asiento (las invitaciones que siguen
pendientes) en vez de revisitar cada perfil invitado. Menos huella de
automatización sobre la cuenta del VP, que es lo que hay que cuidar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata

from playwright.async_api import async_playwright

URL_ENVIADAS = "https://www.linkedin.com/mynetwork/invitation-manager/sent/"
URL_MENSAJES = "https://www.linkedin.com/messaging/"

# Ancla del bloque de una invitación pendiente, en los dos idiomas.
RE_ENVIADA = re.compile(r"^(sent\s+.*ago|enviad[oa]\s+(hace|hoy|ayer).*)$", re.I)
# Prefijos de un mensaje NUESTRO: si el snippet empieza así, no es una respuesta.
RE_SALIENTE = re.compile(r"^(you|t[úu]|vos)\s*:", re.I)
RE_HORA = re.compile(r"^([01]?\d:\d{2}\s*(am|pm)?|[A-Z][a-z]{2}\s+\d{1,2}|ayer|yesterday|\d{1,2}/\d{1,2}/\d{2,4})$", re.I)


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


MAS_BOTON = re.compile(r"^(show more|mostrar m[áa]s|ver m[áa]s|cargar m[áa]s)$", re.I)


async def _lineas(page) -> list[str]:
    t = await page.evaluate("() => document.body.innerText")
    return [l.strip() for l in (t or "").split("\n") if l.strip()]


# El scroll NO es de la ventana. Medido en la página real: el documento tiene
# scrollHeight == clientHeight (no se mueve, scrollY se queda en 0) y quien
# scrollea es el contenedor de la lista — en esta maqueta, <main>. Por mover la
# ventana el lector leía 15 de 42 invitaciones, y una invitación no leída parece
# una invitación aceptada. Tampoco hay botón «Mostrar más»: es scroll infinito.
_JS_DESPAGINAR = """() => {
  const cand = [...document.querySelectorAll('main, main *, div')].filter(e => {
    if (!e.querySelector('a[href*="/in/"]')) return false;
    const s = getComputedStyle(e);
    return e.scrollHeight > e.clientHeight + 40 && /(auto|scroll)/.test(s.overflowY);
  });
  cand.sort((a, b) => b.querySelectorAll('a[href*="/in/"]').length
                    - a.querySelectorAll('a[href*="/in/"]').length);
  const c = cand[0];
  if (c) { c.scrollTop = c.scrollHeight; }
  else { window.scrollTo(0, document.scrollingElement.scrollHeight); }
  return {contenedor: c ? c.tagName : 'ventana',
          enlaces: document.querySelectorAll('a[href*="/in/"]').length};
}"""

# Los SLUGS son el dato bueno para cruzar: los nombres de LinkedIn no coinciden
# con los de la base («Juan Diaz Cumpalli» vs «JUAN FIDEL DIAZ CUMPALLI») y los
# slugs traen acentos y hasta emojis, pero son únicos y estables.
# Solo los enlaces DENTRO del contenedor de la lista. El overlay de búsqueda de
# LinkedIn («historial reciente») mete 5 perfiles ajenos en cada página y esos
# inflarían el conteo, que es justo lo que usa el seguro de completitud.
_JS_SLUGS = """() => {
  const cand = [...document.querySelectorAll('main, main *, div')].filter(e => {
    if (!e.querySelector('a[href*="/in/"]')) return false;
    const s = getComputedStyle(e);
    return e.scrollHeight > e.clientHeight + 40 && /(auto|scroll)/.test(s.overflowY);
  });
  cand.sort((a, b) => b.querySelectorAll('a[href*="/in/"]').length
                    - a.querySelectorAll('a[href*="/in/"]').length);
  const raiz = cand[0] || document.querySelector('main') || document.body;
  const out = [];
  raiz.querySelectorAll('a[href*="/in/"]').forEach(a => {
    const m = (a.getAttribute('href') || '').match(/\\/in\\/([^\\/?#]+)/);
    if (m) out.push(decodeURIComponent(m[1]).toLowerCase());
  });
  return out;
}"""


async def _texto(page, url: str, scrolls: int = 4, despaginar: bool = False):
    """Carga la página, la despagina y devuelve (líneas, slugs)."""
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(5500)
    objetivo = parse_conteo(await _lineas(page)) if despaginar else 0

    slugs: set[str] = set()
    sin_crecer, ultimo = 0, -1
    # Paciencia alta a propósito: el corte prematuro deja la lista incompleta y
    # eso bloquea la detección de aceptaciones (medido: 36 de 39 con paciencia
    # 5). Sale antes en cuanto alcanza el total declarado, así que la paciencia
    # solo cuesta tiempo cuando de verdad hace falta.
    for _ in range(80 if despaginar else scrolls):
        for x in await page.evaluate(_JS_SLUGS):
            slugs.add(x)
        if objetivo > 0 and len(slugs) >= objetivo:
            break
        info = await page.evaluate(_JS_DESPAGINAR)
        await page.wait_for_timeout(1500)
        n = info.get("enlaces", 0)
        if n == ultimo:
            sin_crecer += 1
            if sin_crecer >= 10:
                break
        else:
            sin_crecer, ultimo = 0, n
    for x in await page.evaluate(_JS_SLUGS):
        slugs.add(x)
    return await _lineas(page), sorted(slugs)


RE_CONTEO = re.compile(r"^(people|personas)\s*\((\d+)\)$", re.I)


def parse_conteo(lineas: list[str]) -> int:
    """Cuántas invitaciones dice LinkedIn que hay pendientes.

    Es el seguro contra listas truncadas: si se parsean menos de las que declara,
    NO se puede deducir ninguna aceptación —los que faltan parecerían aceptados
    sin serlo— y quien llame se entera por este número.
    """
    for l in lineas:
        m = RE_CONTEO.match(l)
        if m:
            return int(m.group(2))
    return -1


def parse_pendientes(lineas: list[str]) -> list[str]:
    """Nombres de las invitaciones que SIGUEN pendientes."""
    fuera, vistos = [], set()
    for i, l in enumerate(lineas):
        if not RE_ENVIADA.match(l) or i < 2:
            continue
        nombre = lineas[i - 2].strip()
        # Descartar cabeceras y ruido: un nombre no trae dos puntos ni paréntesis
        # de conteo, y tiene al menos dos palabras.
        if len(nombre.split()) < 2 or ":" in nombre or re.search(r"\(\d+\)", nombre):
            continue
        if norm(nombre) in vistos:
            continue
        vistos.add(norm(nombre))
        fuera.append(nombre)
    return fuera


def parse_conversaciones(lineas: list[str]) -> list[dict]:
    """Conversaciones con su último mensaje y si vino de ellos."""
    fuera = []
    for i, l in enumerate(lineas):
        if ":" not in l or i < 3:
            continue
        # El snippet va precedido de dos líneas de hora y, antes, del nombre.
        if not (RE_HORA.match(lineas[i - 1]) and RE_HORA.match(lineas[i - 2])):
            continue
        nombre = lineas[i - 3].strip()
        if len(nombre.split()) < 2 or RE_HORA.match(nombre):
            continue
        saliente = bool(RE_SALIENTE.match(l))
        cuerpo = l.split(":", 1)[1].strip()
        fuera.append({"nombre": nombre, "entrante": not saliente,
                      "snippet": cuerpo[:300], "cuando": lineas[i - 1]})
    return fuera


async def run(cdp: str) -> dict:
    async with async_playwright() as pw:
        try:
            b = await pw.chromium.connect_over_cdp(cdp, timeout=20000)
        except Exception as e:
            return {"ok": False, "error": f"no me pude conectar ({e.__class__.__name__})"}
        ctx = b.contexts[0] if b.contexts else await b.new_context()
        page = await ctx.new_page()
        try:
            # `hasta=-1` fuerza el modo despaginado (el objetivo se lee del propio
            # encabezado «People (N)»).
            env, slugs_env = await _texto(page, URL_ENVIADAS, despaginar=True)
            if any("/login" in page.url or "/authwall" in page.url for _ in (1,)):
                return {"ok": False, "error": "la sesión de ese perfil no está viva"}
            pend = parse_pendientes(env)
            msg, _ = await _texto(page, URL_MENSAJES, scrolls=3)
            convs = parse_conversaciones(msg)
            return {"ok": True, "pendientes": pend, "declarados": parse_conteo(env),
                    "slugs_pendientes": slugs_env, "conversaciones": convs,
                    "lineas_enviadas": len(env), "lineas_mensajes": len(msg)}
        except Exception as e:
            return {"ok": False, "error": f"{e.__class__.__name__} al leer las señales"}
        finally:
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
