"""Las dos señales que cierran el circuito: aceptación y respuesta por LinkedIn.

Antes el cockpit solo veía el envío. Una invitación aceptada y un mensaje de
vuelta —lo que de verdad indica que el outreach está funcionando— no llegaban a
la base, así que el paso que escribe al que aceptó nunca disparaba y una
respuesta se quedaba en la bandeja del VP sin que nadie se enterara.

## Cómo se deduce una aceptación

De la lista de invitaciones PENDIENTES de ese perfil, no revisitando perfiles.
Si invitamos a alguien y ya no está en esa lista, aceptó. Es una inferencia y se
guarda marcada como tal (`fuente: salio-de-enviadas`), porque en teoría también
se sale de ahí al retirar la invitación o cuando caduca — nosotros no retiramos
y la caducidad tarda meses, así que en la práctica significa aceptación.

**Seguro contra el falso positivo:** si la lista se parsea incompleta (LinkedIn
pagina), los que no alcanzamos a leer parecerían aceptados. Por eso se compara
con el total que LinkedIn declara —«People (33)»— y si no cuadra NO se deduce
ninguna aceptación.

## Cómo se detecta una respuesta

De la lista de conversaciones: si el último mensaje no empieza por «You:», vino
de ellos. Se cruza el nombre con los contactos de ESE asiento, y una coincidencia
crea la respuesta, pausa la secuencia y deja la tarea de revisión — igual que
hace el correo.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import seats
import store
from config import PYTHON

LECTOR = Path(__file__).resolve().parent / "li_senales.py"
# Margen para que la lista de pendientes refleje lo que se acaba de enviar.
MINUTOS_GRACIA = 15


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def leer(seat) -> dict:
    if not seat.cdp:
        return {"ok": False, "error": "el asiento no tiene Chrome propio"}
    listo = seats.asegurar_envio(seat)
    if not listo.get("ok"):
        return {"ok": False, "error": listo.get("error", "sesión no disponible")}
    try:
        proc = subprocess.run([PYTHON, str(LECTOR), "--cdp", seat.cdp],
                              capture_output=True, text=True, timeout=180,
                              cwd=str(LECTOR.parent))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "el lector no respondió a tiempo"}
    try:
        return json.loads((proc.stdout or "").strip() or "{}") or {"ok": False}
    except json.JSONDecodeError:
        return {"ok": False, "error": "respuesta ilegible del lector"}


# ----------------------------- aceptaciones -------------------------------
def _slug(u: str) -> str:
    import re
    m = re.search(r"/in/([^/?#]+)", (u or "").lower())
    return m.group(1) if m else ""


def _invitados_sin_aceptar(seat_id: str) -> list[dict]:
    """Invitados de ese asiento que todavía no figuran como aceptados.

    Trae también CÓMO se confirmó el envío: solo se deduce una aceptación de un
    envío que quedó confirmado nombrando a la persona. Encadenar una inferencia
    sobre otra es la receta para un dato falso que parece bueno.
    """
    return store.fetch_all(
        "SELECT c.id, c.nombre, c.linkedin, c.stage, MIN(a.ts) AS invitado_ts, "
        "       json_extract(a.meta_json,'$.detail') AS detalle "
        "FROM contacts c JOIN activities a ON a.contact_id=c.id "
        "WHERE c.li_seat=? AND a.channel='linkedin' AND a.type='invite_sent' "
        "  AND NOT EXISTS (SELECT 1 FROM activities x WHERE x.contact_id=c.id "
        "                  AND x.type='accepted') "
        "GROUP BY c.id", (seat_id,))


def procesar_aceptaciones(seat, datos: dict) -> dict:
    """Cruza por SLUG de perfil, no por nombre.

    El nombre no sirve: en la base está «Juan Diaz Cumpalli» y en LinkedIn
    «JUAN FIDEL DIAZ CUMPALLI»; los slugs además traen acentos y hasta emojis.
    Con cruce por nombre, cualquier diferencia haría que el contacto no
    apareciera entre los pendientes y se marcara como aceptado sin serlo.

    Escribir la actividad `accepted` es lo que mueve el contacto a **conectado**:
    `store.ACTIVITY_STAGE` lo mapea y `add_activity` avanza el stage solo.
    """
    pend = {x.lower() for x in datos.get("slugs_pendientes", [])}
    declarados = datos.get("declarados", -1)
    leidos = len(pend)
    if not pend:
        return {"nuevas": 0, "saltado": "no se leyó ningún slug de la lista"}

    # Regla por TRANSICIÓN: estaba en pendientes en la lectura anterior y ya no
    # está. Es mucho más fuerte que «no está ahora»: una fila que no logramos
    # leer tendría que faltar en las DOS lecturas, y entonces no hay transición
    # que observar, así que tampoco hay falso positivo. Con esto la detección
    # deja de depender de leer la lista al 100% —imposible en la práctica: se
    # midió 69 de 70 y 36 de 39 por filas con forma distinta—.
    clave = f"seat:{seat.id}:pendientes_slugs"
    try:
        previo = set(json.loads(store.kv_get(clave, "[]")))
    except json.JSONDecodeError:
        previo = set()

    if not previo:
        # Primera lectura: solo se siembra el estado. Deducir aquí sería adivinar
        # sobre una foto sin antes.
        store.kv_set(clave, json.dumps(sorted(pend)))
        return {"nuevas": 0, "sembrado": leidos,
                "saltado": "primera lectura de este asiento: se guarda el estado "
                           "y se comparará en la siguiente"}
    # Salvaguarda para una lectura parcial. Se mide contra las DOS referencias
    # disponibles: el número que declara LinkedIn en el encabezado y el tamaño
    # de la lectura anterior.
    #
    # La segunda no es redundante, es la que sostiene la guarda: cuando la
    # página no renderiza bien, el contador del encabezado tampoco se parsea y
    # llega como -1 — y una guarda que solo mirara `declarados` se apaga sola
    # justo en la lectura que menos hay que creer. Así se marcaron 6
    # aceptaciones falsas el 8-sep-2026: la lectura devolvió unos pocos slugs y
    # ningún nombre, `declarados` vino -1, y los 6 contactos que faltaban de esa
    # lista parcial se leyeron como transición a «aceptado». Los seis seguían
    # pendientes en LinkedIn cuando se verificó a mano contra el snapshot.
    referencia = max(declarados if declarados > 0 else 0, len(previo))
    if referencia and leidos < referencia * 0.8:
        return {"nuevas": 0, "saltado": (
            f"lectura demasiado parcial ({leidos} leídos contra una referencia "
            f"de {referencia}: {declarados if declarados > 0 else '?'} "
            f"declarados, {len(previo)} en la lectura anterior): no se compara")}

    # El snapshot se guarda SOLO si la lectura es creíble. Guardarlo antes del
    # resguardo dejaba que una lectura corrupta lo envenenara: la siguiente
    # lectura sana se comparaba contra ese resto y las transiciones reales de
    # esa ronda pasaban desapercibidas.
    store.kv_set(clave, json.dumps(sorted(pend)))

    corte = datetime.now() - timedelta(minutes=MINUTOS_GRACIA)
    nuevas, revisar = [], []
    for c in _invitados_sin_aceptar(seat.id):
        try:
            if datetime.fromisoformat(c["invitado_ts"]) > corte:
                continue          # demasiado reciente: la lista puede no reflejarlo
        except (ValueError, TypeError):
            pass
        sl = _slug(c["linkedin"])
        if not sl or sl in pend:
            continue              # sigue pendiente
        if sl not in previo:
            continue              # nunca lo vimos pendiente: no hay transición
        if "confirmado" not in (c.get("detalle") or ""):
            # Salió de pendientes, pero el envío tampoco estaba confirmado: puede
            # haber aceptado o puede que nunca se enviara. Eso lo decide un humano.
            revisar.append(c["nombre"])
            store.add_intento(c["id"], seat.id, "revisar",
                              "salió de pendientes con un envío no confirmado: "
                              "¿aceptó o nunca salió?")
            continue
        if store.add_activity(c["id"], "linkedin", "accepted", store.now_iso(),
                              ext_id=f"li-acc:{seat.id}:{c['id']}",
                              meta={"seat": seat.id, "fuente": "salio-de-enviadas",
                                    "envio": c.get("detalle", "")}):
            nuevas.append(c["nombre"])
    return {"nuevas": len(nuevas), "nombres": nuevas, "a_revisar": revisar}


# ----------------------------- respuestas ---------------------------------
def _contactos_por_nombre(seat_id: str) -> dict[str, int]:
    fuera: dict[str, int] = {}
    for r in store.fetch_all(
            "SELECT id, nombre FROM contacts WHERE li_seat=? AND nombre<>''", (seat_id,)):
        fuera[_norm(r["nombre"])] = r["id"]
    return fuera


def procesar_respuestas(seat, datos: dict) -> dict:
    por_nombre = _contactos_por_nombre(seat.id)
    nuevas, sin_match = [], []
    for c in datos.get("conversaciones", []):
        if not c.get("entrante"):
            continue
        cid = por_nombre.get(_norm(c.get("nombre", "")))
        if not cid:
            sin_match.append(c.get("nombre", ""))
            continue
        # ext_id por contacto + huella del texto: releer la bandeja no duplica,
        # pero un mensaje nuevo del mismo contacto sí entra.
        huella = hashlib.sha1(_norm(c.get("snippet", "")).encode()).hexdigest()[:12]
        if not store.add_activity(
                cid, "linkedin", "reply", store.now_iso(),
                ext_id=f"li-reply:{cid}:{huella}",
                meta={"seat": seat.id, "snippet": c.get("snippet", ""),
                      "cuando": c.get("cuando", ""), "canal": "linkedin"}):
            continue
        store.pause_sequences_for(cid, status="replied")
        if not store.open_task_exists(cid, "reply_review"):
            store.add_task(cid, "reply_review", store.now_iso(),
                           notes=c.get("snippet", ""),
                           meta={"reply": {"canal": "linkedin", "seat": seat.id,
                                           "snippet": c.get("snippet", "")}})
        nuevas.append(c.get("nombre", ""))
    return {"nuevas": len(nuevas), "nombres": nuevas,
            "conversaciones_ajenas": len(sin_match)}


# ----------------------------- orquestación -------------------------------
def revisar_asiento(seat) -> dict:
    datos = leer(seat)
    if not datos.get("ok"):
        return {"seat": seat.id, "nombre": seat.etiqueta,
                "problema": datos.get("error", "no se pudo leer")}
    return {"seat": seat.id, "nombre": seat.etiqueta,
            "pendientes": len(datos.get("pendientes", [])),
            "declarados": datos.get("declarados", -1),
            "aceptaciones": procesar_aceptaciones(seat, datos),
            "respuestas": procesar_respuestas(seat, datos)}


def correr() -> dict:
    partes = [revisar_asiento(s) for s in seats.en_uso() if s.activo]
    return {"ts": store.now_iso(), "asientos": partes,
            "aceptaciones": sum((p.get("aceptaciones") or {}).get("nuevas", 0) for p in partes),
            "respuestas": sum((p.get("respuestas") or {}).get("nuevas", 0) for p in partes)}


# Cada cuánto mirar. La bandeja no cambia cada 15 minutos y cada lectura son dos
# páginas en la cuenta del VP: una vez por hora en horario laboral es suficiente.
def toca_ahora(ahora: datetime | None = None) -> tuple[bool, str]:
    import scheduler
    ahora = ahora or datetime.now()
    if not scheduler.in_business_hours(ahora):
        return False, "fuera de horario laboral"
    ultima = store.kv_get("senales_li_ts", "")
    if ultima:
        try:
            if datetime.fromisoformat(ultima) > ahora - timedelta(minutes=55):
                return False, f"revisado hace poco ({ultima[11:16]})"
        except ValueError:
            pass
    return True, "toca"


def correr_si_toca(ahora: datetime | None = None) -> dict:
    puede, motivo = toca_ahora(ahora)
    if not puede:
        return {"corrio": False, "motivo": motivo}
    store.kv_set("senales_li_ts", store.now_iso())
    r = correr()
    store.kv_set("senales_li_parte", json.dumps(r, ensure_ascii=False)[:3000])
    return {"corrio": True, **r}
