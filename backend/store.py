"""Almacén unificado del cockpit (SQLite): contactos, actividades, secuencias,
cola de aprobación y tareas.

Es la ÚNICA fuente de verdad operativa del copiloto. Los contactos entran por
POST /api/contacts (prospección) y la importación por CSV queda apagada: ver
config.DEDUP_CSV.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(__file__).resolve().parent / "platzi_cockpit.db"
_LEGACY_DB_PATH = Path(__file__).resolve().parent / "klik_cockpit.db"
_LOCK = threading.Lock()


def _adopt_legacy_db() -> None:
    """Renombra la base heredada al nombre de Platzi, una sola vez.

    Se hace aquí y no a mano porque el backend puede estar corriendo cuando se
    despliega el cambio: al reiniciar, el proceso nuevo encuentra la base vieja,
    la adopta con su historial y sigue. Si ya existe la de Platzi no toca nada.
    """
    if DB_PATH.exists() or not _LEGACY_DB_PATH.exists():
        return
    for suffix in ("", "-wal", "-shm"):
        old = _LEGACY_DB_PATH.with_name(_LEGACY_DB_PATH.name + suffix)
        if old.exists():
            old.rename(DB_PATH.with_name(DB_PATH.name + suffix))

# Orden del pipeline: el stage solo avanza automáticamente, nunca retrocede.
# "no_responde" va después de "conectado" (rango < en_conversacion) a propósito:
# un contacto marcado como "no responde" que luego contesta se auto-rescata a
# "en_conversacion" vía el reply, y un follow-up enviado no lo resetea.
STAGES = ["nuevo", "contactado", "conectado", "no_responde", "en_conversacion",
          "registrado", "reunion", "cliente", "descartado"]
_RANK = {s: i for i, s in enumerate(STAGES)}

# Qué stage implica cada tipo de actividad (para auto-avance).
ACTIVITY_STAGE = {
    "invite_sent": "contactado",
    "sent": "contactado",
    "followup_sent": "contactado",
    "accepted": "conectado",
    "reply": "en_conversacion",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  csv_key TEXT UNIQUE,
  email TEXT DEFAULT '',
  linkedin TEXT DEFAULT '',
  nombre TEXT DEFAULT '',
  empresa TEXT DEFAULT '',
  cargo TEXT DEFAULT '',
  celular TEXT DEFAULT '',
  ciudad TEXT DEFAULT '',
  sector TEXT DEFAULT '',
  validacion TEXT DEFAULT '',
  stage TEXT DEFAULT 'nuevo',
  source TEXT DEFAULT 'csv',
  campaign TEXT DEFAULT '',
  tier TEXT DEFAULT '',
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts(stage);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  channel TEXT NOT NULL,            -- email | linkedin | call
  type TEXT NOT NULL,               -- sent|open|click|bounce|reply|invite_sent|accepted|followup_sent|call
  ts TEXT NOT NULL,
  run_id TEXT DEFAULT '',
  ext_id TEXT UNIQUE,               -- id externo para ingesta idempotente (sg_event_id, gmail msg id, run:idx)
  meta_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_activities_contact ON activities(contact_id, ts);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(type, ts);

CREATE TABLE IF NOT EXISTS sequences (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sequence_steps (
  id INTEGER PRIMARY KEY,
  sequence_id INTEGER NOT NULL REFERENCES sequences(id),
  step_order INTEGER NOT NULL,
  channel TEXT NOT NULL,            -- linkedin_invite | linkedin_msg | email
  template TEXT DEFAULT '',         -- base/plantilla que Claude usa como punto de partida
  delay_days INTEGER DEFAULT 3
);

CREATE TABLE IF NOT EXISTS contact_sequences (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  sequence_id INTEGER NOT NULL REFERENCES sequences(id),
  current_step INTEGER DEFAULT 0,   -- pasos ya completados (0 = ninguno)
  status TEXT DEFAULT 'active',     -- active | paused | replied | completed | failed
  next_action_at TEXT,
  updated_at TEXT,
  UNIQUE(contact_id, sequence_id)
);
CREATE INDEX IF NOT EXISTS idx_cs_due ON contact_sequences(status, next_action_at);

CREATE TABLE IF NOT EXISTS approval_queue (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  contact_sequence_id INTEGER REFERENCES contact_sequences(id),
  step_id INTEGER REFERENCES sequence_steps(id),
  channel TEXT NOT NULL,            -- linkedin_invite | linkedin_msg | email
  draft_subject TEXT DEFAULT '',
  draft_body TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',    -- pending | approved | skipped | sent | confirmed | failed
  created_at TEXT,
  decided_at TEXT,
  sent_run_id TEXT DEFAULT '',
  blocked_reasons TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_appr_status ON approval_queue(status);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER REFERENCES contacts(id),
  type TEXT NOT NULL,               -- call | reply_review | manual
  due_at TEXT,
  status TEXT DEFAULT 'open',       -- open | done | dismissed
  notes TEXT DEFAULT '',
  meta_json TEXT DEFAULT '{}',
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, due_at);

CREATE TABLE IF NOT EXISTS jobs_log (
  run_id TEXT PRIMARY KEY,
  channel TEXT,
  status TEXT DEFAULT 'running',    -- running | done | error | stopped | interrupted
  total INTEGER DEFAULT 0,
  params_json TEXT DEFAULT '{}',
  events_file TEXT DEFAULT '',
  started_at TEXT,
  finished_at TEXT
);

-- Intentos de contacto que NO terminaron en envío. Sin esto, el trabajo diario
-- vuelve cada mañana a los mismos perfiles que no tienen ruta y nunca avanza;
-- y en la ficha del prospecto no queda constancia de que se intentó.
CREATE TABLE IF NOT EXISTS intentos (
  id INTEGER PRIMARY KEY,
  contact_id INTEGER NOT NULL REFERENCES contacts(id),
  seat TEXT DEFAULT '',
  canal TEXT DEFAULT 'linkedin',
  estado TEXT NOT NULL,             -- not_found | error | rate_limited | skipped
  detalle TEXT DEFAULT '',
  run_id TEXT DEFAULT '',
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intentos_contacto ON intentos(contact_id, ts);

CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    _adopt_legacy_db()
    with _LOCK, connect() as conn:
        conn.executescript(SCHEMA)
        # Migración aditiva: `model` no estaba en el CREATE TABLE original de
        # approval_queue (CREATE TABLE IF NOT EXISTS no la agrega a una tabla
        # que ya existe). Sin ella, un draft generado por el fallback local
        # (Claude caído/sin cuota/rate-limited) quedaba indistinguible de uno
        # real de Claude — el mismo tipo de error enmascarado que el bug de
        # créditos de Apollo, aquí para la calidad de los borradores.
        try:
            conn.execute("ALTER TABLE approval_queue ADD COLUMN model TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # ya existe (server reiniciado tras la primera migración)
        # Motivos por los que la compuerta frenó un borrador. Van en la fila y no
        # en un log: el que aprueba tiene que leerlos en la misma pantalla.
        try:
            conn.execute("ALTER TABLE approval_queue ADD COLUMN blocked_reasons TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Migración aditiva (Fase B — store.db pasa a ser la única fuente que
        # lee la UI; antes solo el CSV tenía estos tres campos).
        for col in ("tipo", "estado", "empresa_linkedin"):
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        # Notas libres por contacto (Pipeline) — no existía ningún campo de
        # texto libre a nivel de contacto, solo `tasks.notes` (atado a una
        # tarea puntual).
        try:
            conn.execute("ALTER TABLE contacts ADD COLUMN notas TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Cruce automático con HubSpot al cargar prospectos (verde/amarillo/rojo):
        # evita hacer outreach en frío a clientes actuales o gente ya en venta.
        for col in ("hubspot_verdict", "hubspot_reason", "hubspot_checked_at"):
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        # Campaña y tier: sin esto no se puede correr más de una campaña a la
        # vez sobre la misma base ni segmentar el lote dentro de la UI.
        for col in ("campaign", "tier"):
            try:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        # Asiento de LinkedIn que firma a este contacto. Es pegajoso: quien
        # manda la invitación es el único que después puede escribirle, porque
        # es el único a quien aceptó. Vacío = todavía no se ha repartido.
        try:
            conn.execute("ALTER TABLE contacts ADD COLUMN li_seat TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Id del contacto en HubSpot. Se guarda porque la inscripción en una
        # secuencia solo acepta `contactId` y resolverlo por correo en cada
        # vuelta serían 20 búsquedas por tick para un dato que no cambia.
        try:
            conn.execute("ALTER TABLE contacts ADD COLUMN hubspot_contact_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        # Columnas del negocio energético heredado. Se van: en Platzi no
        # significan nada y salían impresas en la tabla de la UI.
        for col in ("consumo_kwh", "prioridad"):
            try:
                conn.execute(f"ALTER TABLE contacts DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def fetch_one(query: str, params: tuple = ()) -> Optional[dict]:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


# ----------------------------- kv ----------------------------------------
def kv_get(key: str, default: str = "") -> str:
    row = fetch_one("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("INSERT INTO kv(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()


# ----------------------------- contactos ----------------------------------
_CONTACT_FIELDS = ("email", "linkedin", "nombre", "empresa", "cargo", "celular",
                   "ciudad", "sector", "validacion", "tipo", "estado",
                   "empresa_linkedin", "campaign", "tier")


def upsert_contact(csv_key: str, fields: dict, source: str = "csv") -> int:
    """Inserta o refresca datos base del contacto. No toca stage si ya existe."""
    ts = now_iso()
    vals = {k: (fields.get(k) or "").strip() for k in _CONTACT_FIELDS}
    with _LOCK, connect() as conn:
        cur = conn.execute("SELECT id FROM contacts WHERE csv_key=?", (csv_key,))
        row = cur.fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in _CONTACT_FIELDS)
            conn.execute(f"UPDATE contacts SET {sets}, updated_at=? WHERE id=?",
                         (*vals.values(), ts, row["id"]))
            conn.commit()
            return row["id"]
        cols = ", ".join(_CONTACT_FIELDS)
        marks = ", ".join("?" for _ in _CONTACT_FIELDS)
        cur = conn.execute(
            f"INSERT INTO contacts (csv_key, {cols}, source, created_at, updated_at) "
            f"VALUES (?, {marks}, ?, ?, ?)",
            (csv_key, *vals.values(), source, ts, ts))
        conn.commit()
        return cur.lastrowid


def get_contact(contact_id: int) -> Optional[dict]:
    return fetch_one("SELECT * FROM contacts WHERE id=?", (contact_id,))


def contacts_needing_hubspot(limit: int = 500) -> list[dict]:
    """Contactos con email pero sin veredicto de HubSpot todavía (para backfill)."""
    return fetch_all(
        "SELECT id, email, empresa FROM contacts "
        "WHERE (hubspot_verdict IS NULL OR hubspot_verdict='') "
        "AND email IS NOT NULL AND email<>'' ORDER BY id DESC LIMIT ?", (limit,))


def hubspot_summary() -> dict:
    """Comparativa leads vs prospectos: cómo se reparte la base según el cruce
    con HubSpot (verde = prospecto nuevo apto, amarillo = ya es lead del CRM,
    rojo = cliente o deal abierto) más el desglose por motivo.

    Los motivos los genera nuestro propio hubspot_check._verdict, así que son
    estables; solo el de deals trae un número variable y se normaliza aquí.
    """
    total = fetch_one("SELECT COUNT(*) AS n FROM contacts")["n"]
    con_email = fetch_one(
        "SELECT COUNT(*) AS n FROM contacts WHERE email IS NOT NULL AND email<>''")["n"]

    verdicts = {"verde": 0, "amarillo": 0, "rojo": 0}
    for r in fetch_all("SELECT hubspot_verdict AS v, COUNT(*) AS n FROM contacts "
                       "WHERE hubspot_verdict IS NOT NULL AND hubspot_verdict<>'' "
                       "GROUP BY 1"):
        verdicts[r["v"]] = verdicts.get(r["v"], 0) + r["n"]
    checked = sum(verdicts.values())

    motivos: dict[tuple[str, str], int] = {}
    for r in fetch_all("SELECT hubspot_verdict AS v, hubspot_reason AS reason, "
                       "COUNT(*) AS n FROM contacts WHERE hubspot_verdict IS NOT NULL "
                       "AND hubspot_verdict<>'' GROUP BY 1, 2"):
        reason = re.sub(r"\bTiene \d+ deal", "Tiene deal", r["reason"] or "—")
        key = (r["v"], reason)
        motivos[key] = motivos.get(key, 0) + r["n"]

    last = fetch_one("SELECT MAX(hubspot_checked_at) AS ts FROM contacts")

    return {
        "total": total,
        "con_email": con_email,
        "checked": checked,
        # Sin email no se puede cruzar: se cuentan aparte de los pendientes reales.
        "pendientes": max(con_email - checked, 0),
        "sin_email": max(total - con_email, 0),
        "verdicts": verdicts,
        "motivos": [{"verdict": v, "reason": reason, "n": n}
                    for (v, reason), n in sorted(motivos.items(), key=lambda x: -x[1])],
        "last_check": (last or {}).get("ts") or "",
    }


def set_hubspot(contact_id: int, verdict: str, reason: str,
                force: bool = False) -> bool:
    """Persiste el veredicto del cruce con HubSpot (verde/amarillo/rojo).

    Un `rojo` NO se degrada a verde/amarillo salvo con `force`. El motivo es que
    hay dos cruces con distinto poder de resolución: el de **persona** (por
    correo, `hubspot_check.check`) y el de **empresa** (por dominio,
    `validar_hubspot`). El de persona es estrictamente menos informado —«este
    correo no está en el CRM» no dice nada de que su empresa sea cliente— así
    que dejarlo ganar es siempre un error. Y lo haría en silencio: basta que
    alguien pulse el botón HS de una ficha, o que corra el recruce masivo, para
    que un rojo heredado de la empresa vuelva a verde y el contacto quede
    invitable otra vez.

    Devuelve True si escribió.
    """
    v = (verdict or "").strip()
    if not force and v in ("verde", "amarillo"):
        actual = fetch_one(
            "SELECT COALESCE(hubspot_verdict,'') v FROM contacts WHERE id=?",
            (contact_id,))
        if (actual or {}).get("v") == "rojo":
            return False
    with _LOCK, connect() as conn:
        conn.execute(
            "UPDATE contacts SET hubspot_verdict=?, hubspot_reason=?, "
            "hubspot_checked_at=? WHERE id=?",
            (v, (reason or "").strip(), now_iso(), contact_id))
        conn.commit()
    return True


def set_hubspot_contact_id(contact_id: int, hs_id: str) -> bool:
    """Guarda el id del contacto en HubSpot.

    Setter propio y no `set_contact_fields`: esa función solo aplica las
    columnas de `EDITABLE_CONTACT_FIELDS` e **ignora el resto en silencio**
    devolviendo False. Así se «escribieron» 81 veredictos que nunca llegaron a
    la base (ver `validar_hubspot._guardar`); no repetirlo aquí.
    """
    hs_id = str(hs_id or "").strip()
    if not hs_id:
        return False
    with _LOCK, connect() as conn:
        conn.execute("UPDATE contacts SET hubspot_contact_id=?, updated_at=? WHERE id=?",
                     (hs_id, now_iso(), contact_id))
        conn.commit()
    return True


def find_contact(email: str = "", linkedin_slug: str = "") -> Optional[dict]:
    if email:
        row = fetch_one("SELECT * FROM contacts WHERE lower(email)=?", (email.lower(),))
        if row:
            return row
    if linkedin_slug:
        row = fetch_one("SELECT * FROM contacts WHERE lower(linkedin) LIKE ?",
                        (f"%/in/{linkedin_slug.lower()}%",))
        if row:
            return row
    return None


def find_contact_by_domain(domain: str) -> Optional[dict]:
    if not domain:
        return None
    return fetch_one("SELECT * FROM contacts WHERE lower(email) LIKE ?",
                     (f"%@{domain.lower()}",))


def set_stage(contact_id: int, stage: str, force: bool = True) -> bool:
    """Cambia el stage. force=False solo avanza (nunca retrocede)."""
    if stage not in _RANK:
        return False
    with _LOCK, connect() as conn:
        if not force:
            row = conn.execute("SELECT stage FROM contacts WHERE id=?", (contact_id,)).fetchone()
            if not row or _RANK.get(row["stage"], 0) >= _RANK[stage]:
                return False
        conn.execute("UPDATE contacts SET stage=?, updated_at=? WHERE id=?",
                     (stage, now_iso(), contact_id))
        conn.commit()
        return True


def set_notes(contact_id: int, notas: str) -> bool:
    with _LOCK, connect() as conn:
        cur = conn.execute("UPDATE contacts SET notas=?, updated_at=? WHERE id=?",
                           (notas, now_iso(), contact_id))
        conn.commit()
        return cur.rowcount > 0


# Campos del lead editables a mano desde el detalle del contacto. Whitelist
# explícita: nunca dejar que un PATCH toque id/stage/timestamps u otra columna.
# `campaign` no se edita a mano como el resto: lo escribe el flujo al inscribir
# (main.sequences_enroll, enrollment.send_approved). Estaba fuera de esta lista,
# así que esas escrituras se descartaban en silencio y todos los contactos
# quedaban con campaign='' — con eso, filtrar el pipeline por campaña devolvía
# cero y no había forma de decir a quién se invitó al desayuno.
EDITABLE_CONTACT_FIELDS = ("nombre", "empresa", "cargo", "email", "celular",
                           "linkedin", "campaign")


def set_contact_fields(contact_id: int, fields: dict) -> bool:
    """Actualiza a mano los datos de un lead (nombre, cargo, email, etc.).
    Solo aplica las columnas de EDITABLE_CONTACT_FIELDS; ignora el resto."""
    updates = {k: (v or "").strip() for k, v in fields.items()
               if k in EDITABLE_CONTACT_FIELDS}
    if not updates:
        return False
    cols = ", ".join(f"{k}=?" for k in updates)
    params = [*updates.values(), now_iso(), contact_id]
    with _LOCK, connect() as conn:
        cur = conn.execute(
            f"UPDATE contacts SET {cols}, updated_at=? WHERE id=?", params)
        conn.commit()
        return cur.rowcount > 0


_REACHABLE = "(nombre != '' OR email != '' OR celular != '' OR linkedin != '')"


def pipeline_summary(limit_per_stage: int = 200,
                    campaigns: Optional[tuple] = None,
                    seat_ids: Optional[tuple] = None) -> dict:
    """Contactos por stage — excluye "cáscaras" (fila solo con nombre de
    empresa, sin persona ni ningún dato de contacto: ni nombre, ni email, ni
    celular, ni LinkedIn). Esas vienen de import CSV de empresas a las
    que nunca se les encontró un decisor — no son gente a la que se pueda
    llamar/escribir, solo ensucian el Pipeline. Siguen existiendo en
    `contacts` (no se borran) para cuando se prospecte esa empresa.

    `campaigns` acota a unos orígenes y es lo que hace posible un tablero por
    procedencia (Pipeline Ventas) sin duplicar esta consulta: mismo cálculo de
    etapas, mismos descartes, distinto universo.
    """
    # El universo se puede acotar por origen (`campaigns`), por dueño
    # (`seat_ids`) o por los dos, y se combinan con OR. Hacen falta las dos vías
    # porque «la base de Ventas» son dos cosas: lo que llegó de la app de Platzi
    # Ventas —eso es origen— y lo que se le asignó a mano al equipo de Sales,
    # que conserva su origen verdadero (`ampliacion-2x` y demás) y solo cambió
    # de dueño. Sobrescribir `campaign` para que apareciera aquí habría borrado
    # de dónde salió cada contacto.
    condiciones, extra = [], []
    if campaigns:
        condiciones.append("campaign IN (%s)" % ",".join("?" * len(campaigns)))
        extra += list(campaigns)
    if seat_ids:
        condiciones.append("li_seat IN (%s)" % ",".join("?" * len(seat_ids)))
        extra += list(seat_ids)
    filtro = (" AND (%s)" % " OR ".join(condiciones)) if condiciones else ""
    extra = tuple(extra)
    counts = {s: 0 for s in STAGES}
    for r in fetch_all(
            f"SELECT stage, COUNT(*) n FROM contacts WHERE {_REACHABLE}{filtro} "
            "GROUP BY stage", extra):
        if r["stage"] in counts:
            counts[r["stage"]] = r["n"]
    stages = {}
    for s in STAGES:
        stages[s] = fetch_all(
            "SELECT c.*, "
            " (SELECT MAX(ts) FROM activities a WHERE a.contact_id=c.id) last_activity, "
            " (SELECT status FROM contact_sequences cs WHERE cs.contact_id=c.id "
            "   ORDER BY cs.id DESC LIMIT 1) seq_status "
            f"FROM contacts c WHERE stage=? AND {_REACHABLE}{filtro} "
            "ORDER BY updated_at DESC LIMIT ?",
            (s, *extra, limit_per_stage))
    # Quién firma cada prospecto. El id («vp-2») no le dice nada a quien mira el
    # tablero, así que el nombre viaja resuelto en la misma respuesta y la UI no
    # tiene que pedir los asientos por separado.
    remitentes = {r["li_seat"]: r["li_seat"] for r in fetch_all(
        "SELECT DISTINCT li_seat FROM contacts WHERE li_seat<>''")}
    for k in list(remitentes):
        nombre = kv_get(f"seat:{k}:nombre", "").strip()
        remitentes[k] = nombre or k
    # Descartados y clientes no se asignan a propósito: contarlos como «sin
    # asignar» manda a alguien a arreglar algo que está bien.
    sin_asignar = fetch_one(
        f"SELECT COUNT(*) n FROM contacts WHERE {_REACHABLE}{filtro} AND "
        "(li_seat IS NULL OR li_seat='') AND linkedin<>'' "
        "AND stage NOT IN ('descartado','cliente')", extra)
    return {"counts": counts, "stages": stages, "total": sum(counts.values()),
            "remitentes": remitentes,
            "sin_remitente": (sin_asignar or {}).get("n", 0)}


# ----------------------------- actividades --------------------------------
def add_activity(contact_id: int, channel: str, type_: str, ts: str,
                 run_id: str = "", ext_id: Optional[str] = None,
                 meta: Optional[dict] = None) -> bool:
    """Registra una actividad (idempotente por ext_id). Auto-avanza el stage.
    Devuelve True si era nueva."""
    with _LOCK, connect() as conn:
        try:
            conn.execute(
                "INSERT INTO activities (contact_id, channel, type, ts, run_id, ext_id, meta_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (contact_id, channel, type_, ts, run_id, ext_id,
                 json.dumps(meta or {}, ensure_ascii=False)))
            conn.commit()
        except sqlite3.IntegrityError:
            return False
    stage = ACTIVITY_STAGE.get(type_)
    if stage:
        set_stage(contact_id, stage, force=False)
    return True


def timeline(contact_id: int) -> list[dict]:
    rows = fetch_all("SELECT * FROM activities WHERE contact_id=? ORDER BY ts DESC, id DESC",
                     (contact_id,))
    for r in rows:
        try:
            r["meta"] = json.loads(r.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            r["meta"] = {}
    return rows


def count_activities_today(channel: str, types: tuple[str, ...]) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    marks = ",".join("?" for _ in types)
    row = fetch_one(
        f"SELECT COUNT(*) n FROM activities WHERE channel=? AND type IN ({marks}) "
        f"AND substr(ts,1,10)=?", (channel, *types, today))
    return row["n"] if row else 0


def count_seat_activities(seat_id: str, types: tuple[str, ...], desde: str) -> int:
    """Toques de LinkedIn de un asiento desde una fecha (inclusive).

    Se cuenta por el asiento ASIGNADO al contacto y no por el job que lo envió:
    la asignación es pegajosa, así que es el mismo dato, y así un envío hecho a
    mano (fuera de la cola) también gasta el cupo del perfil que lo hizo. Si se
    contara por job, el cupo diría que sobra cuando ya no sobra.
    """
    marks = ",".join("?" for _ in types)
    row = fetch_one(
        f"SELECT COUNT(*) n FROM activities a JOIN contacts c ON c.id=a.contact_id "
        f"WHERE c.li_seat=? AND a.channel='linkedin' AND a.type IN ({marks}) "
        f"AND substr(a.ts,1,10) >= ?", (seat_id, *types, desde))
    return row["n"] if row else 0


def count_seat_queued(seat_id: str, channels: tuple[str, ...], desde: str) -> int:
    """Borradores del asiento que todavía no se confirmaron.

    Cuentan contra el cupo: si no, el copiloto genera 20 borradores de un
    asiento con cupo 6 y el tope se rompe en el momento en que alguien aprueba
    todo de una.
    """
    marks = ",".join("?" for _ in channels)
    row = fetch_one(
        f"SELECT COUNT(*) n FROM approval_queue q JOIN contacts c ON c.id=q.contact_id "
        f"WHERE c.li_seat=? AND q.channel IN ({marks}) "
        f"AND q.status IN ('pending','approved','sent') AND substr(q.created_at,1,10) >= ?",
        (seat_id, *channels, desde))
    return row["n"] if row else 0


def count_seat_contacts(seat_id: str) -> int:
    row = fetch_one("SELECT COUNT(*) n FROM contacts WHERE li_seat=?", (seat_id,))
    return row["n"] if row else 0


def add_intento(contact_id: int, seat: str, estado: str, detalle: str,
                run_id: str = "", canal: str = "linkedin") -> None:
    with _LOCK, connect() as conn:
        conn.execute(
            "INSERT INTO intentos (contact_id, seat, canal, estado, detalle, run_id, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (contact_id, seat, canal, estado, detalle, run_id, now_iso()))
        conn.commit()


def intentos_de(contact_id: int, limite: int = 10) -> list[dict]:
    return fetch_all("SELECT * FROM intentos WHERE contact_id=? ORDER BY id DESC LIMIT ?",
                     (contact_id, limite))


def set_seat(contact_id: int, seat_id: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE contacts SET li_seat=?, updated_at=? WHERE id=?",
                     (seat_id, now_iso(), contact_id))
        conn.commit()


def seat_counts() -> dict[str, int]:
    return {r["li_seat"]: r["n"] for r in fetch_all(
        "SELECT li_seat, COUNT(*) n FROM contacts WHERE li_seat<>'' GROUP BY 1")}


# ----------------------------- secuencias ---------------------------------
def sequence_steps(sequence_id: int) -> list[dict]:
    return fetch_all("SELECT * FROM sequence_steps WHERE sequence_id=? ORDER BY step_order",
                     (sequence_id,))


def enroll(contact_id: int, sequence_id: int, next_action_at: Optional[str] = None) -> Optional[int]:
    """Inscribe al contacto en la secuencia (si no está ya). Paso 1 vence ya."""
    ts = now_iso()
    with _LOCK, connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO contact_sequences (contact_id, sequence_id, current_step, status, "
                "next_action_at, updated_at) VALUES (?,?,0,'active',?,?)",
                (contact_id, sequence_id, next_action_at or ts, ts))
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def due_sequences(limit: int = 100) -> list[dict]:
    """Secuencias activas cuyo próximo paso ya venció, con datos del contacto."""
    return fetch_all(
        "SELECT cs.*, c.nombre, c.empresa, c.cargo, c.email, c.linkedin, c.stage, c.sector "
        "FROM contact_sequences cs JOIN contacts c ON c.id=cs.contact_id "
        "WHERE cs.status='active' AND cs.next_action_at <= ? "
        "AND c.stage NOT IN ('cliente','descartado') "
        "ORDER BY cs.next_action_at LIMIT ?", (now_iso(), limit))


def set_sequence_status(cs_id: int, status: str, next_action_at: Optional[str] = None) -> None:
    with _LOCK, connect() as conn:
        if next_action_at is not None:
            conn.execute("UPDATE contact_sequences SET status=?, next_action_at=?, updated_at=? "
                         "WHERE id=?", (status, next_action_at, now_iso(), cs_id))
        else:
            conn.execute("UPDATE contact_sequences SET status=?, updated_at=? WHERE id=?",
                         (status, now_iso(), cs_id))
        conn.commit()


def advance_sequence(cs_id: int, next_action_at: Optional[str]) -> None:
    """Marca el paso actual como completado; si no hay más pasos, completa."""
    cs = fetch_one("SELECT * FROM contact_sequences WHERE id=?", (cs_id,))
    if not cs:
        return
    steps = sequence_steps(cs["sequence_id"])
    new_step = cs["current_step"] + 1
    done = new_step >= len(steps)
    with _LOCK, connect() as conn:
        conn.execute(
            "UPDATE contact_sequences SET current_step=?, status=?, next_action_at=?, updated_at=? "
            "WHERE id=?",
            (new_step, "completed" if done else "active",
             None if done else next_action_at, now_iso(), cs_id))
        conn.commit()


def pause_sequences_for(contact_id: int, status: str = "replied") -> int:
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "UPDATE contact_sequences SET status=?, updated_at=? "
            "WHERE contact_id=? AND status='active'", (status, now_iso(), contact_id))
        conn.commit()
        return cur.rowcount


# ----------------------------- aprobaciones -------------------------------
def add_approval(contact_id: int, channel: str, draft_subject: str, draft_body: str,
                 cs_id: Optional[int] = None, step_id: Optional[int] = None,
                 model: str = "", reasons: Optional[list[str]] = None) -> int:
    """Encola un borrador. Con `reasons` entra como `blocked`: se ve, se puede
    corregir y aprobar a mano, pero no lo puede enviar un 'aprobar todos'."""
    status = "blocked" if reasons else "pending"
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "INSERT INTO approval_queue (contact_id, contact_sequence_id, step_id, channel, "
            "draft_subject, draft_body, status, created_at, model, blocked_reasons) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (contact_id, cs_id, step_id, channel, draft_subject, draft_body, status,
             now_iso(), model, " · ".join(reasons or [])))
        conn.commit()
        return cur.lastrowid


def pending_approval_for(cs_id: int) -> Optional[dict]:
    return fetch_one(
        "SELECT * FROM approval_queue WHERE contact_sequence_id=? "
        "AND status IN ('pending','blocked','approved','sent') "
        "ORDER BY id DESC LIMIT 1", (cs_id,))


def list_approvals(status: str = "pending") -> list[dict]:
    return fetch_all(
        "SELECT a.*, c.nombre, c.empresa, c.cargo, c.email, c.linkedin, c.stage, "
        " c.li_seat, "
        " s.step_order, s.delay_days "
        "FROM approval_queue a JOIN contacts c ON c.id=a.contact_id "
        "LEFT JOIN sequence_steps s ON s.id=a.step_id "
        "WHERE a.status=? ORDER BY a.created_at", (status,))


def decide_approval(approval_id: int, status: str,
                    subject: Optional[str] = None, body: Optional[str] = None) -> Optional[dict]:
    with _LOCK, connect() as conn:
        sets, vals = ["status=?", "decided_at=?"], [status, now_iso()]
        if subject is not None:
            sets.append("draft_subject=?")
            vals.append(subject)
        if body is not None:
            sets.append("draft_body=?")
            vals.append(body)
        vals.append(approval_id)
        conn.execute(f"UPDATE approval_queue SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()
    return fetch_one("SELECT * FROM approval_queue WHERE id=?", (approval_id,))


def mark_approvals_sent(ids: list[int], run_id: str) -> None:
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    with _LOCK, connect() as conn:
        conn.execute(
            f"UPDATE approval_queue SET status='sent', sent_run_id=? WHERE id IN ({marks})",
            (run_id, *ids))
        conn.commit()


# ----------------------------- tareas -------------------------------------
def add_task(contact_id: Optional[int], type_: str, due_at: str,
             notes: str = "", meta: Optional[dict] = None) -> int:
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (contact_id, type, due_at, status, notes, meta_json, created_at, "
            "updated_at) VALUES (?,?,?,'open',?,?,?,?)",
            (contact_id, type_, due_at, notes,
             json.dumps(meta or {}, ensure_ascii=False), now_iso(), now_iso()))
        conn.commit()
        return cur.lastrowid


def list_tasks(status: str = "open", due_before: Optional[str] = None) -> list[dict]:
    q = ("SELECT t.*, c.nombre, c.empresa, c.email, c.celular, c.linkedin "
         "FROM tasks t LEFT JOIN contacts c ON c.id=t.contact_id WHERE t.status=?")
    params: list = [status]
    if due_before:
        q += " AND (t.due_at IS NULL OR t.due_at <= ?)"
        params.append(due_before)
    q += " ORDER BY t.due_at"
    rows = fetch_all(q, tuple(params))
    for r in rows:
        try:
            r["meta"] = json.loads(r.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            r["meta"] = {}
    return rows


def set_task_status(task_id: int, status: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                     (status, now_iso(), task_id))
        conn.commit()


def open_task_exists(contact_id: int, type_: str) -> bool:
    return bool(fetch_one(
        "SELECT id FROM tasks WHERE contact_id=? AND type=? AND status='open' LIMIT 1",
        (contact_id, type_)))


# ----------------------------- jobs log -----------------------------------
def log_job(run_id: str, channel: str, total: int, params: dict, events_file: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs_log (run_id, channel, status, total, params_json, "
            "events_file, started_at) VALUES (?,?,'running',?,?,?,?)",
            (run_id, channel, total, json.dumps(params, ensure_ascii=False, default=str),
             events_file, now_iso()))
        conn.commit()


def finish_job(run_id: str, status: str) -> None:
    with _LOCK, connect() as conn:
        conn.execute("UPDATE jobs_log SET status=?, finished_at=? WHERE run_id=?",
                     (status, now_iso(), run_id))
        conn.commit()


def mark_interrupted_jobs() -> int:
    """Al arrancar: jobs que quedaron 'running' de una sesión anterior."""
    with _LOCK, connect() as conn:
        cur = conn.execute(
            "UPDATE jobs_log SET status='interrupted', finished_at=? WHERE status='running'",
            (now_iso(),))
        conn.commit()
        return cur.rowcount


init()
