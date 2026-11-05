"""Compuerta: qué NO puede salir hacia un presidente.

Se corre sobre el mensaje YA renderizado, justo antes de meterlo a la cola de
aprobación. Un borrador que no pasa no se descarta en silencio: entra a la cola
marcado, con el motivo, para que la persona lo vea y decida.

El criterio de «hueco sin rellenar» está calibrado en
`PLATZI/correos-hubspot/plantilla.py` contra un lote real donde 118 cuerpos
llegaron con el marcador puesto; aquí se reusa el mismo, más las reglas propias
de este canal (límite de LinkedIn, link de atribución, cifras inventadas).
"""
from __future__ import annotations

import re

# Huecos sin rellenar. Instrucciones inequívocas, no comillas legítimas.
_MARCA_CLARA = [
    re.compile(r"\bPEGAR AQU[ÍI]\b", re.I),
    re.compile(r"\bEST[ÁA] VAC[ÍI]A\b", re.I),
    re.compile(r"\bFALTA (LA|EL) \w+", re.I),
    re.compile(r"\b(TBD|FIXME|XXXX+)\b"),
    re.compile(r"\bTODO\b"),                 # en mayúsculas; "todo" en español, no
    re.compile(r"\{\{[^}]{0,120}\}\}"),      # token que no se resolvió
    re.compile(r"<<[^>]{0,200}>>"),
    re.compile(r"\[\[[^\]]{0,200}\]\]"),
]
# «…» son comillas normales en español: solo cuentan si dentro hay una orden.
_ENTRE_COMILLAS = re.compile(r"«([^»]{0,200})»")
_GRITADO = re.compile(r"[A-ZÁÉÍÓÚÑ]{2,}(\s+[A-ZÁÉÍÓÚÑ0-9]{2,}){1,}")

# Cifras que suenan a dato duro. La guía de outbound tiene TODAS las cifras
# bloqueadas sin fuente, así que un porcentaje o un "3x" en un correo en frío es
# casi siempre una alucinación del modelo.
_CIFRA_DURA = [
    re.compile(r"\d+\s?%"),
    re.compile(r"\b\d+\s?x\b", re.I),
    re.compile(r"\b\d{1,3}(\.\d{3})+\b"),           # 10.000
    re.compile(r"\b(millones?|billones?)\b", re.I),
    re.compile(r"\b(estudio|encuesta|informe|reporte)\s+(de|del|reciente)", re.I),
]

# Palabras que rompen el tono acordado para este segmento.
_VETADAS = [
    re.compile(r"\bvender(le|te)?\b", re.I),
    re.compile(r"\bestimad[oa]\b", re.I),
    re.compile(r"\bcordialmente\b", re.I),
    re.compile(r"\bpromoci[óo]n\b", re.I),
    re.compile(r"\bdescuento\b", re.I),
    re.compile(r"[!¡]"),
    re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"),   # emojis
]

LINKEDIN_INVITE_MAX = 300
# El gancho es una frase que se inyecta en la plantilla de Amplemarket. Si se
# alarga, deja de ser un gancho y se come el correo que escribió una persona.
HOOK_MAX = 300


def _anota(fuera: list[str], trozo: str) -> None:
    trozo = (trozo or "").strip()
    if trozo and trozo not in fuera:
        fuera.append(trozo[:120])


def huecos(texto: str) -> list[str]:
    """Trozos que parecen un hueco sin rellenar."""
    fuera: list[str] = []
    for rx in _MARCA_CLARA:
        for m in rx.finditer(texto or ""):
            _anota(fuera, m.group(0))
    for m in _ENTRE_COMILLAS.finditer(texto or ""):
        if _GRITADO.search(m.group(1)):
            _anota(fuera, m.group(0))
    return fuera


def cifras(texto: str) -> list[str]:
    fuera: list[str] = []
    for rx in _CIFRA_DURA:
        for m in rx.finditer(texto or ""):
            _anota(fuera, m.group(0))
    return fuera


def vetadas(texto: str) -> list[str]:
    fuera: list[str] = []
    for rx in _VETADAS:
        for m in rx.finditer(texto or ""):
            _anota(fuera, m.group(0))
    return fuera


def check(body: str, channel: str, *, subject: str = "",
          link: str = "", require_link: bool = False) -> list[str]:
    """Devuelve los motivos por los que este mensaje no debería salir.

    Lista vacía = apto. El orden importa: primero lo que rompe al destinatario
    (huecos, largo), después lo que rompe la credibilidad (cifras, tono).
    """
    razones: list[str] = []
    texto = f"{subject}\n{body}" if subject else (body or "")

    for h in huecos(texto):
        razones.append(f"hueco sin rellenar: {h}")

    if channel == "linkedin_invite":
        n = len(body or "")
        if n > LINKEDIN_INVITE_MAX:
            razones.append(f"la nota de invitación mide {n} caracteres y LinkedIn "
                           f"corta en {LINKEDIN_INVITE_MAX}")

    if channel == "hook":
        n = len(body or "")
        if n > HOOK_MAX:
            razones.append(f"el gancho mide {n} caracteres y el tope es {HOOK_MAX}")
        if "\n\n" in (body or ""):
            razones.append("el gancho trae más de un párrafo: va dentro de un "
                           "correo ya escrito, tiene que ser una sola frase")

    if require_link:
        if not link:
            razones.append("no hay link de atribución para este contacto")
        elif link not in (body or ""):
            razones.append("el link de atribución no quedó en el cuerpo — "
                           "sin él el registro no se puede atribuir")

    for c in cifras(texto):
        razones.append(f"cifra sin fuente: {c}")
    for v in vetadas(texto):
        razones.append(f"palabra o signo fuera de tono: {v}")

    return razones
