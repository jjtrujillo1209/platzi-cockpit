"""Inscripción automática en la secuencia de Amplemarket del VP que firma.

## Una secuencia por perfil, no por campaña

`enrollment.py` inscribe contra una `Campaign` con su copy, su evento y su
corte. Esto es otra cosa: la secuencia la firma **el VP**, y el prospecto aceptó
a una persona concreta. Si quien aceptó a Patricia recibe el correo de Jess, el
hilo se rompe —aceptó a una y le escribe otra— así que la secuencia vive en el
asiento (`seats.Seat.secuencia_am`) y se elige por el `li_seat` del contacto.

    vp-1 · Jess Harris      → B2B ENTERPRISE … TEST 1 - JEFFERSON -jess
    vp-2 · Patricia Salgar  → B2B ENTERPRISE … TEST 1 - Pablo - patricia

## Por qué esto NO pasa por la cola de aprobación

La cola existe para que ningún texto sin revisar salga a un presidente. Aquí no
hay texto del cockpit: las dos plantillas consumen `first_name` y
`company_name` y nada más —verificado con `dynamic_fields_used`—, así que el
cuerpo del correo lo escribió una persona en Amplemarket y el cockpit solo
aporta el nombre y la empresa, que salen de la base. No hay gancho generado por
modelo, y por lo tanto no hay nada que auditar: mismo criterio que la invitación
de LinkedIn sin nota.

Lo que sí se decide aquí —y queda registrado— es **a quién** y **en cuál de las
dos secuencias**.

## Se inscribe en «contactado» y en «conectado»

Decisión del 8-sep-2026: el correo corre en paralelo a la invitación pendiente,
no solo después de que aceptan. Un `contactado` es alguien a quien ya le salió
la invitación y todavía no responde; el correo es el segundo canal mientras
LinkedIn madura. Los `conectado` entran igual, en el momento en que aceptan.

Consecuencia que hay que tener presente: el remitente del correo y el firmante
de la invitación pendiente son el mismo perfil, a propósito. Si se separaran, el
prospecto recibiría un correo de alguien que no es quien le escribió por
LinkedIn.

## Idempotencia

Cada intento deja una actividad `enrolled` con `ext_id` por (secuencia,
contacto), y `objetivos()` excluye a quien ya tiene una de **cualquier**
secuencia: al cambiarle la secuencia a un VP, quien pasó por la anterior no
entra en la nueva. Se registra también lo
que Amplemarket **saltó** (duplicado, lista de exclusión, contactado hace poco):
si no se registrara, cada tick volvería a mandar los mismos leads a una API que
ya dijo no. El motivo viaja en el meta para que se pueda ver por qué.

`enrolled` no está en `store.ACTIVITY_STAGE`, así que inscribir no mueve el
stage: lo mueve el envío real cuando Amplemarket lo reporte por su webhook.
"""
from __future__ import annotations

import time

import amplemarket_seq as ams
import seats
import store

CANAL = "amplemarket"
TIPO = "enrolled"

# Etapas que se inscriben. Ver el encabezado: son las dos a propósito.
ETAPAS = ("contactado", "conectado")

# Tope del carril automático. No es ritmo —el ritmo lo pone la cadencia de
# Amplemarket y el tope diario de cada buzón—: es radio de daño de un bucle que
# corre desatendido cada 15 minutos. La corrida manual no lo aplica.
TOPE_AUTOMATICO = 100


# Nombres de las secuencias, cacheados. La pantalla se refresca cada 30 s y el
# id («6c19b6a0…») no le dice nada a quien mira: el nombre sí. Pero listar en
# cada refresco serían dos llamadas por minuto a Amplemarket para un dato que no
# cambia, así que se cachea en memoria.
_CACHE_TTL = 600.0
_cache_nombres: tuple[float, dict[str, str]] = (0.0, {})


def nombres_secuencias() -> dict[str, str]:
    global _cache_nombres
    ts, datos = _cache_nombres
    if datos and (time.monotonic() - ts) < _CACHE_TTL:
        return datos
    try:
        datos = {x.get("id"): x.get("name", "") for x in ams.list_sequences()}
    except Exception:
        return datos          # se conserva lo último bueno; el id es el respaldo
    _cache_nombres = (time.monotonic(), datos)
    return datos


