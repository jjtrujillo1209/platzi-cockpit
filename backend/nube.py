"""El Mac frente a platzi-cockpit-nube.

La nube atiende la cola de Platzi Ventas aunque el Mac duerma y deja los
decisores en una bandeja. Este módulo es la otra mitad:

  · `sincronizar()` — trae la bandeja, la mete a la base con
    `contacts.add_contacts` (mismo dedup y mismo sello `maquina-ventas` que
    cuando la máquina corría aquí), confirma lo entregado, dispara la vía rápida
    y deja un latido. Lo llama un bucle de 60 s del cockpit y el tick del
    scheduler; al despertar el Mac, la primera vuelta vacía lo acumulado.

  · `LibroNube` — el libro de créditos de Apollo pasa a ser la fila de Neon, que
    es la misma de la que gasta la nube. Sin red, cae al JSON local y apunta lo
    gastado para devolverlo en la siguiente sincronización.

Se enciende solo si hay NUBE_TOKEN en PLATZI/.env (lo deja ahí
`cockpit-nube/cargar_secretos.py`). Sin él, todo sigue siendo local.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import requests

_RAIZ = str(Path(__file__).resolve().parents[2])
if _RAIZ not in sys.path:
    sys.path.append(_RAIZ)

from platzi_core import apollo_budget, entorno  # noqa: E402

URL_DEFECTO = "https://platzi-cockpit-nube.vercel.app"
TIMEOUT = (5, 15)
PENDIENTE = Path(__file__).resolve().parent / "apollo_budget_pendiente.json"

_LOCK = threading.Lock()          # una sincronización a la vez
_LOCK_PEND = threading.Lock()     # el archivo de lo gastado sin red
_ultimo: dict = {}


def _cfg() -> tuple[str, str]:
    env = entorno.leer_cockpit()
    return ((env.get("NUBE_URL") or URL_DEFECTO).rstrip("/"),
            (env.get("NUBE_TOKEN") or "").strip())


def configurada() -> bool:
    return bool(_cfg()[1])


def _pedir(metodo: str, ruta: str, **kw) -> dict:
    url, token = _cfg()
    r = requests.request(metodo, url + ruta, headers={"X-Nube-Token": token},
                         timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


# ─────────────────────────── libro de créditos ──────────────────────────────
def _pendiente() -> dict:
    try:
        return json.loads(PENDIENTE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"descontar": 0, "fijar": None}


def _guardar_pendiente(d: dict) -> None:
    if not d.get("descontar") and d.get("fijar") is None:
        PENDIENTE.unlink(missing_ok=True)
    else:
        PENDIENTE.write_text(json.dumps(d), encoding="utf-8")


class LibroNube:
    """Neon manda; el JSON local es espejo y respaldo."""

    def __init__(self):
        self.local = apollo_budget.LibroLocal()

    def _espejo(self, d: dict) -> dict:
        self.local._guardar({"budget": d["budget"], "used": d["used"],
                             "updated_at": d.get("updated_at", "")})
        return {**d, "origen": "nube"}

    def cargar(self) -> dict:
        try:
            d = _pedir("GET", "/api/presupuesto")
            if not d.get("budget") and not d.get("updated_at"):
                # Libro vacío: primera vez. Se siembra con el saldo del Mac.
                l = self.local.cargar()
                d = _pedir("POST", "/api/presupuesto/sembrar", json=l)
            return self._espejo(d)
        except (requests.RequestException, ValueError, KeyError):
            return {**self.local.cargar(), "origen": "local"}

    def fijar(self, value: int) -> dict:
        try:
            return self._espejo(_pedir("POST", "/api/presupuesto/fijar",
                                       json={"budget": int(value)}))
        except (requests.RequestException, ValueError, KeyError):
            with _LOCK_PEND:
                _guardar_pendiente({"descontar": 0, "fijar": int(value)})
            return {**self.local.fijar(value), "origen": "local"}

    def descontar(self, n: int) -> dict:
        try:
            return self._espejo(_pedir("POST", "/api/presupuesto/descontar", json={"n": int(n)}))
        except (requests.RequestException, ValueError, KeyError):
            with _LOCK_PEND:
                p = _pendiente()
                p["descontar"] = int(p.get("descontar") or 0) + int(n)
                _guardar_pendiente(p)
            return {**self.local.descontar(n), "origen": "local"}


def _devolver_pendiente() -> dict:
    """Lo que se gastó sin red vuelve al libro de la nube, en orden: primero un
    saldo fijado a mano, después los descuentos."""
    with _LOCK_PEND:
        p = _pendiente()
        if not p.get("descontar") and p.get("fijar") is None:
            return {}
        hecho = {}
        if p.get("fijar") is not None:
            _pedir("POST", "/api/presupuesto/fijar", json={"budget": p["fijar"]})
            hecho["fijado"], p["fijar"] = p["fijar"], None
            _guardar_pendiente(p)
        if p.get("descontar"):
            _pedir("POST", "/api/presupuesto/descontar", json={"n": p["descontar"]})
            hecho["descontado"], p["descontar"] = p["descontar"], 0
            _guardar_pendiente(p)
        return hecho


def enchufar_libro() -> bool:
    """Lo llama el alias `cockpit/backend/apollo_budget.py` al importarse: todo
    proceso del Mac que gaste Apollo (cockpit, máquina, scripts) usa el mismo libro."""
    if not configurada() or isinstance(apollo_budget.libro(), LibroNube):
        return False
    apollo_budget.usar(LibroNube())
    return True


# ─────────────────────────────── bandeja ────────────────────────────────────
def sincronizar(limite: int = 200) -> dict:
    """Una vuelta. No lanza: lo que falle queda en el resultado y en el latido."""
    if not configurada():
        return {"saltado": "sin NUBE_TOKEN en PLATZI/.env"}
    if not _LOCK.acquire(blocking=False):
        return {"saltado": "ya hay una sincronización corriendo"}
    out = {"recibidos": 0, "agregados": 0, "saltados": 0, "confirmados": 0,
           "via_rapida": "", "error": ""}
    try:
        filas = _pedir("GET", "/api/bandeja", params={"limite": limite}).get("pendientes") or []
        out["recibidos"] = len(filas)
        if filas:
            import contacts
            r = contacts.add_contacts([f["payload"] for f in filas])
            out["agregados"] = r.get("added", 0)
            out["saltados"] = len(r.get("skipped") or [])
            c = _pedir("POST", "/api/bandeja/confirmar",
                       json={"ids": [f["id"] for f in filas],
                             "resultado": {"agregados": out["agregados"],
                                           "saltados": out["saltados"]}})
            out["confirmados"] = c.get("confirmados", 0)
            if out["agregados"]:
                import conexiones
                v = conexiones.avisar_via_rapida()
                out["via_rapida"] = "lanzada" if v.get("lanzada") else v.get("motivo", "")
    except Exception as e:
        out["error"] = f"{e.__class__.__name__}: {e}"[:300]
    finally:
        _LOCK.release()
    try:
        out["presupuesto"] = _devolver_pendiente()
    except Exception as e:
        out["error"] = out["error"] or f"presupuesto: {e.__class__.__name__}: {e}"[:300]
    try:
        _pedir("POST", "/api/latido", json={k: v for k, v in out.items() if k != "presupuesto"})
    except Exception as e:
        out["error"] = out["error"] or f"latido: {e.__class__.__name__}: {e}"[:300]
    _ultimo.clear()
    _ultimo.update(out)
    return out


def ultimo() -> dict:
    return dict(_ultimo)
