"""Teléfono de los prospectos ya conectados, vía Amplemarket.

A un conectado se le puede llamar: aceptó la invitación, sabe quién es Platzi y
el siguiente paso del método es una llamada, no otro mensaje. Pero la base los
tiene sin número —entraron por lotes donde el teléfono no se reveló para ahorrar
créditos— así que la llamada no se puede marcar. Esto los completa.

## La llave es el LinkedIn, no el correo

`enrich_people` acepta `linkedin_url`, `email` o `name`+`company_domain`. Medido:
por LinkedIn revela el 77% y por correo el 36%. Estos contactos vienen del canal
de LinkedIn, así que todos tienen esa llave y es la buena.

Los resultados se emparejan **por `linkedin_url`, nunca por posición**: la API
puede devolverlos en otro orden o saltarse uno, y leerlo por índice le pega el
teléfono de una persona a otra. El correo, si viniera, vive en `result.email` —
no en `result.person`.

## Se pide teléfono y NO correo

`reveal_email=False` a propósito: los 12 ya tienen correo, y cada revelado
cuesta un crédito del pozo que justamente está vacío.

## Los tres pozos de Amplemarket, y el 402 que miente

La cuenta tiene tres bolsas separadas —correo, teléfono y job-change alerts— y
el `402 insufficient_credits` no dice cuál se agotó. El 8-sep-2026 el pozo de
teléfono tenía 1.735 créditos disponibles y el revelado igual fallaba: el paso
*find* del enrich cuesta 0,5 y se cobra del pozo de **correo**, que estaba en 0.
O sea que sin correo no hay teléfono aunque el teléfono esté pagado.

Por eso `costo()` reporta las dos bolsas y el 402 se traduce a esa explicación
en vez de dejar «insufficient_credits» a secas, que manda a recargar la bolsa
equivocada.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import am_budget
import contacts
import store

# Se importa el cliente ya probado en producción en vez de copiarlo. La ruta se
# resuelve subiendo hasta "Ai programas", igual que hace modulo-prospeccion.
_AQUI = Path(__file__).resolve()


def _cargar_cliente():
    for anc in _AQUI.parents:
        cand = anc / "prospeccion-2000" / "amplemarket.py"
        if cand.is_file():
            if str(cand.parent) not in sys.path:
                sys.path.insert(0, str(cand.parent))
            import amplemarket  # type: ignore
            return amplemarket
    raise RuntimeError(
        "No se encontró PLATZI/prospeccion-2000/amplemarket.py — este runner "
        "reutiliza ese cliente y no trae uno propio.")


STAGE_POR_DEFECTO = "conectado"
LOTE = 25          # el polling de un lote grande se hace eterno y opaco
ESPERA = 240.0


def objetivos(stage: str = STAGE_POR_DEFECTO, limite: int = 0) -> list[dict]:
    """Contactos de esa etapa con LinkedIn y sin teléfono."""
    filas = store.fetch_all(
        "SELECT id, nombre, empresa, cargo, email, linkedin FROM contacts "
        "WHERE stage=? AND COALESCE(celular,'')='' AND COALESCE(linkedin,'')<>'' "
        "ORDER BY id", (stage,))
    return filas[:limite] if limite else filas


def costo(n: int) -> dict:
    """Lo que cuesta revelar n teléfonos, por bolsa. La tarifa vive en
    `am_budget` para que no haya dos definiciones del mismo precio."""
    return {**am_budget.costo_telefonos(n),
            "nota": "el find de 0,5 sale del pozo de CORREO; sin saldo ahí no "
                    "se puede revelar teléfono aunque el pozo de teléfono esté lleno"}


def _telefono_util(result: dict) -> tuple[str, str]:
    """Mejor número del perfil revelado → (numero, tipo).

    Descarta los marcados `is_wrong_number`: Amplemarket los devuelve igual y
    guardarlos es peor que no tener nada — alguien va a marcarlos.
    Prefiere el móvil: a un conmutador no se le llama para prospectar.
    """
    nums = [p for p in (result.get("phone_numbers") or [])
            if (p.get("number") or "").strip() and not p.get("is_wrong_number")]
    if not nums:
        return "", ""
    movil = next((p for p in nums if (p.get("type") or "").lower() == "mobile"), None)
    elegido = movil or nums[0]
    # Se conserva el formato de Amplemarket (E.164 con espacios): es el mismo de
    # las 711 filas que ya tienen número, y el marcador del front ya lo limpia.
    return (elegido.get("number") or "").strip(), (elegido.get("type") or "").strip()


def correr(stage: str = STAGE_POR_DEFECTO, limite: int = 0,
           simular: bool = False, forzar: bool = False) -> dict:
    """Revela y escribe. Devuelve el parte, sin lanzar."""
    objs = objetivos(stage, limite)
    parte: dict = {"stage": stage, "objetivos": len(objs), "costo": costo(len(objs)),
                   "revelados": 0, "escritos": 0, "sin_numero": [], "detalle": [],
                   "error": ""}
    if not objs:
        parte["error"] = f"no hay contactos en «{stage}» con LinkedIn y sin teléfono"
        return parte
    parte["saldo"] = am_budget.status()
    alcanza, motivo = am_budget.alcanza_para_telefonos(len(objs))
    parte["alcanza"] = alcanza
    parte["saldo_motivo"] = motivo

    if simular:
        parte["detalle"] = [{"nombre": c["nombre"], "empresa": c["empresa"],
                             "linkedin": c["linkedin"]} for c in objs]
        return parte

    # Negarse aquí ahorra el viaje al 402 y, sobre todo, dice cuál pozo falla.
    # El saldo es manual, así que puede estar viejo: `--igual` lo salta y deja
    # que la API tenga la última palabra.
    if not alcanza and not forzar:
        parte["error"] = (f"el saldo cargado no alcanza para {len(objs)} "
                          f"teléfonos — {motivo}. Si ya recargaste, corre con "
                          f"--igual o actualiza el saldo.")
        return parte

    am = _cargar_cliente()
    por_slug = {contacts._norm_li(c["linkedin"]): c for c in objs}
    altas: list[dict] = []

    for i in range(0, len(objs), LOTE):
        tramo = objs[i:i + LOTE]
        leads = [{"linkedin_url": c["linkedin"]} for c in tramo]
        try:
            res = am.enrich_people(leads, reveal_email=False, reveal_phone=True,
                                   max_wait=ESPERA)
        except Exception as e:
            msg = str(e)
            if "402" in msg or "insufficient_credits" in msg:
                parte["error"] = (
                    "Amplemarket responde 402. El pozo de CORREO es el que está "
                    "en cero y el find de 0,5 se cobra de ahí, así que bloquea el "
                    f"teléfono aunque quede saldo de teléfono. Hacen falta "
                    f"{costo(len(objs))['correo']} créditos de correo y "
                    f"{costo(len(objs))['telefono']} de teléfono para estos "
                    f"{len(objs)}.")
            else:
                parte["error"] = f"{e.__class__.__name__}: {msg[:200]}"
            return parte

        for r in res:
            # Emparejar por linkedin_url, nunca por posición (ver encabezado).
            c = por_slug.get(contacts._norm_li(r.get("linkedin_url") or ""))
            if c is None:
                continue
            num, tipo = _telefono_util(r.get("result") or {})
            if not num:
                parte["sin_numero"].append({"nombre": c["nombre"],
                                            "empresa": c["empresa"],
                                            "estado": r.get("status", "?")})
                continue
            parte["revelados"] += 1
            altas.append({"linkedin": c["linkedin"], "celular": num})
            parte["detalle"].append({"nombre": c["nombre"], "empresa": c["empresa"],
                                     "telefono": num, "tipo": tipo})

    intentados = parte["revelados"] + len(parte["sin_numero"])
    if intentados:
        am_budget.consume(correo=intentados * am_budget.COSTO_FIND,
                          telefono=parte["revelados"] * am_budget.COSTO_TELEFONO)
        parte["saldo"] = am_budget.status()

    if altas:
        # enrich_contacts empareja por slug de LinkedIn y NO pisa un valor ya
        # presente: si alguien consiguió el número a mano mientras esto corría,
        # gana el de la mano.
        r = contacts.enrich_contacts(altas)
        parte["escritos"] = r.get("updated", 0)
        parte["no_encontrados"] = r.get("not_found", [])
    return parte


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--stage", default=STAGE_POR_DEFECTO,
                   help=f"etapa a completar (por defecto {STAGE_POR_DEFECTO})")
    p.add_argument("--limite", type=int, default=0, help="tope de contactos")
    p.add_argument("--simular", action="store_true",
                   help="lista a quién le pediría el número y cuánto cuesta, sin gastar")
    p.add_argument("--igual", action="store_true",
                   help="corre aunque el saldo cargado diga que no alcanza "
                        "(úsalo si ya recargaste y no has actualizado am_budget)")
    a = p.parse_args()

    r = correr(a.stage, a.limite, a.simular, a.igual)
    c = r["costo"]
    print(f"etapa «{r['stage']}» · {r['objetivos']} sin teléfono con LinkedIn")
    print(f"costo: {c['correo']:g} créditos de correo + {c['telefono']:g} de teléfono")
    saldo = r.get("saldo") or {}
    if saldo.get("updated_at"):
        pz = saldo["pozos"]
        print(f"saldo cargado ({saldo['updated_at']}): "
              f"correo {pz['correo']['disponibles']:g} · "
              f"teléfono {pz['telefono']['disponibles']:g}")
    if r["error"]:
        print(f"\n✗ {r['error']}")
        raise SystemExit(1)
    if a.simular:
        for d in r["detalle"]:
            print(f"  · {d['nombre']} — {d['empresa']}")
        print("\n(simulación: no se gastó ni se escribió nada)")
        return
    print(f"\nrevelados {r['revelados']} · escritos en la base {r['escritos']}")
    for d in r["detalle"]:
        print(f"  ✓ {d['nombre']:26} {d['empresa'][:24]:26} {d['telefono']}  ({d['tipo']})")
    for s in r["sin_numero"]:
        print(f"  – {s['nombre']:26} {s['empresa'][:24]:26} sin número ({s['estado']})")


if __name__ == "__main__":
    _cli()
