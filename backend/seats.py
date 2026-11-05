"""Asientos de LinkedIn: los perfiles que firman el outreach.

Antes había UNO —las cookies de `config.COOKIES_PATH`, con un override por
campaña— y todo el cupo diario era un solo número. Con tres VP prestando su
perfil eso deja de servir por tres razones distintas:

  1. **El cupo no es del cockpit, es de cada cuenta.** LinkedIn restringe por
     perfil. Tres perfiles no son «el triple de cupo» en una bolsa común: son
     tres bolsas separadas, y gastarlas como si fueran una es exactamente la
     forma de quemar la que menos aguanta.
  2. **Quien invita es quien tiene que mensajear.** El paso 4 escribe al que
     aceptó, y solo aceptó a UN perfil. Si el mensaje sale de otro asiento, le
     escribe un desconocido a alguien que nunca lo aceptó — y LinkedIn ni
     siquiera deja abrir ese chat. Por eso la asignación es pegajosa: el
     contacto queda amarrado a su asiento desde la primera invitación.
  3. **Un asiento sin sesión no es un asiento lento, es un asiento muerto.**
     Los VP todavía no han entregado su login. Un asiento sin sesión tiene cupo
     CERO a propósito: así el copiloto no fabrica borradores que nadie puede
     enviar ni deja contactos esperando a un perfil que quizá nunca llegue.

Todo lo editable vive en `store.kv` (`seat:<id>:<campo>`), no en el código: los
nombres reales de los VP, sus topes y sus sesiones se llenan desde la UI sin
tocar un archivo ni reiniciar el backend.

## Calentamiento

Un perfil que nunca ha hecho outreach y de un día para otro manda 20
invitaciones es el caso de libro de restricción. El cupo de invitaciones arranca
en `WARMUP_BASE` y sube 20% por semana cumplida hasta el techo del asiento; el
reloj arranca el día del primer envío real, no el día en que se creó el asiento,
porque un asiento configurado en agosto y estrenado en octubre no se calentó
solo por esperar.

Los mensajes a ya-conectados NO se calientan: van contra gente que ya te
aceptó, que es lo que LinkedIn mira. Tienen su propio tope, plano.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import li_login
import store
from config import DATA_DIR

# Cupos posibles, no perfiles en uso. El nombre real del VP se llena solo al
# iniciar sesión (li_perfil lo lee del título de la página) y un cupo que nadie
# usa se apaga (`activo=0`), no se borra: así su puerto y su historia siguen
# reservados si mañana vuelve.
#
# Los cuatro primeros nacieron en el código; los que abre Ajustes viven en `kv`
# (`seats:ids`). La semilla se queda escrita aquí a propósito: si mañana `kv`
# llegara vacío, los asientos con contactos e historia siguen existiendo en vez
# de desaparecer de la pantalla junto con la única sesión que puede escribirles.
#
# El orden importa: `li_login.puerto()` asigna PUERTO_BASE + índice cuando el
# asiento todavía no ha reclamado puerto, así que los nuevos van SIEMPRE al
# final. Reordenar esto le cambiaría el puerto a un asiento ya levantado.
_SEMILLA = ("vp-1", "vp-2", "vp-3", "vp-4")
_CLAVE_IDS = "seats:ids"


def seat_ids() -> tuple[str, ...]:
    """Los asientos que existen hoy: la semilla más los abiertos desde Ajustes.

    Se lee de `kv` en cada llamada y no se cachea: abrir un espacio tiene que
    verse en la pantalla siguiente, no después de reiniciar el backend — que es
    justo lo que obligaba a hacer la tupla en código.
    """
    try:
        extra = json.loads(store.kv_get(_CLAVE_IDS, "") or "[]")
    except (json.JSONDecodeError, TypeError):
        extra = []
    ids = list(_SEMILLA)
    for i in extra:
        if isinstance(i, str) and i.strip() and i.strip() not in ids:
            ids.append(i.strip())
    return tuple(ids)

COOKIES_DIR = DATA_DIR / "cookies"

# Calentamiento del cupo de invitaciones.
WARMUP_BASE = 6          # invitaciones/día la primera semana
WARMUP_FACTOR = 1.2      # +20% por semana cumplida
# Techos por defecto. Salen de la auditoría de cupos
# (PLATZI/linkedin-rate-limits/rate_limits_linkedin.html): 20/día es el máximo
# que sostiene una cuenta gratis bien calificada y 40 el de una con Sales
# Navigator. No son un objetivo: son el techo al que llega la rampa.
TOPE_DIA_DEFAULT = 20
TOPE_DIA_SALES_NAV = 40
# Invitaciones por semana. LinkedIn corta por semana antes que por día, así que
# un asiento puede tener cupo hoy y aun así estar agotado.
TOPE_SEMANA_MAX = 100
# Mensajes a ya-conectados: no entran en la rampa (van a gente que ya aceptó).
TOPE_MSGS_DEFAULT = 20

_CAMPOS_TEXTO = ("nombre", "rol", "cookies", "cdp", "warmup_start",
                 "secuencia_am", "secuencia_hs", "remitente_hs", "prioriza")
_CAMPOS_BOOL = ("activo", "sales_nav", "es_anfitrion")
_CAMPOS_INT = ("tope_dia", "tope_semana", "tope_msgs", "cupo_fijo")


@dataclass(frozen=True)
class Seat:
    id: str
    nombre: str
    rol: str
    activo: bool
    sales_nav: bool
    es_anfitrion: bool
    cookies: str
    cdp: str
    tope_dia: int
    tope_semana: int
    tope_msgs: int
    warmup_start: str
    # Secuencia de Amplemarket donde se inscribe a quien acepta la invitación de
    # ESTE perfil. Va en el asiento y no en la campaña porque la secuencia la
    # firma el VP: quien aceptó a Patricia tiene que recibir el correo de
    # Patricia, y meterlo en la secuencia de Jess rompe el hilo — el prospecto
    # aceptó a una persona y le escribe otra.
    secuencia_am: str = ""
    # Lo mismo, pero con secuencias de HubSpot: el correo sale de la bandeja de
    # Google del vendedor (`remitente_hs`), no de un motor externo. Es el carril
    # de los perfiles cuyo dueño tiene asiento de Sales Hub y buzón conectado.
    #
    # Los dos motores son EXCLUYENTES por asiento: con `secuencia_am` y
    # `secuencia_hs` puestas a la vez, la misma persona recibiría dos correos
    # distintos de dos remitentes. `secuencias_am` y `secuencias_hs` se saltan
    # los asientos que tengan la otra, así que un asiento mal configurado no
    # envía por ninguno — falla en seco y se ve, en vez de duplicar en silencio.
    secuencia_hs: str = ""
    # Buzón que firma el correo de HubSpot. Explícito siempre: nunca un buzón
    # por defecto ni el del dueño «deducido». Un correo firmado por quien no
    # toca es peor que no mandarlo.
    remitente_hs: str = ""
    # Origen que este perfil atiende con preferencia (hoy: «maquina-ventas», lo
    # que entra de Platzi Ventas). Existe porque no todos los perfiles son
    # equivalentes: los de Sales trabajan las cuentas que un vendedor marcó
    # HOY —esas tienen reloj— y los de dirección muelen el backlog. Sin esto la
    # vía rápida reparte por cupo libre y una cuenta urgente puede caer en el
    # perfil que menos la va a atender.
    prioriza: str = ""
    # Cupo diario puesto a mano por el operador. Manda sobre la rampa: 0 = usar
    # la rampa. Existe separado a propósito — así en pantalla se distingue «el
    # sistema calculó esto» de «una persona decidió esto».
    cupo_fijo: int = 0

    @property
    def etiqueta(self) -> str:
        return self.nombre or f"{self.id} · sin asignar"

    @property
    def configurado(self) -> bool:
        """¿Ya sabemos de quién es este asiento? Sin nombre es un hueco."""
        return bool(self.nombre.strip())


def _k(seat_id: str, campo: str) -> str:
    return f"seat:{seat_id}:{campo}"


def cookies_path(seat_id: str) -> Path:
    """Dónde guarda el cockpit la sesión de este asiento.

    Una por asiento y dentro de PLATZI/: si las tres compartieran archivo, el
    último login sobrescribiría a los otros dos sin avisar.
    """
    return COOKIES_DIR / f"{seat_id}.json"


def get(seat_id: str) -> Optional[Seat]:
    if seat_id not in seat_ids():
        return None
    g = lambda campo, d="": store.kv_get(_k(seat_id, campo), d).strip()  # noqa: E731
    sales_nav = g("sales_nav", "0") == "1"
    tope_dia = int(g("tope_dia") or (TOPE_DIA_SALES_NAV if sales_nav else TOPE_DIA_DEFAULT))
    tope_semana = int(g("tope_semana") or min(tope_dia * 5, TOPE_SEMANA_MAX))
    return Seat(
        id=seat_id,
        nombre=g("nombre"),
        rol=g("rol"),
        # Un asiento arranca activo: lo que lo frena es no tener sesión, no una
        # casilla. Apagarlo es una decisión explícita («este VP se salió»).
        activo=g("activo", "1") == "1",
        sales_nav=sales_nav,
        es_anfitrion=g("es_anfitrion", "0") == "1",
        cookies=g("cookies") or str(cookies_path(seat_id)),
        # En cuanto el VP inicia sesión una vez, su asiento apunta solo a SU
        # Chrome: no hay que pegar ningún endpoint a mano. El campo se sigue
        # respetando si alguien lo puso manualmente.
        cdp=g("cdp") or (li_login.endpoint(seat_id)
                         if li_login.tiene_perfil(seat_id) else ""),
        tope_dia=tope_dia,
        tope_semana=tope_semana,
        tope_msgs=int(g("tope_msgs") or TOPE_MSGS_DEFAULT),
        warmup_start=g("warmup_start"),
        secuencia_am=g("secuencia_am"),
        secuencia_hs=g("secuencia_hs"),
        remitente_hs=g("remitente_hs").lower(),
        prioriza=g("prioriza"),
        cupo_fijo=int(g("cupo_fijo") or 0),
    )


def all_seats() -> list[Seat]:
    """Todos los cupos, usados o no. Para administrar, no para contar."""
    return [s for s in (get(i) for i in seat_ids()) if s is not None]


def en_uso() -> list[Seat]:
    """Los perfiles que el cockpit realmente tiene: los activos, más cualquiera
    apagado que todavía cargue contactos.

    Lo segundo no es un detalle: un asiento apagado con contactos asignados es el
    único que puede escribirles (ya los invitó él). Esconderlo dejaría a esos
    contactos congelados sin nada en pantalla que explique por qué.
    """
    return [s for s in all_seats()
            if s.activo or store.count_seat_contacts(s.id) > 0]


def libres() -> list[str]:
    """Cupos apagados y sin contactos: se pueden estrenar con un VP nuevo."""
    return [s.id for s in all_seats()
            if not s.activo and store.count_seat_contacts(s.id) == 0]


def crear() -> tuple[Optional[Seat], str]:
    """Abre un espacio nuevo para otro perfil. Devuelve (asiento, motivo).

    Antes esto era editar una tupla en `seats.py` y reiniciar el backend, que es
    tanto como decir que sumar un VP era una tarea de programador. El id sigue la
    serie (`vp-5`, `vp-6`…) y no reusa números: un id repetido heredaría en
    silencio el cupo, la carpeta de Chrome y los contactos del anterior.

    Se crea **encendido** a propósito. Un cupo libre está apagado —así lo
    distingue `libres()`— pero un espacio recién abierto tiene que enseñar su
    tarjeta para que alguien pueda entrar desde ahí; apagado no se vería y el
    botón no serviría de nada. Encenderlo no lo pone a enviar: sin sesión
    verificada su cupo es 0.
    """
    if libres():
        # Antes de inventar un asiento nuevo se estrena el que ya está pago: cada
        # asiento carga con una carpeta de Chrome y un puerto, y dejar huecos
        # apagados mientras se crean otros es acumular basura con nombre de VP.
        seat = update(libres()[0], activo=True)
        return seat, ""
    ids = seat_ids()
    usados = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"vp-(\d+)", i))]
    nuevo = f"vp-{max(usados, default=0) + 1}"
    # El puerto se reserva AQUÍ y no al abrir la ventana: `li_login.puerto()`
    # cae en PUERTO_BASE + índice cuando no hay uno guardado, y ese número puede
    # ser el que otro asiento ya tiene apuntado. Dos asientos en el mismo puerto
    # es un asiento firmando con la cuenta del otro.
    p = li_login.puerto_libre(nuevo)
    if not p:
        return None, ("no quedan puertos de depuración libres "
                      f"({li_login.PUERTO_BASE}-{li_login.PUERTO_TOPE - 1}): "
                      "apaga un asiento que ya no se use antes de abrir otro")
    store.kv_set(f"seat:{nuevo}:puerto", str(p))
    store.kv_set(_CLAVE_IDS, json.dumps([i for i in ids if i not in _SEMILLA] + [nuevo]))
    store.kv_set(_k(nuevo, "activo"), "1")
    invalidate_probe(nuevo)
    return get(nuevo), ""


def update(seat_id: str, **campos) -> Optional[Seat]:
    if seat_id not in seat_ids():
        return None
    for k, v in campos.items():
        if v is None:
            continue
        if k in _CAMPOS_BOOL:
            store.kv_set(_k(seat_id, k), "1" if v else "0")
        elif k in _CAMPOS_INT:
            store.kv_set(_k(seat_id, k), str(max(0, int(v))))
        elif k in _CAMPOS_TEXTO:
            store.kv_set(_k(seat_id, k), str(v).strip())
    return get(seat_id)


# ----------------------------- sesión --------------------------------------
_PROBE_TTL = 60.0
_probe_cache: dict[str, tuple[float, dict]] = {}


def _probe_cdp(endpoint: str) -> dict:
    """¿Responde el Chrome dedicado de este asiento?

    Se cachea: `budgets()` se llama en cada poll de la UI y tres HTTP con
    timeout por poll convierten una pantalla en una espera.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/json/version", timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return {"ok": True, "detalle": data.get("Browser", "Chrome")}
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "detalle": f"no responde ({e.__class__.__name__})"}