def _ext_id(sequence_id: str, contact_id: int) -> str:
    return f"am-seq:{sequence_id}:{contact_id}"


def mapa_asientos() -> dict[str, dict]:
    """seat_id → {secuencia, nombre_vp}. Solo asientos con secuencia puesta.

    Un asiento que además tenga `secuencia_hs` queda fuera: con los dos motores
    puestos la misma persona recibiría dos correos distintos, de dos remitentes
    distintos. `secuencias_hs.mapa_asientos()` hace lo simétrico, así que una
    configuración mala no envía por ninguno — falla en seco y se ve, en vez de
    duplicar en silencio.
    """
    out = {}
    for s in seats.en_uso():
        if (s.secuencia_am or "").strip() and not (s.secuencia_hs or "").strip():
            out[s.id] = {"secuencia": s.secuencia_am.strip(), "nombre_vp": s.etiqueta}
    return out


def objetivos(limite: int = 0, ids: list[int] | None = None) -> list[dict]:
    """Quién falta por inscribir, con su secuencia ya resuelta.

    Exige correo: es una secuencia de correo. Un contacto con LinkedIn pero sin
    correo entraría como lead y se quedaría esperando un canal que no tiene.

    `ids` = selección explícita desde el Pipeline. Ahí **no** se filtra por
    etapa: el filtro de `ETAPAS` existe para el carril automático, que decide
    solo; si una persona hizo clic en un prospecto concreto, ya decidió. Los
    frenos que sí se mantienen son los que no son una preferencia —bandera roja
    de HubSpot, correo, asiento con secuencia— porque esos protegen al prospecto
    o hacen imposible el envío.
    """
    mapa = mapa_asientos()
    if not mapa:
        return []
    if ids:
        marcas = ",".join("?" * len(ids))
        filas = store.fetch_all(
            f"SELECT id, nombre, empresa, email, linkedin, li_seat, stage "
            f"FROM contacts WHERE id IN ({marcas}) "
            f"  AND COALESCE(email,'')<>'' AND COALESCE(li_seat,'')<>'' "
            f"  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
            f"ORDER BY id", tuple(ids))
    else:
        marcas = ",".join("?" * len(ETAPAS))
        filas = store.fetch_all(
            f"SELECT id, nombre, empresa, email, linkedin, li_seat, stage "
            f"FROM contacts "
            f"WHERE stage IN ({marcas}) AND COALESCE(email,'')<>'' "
            f"  AND COALESCE(li_seat,'')<>'' "
            f"  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
            f"ORDER BY (stage='conectado') DESC, id DESC", tuple(ETAPAS))

    out = []
    for f in filas:
        info = mapa.get(f["li_seat"])
        if info is None:
            continue                      # su asiento no tiene secuencia puesta
        # Cualquier secuencia, no solo la actual del asiento: cambiarle la
        # secuencia a un VP no puede volver a escribirle a quien ya pasó por la
        # anterior — sería un segundo correo en frío a la misma persona.
        ya = store.fetch_one(
            "SELECT id FROM activities WHERE contact_id=? AND ext_id LIKE ? LIMIT 1",
            (f["id"], _ext_id("%", f["id"])))
        if ya:
            continue
        out.append({**f, "secuencia": info["secuencia"],
                    "nombre_vp": info["nombre_vp"]})
        if limite and len(out) >= limite:
            break
    return out


# Las siete cubetas con que la API contesta, verificadas contra la cuenta el
# 8-sep-2026. No hay una llave `skipped`: cada motivo es una lista de
# `{email, linkedin_url}` en la raíz de la respuesta.
CUBETAS = ("duplicate_emails", "duplicate_linkedin_urls",
           "in_exclusion_list_and_skipped", "recently_contacted_and_skipped",
           "already_in_sequence_and_skipped",
           "in_other_draft_sequences_and_skipped",
           "in_other_active_sequences_and_skipped")

