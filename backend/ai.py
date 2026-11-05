"""Capa Claude del copiloto: gancho por contacto, clasificación de respuestas y
briefing diario.

Reparto de responsabilidades, a propósito:

  · El COPY lo escribe una persona y vive en `campaigns.py`. Claude no lo toca.
  · Lo único que Claude genera de cero es el **gancho**: una frase específica de
    la empresa que va dentro del correo. Es la parte que no se puede escribir a
    mano cincuenta veces y la única superficie donde puede alucinar.
  · Todo lo generado pasa por `guard.check()` antes de llegar a la cola.

Modelos: Haiku para volumen (ganchos, clasificación) y Sonnet para el briefing.
Si Claude falla o no hay API key, se cae a un gancho neutro marcado `fallback` —
nunca se frena la cadencia por un problema del modelo, pero queda visible.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv

import guard
from config import ENV_FILE

load_dotenv(ENV_FILE)

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"

_client = None


def _get_client():
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------- render
_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(plantilla: str, tokens: dict[str, str]) -> str:
    """Sustitución determinista. Un token sin dato se queda como `{{token}}`
    a propósito: así la compuerta lo ve y frena el mensaje, en vez de mandar
    un hueco en blanco."""
    def _sub(m: re.Match) -> str:
        val = (tokens.get(m.group(1)) or "").strip()
        return val if val else m.group(0)
    return _TOKEN_RE.sub(_sub, plantilla or "")


# --------------------------------------------------------------- gancho
_HOOK_RULES = (
    "Escribe UNA sola frase (máximo 25 palabras) que conecte a esta compañía "
    "con el problema de formar equipos técnicos. Va DENTRO de un mensaje ya "
    "escrito, así que NO saluda, NO se despide, NO invita a nada y NO menciona "
    "el motivo del contacto: solo aterriza por qué esta compañía en particular.\n"
    "Prohibido: cifras, porcentajes, rankings, nombres de estudios, halagos "
    "vacíos ('empresa líder', 'referente del sector') y cualquier dato que no "
    "esté en la ficha que te doy. Si no tienes nada específico que decir, "
    "escribe exactamente: SIN_GANCHO"
)

_HOOK_NEUTRO = ("Su compañía está en el grupo de empresas que estamos contactando "
                "este año.")


def draft_hook(contact: dict, campaign) -> dict:
    """Una frase específica de la empresa. Devuelve {"hook", "model"}."""
    client = _get_client()
    if client is None:
        return {"hook": _HOOK_NEUTRO, "model": "fallback"}

    system = f"{campaign.brand}\n\n{campaign.style}\n\n{_HOOK_RULES}"
    user = (f"Ficha de la compañía y la persona:\n"
            f"- Persona: {contact.get('nombre', '')}\n"
            f"- Cargo: {contact.get('cargo', '')}\n"
            f"- Compañía: {contact.get('empresa', '')}\n"
            f"- Sector: {contact.get('sector', '')}\n"
            f"- Ciudad: {contact.get('ciudad', '')}\n\n"
            "Escribe la frase.")
    try:
        resp = client.messages.create(
            model=HAIKU, max_tokens=200, system=system,
            messages=[{"role": "user", "content": user}])
        if resp.stop_reason == "refusal":
            raise RuntimeError("refusal")
        text = next(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        return {"hook": _HOOK_NEUTRO, "model": "fallback"}

    if not text or "SIN_GANCHO" in text:
        return {"hook": _HOOK_NEUTRO, "model": "fallback"}
    # El gancho es texto libre: si trae una cifra o se sale de tono, se descarta
    # entero en vez de intentar arreglarlo.
    if guard.cifras(text) or guard.vetadas(text):
        return {"hook": _HOOK_NEUTRO, "model": "fallback"}
    return {"hook": text, "model": HAIKU}


# --------------------------------------------------------------- mensaje
def build_message(contact: dict, campaign, step, link: str = "", seat=None) -> dict:
    """Copy aprobado + gancho + link → mensaje listo, ya auditado.

    `seat` es el perfil de LinkedIn que firma este toque (None = lo firma el
    equipo). Importa para el copy: cuando el que manda la invitación es el
    propio anfitrión del desayuno, el paso trae una versión en primera persona.

    Devuelve {"subject", "body", "model", "blocked", "reasons"}. Nunca lanza:
    un fallo de Claude degrada el gancho, no tumba la cadencia.
    """
    hook, model = "", ""
    if step.needs_hook:
        h = draft_hook(contact, campaign)
        hook, model = h["hook"], h["model"]

    firma_anfitrion = bool(seat is not None and getattr(seat, "es_anfitrion", False))
    tokens = campaign.tokens(contact, link=link, hook=hook,
                             firma=getattr(seat, "nombre", "") if seat else "")
    subject = render(step.subject, tokens)
    body = render(step.cuerpo(firma_anfitrion), tokens)

    # El link solo se exige en los pasos cuyo copy lo lleva. Ojo con la
    # tentación de escribir `link in body`: con link vacío eso siempre da
    # verdadero y bloquea la invitación de LinkedIn, que no lleva link.
    reasons = guard.check(body, step.channel, subject=subject, link=link,
                          require_link="{{link}}" in step.cuerpo(firma_anfitrion))
    return {"subject": subject, "body": body, "model": model,
            "blocked": bool(reasons), "reasons": reasons}


# --------------------------------------------------------------- respuestas
INTENTS = ("interesado", "no_interesado", "fuera_oficina", "referido", "otro")


def classify_reply(contact: dict, subject: str, snippet: str,
                   campaign=None) -> dict:
    """Clasifica la intención de una respuesta y sugiere cómo contestar."""
    client = _get_client()
    if client is None:
        return {"intent": "otro", "summary": snippet[:150],
                "suggested_response": "", "model": "fallback"}
    marca = campaign.brand if campaign else _MARCA_GENERICA
    try:
        resp = client.messages.create(
            model=HAIKU, max_tokens=800,
            system=(f"{marca}\n\nClasificas respuestas de prospectos a nuestro outreach "
                    "y sugieres una respuesta breve en español, tratando de usted. "
                    "Intenciones posibles: interesado (quiere saber más / acepta), "
                    "no_interesado (rechaza), fuera_oficina (auto-reply OOO), "
                    "referido (redirige a otra persona de su organización), otro. "
                    "Un 'hable con mi asistente' o 'eso lo ve Talento' es REFERIDO, "
                    "y es una buena noticia: esa persona suele ser quien decide."),
            messages=[{"role": "user", "content":
                       f"Prospecto: {contact.get('nombre','')} — {contact.get('cargo','')} "
                       f"en {contact.get('empresa','')}\n"
                       f"Asunto: {subject}\nRespuesta recibida: {snippet}"}],
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": list(INTENTS)},
                    "summary": {"type": "string",
                                "description": "resumen de 1 línea de lo que dijo"},
                    "suggested_response": {"type": "string",
                                           "description": "borrador breve de respuesta "
                                           "(vacío si es OOO o rechazo definitivo)"},
                },
                "required": ["intent", "summary", "suggested_response"],
                "additionalProperties": False,
            }}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("refusal")
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        data["model"] = HAIKU
        return data
    except Exception:
        return {"intent": "otro", "summary": snippet[:150],
                "suggested_response": "", "model": "fallback"}


_MARCA_GENERICA = (
    "Platzi para Empresas forma equipos técnicos de compañías de LATAM en IA, "
    "datos, desarrollo y liderazgo.")


# --------------------------------------------------------------- briefing
def daily_briefing(context: dict) -> str:
    """Briefing matinal a partir del resumen que arma el scheduler."""
    client = _get_client()
    if client is None:
        return ""
    try:
        resp = client.messages.create(
            model=SONNET, max_tokens=1200,
            system=("Eres el copiloto de outreach de Platzi para Empresas. Escribe un "
                    "briefing matinal corto en español (máx 200 palabras) para Juan "
                    "José: qué pasó ayer, qué hay pendiente hoy (aprobaciones, "
                    "respuestas, llamadas) y alertas del sistema. Directo, en viñetas, "
                    "sin relleno. Si hay respuestas de interesados, destácalas primero, "
                    "y si hay una campaña con fecha de evento cerca, di cuántos días "
                    "quedan. "
                    "TEXTO PLANO: nada de markdown (sin #, sin **, sin tablas); usa '•' "
                    "para viñetas y MAYÚSCULAS solo para los títulos de sección."),
            messages=[{"role": "user", "content":
                       "Datos del sistema:\n" + json.dumps(context, ensure_ascii=False,
                                                           indent=2, default=str)}],
        )
        if resp.stop_reason == "refusal":
            return ""
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception:
        return ""
