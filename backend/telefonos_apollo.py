"""Teléfono verificado de los prospectos, vía Apollo y su webhook asíncrono.

Hermano de `telefonos_am.py`, que hace lo mismo por Amplemarket. Existen los dos
porque cada uno se bloquea por su lado: Amplemarket es 8x más barato (1 crédito
contra ~8) pero su pozo de CORREO está en cero, y el paso *find* de 0,5 se cobra
de ahí, así que no revela teléfono aunque el pozo de teléfono esté lleno
(402 verificado en vivo el 15-sep-2026). Apollo tiene saldo pero exige webhook.

## El teléfono de Apollo ya no llega en la respuesta

Desde 2026 el revelado es ASÍNCRONO y el circuito tiene tres piezas:

  1. `POST /people/bulk_match?reveal_phone_number=true&webhook_url=…` con hasta
     10 personas. Los dos parámetros van en **query string**, no en el cuerpo, y
     el `webhook_url` es obligatorio: sin él la respuesta es 400 y **también se
     cae el correo**, no solo el teléfono.
  2. La respuesta síncrona trae el perfil y `phone_enrichment: {status:
     "pending"}`. El número NO viene aquí.
  3. Minutos después Apollo hace POST al webhook con los números.

## El mapa id→contacto es lo único que hace utilizable el payload

El webhook identifica a la persona **solo por su `id` de Apollo**: no trae
linkedin_url ni correo. Si no se guarda el mapa al pedir, los teléfonos que
lleguen no se pueden pegar a nadie y el crédito se tiró. Por eso el mapa se
escribe ANTES de esperar nada.

La identidad se decide al PEDIR, no al recibir: si el match de Apollo viene con
`match_confidence` baja, el celular que llegue después es de otra persona. Se
guarda la confianza en el mapa y se descarta al aplicar.

## Qué se considera «verificado»

Del payload solo se acepta un número con `status_cd == "valid_number"`, y se
prefiere el `type_cd == "mobile"`: a un conmutador no se le llama para
prospectar. Un número marcado en la lista de no-llamar (`dnc_status_cd`) se
guarda igual pero con la marca en la actividad, porque el dato es cierto y
ocultarlo llevaría a re-comprarlo; quien marque tiene que verlo.

## El gasto es el punto delicado

~8 créditos por persona, no 1 como el match de correo — lo reporta
`credits_consumed`. En julio de 2026 un job desatendido quemó 2.9K créditos de
Apollo; de ahí que `pedir()` exija un tope explícito y se niegue a pasar de
`LOTE_SEGURO` sin que alguien lo pida a mano.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

import apollo_budget
import apollo_prospect as apollo
import store
from config import DATA_DIR, PUBLIC_BASE_URL

EP_BULK = "https://api.apollo.io/api/v1/people/bulk_match"
RUTA_WEBHOOK = "/api/webhooks/apollo/telefono"
TIMEOUT = 180

CANAL = "apollo"
TIPO_PEDIDO = "telefono_pedido"
TIPO_RECIBIDO = "telefono"

# Coste medido por persona. No es 1 como el match de correo.
CREDITOS_POR_TELEFONO = 8
# Apollo acepta 10 por llamada.
POR_LLAMADA = 10
# Más de esto exige `forzar=True`. El número no es un capricho: es el radio de
# daño de una equivocación a 8 créditos cada una.
LOTE_SEGURO = 25

# Confianza mínima del match para fiarse del número que llegue después.
CONFIANZA_OK = ("high", "medium")

_CRUDO = DATA_DIR / "apollo_telefono_webhook.jsonl"


# ── el mapa id→contacto ─────────────────────────────────────────────────────
def _k(apollo_id: str) -> str:
    return f"apollo_tel:{apollo_id}"


def _recordar(apollo_id: str, contacto: dict) -> None:
    store.kv_set(_k(apollo_id), json.dumps(contacto, ensure_ascii=False))


def _recordado(apollo_id: str) -> dict:
    raw = store.kv_get(_k(apollo_id), "")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


# ── a quién le falta ────────────────────────────────────────────────────────
def objetivos(stage: str = "conectado", limite: int = 0,
              ids: list[int] | None = None) -> list[dict]:
    """Contactos de esa etapa con LinkedIn y sin teléfono.

    Exige LinkedIn y no correo: medido en la base, Apollo y Amplemarket resuelven
    mucho mejor por perfil que por correo, y estos contactos vienen todos del
    canal de LinkedIn.
    """
    if ids:
        marcas = ",".join("?" * len(ids))
        filas = store.fetch_all(
            f"SELECT id, nombre, empresa, cargo, email, linkedin, li_seat, stage "
            f"FROM contacts WHERE id IN ({marcas}) "
            f"  AND COALESCE(celular,'')='' AND COALESCE(linkedin,'')<>'' "
            f"ORDER BY id", tuple(ids))
    else:
        filas = store.fetch_all(
            "SELECT id, nombre, empresa, cargo, email, linkedin, li_seat, stage "
            "FROM contacts WHERE stage=? "
            "  AND COALESCE(celular,'')='' AND COALESCE(linkedin,'')<>'' "
            "ORDER BY id", (stage,))
    # Quien ya se pidió no se vuelve a pedir: el crédito ya se gastó y el número
    # puede estar todavía en vuelo.
    out = []
    for f in filas:
        ya = store.fetch_one(
            "SELECT id FROM activities WHERE contact_id=? AND channel=? AND type=? LIMIT 1",
            (f["id"], CANAL, TIPO_PEDIDO))
        if not ya:
            out.append(f)
        if limite and len(out) >= limite:
            break
    return out


def webhook_url() -> str:
    """La URL pública del receptor. Sin ella no se puede pedir nada.

    Sale del mismo túnel que ya usan el webhook de SendGrid y los links de
    atribución (`config.PUBLIC_BASE_URL`), que tiene dominio estático: la URL no
    cambia entre reinicios, así que un teléfono que llegue tarde sigue teniendo
    a dónde llegar.
    """
    base = (PUBLIC_BASE_URL or "").strip().rstrip("/")
    return f"{base}{RUTA_WEBHOOK}" if base else ""


def costo(n: int) -> dict:
    est = n * CREDITOS_POR_TELEFONO
    s = apollo_budget.status()
    return {"personas": n, "creditos_estimados": est,
            "saldo": s["remaining"], "alcanza": s["remaining"] >= est}


# ── pedir ───────────────────────────────────────────────────────────────────
def pedir(stage: str = "conectado", limite: int = 0, simular: bool = False,
          forzar: bool = False, ids: list[int] | None = None) -> dict:
    """Pide los teléfonos. Los números NO llegan aquí: llegan por el webhook."""
    objs = objetivos(stage, limite, ids)
    parte: dict = {"stage": stage, "objetivos": len(objs), "pedidos": 0,
                   "creditos_gastados": 0, "lotes": [], "error": "",
                   "costo": costo(len(objs)), "detalle": []}
    if not objs:
        parte["error"] = (f"no hay contactos en «{stage}» con LinkedIn, sin teléfono "
                          f"y sin pedido previo")
        return parte

    url = webhook_url()
    parte["webhook"] = url or "— sin configurar —"
    if not url:
        parte["error"] = ("falta la URL pública del webhook (PUBLIC_BASE_URL). "
                          "Apollo exige webhook_url para revelar teléfono: sin "
                          "ella responde 400 y tumba también el correo.")
        return parte

    if simular:
        parte["detalle"] = [{"nombre": o["nombre"], "empresa": o["empresa"],
                             "stage": o["stage"], "linkedin": o["linkedin"]}
                            for o in objs]
        return parte

    if len(objs) > LOTE_SEGURO and not forzar:
        parte["error"] = (f"{len(objs)} personas son ~{len(objs) * CREDITOS_POR_TELEFONO} "
                          f"créditos. Por encima de {LOTE_SEGURO} hay que pedirlo "
                          f"a propósito (forzar=True).")
        return parte
    if not parte["costo"]["alcanza"]:
        parte["error"] = (f"el saldo de Apollo no alcanza: hacen falta "
                          f"~{parte['costo']['creditos_estimados']} y hay "
                          f"{parte['costo']['saldo']}")
        return parte

    key = apollo._key()
    if not key:
        parte["error"] = "no hay APOLLO_API_KEY configurada"
        return parte

    for i in range(0, len(objs), POR_LLAMADA):
        grupo = objs[i:i + POR_LLAMADA]
        det = [{"linkedin_url": o["linkedin"]} for o in grupo]
        try:
            r = requests.post(EP_BULK, headers=apollo._headers(key), timeout=TIMEOUT,
                              params={"reveal_phone_number": "true", "webhook_url": url},
                              json={"details": det, "reveal_personal_emails": False})
        except requests.RequestException as e:
            parte["lotes"].append({"error": f"red: {e.__class__.__name__}"})
            break
        if r.status_code != 200:
            parte["lotes"].append({"http": r.status_code, "cuerpo": r.text[:300]})
            parte["error"] = f"Apollo HTTP {r.status_code}: {r.text[:200]}"
            break
        j = r.json()
        gast = int(j.get("credits_consumed") or 0)
        parte["creditos_gastados"] += gast
        if gast:
            apollo_budget.consume(gast)
        pe = j.get("phone_enrichment") or {}
        parte["lotes"].append({"personas": len(grupo), "credits_consumed": gast,
                               "phone_enrichment": pe})

        # El mapa, ANTES de esperar nada: el crédito ya está gastado y sin él
        # el payload que llegue es basura.
        for o, m in zip(grupo, j.get("matches") or []):
            if not isinstance(m, dict):
                continue
            aid = str(m.get("id") or "")
            conf = (m.get("match_confidence") or "").lower()
            if aid:
                _recordar(aid, {"contact_id": o["id"], "nombre": o["nombre"],
                                "linkedin": o["linkedin"], "confianza": conf})
            store.add_activity(o["id"], CANAL, TIPO_PEDIDO, store.now_iso(),
                               ext_id=f"apollo-tel:{o['id']}",
                               meta={"apollo_id": aid, "confianza": conf,
                                     "request_id": pe.get("request_id", ""),
                                     "creditos": gast})
            parte["pedidos"] += 1
            parte["detalle"].append({"nombre": o["nombre"], "apollo_id": aid,
                                     "confianza": conf})
        time.sleep(1.0)
    return parte


# ── recibir ─────────────────────────────────────────────────────────────────
def _mejor_numero(persona: dict) -> tuple[str, str, str]:
    """(numero, tipo, dnc). Solo números verificados; se prefiere el móvil.

    `status_cd == "valid_number"` es lo que hace que esto sea un teléfono
    verificado y no un dato cualquiera: Apollo devuelve también los que sabe
    malos, y guardarlos es peor que no tener nada porque alguien va a marcarlos.
    """
    nums = [n for n in (persona.get("phone_numbers") or [])
            if (n.get("status_cd") or "valid_number") == "valid_number"
            and (n.get("sanitized_number") or n.get("raw_number"))]
    if not nums:
        return "", "", ""
    nums.sort(key=lambda n: n.get("position") if n.get("position") is not None else 99)
    elegido = next((n for n in nums if n.get("type_cd") == "mobile"), nums[0])
    dnc = (elegido.get("dnc_status_cd") or "").strip()
    return ((elegido.get("sanitized_number") or elegido.get("raw_number") or "").strip(),
            (elegido.get("type_cd") or "?").strip(),
            "" if dnc in ("", "not_found") else dnc)


def aplicar(payload: dict) -> dict:
    """Escribe los teléfonos que trae un payload del webhook.

    Idempotente a propósito: «Apollo may retry webhook calls». Un contacto que
    ya tiene número no se pisa, y la actividad va con `ext_id` por contacto.
    """
    parte = {"personas": 0, "escritos": 0, "sin_numero": 0, "sin_mapa": 0,
             "baja_confianza": 0, "detalle": []}
    for per in (payload.get("people") or []):
        parte["personas"] += 1
        aid = str(per.get("id") or "")
        quien = _recordado(aid)
        if not quien:
            parte["sin_mapa"] += 1
            continue
        if (quien.get("confianza") or "") not in CONFIANZA_OK:
            # La identidad se decidió al pedir: con match dudoso, este número es
            # de otra persona. Se descarta aunque sea válido.
            parte["baja_confianza"] += 1
            continue
        num, tipo, dnc = _mejor_numero(per)
        if not num:
            parte["sin_numero"] += 1
            continue
        cid = quien["contact_id"]
        actual = store.get_contact(cid) or {}
        if (actual.get("celular") or "").strip():
            continue                     # ya tenía: no se pisa
        store.set_contact_fields(cid, {"celular": num})
        store.add_activity(cid, CANAL, TIPO_RECIBIDO, store.now_iso(),
                           ext_id=f"apollo-tel-ok:{cid}",
                           meta={"numero": num, "tipo": tipo, "dnc": dnc,
                                 "apollo_id": aid})
        parte["escritos"] += 1
        parte["detalle"].append({"nombre": quien.get("nombre", ""), "numero": num,
                                 "tipo": tipo, "dnc": dnc})
    return parte


def recibir(payload: dict) -> dict:
    """Guarda el payload crudo y lo aplica. Lo llama el endpoint del webhook."""
    try:
        _CRUDO.parent.mkdir(parents=True, exist_ok=True)
        with open(_CRUDO, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": store.now_iso(), "payload": payload},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass                             # el crudo es para auditar, no bloquea
    return aplicar(payload)


def estado() -> dict:
    pend = store.fetch_all(
        "SELECT COUNT(*) n FROM activities a WHERE a.channel=? AND a.type=? "
        "AND NOT EXISTS(SELECT 1 FROM activities b WHERE b.contact_id=a.contact_id "
        "               AND b.channel=? AND b.type=?)",
        (CANAL, TIPO_PEDIDO, CANAL, TIPO_RECIBIDO))
    return {"webhook": webhook_url() or "— sin configurar —",
            "saldo_apollo": apollo_budget.status(),
            "creditos_por_telefono": CREDITOS_POR_TELEFONO,
            "sin_telefono": (store.fetch_one(
                "SELECT COUNT(*) n FROM contacts WHERE COALESCE(celular,'')='' "
                "AND COALESCE(linkedin,'')<>''") or {}).get("n", 0),
            "pedidos_en_vuelo": (pend[0]["n"] if pend else 0)}
