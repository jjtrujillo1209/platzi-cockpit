"""Cruce completo contra HubSpot: por persona Y por empresa.

`hubspot_check.check(email, domain)` acepta un dominio y **no lo usa**: solo
busca el contacto por correo. Por eso su veredicto «No está en HubSpot — apto
para outbound» puede ser cierto del decisor y falso de su empresa, y por eso
Dolphin Discovery —`customer` en HubSpot— recibió dos invitaciones en frío.

Aquí se hacen las dos preguntas:

  · **por persona**  → ¿este correo está en el CRM? (lo que ya hacía)
  · **por empresa**  → ¿este dominio está en el CRM, y como qué?

Y hay un segundo agujero, aparte del dominio: `conexiones.candidatos()` excluye
solo a los marcados `rojo`, así que un contacto **nunca cruzado** —veredicto en
blanco— pasa el filtro y se le invita. Este script llena esos blancos, que es
lo que convierte la guarda en una guarda de verdad.

## El veredicto de la empresa manda sobre el de la persona

Si la empresa es cliente o tiene un deal abierto, sus decisores pasan a `rojo`
aunque el correo no esté en el CRM: escribirle en frío a alguien de una cuenta
que ya es cliente es el error que la guarda existe para evitar, y de quién sea
el correo no cambia eso. El motivo queda escrito con la empresa nombrada, para
que se pueda distinguir un rojo heredado de uno propio.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

import hubspot_check as hc
import reparto
import store

BASE = "https://api.hubapi.com"
TIMEOUT = 20
PAUSA = 0.12                 # ~8 req/s: el límite de una Private App es 100/10s
ESTADO = Path(__file__).resolve().parent / "data" / "validar_hubspot.json"

# Correos que no dicen nada de la empresa.
GENERICOS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "hotmail.es",
             "yahoo.com.mx", "live.com", "icloud.com", "me.com", "aol.com"}

ETAPAS_ROJAS = {"customer"}
ETAPAS_CRM = {"lead", "marketingqualifiedlead", "salesqualifiedlead",
              "opportunity", "other"}


def _dominio(email: str) -> str:
    d = (email or "").split("@")[-1].strip().lower()
    return "" if not d or d in GENERICOS else d


def empresas_de_la_base(solo_activos: bool = True) -> dict[str, dict]:
    """clave de empresa → {empresa, dominio, contactos, invitados}."""
    etapas = ("nuevo", "contactado", "conectado") if solo_activos else None
    sql = ("SELECT c.id, c.nombre, c.empresa, c.email, c.stage, c.li_seat, "
           " EXISTS(SELECT 1 FROM activities a WHERE a.contact_id=c.id "
           "        AND a.type='invite_sent') invitado "
           "FROM contacts c WHERE COALESCE(c.empresa,'')<>''")
    params: tuple = ()
    if etapas:
        sql += " AND c.stage IN (%s)" % ",".join("?" * len(etapas))
        params = etapas
    out: dict[str, dict] = {}
    for r in store.fetch_all(sql, params):
        k = reparto.clave_empresa(r["empresa"])
        if not k:
            continue
        g = out.setdefault(k, {"empresa": r["empresa"], "dominios": {},
                               "contactos": [], "invitados": 0})
        g["contactos"].append(dict(r))
        g["invitados"] += 1 if r["invitado"] else 0
        d = _dominio(r["email"])
        if d:
            g["dominios"][d] = g["dominios"].get(d, 0) + 1
    for g in out.values():
        # El dominio de quien ya fue invitado manda: es la entidad con la que de
        # verdad estamos hablando (la filial, no la corporación global).
        pref = {}
        for c in g["contactos"]:
            if c["invitado"]:
                d = _dominio(c["email"])
                if d:
                    pref[d] = pref.get(d, 0) + 1
        fuente = pref or g["dominios"]
        g["dominio"] = max(fuente, key=lambda d: (fuente[d], -len(d))) if fuente else ""
    return out


def _deals_abiertos_de_empresa(tok: str, company_id: str) -> int:
    """Deals ABIERTOS de la empresa, no todos.

    `num_associated_deals` cuenta cerrados también, y con eso Procolombia salía
    roja por un deal que podía ser un cerrado-perdido de hace años. Bloquear una
    cuenta por eso es tan malo como no bloquear a un cliente: se pierde una
    oportunidad legítima. Misma mecánica que `hubspot_check._open_deals_for_contact`.
    """
    try:
        r = requests.get(
            f"{BASE}/crm/v4/objects/companies/{company_id}/associations/deals",
            headers=hc._headers(tok), params={"limit": 100}, timeout=TIMEOUT)
        if r.status_code != 200:
            return 0
        ids = [x.get("toObjectId") for x in r.json().get("results", []) if x.get("toObjectId")]
        if not ids:
            return 0
        rd = requests.post(
            f"{BASE}/crm/v3/objects/deals/batch/read", headers=hc._headers(tok),
            json={"properties": ["hs_is_closed", "dealstage", "dealname"],
                  "inputs": [{"id": str(i)} for i in ids[:100]]}, timeout=TIMEOUT)
        if rd.status_code != 200:
            return 0
        return sum(1 for d in rd.json().get("results", [])
                   if str(d.get("properties", {}).get("hs_is_closed")).lower() == "false")
    except requests.RequestException:
        return 0


def empresa_en_hubspot(dominio: str) -> dict:
    """¿Está el dominio en HubSpot, y como qué? Una sola llamada."""
    tok = hc._token()
    if not tok or not dominio:
        return {"ok": False, "error": "sin token o sin dominio"}
    try:
        r = requests.post(
            f"{BASE}/crm/v3/objects/companies/search", headers=hc._headers(tok),
            json={"filterGroups": [{"filters": [
                      {"propertyName": "domain", "operator": "EQ", "value": dominio}]}],
                  "properties": ["name", "domain", "lifecyclestage",
                                 "hubspot_owner_id", "num_associated_deals"],
                  "limit": 1}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        res = r.json().get("results", [])
    except requests.RequestException as e:
        return {"ok": False, "error": f"red: {e.__class__.__name__}"}

    if not res:
        return {"ok": True, "existe": False, "veredicto": "verde",
                "motivo": "el dominio no está en HubSpot"}
    p = res[0].get("properties", {})
    etapa = (p.get("lifecyclestage") or "").strip().lower()
    todos = int(p.get("num_associated_deals") or 0)
    nombre = p.get("name") or dominio
    # Solo se pagan las dos llamadas extra si hay algún deal que mirar.
    deals = _deals_abiertos_de_empresa(tok, res[0].get("id")) if todos else 0
    if etapa in ETAPAS_ROJAS:
        v, m = "rojo", f"«{nombre}» ya es CLIENTE en HubSpot"
    elif deals > 0:
        v, m = "rojo", f"«{nombre}» tiene {deals} deal(s) ABIERTO(s) en HubSpot"
    elif etapa in ETAPAS_CRM:
        v, m = "amarillo", f"«{nombre}» está en el CRM como {hc._LS_LABEL.get(etapa, etapa)}"
    else:
        v, m = "verde", f"«{nombre}» está en HubSpot pero sin actividad de ventas"
    return {"ok": True, "existe": True, "veredicto": v, "motivo": m,
            "nombre": nombre, "etapa": etapa, "deals": deals, "deals_total": todos,
            "owner": p.get("hubspot_owner_id") or ""}


def _veredicto_empresa(p: dict, deals: int) -> dict:
    """Arma el veredicto de empresa a partir de propiedades ya traídas."""
    etapa = (p.get("lifecyclestage") or "").strip().lower()
    nombre = p.get("name") or p.get("domain") or ""
    if etapa in ETAPAS_ROJAS:
        v, m = "rojo", f"«{nombre}» ya es CLIENTE en HubSpot"
    elif deals > 0:
        v, m = "rojo", f"«{nombre}» tiene {deals} deal(s) ABIERTO(s) en HubSpot"
    elif etapa in ETAPAS_CRM:
        v, m = "amarillo", f"«{nombre}» está en el CRM como {hc._LS_LABEL.get(etapa, etapa)}"
    else:
        v, m = "verde", f"«{nombre}» está en HubSpot pero sin actividad de ventas"
    return {"ok": True, "existe": True, "veredicto": v, "motivo": m,
            "nombre": nombre, "etapa": etapa, "deals": deals,
            "deals_total": int(p.get("num_associated_deals") or 0),
            "owner": p.get("hubspot_owner_id") or ""}


def empresas_en_lote(dominios: list[str]) -> dict[str, dict]:
    """dominio → mismo veredicto que `empresa_en_hubspot`, de a 100 por llamada.

    `empresa_en_hubspot` cuesta 1,41 s de media (medido 24-sep-2026), así que
    los 673 dominios de la base eran ~15 min de reloj. Con `domain IN` son tres
    llamadas por cada 100 dominios y los deals se piden una sola vez para todas
    las empresas que tengan alguno.
    """
    tok = hc._token()
    limpios = sorted({(d or "").strip().lower() for d in dominios if (d or "").strip()})
    if not tok or not limpios:
        return {}
    hallados: dict[str, dict] = {}
    for grupo in hc._chunks(limpios):
        after = None
        while True:
            cuerpo = {"filterGroups": [{"filters": [
                          {"propertyName": "domain", "operator": "IN", "values": grupo}]}],
                      "properties": ["name", "domain", "lifecyclestage",
                                     "hubspot_owner_id", "num_associated_deals"],
                      "limit": hc.LOTE}
            if after:
                cuerpo["after"] = after
            try:
                r = requests.post(f"{BASE}/crm/v3/objects/companies/search",
                                  headers=hc._headers(tok), json=cuerpo, timeout=TIMEOUT * 2)
            except requests.RequestException as e:
                return {d: {"ok": False, "error": f"red: {e.__class__.__name__}"}
                        for d in limpios if d not in hallados}
            if r.status_code != 200:
                break
            j = r.json()
            for c in j.get("results", []):
                pr = dict(c.get("properties", {}))
                pr["id"] = c.get("id")
                dom = (pr.get("domain") or "").strip().lower()
                # Una empresa puede matchear un dominio del grupo aunque su
                # propiedad `domain` venga con www. o vacía: se ancla por el
                # valor pedido cuando el devuelto no está en la lista.
                if dom.removeprefix("www.") in grupo:
                    hallados.setdefault(dom.removeprefix("www."), pr)
            after = (j.get("paging", {}).get("next", {}) or {}).get("after")
            if not after:
                break

    con_deals = [pr["id"] for pr in hallados.values()
                 if int(pr.get("num_associated_deals") or 0) and pr.get("id")]
    deals = hc.deals_abiertos_en_lote(tok, "companies", con_deals)

    out: dict[str, dict] = {}
    for d in limpios:
        pr = hallados.get(d)
        if not pr:
            out[d] = {"ok": True, "existe": False, "veredicto": "verde",
                      "motivo": "el dominio no está en HubSpot"}
        else:
            out[d] = _veredicto_empresa(pr, deals.get(str(pr.get("id")), 0))
    return out


def _guardar(cid: int, veredicto: str, motivo: str) -> None:
    """Escribe el veredicto con `store.set_hubspot`, que es el que sabe hacerlo.

    La primera versión usaba `set_contact_fields`, que **solo** aplica las
    columnas de `EDITABLE_CONTACT_FIELDS` —nombre, empresa, cargo, correo,
    celular, linkedin, campaign— e **ignora el resto en silencio** devolviendo
    False. Así se «escribieron» 81 veredictos que nunca llegaron a la base: el
    parte decía que sí y la columna seguía vacía. Ignorar ese False fue el error.
    """
    # force: este cruce SÍ puede degradar, es el más informado de los dos.
    store.set_hubspot(cid, veredicto, motivo[:240], force=True)


def correr(simular: bool = False, limite: int = 0,
           solo_activos: bool = True) -> dict:
    grupos = empresas_de_la_base(solo_activos)
    claves = sorted(grupos, key=lambda k: -grupos[k]["invitados"])
    if limite:
        claves = claves[:limite]

    parte = {"empresas": len(claves), "rojas": [], "amarillas": [],
             "sin_dominio": [], "personas_rojas": [], "personas_amarillas": [],
             "escritos": 0, "errores": []}

    # Una sola pasada de red por TODOS los dominios (de a 100) antes del bucle:
    # el bucle ya no toca HubSpot para empresas, solo lee este diccionario.
    doms = [grupos[k]["dominio"] for k in claves if grupos[k]["dominio"]]
    # Los veredictos ya escritos, de una sola consulta a la base en vez de un
    # SELECT por contacto dentro del bucle.
    ya_con_veredicto = {r["id"]: r["v"] for r in store.fetch_all(
        "SELECT id, COALESCE(hubspot_verdict,'') v FROM contacts", ()) if r["v"]}
    pendientes: list[tuple[dict, str]] = []

    print(f"  consultando {len(doms)} dominios en lotes de {hc.LOTE}…", flush=True)
    emp_lote = empresas_en_lote(doms)

    for i, k in enumerate(claves, 1):
        g = grupos[k]
        dom = g["dominio"]
        if not dom:
            parte["sin_dominio"].append({"empresa": g["empresa"],
                                         "contactos": len(g["contactos"]),
                                         "invitados": g["invitados"]})
        else:
            emp = emp_lote.get(dom.strip().lower()) or empresa_en_hubspot(dom)
            if not emp.get("ok"):
                parte["errores"].append({"empresa": g["empresa"], "dominio": dom,
                                         "error": emp.get("error")})
            elif emp["veredicto"] in ("rojo", "amarillo"):
                fila = {"empresa": g["empresa"], "dominio": dom,
                        "veredicto": emp["veredicto"], "motivo": emp["motivo"],
                        "contactos": len(g["contactos"]), "invitados": g["invitados"]}
                parte["rojas" if emp["veredicto"] == "rojo" else "amarillas"].append(fila)
                if not simular:
                    # La empresa manda: sus decisores heredan su veredicto
                    # aunque su correo no esté en el CRM (ver encabezado).
                    #
                    # El amarillo se escribe igual que el rojo. La primera
                    # versión solo guardaba el rojo y las 35 amarillas quedaron
                    # reportadas en pantalla y en blanco en la base: visibles
                    # para quien leyera el parte e invitables para el sistema.
                    # Un hallazgo que no se escribe no protege a nadie.
                    for c in g["contactos"]:
                        _guardar(c["id"], emp["veredicto"], emp["motivo"])
                        parte["escritos"] += 1
                continue        # ya está resuelto por empresa: no se gasta en cada correo

        # Por persona, solo a quien le falte veredicto: lo demás ya se cruzó.
        # Aquí solo se apuntan; la red se toca una vez, en lote, al salir.
        for c in g["contactos"]:
            if (c.get("email") or "").strip() == "":
                continue
            if ya_con_veredicto.get(c["id"]):
                continue
            pendientes.append((c, g["empresa"]))
        if i % 100 == 0:
            print(f"  … {i}/{len(claves)} empresas", flush=True)

    if pendientes:
        print(f"  cruzando {len(pendientes)} personas en lotes de {hc.LOTE}…", flush=True)
        veredictos = hc.check_lote([c["email"] for c, _ in pendientes])
        for c, empresa in pendientes:
            res = veredictos.get((c["email"] or "").strip().lower(), {})
            if not res.get("ok"):
                parte["errores"].append({"contacto": c["nombre"],
                                         "error": res.get("error", "sin respuesta")})
                continue
            v, m = res["verdict"], res["reason"]
            if not simular:
                _guardar(c["id"], v, m)
                parte["escritos"] += 1
            if v == "rojo":
                parte["personas_rojas"].append({"nombre": c["nombre"], "empresa": empresa,
                                                "motivo": m, "invitado": bool(c["invitado"])})
            elif v == "amarillo":
                parte["personas_amarillas"].append({"nombre": c["nombre"],
                                                    "empresa": empresa, "motivo": m})

    # Segunda pasada sobre los que se cayeron por red. Un ReadTimeout no es un
    # veredicto: dejarlos como «no revisados» los deja invitables, que es
    # exactamente el agujero que este script viene a tapar.
    caidos = [e for e in parte["errores"]
              if e.get("dominio") and str(e.get("error", "")).startswith("red:")]
    if caidos:
        print(f"  reintentando {len(caidos)} que se cayeron por red…", flush=True)
        parte["errores"] = [e for e in parte["errores"] if e not in caidos]
        for e in caidos:
            emp = empresa_en_hubspot(e["dominio"])
            time.sleep(PAUSA * 3)          # más suave: ya falló una vez
            if not emp.get("ok"):
                parte["errores"].append({**e, "error": f"reintento: {emp.get('error')}"})
                continue
            g = grupos.get(reparto.clave_empresa(e["empresa"]))
            if g is None or emp["veredicto"] == "verde":
                continue
            fila = {"empresa": e["empresa"], "dominio": e["dominio"],
                    "veredicto": emp["veredicto"], "motivo": emp["motivo"],
                    "contactos": len(g["contactos"]), "invitados": g["invitados"]}
            parte["rojas" if emp["veredicto"] == "rojo" else "amarillas"].append(fila)
            if not simular:
                for c in g["contactos"]:
                    _guardar(c["id"], emp["veredicto"], emp["motivo"])
                    parte["escritos"] += 1
    return parte


def _cli() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--simular", action="store_true", help="no escribe veredictos")
    p.add_argument("--limite", type=int, default=0, help="tope de empresas")
    p.add_argument("--todo", action="store_true",
                   help="incluye descartados y clientes, no solo la base activa")
    a = p.parse_args()
    r = correr(a.simular, a.limite, not a.todo)

    print(f"\nempresas revisadas: {r['empresas']} · veredictos escritos: {r['escritos']}")
    if r["rojas"]:
        print(f"\n🔴 EMPRESAS QUE NO SE DEBEN TOCAR ({len(r['rojas'])}):")
        for x in r["rojas"]:
            marca = "  ⚠ YA INVITADA" if x["invitados"] else ""
            print(f"   {x['empresa'][:30]:32} {x['motivo'][:60]}{marca}")
    if r["personas_rojas"]:
        print(f"\n🔴 PERSONAS ({len(r['personas_rojas'])}):")
        for x in r["personas_rojas"]:
            print(f"   {x['nombre'][:24]:26} {x['empresa'][:22]:24} {x['motivo'][:46]}"
                  + ("  ⚠ YA INVITADA" if x["invitado"] else ""))
    if r["amarillas"]:
        print(f"\n🟡 empresas en el CRM ({len(r['amarillas'])}), coordinar antes de ir en frío:")
        for x in r["amarillas"][:15]:
            print(f"   {x['empresa'][:30]:32} {x['motivo'][:58]}")
    print(f"\n🟡 personas en el CRM: {len(r['personas_amarillas'])}")
    print(f"sin dominio deducible: {len(r['sin_dominio'])} empresas")
    if r["errores"]:
        print(f"errores: {len(r['errores'])} (primeros 3: "
              f"{json.dumps(r['errores'][:3], ensure_ascii=False)[:200]})")
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nparte completo en {ESTADO}")


if __name__ == "__main__":
    _cli()
