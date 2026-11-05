"""Presupuesto local de créditos de Amplemarket — tres pozos, no uno.

Igual que Apollo, Amplemarket **no expone el saldo por API**: `/credits`,
`/account`, `/me`, `/usage`, `/account/credits` y `/subscription` dan 404, y
`/account-info` solo devuelve el id y el nombre de la cuenta. La única forma de
enterarse por API es intentar un revelado y comerse el 402 — que es tarde y, lo
peor, **miente sobre cuál pozo se agotó**.

## Por qué tres pozos y no un número

Amplemarket cobra de tres bolsas separadas (Settings → Credits):

    Email Credits          22.009,5 de 22.000   →      0 disponibles
    Phone Credits           5.150   de  6.885   →  1.735 disponibles
    Job Change Alerts         588   de    500   →    −88 disponibles

Y el `402 insufficient_credits` es el mismo texto para las tres. Eso llevó a
concluir que "Amplemarket está sin créditos" cuando en realidad había 1.735
teléfonos pagados sin usar.

## La trampa que hace falta modelar

El paso *find* del enrich cuesta **0,5 y se cobra del pozo de CORREO**, incluso
cuando solo se pide teléfono. Así que revelar N teléfonos necesita:

    N/2 del pozo de correo   +   N del pozo de teléfono

y con el correo en cero **no sale ni un teléfono** aunque el pozo de teléfono
esté lleno. Por eso `alcanza_para_telefonos()` mira las dos bolsas: un chequeo
que solo mirara la de teléfono daría luz verde y el job se estrellaría contra
un 402 después de que el usuario ya lo lanzó.

Los números los carga una persona a mano tras mirar el dashboard —misma
mecánica que `apollo_budget`—, así que pueden quedar viejos: esto sirve para
**negarse temprano y explicar bien**, no como verdad absoluta. El manejo del 402
sigue en su sitio como red de seguridad.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "am_budget.json"
_LOCK = threading.Lock()

POZOS = ("correo", "telefono", "job_change")

# Lo que cuesta un revelado, por unidad. El find se cobra del pozo de correo
# aunque no se revele ningún correo — ver el encabezado.
COSTO_FIND = 0.5
COSTO_TELEFONO = 1.0
COSTO_CORREO = 1.0


def _vacio() -> dict:
    return {p: {"total": 0.0, "usados": 0.0} for p in POZOS} | {"updated_at": ""}


def _load() -> dict:
    if not _PATH.exists():
        return _vacio()
    try:
        d = json.loads(_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _vacio()
    out = _vacio()
    for p in POZOS:
        v = d.get(p) or {}
        try:
            out[p] = {"total": float(v.get("total", 0)),
                      "usados": float(v.get("usados", 0))}
        except (TypeError, ValueError):
            pass
    out["updated_at"] = d.get("updated_at", "")
    return out


def _save(d: dict) -> None:
    _PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def status() -> dict:
    """Estado de los tres pozos. `disponibles` puede ser negativo: el dashboard
    los muestra así (Job Change Alerts en −88) y aplanarlo a 0 esconde que se
    pasó del límite, que es justo lo que hay que ver."""
    d = _load()
    out = {"updated_at": d["updated_at"], "pozos": {}}
    for p in POZOS:
        total, usados = d[p]["total"], d[p]["usados"]
        out["pozos"][p] = {"total": total, "usados": usados,
                           "disponibles": total - usados}
    return out


def set_pozos(correo: tuple[float, float] | None = None,
              telefono: tuple[float, float] | None = None,
              job_change: tuple[float, float] | None = None) -> dict:
    """Carga lo que dice el dashboard, como `(total, usados)` por pozo.

    Se piden los dos números en vez del saldo porque es lo que la pantalla
    muestra («used 5150.0 of 6885.0») y porque el saldo derivado deja ver
    cuándo el uso pasó del total, que es un estado real en esa cuenta.
    """
    with _LOCK:
        d = _load()
        for nombre, par in (("correo", correo), ("telefono", telefono),
                            ("job_change", job_change)):
            if par is None:
                continue
            total, usados = par
            d[nombre] = {"total": float(total), "usados": float(usados)}
        d["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save(d)
    return status()


def consume(correo: float = 0.0, telefono: float = 0.0) -> dict:
    """Descuenta lo gastado por una corrida real."""
    with _LOCK:
        d = _load()
        d["correo"]["usados"] += float(correo)
        d["telefono"]["usados"] += float(telefono)
        d["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save(d)
    return status()


def costo_telefonos(n: int) -> dict:
    """Lo que cuesta revelar n teléfonos, por pozo."""
    return {"correo": n * COSTO_FIND, "telefono": n * COSTO_TELEFONO}


def alcanza_para_telefonos(n: int) -> tuple[bool, str]:
    """¿Alcanza el saldo cargado para n teléfonos? (puede, motivo).

    Mira las DOS bolsas a propósito. Si nunca se cargó nada, deja pasar: un
    presupuesto en blanco no es lo mismo que un presupuesto agotado, y negarse
    por no saber convertiría este archivo en un requisito para operar.
    """
    st = status()
    if not st["updated_at"]:
        return True, ("no hay saldo cargado (am_budget está en blanco): se "
                      "intenta y manda lo que responda la API")
    c = costo_telefonos(n)
    faltan = []
    for pozo in ("correo", "telefono"):
        disp = st["pozos"][pozo]["disponibles"]
        if disp < c[pozo]:
            faltan.append(f"{pozo}: hacen falta {c[pozo]:g} y hay {disp:g}")
    if not faltan:
        return True, "alcanza"
    extra = ""
    if any(f.startswith("correo") for f in faltan) and \
            st["pozos"]["telefono"]["disponibles"] >= c["telefono"]:
        extra = (" — ojo: el pozo de teléfono SÍ alcanza; lo que bloquea es el "
                 "find de 0,5 que se cobra del de correo")
    return False, "; ".join(faltan) + extra
