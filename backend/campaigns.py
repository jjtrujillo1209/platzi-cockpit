"""Campañas de outreach: marca, evento, remitente, cadencia y copy aprobado.

El copy vive aquí, en el código, y no en la base ni en la UI — mismo criterio que
`sdr-voz/backend/agent_spec.py`: un mensaje que sale a un grupo de presidentes es
contenido revisado, no configuración. Para cambiarlo se edita este archivo y se
vuelve a sembrar; queda versionado y se puede leer entero de una sentada.

Lo único que NO vive aquí son los datos que todavía no están confirmados
(anfitrión, lugar, qué se promete). Esos se leen de `store.kv` para poder
llenarlos sin tocar código, y mientras estén vacíos `preflight()` bloquea la
campaña: es preferible no enviar a enviarle a un presidente un mensaje con un
hueco.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import seats
import store

# ---------------------------------------------------------------- estructura
CHANNELS = ("linkedin_invite", "linkedin_msg", "email", "call")


@dataclass(frozen=True)
class Step:
    """Un toque de la cadencia, con su copy ya escrito.

    `delay_days` es el espaciado normal; `send_by_offset` es el tope duro en días
    ANTES del evento. Cuando los dos se contradicen manda el tope: una campaña de
    evento no tiene sentido después de su fecha.
    """
    order: int
    channel: str
    label: str
    body: str
    subject: str = ""
    delay_days: int = 0
    send_by_offset: Optional[int] = None   # días antes del evento
    needs_hook: bool = False
    # Mismo paso, escrito en primera persona, para cuando el perfil que firma es
    # el propio anfitrión. Sin esto el anfitrión habla de sí mismo en tercera
    # persona («Fulano está armando algo… y quiere que estés ahí»), que es
    # exactamente como se ve un mensaje que escribió otro.
    body_anfitrion: str = ""

    def send_by(self, event_day: date) -> Optional[date]:
        if self.send_by_offset is None:
            return None
        return event_day - _timedelta_days(self.send_by_offset)

    def cuerpo(self, firma_el_anfitrion: bool = False) -> str:
        if firma_el_anfitrion and self.body_anfitrion:
            return self.body_anfitrion
        return self.body


def _timedelta_days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


@dataclass(frozen=True)
class Campaign:
    slug: str
    name: str
    brand: str
    style: str
    event_name: str
    event_day: date
    event_url: str
    cutoff_day: date            # después de esto no se generan toques nuevos
    steps: tuple[Step, ...]
    sender_name: str
    sender_email: str
    # El correo siempre sale del buzón del equipo (regla VP a VP: el VP presta
    # el perfil de LinkedIn, nunca el buzón). LinkedIn, en cambio, lo firman los
    # asientos de `seats.py` — ya no hay un asiento por campaña.
    confirm_keys: tuple[str, ...] = ()

    # ---- datos pendientes de confirmar, con override por kv ----
    def blanks(self) -> dict[str, str]:
        return {k: store.kv_get(f"campaign:{self.slug}:{k}", "").strip()
                for k in self.confirm_keys}

    def missing(self) -> list[str]:
        return [k for k, v in self.blanks().items() if not v]

    def tokens(self, contact: dict, link: str = "", hook: str = "",
               firma: str = "") -> dict[str, str]:
        nombre = (contact.get("nombre") or "").strip()
        ctx = {
            "nombre": nombre,
            "primer_nombre": nombre.split(" ")[0] if nombre else "",
            # Apellido = todo lo que sigue al primer nombre. En Colombia son dos
            # («Carlos Mario González Ruiz» → «Mario González Ruiz» sería falso),
            # así que se toma solo el último token, que es el que se usa al tratar
            # a alguien por su apellido.
            "apellido": nombre.split(" ")[-1] if len(nombre.split(" ")) > 1 else "",
            # Un solo campo para el asunto: con «Mateo» (sin apellido en la base)
            # «{{primer_nombre}} {{apellido}}» dejaría el token colgando o un doble
            # espacio. Con nombre completo siempre sale bien.
            "nombre_completo": nombre,
            "empresa": (contact.get("empresa") or "").strip(),
            "cargo": (contact.get("cargo") or "").strip(),
            "email": (contact.get("email") or "").strip(),
            "evento": self.event_name,
            "fecha_evento": _es_fecha(self.event_day),
            "fecha_cierre": _es_fecha(self.cutoff_day),
            "remitente": self.sender_name,
            # Quién firma el toque de LinkedIn. En correo y llamada sigue siendo
            # el equipo; en LinkedIn es el VP que prestó el perfil.
            "firma": firma or self.sender_name,
            "link": link,
            "gancho": hook,
        }
        ctx.update(self.blanks())
        return ctx


# Tokens que varían por lead → variables Liquid de Amplemarket. El resto de los
# tokens son constantes de la campaña (fecha, lugar, anfitrión) y se imprimen
# como texto literal en la plantilla: no tiene sentido mandarlos 50 veces.
LIQUID = {
    "nombre": "first_name",
    "primer_nombre": "first_name",
    "apellido": "last_name",
    "nombre_completo": "full_name",
    "empresa": "company_name",
    "gancho": "gancho",
    "link": "link_invitacion",
}


def _es_fecha(d: date) -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{dias[d.weekday()]} {d.day} de {meses[d.month - 1]}"


# ------------------------------------------------ marca y tono (reutilizables)
_MARCA = (
    "Platzi para Empresas forma equipos técnicos de compañías de LATAM en IA, "
    "datos, desarrollo y liderazgo, con rutas de aprendizaje y seguimiento de "
    "avance por persona. Quien compra suele ser el líder de Talento, de "
    "Tecnología o el propio presidente de la compañía."
)

_ESTILO = (
    "Español colombiano, cercano y de persona a persona, TUTEANDO — es la voz "
    "de Platzi. El destinatario es presidente o director general de una compañía "
    "de más de 200 empleados, así que cercano no es confianzudo: nada de jerga "
    "ni de palmadita en la espalda. "
    "Sin emojis, sin signos de admiración, sin 'estimado' ni 'cordialmente'. "
    "PROHIBIDO inventar cifras, porcentajes, rankings o estudios: si no está en "
    "el contexto que te doy, no existe. Una sola llamada a la acción."
)

# ----------------------------------------------------------- sin campañas
# La campaña del desayuno (28 de agosto de 2026) se retiró el 25/08/2026: el
# evento caía en tres días y su paso 1 era una invitación CON nota, que no es la
# cadencia que se va a operar. Se conserva toda la maquinaria —`Campaign`,
# `Step`, la compuerta, el sembrado, el preflight— y arriba quedan la marca y el
# tono, que son reutilizables por la secuencia que la reemplace.
#
# Mientras `_ALL` esté vacío el cockpit no improvisa: `generate_drafts()` deja
# quietas las secuencias sin campaña dueña y `POST /api/sequences/enroll`
# responde «campaña desconocida» en vez de inscribir gente contra un copy que no
# existe.
_ALL: dict[str, Campaign] = {}


def get(slug: str) -> Optional[Campaign]:
    return _ALL.get(slug)


def list_all() -> list[dict]:
    return [{"slug": c.slug, "name": c.name, "event_day": c.event_day.isoformat(),
             "cutoff_day": c.cutoff_day.isoformat(), "steps": len(c.steps),
             "missing": c.missing()} for c in _ALL.values()]


# ------------------------------------------------------------------ sembrado
def seed(campaign: Campaign) -> int:
    """Escribe la secuencia y sus pasos en la base. Idempotente por nombre."""
    row = store.fetch_one("SELECT id FROM sequences WHERE name=?", (campaign.name,))
    if row:
        seq_id = row["id"]
        with store._LOCK, store.connect() as conn:
            conn.execute("DELETE FROM sequence_steps WHERE sequence_id=?", (seq_id,))
            conn.commit()
    else:
        with store._LOCK, store.connect() as conn:
            cur = conn.execute("INSERT INTO sequences (name, active) VALUES (?,1)",
                               (campaign.name,))
            seq_id = cur.lastrowid
            conn.commit()
    with store._LOCK, store.connect() as conn:
        for st in campaign.steps:
            conn.execute(
                "INSERT INTO sequence_steps (sequence_id, step_order, channel, "
                "template, delay_days) VALUES (?,?,?,?,?)",
                (seq_id, st.order, st.channel, st.label, st.delay_days))
        conn.commit()
    return seq_id


def sequence_id(campaign: Campaign) -> Optional[int]:
    row = store.fetch_one("SELECT id FROM sequences WHERE name=?", (campaign.name,))
    return row["id"] if row else None


def step_for(campaign: Campaign, order: int) -> Optional[Step]:
    for st in campaign.steps:
        if st.order == order:
            return st
    return None


def for_sequence(sequence_id_: int) -> Optional[Campaign]:
    """Campaña dueña de una secuencia de la base (por nombre)."""
    row = store.fetch_one("SELECT name FROM sequences WHERE id=?", (sequence_id_,))
    if not row:
        return None
    for c in _ALL.values():
        if c.name == row["name"]:
            return c
    return None


# ------------------------------------------------- Amplemarket (motor de envío)
def amplemarket_sequence_id(campaign: Campaign) -> str:
    """Id de la secuencia que envía. Vive en kv porque la secuencia se crea en la
    web app: la API de Amplemarket no puede crearla."""
    return store.kv_get(f"campaign:{campaign.slug}:amplemarket_sequence_id", "").strip()


def plantilla_verificada(campaign: Campaign) -> str:
    """Fecha en que una persona confirmó que la plantilla lleva los campos.

    Es una constancia, no una verificación: `dynamic_fields_used` solo enumera
    los campos nativos de Amplemarket, así que `{{ gancho }}` y
    `{{ link_invitacion }}` no se pueden comprobar por API ni ahora ni nunca. Lo
    que sí se puede es que quien abrió la secuencia lo deje firmado con fecha,
    para que el aviso no siga preguntando lo mismo cada vez que se abre la
    pantalla. Se guarda junto al id de la secuencia: si la secuencia cambia, la
    constancia se cae con ella.
    """
    return store.kv_get(f"campaign:{campaign.slug}:plantilla_verificada", "").strip()


def by_amplemarket_sequence(sequence_id: str) -> Optional[Campaign]:
    """Campaña dueña de una secuencia de Amplemarket.

    El webhook es de la CUENTA, no de la campaña: por la misma URL entran las
    respuestas de las 20 secuencias de Platzi (las de Carlin, las de Victoria,
    las plantillas de ejemplo). Sin este mapeo, una respuesta del desayuno queda
    registrada sin campaña y el reporte del test no la cuenta; peor, una
    respuesta de otra secuencia se vería igual que una del desayuno.
    """
    sid = (sequence_id or "").strip()
    if not sid:
        return None
    for c in _ALL.values():
        if amplemarket_sequence_id(c) == sid:
            return c
    return None


def liquid_spec(campaign: Campaign) -> list[dict]:
    """La secuencia lista para armarla en la web app de Amplemarket.

    Las constantes de la campaña (fecha, lugar, anfitrión) quedan como texto; lo
    que cambia por persona queda como variable Liquid. Es lo que hay que copiar
    y pegar en cada etapa, porque la API no puede crear la secuencia.
    """
    import re
    constantes = {
        "evento": campaign.event_name,
        "fecha_evento": _es_fecha(campaign.event_day),
        "fecha_cierre": _es_fecha(campaign.cutoff_day),
        "remitente": campaign.sender_name,
        **campaign.blanks(),
    }
    tok = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

    def convertir(texto: str) -> str:
        def _sub(m):
            k = m.group(1)
            if k in constantes and constantes[k]:
                return constantes[k]
            if k in LIQUID:
                return "{{ " + LIQUID[k] + " }}"
            return m.group(0)          # sin resolver: se ve y se corrige
        return tok.sub(_sub, texto or "")

    liquid_ok = set(LIQUID.values())

    def pendientes(texto: str) -> list[str]:
        """Solo lo que quedó sin resolver de verdad. Las variables Liquid son
        intencionales: las rellena Amplemarket con lo que mandamos por lead."""
        return sorted({k for k in tok.findall(texto) if k not in liquid_ok})

    out = []
    for s in campaign.steps:
        subject, body = convertir(s.subject), convertir(s.body)
        out.append({
            "order": s.order,
            "channel": s.channel,
            "label": s.label,
            "delay_days": s.delay_days,
            "subject": subject,
            "body": body,
            "pendiente": pendientes(subject + "\n" + body),
        })
    return out


def lead_fields() -> list[str]:
    """Variables que la plantilla de Amplemarket tiene que nombrar."""
    return sorted(set(LIQUID.values()))


# ----------------------------------------------------------------- preflight
def preflight(campaign: Campaign) -> dict:
    """Qué falta para poder enviar. Se mira ANTES de aprobar, no después."""
    problems: list[str] = []
    for k in campaign.missing():
        problems.append(f"falta confirmar «{k}» (kv: campaign:{campaign.slug}:{k})")

    # Los asientos de LinkedIn son de la cuenta, no de la campaña: los tres VP
    # firman todo lo que salga. Un asiento caído se ve como 'rate_limited' sin
    # explicación, así que se dice aquí y no cuando ya falló.
    problemas_asientos = seats.preflight()
    problems.extend(problemas_asientos)

    today = datetime.now().date()
    if today > campaign.cutoff_day:
        problems.append(f"pasó el corte ({campaign.cutoff_day.isoformat()}): "
                        "no se generan toques nuevos")

    return {
        "slug": campaign.slug,
        "ok": not problems,
        "problems": problems,
        "blanks": campaign.blanks(),
        "seats": seats.resumen(),
        "seats_ok": not problemas_asientos,
        "days_to_event": (campaign.event_day - today).days,
    }
