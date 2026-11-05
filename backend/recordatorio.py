"""Recordatorio diario: qué está esperando a una persona.

El cockpit hace solo casi todo —invita, detecta aceptaciones, inscribe en la
secuencia— pero hay cosas que ningún robot puede: recargar créditos, reconectar
un buzón, decidir si alguien aceptó cuando la evidencia es ambigua. Esas se
quedan quietas sin que nadie se entere, porque no fallan: simplemente no pasan.

Esto las junta y las manda por correo una vez al día.

## Determinista a propósito

El texto lo arma este script, no un modelo. Un recordatorio que se inventa una
cifra es peor que no tenerlo: se actúa sobre él. Todo lo que sale de aquí viene
de la base del cockpit, del presupuesto local o de la API de Amplemarket, y lo
que no se pudo leer se dice («no pude leer los buzones»), no se rellena.

El modelo solo lo entrega: `launchd` llama a `claude -p`, que corre esto y pega
la salida en un correo por el conector de Gmail. Así no hace falta ni una
credencial de SMTP nueva.

## Solo lo que está trabado

Si no hay nada pendiente, la sección de bloqueos sale vacía y el correo lo dice
en una línea. Un recordatorio que llega todos los días con la misma lista larga
deja de leerse; uno que la mayoría de los días dice «nada trabado» hace que el
día que trae algo se note.
"""
from __future__ import annotations

import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))

import am_budget                        # noqa: E402
import seats                            # noqa: E402
import store                            # noqa: E402
from settings import _read_env          # noqa: E402

DESTINO = "jtrujillo@platzi.com"


def _cliente_am():
    for anc in _AQUI.parents:
        cand = anc / "prospeccion-2000" / "amplemarket.py"
        if cand.is_file():
            if str(cand.parent) not in sys.path:
                sys.path.insert(0, str(cand.parent))
            import amplemarket           # type: ignore
            return amplemarket
    return None


def _n(sql: str, params: tuple = ()) -> int:
    r = store.fetch_one(sql, params)
    return int((r or {}).get("n") or 0)


# ----------------------------- bloqueos ------------------------------------
def creditos_amplemarket() -> dict | None:
    """El pozo de correo en cero bloquea el revelado de teléfono aunque el de
    teléfono esté pagado — ver `am_budget`."""
    st = am_budget.status()
    if not st["updated_at"]:
        return None
    sin_tel = _n("SELECT COUNT(*) n FROM contacts WHERE stage='conectado' "
                 "AND COALESCE(celular,'')='' AND COALESCE(linkedin,'')<>''")
    if not sin_tel:
        return None
    ok, motivo = am_budget.alcanza_para_telefonos(sin_tel)
    if ok:
        return None
    c = am_budget.costo_telefonos(sin_tel)
    p = st["pozos"]
    return {"titulo": f"Recargar créditos de CORREO en Amplemarket",
            "detalle": [
                f"{sin_tel} conectados siguen sin teléfono y no se pueden revelar.",
                f"Hacen falta {c['correo']:g} créditos de correo y {c['telefono']:g} de teléfono.",
                f"Pozos hoy: correo {p['correo']['disponibles']:g} · "
                f"teléfono {p['telefono']['disponibles']:g} (ya pagados).",
                "El paso «find» de 0,5 se cobra del pozo de correo, así que con "
                "correo en cero no sale ni un teléfono.",
            ],
            "hacer": "Settings → Credits en app.amplemarket.com. Después: "
                     "python3 telefonos_am.py"}


def buzones_amplemarket() -> dict | None:
    """Una secuencia cuyo remitente esté caído acumula leads y no manda nada.
    La API no dice desde qué buzón envía cada secuencia, así que esto reporta
    los buzones caídos y deja la conclusión a quien lo lee."""
    am = _cliente_am()
    if am is None or not am.configured():
        return None
    try:
        import requests
        r = requests.get(am.BASE + "/mailboxes", headers=am._headers(), timeout=25)
        if not r.ok:
            return {"titulo": "No pude leer los buzones de Amplemarket",
                    "detalle": [f"HTTP {r.status_code}"], "hacer": ""}
        lst = r.json().get("mailboxes") or []
    except Exception as e:
        return {"titulo": "No pude leer los buzones de Amplemarket",
                "detalle": [f"{e.__class__.__name__}: {str(e)[:120]}"], "hacer": ""}

    caidos = [m for m in lst if (m.get("status") or "") == "needs_reconnection"]
    if not caidos:
        return None
    activos = sum(1 for m in lst if (m.get("status") or "") == "active")
    return {"titulo": f"{len(caidos)} buzón(es) de Amplemarket piden reconexión",
            "detalle": [f"{m.get('email')} (tope {m.get('daily_email_limit') or '?'}/día)"
                        for m in caidos]
                       + [f"Activos ahora: {activos} de {len(lst)}."],
            "hacer": "Si una de las dos secuencias envía desde uno de estos, los "
                     "leads inscritos se acumulan sin salir."}


