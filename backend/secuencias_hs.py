"""Inscripción automática en la secuencia de HubSpot del vendedor que firma.

Hermano de `secuencias_am.py`, con el mismo esqueleto y otro motor: allí el
correo lo manda Amplemarket, aquí lo manda **la bandeja de Google del vendedor**
a través de una secuencia de Sales Hub. Los dos carriles conviven porque los
perfiles no son equivalentes: Jess y Patricia prestan solo su LinkedIn, mientras
que Fabrizzio y Jefferson son vendedores con asiento de Sales Hub y buzón
conectado, así que su correo puede salir a su nombre y volver a su bandeja.

## Por qué HubSpot y no otro transporte

`platzi.com` publica `v=spf1 include:_spf.google.com mx -all` y un DMARC en
`p=quarantine`. Traducido: lo único autorizado a enviar como `@platzi.com` es
Google Workspace. La secuencia de Sales Hub no retransmite —le pide a Gmail que
envíe— así que pasa SPF y DKIM como cualquier correo 1:1 de esa persona. El
marketing masivo de Platzi vive aparte, en `email.platzi.com`, y este carril no
lo toca.

## A esta secuencia solo entran los de Fabrizzio y Jefferson

Decisión del 14-sep-2026. No hay una lista de nombres en el código que lo
imponga: lo que decide es `secuencia_hs`, y solo los asientos de ellos dos la
tienen (`vp-1` y `vp-2` van por Amplemarket). Un contacto de otro perfil no
entra ni por el carril automático ni eligiéndolo a mano en el Pipeline — el
`mapa_asientos()` no lo reconoce y se queda fuera con su motivo.

El riesgo que queda no es de código sino de configuración: la secuencia
`311534742` la ven todos los usuarios del portal, así que apuntar a ella el
`secuencia_hs` de otro asiento metería a su gente aquí. Si eso deja de ser
aceptable, el sitio de la restricción es este módulo, no HubSpot.

## Un motor por asiento, nunca dos

`secuencia_am` y `secuencia_hs` en el mismo asiento significarían dos correos
distintos, de dos remitentes distintos, a la misma persona. `mapa_asientos()`
salta el asiento que tenga las dos —y `secuencias_am` hace lo simétrico—, así
que una configuración mala **no envía por ninguno**. Falla en seco y se ve en
pantalla, que es preferible a duplicar en silencio.

## Se inscribe en «contactado» y en «conectado»

Igual que Amplemarket desde el 8-sep-2026: el correo corre en paralelo a la
invitación pendiente, no solo después de que aceptan. Y el remitente del correo
es el mismo perfil que firmó la invitación, a propósito.

## Por qué esto NO pasa por la cola de aprobación

La cola existe para que ningún texto sin revisar salga a un decisor. Aquí no hay
texto del cockpit: la inscripción **no acepta cuerpo** —solo `sequenceId`,
`contactId` y `senderEmail`—, el copy vive entero en la plantilla que el
vendedor escribió en HubSpot con tokens nativos, y el cockpit no aporta ni una
palabra. No hay nada generado por modelo que auditar: mismo criterio que la
invitación de LinkedIn sin nota.

## Los prospectos se crean en el CRM

Una secuencia solo inscribe un `contactId`, y 19 de los 20 primeros elegibles no
existían en HubSpot. Se crean con dueño (el vendedor que firma), etapa
**Prospect** y `fuente = "Maquina outbound"` — esa marca es lo que permite
listar y revertir después todo lo que escribió esta máquina.

## Idempotencia y el freno que no tiene vuelta atrás

Cada intento deja una actividad con `ext_id` por (secuencia, contacto), y
`objetivos()` excluye a quien ya la tiene; lo saltado se registra igual, o cada
tick reintentaría lo que HubSpot ya rechazó. Importa más que en Amplemarket:
**HubSpot no expone ningún endpoint para desinscribir**. Todo freno tiene que
aplicarse antes de la llamada, y el tope por tick es el radio de daño.

Ni `enrolled` ni `skipped` están en `store.ACTIVITY_STAGE`: inscribir no mueve
el stage. Lo mueve la respuesta del prospecto, no nuestro envío.
"""
from __future__ import annotations

