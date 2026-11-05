"""Respalda las cookies de LinkedIn de un asiento. Imprime JSON en stdout.

**Esto no es la forma de entrar, es el paracaídas.** La sesión de verdad vive en
el perfil de Chrome del asiento (`data/chrome/<asiento>/`) y sobrevive a cerrar
la ventana y a reiniciar la máquina. Las cookies se guardan por si esa carpeta se
pierde: un disco que se llena, un `rm -rf` con prisa, una máquina nueva.

Y hay que decirlo donde se pueda leer: restaurar desde cookies **no es
equivalente**. Metidas en un navegador limpio, LinkedIn ve una huella nueva y
manda a un checkpoint de seguridad — que le llega al VP, no a nosotros. Sirve
para un rescate con la máquina de siempre, no para montar la sesión en otra
parte ni para evitarle el trámite a un VP nuevo.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DOMINIOS = ("linkedin.com", ".linkedin.com", "www.linkedin.com")


async def run(cdp: str, out: Path) -> dict:
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp, timeout=15000)
        except Exception as e:
            return {"ok": False, "error": f"no me pude conectar a la ventana ({e.__class__.__name__})"}
        if not browser.contexts:
            return {"ok": False, "error": "la ventana no tiene contexto de navegación"}
        todas = await browser.contexts[0].cookies()

    cookies = [c for c in todas if any(d in (c.get("domain") or "") for d in DOMINIOS)]
    li_at = next((c for c in cookies if c.get("name") == "li_at"), None)
    if not li_at:
        # Sin li_at no hay sesión que respaldar: guardar el archivo igual dejaría
        # un respaldo que parece bueno y no sirve para nada.
        return {"ok": False, "error": "esa ventana no tiene sesión de LinkedIn (falta li_at)"}

    vence = ""
    exp = li_at.get("expires")
    if exp and exp > 0:
        try:
            vence = datetime.fromtimestamp(int(exp)).date().isoformat()
        except (ValueError, OSError, TypeError):
            vence = ""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    # Esto es la sesión de LinkedIn de otra persona: con permisos por defecto
    # (0644) cualquier cuenta de la máquina puede leerla y entrar como ella.
    out.chmod(0o600)
    out.parent.chmod(0o700)
    return {"ok": True, "n": len(cookies), "li_at_vence": vence,
            "de_sesion": not bool(vence), "archivo": str(out)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cdp", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    res = asyncio.run(run(a.cdp, Path(a.out)))
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