# Quien ya está en la secuencia no es un fallo: es el estado que queríamos.
CUBETAS_OK = ("already_in_sequence_and_skipped",)

# El único guardarraíl que se pisa, y solo porque es exactamente lo que se pidió:
# el correo tiene que salir mientras la invitación de LinkedIn está pendiente, y
# esa invitación es justo lo que hace que Amplemarket los vea «contactados hace
# poco». Va DENTRO de cada lead, en `overrides` — en la raíz del body se ignora
# en silencio (probado con cinco variantes antes de encontrar la buena).
#
# Los otros tres overrides NO se usan, a propósito:
#   · `ignore_exclusion_list` → la lista de exclusión es un no-contactar; pisarla
#     no es una decisión de producto, es un problema.
#   · `ignore_duplicate_leads_in_other_active_sequences` → la misma persona en
#     dos secuencias a la vez se lee como campaña, no como una persona
#     escribiendo. Si aparece, hay que sacarla de la otra a mano.
OVERRIDES = {"ignore_recently_contacted": True}


def _resultado_por_llave(resp: dict) -> dict[str, str]:
    """correo/LinkedIn (minúsculas) → nombre de la cubeta que lo descartó."""
    out: dict[str, str] = {}
    for cubeta in CUBETAS:
        for item in (resp.get(cubeta) or []):
            if isinstance(item, str):
                out[item.strip().lower()] = cubeta
            elif isinstance(item, dict):
                for k in ("email", "linkedin_url"):
                    v = (item.get(k) or "").strip().lower()
                    if v:
                        out[v] = cubeta
    return out


def _por_que_ninguno(ids: list[int] | None) -> str:
    """El motivo concreto, no «no hay nadie». Un 400 genérico sobre un contacto
    que se acaba de clicar manda a revisar el sistema entero."""
    if not ids:
        return ("no hay contactado/conectado con correo, asiento y secuencia "
                "que falte por inscribir")
    faltas = []
    for cid in ids:
        c = store.get_contact(cid)
        if not c:
            faltas.append(f"{cid}: no existe")
            continue
        n = c.get("nombre") or cid
        if not (c.get("email") or "").strip():
            faltas.append(f"{n}: sin correo")
        elif not (c.get("li_seat") or "").strip():
            faltas.append(f"{n}: sin perfil de LinkedIn asignado")
        elif (c.get("li_seat") or "") not in mapa_asientos():
            s = seats.get(c.get("li_seat") or "")
            if s and (s.secuencia_am or "").strip() and (s.secuencia_hs or "").strip():
                faltas.append(f"{n}: su perfil tiene secuencia de Amplemarket Y de "
                              f"HubSpot — quita una, o recibiría dos correos")
            else:
                faltas.append(f"{n}: su perfil no tiene secuencia configurada")
        elif (c.get("hubspot_verdict") or "") == "rojo":
            faltas.append(f"{n}: 🔴 en HubSpot — no ir en frío")
        else:
            faltas.append(f"{n}: ya pasó por una secuencia de Amplemarket")
    return "; ".join(faltas)


