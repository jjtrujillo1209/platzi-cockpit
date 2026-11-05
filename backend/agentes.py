"""Vigilancia de los agentes de launchd: que ninguno se calle sin que se note.

Por qué existe
--------------
El 21-sep-2026 a las 12:20 la máquina reinició y **los cuatro** agentes de
launchd quedaron muertos hasta el 23-sep. Dos días sin prospección, y el aviso
del tablero de Platzi Ventas («sin dar señales») era el de siempre: ambiguo por
construcción (ver `modulo-prospeccion/backend/latido.py`).

La avería no fue del código. launchd no pudo abrir el archivo al que apuntaban
`StandardOutPath`/`StandardErrorPath` y abortó el job **antes de ejecutarlo**,
con `EX_CONFIG` (78). Los cuatro logs vivían bajo `~/Documents`, que es una
carpeta protegida por TCC, y tras el reinicio los cuatro archivos tenían el
atributo extendido `com.apple.macl` — la lista de control por-app que macOS le
cuelga a un archivo dentro de una carpeta protegida. launchd no está en esa
lista, así que no pudo abrirlos.

Lo perverso del caso: **el canal por el que la avería se habría contado era la
avería misma**. No se escribió ni una línea en ningún log, en ninguna parte, en
48 horas. Y el vigía (`latido.py --vigilar`), que existe justo para avisar de
esto, murió exactamente igual — es otro agente de launchd con el log en la misma
carpeta.

Lo que este módulo arregla
--------------------------
1. **Mira a launchd, no a los logs.** `launchctl list <label>` publica
   `LastExitStatus` aunque el proceso no haya llegado a arrancar. El 78 estuvo
   ahí, visible, las 48 horas. Nadie se lo preguntó.
2. **Repara la causa concreta**: le quita el `com.apple.macl` al log y vuelve a
   arrancar el agente. Idempotente y barato.
3. **Se niega a dar por sano un agente cuyo log viva bajo `~/Documents`**, que
   es la condición que dispara todo. Es un aviso, no un fallo: sigue funcionando
   hasta el día en que macOS decida colgarle el atributo.

Lo que este módulo NO puede hacer
---------------------------------
Vigilarse a sí mismo. Corre dentro de uvicorn, así que si el backend del cockpit
se cae, esto se cae con él. La diferencia es que **el backend caído se nota**
—la interfaz deja de cargar— y la máquina callada no. Para cerrar ese lado,
`latido.py --vigilar` (agente aparte, cada 30 min) comprueba que el cockpit
responda. Se vigilan mutuamente y no comparten proceso.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
UID = os.getuid()

# Carpetas que macOS protege con TCC. Un log aquí dentro es una bomba de
# relojería: el día que el archivo reciba `com.apple.macl`, el agente muere.
PROTEGIDAS = (HOME / "Documents", HOME / "Desktop", HOME / "Downloads")

# Dónde deberían vivir los logs: `~/Library/Logs` no está protegida.
LOGS = HOME / "Library/Logs/platzi"

AGENTES: dict[str, dict] = {
    "com.platzi.maquina.prospeccion": {
        "nombre": "Máquina de prospección",
        "hace": "peticiones del cockpit y estado de LinkedIn → Ventas cada 5 min "
                "(la cola la atiende platzi-cockpit-nube)",
        "critico": True,
    },
    "com.platzi.maquina.oido": {
        "nombre": "Oído de la máquina",
        "hace": "mira la cola de Ventas cada 20 s y arranca la máquina al llegar algo",
        "critico": False,
    },
    "com.platzi.maquina.vigia": {
        "nombre": "Vigía de la máquina",
        "hace": "avisa si la máquina se calla (cada 30 min)",
        "critico": True,
    },
    "com.platzi.cockpit.backend": {
        "nombre": "Backend del cockpit",
        "hace": "API :8532 y el tick que invita por LinkedIn",
        "critico": True,
    },
    "com.platzi.cockpit.recordatorio": {
        "nombre": "Recordatorio diario",
        "hace": "correo de las 9:37",
        "critico": False,
    },
    "com.platzi.cockpit.frontend": {
        "nombre": "Interfaz del cockpit",
        "hace": "Vite en :5181",
        "critico": False,
    },
    "com.platzi.cockpit.despierto": {
        "nombre": "Mac despierto",
        "hace": "caffeinate -s: no se duerme mientras esté en la corriente",
        "critico": False,
    },
    "com.platzi.cockpit.ngrok": {
        "nombre": "Túnel de ngrok",
        "hace": "webhook de SendGrid (open/click/bounce)",
        "critico": False,
    },
}

# Apagados a propósito (`launchctl bootout`, con el plist intacto para volver
# atrás). No se vigilan ni se reparan: si no, `reparar()` los volvería a cargar.
# Para devolverle al Mac el trabajo, se quita la línea y se hace bootstrap.
RETIRADOS: dict[str, str] = {
    "com.platzi.maquina.oido": "la cola de Ventas la mira el cron de platzi-cockpit-nube cada minuto",
}

_TIMEOUT = 10


def _launchctl(*args: str, timeout: int = _TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["launchctl", *args], capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def _campo(texto: str, clave: str) -> str | None:
    """Saca un campo del dict que imprime `launchctl list <label>`."""
    m = re.search(rf'"{clave}"\s*=\s*([^;]+);', texto)
    return m.group(1).strip().strip('"') if m else None


def _decodifica(crudo: int | None) -> tuple[int | None, int | None]:
    """`launchctl list` publica el estado CRUDO de wait(2), no el código.

    Un `exit 78` sale como 19968 (78 << 8) y un proceso matado con SIGTERM sale
    como 15. Leerlo tal cual confunde las dos cosas y hace que la avería del
    21-sep (78) no se reconozca. `launchctl print` sí lo decodifica, pero cuesta
    mucho más por agente.
    """
    if crudo is None:
        return None, None
    if crudo < 0:
        # `launchctl list` imprime la señal en negativo (-15 = SIGTERM). Sin
        # esto, el `& 0x7F` sobre un negativo daba una señal inventada (113).
        return None, -crudo
    senal = crudo & 0x7F
    if senal:
        return None, senal
    return (crudo >> 8) & 0xFF, None


def _log_del_plist(label: str) -> Path | None:
    p = HOME / "Library/LaunchAgents" / f"{label}.plist"
    if not p.exists():
        return None
    try:
        import plistlib

        d = plistlib.loads(p.read_bytes())
    except Exception:
        return None
    ruta = d.get("StandardOutPath") or d.get("StandardErrorPath")
    return Path(ruta) if ruta else None


def _en_carpeta_protegida(p: Path) -> bool:
    return any(str(p).startswith(str(d) + os.sep) for d in PROTEGIDAS)


def _tiene_macl(p: Path) -> bool:
    if not p.exists():
        return False
    rc, out = 0, ""
    try:
        r = subprocess.run(
            ["xattr", str(p)], capture_output=True, text=True, timeout=_TIMEOUT
        )
        rc, out = r.returncode, r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0 and "com.apple.macl" in out


def estado_de(label: str) -> dict:
    """Veredicto de un agente, preguntándole a launchd (no a sus logs)."""
    meta = AGENTES.get(label, {})
    rc, out = _launchctl("list", label)
    info: dict = {
        "label": label,
        "nombre": meta.get("nombre", label),
        "hace": meta.get("hace", ""),
        "critico": bool(meta.get("critico")),
        "pid": None,
        "ultimo_codigo": None,
        "ultima_senal": None,
        "estado_crudo": None,
        "log": None,
        "log_protegido": False,
        "log_con_macl": False,
    }

    log = _log_del_plist(label)
    if log is not None:
        info["log"] = str(log)
        info["log_protegido"] = _en_carpeta_protegida(log)
        info["log_con_macl"] = _tiene_macl(log)

    if rc != 0:
        # No está cargado en launchd: alguien hizo bootout, o el plist no está.
        info["veredicto"] = "descargado"
        info["detalle"] = "no está cargado en launchd"
        return info

    pid = _campo(out, "PID")
    crudo = _campo(out, "LastExitStatus")
    info["pid"] = int(pid) if pid and pid.isdigit() else None
    try:
        info["estado_crudo"] = int(crudo) if crudo is not None else None
    except ValueError:
        info["estado_crudo"] = None
    info["ultimo_codigo"], info["ultima_senal"] = _decodifica(info["estado_crudo"])

    cod = info["ultimo_codigo"]
    if info["pid"] is not None and cod != 78:
        # Está corriendo AHORA: el último código es historia. Se comprueba antes
        # que nada porque un agente con KeepAlive (el backend) guarda el 15 del
        # SIGTERM de su último reinicio y leerlo como avería lo daría por caído
        # estando sano. El 78 es la excepción: launchd aborta el job antes de
        # ejecutarlo, así que un pid ahí no significa que el trabajo corra.
        info["veredicto"] = "corriendo"
        info["detalle"] = f"pid {info['pid']}"
    elif cod == 78:
        # Exactamente la avería del 21-sep. Se nombra aparte porque tiene
        # arreglo automático y porque confundirla con «el script falló» manda a
        # depurar código que está sano.
        info["veredicto"] = "no_arranca"
        info["detalle"] = (
            "launchd no pudo abrir su log y abortó el job antes de ejecutarlo "
            "(EX_CONFIG 78). Causa habitual: com.apple.macl en el archivo de log."
        )
    elif info.get("ultima_senal"):
        # Lo mató una señal. Con KeepAlive es lo normal tras un reinicio, así
        # que solo importa si además no está corriendo (que es el caso aquí).
        info["veredicto"] = "fallando"
        info["detalle"] = f"terminó por señal {info['ultima_senal']} y no volvió a arrancar"
    elif cod not in (None, 0):
        info["veredicto"] = "fallando"
        info["detalle"] = f"última salida con código {cod}"
    else:
        info["veredicto"] = "vivo"
        info["detalle"] = "última pasada terminó bien"

    if info["veredicto"] in ("vivo", "corriendo") and info["log_protegido"]:
        info["aviso"] = (
            "su log vive en una carpeta protegida por TCC; el día que macOS le "
            "cuelgue com.apple.macl, el agente dejará de arrancar en silencio"
        )
    return info


def estado() -> dict:
    agentes = [estado_de(l) for l in AGENTES if l not in RETIRADOS]
    rotos = [a for a in agentes if a["veredicto"] in ("no_arranca", "fallando", "descargado")]
    criticos = [a for a in rotos if a["critico"]]
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agentes": agentes,
        "sano": not rotos,
        "rotos": [a["label"] for a in rotos],
        "criticos_rotos": [a["label"] for a in criticos],
        "avisos": [
            {"label": a["label"], "nombre": a["nombre"], "aviso": a["aviso"]}
            for a in agentes
            if a.get("aviso")
        ],
    }


def reparar(solo: str | None = None) -> dict:
    """Le quita el `com.apple.macl` al log y vuelve a arrancar lo que no corre.

    Idempotente: sobre un agente sano no hace nada. Devuelve qué tocó, para que
    quede en el parte — una reparación silenciosa es otra forma de la misma
    avería.
    """
    hechos: list[dict] = []
    for a in estado()["agentes"]:
        if solo and a["label"] != solo:
            continue
        if a["veredicto"] in ("vivo", "corriendo"):
            continue

        acciones: list[str] = []
        log = Path(a["log"]) if a["log"] else None

        if log is not None and a["log_con_macl"]:
            r = subprocess.run(
                ["xattr", "-d", "com.apple.macl", str(log)],
                capture_output=True, text=True, timeout=_TIMEOUT,
            )
            acciones.append(
                "quitado com.apple.macl del log" if r.returncode == 0
                else f"no se pudo quitar com.apple.macl ({(r.stderr or '').strip()})"
            )

        if a["veredicto"] == "descargado":
            plist = HOME / "Library/LaunchAgents" / f"{a['label']}.plist"
            if plist.exists():
                rc, out = _launchctl("bootstrap", f"gui/{UID}", str(plist))
                acciones.append("recargado en launchd" if rc == 0
                                else f"no se pudo recargar ({out.strip()})")
            else:
                acciones.append("falta el plist; hay que reinstalarlo a mano")
        else:
            # `-k` mata y espera; sobre un job que NO está corriendo se queda
            # colgado (medido: >10 s sin devolver). Solo se usa cuando hay algo
            # que matar.
            args = ["kickstart"] + (["-k"] if a["pid"] else []) + [f"gui/{UID}/{a['label']}"]
            rc, out = _launchctl(*args, timeout=25)
            acciones.append("reiniciado" if rc == 0
                            else f"no se pudo reiniciar ({out.strip()})")

        hechos.append({"label": a["label"], "nombre": a["nombre"],
                       "venia_de": a["veredicto"], "acciones": acciones})

    return {"ts": datetime.now().isoformat(timespec="seconds"),
            "reparados": hechos, "despues": estado()}


# --------------------------- latido de la máquina --------------------------
# El tablero de 23-platzi-ventas deduce «sin dar señales» del último
# POST /api/prospeccion, así que una máquina sana con la cola vacía se ve
# idéntica a una apagada. La prueba de vida real la deja cada pasada en
# latido.json; aquí se lee para que el cockpit sí sepa distinguirlas.
LATIDO = (
    Path(__file__).resolve().parent.parent.parent
    / "modulo-prospeccion" / "data" / "latido.json"
)
MAX_EDAD_MIN = 15  # el cron corre cada 5 min: 15 = tres turnos perdidos


def maquina() -> dict:
    import json

    if not LATIDO.exists():
        return {"veredicto": "nunca", "detalle": "no hay latido.json",
                "edad_min": None, "ruta": str(LATIDO)}
    try:
        d = json.loads(LATIDO.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"veredicto": "ilegible", "detalle": str(e),
                "edad_min": None, "ruta": str(LATIDO)}

    try:
        edad = (datetime.now() - datetime.fromisoformat(d["ts"])).total_seconds() / 60
    except (KeyError, ValueError):
        return {"veredicto": "ilegible", "detalle": "latido sin ts válido",
                "edad_min": None, "ruta": str(LATIDO)}

    info = {
        "ts": d.get("ts"),
        "edad_min": round(edad, 1),
        "ok": bool(d.get("ok")),
        "estado_pasada": d.get("estado_pasada"),
        "error": d.get("error") or "",
        "saldo_apollo": d.get("saldo_apollo"),
        "ruta": str(LATIDO),
    }
    if edad > MAX_EDAD_MIN:
        horas = edad / 60
        info["veredicto"] = "mudo"
        info["detalle"] = (
            f"sin latir hace {horas:.1f} h" if horas >= 1
            else f"sin latir hace {edad:.0f} min"
        )
    elif not d.get("ok"):
        info["veredicto"] = "fallando"
        info["detalle"] = d.get("error") or "la última pasada terminó en error"
    else:
        info["veredicto"] = "viva"
        info["detalle"] = f"latió hace {edad:.0f} min"
    return info


def salud() -> dict:
    """Lo que la interfaz necesita para decidir si pinta la banda de alarma."""
    ag = estado()
    mq = maquina()
    problemas: list[str] = []
    for a in ag["agentes"]:
        if a["veredicto"] in ("no_arranca", "fallando", "descargado") and a["critico"]:
            problemas.append(f"{a['nombre']}: {a['detalle']}")
    if mq["veredicto"] in ("mudo", "nunca", "ilegible", "fallando"):
        problemas.append(f"Máquina de prospección: {mq['detalle']}")
    return {"ts": ag["ts"], "sano": not problemas, "problemas": problemas,
            "agentes": ag["agentes"], "avisos": ag["avisos"], "maquina": mq}