def _probe_cookies(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "detalle": "no hay archivo de sesión"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"ok": False, "detalle": "el archivo de sesión no es JSON válido"}
    cookies = data.get("cookies", data) if isinstance(data, dict) else data
    li_at = next((c for c in cookies if c.get("name") == "li_at"), None) \
        if isinstance(cookies, list) else None
    if not li_at:
        return {"ok": False, "detalle": "el archivo no trae la cookie li_at"}
    exp = li_at.get("expires")
    if exp:
        try:
            venc = datetime.fromtimestamp(int(exp))
            if venc < datetime.now():
                return {"ok": False, "detalle": f"la sesión venció el {venc.date().isoformat()}"}
            return {"ok": True, "detalle": f"vence {venc.date().isoformat()}"}
        except (ValueError, TypeError, OSError):
            pass
    return {"ok": True, "detalle": "cookie li_at presente"}


def _cachear(seat_id: str, res: dict) -> dict:
    _probe_cache[seat_id] = (time.monotonic(), res)
    return res


def session(seat: Seat, refresh: bool = False) -> dict:
    """Estado de la sesión del asiento: {ok, via, detalle}.

    El CDP manda sobre las cookies porque es lo único que aguanta en la
    práctica: inyectar cookies en un Chrome limpio termina en checkpoint.
    """
    hit = _probe_cache.get(seat.id)
    if hit and not refresh and time.monotonic() - hit[0] < _PROBE_TTL:
        return hit[1]
    if seat.cdp:
        # Un Chrome abierto en la pantalla de login NO es una sesión. Sin login
        # verificado el asiento queda en cupo 0: si no, el copiloto le armaría
        # borradores a un VP que apenas abrió la ventana.
        if li_login.tiene_perfil(seat.id) and not li_login.login_guardado(seat.id):
            return _cachear(seat.id, {
                "ok": False, "via": "chrome",
                "detalle": "la ventana está abierta pero nadie ha iniciado sesión todavía"})
        res = {**_probe_cdp(seat.cdp), "via": "chrome"}
        if res["ok"]:
            # Que Chrome conteste no es que haya sesión. `_probe_cdp` solo pide
            # la versión del navegador, y `login_guardado` es un registro
            # histórico —«entró el 25 de agosto»—, no el estado de ahora. Con
            # esas dos señales un perfil deslogueado se reportaba listo: Jess
            # estuvo 5 días en la pantalla de acceso con cupo 10/10 en pantalla,
            # sin mandar nada, y ni el tablero ni el correo diario lo dijeron.
            # El motor sí lo veía, pero solo al intentar enviar.
            f = li_login.fase(seat.id)
            if f.get("fase") in ("login", "verifica", "otro"):
                res = {"ok": False, "via": "chrome", "fase": f["fase"],
                       "detalle": f.get("detalle", "la sesión no está iniciada")}
        if not res["ok"] and res.get("fase") is None and \
                li_login.tiene_perfil(seat.id) and li_login.login_guardado(seat.id):
            # Chrome cerrado ≠ sesión perdida. La sesión vive en el perfil en
            # disco y `li_login.asegurar()` levanta la ventana sola antes de
            # enviar. Tratarlo como caído dejaría al asiento en cupo 0 cada vez
            # que alguien cierra una ventana, y el VP tendría que volver a
            # entrar sin ninguna razón.
            desde = li_login.login_guardado(seat.id)[:10]
            res = {"ok": True, "via": "chrome", "dormido": True,
                   "detalle": f"ventana cerrada, se levanta sola al enviar "
                              f"(último acceso {desde})"}
    else:
        res = {**_probe_cookies(seat.cookies), "via": "cookies"}
    _probe_cache[seat.id] = (time.monotonic(), res)
    return res


