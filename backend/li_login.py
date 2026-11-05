"""Entrada de sesión de los VP: un Chrome dedicado y persistente por asiento.

El VP hace UNA cosa —iniciar sesión en una ventana que le abrimos— y de ahí en
adelante el cockpit se encarga. Nada de pedirle cookies, ni su contraseña, ni
que repita el trámite cada semana.

## Por qué un Chrome propio y no cookies

Exportar cookies y meterlas en un navegador limpio no funciona: LinkedIn ve una
huella nueva y manda a un checkpoint de seguridad. Y el checkpoint le llega al
VP, no a nosotros, así que además de fallar queda quedando mal. Un Chrome con su
propio `--user-data-dir` resuelve las dos cosas: la sesión queda guardada en
disco como en cualquier navegador, sobrevive a reinicios y la huella es siempre
la misma.

Un perfil y un puerto por asiento. Si compartieran `--user-data-dir`, el segundo
VP entraría sobre la sesión del primero y tendríamos dos asientos que en realidad
son la misma cuenta — con un cupo contado dos veces.

## Lo que el cockpit nunca ve

La contraseña y el 2FA se escriben en la ventana de Chrome, que es un navegador
normal. El cockpit no los intercepta ni los guarda: lo único que queda en disco
es la sesión, en la carpeta del asiento, igual que si el VP hubiera entrado a
LinkedIn en su propio computador. Para sacarlo, se borra esa carpeta.

## Segundo plano: headless sobre el mismo perfil

Con la sesión iniciada, el Chrome del asiento corre `--headless=new` sobre el
mismo `--user-data-dir`: sin ventana, sin pestañas a la vista y sin robar el
foco. Solo `abrir(..., ventana=True)` —el botón de iniciar sesión— lo muestra,
y en cuanto `capturar()` confirma el login vuelve a segundo plano.

Se probó (25-sep-2026) lo que no sirve antes de llegar aquí:
- Ocultar la app (`NSRunningApplication.hide`): cada pestaña nueva de
  Playwright la vuelve a mostrar, incluso con `background: true`.
- Ventana fuera de pantalla: macOS deja una franja visible y cada `new_page`
  pone ese Chrome al frente — le quita el foco a quien esté escribiendo.

Headless comparte la sesión con la ventana en los dos sentidos (mismo llavero),
pero se anuncia distinto: por eso lleva el UA del Chrome instalado (sin él dice
«HeadlessChrome») y el tamaño real de la pantalla (sin él reporta 800×600 con
una ventana de 1280). Con eso Client Hints, UA y WebGL salen como en la ventana.

## Ciclo de vida

  abrir()    → lanza el Chrome del asiento: con ventana para iniciar sesión,
               headless para enviar
  fase()     → sondeo BARATO (HTTP a /json/list): ¿está en login o ya entró?
  capturar() → una sola vez, cuando ya entró: lee quién es, lo guarda y pasa
               el asiento a segundo plano
  asegurar() → antes de enviar: si el Chrome está cerrado lo levanta solo (en
               segundo plano) y confirma que la sesión sigue viva

`fase()` es lo que la UI consulta en bucle, por eso no usa Playwright: leer las
pestañas por HTTP cuesta milisegundos, mientras abrir una página cuesta segundos.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import store
from config import DATA_DIR, PYTHON

CHROME = os.environ.get(
    "PLATZI_CHROME",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

PERFILES_DIR = DATA_DIR / "chrome"
CAPTURA_PY = Path(__file__).resolve().parent / "li_perfil.py"
COOKIES_PY = Path(__file__).resolve().parent / "li_cookies.py"

# Un puerto por asiento, reclamado la primera vez y guardado en kv para que el
# endpoint no cambie entre reinicios.
#
# Ojo con el 9334: es el que usa el Chrome del OPERADOR (`PLATZI_LINKEDIN_CDP`).
# Si un asiento arrancara ahí, el sondeo encontraría ese Chrome, lo daría por
# bueno y el asiento enviaría desde la cuenta del operador firmando como el VP.
# Por eso el rango empieza más arriba y, antes de usarlo, se comprueba que el
# que contesta sea nuestro y no el de alguien más.
PUERTO_BASE = 9411
PUERTO_TOPE = 9440

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"

# «Dentro» se decide por lista blanca, no descartando pantallas de login.
# Deslogueado, LinkedIn manda /feed/ a su portada (linkedin.com/), que no
# contiene «login» ni «authwall»: con una lista negra eso pasaba por sesión
# iniciada y el asiento se daba por listo sin que nadie hubiera entrado.
_DENTRO = ("/feed", "/in/", "/mynetwork", "/messaging", "/notifications",
           "/search/results", "/company/", "/sales/")
# LinkedIn manda la verificación por dos caminos distintos: el clásico
# /checkpoint/… y el nuevo /flagship-web/login/?challengeId=…, que por la ruta
# parece un login normal. Sin el segundo, un VP esperando un código se ve igual
# que un VP que no ha hecho nada, y el operador no sabe que hay algo que atender.
_VERIFICA = ("/checkpoint", "challengeid=", "/challenge")
_FUERA = ("/login", "/authwall", "/uas/login", "signup", "/start/")


# ----------------------------- rutas y puertos ----------------------------
def puerto(seat_id: str) -> int:
    """Puerto ya reclamado por el asiento, o el que le tocaría por orden."""
    guardado = store.kv_get(f"seat:{seat_id}:puerto", "").strip()
    if guardado.isdigit():
        return int(guardado)
    try:
        return PUERTO_BASE + list(_ids()).index(seat_id)
    except ValueError:
        return PUERTO_BASE


def _ids() -> tuple[str, ...]:
    # Import local: `seats` importa este módulo, así que arriba sería un ciclo.
    from seats import seat_ids
    return seat_ids()


def _pid(seat_id: str) -> Optional[int]:
    raw = store.kv_get(f"seat:{seat_id}:chrome_pid", "").strip()
    return int(raw) if raw.isdigit() else None


def _pid_vivo(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def nuestro(seat_id: str) -> bool:
    """¿El Chrome que contesta en ese puerto es el que lanzamos para ESTE asiento?

    Sin esta pregunta, encontrar «algo» escuchando en el puerto es suficiente
    para creer que el asiento está listo — y ese algo puede ser el Chrome del
    operador, con otra cuenta de LinkedIn.
    """
    if _cdp(seat_id, "/json/version") is None:
        return False
    pid = _pid(seat_id)
    if pid is None:
        # Endpoint puesto a mano (flujo manual): no hay pid nuestro que validar,
        # se confía en quien lo configuró.
        return True
    return _pid_vivo(pid)


def _escucha(p: int) -> bool:
    """¿Hay algo contestando CDP en ese puerto? (sea nuestro o no)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/json/version", timeout=1.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def puerto_libre(seat_id: str) -> int:
    """Puerto para un asiento que todavía no tiene ninguno. 0 si no queda.

    Mira dos cosas y no una: que nadie esté escuchando AHORA y que ningún otro
    asiento lo tenga apuntado. Lo segundo es lo que se escapaba — un Chrome
    cerrado deja su puerto mudo, así que un asiento nuevo se lo llevaba y, al
    reabrir el viejo, dos asientos apuntaban al mismo navegador: uno de los dos
    acaba firmando con la cuenta del otro.
    """
    apuntados = {store.kv_get(f"seat:{otro}:puerto", "").strip()
                 for otro in _ids() if otro != seat_id}
    for p in range(PUERTO_BASE, PUERTO_TOPE):
        if str(p) in apuntados or _escucha(p):
            continue
        return p
    return 0