def inscribir(limite: int = 0, simular: bool = False,
              ids: list[int] | None = None) -> dict:
    """Inscribe en la secuencia de su VP. `ids` = selección del Pipeline."""
    objs = objetivos(limite, ids)
    parte: dict = {"objetivos": len(objs), "inscritos": 0, "saltados": 0,
                   "por_secuencia": [], "error": "",
                   "asientos_sin_secuencia": [
                       s.id for s in seats.en_uso()
                       if not (s.secuencia_am or "").strip()]}
    if not objs:
        parte["error"] = _por_que_ninguno(ids)
        return parte
    if simular:
        parte["detalle"] = [{"nombre": o["nombre"], "empresa": o["empresa"],
                             "stage": o["stage"], "vp": o["nombre_vp"]}
                            for o in objs]
        return parte

    # Un POST por secuencia: la API acepta hasta 250 leads y así el reparto
    # entre buzones lo hace Amplemarket de una sola vez por secuencia.
    por_seq: dict[str, list[dict]] = {}
    for o in objs:
        por_seq.setdefault(o["secuencia"], []).append(o)

    for seq_id, grupo in por_seq.items():
        info = {"secuencia": seq_id, "vp": grupo[0]["nombre_vp"],
                "enviados": len(grupo), "añadidos": 0, "ya_estaban": 0,
                "saltados": 0, "motivos": {}, "error": ""}
        leads = []
        for o in grupo:
            lead = ams.build_lead(o, hook="", link="")
            lead["overrides"] = dict(OVERRIDES)
            leads.append(lead)
        try:
            resp = ams.add_leads(seq_id, leads)
        except Exception as e:
            info["error"] = f"{e.__class__.__name__}: {str(e)[:200]}"
            parte["por_secuencia"].append(info)
            continue

        cubetas = _resultado_por_llave(resp)
        info["total_añadidos_api"] = resp.get("total_added_to_sequence")
        for o in grupo:
            cubeta = cubetas.get((o["email"] or "").strip().lower()) or \
                cubetas.get((o["linkedin"] or "").strip().lower())
            if cubeta is None:
                resultado = "añadido"
                info["añadidos"] += 1
            elif cubeta in CUBETAS_OK:
                resultado = f"ya estaba: {cubeta}"
                info["ya_estaban"] += 1
            else:
                resultado = f"saltado: {cubeta}"
                info["saltados"] += 1
                info["motivos"][cubeta] = info["motivos"].get(cubeta, 0) + 1
            # Se registra el intento incluso si Amplemarket lo saltó: si no,
            # el tick siguiente vuelve a mandarlo (ver encabezado).
            store.add_activity(o["id"], CANAL, TIPO, store.now_iso(),
                               ext_id=_ext_id(seq_id, o["id"]),
                               meta={"secuencia": seq_id, "vp": o["nombre_vp"],
                                     "seat": o["li_seat"], "stage": o["stage"],
                                     "resultado": resultado})
        info["respuesta"] = {k: v for k, v in resp.items() if v}
        parte["inscritos"] += info["añadidos"]
        parte["ya_estaban"] = parte.get("ya_estaban", 0) + info["ya_estaban"]
        parte["saltados"] += info["saltados"]
        parte["por_secuencia"].append(info)

    return parte


# ----------------------------- carril automático ---------------------------
def activa() -> bool:
    return store.kv_get("secuencias_am_auto", "0") == "1"


def set_activa(v: bool) -> None:
    store.kv_set("secuencias_am_auto", "1" if v else "0")


def estado() -> dict:
    nombres = nombres_secuencias()
    return {"activo": activa(),
            "etapas": list(ETAPAS),
            "tope_automatico": TOPE_AUTOMATICO,
            # Solo asientos con dueño: `vp-3` es un hueco reservado, y pintarlo
            # como «sin secuencia asignada» es una alarma sobre algo que no está
            # roto — hay que asignarle un VP antes, no una secuencia.
            "asientos": [{"seat_id": s.id, "vp": s.etiqueta,
                          "secuencia": s.secuencia_am or "",
                          "secuencia_nombre": nombres.get(s.secuencia_am or "", "")}
                         for s in seats.en_uso() if s.configurado],
            "pendientes": len(objetivos()),
            "ultima_corrida": store.kv_get("secuencias_am_ts", ""),
            "ultimo_parte": store.kv_get("secuencias_am_parte", "")}


def toca_ahora(ahora=None) -> tuple[bool, str]:
    import scheduler
    if not activa():
        return False, "la inscripción automática está apagada"
    if not scheduler.in_business_hours(ahora):
        return False, "fuera de horario laboral"
    return True, "toca"


def correr_si_toca(ahora=None) -> dict:
    puede, motivo = toca_ahora(ahora)
    if not puede:
        return {"corrio": False, "motivo": motivo}
    if not objetivos(1):
        return {"corrio": False, "motivo": "no hay nadie nuevo que inscribir"}
    import json
    parte = inscribir(limite=TOPE_AUTOMATICO)
    store.kv_set("secuencias_am_ts", store.now_iso())
    store.kv_set("secuencias_am_parte",
                 json.dumps(parte, ensure_ascii=False, default=str)[:4000])
    return {"corrio": True, **parte}
