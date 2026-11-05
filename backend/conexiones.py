"""Envío de solicitudes de conexión de LinkedIn, sin nota.

Es el único sitio donde se decide a quién se le manda una conexión y con qué
perfil. Lo usan tanto la corrida manual como el trabajo de cada mañana, para que
no haya dos criterios distintos.

## Por qué esto NO pasa por la cola de aprobación

El cockpit no envía nada sin aprobación humana, y esa regla sigue intacta: existe
para que nadie le mande a un presidente un texto que ninguna persona revisó. Una
solicitud de conexión **sin nota no lleva texto**, así que no hay copy que
aprobar — lo único que se decide es a quién y desde qué perfil, y eso ya está
acotado por el reparto (cada contacto tiene dueño) y por el cupo de cada asiento.
Cualquier paso CON nota sigue pasando por la cola.

## Lo que acota el volumen

  · el cupo diario de cada asiento, con su rampa de calentamiento
  · el tope semanal (LinkedIn corta por semana antes que por día)
  · solo contactos de ESE asiento, en etapa `nuevo`, sin bandera roja de HubSpot
  · nunca dos veces al mismo contacto, ni a uno que falló hace poco

## La vía rápida: lo que pide un vendedor no espera a mañana

La corrida de la mañana muele el backlog una vez al día. Pero un prospecto que
entra porque un vendedor apretó «trabajar con esta empresa» en Platzi Ventas
tiene reloj: el vendedor está en esa cuenta HOY. Si la invitación sale mañana a
las 8, la conexión se acepta cuando la llamada ya pasó.

Así que los orígenes de `ORIGENES_VIA_RAPIDA` salen por su propio carril: el
copiloto los detecta en el tick siguiente (≤15 min), les da asiento y los
invita, **antes** de que la corrida general se coma el cupo del día.

No es un canal aparte ni un permiso nuevo: usa el mismo `candidatos()`, el mismo
cupo por asiento y los mismos frenos. Lo único que cambia es *cuándo* y en qué
orden. La ventana es más ancha que la de la mañana (8–18 en vez de 8–11) porque
esto reacciona a algo que acaba de pasar, pero sigue siendo horario hábil: una
invitación a las 3 a.m. de un domingo la firma un robot, no un VP.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta

import reparto
import seats
import store
import sync
from config import RESULTS_DIR
from jobs import manager
from runs import read_jsonl

# Un perfil que no ofrece ruta hoy tampoco la ofrecerá mañana: se deja descansar
# para que la corrida diaria avance en vez de moler siempre los mismos.
DIAS_ENFRIAMIENTO = 7
OK = ("sent", "already", "pending_invite")

# ## Qué intento congela a un contacto y qué intento no
#
# El enfriamiento de arriba existe para un caso concreto y solo para ese: el
# motor llegó al perfil, lo leyó, y ahí no había forma de conectar —sin botón,
# sin ruta en el menú, perfil que ya no existe—. Eso es `not_found` y mañana va
# a ser igual, así que descansa.
#
# Un fallo del motor no dice nada del perfil. El 7 de septiembre de 2026 las 20
# invitaciones de la mañana murieron con `nav:Error` (el navegador no llegó a
# cargar la página) y esos 20 contactos quedaron congelados una semana sin que
# nadie les hubiera escrito nada; minutos después el mismo Chrome navegaba bien.
# Confundir «no pude mirar» con «mirado y no hay ruta» convierte cualquier
# tropiezo de infraestructura en una semana de silencio.
#
# Así que los fallos de motor se registran —el parte los necesita— pero no
# congelan: el contacto vuelve al lote en la corrida siguiente. Hasta el
# tercero. Un tropiezo no se repite tres veces en una semana; un problema
# estructural —una URL rota, un perfil que siempre revienta el modal— sí, y ahí
# el enfriamiento vuelve a ser la respuesta correcta en vez de un bucle.
#
# `revisar` no entra en esta cuenta para nada: no es un intento de envío sino
# una nota de `senales_li` sobre alguien **ya invitado** (salió de la lista de
# pendientes sin que el envío estuviera confirmado). Esos ya están fuera del
# lote por su actividad de LinkedIn, y como se anota una vez por corrida de
# señales, tres notas sobre el mismo contacto son normales — contarlas como
# tropiezos lo habría congelado por existir.
#
# Lo desconocido congela a propósito: un estado nuevo que nadie clasificó es
# mejor tratarlo como definitivo —se pierde una invitación— que como transitorio
# y molerlo en cada corrida.
ESTADOS_TRANSITORIOS = ("error", "rate_limited", "sin_evento")

# Y un `not_found` concreto que tampoco habla del perfil. El motor emite
# «no-pude-leer-de-quien-es-el-perfil» cuando no logra leer de quién es la
# página — y eso pasa igual con un perfil que no carga que con una sesión caída,
# donde lo único que se ve es el muro de login. El 10 y 11 de septiembre, con
# Jess deslogueada, 17 contactos sanos quedaron congelados una semana por esto.
# Es la misma distinción de siempre: «no pude mirar» no es «miré y no hay ruta».
DETALLES_TRANSITORIOS = ("no-pude-leer-de-quien-es-el-perfil",
                         "sin-identidad-no-se-toca-nada",
                         "sesion-invalida-o-checkpoint",
                         "sesion-invalida-en-preload")
ESTADOS_IGNORADOS = ("revisar",)
TROPIEZOS_TOLERADOS = 3
_NO_CONGELAN = ESTADOS_TRANSITORIOS + ESTADOS_IGNORADOS
_M_NO_CONGELAN = ",".join("?" * len(_NO_CONGELAN))
_M_TRANS = ",".join("?" * len(ESTADOS_TRANSITORIOS))
_M_DET = ",".join("?" * len(DETALLES_TRANSITORIOS))

# Campañas que entran por la vía rápida. Es el sello que le pone la máquina de
# prospección (`modulo-prospeccion/backend/maquina.py`) a todo decisor que nace
# de una cuenta marcada en Platzi Ventas — el único camino por el que un
# prospecto de esa app llega a esta base.
ORIGENES_VIA_RAPIDA = ("maquina-ventas",)


def candidatos(seat_id: str, n: int,
               campaigns: tuple[str, ...] | None = None) -> list[dict]:
    """A quién le puede mandar solicitud ESTE asiento hoy.

    `campaigns` acota a unos orígenes —es lo que usa la vía rápida—; sin él
    entra el backlog completo. El criterio de a-quién-sí vive aquí y solo aquí:
    los dos carriles tienen que descartar exactamente lo mismo, o uno acabaría
    invitando a alguien que el otro protege.
    """
    if n <= 0:
        return []
    corte = (date.today() - timedelta(days=DIAS_ENFRIAMIENTO)).isoformat()
    filtro, extra = "", ()
    if campaigns:
        filtro = ("  AND c.campaign IN (%s) " % ",".join("?" * len(campaigns)))
        extra = tuple(campaigns)
    return store.fetch_all(
        "SELECT c.id, c.nombre, c.empresa, c.cargo, c.linkedin "
        "FROM contacts c "
        "WHERE c.li_seat=? AND c.linkedin<>'' AND c.stage='nuevo' "
        # No basta con «no es rojo»: hace falta que ALGUIEN haya preguntado.
        # Con `<> 'rojo'` un contacto nunca cruzado pasaba el filtro, y así
        # salieron 7 invitaciones a ciegas —una a una cuenta que ya es cliente—
        # porque el veredicto en blanco se leía como permiso. Ahora el silencio
        # no es un sí: sin veredicto se espera a que `validar_hubspot` lo llene.
        "  AND COALESCE(c.hubspot_verdict,'') NOT IN ('', 'rojo') "
        + filtro +
        "  AND NOT EXISTS (SELECT 1 FROM activities a "
        "                  WHERE a.contact_id=c.id AND a.channel='linkedin') "
        "  AND NOT EXISTS (SELECT 1 FROM approval_queue q "
        "                  WHERE q.contact_id=c.id AND q.status IN "
        "                        ('pending','blocked','approved','sent','confirmed')) "
        "  AND NOT EXISTS (SELECT 1 FROM intentos i "
        "                  WHERE i.contact_id=c.id AND substr(i.ts,1,10) >= ? "
        f"                  AND i.estado NOT IN ({_M_NO_CONGELAN}) "
        f"                  AND i.detalle NOT IN ({_M_DET})) "
        "  AND (SELECT COUNT(*) FROM intentos i "
        "       WHERE i.contact_id=c.id AND substr(i.ts,1,10) >= ? "
        f"       AND (i.estado IN ({_M_TRANS}) "
        f"            OR i.detalle IN ({_M_DET}))) < ? "
        "ORDER BY c.id DESC LIMIT ?",
        (seat_id, *extra,
         corte, *_NO_CONGELAN, *DETALLES_TRANSITORIOS,
         corte, *ESTADOS_TRANSITORIOS, *DETALLES_TRANSITORIOS,
         TROPIEZOS_TOLERADOS, n))


def _opts(seat, cupo: int) -> dict:
    opts = {"daily_cap": max(1, cupo), "headless": True, "cookies": seat.cookies,
            "seat": seat.id, "sales_nav": seat.sales_nav}
    if seat.cdp:
        opts["cdp_endpoint"] = seat.cdp
    return opts


def enviar_para_asiento(seat, tope: int = 0,
                        campaigns: tuple[str, ...] | None = None) -> dict:
    """Envía hasta el cupo libre de ese asiento. Devuelve el parte de la corrida."""
    b = seats.budget(seat)
    if not b["listo"]:
        return {"seat": seat.id, "nombre": seat.etiqueta, "enviadas": 0,
                "problema": f"sin sesión ({b['sesion']['detalle']})"}
    cupo = b["invites"]["remaining"]
    if tope:
        cupo = min(cupo, tope)
    if cupo <= 0:
        return {"seat": seat.id, "nombre": seat.etiqueta, "enviadas": 0,
                "problema": "sin cupo hoy"}

    lote = candidatos(seat.id, cupo, campaigns)
    if not lote:
        return {"seat": seat.id, "nombre": seat.etiqueta, "enviadas": 0,
                "problema": "sin candidatos disponibles"}

    listo = seats.asegurar_envio(seat)
    if not listo.get("ok"):
        return {"seat": seat.id, "nombre": seat.etiqueta, "enviadas": 0,
                "problema": listo.get("error", "no pude preparar la sesión")}

    job = manager.launch_linkedin([c["linkedin"] for c in lote], _opts(seat, cupo))
    seats.marcar_arranque(seat)
    return {"seat": seat.id, "nombre": seat.etiqueta, "run_id": job.job_id,
            "lote": lote, "lanzadas": len(lote)}


def _esperar(runs: list[str], espera: float) -> None:
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        vivos = [r for r in runs
                 if (manager.get(r) is not None and manager.get(r).status == "running")]
        if not vivos:
            return
        time.sleep(3)


def _registrar(parte: dict) -> dict:
    """Cruza los eventos del job con el lote y deja constancia de cada intento.

    Los envíos buenos los ingiere `sync` (que es lo que crea la actividad y mueve
    el stage). Aquí se guarda lo que NO salió, que es justo lo que el trabajo de
    mañana necesita saber para no repetirlo.

    Cada fallo dice además si el contacto vuelve al lote o si queda descansando,
    porque son cosas distintas y desde fuera se veían iguales: veinte «error» en
    el parte podían ser veinte perfiles sin ruta o un Chrome que no arrancó.
    """
    from contacts import _norm_li
    run_id = parte.get("run_id", "")
    if not run_id:
        return parte
    eventos = {_norm_li(e.get("url", "")): e
               for e in read_jsonl(RESULTS_DIR / f"{run_id}-events.jsonl")
               if e.get("idx", 0) != 0}
    enviadas, fallidas = 0, []

    # Si ningún perfil del lote tiene evento, el motor no arrancó (el 24-sep:
    # «No module named 'playwright'» seis veces en 33 minutos). Eso no dice nada
    # de los perfiles; anotarlo como `sin_evento` a cada uno les gastaba los
    # tres tropiezos de la semana y los congelaba sin haberlos visitado.
    lote = parte.get("lote", [])
    if lote and not any(_norm_li(c["linkedin"]) in eventos for c in lote):
        parte.update(enviadas=0, fallidas=[], reintentables=0, congelados=0,
                     problema="el motor no llegó a ningún perfil (¿no arrancó?)")
        parte.pop("lote", None)
        return parte

    def anotar(c: dict, estado: str, detalle: str, motivo: str) -> None:
        store.add_intento(c["id"], parte["seat"], estado, detalle, run_id)
        fallidas.append({"nombre": c["nombre"], "motivo": motivo,
                         "reintentara": estado in ESTADOS_TRANSITORIOS})

    for c in parte.get("lote", []):
        e = eventos.get(_norm_li(c["linkedin"]))
        if e is None:
            anotar(c, "sin_evento", "el motor no llegó a este perfil", "sin evento")
            continue
        if e.get("status") in OK:
            enviadas += 1
            continue
        estado, detalle = e.get("status", "?"), e.get("detail", "")
        anotar(c, estado, detalle, f"{estado}: {detalle[:40]}")

    parte["enviadas"] = enviadas
    parte["fallidas"] = fallidas
    parte["reintentables"] = sum(1 for f in fallidas if f["reintentara"])
    parte["congelados"] = len(fallidas) - parte["reintentables"]
    parte.pop("lote", None)
    return parte


def correr(tope_por_asiento: int = 0, espera: float = 420.0,
           campaigns: tuple[str, ...] | None = None,
           solo: set[str] | None = None) -> dict:
    """Una corrida completa: un job por asiento, en paralelo, y luego la ingesta.

    `solo` acota a unos asientos — lo usa la vía rápida, que sirve nada más a
    los perfiles que reclaman su origen.
    """
    partes = [enviar_para_asiento(s, tope_por_asiento, campaigns)
              for s in seats.en_uso()
              if s.activo and (not solo or s.id in solo)]
    _esperar([p["run_id"] for p in partes if p.get("run_id")], espera)
    partes = [_registrar(p) for p in partes]
    ingesta = sync.backfill_activities()
    return {"ts": store.now_iso(), "asientos": partes, "ingesta": ingesta,
            "enviadas": sum(p.get("enviadas", 0) for p in partes)}


# ----------------------------- trabajo de cada mañana ----------------------
HORA_DESDE, HORA_HASTA = 8, 11


def activo() -> bool:
    return store.kv_get("envio_diario", "0") == "1"


def set_activo(v: bool) -> None:
    store.kv_set("envio_diario", "1" if v else "0")


def estado() -> dict:
    return {"activo": activo(),
            "ultima_corrida": store.kv_get("envio_diario_fecha", ""),
            "ultimo_parte": store.kv_get("envio_diario_parte", ""),
            "ventana": f"{HORA_DESDE}:00–{HORA_HASTA}:00, días hábiles"}


def toca_ahora(ahora: datetime | None = None) -> tuple[bool, str]:
    """¿Le toca correr? Una vez al día, día hábil, por la mañana."""
    import scheduler
    ahora = ahora or datetime.now()
    if not activo():
        return False, "el envío diario está apagado"
    if not scheduler.es_habil(ahora.date()):
        return False, "no es día hábil"
    if not (HORA_DESDE <= ahora.hour < HORA_HASTA):
        return False, f"fuera de la ventana ({HORA_DESDE}:00–{HORA_HASTA}:00)"
    if store.kv_get("envio_diario_fecha", "") == ahora.date().isoformat():
        return False, "ya corrió hoy"
    return True, "toca"


def correr_si_toca(ahora: datetime | None = None) -> dict:
    puede, motivo = toca_ahora(ahora)
    if not puede:
        return {"corrio": False, "motivo": motivo}
    # La fecha se marca ANTES de enviar: si la corrida se cae a mitad, el tick
    # siguiente no la repite y duplica invitaciones. Se retoma mañana.
    hoy = (ahora or datetime.now()).date().isoformat()
    store.kv_set("envio_diario_fecha", hoy)
    import json
    parte = correr()
    store.kv_set("envio_diario_parte", json.dumps(parte, ensure_ascii=False)[:4000])
    return {"corrio": True, **parte}


# --------------------------- vía rápida (Platzi Ventas) --------------------
# Más ancha que la de la mañana: esto reacciona a algo que un vendedor acaba de
# hacer. Pero sigue siendo horario hábil — ver el encabezado del módulo.
HORA_VR_DESDE, HORA_VR_HASTA = 8, 18


def via_rapida_activa() -> bool:
    return store.kv_get("via_rapida", "0") == "1"


def set_via_rapida(v: bool) -> None:
    store.kv_set("via_rapida", "1" if v else "0")


def _sin_asiento(campaigns: tuple[str, ...]) -> list[dict]:
    """Recién llegados de este origen que todavía no tienen dueño.

    Sin asiento no hay invitación posible: `candidatos()` filtra por `li_seat`.
    Es justo lo que le pasaba a la máquina de Platzi Ventas — dejaba los
    decisores en la base y ahí se quedaban, porque nadie les asignaba perfil.
    """
    if not campaigns:
        return []
    return store.fetch_all(
        "SELECT id, nombre, empresa FROM contacts "
        "WHERE COALESCE(li_seat,'')='' AND linkedin<>'' AND stage='nuevo' "
        "  AND COALESCE(hubspot_verdict,'') <> 'rojo' "
        "  AND campaign IN (%s) ORDER BY id" % ",".join("?" * len(campaigns)),
        tuple(campaigns))


def _anclas_por_empresa() -> dict[str, str]:
    """Empresa normalizada → perfil que ya la tiene.

    La empresa entera va a un solo VP. Dos gerentes del mismo sitio invitados la
    misma semana por dos perfiles distintos no se lee como dos acercamientos
    personales: se lee como una campaña — y si hablan entre ellos, que es lo
    normal, el efecto es peor que no haber escrito (misma regla que `reparto`).
    """
    anclas: dict[str, str] = {}
    for r in store.fetch_all(
            "SELECT empresa, li_seat FROM contacts "
            "WHERE COALESCE(li_seat,'')<>'' AND COALESCE(empresa,'')<>'' "
            "ORDER BY id"):
        k = reparto.clave_empresa(r["empresa"])
        if k:
            anclas.setdefault(k, r["li_seat"])
    return anclas


def asignar_pendientes(campaigns: tuple[str, ...] = ORIGENES_VIA_RAPIDA,
                       simular: bool = False) -> dict:
    """Da asiento a los recién llegados de este origen, empresa por empresa.

    ## El peso es el cupo libre de HOY, no el techo diario

    En el reparto del backlog (`reparto.plan`) manda el techo de cada perfil,
    porque ahí se colocan miles de contactos de una vez y lo que importa es la
    proporción a la larga. Aquí no: llegan de a dos y salen en minutos. Con esa
    misma regla, un perfil con el doble de techo y la misma carga se lleva todo
    lo que entra hasta ponerse a la par —cientos de contactos— así que los diez
    del día caían en un solo perfil, salían diez (su cupo) y los diez cupos
    libres del otro se perdían. Con el cupo libre como peso salen veinte.

    ## La empresa entera sigue yendo a un solo perfil

    Dos gerentes del mismo sitio invitados la misma semana por dos VP distintos
    no se lee como dos acercamientos personales: se lee como una campaña. Si la
    empresa ya tiene dueño, ahí va —aunque ese perfil ya no tenga cupo hoy y la
    invitación salga mañana. El reparto no se deshace; la demora sí se recupera.
    """
    pend = _sin_asiento(campaigns)
    if not pend:
        return {"asignados": 0, "esperando": [], "detalle": [], "problema": ""}

    listos = {b["seat_id"]: b for b in seats.budgets() if b["listo"]}

    # ## Reparto por origen: exclusivo, no preferente
    #
    # Si algún perfil reclama este origen (`Seat.prioriza`), lo que entra por
    # aquí es SOLO para ellos. Fabrizzio y Jefferson trabajan lo de Platzi
    # Ventas; Jess y Patricia, el backlog general.
    #
    # Y es exclusivo a propósito, no «primero ellos y si no hay cupo, otro»:
    # dar asiento decide quién firma **para siempre** —el que invita es el único
    # que después puede escribirle, porque es el único a quien el prospecto
    # aceptó—, así que un desbordamiento al perfil equivocado no se arregla
    # mañana. Si los reclamantes no tienen cupo hoy, lo que entró espera.
    # Los reclamantes se buscan entre TODOS los perfiles con dueño, no entre los
    # que hoy pueden enviar. Calcularlo sobre los listos tiene un agujero: el día
    # que los reclamantes estén apagados o sin sesión, el conjunto queda vacío,
    # la restricción no aplica y el origen desborda al perfil equivocado. Y ese
    # desbordamiento no se deshace.
    reclaman = {x.id for x in seats.en_uso()
                if x.configurado and (x.prioriza or "") in (campaigns or ())}
    if reclaman:
        listos = {sid: b for sid, b in listos.items() if sid in reclaman}

    if not listos:
        motivo = ("los perfiles que atienden este origen no pueden enviar hoy "
                  "(apagados o sin sesión): se espera, no se desborda a otro"
                  if reclaman else "ningún perfil con sesión viva")
        return {"asignados": 0, "detalle": [],
                "esperando": [{"nombre": c["nombre"], "empresa": c["empresa"],
                               "motivo": motivo} for c in pend],
                "problema": motivo}

    anclas = _anclas_por_empresa()
    cupo = {sid: b["invites"]["remaining"] for sid, b in listos.items()}
    carga = {sid: b["asignados"] for sid, b in listos.items()}   # desempate
    dados = {sid: 0 for sid in listos}
    hechos, esperando = [], []

    for c in pend:
        k = reparto.clave_empresa(c["empresa"] or "")
        destino = anclas.get(k) if k else None
        if destino is not None and destino not in listos:
            # Su empresa ya es de un perfil que hoy no puede enviar. Esperar es
            # mejor que partirla entre dos VP: el reparto no se deshace.
            esperando.append({"nombre": c["nombre"], "empresa": c["empresa"],
                              "motivo": f"su empresa ya es de {destino}, que hoy "
                                        "no tiene sesión o está apagado"})
            continue
        if destino is None:
            # Empresa nueva: al perfil con más cupo libre ahora mismo. El
            # desempate por carga histórica conserva algo del balance a la larga
            # sin dejar que decida cuándo sale la invitación.
            libres = [sid for sid in listos if cupo[sid] - dados[sid] > 0]
            if not libres:
                esperando.append({"nombre": c["nombre"], "empresa": c["empresa"],
                                  "motivo": "ningún perfil con cupo de "
                                            "invitaciones libre hoy"})
                continue
            destino = max(libres, key=lambda sid: (cupo[sid] - dados[sid],
                                                   -carga[sid], sid))
        if not simular:
            store.set_seat(c["id"], destino)
        if k:
            anclas.setdefault(k, destino)
        dados[destino] = dados.get(destino, 0) + 1
        hechos.append({"contact_id": c["id"], "nombre": c["nombre"],
                       "empresa": c["empresa"], "a": destino})

    return {"asignados": len(hechos), "detalle": hechos,
            "esperando": esperando, "problema": ""}


def pendientes(campaigns: tuple[str, ...] = ORIGENES_VIA_RAPIDA) -> dict:
    """Cuántos esperan solicitud en la base que atienden los perfiles de Sales.

    Se cuenta **toda** la base de esos perfiles, sin filtrar por origen: desde
    que se les asignan contactos a mano, su base ya no es solo lo que llega de
    la app de Ventas —los 200 del backlog conservan su `campaign` original— y
    filtrar por origen los dejaría fuera del conteo y del envío.
    """
    reclamantes = {x.id for x in seats.en_uso()
                   if x.activo and x.configurado and (x.prioriza or "") in campaigns}
    listos = 0
    for sid in reclamantes:
        listos += len(candidatos(sid, 2000))
    sin = len(_sin_asiento(campaigns))
    return {"sin_asiento": sin, "con_asiento": listos, "total": sin + listos,
            "asientos": sorted(reclamantes)}


def atienden_origen(campaigns: tuple[str, ...] = ORIGENES_VIA_RAPIDA) -> list[dict]:
    """Perfiles que atienden este origen en exclusiva, y si hoy pueden.

    Va en la respuesta del tablero porque si no, la regla vive solo en un campo
    kv: quien mira la pantalla ve tarjetas firmadas por Jess de cuando ella
    atendía esta base y no tiene forma de saber que ya no le entra nada nuevo.
    """
    out = []
    for x in seats.en_uso():
        if not x.configurado or (x.prioriza or "") not in campaigns:
            continue
        b = seats.budget(x)
        out.append({"seat_id": x.id, "vp": x.etiqueta, "rol": x.rol,
                    "listo": b["listo"], "cupo": b["invites"]["remaining"],
                    "secuencia": x.secuencia_am or ""})
    return out


def estado_via_rapida() -> dict:
    return {"activo": via_rapida_activa(),
            "origenes": list(ORIGENES_VIA_RAPIDA),
            "atienden": atienden_origen(),
            "ventana": f"{HORA_VR_DESDE}:00–{HORA_VR_HASTA}:00, días hábiles",
            "toca_ahora": toca_via_rapida()[1],
            "pendientes": pendientes(),
            "ultima_corrida": store.kv_get("via_rapida_ts", ""),
            "ultimo_parte": store.kv_get("via_rapida_parte", ""),
            "ultimo_aviso": store.kv_get("via_rapida_aviso", "")}


def toca_via_rapida(ahora: datetime | None = None) -> tuple[bool, str]:
    """¿Puede salir ahora? A diferencia de la corrida de la mañana no hay tope
    de una-vez-al-día: esto es reactivo, sale cuando hay algo que sacar."""
    import scheduler
    ahora = ahora or datetime.now()
    if not via_rapida_activa():
        return False, "la vía rápida está apagada"
    if not scheduler.es_habil(ahora.date()):
        return False, "no es día hábil"
    if not (HORA_VR_DESDE <= ahora.hour < HORA_VR_HASTA):
        return False, f"fuera de la ventana ({HORA_VR_DESDE}:00–{HORA_VR_HASTA}:00)"
    return True, "toca"


# Dos llamadores dentro de uvicorn: el tick y el aviso de la máquina. Sin
# candado, los dos podían armar el mismo lote y mandar la misma invitación dos
# veces. El que llega tarde no espera: deja pedida otra vuelta al que corre,
# porque lo que trae puede haber entrado después de que ese armara su lote.
_VR_LOCK = threading.Lock()
_VR_OTRA_VUELTA = threading.Event()


def correr_via_rapida(ahora: datetime | None = None, simular: bool = False,
                      forzar: bool = False, espera: float = 420.0) -> dict:
    """Asiento + invitación para lo que acaba de entrar por Platzi Ventas.

    Corre en cada tick del copiloto y **antes** de la corrida general, que es lo
    que le da la prioridad: si el backlog se lleva el cupo del día primero, la
    cuenta que el vendedor está trabajando hoy sale mañana. Y corre también en
    cuanto la máquina avisa que dejó decisores en la base (`avisar_via_rapida`).
    """
    if simular:
        return _correr_via_rapida(ahora, simular, forzar, espera)
    if not _VR_LOCK.acquire(blocking=False):
        _VR_OTRA_VUELTA.set()
        return {"corrio": False,
                "motivo": "ya hay una vía rápida corriendo; repite al terminar"}
    try:
        while True:
            _VR_OTRA_VUELTA.clear()
            res = _correr_via_rapida(ahora, simular, forzar, espera)
            if not _VR_OTRA_VUELTA.is_set():
                return res
    finally:
        _VR_LOCK.release()


def avisar_via_rapida() -> dict:
    """La máquina dejó decisores de Platzi Ventas en la base: corre ya, sin
    esperar al tick. No bloquea — la corrida espera a sus jobs, minutos."""
    puede, motivo = toca_via_rapida()
    store.kv_set("via_rapida_aviso", json.dumps(
        {"ts": store.now_iso(), "lanzada": puede, "motivo": motivo}, ensure_ascii=False))
    if not puede:
        return {"lanzada": False, "motivo": f"{motivo}: sale en el primer tick que toque"}
    threading.Thread(target=correr_via_rapida, daemon=True,
                     name="via-rapida-aviso").start()
    return {"lanzada": True}


def _correr_via_rapida(ahora: datetime | None, simular: bool, forzar: bool,
                       espera: float) -> dict:
    puede, motivo = (True, "forzada") if forzar else toca_via_rapida(ahora)
    if not puede:
        return {"corrio": False, "motivo": motivo}
    pend = pendientes()
    if not pend["total"]:
        return {"corrio": False, "motivo": "no hay nada nuevo de este origen"}

    reparto_ = asignar_pendientes(simular=simular)
    if simular:
        # En simulación el reparto no se escribió, así que `candidatos()` todavía
        # no ve a los recién llegados: el lote se arma con los que ya tenían
        # asiento MÁS los que el reparto acaba de colocar en el aire.
        cupos = {b["seat_id"]: b["invites"]["remaining"]
                 for b in seats.budgets()
                 if b["listo"] and (b.get("prioriza") or "") in ORIGENES_VIA_RAPIDA}
        saldria: dict[str, list[dict]] = {}
        for sid, cupo in cupos.items():
            saldria[sid] = [{"nombre": c["nombre"], "empresa": c["empresa"]}
                            for c in candidatos(sid, cupo)]
        for m in reparto_["detalle"]:
            sid = m["a"]
            if len(saldria.get(sid, [])) < cupos.get(sid, 0):
                saldria.setdefault(sid, []).append(
                    {"nombre": m["nombre"], "empresa": m["empresa"]})
        return {"corrio": False, "motivo": "simulación: no se envió nada",
                "reparto": reparto_, "pendientes": pend, "saldria": saldria}

    # Sin filtro por origen y solo para los perfiles que lo reclaman: su base
    # entera es la base prioritaria (ver `pendientes`).
    reclamantes = {x.id for x in seats.en_uso()
                   if x.activo and x.configurado
                   and (x.prioriza or "") in ORIGENES_VIA_RAPIDA}
    parte = correr(espera=espera, solo=reclamantes)
    parte["reparto"] = reparto_
    store.kv_set("via_rapida_ts", parte.get("ts") or store.now_iso())
    import json
    store.kv_set("via_rapida_parte",
                 json.dumps(parte, ensure_ascii=False, default=str)[:4000])
    return {"corrio": True, **parte}