import json
import time
from datetime import date

import requests

import apollo_prospect as apollo
import hs_secuencias as hsq
import seats
import store

CANAL = "hubspot"
TIPO = "enrolled"
# Mismo `ext_id`, otro tipo: así el intento saltado bloquea el reintento igual
# que uno bueno, pero no gasta cupo del día.
TIPO_SALTADO = "skipped"

# Etapas que se inscriben. Ver el encabezado: son las dos a propósito.
ETAPAS = ("contactado", "conectado")

# Correos por vendedor y día. El techo de HubSpot es 1.000 por asiento
# Enterprise contando todos los pasos de la cadencia; esto es mucho más bajo a
# propósito: el límite que importa no es el de la API sino la reputación de
# `platzi.com` en Google, y dos buzones que nunca han hecho outbound no deberían
# estrenarse a volumen de campaña.
TOPE_DIA_REMITENTE = 50

# Tope de una sola corrida automática. Con la pausa de 21 s, 20 inscripciones
# son ~7 minutos: cabe de sobra en el tick de 15. Los 50 del día se acumulan a
# lo largo de los ticks, no se gastan de un golpe.
TOPE_POR_TICK = 20

# HubSpot tolera 3 inscripciones por minuto al inscribir en masa.
PAUSA_SEG = 21.0


def _ext_id(sequence_id: str, contact_id: int) -> str:
    return f"hs-seq:{sequence_id}:{contact_id}"


# ── configuración de los asientos ───────────────────────────────────────────
def conflictos() -> list[dict]:
    """Asientos con los dos motores puestos. Ninguno de los dos los toca."""
    return [{"seat_id": s.id, "vp": s.etiqueta}
            for s in seats.en_uso()
            if (s.secuencia_hs or "").strip() and (s.secuencia_am or "").strip()]


def mapa_asientos() -> dict[str, dict]:
    """seat_id → {secuencia, remitente, nombre_vp}. Solo los bien configurados.

    Exige remitente explícito: sin buzón no hay a nombre de quién enviar, y
    HubSpot no tiene forma de deducirlo (el selector de su UI ofrece a todos los
    owners como candidatos, no como garantía).
    """
    out = {}
    for s in seats.en_uso():
        seq = (s.secuencia_hs or "").strip()
        rem = (s.remitente_hs or "").strip().lower()
        if not seq or not rem:
            continue
        if (s.secuencia_am or "").strip():
            continue                  # dos motores: no envía por ninguno
        out[s.id] = {"secuencia": seq, "remitente": rem, "nombre_vp": s.etiqueta}
    return out


