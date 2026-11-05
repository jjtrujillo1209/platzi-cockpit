"""Auditoría del localizador de «Conectar». Visita perfiles y NO hace clic.

Existe porque el incidente del 2026-08-25 —conexiones salidas hacia perfiles
sugeridos— se habría visto aquí antes de tocar a nadie. La regla que deja: el
localizador se valida contra perfiles reales en modo lectura, y solo cuando la
distribución de estados es la esperada se envía.

Qué reporta por perfil:
  conectar-directo  → hay botón «Conectar» propio a la vista
  conectar-en-menu  → hay que abrir SU menú «Más» (nivel = cuántos ancestros
                      hubo que subir desde el botón que lo nombra)
  pendiente         → ya hay una solicitud enviada
  conectados        → ya son contactos
  sin-ruta          → no se puede probar cuál es su botón → no se enviaría
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import seats
import store
from config import BULK_CONNECT_DIR

if str(BULK_CONNECT_DIR) not in sys.path:
    sys.path.insert(0, str(BULK_CONNECT_DIR))
import runner  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402


def candidatos(seat_id: str, n: int) -> list[dict]:
    return store.fetch_all(
        "SELECT id, nombre, empresa, linkedin FROM contacts "
        "WHERE li_seat=? AND linkedin<>'' AND stage='nuevo' "
        "  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
        "  AND NOT EXISTS (SELECT 1 FROM activities a WHERE a.contact_id=contacts.id "
        "                  AND a.channel='linkedin') "
        "ORDER BY id DESC LIMIT ?", (seat_id, n))


async def auditar(seat, filas: list[dict], abrir_menu: bool = False) -> list[dict]:
    salida = []
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(seat.cdp, timeout=20000)
        ctx = b.contexts[0] if b.contexts else await b.new_context()
        page = await ctx.new_page()
        for f in filas:
            url = runner.normalize_url(f["linkedin"]) or f["linkedin"]
            fila = {"contacto": f["nombre"], "empresa": f["empresa"], "url": url}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                await page.wait_for_timeout(4500)
                if any(x in page.url for x in ("/login", "/checkpoint", "/authwall")):
                    fila["estado"] = "sesion-caida"
                    salida.append(fila)
                    break
                if "/in/" not in page.url:
                    fila["estado"] = "perfil-no-existe"
                    salida.append(fila)
                    continue
                nombre = await runner.nombre_del_perfil(page)
                fila["perfil"] = nombre
                # Misma espera que usa el envío: auditar con otro criterio
                # daría una foto que no corresponde a lo que va a pasar.
                est = await runner.esperar_ruta(page, nombre)
                fila.update({k: est.get(k) for k in
                             ("estado", "label", "nivel", "via", "ancla", "visible")})
                fila["nombrados"] = [d["al"] for d in (est.get("nombrados") or [])][:4]

                # Verificación de la capa 2: abrir SU menú, comprobar que el
                # «Conectar» de dentro lo nombra… y cerrar con Escape sin
                # enviar. Es el ensayo más cercano al envío real que no toca a
                # nadie, y es donde se habría visto el incidente del 25/08.
                if abrir_menu and est.get("estado") == "conectar-en-menu":
                    if await runner.clic_seguro(page):
                        await asyncio.sleep(1.2)
                        m = await runner.estado_del_menu(page, nombre)
                        fila["menu"] = m.get("estado")
                        fila["menu_label"] = m.get("label") or m.get("texto")
                    else:
                        fila["menu"] = "no-pude-abrir"
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.6)
                    abierto = await page.locator("div[role='menu']").count()
                    fila["menu_cerrado"] = abierto == 0

                # Deja el DOM limpio: la marca es nuestra, no de LinkedIn.
                await page.evaluate(
                    "(m) => document.querySelectorAll('['+m+']')"
                    ".forEach(e => e.removeAttribute(m))", runner.MARCA)
            except Exception as e:
                fila["estado"] = f"error:{e.__class__.__name__}"
            salida.append(fila)
            await asyncio.sleep(2.5)
        await page.close()
    return salida


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="perfiles por asiento")
    ap.add_argument("--seat", default="", help="solo este asiento")
    ap.add_argument("--abrir-menu", action="store_true",
                    help="abre el menú para verificarlo y lo cierra con Escape "
                         "(no envía nada)")
    a = ap.parse_args()
    store.init()

    total: dict[str, int] = {}
    for s in seats.en_uso():
        if not s.activo or (a.seat and s.id != a.seat):
            continue
        listo = seats.asegurar_envio(s)
        if not listo.get("ok"):
            print(f"\n### {s.etiqueta}: {listo.get('error')}")
            continue
        filas = candidatos(s.id, a.n)
        print(f"\n### {s.etiqueta} — {len(filas)} perfiles (solo lectura)")
        for r in asyncio.run(auditar(s, filas, abrir_menu=a.abrir_menu)):
            total[r.get("estado", "?")] = total.get(r.get("estado", "?"), 0) + 1
            extra = ""
            if r.get("estado") == "conectar-en-menu":
                via = r.get("via") or ("ancestro" if r.get("nivel") is not None else "?")
                extra = f" · vía {via} desde «{(r.get('ancla') or '')[:44]}»"
            elif r.get("label"):
                extra = f" · «{r['label'][:44]}»"
            print(f"  {str(r.get('estado')):<18} {r['contacto'][:24]:<24}{extra}")
            if r.get("menu"):
                ok = "✓" if r["menu"] == "conectar" else "✗"
                print(f"       menú: {ok} {r['menu']} «{str(r.get('menu_label'))[:52]}» "
                      f"· cerrado: {r.get('menu_cerrado')}")
            if r.get("estado") == "sin-ruta":
                print(f"       botones que lo nombran: {r.get('nombrados')}")

    print("\n=== distribución ===")
    for k, v in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<20} {v}")
    enviables = sum(v for k, v in total.items()
                    if k in ("conectar-directo", "conectar-en-menu"))
    print(f"  → enviables: {enviables} de {sum(total.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