def _ocupado_por_otro(p: int, seat_id: str) -> bool:
    """¿Hay algo en ese puerto que no sea el Chrome de este asiento?"""
    if not _escucha(p):
        return False            # nadie contesta: libre
    return not (_pid_vivo(_pid(seat_id)) and puerto(seat_id) == p)


def reclamar_puerto(seat_id: str) -> int:
    """Fija un puerto libre para el asiento y lo guarda."""
    inicio = puerto(seat_id)
    for p in list(range(inicio, PUERTO_TOPE)) + list(range(PUERTO_BASE, inicio)):
        if not _ocupado_por_otro(p, seat_id):
            store.kv_set(f"seat:{seat_id}:puerto", str(p))
            return p
    return inicio


def perfil_dir(seat_id: str) -> Path:
    return PERFILES_DIR / seat_id


def endpoint(seat_id: str) -> str:
    return f"http://127.0.0.1:{puerto(seat_id)}"


def tiene_perfil(seat_id: str) -> bool:
    """¿Ya hay un Chrome con historia para este asiento?

    «Local State» y «Default» son lo primero que Chrome escribe: una carpeta
    creada por nosotros y todavía vacía no cuenta como perfil, o el cockpit
    creería que hay sesión donde nunca hubo login.
    """
    d = perfil_dir(seat_id)
    return d.exists() and ((d / "Local State").exists() or (d / "Default").exists())


