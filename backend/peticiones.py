"""Bandeja de peticiones de prospección.

Alguien escribe una empresa desde el cockpit y queda pendiente; la máquina de
prospección (`modulo-prospeccion/backend/maquina.py`) la recoge en su siguiente
vuelta —la misma que trabaja la cola de Platzi Ventas— y la deja resuelta con
sus 2 decisores, ya dados de alta en la base.

Por qué una tabla y no una llamada directa: pedir es instantáneo, resolver
tarda (Apollo cobra por reveal y hay que cruzar HubSpot). Dejando la petición
escrita, quien pide no espera, la máquina no depende de que nadie tenga el
navegador abierto, y queda el registro de quién pidió qué y qué salió.

Estados
    pendiente       recién pedida; nadie la ha tocado
    en_proceso      la máquina la tomó (evita que dos vueltas la trabajen a la vez)
    resuelta        salieron decisores y están en la base
    frenada         la empresa ya la trabaja alguien más (cliente o deal abierto)
    sin_contactos   Apollo no tiene decisores del ICP en esa empresa
    error           falló el intento; el detalle va en `nota`
    cancelada       la canceló quien la pidió
"""
from __future__ import annotations

import json
from datetime import datetime

import store

ESTADOS_ABIERTOS = ("pendiente", "en_proceso")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS peticiones (
  id INTEGER PRIMARY KEY,
  consulta TEXT NOT NULL,              -- lo que se escribió: nombre o dominio
  empresa TEXT DEFAULT '',             -- nombre real, ya resuelto por Apollo
  dominio TEXT DEFAULT '',
  pidio TEXT DEFAULT '',               -- correo de quien la pidió
  estado TEXT DEFAULT 'pendiente',
  nota TEXT DEFAULT '',
  decisores TEXT DEFAULT '[]',         -- JSON con los perfiles encontrados
  contactos_nuevos INTEGER DEFAULT 0,  -- cuántos entraron a la base
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_peticiones_estado ON peticiones(estado, id DESC);
"""


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init() -> None:
    # store.connect() cierra sin confirmar: en este backend cada escritor hace
    # su propio commit (ver store.py). Sin él, el INSERT se pierde al salir.
    with store._LOCK, store.connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def _fila(r: dict) -> dict:
    d = dict(r)
    try:
        d["decisores"] = json.loads(d.get("decisores") or "[]")
    except json.JSONDecodeError:
        d["decisores"] = []
    return d


def crear(consulta: str, pidio: str = "") -> dict:
    """Nueva petición. Si ya hay una abierta para lo mismo, devuelve esa en vez
    de duplicarla: pedir dos veces la misma empresa no debe costar el doble."""
    consulta = (consulta or "").strip()
    if not consulta:
        return {"ok": False, "error": "Escribe el nombre o el dominio de la empresa"}
    init()
    with store._LOCK, store.connect() as conn:
        abierta = conn.execute(
            "SELECT * FROM peticiones WHERE lower(consulta)=lower(?) "
            "AND estado IN (?,?) ORDER BY id DESC LIMIT 1",
            (consulta, *ESTADOS_ABIERTOS)).fetchone()
        if abierta:
            return {"ok": True, "repetida": True, "peticion": _fila(abierta)}
        cur = conn.execute(
            "INSERT INTO peticiones (consulta, pidio, estado, created_at, updated_at) "
            "VALUES (?,?,'pendiente',?,?)",
            (consulta, (pidio or "").strip(), _ahora(), _ahora()))
        conn.commit()
        fila = conn.execute("SELECT * FROM peticiones WHERE id=?",
                            (cur.lastrowid,)).fetchone()
    return {"ok": True, "repetida": False, "peticion": _fila(fila)}


def listar(estado: str = "", limite: int = 100) -> dict:
    init()
    q = "SELECT * FROM peticiones"
    params: tuple = ()
    if estado == "abiertas":
        q += " WHERE estado IN (?,?)"
        params = ESTADOS_ABIERTOS
    elif estado:
        q += " WHERE estado=?"
        params = (estado,)
    q += " ORDER BY id DESC LIMIT ?"
    with store._LOCK, store.connect() as conn:
        filas = [_fila(r) for r in conn.execute(q, (*params, max(1, limite))).fetchall()]
        resumen = {r["estado"]: r["n"] for r in conn.execute(
            "SELECT estado, COUNT(*) n FROM peticiones GROUP BY estado").fetchall()}
    return {"ok": True, "peticiones": filas, "resumen": resumen}


def tomar(limite: int = 10) -> list[dict]:
    """Marca como `en_proceso` las pendientes más viejas y las devuelve.

    El paso de estado va en la misma transacción que la lectura: si dos vueltas
    de la máquina se solapan, la segunda no vuelve a coger lo que ya cogió la
    primera y nadie paga dos veces el mismo reveal.
    """
    init()
    with store._LOCK, store.connect() as conn:
        filas = conn.execute(
            "SELECT * FROM peticiones WHERE estado='pendiente' "
            "ORDER BY id LIMIT ?", (max(1, limite),)).fetchall()
        ids = [r["id"] for r in filas]
        if ids:
            conn.execute(
                f"UPDATE peticiones SET estado='en_proceso', updated_at=? "
                f"WHERE id IN ({','.join('?' * len(ids))})", (_ahora(), *ids))
            conn.commit()
    return [_fila(r) for r in filas]


def resolver(pid: int, estado: str, empresa: str = "", dominio: str = "",
             nota: str = "", decisores: list[dict] | None = None,
             contactos_nuevos: int = 0) -> dict:
    init()
    with store._LOCK, store.connect() as conn:
        conn.execute(
            "UPDATE peticiones SET estado=?, empresa=?, dominio=?, nota=?, "
            "decisores=?, contactos_nuevos=?, updated_at=? WHERE id=?",
            (estado, empresa, dominio, nota[:500],
             json.dumps(decisores or [], ensure_ascii=False),
             int(contactos_nuevos), _ahora(), int(pid)))
        conn.commit()
        fila = conn.execute("SELECT * FROM peticiones WHERE id=?", (int(pid),)).fetchone()
    return {"ok": True, "peticion": _fila(fila) if fila else None}


def cancelar(pid: int) -> dict:
    init()
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE peticiones SET estado='cancelada', updated_at=? "
                     "WHERE id=? AND estado IN (?,?)",
                     (_ahora(), int(pid), *ESTADOS_ABIERTOS))
        conn.commit()
        fila = conn.execute("SELECT * FROM peticiones WHERE id=?", (int(pid),)).fetchone()
    return {"ok": True, "peticion": _fila(fila) if fila else None}


def reintentar(pid: int) -> dict:
    """Devuelve a la cola una que quedó en error, en_proceso colgada o cancelada."""
    init()
    with store._LOCK, store.connect() as conn:
        conn.execute("UPDATE peticiones SET estado='pendiente', nota='', updated_at=? "
                     "WHERE id=?", (_ahora(), int(pid)))
        conn.commit()
        fila = conn.execute("SELECT * FROM peticiones WHERE id=?", (int(pid),)).fetchone()
    return {"ok": True, "peticion": _fila(fila) if fila else None}