def _remitentes_ok(mapa: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Parte el mapa en (los que pueden enviar, los que no con su motivo).

    Un remitente sin asiento de pago o sin bandeja conectada falla en runtime a
    mitad del lote, no con un error limpio. Una llamada aquí lo evita.
    """
    buenos, malos = {}, {}
    try:
        usuarios = hsq.remitentes_validos()
    except hsq.HubSpotError as e:
        return {}, {sid: f"no pude verificar el remitente: {e}" for sid in mapa}
    for sid, info in mapa.items():
        u = usuarios.get(info["remitente"])
        if u is None:
            malos[sid] = f"«{info['remitente']}» no es usuario de este portal"
        elif not u["conectado"]:
            malos[sid] = f"«{info['remitente']}» no tiene la bandeja conectada a HubSpot"
        elif not u["puede_enviar"]:
            malos[sid] = (f"«{info['remitente']}» no tiene asiento de Sales/Service "
                          f"(tiene: {u['asientos'] or 'ninguno'})")
        else:
            buenos[sid] = info
    return buenos, malos


def enviados_hoy(seat_id: str) -> int:
    hoy = date.today().isoformat()
    row = store.fetch_one(
        "SELECT COUNT(*) n FROM activities a JOIN contacts c ON c.id=a.contact_id "
        "WHERE c.li_seat=? AND a.channel=? AND a.type=? AND substr(a.ts,1,10)=?",
        (seat_id, CANAL, TIPO, hoy))
    return row["n"] if row else 0


# ── a quién le toca ─────────────────────────────────────────────────────────
def objetivos(limite: int = 0, ids: list[int] | None = None,
              mapa: dict[str, dict] | None = None) -> list[dict]:
    """Quién falta por inscribir, con secuencia y remitente ya resueltos.

    Exige correo: es una secuencia de correo. Y respeta el veredicto de HubSpot
    —nunca en frío a un cliente o a una cuenta en venta—, que es el mismo freno
    de `secuencias_am` y de `hubspot_check`.

    `ids` = selección explícita desde el Pipeline. Ahí **no** se filtra por
    etapa ni por el cupo del día: quien hizo clic en un prospecto concreto ya
    decidió. Los frenos que sí se mantienen son los que no son una preferencia
    —bandera roja, correo, asiento configurado—: esos protegen al prospecto o
    hacen imposible el envío.
    """
    mapa = mapa_asientos() if mapa is None else mapa
    if not mapa:
        return []
    if ids:
        marcas = ",".join("?" * len(ids))
        filas = store.fetch_all(
            f"SELECT id, nombre, empresa, cargo, email, celular, linkedin, li_seat, "
            f"       stage, hubspot_contact_id "
            f"FROM contacts WHERE id IN ({marcas}) "
            f"  AND COALESCE(email,'')<>'' AND COALESCE(li_seat,'')<>'' "
            f"  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
            f"ORDER BY id", tuple(ids))
    else:
        marcas = ",".join("?" * len(ETAPAS))
        filas = store.fetch_all(
            f"SELECT id, nombre, empresa, cargo, email, celular, linkedin, li_seat, "
            f"       stage, hubspot_contact_id "
            f"FROM contacts "
            f"WHERE stage IN ({marcas}) AND COALESCE(email,'')<>'' "
            f"  AND COALESCE(li_seat,'')<>'' "
            f"  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
            f"ORDER BY (stage='conectado') DESC, id DESC", tuple(ETAPAS))

    # El cupo del día se descuenta aquí y no al final: si no, el lote se llena
    # con gente del asiento que ya está agotado y el otro se queda sin turno.
    libre = {} if ids else {sid: max(0, TOPE_DIA_REMITENTE - enviados_hoy(sid))
                            for sid in mapa}

    out = []
    for f in filas:
        info = mapa.get(f["li_seat"])
        if info is None:
            continue                      # su asiento no tiene este carril
        if not ids:
            if libre.get(f["li_seat"], 0) <= 0:
                continue
        ya = store.fetch_one(
            "SELECT id FROM activities WHERE contact_id=? AND ext_id=? LIMIT 1",
            (f["id"], _ext_id(info["secuencia"], f["id"])))
        if ya:
            continue
        if not ids:
            libre[f["li_seat"]] -= 1
        out.append({**f, **info})
        if limite and len(out) >= limite:
            break
    return out


def _por_que_ninguno(ids: list[int] | None) -> str:
    """El motivo concreto, no «no hay nadie»."""
    mapa = mapa_asientos()
    if not mapa:
        malos = conflictos()
        if malos:
            return ("ningún asiento usable: " +
                    ", ".join(f"{m['vp']} tiene los dos motores puestos" for m in malos))
        return "ningún asiento tiene secuencia de HubSpot y remitente configurados"
    if not ids:
        agotados = [sid for sid in mapa if enviados_hoy(sid) >= TOPE_DIA_REMITENTE]
        if agotados:
            return (f"{', '.join(mapa[s]['nombre_vp'] for s in agotados)} ya llegó "
                    f"al tope de {TOPE_DIA_REMITENTE} correos de hoy")
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
        elif (c.get("li_seat") or "") not in mapa:
            faltas.append(f"{n}: su perfil no tiene secuencia de HubSpot")
        elif (c.get("hubspot_verdict") or "") == "rojo":
            faltas.append(f"{n}: 🔴 en HubSpot — no ir en frío")
        else:
            faltas.append(f"{n}: ya estaba inscrito en su secuencia")
    return "; ".join(faltas)


# ── carga en el CRM ─────────────────────────────────────────────────────────
TIPO_CARGADO = "cargado"


def firmografia(dominio: str) -> dict:
    """País y tamaño de la empresa, por dominio. `{}` si no se sabe.

    Sale de `organizations/enrich` de Apollo, que **cuesta 0 créditos** (a
    diferencia de `people/match`, que sí se cobra y por eso tiene presupuesto).
    Cobertura medida sobre los 14 dominios del primer lote: 14 de 14.

    El headcount de Apollo es una estimación y se le conoce el defecto de
    inflar: en esta misma muestra da 68.000 para `cummins.com` —que es la
    corporación global, correcto— y 1 para `cmd.com.mx`. Sirve para segmentar
    por tamaño, no para citarlo como dato duro.
    """
    dominio = (dominio or "").strip().lower()
    if not dominio:
        return {}
    key = apollo._key()
    if not key:
        return {}
    try:
        r = requests.post(f"{apollo.APOLLO_BASE}/organizations/enrich",
                          headers=apollo._headers(key), json={"domain": dominio},
                          timeout=30)
        if not r.ok:
            return {}
        org = (r.json() or {}).get("organization") or {}
    except requests.RequestException:
        return {}
    return {"country": (org.get("country") or "").strip(),
            "empleados": org.get("estimated_num_employees")}


def _ext_carga(contact_id: int) -> str:
    return f"hs-load:{contact_id}"


def objetivos_carga(por_asiento: int = 0, ids: list[int] | None = None) -> list[dict]:
    """A quién falta por subir al CRM. Mismos frenos, otra pregunta.

    `objetivos()` pregunta «a quién falta inscribir»; esto pregunta «a quién
    falta cargar», que son cosas distintas: cargar es reversible (se archiva) e
    inscribir no lo es. Por eso la carga va por su lado y puede correr antes de
    que la plantilla esté lista.

    No filtra por etapa `nuevo`: se carga a quien ya recibió la invitación, que
    es lo que hace verdad la casilla de conexión de LinkedIn.
    """
    mapa = mapa_asientos()
    if not mapa:
        return []
    campos = ("SELECT id, nombre, empresa, cargo, email, celular, linkedin, li_seat, "
              "       stage, hubspot_contact_id FROM contacts WHERE ")
    frenos = ("COALESCE(email,'')<>'' AND COALESCE(li_seat,'')<>'' "
              "AND COALESCE(hubspot_verdict,'') <> 'rojo' "
              "AND COALESCE(hubspot_contact_id,'')='' ")
    if ids:
        # Selección explícita: no se filtra por etapa —quien eligió un contacto
        # concreto ya decidió— pero los frenos duros se mantienen.
        filas = store.fetch_all(
            f"{campos} id IN ({','.join('?' * len(ids))}) AND {frenos} ORDER BY id",
            tuple(ids))
    else:
        marcas = ",".join("?" * len(ETAPAS))
        filas = store.fetch_all(
            f"{campos} stage IN ({marcas}) AND {frenos} "
            f"ORDER BY (stage='conectado') DESC, id DESC", tuple(ETAPAS))
    cupo = {sid: (por_asiento or 10**6) for sid in mapa}
    out = []
    for f in filas:
        info = mapa.get(f["li_seat"])
        if info is None or cupo.get(f["li_seat"], 0) <= 0:
            continue
        cupo[f["li_seat"]] -= 1
        out.append({**f, **info})
    return out


def cargar(por_asiento: int = 5, simular: bool = False,
           ids: list[int] | None = None) -> dict:
    """Sube los prospectos al CRM: empresa primero, contacto después, asociados.

    El orden importa. La empresa se resuelve por el **dominio del correo** de
    cada contacto, no por el nombre: «Cummins México» vive en esta base con dos
    dominios (`cummins.com` y `motorescummins.com`) que en HubSpot son dos
    fichas distintas, y agruparlas por nombre metería a la filial dentro de la
    corporación.

    No escribe nada en HubSpot que no sea reversible: crear se deshace
    archivando. Lo que no se hace aquí es inscribir en la secuencia — eso sí es
    irreversible y va por `inscribir()`.
    """
    parte: dict = {"objetivos": 0, "contactos_creados": 0, "contactos_completados": 0,
                   "empresas_creadas": 0, "empresas_existentes": 0, "asociados": 0,
                   "sin_dominio": 0, "errores": [], "detalle": [], "error": ""}
    objs = objetivos_carga(por_asiento, ids)
    parte["objetivos"] = len(objs)
    if not objs:
        parte["error"] = ("no hay nadie por cargar: o ya están todos en el CRM, "
                          "o ningún asiento tiene este carril configurado")
        return parte

    if simular:
        for o in objs:
            parte["detalle"].append({
                "nombre": o["nombre"], "empresa": o["empresa"], "vp": o["nombre_vp"],
                "dominio": hsq.dominio_de(o["email"]) or "— personal —",
                "cargo": o["cargo"], "linkedin": bool(o["linkedin"]),
                "telefono": bool(o["celular"]),
                "conexion_li": "aceptó" if o["stage"] == "conectado" else "invitado"})
        return parte

    ok, detalle = hsq.asegurar_propiedad_conexion()
    parte["propiedad_conexion"] = detalle
    if not ok:
        parte["error"] = detalle
        return parte
    # `fuente` existía en contactos pero no en empresas: el 400 se llevó por
    # delante a los 7 del primer lote que necesitaban empresa nueva.
    ok_f, det_f = hsq.asegurar_propiedad_fuente_empresas()
    parte["propiedad_fuente_empresas"] = det_f if ok_f else f"⚠ {det_f}"
    try:
        owners = hsq.owners_por_correo()
        encontrados = hsq.resolver_ids([o["email"] for o in objs])
    except hsq.HubSpotError as e:
        parte["error"] = f"HubSpot no responde: {e}"
        return parte

    # Un dominio se busca una sola vez por corrida: Procolombia, Cummins,
    # Coservipp y Creel aparecen dos veces cada uno en el primer lote.
    empresas: dict[str, str] = {}
    # Y de las que YA existían se guarda cómo estaban, para devolverlas igual.
    # Una empresa que ya está en el CRM no es nuestra: se le añade el contacto y
    # nada más. Las que creamos no llevan foto — son nuestras desde cero.
    fotos: dict[str, dict] = {}

    for o in objs:
        correo = (o["email"] or "").strip().lower()
        dom = hsq.dominio_de(correo)
        owner = owners.get(o["remitente"], {}).get("owner_id", "")
        fila = {"nombre": o["nombre"], "empresa": o["empresa"], "vp": o["nombre_vp"],
                "dominio": dom or "— personal —"}
        try:
            # 1 · la empresa
            cid_emp = empresas.get(dom, "") if dom else ""
            if not dom:
                parte["sin_dominio"] += 1
                fila["empresa_hs"] = "sin dominio corporativo: no se crea"
            elif cid_emp:
                fila["empresa_hs"] = "ya resuelta en este lote"
            else:
                # La firmografía va como función: solo se sale a Apollo si de
                # verdad hay que crear la empresa.
                cid_emp, creada, _ = hsq.asegurar_empresa(
                    o["empresa"], dom, owner, lambda d=dom: firmografia(d))
                empresas[dom] = cid_emp
                parte["empresas_creadas" if creada else "empresas_existentes"] += 1
                fila["empresa_hs"] = "creada" if creada else "ya estaba"
                if not creada and cid_emp:
                    fotos[cid_emp] = hsq.foto_empresa(cid_emp)

            # 2 · el contacto, con todo lo que sabemos de él
            enc = encontrados.get(correo)
            if enc:
                hs_id = enc["id"]
                escritos = hsq.actualizar_contacto(hs_id, o)
                parte["contactos_completados"] += 1 if escritos else 0
                fila["contacto"] = ("completado: " + ", ".join(escritos)) if escritos \
                    else "ya estaba completo"
            else:
                hs_id, creado = hsq.crear_contacto(o, owner)
                parte["contactos_creados"] += 1 if creado else 0
                fila["contacto"] = "creado" if creado else "ya existía (409)"
            store.set_hubspot_contact_id(o["id"], hs_id)
            fila["hubspot_id"] = hs_id

            # 3 · atarlos. Lo que la asociación le cambie a una empresa que ya
            # existía se deshace al final, no aquí: HubSpot propaga tarde.
            if cid_emp:
                hsq.asociar_a_empresa(hs_id, cid_emp)
                parte["asociados"] += 1
        except hsq.HubSpotError as e:
            parte["errores"].append({"nombre": o["nombre"], "error": str(e)})
            fila["error"] = str(e)
            parte["detalle"].append(fila)
            continue

        store.add_activity(o["id"], CANAL, TIPO_CARGADO, store.now_iso(),
                           ext_id=_ext_carga(o["id"]),
                           meta={"hubspot_contact_id": hs_id, "empresa_hs": cid_emp,
                                 "dominio": dom, "seat": o["li_seat"],
                                 "vp": o["nombre_vp"], "stage": o["stage"]})
        parte["detalle"].append(fila)

    # Último paso: devolver las empresas preexistentes a como estaban. HubSpot
    # propaga el dueño del contacto a la empresa cuando esta no tiene ninguno, y
    # lo hace más de un minuto después de asociar — de ahí la espera única.
    devueltas = hsq.devolver_empresas(fotos)
    parte["empresas_restauradas"] = len(devueltas)
    if devueltas:
        parte["detalle_restauradas"] = devueltas
    return parte


# ── la corrida ──────────────────────────────────────────────────────────────
# Etapas del ciclo de vida que frenan aunque el veredicto guardado diga otra
# cosa: el veredicto puede tener semanas y el `batch/read` trae la de hoy gratis.
ETAPAS_ROJAS = ("customer",)


def inscribir(limite: int = 0, simular: bool = False,
              ids: list[int] | None = None) -> dict:
    """Inscribe en la secuencia de HubSpot de su vendedor.

    Orden: resolver ids de todo el lote de una vez → crear en el CRM al que no
    exista → inscribir uno a uno con pausa. Cada paso se persiste antes del
    siguiente, así que una caída no repite inscripciones.
    """
    mapa = mapa_asientos()
    parte: dict = {"objetivos": 0, "inscritos": 0, "saltados": 0, "creados": 0,
                   "error": "", "aviso": "", "detalle": [],
                   "conflictos": conflictos(), "remitentes_malos": {}}

    # Los owners primero: de ahí salen los dos números que hacen falta después
    # —`owner_id` para crear el contacto y `user_id` para hablar con la
    # Sequences API, que no acepta ninguna llamada sin decir a nombre de quién.
    try:
        owners = hsq.owners_por_correo()
    except hsq.HubSpotError as e:
        parte["error"] = f"no pude leer los owners de HubSpot: {e}"
        return parte
    quien = next((owners.get(i["remitente"], {}).get("user_id")
                  for i in mapa.values() if owners.get(i["remitente"], {}).get("user_id")), "")

    # El ensayo no llama a la Sequences API, así que no exige sus scopes: sirve
    # justamente para ver a quién le tocaría ANTES de montar nada en HubSpot.
    # La corrida real sí se para en seco.
    pre = hsq.preflight(quien)
    if not pre.get("ok"):
        if not simular:
            parte["error"] = pre.get("error", "HubSpot no está disponible")
            return parte
        parte["aviso"] = pre.get("error", "HubSpot no está disponible")

    mapa, malos = ({}, {}) if not mapa else _remitentes_ok(mapa)
    parte["remitentes_malos"] = malos

    objs = objetivos(limite, ids, mapa=mapa)
    parte["objetivos"] = len(objs)
    if not objs:
        parte["error"] = (
            "; ".join(malos.values()) if malos and not mapa
            else _por_que_ninguno(ids))
        return parte

    if simular:
        # El `batch/read` es de lectura: el ensayo sigue sin escribir nada en
        # HubSpot y a cambio el conteo de «por crear» es el de verdad. Con la
        # columna local basta no: está vacía hasta la primera corrida real, así
        # que daría por nuevo a todo el mundo.
        try:
            enc = hsq.resolver_ids([o["email"] for o in objs])
        except hsq.HubSpotError as e:
            enc = {}
            parte["aviso"] = (parte["aviso"] + " · " if parte["aviso"] else "") + \
                f"no pude leer los contactos en HubSpot ({e}): «por crear» es una cota alta"
        parte["detalle"] = [
            {"nombre": o["nombre"], "empresa": o["empresa"], "stage": o["stage"],
             "vp": o["nombre_vp"], "remitente": o["remitente"],
             "en_crm": (o["email"] or "").strip().lower() in enc,
             "ya_en_secuencia": bool(
                 (enc.get((o["email"] or "").strip().lower()) or {}).get("enrolled"))}
            for o in objs]
        parte["por_crear"] = sum(1 for d in parte["detalle"] if not d["en_crm"])
        parte["ya_en_secuencia"] = sum(1 for d in parte["detalle"] if d["ya_en_secuencia"])
        return parte

    # Un solo batch/read para todo el lote: resuelve el id y trae de paso las
    # dos señales de freno (etapa de hoy y si ya está en otra secuencia).
    try:
        encontrados = hsq.resolver_ids([o["email"] for o in objs])
    except hsq.HubSpotError as e:
        parte["error"] = f"no pude leer los contactos en HubSpot: {e}"
        return parte

    for o in objs:
        correo = (o["email"] or "").strip().lower()
        hs_id = (o.get("hubspot_contact_id") or "").strip()
        enc = encontrados.get(correo)
        fila = {"nombre": o["nombre"], "empresa": o["empresa"],
                "vp": o["nombre_vp"], "remitente": o["remitente"]}

        def saltar(motivo: str) -> None:
            store.add_activity(o["id"], CANAL, TIPO_SALTADO, store.now_iso(),
                               ext_id=_ext_id(o["secuencia"], o["id"]),
                               meta={"secuencia": o["secuencia"], "vp": o["nombre_vp"],
                                     "seat": o["li_seat"], "stage": o["stage"],
                                     "remitente": o["remitente"], "motivo": motivo})
            parte["saltados"] += 1
            parte["detalle"].append({**fila, "resultado": f"saltado: {motivo}"})

        if enc:
            hs_id = enc["id"]
            if hs_id != (o.get("hubspot_contact_id") or ""):
                store.set_hubspot_contact_id(o["id"], hs_id)
            if enc["lifecyclestage"] in ETAPAS_ROJAS:
                store.set_hubspot(o["id"], "rojo",
                                  f"es {enc['lifecyclestage']} en HubSpot", force=True)
                saltar(f"en HubSpot ya es {enc['lifecyclestage']}")
                continue
            if enc["enrolled"]:
                # Límite duro de HubSpot: un contacto, una secuencia. Y sacarlo
                # de la otra solo se puede a mano desde la UI.
                saltar("ya está en otra secuencia de HubSpot")
                continue
        else:
            try:
                hs_id, creado = hsq.crear_contacto(
                    o, owners.get(o["remitente"], {}).get("owner_id", ""))
            except hsq.HubSpotError as e:
                saltar(f"no pude crearlo en HubSpot: {e}")
                continue
            if not hs_id:
                saltar("HubSpot no devolvió id al crearlo")
                continue
            store.set_hubspot_contact_id(o["id"], hs_id)
            parte["creados"] += 1 if creado else 0

        try:
            resp = hsq.inscribir(o["secuencia"], hs_id, o["remitente"])
        except hsq.HubSpotError as e:
            saltar(f"{e}")
            continue

        store.add_activity(o["id"], CANAL, TIPO, store.now_iso(),
                           ext_id=_ext_id(o["secuencia"], o["id"]),
                           meta={"secuencia": o["secuencia"], "vp": o["nombre_vp"],
                                 "seat": o["li_seat"], "stage": o["stage"],
                                 "remitente": o["remitente"],
                                 "hubspot_contact_id": hs_id,
                                 "respuesta": {k: v for k, v in (resp or {}).items()
                                               if k in ("id", "status", "enrollmentId")}})
        parte["inscritos"] += 1
        parte["detalle"].append({**fila, "resultado": "inscrito"})
        time.sleep(PAUSA_SEG)

    return parte


# ----------------------------- carril automático ---------------------------
def activa() -> bool:
    return store.kv_get("secuencias_hs_auto", "0") == "1"


def set_activa(v: bool) -> None:
    store.kv_set("secuencias_hs_auto", "1" if v else "0")


# Nombres de las secuencias, cacheados por remitente. La pantalla se refresca
# cada 30 s y el id («309396824») no le dice nada a quien mira: el nombre sí.
# Pero listar en cada refresco serían dos llamadas por minuto para un dato que
# no cambia. Y el listado es POR USUARIO: HubSpot no tiene uno «del portal».
_CACHE_TTL = 600.0
_cache_nombres: dict[str, tuple[float, dict[str, str]]] = {}


def nombre_secuencia(user_id: str, seq_id: str) -> str:
    if not user_id or not seq_id:
        return ""
    ts, datos = _cache_nombres.get(user_id, (0.0, {}))
    if not datos or (time.monotonic() - ts) >= _CACHE_TTL:
        nuevos = hsq.nombres_secuencias(user_id)
        if nuevos:                    # si falla se conserva lo último bueno
            datos = nuevos
            _cache_nombres[user_id] = (time.monotonic(), datos)
    return datos.get(str(seq_id), "")


def estado() -> dict:
    mapa = mapa_asientos()
    try:
        owners = hsq.owners_por_correo()
    except hsq.HubSpotError:
        owners = {}
    return {"activo": activa(),
            "etapas": list(ETAPAS),
            "tope_automatico": TOPE_POR_TICK,
            "tope_dia": TOPE_DIA_REMITENTE,
            # Solo asientos con dueño: un hueco reservado sin VP asignado no es
            # una alarma de configuración, es un hueco.
            "asientos": [{"seat_id": s.id, "vp": s.etiqueta,
                          "secuencia": s.secuencia_hs or "",
                          "secuencia_nombre": nombre_secuencia(
                              owners.get(s.remitente_hs or "", {}).get("user_id", ""),
                              s.secuencia_hs or ""),
                          "remitente": s.remitente_hs or "",
                          "enviados_hoy": enviados_hoy(s.id),
                          "listo": s.id in mapa}
                         for s in seats.en_uso() if s.configurado],
            "conflictos": conflictos(),
            "pendientes": len(objetivos()),
            "ultima_corrida": store.kv_get("secuencias_hs_ts", ""),
            "ultimo_parte": store.kv_get("secuencias_hs_parte", "")}


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
    parte = inscribir(limite=TOPE_POR_TICK)
    store.kv_set("secuencias_hs_ts", store.now_iso())
    store.kv_set("secuencias_hs_parte",
                 json.dumps(parte, ensure_ascii=False, default=str)[:4000])
    return {"corrio": True, **parte}