# Solo lo marcado HOY. La nota `revisar` se dispara cuando el detalle del envío
# no contiene «confirmado», y la ruta `sin-nota-preload` —la normal— nunca lo
# dice: por eso el acumulado es de decenas y no son decenas de decisiones
# pendientes, es un artefacto del chequeo de confirmación. Un recordatorio que
# repite 77 nombres cada mañana se deja de leer, así que aquí va lo que apareció
# hoy y el acumulado se menciona en una línea, para que se limpie una vez y no
# se confunda con trabajo del día.
TOPE_LISTA = 6


def revisar_a_mano() -> dict | None:
    """Aceptaciones ambiguas: salieron de pendientes sin envío confirmado.

    Solo los que siguen en `contactado`: si ya se movió a conectado o a
    conversación, alguien —o una señal posterior— ya lo resolvió.
    """
    hoy = date.today().isoformat()
    filas = store.fetch_all(
        "SELECT DISTINCT c.nombre, c.empresa, c.cargo FROM intentos i "
        "JOIN contacts c ON c.id=i.contact_id "
        "WHERE i.estado='revisar' AND substr(i.ts,1,10)=? "
        "  AND c.stage='contactado' "
        "ORDER BY c.nombre", (hoy,))
    if not filas:
        return None
    backlog = _n("SELECT COUNT(DISTINCT i.contact_id) n FROM intentos i "
                 "JOIN contacts c ON c.id=i.contact_id "
                 "WHERE i.estado='revisar' AND c.stage='contactado'")
    detalle = [f"{f['nombre']} — {f['cargo'] or 'sin cargo'} @ {f['empresa']}"
               for f in filas[:TOPE_LISTA]]
    if len(filas) > TOPE_LISTA:
        detalle.append(f"…y {len(filas) - TOPE_LISTA} más de hoy.")
    if backlog > len(filas):
        detalle.append(f"Acumulado histórico: {backlog}. La mayoría es artefacto "
                       "del chequeo de «confirmado» en el envío, no ambigüedad "
                       "real — vale una limpieza de una sola vez.")
    return {"titulo": f"{len(filas)} contacto(s) quedaron ambiguos hoy",
            "detalle": detalle,
            "hacer": "Salieron de la lista de pendientes de LinkedIn sin que el "
                     "envío estuviera confirmado: o aceptaron, o la invitación "
                     "nunca salió. El sistema no lo decide solo."}


def sesiones_linkedin() -> dict | None:
    muertas = []
    for s in seats.en_uso():
        # `configurado` = alguien puso el nombre del VP. Un asiento vacío es un
        # hueco reservado, no una sesión caída: reportarlo manda a arreglar algo
        # que no está roto, y un recordatorio con una alarma falsa fija se deja
        # de leer entero.
        if not s.activo or not s.configurado:
            continue
        b = seats.budget(s)
        if not b["sesion"]["ok"]:
            muertas.append(f"{s.etiqueta}: {b['sesion']['detalle']}")
    if not muertas:
        return None
    return {"titulo": f"{len(muertas)} perfil(es) de LinkedIn sin sesión",
            "detalle": muertas,
            "hacer": "Sin sesión el cupo es 0: no sale ninguna invitación. "
                     "Ajustes → Perfiles en el cockpit."}


BLOQUEOS = (creditos_amplemarket, buzones_amplemarket, revisar_a_mano,
            sesiones_linkedin)


# ----------------------------- lo que corrió -------------------------------
def integrados_hoy() -> list[dict]:
    """Prospectos que entraron a cada secuencia hoy, por VP.

    Es el número que se quiere ver de un golpe: cuántos y a cuál de las dos.
    Solo los `añadido` — los que Amplemarket saltó o los que ya estaban no son
    prospectos nuevos en la secuencia, y contarlos infla la cifra.
    """
    import secuencias_am as sa
    nombres = sa.nombres_secuencias()
    hoy = date.today().isoformat()
    filas = store.fetch_all(
        "SELECT json_extract(meta_json,'$.vp') vp, "
        "       json_extract(meta_json,'$.secuencia') seq, COUNT(*) n "
        "FROM activities WHERE type='enrolled' AND substr(ts,1,10)=? "
        "  AND json_extract(meta_json,'$.resultado')='añadido' "
        "GROUP BY 1,2 ORDER BY n DESC", (hoy,))
    return [{"vp": f["vp"] or "?", "n": f["n"],
             "secuencia": nombres.get(f["seq"], f["seq"] or "")} for f in filas]


def linkedin_hoy() -> tuple[int, int]:
    hoy = date.today().isoformat()
    return (_n("SELECT COUNT(*) n FROM activities WHERE type='invite_sent' "
               "AND substr(ts,1,10)=?", (hoy,)),
            _n("SELECT COUNT(*) n FROM activities WHERE type='accepted' "
               "AND substr(ts,1,10)=?", (hoy,)))