def asegurar_envio(seat: Seat) -> dict:
    """Deja el asiento listo para enviar AHORA. Se llama justo antes del job."""
    if seat.cdp and li_login.tiene_perfil(seat.id):
        res = li_login.asegurar(seat.id)
        invalidate_probe(seat.id)
        return res
    ses = session(seat, refresh=True)
    return {"ok": ses["ok"], "error": "" if ses["ok"] else ses["detalle"]}


def invalidate_probe(seat_id: str = "") -> None:
    if seat_id:
        _probe_cache.pop(seat_id, None)
    else:
        _probe_cache.clear()


# ----------------------------- cupos ---------------------------------------
def _lunes(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def cupo_invites(seat: Seat, hoy: Optional[date] = None) -> int:
    """Cupo de invitaciones de HOY.

    Si hay `cupo_fijo` manda ese número y la rampa se ignora: es una decisión
    humana explícita. El tope semanal sigue aplicando por encima.
    """
    hoy = hoy or date.today()
    if seat.cupo_fijo > 0:
        return seat.cupo_fijo
    if not seat.warmup_start:
        return min(WARMUP_BASE, seat.tope_dia)
    try:
        inicio = date.fromisoformat(seat.warmup_start)
    except ValueError:
        return min(WARMUP_BASE, seat.tope_dia)
    semanas = max(0, (hoy - inicio).days // 7)
    return int(min(seat.tope_dia, WARMUP_BASE * (WARMUP_FACTOR ** semanas)))


def marcar_arranque(seat: Seat) -> None:
    """Arranca el reloj del calentamiento en el primer envío real.

    Se llama al lanzar el job, no al configurar el asiento: un asiento creado en
    agosto y estrenado en octubre no se calentó por esperar sentado.
    """
    if not seat.warmup_start:
        store.kv_set(_k(seat.id, "warmup_start"), date.today().isoformat())


def budget(seat: Seat) -> dict:
    """Cuánto le queda hoy a este asiento, por bolsa.

    Invitaciones y mensajes son bolsas distintas porque los limita LinkedIn por
    mecanismos distintos: la invitación a un desconocido es el recurso escaso y
    el que dispara restricciones; el mensaje a alguien que ya te aceptó, no.
    """
    hoy = date.today()
    ses = session(seat)
    listo = seat.activo and ses["ok"]

    cupo_inv = cupo_invites(seat, hoy) if listo else 0
    cupo_sem = min(seat.tope_semana, cupo_inv * 5) if listo else 0
    cupo_msg = seat.tope_msgs if listo else 0

    usados_inv = store.count_seat_activities(seat.id, ("invite_sent",), hoy.isoformat())
    usados_sem = store.count_seat_activities(seat.id, ("invite_sent",), _lunes(hoy))
    usados_msg = store.count_seat_activities(seat.id, ("followup_sent",), hoy.isoformat())
    col_inv = store.count_seat_queued(seat.id, ("linkedin_invite",), hoy.isoformat())
    col_msg = store.count_seat_queued(seat.id, ("linkedin_msg",), hoy.isoformat())

    rest_dia = max(0, cupo_inv - usados_inv - col_inv)
    rest_sem = max(0, cupo_sem - usados_sem - col_inv)
    return {
        "seat_id": seat.id,
        "nombre": seat.etiqueta,
        "nombre_real": seat.nombre,   # vacío si nadie lo ha llenado (para el input)
        "rol": seat.rol,
        "activo": seat.activo,
        "configurado": seat.configurado,
        "sesion": ses,
        "listo": listo,
        "login": li_login.estado(seat.id),
        "es_anfitrion": seat.es_anfitrion,
        "sales_nav": seat.sales_nav,
        "prioriza": seat.prioriza,
        "warmup_start": seat.warmup_start,
        "tope_dia": seat.tope_dia,
        "cupo_fijo": seat.cupo_fijo,
        "invites": {"cap": cupo_inv, "used": usados_inv, "queued": col_inv,
                    "remaining": min(rest_dia, rest_sem)},
        "semana": {"cap": cupo_sem, "used": usados_sem, "remaining": rest_sem},
        "mensajes": {"cap": cupo_msg, "used": usados_msg, "queued": col_msg,
                     "remaining": max(0, cupo_msg - usados_msg - col_msg)},
        "asignados": store.count_seat_contacts(seat.id),
    }


def budgets() -> list[dict]:
    return [budget(s) for s in en_uso()]


def _restante(b: dict, canal: str) -> int:
    return b["mensajes"]["remaining"] if canal == "linkedin_msg" else b["invites"]["remaining"]


# ----------------------------- asignación ----------------------------------
def tiene_historia_li(contact_id: int) -> bool:
    """¿Ya hubo contacto por LinkedIn? Entonces el asiento queda congelado."""
    return bool(store.fetch_one(
        "SELECT id FROM activities WHERE contact_id=? AND channel='linkedin' "
        "AND type IN ('invite_sent','followup_sent','accepted') LIMIT 1",
        (contact_id,)))


def pick_for(contact: dict, canal: str) -> tuple[Optional[Seat], str]:
    """Asiento que le toca a este contacto para este canal.

    Devuelve (asiento, motivo). Si el asiento es None el motivo explica por qué
    no se puede tocar a este contacto HOY — nunca se reparte a otro asiento con
    cupo, porque el que invitó es el único que puede escribir después.
    """
    seat = get((contact.get("li_seat") or "").strip())
    # Un asiento apagado que todavía no tocó a este contacto se puede soltar; si
    # ya lo invitó, no: el contacto solo aceptó a ESE perfil.
    if seat is not None and not seat.activo and not tiene_historia_li(contact["id"]):
        seat = None
    if seat is not None:
        # El motivo tiene que ser el de verdad: decir «sin sesión» de un asiento
        # que está apagado manda a revisar el login de alguien que sí lo dio.
        if not seat.activo:
            return None, (f"{seat.etiqueta} está apagado y es el único que puede "
                          "escribirle: ya lo invitó, así que nadie más puede tomarlo")
        b = budget(seat)
        if not b["sesion"]["ok"]:
            return None, (f"{seat.etiqueta} no tiene sesión de LinkedIn viva "
                          f"({b['sesion']['detalle']})")
        if _restante(b, canal) <= 0:
            return None, f"{seat.etiqueta} agotó su cupo de hoy"
        return seat, ""

    # Sin asiento (o reasignable): el que más cupo libre tenga. Repartir por
    # cupo restante y no por turno hace que el asiento con el doble de cupo
    # reciba el doble de contactos, sin tener que llevar un contador aparte.
    libres = sorted(
        [b for b in budgets() if b["listo"] and _restante(b, canal) > 0],
        key=lambda b: (-_restante(b, canal), b["asignados"], b["seat_id"]))
    if not libres:
        return None, "ningún asiento de LinkedIn con sesión y cupo disponible"
    seat = get(libres[0]["seat_id"])
    if seat is None:
        return None, "ningún asiento de LinkedIn disponible"
    store.set_seat(contact["id"], seat.id)
    return seat, ""


def seat_de(contact_id: int) -> Optional[Seat]:
    row = store.fetch_one("SELECT li_seat FROM contacts WHERE id=?", (contact_id,))
    return get((row or {}).get("li_seat") or "")


# ----------------------------- estado global -------------------------------
def preflight() -> list[str]:
    """Qué le falta a los asientos para poder enviar. Vacío = todo listo."""
    problemas: list[str] = []
    activos = [s for s in en_uso() if s.activo]
    if not activos:
        return ["no hay ningún asiento de LinkedIn activo: el canal está apagado"]
    for s in activos:
        if not s.configurado:
            problemas.append(f"{s.id}: falta decir de quién es el asiento (nombre del VP)")
        ses = session(s)
        if not ses["ok"]:
            nunca = not li_login.login_guardado(s.id)
            problemas.append(
                f"{s.etiqueta}: {'todavía no ha iniciado sesión' if nunca else 'su sesión se cayó'} "
                f"({ses['detalle']}) — cupo 0 hasta que entre en Ajustes → Perfiles")
    if len([s for s in activos if session(s)["ok"]]) == 0:
        problemas.append("ningún asiento tiene sesión viva: no sale ni una invitación")
    return problemas


def resumen() -> dict:
    bs = budgets()
    return {
        "seats": bs,
        "listos": sum(1 for b in bs if b["listo"]),
        "total": len(bs),
        # Cupos que se pueden estrenar sin tocar código, si entra otro VP.
        "libres": libres(),
        "cap_invites": sum(b["invites"]["cap"] for b in bs),
        "used_invites": sum(b["invites"]["used"] for b in bs),
        "queued_invites": sum(b["invites"]["queued"] for b in bs),
        "remaining_invites": sum(b["invites"]["remaining"] for b in bs),
        "cap_mensajes": sum(b["mensajes"]["cap"] for b in bs),
        "used_mensajes": sum(b["mensajes"]["used"] for b in bs),
        "queued_mensajes": sum(b["mensajes"]["queued"] for b in bs),
        "remaining_mensajes": sum(b["mensajes"]["remaining"] for b in bs),
        "problemas": preflight(),
    }


# ----------------------------- migración -----------------------------------
def adoptar_asiento_unico() -> Optional[str]:
    """Se ejecuta una vez: mueve la sesión del asiento único al primer asiento.

    Antes la campaña apuntaba a `campaign:<slug>:li_cookies` y esa era LA
    sesión. Si existe y ningún asiento tiene sesión propia todavía, se hereda a
    `vp-1` en vez de perderla en silencio.
    """
    if any(session(s)["ok"] for s in all_seats()):
        return None
    heredado = ""
    for row in store.fetch_all("SELECT key, value FROM kv WHERE key LIKE 'campaign:%:li_cookies'"):
        if (row.get("value") or "").strip():
            heredado = row["value"].strip()
            break
    if not heredado or not Path(heredado).expanduser().exists():
        return None
    update("vp-1", cookies=heredado)
    invalidate_probe("vp-1")
    return heredado
