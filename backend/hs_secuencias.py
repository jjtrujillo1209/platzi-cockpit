"""Cliente de HubSpot para el carril de correo: crear contactos e inscribir.

`hubspot_check.py` se queda de solo lectura, como dice su encabezado: aquí vive
todo lo que escribe. Comparte con él las credenciales (`hc._token()` /
`hc._headers()`) — mismo patrón que ya usa `validar_hubspot.py`, misma Private
App, un solo sitio donde cambiar el token.

## Por qué esta ruta y no otra

La Private App **no puede enviar un correo suelto**: eso es el add-on
transaccional, que este portal no tiene. Lo que sí puede es inscribir a un
contacto en una secuencia de Sales Hub, y esa secuencia envía desde la **bandeja
de Google conectada del vendedor**. HubSpot no retransmite nada: le pide a Gmail
que envíe. Es lo único compatible con el SPF de `platzi.com`
(`include:_spf.google.com … -all`) y con su DMARC en `p=quarantine` — cualquier
otro transporte exigiría tocar el DNS del dominio raíz.

Consecuencia práctica: **el copy no viaja en la inscripción**. La API acepta
tres campos y ninguno es texto. El cuerpo vive en la plantilla de la secuencia,
escrita a mano en la UI de HubSpot. Es un contrato implícito que no se puede
verificar por API: HubSpot no expone el contenido de las plantillas.

## Lo que falla en runtime y no con un 403 limpio

El `senderEmail` necesita tres cosas a la vez —asiento de pago de Sales/Service,
bandeja personal conectada, y acceso a esa secuencia— y HubSpot **no tiene
ningún endpoint** que liste las bandejas conectadas de verdad. Las dos primeras
sí se leen de `/crm/v3/objects/users`, y por eso `remitentes_validos()` se
consulta antes de cada lote: descubrirlo a mitad de 20 inscripciones cuesta
inscripciones a medias, y **no existe API para desinscribir**.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import requests

import hubspot_check as hc

BASE = "https://api.hubapi.com"
TIMEOUT = 30

# Asientos que permiten enviar por secuencia. Un usuario sin uno de estos, o sin
# la bandeja conectada, falla en runtime (400/500), no con un 403 limpio.
ASIENTOS_ENVIO = ("sales-enterprise", "sales-professional",
                  "service-enterprise", "service-professional")

# La Sequences API convive en dos rutas: la versionada por fecha y el alias v4.
# Cuál contesta no se sabe hasta tener los scopes puestos, así que se prueban en
# orden y se recuerda la que funcione. Un 403 SÍ cuenta como «esta es la buena»:
# la ruta existe y lo único que falta es el permiso.
SEQ_BASES = ("/automation/sequences/2026-03", "/automation/v4/sequences")
_seq_base: Optional[str] = None

SCOPES_SEQ = ("automation.sequences.read", "automation.sequences.enrollments.write")

# Etapa con la que nace un prospecto frío creado por el cockpit. `subscriber` es
# el valor interno de lo que en pantalla se llama «Prospect»: la API exige el
# valor, no la etiqueta (mandar «Prospect» devuelve 400 INVALID_OPTION).
LIFECYCLE_NUEVO = "subscriber"
# Propiedad de texto libre que ya existe en el portal. Se reusa a propósito en
# vez de crear una nueva: es lo que permite listar y revertir lo que escribió
# esta máquina si mañana cambia el criterio.
PROP_FUENTE = "fuente"
FUENTE = "outbound"

# Cómo llegó el lead. Es un desplegable cerrado (Inbound / Outbound / Other), así
# que el valor tiene que ser exactamente uno de esos tres: la API rechaza
# cualquier otro con 400 INVALID_OPTION. Solo existe en contactos y deals, no en
# empresas.
PROP_ESTRATEGIA = "lead_strategy"
ESTRATEGIA = "Outbound"

# La casilla que marca quién recibió invitación de LinkedIn y quién ya aceptó.
# La define `hubspot_conexion.py`; aquí solo se rellena al crear el contacto,
# para que nazca con el dato en vez de esperar a que pase el marcador.
PROP_CONEXION_LI = "conexion_linkedin_outbound"

# Perfil de LinkedIn. El portal tiene CINCO propiedades candidatas y la que usa
# el equipo es `linkedin_profile`: 24.814 contactos la tienen, contra 3.205 de
# `hs_linkedin_url` (la nativa de HubSpot) y 7.981 de `linkedin` (heredada, del
# grupo «migración»). Escribir en la nativa dejaba el dato donde nadie mira.
#
# Se escriben las dos: `linkedin_profile` porque es donde el equipo lo busca, y
# `hs_linkedin_url` porque es la que leen las integraciones de HubSpot. No se
# toca `linkedinconnections`, que es el NÚMERO de contactos del perfil y no
# tiene nada que ver.
PROPS_LINKEDIN = ("linkedin_profile", "hs_linkedin_url")

# Dominios de correo personal: no identifican a una empresa, así que con uno de
# estos no se busca ni se crea nada en el CRM de empresas.
GENERICOS = {"gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
             "yahoo.com", "yahoo.es", "icloud.com", "me.com", "live.com",
             "aol.com", "protonmail.com", "proton.me", "msn.com"}

# contacto → empresa. El typeId 1 es «Primary»: además de asociar, deja la
# empresa como la principal del contacto, que es lo que hace que salga en su
# ficha y que el contacto aparezca bajo la empresa. El 279 asocia sin marcarla.
ASOC_CONTACTO_EMPRESA = 1

# Cuánto tarda HubSpot en propagar el dueño tras asociar. **Medido, y es la
# razón de que esto sea un barrido al final y no una comprobación por contacto**:
# la carga corrió a las 15:15:3x y el propagador escribió a las 15:16:49, más de
# un minuto después. Una comprobación inmediata da siempre un falso «no cambió
# nada» — la primera versión de este guarda pasó sus pruebas por eso.
PAUSA_PROPAGACION = 105.0


def dominio_de(email: str) -> str:
    """Dominio corporativo del correo, o «» si es personal."""
    d = (email or "").split("@")[-1].strip().lower()
    return "" if not d or "." not in d or d in GENERICOS else d


class HubSpotError(RuntimeError):
    def __init__(self, status: int, mensaje: str, cuerpo: str = ""):
        super().__init__(f"HTTP {status}: {mensaje}")
        self.status = status
        self.mensaje = mensaje
        self.cuerpo = cuerpo


def configured() -> bool:
    return bool(hc._token())


def _request(method: str, path: str, body: Optional[dict] = None,
             params: Optional[dict] = None) -> Any:
    tok = hc._token()
    if not tok:
        raise HubSpotError(0, "no hay token de HubSpot en el entorno")
    try:
        r = requests.request(method, BASE + path, headers=hc._headers(tok),
                             json=body, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise HubSpotError(0, f"red: {e.__class__.__name__}") from e
    if r.status_code >= 400:
        raise HubSpotError(r.status_code, _mensaje_de(r.text), r.text)
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _mensaje_de(cuerpo: str) -> str:
    try:
        d = json.loads(cuerpo)
        return (d.get("message") or d.get("error") or cuerpo)[:300]
    except Exception:
        return (cuerpo or "")[:300]


def _seq_request(method: str, sufijo: str, **kw) -> Any:
    """Llama a la Sequences API probando las dos bases conocidas."""
    global _seq_base
    bases = [_seq_base] if _seq_base else list(SEQ_BASES)
    ultimo: Optional[HubSpotError] = None
    for base in bases:
        try:
            out = _request(method, base + sufijo, **kw)
            _seq_base = base
            return out
        except HubSpotError as e:
            if e.status == 404 and not _seq_base:
                ultimo = e
                continue              # ruta equivocada: prueba la siguiente
            _seq_base = base
            raise
    raise ultimo if ultimo else HubSpotError(404, "sin ruta de secuencias")


# ── remitentes ──────────────────────────────────────────────────────────────
def remitentes_validos() -> dict[str, dict]:
    """Usuarios que PUEDEN enviar por secuencia, indexados por correo.

    Un owner activo no basta: hace falta asiento de Sales/Service **y** la
    bandeja personal conectada. En este portal solo 16 de 101 usuarios lo
    cumplen, así que comprobarlo antes no es paranoia.
    """
    salida: dict[str, dict] = {}
    campos = ["hs_email", "hs_assigned_seats", "hs_connected_email_status",
              "hs_given_name", "hs_family_name"]
    after: Optional[str] = None
    while True:
        params: dict[str, Any] = {"limit": 100, "properties": ",".join(campos)}
        if after:
            params["after"] = after
        data = _request("GET", "/crm/v3/objects/users", params=params)
        for r in (data or {}).get("results") or []:
            p = r.get("properties") or {}
            correo = (p.get("hs_email") or "").strip().lower()
            if not correo:
                continue
            asientos = (p.get("hs_assigned_seats") or "").lower()
            conectado = (p.get("hs_connected_email_status") or "").upper() == "ENABLED"
            salida[correo] = {
                "email": correo,
                "nombre": " ".join(x for x in (p.get("hs_given_name"),
                                               p.get("hs_family_name")) if x).strip(),
                "asientos": p.get("hs_assigned_seats") or "",
                "conectado": conectado,
                "puede_enviar": conectado and any(a in asientos for a in ASIENTOS_ENVIO),
            }
        after = (((data or {}).get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break
    return salida


def owners_por_correo() -> dict[str, dict]:
    """correo → {owner_id, user_id}. Los dos, porque no son lo mismo y cada API
    quiere el suyo:

      · `owner_id` (el `id` de owners) → va en `hubspot_owner_id` al crear el
        contacto.
      · `user_id` (el `userId` de owners) → lo exige la Sequences API. Y no es
        tampoco el `hs_object_id` de `/crm/v3/objects/users`: son tres números
        distintos para la misma persona.
    """
    salida: dict[str, dict] = {}
    after: Optional[str] = None
    while True:
        params: dict[str, Any] = {"limit": 100}
        if after:
            params["after"] = after
        data = _request("GET", "/crm/v3/owners", params=params)
        for o in (data or {}).get("results") or []:
            correo = (o.get("email") or "").strip().lower()
            if correo and not o.get("archived"):
                salida[correo] = {"owner_id": str(o.get("id")),
                                  "user_id": str(o.get("userId") or "")}
        after = (((data or {}).get("paging") or {}).get("next") or {}).get("after")
        if not after:
            break
    return salida


# ── contactos ───────────────────────────────────────────────────────────────
PROPS_LECTURA = ["email", "lifecyclestage", "hs_sequences_is_enrolled",
                 "hubspot_owner_id", "firstname", "lastname"]


def resolver_ids(emails: list[str]) -> dict[str, dict]:
    """correo → {id, lifecyclestage, enrolled}. Una llamada por lote.

    El `batch/read` por `idProperty=email` resuelve el id y de paso trae gratis
    las dos señales de freno (etapa del ciclo de vida y si ya está en una
    secuencia), que si no serían dos llamadas más por contacto.

    Los que no existen sencillamente no vienen en `results`: HubSpot los reporta
    en `errors` y eso NO es un fallo de la llamada.
    """
    limpios = sorted({(e or "").strip().lower() for e in emails if (e or "").strip()})
    salida: dict[str, dict] = {}
    for i in range(0, len(limpios), 100):
        trozo = limpios[i:i + 100]
        data = _request("POST", "/crm/v3/objects/contacts/batch/read",
                        body={"idProperty": "email", "properties": PROPS_LECTURA,
                              "inputs": [{"id": e} for e in trozo]})
        for r in (data or {}).get("results") or []:
            p = r.get("properties") or {}
            correo = (p.get("email") or "").strip().lower()
            if not correo:
                continue
            salida[correo] = {
                "id": str(r.get("id")),
                "lifecyclestage": (p.get("lifecyclestage") or "").strip().lower(),
                "enrolled": str(p.get("hs_sequences_is_enrolled")).lower() == "true",
                "owner": p.get("hubspot_owner_id") or "",
            }
    return salida


# ── empresas ────────────────────────────────────────────────────────────────
def buscar_empresa(dominio: str) -> Optional[dict]:
    """La empresa de ese dominio, si está. El dominio es la única llave fiable.

    Por nombre no se busca: «Cummins México» aparece en esta base con dos
    dominios distintos (`cummins.com` y `motorescummins.com`), que en HubSpot
    son —correctamente— dos fichas. Agruparlas por el nombre metería a gente de
    la filial en la corporación global.
    """
    if not dominio:
        return None
    data = _request("POST", "/crm/v3/objects/companies/search", body={
        "filterGroups": [{"filters": [
            {"propertyName": "domain", "operator": "EQ", "value": dominio}]}],
        "properties": ["name", "domain", "lifecyclestage", "hubspot_owner_id"],
        "limit": 1})
    res = (data or {}).get("results") or []
    return res[0] if res else None


def crear_empresa(nombre: str, dominio: str, owner_id: str = "",
                  firmografia: Optional[dict] = None) -> str:
    props = {"name": (nombre or dominio).strip(), "domain": dominio,
             PROP_FUENTE: FUENTE}
    # País y tamaño. HubSpot NO enriquece sola las empresas creadas por API
    # —comprobado: las 6 del primer lote quedaron con country y
    # numberofemployees vacíos—, así que si no se lo damos aquí, no lo tiene
    # nadie. Solo al crear: una empresa que ya existía no se toca.
    f = firmografia or {}
    if (f.get("country") or "").strip():
        props["country"] = f["country"].strip()
    if f.get("empleados"):
        props["numberofemployees"] = str(f["empleados"])
    if owner_id:
        props["hubspot_owner_id"] = str(owner_id)
    try:
        r = _request("POST", "/crm/v3/objects/companies", body={"properties": props})
    except HubSpotError as e:
        # `fuente` es una propiedad de CONTACTOS: en este portal no existía en
        # empresas y el 400 tumbó el primer lote entero. Se intenta crearla
        # (ver `asegurar_propiedad_fuente_empresas`), pero si no se puede, la
        # marca no vale una empresa sin crear: se reintenta sin ella.
        if "PROPERTY_DOESNT_EXIST" not in (e.cuerpo or "") or PROP_FUENTE not in (e.cuerpo or ""):
            raise
        props.pop(PROP_FUENTE, None)
        r = _request("POST", "/crm/v3/objects/companies", body={"properties": props})
    return str((r or {}).get("id") or "")


def asegurar_empresa(nombre: str, dominio: str, owner_id: str = "",
                     firmografia: Any = None) -> tuple[str, bool, str]:
    """(company_id, se_creó, nombre_en_hubspot). Sin dominio, no se toca nada.

    Con un correo personal no hay empresa que buscar ni que crear: inventar una
    ficha «gmail.com» ensucia el CRM y no agrupa a nadie.

    `firmografia` admite un dict o una **función sin argumentos**. Si es función
    solo se llama cuando de verdad hay que crear la empresa: para una que ya
    existe no se usaría —no se le tocan propiedades— y sería salir a la red
    para tirar el resultado.
    """
    if not dominio:
        return "", False, ""
    yaesta = buscar_empresa(dominio)
    if yaesta:
        return str(yaesta["id"]), False, (yaesta.get("properties") or {}).get("name") or ""
    datos = firmografia() if callable(firmografia) else firmografia
    return (crear_empresa(nombre, dominio, owner_id, datos), True,
            (nombre or dominio).strip())


# Lo que una empresa PREEXISTENTE no puede cambiar porque la toquemos. No basta
# con «no hacerle PATCH»: este portal tiene encendida la propagación automática
# del dueño del contacto a la empresa, y al asociar reasignó tres cuentas a
# Fabrizzio —«ContactOwnerToCompanyPropagator: triggered by vid=…»— sin que el
# cockpit escribiera nada. Estaban sin dueño, así que no se perdió ninguna, pero
# la próxima vez podría tocarle a una que sí lo tiene.
PROPS_EMPRESA_INTOCABLES = ("hubspot_owner_id", "lifecyclestage")


def foto_empresa(company_id: str) -> dict:
    """Cómo está la empresa ANTES de que la toquemos. Solo lo intocable."""
    d = _request("GET", f"/crm/v3/objects/companies/{company_id}",
                 params={"properties": ",".join(PROPS_EMPRESA_INTOCABLES)}) or {}
    p = d.get("properties") or {}
    return {k: (p.get(k) or "") for k in PROPS_EMPRESA_INTOCABLES}


def restaurar_empresa(company_id: str, foto: dict) -> list[str]:
    """Devuelve la empresa a como estaba. Lista lo que hubo que restaurar.

    Se comprueba después de asociar porque la propagación de HubSpot es un
    efecto del evento, no de nuestra llamada. Verificado: el propagador no
    vuelve a dispararse solo, así que restaurar una vez basta.
    """
    if not foto:
        return []
    ahora = foto_empresa(company_id)
    cambiadas = {k: v for k, v in foto.items() if ahora.get(k, "") != v}
    if not cambiadas:
        return []
    _request("PATCH", f"/crm/v3/objects/companies/{company_id}",
             body={"properties": cambiadas})
    return sorted(cambiadas)


def asociar_a_empresa(contact_id: str, company_id: str) -> None:
    """Deja la empresa como la principal del contacto. Idempotente: repetirlo
    no duplica la asociación.

    Lo que la asociación desencadene en la empresa NO se arregla aquí: la
    propagación de HubSpot llega más de un minuto después. Se deshace al final
    del lote, con `devolver_empresas`.
    """
    _request("PUT",
             f"/crm/v4/objects/contacts/{contact_id}/associations/companies/{company_id}",
             body=[{"associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": ASOC_CONTACTO_EMPRESA}])


def devolver_empresas(fotos: dict[str, dict], esperar: bool = True) -> dict[str, list[str]]:
    """Deja como estaban las empresas que ya existían. {company_id: campos}.

    Un solo barrido al final del lote: esperar una vez por corrida en lugar de
    una vez por contacto es la diferencia entre minuto y medio y media hora.
    """
    if not fotos:
        return {}
    if esperar:
        time.sleep(PAUSA_PROPAGACION)
    out = {}
    for cid, foto in fotos.items():
        try:
            campos = restaurar_empresa(cid, foto)
        except HubSpotError as e:
            campos = [f"⚠ no pude devolverla: {e}"]
        if campos:
            out[cid] = campos
    return out


# ── propiedades del portal ──────────────────────────────────────────────────
def asegurar_propiedad_fuente_empresas() -> tuple[bool, str]:
    """`fuente` en EMPRESAS. En contactos ya existía; en empresas no, y por eso
    el primer lote se cayó con `PROPERTY_DOESNT_EXIST`. Es la misma marca, y sin
    ella no habría forma de listar las empresas que creó esta máquina."""
    try:
        _request("GET", f"/crm/v3/properties/companies/{PROP_FUENTE}")
        return True, "ya existía"
    except HubSpotError as e:
        if e.status != 404:
            return False, f"no pude consultarla: {e}"
    try:
        _request("POST", "/crm/v3/properties/companies", body={
            "name": PROP_FUENTE, "label": "Fuente",
            "description": "De dónde salió el registro. La escribe el cockpit.",
            "groupName": "companyinformation", "type": "string", "fieldType": "text"})
        return True, "creada"
    except HubSpotError as e:
        return False, f"no pude crearla: {e}"


def asegurar_propiedad_conexion() -> tuple[bool, str]:
    """Crea la casilla de conexión de LinkedIn si no existe. (ok, detalle).

    Tres estados en una casilla, y de ahí sale la tasa de aceptación:
    vacía = nunca se le invitó · false = invitado sin aceptar · true = aceptó.
    """
    try:
        _request("GET", f"/crm/v3/properties/contacts/{PROP_CONEXION_LI}")
        return True, "ya existía"
    except HubSpotError as e:
        if e.status != 404:
            return False, f"no pude consultarla: {e}"
    try:
        _request("POST", "/crm/v3/properties/contacts", body={
            "name": PROP_CONEXION_LI,
            "label": "Conexión LinkedIn (outbound Platzi)",
            "description": "Vacía = nunca invitado · No = invitado sin aceptar · "
                           "Sí = aceptó la invitación. La escribe el cockpit.",
            "groupName": "contactinformation",
            "type": "enumeration", "fieldType": "booleancheckbox",
            "options": [{"label": "Sí", "value": "true", "displayOrder": 0},
                        {"label": "No", "value": "false", "displayOrder": 1}]})
        return True, "creada"
    except HubSpotError as e:
        return False, f"no pude crearla: {e}"


_ID_EN_CONFLICTO = re.compile(r"Existing ID:\s*(\d+)")


def _props_contacto(datos: dict, owner_id: str = "") -> dict:
    """Todo lo que el cockpit sabe del prospecto, en lenguaje de HubSpot.

    Uno solo para crear y para completar: si fueran dos listas, el contacto
    creado hoy y el completado mañana acabarían con campos distintos.
    """
    nombre = (datos.get("nombre") or "").strip()
    partes = nombre.split()
    props = {
        "email": (datos.get("email") or "").strip().lower(),
        "firstname": partes[0] if partes else "",
        "lastname": " ".join(partes[1:]) if len(partes) > 1 else "",
        "company": (datos.get("empresa") or "").strip(),
        "jobtitle": (datos.get("cargo") or "").strip(),
        "lifecyclestage": LIFECYCLE_NUEVO,
        PROP_FUENTE: FUENTE,
        PROP_ESTRATEGIA: ESTRATEGIA,
    }
    tel = (datos.get("celular") or "").strip()
    if tel:
        # En las dos: `mobilephone` es lo que es —un móvil, que es lo que trae
        # el enriquecimiento— y `phone` es la que mira medio HubSpot por defecto
        # (vistas, filtros, el marcador). Dejarla vacía esconde el teléfono de
        # quien lo va a usar.
        props["mobilephone"] = tel
        props["phone"] = tel
    if (datos.get("linkedin") or "").strip():
        for prop in PROPS_LINKEDIN:
            props[prop] = datos["linkedin"].strip()
    # Nace ya marcado: se le invitó por LinkedIn —eso es lo que lo trajo aquí— y
    # `true` solo si además ya aceptó. Dejar la casilla vacía significaría
    # «nunca se le invitó», que es justo lo contrario.
    if datos.get("stage") in ("contactado", "conectado"):
        props[PROP_CONEXION_LI] = "true" if datos.get("stage") == "conectado" else "false"
    if owner_id:
        props["hubspot_owner_id"] = str(owner_id)
    return {k: v for k, v in props.items() if v}


def crear_contacto(datos: dict, owner_id: str = "") -> tuple[str, bool]:
    """Crea el prospecto en el CRM. Devuelve (contact_id, se_creó).

    Nace como **Prospect** (`subscriber`) y marcado con `fuente`, que es lo que
    después permite listar y revertir todo lo que escribió esta máquina.

    HubSpot no admite dos contactos con el mismo correo: si otro proceso lo
    creó entre el `batch/read` y este POST, contesta 409 con el id del que ya
    existe en el mensaje. Ese id es la respuesta correcta, no un error — hacerlo
    fallar dejaría al contacto fuera del carril para siempre.
    """
    props = _props_contacto(datos, owner_id)
    try:
        r = _request("POST", "/crm/v3/objects/contacts", body={"properties": props})
        return str((r or {}).get("id") or ""), True
    except HubSpotError as e:  # noqa: PERF203
        if e.status == 409:
            m = _ID_EN_CONFLICTO.search(e.mensaje or "") or \
                _ID_EN_CONFLICTO.search(e.cuerpo or "")
            if m:
                return m.group(1), False
        raise


# Lo que NUNCA se pisa en un contacto que ya existía en el CRM. El dueño y la
# etapa son de quien lo trabaja: sobrescribirlos con «Prospect, dueño el
# vendedor» le robaría la cuenta a otro comercial en silencio. Al crearlo sí se
# ponen, porque ahí no había nada que robar.
_SOLO_AL_CREAR = ("lifecyclestage", "hubspot_owner_id")


def actualizar_contacto(contact_id: str, datos: dict) -> list[str]:
    """Completa un contacto que ya estaba en el CRM. Devuelve qué campos escribió.

    Solo rellena, no corrige: si HubSpot ya tiene un valor en el campo, se
    respeta. El cockpit sabe del prospecto lo que sacó de LinkedIn y del
    enriquecimiento; quien lo tenga trabajado en el CRM sabe más.
    """
    props = _props_contacto(datos, owner_id="")
    for k in _SOLO_AL_CREAR:
        props.pop(k, None)
    if not props:
        return []
    actual = _request("GET", f"/crm/v3/objects/contacts/{contact_id}",
                      params={"properties": ",".join(props)}) or {}
    tenia = actual.get("properties") or {}
    nuevos = {k: v for k, v in props.items() if not (tenia.get(k) or "").strip()}
    if not nuevos:
        return []
    _request("PATCH", f"/crm/v3/objects/contacts/{contact_id}",
             body={"properties": nuevos})
    return sorted(nuevos)


# ── secuencias ──────────────────────────────────────────────────────────────
def listar_secuencias(user_id: str) -> list[dict]:
    """Secuencias visibles para ese usuario (propias + compartidas con él).

    `userId` es **obligatorio**: sin él la API responde 400 «query param userId
    may not be null». No existe un listado «del portal» — HubSpot devuelve lo
    que ve una persona concreta, así que hay que preguntar por el remitente.
    """
    if not str(user_id or "").strip():
        raise HubSpotError(400, "hace falta el userId del remitente para listar "
                                "secuencias (la API no tiene listado del portal)")
    data = _seq_request("GET", "", params={"limit": 100, "userId": str(user_id)})
    if isinstance(data, list):
        return data
    return list((data or {}).get("results") or [])


def nombres_secuencias(user_id: str) -> dict[str, str]:
    """id → nombre. Para que en pantalla salga «Outbound Fabrizzio» y no
    «309396824», que no le dice nada a quien mira."""
    try:
        return {str(s.get("id")): s.get("name") or "" for s in listar_secuencias(user_id)}
    except HubSpotError:
        return {}


def inscribir(sequence_id: str, contact_id: str, sender_email: str) -> dict:
    """Inscribe al contacto: esto ES el envío del primer paso.

    No hay vuelta atrás por API — HubSpot no expone ningún endpoint para
    desinscribir. Todo freno tiene que estar aplicado antes de esta línea.
    """
    return _seq_request(
        "POST", "/enrollments",
        body={"sequenceId": str(sequence_id), "contactId": str(contact_id),
              "senderEmail": sender_email}) or {}


def preflight(user_id: str = "") -> dict:
    """¿Está el carril en pie? Token y scopes, sin escribir nada.

    `user_id` es el de un remitente configurado: la Sequences API no tiene
    ninguna llamada «del portal», todas piden a nombre de quién. Sin uno, lo
    único que se puede comprobar es que hay token.
    """
    if not configured():
        return {"ok": False, "error": "no hay token de HubSpot en el entorno"}
    if not str(user_id or "").strip():
        return {"ok": False, "error": "no hay ningún remitente con userId para "
                                      "comprobar el acceso a secuencias"}
    try:
        listar_secuencias(user_id)
    except HubSpotError as e:
        if e.status == 403:
            return {"ok": False, "scopes": False,
                    "error": "faltan los scopes " + " y ".join(SCOPES_SEQ) +
                             " en la Private App"}
        return {"ok": False, "error": f"secuencias: {e}"}
    return {"ok": True, "scopes": True}