def armar() -> dict:
    bloqueos = []
    for fn in BLOQUEOS:
        try:
            b = fn()
        except Exception as e:                     # un bloqueo que falla no
            b = {"titulo": f"no pude revisar «{fn.__name__}»",   # tumba el correo
                 "detalle": [f"{e.__class__.__name__}: {str(e)[:120]}"], "hacer": ""}
        if b:
            bloqueos.append(b)

    integrados = integrados_hoy()
    total = sum(i["n"] for i in integrados)
    invitaciones, aceptaciones = linkedin_hoy()
    hoy = datetime.now().strftime("%d/%m/%Y")

    asunto = (f"Cockpit · {total} prospecto(s) a secuencias — {hoy}" if total
              else f"Cockpit · sin prospectos nuevos a secuencias — {hoy}")

    L = []
    if integrados:
        L.append(f"Hoy se integraron {total} prospecto(s) a las secuencias:")
        L.append("")
        for i in integrados:
            L.append(f"    {i['n']:>3} → {i['vp']}")
            if i["secuencia"]:
                L.append(f"          {i['secuencia']}")
        L.append("")
    else:
        L += ["Hoy no entró ningún prospecto nuevo a las secuencias.", ""]

    L.append(f"LinkedIn: {invitaciones} invitaciones · {aceptaciones} aceptaciones.")

    if bloqueos:
        L += ["", f"Te esperan {len(bloqueos)}:"]
        for b in bloqueos:
            L.append(f"    · {b['titulo']}")
            if b["hacer"]:
                L.append(f"      {b['hacer']}")
    else:
        L += ["", "Nada trabado esperando una decisión tuya."]

    L += ["", "—", "cockpit/backend/recordatorio.py · para no recibirlo: "
          "launchctl unload ~/Library/LaunchAgents/com.platzi.cockpit.recordatorio.plist"]
    return {"asunto": asunto, "cuerpo": "\n".join(L),
            "bloqueos": len(bloqueos), "integrados": total, "destino": DESTINO}


# ----------------------------- entrega -------------------------------------
# ## Por qué SMTP y no el conector de Gmail
#
# El conector de Gmail funciona cuando el CLI se llama desde una sesión
# interactiva, pero **no** cuando lo arranca launchd: ahí el CLI contesta que
# «Gmail requiere autorización» y no manda nada. Probado el 8-sep-2026, y es la
# razón por la que un recordatorio diario no puede depender de él.
#
# Con SMTP el script se entrega solo: ni CLI, ni modelo, ni conector. Necesita
# una contraseña de aplicación de Google (Cuenta → Seguridad → Contraseñas de
# aplicaciones), en `PLATZI/.env` como SMTP_USER y SMTP_PASSWORD. Mismas
# variables que ya usa `rsvp.py`, así que se configura una vez para los dos.
def _smtp() -> tuple[str, str, str, int]:
    env = _read_env()
    g = lambda k, d="": (env.get(k) or os.environ.get(k) or d).strip()  # noqa: E731
    return (g("SMTP_USER"), g("SMTP_PASSWORD"),
            g("SMTP_HOST", "smtp.gmail.com"), int(g("SMTP_PORT", "587")))


def enviar(r: dict) -> tuple[bool, str]:
    user, pwd, host, port = _smtp()
    if not user or not pwd:
        return False, ("SMTP sin configurar: falta SMTP_USER y SMTP_PASSWORD en "
                       "PLATZI/.env (contraseña de aplicación de Google). "
                       "El recordatorio se armó pero no se puede entregar.")
    msg = EmailMessage()
    msg["Subject"] = r["asunto"]
    msg["From"] = f"Cockpit de Platzi <{user}>"
    msg["To"] = r["destino"]
    msg.set_content(r["cuerpo"])
    try:
        with smtplib.SMTP(host, port, timeout=30) as srv:
            srv.starttls()
            srv.login(user, pwd)
            srv.send_message(msg)
    except Exception as e:
        return False, f"{e.__class__.__name__}: {str(e)[:200]}"
    return True, f"enviado a {r['destino']}"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--enviar", action="store_true",
                    help="lo manda por SMTP; sin esto solo lo imprime")
    a = ap.parse_args()

    r = armar()
    if not a.enviar:
        print(f"PARA: {r['destino']}")
        print(f"ASUNTO: {r['asunto']}")
        print()
        print(r["cuerpo"])
        raise SystemExit(0)

    ok, detalle = enviar(r)
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} · integrados {r['integrados']} "
          f"· bloqueos {r['bloqueos']} · {'OK' if ok else 'FALLÓ'}: {detalle}")
    raise SystemExit(0 if ok else 1)