def login_guardado(seat_id: str) -> str:
    """Fecha en que se verificó por última vez que la sesión estaba viva."""
    return store.kv_get(f"seat:{seat_id}:login_ts", "").strip()


# ----------------------------- sondeo CDP ---------------------------------
def _cdp(seat_id: str, ruta: str, timeout: float = 1.5):
    url = endpoint(seat_id).rstrip("/") + ruta
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def nueva_pestana(seat_id: str, url: str) -> bool:
    """Abre una pestaña en el Chrome del asiento.

    `/json/new` dejó de aceptar GET en Chrome 111: hay que mandar PUT o
    responde 405 y la pestaña nunca aparece.
    """
    destino = endpoint(seat_id).rstrip("/") + "/json/new?" + url
    req = urllib.request.Request(destino, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=4.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


# Pestañas que se pueden reciclar sin romper nada: el feed que abrimos nosotros
# y las que quedan en blanco. Una pestaña en /in/, /checkpoint/ o /messaging
# puede ser el runner mandando una invitación o el VP a mitad de una
# verificación — esas no se tocan.
_RECICLABLE = ("about:blank", "chrome://new-tab-page", "chrome://newtab",
               "linkedin.com/feed")


def _reciclable(url: str) -> bool:
    u = (url or "").lower()
    return not u or any(x in u for x in _RECICLABLE)


def _paginas(seat_id: str) -> list[dict]:
    """Pestañas CON su id de CDP. `pestanas()` devuelve solo lo que ve la UI."""
    return [t for t in (_cdp(seat_id, "/json/list") or [])
            if t.get("type") == "page"]


def cerrar_pestana(seat_id: str, tab_id: str) -> bool:
    """`/json/close/<id>` contesta texto plano, no JSON: `_cdp` no sirve."""
    destino = endpoint(seat_id).rstrip("/") + f"/json/close/{tab_id}"
    try:
        with urllib.request.urlopen(destino, timeout=3.0):
            return True
    except (urllib.error.URLError, OSError):
        return False


def podar(seat_id: str, dejar: int = 1) -> int:
    """Cierra las pestañas reciclables que sobran. Devuelve cuántas cerró.

    Cada preparación de un asiento abría una pestaña y ninguna se cerraba
    nunca: con un tick cada 15 minutos, el Chrome del VP acababa con el mismo
    feed repetido decenas de veces, y cada copia es una pestaña de LinkedIn
    recargándose sola en segundo plano.
    """
    sobran = [t for t in _paginas(seat_id) if _reciclable(t.get("url", ""))]
    cerradas = 0
    for t in sobran[:max(0, len(sobran) - max(0, dejar))]:
        if t.get("id") and cerrar_pestana(seat_id, t["id"]):
            cerradas += 1
    return cerradas


def una_pestana(seat_id: str, url: str) -> bool:
    """Deja el asiento en `url` con UNA sola pestaña reciclable.

    Se abre primero y se cierran después las que YA estaban, anotadas por id:
    podar por posición dependería del orden en que Chrome liste los targets, y
    cerrar antes de abrir dejaría la ventana sin ninguna pestaña un instante
    —que en macOS es lo mismo que cerrar la ventana—.
    """
    antes = [t for t in _paginas(seat_id) if _reciclable(t.get("url", ""))]
    if not nueva_pestana(seat_id, url):
        return False
    for t in antes:
        if t.get("id"):
            cerrar_pestana(seat_id, t["id"])
    return True


def _linea_del_perfil(seat_id: str) -> str:
    """Línea de `ps` del proceso principal de Chrome con el perfil del asiento."""
    marca = f"--user-data-dir={perfil_dir(seat_id)}"
    try:
        salida = subprocess.run(["/bin/ps", "-Ao", "pid=,command="],
                                capture_output=True, text=True,
                                timeout=10).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    for linea in salida.splitlines():
        linea = linea.strip()
        if marca not in linea or "Helper" in linea:
            continue
        # Que el perfil termine ahí: si no, `vp-1` casaría con `vp-10`.
        resto = linea.split(marca, 1)[1]
        if resto and not resto.startswith(" "):
            continue
        return linea
    return ""


def en_segundo_plano(seat_id: str) -> bool:
    """¿El Chrome del asiento corre headless? Cuesta un `ps`: no va en bucles."""
    return "--headless" in _linea_del_perfil(seat_id)


def _chrome_del_perfil(seat_id: str) -> tuple[int, int]:
    """(pid, puerto) del Chrome que YA tiene abierto el perfil del asiento.

    Es la pregunta que faltaba. `reclamar_puerto()` mira quién escucha en cada
    puerto, nunca quién tiene abierto el perfil — y Chrome no admite dos
    procesos sobre el mismo `user-data-dir`: el segundo muere al instante y su
    puerto no contesta jamás. Así que en cuanto el apunte del pid se pierde (un
    reinicio del backend, un Chrome relanzado a mano), el asiento esquiva su
    propio navegador vivo, reclama un puerto nuevo, lanza un Chrome que muere y
    se queda sin poder enviar — con la sesión intacta a dos dedos.

    El 18-sep-2026 los cinco asientos estaban así: tres apuntando al 9415 y uno
    al 9417, con sus Chrome vivos en 9411-9414 y 9416.
    """
    linea = _linea_del_perfil(seat_id)
    m = re.search(r"--remote-debugging-port=(\d+)", linea)
    pid = linea.split(None, 1)[0] if linea else ""
    if m and pid.isdigit():
        return int(pid), int(m.group(1))
    return 0, 0


def adoptar(seat_id: str) -> dict:
    """Apunta el asiento al Chrome que ya tiene su perfil abierto, si lo hay.

    Adoptar y no relanzar: el navegador que está en pie lleva dentro la sesión
    de LinkedIn que el VP inició a mano, y esa no se recupera matándolo.
    """
    pid, p = _chrome_del_perfil(seat_id)
    if not (pid and p):
        return {"adoptado": False}
    store.kv_set(f"seat:{seat_id}:puerto", str(p))
    store.kv_set(f"seat:{seat_id}:chrome_pid", str(pid))
    return {"adoptado": True, "pid": pid, "puerto": p}


def chrome_vivo(seat_id: str) -> bool:
    """Vivo Y nuestro. Un puerto que contesta no basta (ver `nuestro`).

    Barato a propósito: `fase()` lo llama en bucle desde la UI, así que aquí no
    se pregunta al sistema. La adopción (que cuesta un `ps`) va donde se decide
    de verdad —`abrir()` y `asegurar()`—, una vez por job.
    """
    return nuestro(seat_id)


def vivo_o_adoptado(seat_id: str) -> bool:
    """`chrome_vivo`, y si no, un último intento de adoptar el suyo."""
    return nuestro(seat_id) or (
        bool(adoptar(seat_id).get("adoptado")) and nuestro(seat_id))


def pestanas(seat_id: str) -> list[dict]:
    data = _cdp(seat_id, "/json/list") or []
    return [{"url": t.get("url", ""), "titulo": t.get("title", "")}
            for t in data if t.get("type") == "page"]


def fase(seat_id: str) -> dict:
    """En qué punto va el VP. Barato: se puede llamar en bucle desde la UI.

    · cerrado  → no hay Chrome escuchando en su puerto
    · login    → la ventana está en la pantalla de acceso
    · verifica → LinkedIn pidió código/2FA (checkpoint)
    · dentro   → hay una pestaña de LinkedIn fuera de login
    · otro     → el Chrome está abierto pero no en LinkedIn
    """
    if not chrome_vivo(seat_id):
        return {"fase": "cerrado", "detalle": "la ventana no está abierta"}
    urls = [t["url"] for t in pestanas(seat_id)]
    li = [u for u in urls if "linkedin.com" in u]
    if not li:
        return {"fase": "otro", "detalle": "la ventana está abierta pero no en LinkedIn",
                "urls": urls}
    if any(x in u.lower() for u in li for x in _VERIFICA):
        return {"fase": "verifica",
                "detalle": "LinkedIn pidió verificación: que la complete en la ventana",
                "urls": li}
    if any(x in u for u in li for x in _DENTRO):
        return {"fase": "dentro", "detalle": "sesión iniciada", "urls": li}
    if any(x in u for u in li for x in _FUERA) or any(
            u.rstrip("/").endswith("linkedin.com") for u in li):
        return {"fase": "login", "detalle": "esperando que inicie sesión", "urls": li}
    return {"fase": "otro", "detalle": "en LinkedIn pero no se ve la sesión", "urls": li}


# ----------------------------- abrir / cerrar -----------------------------
def _user_agent() -> str:
    """El UA que manda el Chrome instalado con ventana. "" si no se puede leer."""
    try:
        with open(Path(CHROME).parent.parent / "Info.plist", "rb") as f:
            version = plistlib.load(f)["CFBundleShortVersionString"]
    except (OSError, KeyError, plistlib.InvalidFileException):
        return ""
    mayor = version.split(".")[0]
    return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{mayor}.0.0.0 Safari/537.36")


def _pantalla() -> str:
    """Tamaño de la pantalla principal («1710x1107»), para que headless lo reporte."""
    try:
        out = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e",
             "ObjC.import('AppKit'); var f=$.NSScreen.mainScreen.frame; "
             "Math.round(f.size.width)+'x'+Math.round(f.size.height)"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out if re.fullmatch(r"\d+x\d+", out) else "1512x982"


def abrir(seat_id: str, url: str = LOGIN_URL, espera: float = 15.0,
          ventana: bool = False) -> dict:
    """Lanza (o reusa) el Chrome del asiento y lo deja en `url`.

    `ventana=True` es para que una persona escriba en él (iniciar sesión,
    pasar una verificación). Sin eso corre headless: ver la cabecera.

    No se toca el Chrome del operador: este es un proceso aparte, con su propio
    perfil en disco y su propio puerto. Cerrar el del operador no lo afecta y
    viceversa.
    """
    if not Path(CHROME).exists():
        return {"ok": False, "error": f"no encuentro Chrome en {CHROME}. "
                                      "Configúralo con PLATZI_CHROME."}
    if vivo_o_adoptado(seat_id):
        if ventana and en_segundo_plano(seat_id):
            # Headless no tiene dónde escribir la clave: se cierra y se vuelve a
            # lanzar con ventana sobre el mismo perfil, con la sesión intacta.
            cerrar(seat_id)
        else:
            # Ya está abierto: se le manda una pestaña nueva por CDP en vez de
            # lanzar un segundo Chrome sobre el mismo perfil (Chrome lo rechaza
            # y el VP ve un error raro).
            una_pestana(seat_id, url)
            return {"ok": True, "ya_estaba": True, "endpoint": endpoint(seat_id),
                    "puerto": puerto(seat_id), **fase(seat_id)}

    # Puerto libre y propio ANTES de lanzar: si el que le tocaba lo tiene otro
    # Chrome (el del operador, por ejemplo), se corre al siguiente en vez de
    # quedar hablando con una sesión ajena.
    store.kv_set(f"seat:{seat_id}:chrome_pid", "")
    p_libre = reclamar_puerto(seat_id)

    d = perfil_dir(seat_id)
    d.mkdir(parents=True, exist_ok=True)
    # Dentro vive la sesión del VP: solo el dueño del cockpit puede mirar.
    for carpeta in (d, d.parent):
        try:
            carpeta.chmod(0o700)
        except OSError:
            pass
    cmd = [
        CHROME,
        f"--remote-debugging-port={p_libre}",
        f"--user-data-dir={d}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,900",
    ]
    ua = _user_agent()
    if ventana or not ua:
        # Sin UA propio, headless se anunciaría como «HeadlessChrome»: mejor
        # una ventana a la vista que un asiento marcado ante LinkedIn.
        # Ventana propia y reconocible: tres Chrome idénticos en el dock es la
        # forma más fácil de que un VP escriba en la ventana de otro.
        cmd.append(f"--window-name={seat_id}")
    else:
        cmd += ["--headless=new", f"--user-agent={ua}",
                f"--screen-info={{{_pantalla()}}}"]
    cmd.append(url)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    store.kv_set(f"seat:{seat_id}:chrome_pid", str(proc.pid))

    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        if proc.poll() is not None:
            # Murió al arrancar, casi siempre porque otro Chrome tiene el
            # perfil. Sin mirar poll(), su pid zombi pasa por vivo y el puerto
            # que contesta es el del otro.
            store.kv_set(f"seat:{seat_id}:chrome_pid", "")
            return {"ok": False, "error": "Chrome se cerró al arrancar: otro proceso "
                                          "tiene abierto el perfil de este asiento"}
        if chrome_vivo(seat_id):
            return {"ok": True, "ya_estaba": False, "endpoint": endpoint(seat_id),
                    "puerto": puerto(seat_id), **fase(seat_id)}
        time.sleep(0.4)
    return {"ok": False, "error": "Chrome no respondió en el puerto de depuración "
                                  f"({puerto(seat_id)}) — revisa si quedó otro Chrome "
                                  "usando ese perfil"}


def cerrar(seat_id: str, espera: float = 15.0) -> dict:
    """Cierra el Chrome del asiento. La sesión NO se pierde: vive en el perfil.

    Espera a que Chrome suelte el perfil (`SingletonLock`): relanzar antes haría
    que el nuevo proceso muriera al instante por perfil ocupado.

    Un Chrome que arrancó antes de una actualización se relanza solo al
    cerrarse (con los mismos argumentos más `--no-startup-window`) para aplicar
    la versión nueva, y ese relanzado se queda con el perfil. Por eso, soltado
    el perfil, se vuelve a mirar y se cierra también el relanzado.
    """
    cerrados = 0
    for _ in range(3):
        pid = _chrome_del_perfil(seat_id)[0] or _pid(seat_id)
        if not pid or not _pid_vivo(pid):
            break
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
        cerrados += 1
        cerrojo = perfil_dir(seat_id) / "SingletonLock"
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            try:
                # Si es hijo nuestro, recogerlo: un zombi sigue «vivo» para kill(0).
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            if not os.path.lexists(cerrojo) and not _pid_vivo(pid):
                break
            time.sleep(0.3)
        store.kv_set(f"seat:{seat_id}:chrome_pid", "")
        time.sleep(2.0)
        if not _chrome_del_perfil(seat_id)[0]:
            break
    store.kv_set(f"seat:{seat_id}:chrome_pid", "")
    return {"ok": True, "cerrado": bool(cerrados)}


def a_segundo_plano(seat_id: str) -> dict:
    """Cambia la ventana del asiento por su Chrome headless, con la misma sesión.

    Solo con la sesión ya dentro: una ventana en login o en verificación es un
    VP escribiendo, y cerrársela le borraría lo que lleva.
    """
    if not vivo_o_adoptado(seat_id):
        return {"ok": False, "error": "la ventana no está abierta"}
    if en_segundo_plano(seat_id):
        return {"ok": True, "ya_estaba": True}
    f = fase(seat_id)
    if f["fase"] != "dentro":
        return {"ok": False, "error": f"no se pasa a segundo plano: {f['detalle']}"}
    cerrar(seat_id)
    res = abrir(seat_id, FEED_URL, espera=25.0)
    if not res.get("ok"):
        return res
    if not en_segundo_plano(seat_id):
        return {"ok": False, "error": "el Chrome que quedó con el perfil no es el de segundo plano"}
    f = _esperar_fase(seat_id, {"dentro", "login", "verifica"}, 20)
    if f["fase"] != "dentro":
        return {"ok": False, "error": f"en segundo plano no se ve la sesión: {f['detalle']}"}
    return {"ok": True, "ya_estaba": False}


def olvidar(seat_id: str) -> dict:
    """Borra la sesión del asiento: el VP se sale del programa.

    Se borra la carpeta entera del perfil, que es donde vive lo único suyo que
    tocamos. Después de esto el cockpit no puede entrar a su LinkedIn ni con el
    Chrome abierto.
    """
    cerrar(seat_id)
    d = perfil_dir(seat_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    for campo in ("login_ts", "login_ok", "chrome_pid", "perfil_url"):
        store.kv_set(f"seat:{seat_id}:{campo}", "")
    return {"ok": True, "borrado": str(d)}


# ----------------------------- capturar identidad ------------------------
def capturar(seat_id: str, timeout: float = 90.0) -> dict:
    """Lee quién inició sesión: nombre, titular y URL de su perfil.

    Es la parte cara: abre una página, así que se llama UNA vez —cuando
    `fase()` ya dijo "dentro"— y no en el bucle de la UI.
    """
    if not chrome_vivo(seat_id):
        return {"ok": False, "error": "la ventana no está abierta"}
    try:
        proc = subprocess.run(
            [PYTHON, str(CAPTURA_PY), "--cdp", endpoint(seat_id)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(CAPTURA_PY.parent))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "LinkedIn no respondió a tiempo; intenta de nuevo"}
    # El motivo útil («esa ventana no tiene sesión iniciada») viaja por stdout
    # aunque el script salga con código 1. Mirar stderr primero convertía cada
    # fallo explicado en un «no se pudo leer el perfil» genérico.
    datos = {}
    try:
        datos = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        datos = {}
    if not datos.get("ok"):
        if datos.get("error"):
            return {"ok": False, "error": datos["error"]}
        ultima = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        return {"ok": False, "error": ultima[-1] if ultima else "no se pudo leer el perfil"}

    store.kv_set(f"seat:{seat_id}:login_ok", "1")
    store.kv_set(f"seat:{seat_id}:login_ts", store.now_iso())
    store.kv_set(f"seat:{seat_id}:perfil_url", datos.get("url", ""))
    # Respaldo inmediato: el mejor momento para copiar una sesión es justo
    # cuando se acaba de crear, y así nadie tiene que acordarse de hacerlo.
    resp = respaldar_cookies(seat_id)
    fondo = a_segundo_plano(seat_id)
    return {"ok": True, **datos, "endpoint": endpoint(seat_id), "respaldo": resp,
            "segundo_plano": fondo}


# ----------------------------- respaldo ----------------------------------
def respaldar_cookies(seat_id: str, timeout: float = 60.0) -> dict:
    """Guarda las cookies del asiento como paracaídas del perfil de Chrome.

    Se llama solo después de cada login verificado, así que el respaldo nace
    junto a la sesión y nadie tiene que acordarse de hacerlo. No reemplaza al
    perfil: ver la cabecera de `li_cookies.py`.
    """
    from seats import cookies_path        # import local: seats importa este módulo
    if not chrome_vivo(seat_id):
        return {"ok": False, "error": "la ventana no está abierta"}
    destino = cookies_path(seat_id)
    try:
        proc = subprocess.run(
            [PYTHON, str(COOKIES_PY), "--cdp", endpoint(seat_id), "--out", str(destino)],
            capture_output=True, text=True, timeout=timeout, cwd=str(COOKIES_PY.parent))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "no respondió a tiempo"}
    try:
        datos = json.loads((proc.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        datos = {}
    if not datos.get("ok"):
        return {"ok": False, "error": datos.get("error") or "no se pudo respaldar la sesión"}
    store.kv_set(f"seat:{seat_id}:cookies_ts", store.now_iso())
    store.kv_set(f"seat:{seat_id}:li_at_vence", datos.get("li_at_vence", ""))
    return datos


def respaldo(seat_id: str) -> dict:
    """Qué hay guardado: cuándo se respaldó y hasta cuándo sirve la sesión."""
    from seats import cookies_path
    ruta = cookies_path(seat_id)
    return {
        "existe": ruta.exists(),
        "archivo": str(ruta),
        "fecha": store.kv_get(f"seat:{seat_id}:cookies_ts", ""),
        "li_at_vence": store.kv_get(f"seat:{seat_id}:li_at_vence", ""),
    }


# ----------------------------- antes de enviar ---------------------------
def asegurar(seat_id: str, espera: float = 25.0) -> dict:
    """Deja el Chrome del asiento listo para enviar, o explica por qué no.

    Esto es lo que hace que el VP entre UNA sola vez: si su ventana está
    cerrada, se levanta sola con la sesión que ya quedó en disco. Si la sesión
    caducó de verdad, se dice con nombre propio en vez de dejar que el motor
    falle con un `rate_limited` que no significa eso.
    """
    if vivo_o_adoptado(seat_id):
        f = fase(seat_id)
        if f["fase"] == "dentro":
            # Una ventana que quedó abierta (un login de antes, un Chrome
            # lanzado a mano) pasa aquí a segundo plano, una sola vez.
            if not en_segundo_plano(seat_id):
                fondo = a_segundo_plano(seat_id)
                if not fondo.get("ok"):
                    return {"ok": False, "error": fondo.get("error", "no pasó a segundo plano")}
                return {"ok": True, "levantado": True}
            # El camino normal, y por eso el sitio donde hay que barrer: esto
            # corre antes de CADA job. Se deja una sola pestaña reciclable; las
            # de /in/ (el runner enviando) no entran en la poda.
            podar(seat_id, dejar=1)
            return {"ok": True, "levantado": False}
        if f["fase"] == "otro":
            # Ventana nuestra pero sin LinkedIn a la vista (pestaña en blanco,
            # recién abierta). Se le pide el feed y se vuelve a mirar: dar esto
            # por bueno sería enviar a ciegas sin saber si hay sesión.
            una_pestana(seat_id, FEED_URL)
            f = _esperar_fase(seat_id, {"dentro", "login", "verifica"}, 20)
            if f["fase"] == "dentro":
                return {"ok": True, "levantado": False}
        return {"ok": False, "error": f"hay que volver a iniciar sesión: {f['detalle']}"}

    if not (tiene_perfil(seat_id) and login_guardado(seat_id)):
        # Que exista la carpeta no prueba nada: se crea al abrir la ventana,
        # antes de que el VP escriba su clave. La prueba es el login verificado.
        return {"ok": False, "error": "este perfil nunca inició sesión en el cockpit"}

    res = abrir(seat_id, FEED_URL, espera=espera)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "no se pudo abrir la ventana")}

    # Darle un momento a que cargue el feed antes de juzgar la sesión: recién
    # lanzado, la primera pestaña puede seguir en about:blank.
    f = _esperar_fase(seat_id, {"dentro", "login", "verifica"}, 20)
    if f["fase"] == "dentro":
        store.kv_set(f"seat:{seat_id}:login_ts", store.now_iso())
        return {"ok": True, "levantado": True}
    if f["fase"] in ("login", "verifica"):
        return {"ok": False, "error": f"la sesión caducó: {f['detalle']}"}
    return {"ok": False, "error": "el Chrome abrió pero LinkedIn no cargó a tiempo"}


def _esperar_fase(seat_id: str, fases: set[str], segundos: float) -> dict:
    limite = time.monotonic() + segundos
    f = fase(seat_id)
    while time.monotonic() < limite:
        if f["fase"] in fases:
            return f
        time.sleep(0.6)
        f = fase(seat_id)
    return f


def estado(seat_id: str) -> dict:
    """Todo lo que la UI necesita mostrar de un asiento, sin abrir páginas."""
    f = fase(seat_id)
    return {
        "seat_id": seat_id,
        "puerto": puerto(seat_id),
        "endpoint": endpoint(seat_id),
        "perfil_en_disco": tiene_perfil(seat_id),
        "login_ts": login_guardado(seat_id),
        "perfil_url": store.kv_get(f"seat:{seat_id}:perfil_url", ""),
        "respaldo": respaldo(seat_id),
        "chrome_abierto": f["fase"] != "cerrado",
        **f,
    }
