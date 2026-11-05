"""Marca en HubSpot quién aceptó la invitación de LinkedIn del outbound.

El cockpit sabe quién aceptó —lo detecta `senales_li` leyendo la lista de
invitaciones enviadas de cada perfil— y ese dato no salía de aquí. En HubSpot no
había forma de responder «¿cuántos de los que invitamos conectaron?», que es la
única métrica que dice si el canal de LinkedIn funciona.

## Tres estados en una casilla

La propiedad es una casilla (`booleancheckbox`), y de eso salen tres lecturas
útiles según cómo se llene:

    vacía  → nunca se le invitó
    false  → se le invitó y todavía no acepta
    true   → aceptó

Escribir el `false` explícito es lo que hace medible la tasa de aceptación: si
solo se marcara el positivo, un invitado que no acepta sería indistinguible de
alguien a quien nunca se tocó, y el denominador desaparece.

## No confundir con «LinkedIn Connections»

HubSpot ya trae `linkedinconnections`, que es el **número de contactos que tiene
esa persona** en su perfil. No tiene nada que ver. De ahí el nombre largo de la
nuestra: en un desplegable de 668 propiedades, dos etiquetas parecidas terminan
en un informe mal armado.

## Lo que este módulo NO hace

No crea contactos en HubSpot. Si alguien aceptó y no está en el CRM, se reporta
y se queda fuera: dar de alta un contacto es una decisión de quién administra el
CRM, no un efecto secundario de un marcador. Hoy son 2 de 14.
"""
from __future__ import annotations

import argparse

import requests

import hubspot_check as hc
import store

BASE = "https://api.hubapi.com"
TIMEOUT = 25
PROPIEDAD = "conexion_linkedin_outbound"
ETIQUETA = "Conexión LinkedIn (outbound Platzi)"


def existe_propiedad() -> tuple[bool, str]:
    """¿Está creada la propiedad? Devuelve (existe, detalle)."""
    tok = hc._token()
    if not tok:
        return False, "HubSpot no está configurado"
    r = requests.get(f"{BASE}/crm/v3/properties/contacts/{PROPIEDAD}",
                     headers=hc._headers(tok), timeout=TIMEOUT)
    if r.status_code == 200:
        d = r.json()
        return True, f"{d.get('label')} ({d.get('type')}/{d.get('fieldType')})"
    if r.status_code == 404:
        return False, (
            f"la propiedad «{PROPIEDAD}» no existe todavía. El token no puede "
            "crearla: le falta el scope crm.schemas.contacts.write. Hay que "
            "añadirlo a la Private App, o crearla a mano en HubSpot "
            "(Ajustes → Propiedades → Crear propiedad, tipo casilla, nombre "
            f"interno «{PROPIEDAD}»).")
    return False, f"HTTP {r.status_code} al consultarla"


def _buscar(email: str) -> dict | None:
    tok = hc._token()
    r = requests.post(
        f"{BASE}/crm/v3/objects/contacts/search", headers=hc._headers(tok),
        json={"filterGroups": [{"filters": [
                  {"propertyName": "email", "operator": "EQ", "value": email}]}],
              "properties": ["email", PROPIEDAD], "limit": 1}, timeout=TIMEOUT)
    if not r.ok:
        return None
    res = r.json().get("results", [])
    return res[0] if res else None


def objetivos() -> list[dict]:
    """Quién debe quedar marcado, y con qué valor.

    Solo gente a la que de verdad se le mandó una invitación: sin eso la casilla
    no significa nada. `aceptado` sale de la actividad `accepted`, que es la que
    escribe `senales_li` cuando ve la transición en la bandeja del perfil.
    """
    return store.fetch_all("""
        SELECT c.id, c.nombre, c.empresa, c.email, c.li_seat,
               EXISTS(SELECT 1 FROM activities a WHERE a.contact_id=c.id
                      AND a.type='accepted') aceptado
        FROM contacts c
        WHERE COALESCE(c.email,'')<>''
          AND EXISTS(SELECT 1 FROM activities a WHERE a.contact_id=c.id
                     AND a.type='invite_sent')
        ORDER BY aceptado DESC, c.nombre""")


def marcar(simular: bool = False, limite: int = 0) -> dict:
    parte: dict = {"objetivos": 0, "marcados": 0, "ya_estaban": 0,
                   "fuera_del_crm": [], "positivos": [], "error": ""}
    ok, detalle = existe_propiedad()
    if not ok:
        parte["error"] = detalle
        return parte

    tok = hc._token()
    objs = objetivos()
    if limite:
        objs = objs[:limite]
    parte["objetivos"] = len(objs)

    for c in objs:
        valor = "true" if c["aceptado"] else "false"
        hs = _buscar(c["email"])
        if hs is None:
            parte["fuera_del_crm"].append({"nombre": c["nombre"],
                                           "empresa": c["empresa"],
                                           "aceptado": bool(c["aceptado"])})
            continue
        if (hs.get("properties") or {}).get(PROPIEDAD) == valor:
            parte["ya_estaban"] += 1
            continue
        if not simular:
            r = requests.patch(f"{BASE}/crm/v3/objects/contacts/{hs['id']}",
                               headers=hc._headers(tok),
                               json={"properties": {PROPIEDAD: valor}},
                               timeout=TIMEOUT)
            if not r.ok:
                parte["error"] = f"HTTP {r.status_code} al escribir en {c['nombre']}"
                return parte
        parte["marcados"] += 1
        if c["aceptado"]:
            parte["positivos"].append({"nombre": c["nombre"], "empresa": c["empresa"],
                                       "seat": c["li_seat"]})
    return parte


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--simular", action="store_true", help="no escribe en HubSpot")
    p.add_argument("--limite", type=int, default=0)
    a = p.parse_args()

    ok, detalle = existe_propiedad()
    print(f"propiedad «{PROPIEDAD}»: {'existe · ' + detalle if ok else 'NO existe'}")
    if not ok:
        print(f"\n✗ {detalle}")
        raise SystemExit(1)

    r = marcar(a.simular, a.limite)
    if r["error"]:
        print(f"\n✗ {r['error']}")
        raise SystemExit(1)
    print(f"\ninvitados con correo: {r['objetivos']} · marcados: {r['marcados']} "
          f"· ya estaban: {r['ya_estaban']}")
    if r["positivos"]:
        print(f"\npositivos (aceptaron) — {len(r['positivos'])}:")
        for x in r["positivos"]:
            print(f"   ✓ {x['nombre'][:26]:28} {x['empresa'][:26]:28} {x['seat']}")
    if r["fuera_del_crm"]:
        print(f"\nno están en HubSpot y por eso no se marcaron ({len(r['fuera_del_crm'])}):")
        for x in r["fuera_del_crm"][:10]:
            print(f"   – {x['nombre'][:26]:28} {x['empresa'][:26]:28}"
                  + ("  (ACEPTÓ)" if x["aceptado"] else ""))


if __name__ == "__main__":
    _cli()
