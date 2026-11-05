"""Rutas y configuración del Platzi Cockpit.

Todo es relativo a la ubicación del cockpit, con override por variables de
entorno. No reescribe nada del sistema existente: solo apunta a él.
"""
from __future__ import annotations

import os
from pathlib import Path

# cockpit/backend/config.py -> cockpit/ -> prospector-flow/
BACKEND_DIR = Path(__file__).resolve().parent
COCKPIT_DIR = BACKEND_DIR.parent
PROSPECTOR_DIR = COCKPIT_DIR.parent
AI_PROGRAMAS_DIR = PROSPECTOR_DIR.parent.parent  # .../Ai programas


def _env_path(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else default


# Motores existentes que reutilizamos como subprocesos.
BULK_CONNECT_DIR = _env_path("PLATZI_BULK_DIR", PROSPECTOR_DIR / "bulk_connect")
RUNNER_PY = BULK_CONNECT_DIR / "runner.py"
EMAIL_SENDER_PY = BULK_CONNECT_DIR / "email_sender.py"
FOLLOWUP_RUNNER_PY = BACKEND_DIR / "followup_runner.py"
RESULTS_DIR = BULK_CONNECT_DIR / "results"
TEMPLATES_DIR = BULK_CONNECT_DIR / "templates"

# Datos. Todo vive dentro de PLATZI/: el cockpit no lee ni escribe bases de
# otros proyectos.
DATA_DIR = COCKPIT_DIR / "data"

# Vía de importación por CSV, heredada y apagada a propósito: los contactos de
# Platzi entran por POST /api/contacts (prospeccion-2000/subir_a_cockpit.py) y
# la fuente de verdad operativa es store.db. Si el archivo no existe,
# contacts._load_base() devuelve [] y sync.sync_contacts() no importa nada —
# que es exactamente lo que queremos. Apunta PLATZI_DEDUP_CSV a un CSV real
# solo si quieres reactivar esa importación.
DEDUP_CSV = _env_path("PLATZI_DEDUP_CSV", DATA_DIR / "contactos_platzi.csv")

# Eventos del Event Webhook de SendGrid (open/click/bounce/spam). Base propia:
# webhooks._ensure_table() la crea sola en el primer evento.
TRACKING_DB = _env_path("PLATZI_TRACKING_DB", DATA_DIR / "tracking_platzi.db")
COOKIES_PATH = _env_path(
    "PLATZI_LI_COOKIES",
    PROSPECTOR_DIR / "data" / "cookies" / "linkedin_session.json",
)

# Intérprete para los subprocesos de los motores (runner LinkedIn, email_sender):
# necesitan Playwright, pandas y requests. El `python3` del sistema NO los trae,
# así que si PYTHON cae a "python3" el subproceso muere al instante con
# ModuleNotFoundError y el job se cuelga sin explicación. Orden de preferencia:
#   1. PLATZI_PYTHON explícito (override).
#   2. venv propio de Platzi, si existe.
#   3. venv de prospector-flow: de ahí se copiaron los motores, así que
#      trae exactamente las deps que necesitan.
#   4. python3 del sistema (último recurso; probablemente sin deps).
def _tiene_playwright(exe: str) -> bool:
    """¿Este intérprete puede correr los motores?

    Se pregunta de verdad en vez de asumirlo por la ruta. Un venv que existe
    pero perdió playwright (o que nunca lo tuvo) hace que TODO subproceso muera
    al instante con ModuleNotFoundError, y desde la UI eso se ve como un job que
    se cuelga sin explicación: el síntoma más caro de diagnosticar de este
    sistema. Cuesta unos 100 ms una sola vez por proceso.
    """
    import subprocess
    try:
        return subprocess.run([exe, "-c", "import playwright"],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _resolve_python() -> str:
    override = os.environ.get("PLATZI_PYTHON", "").strip()
    if override:
        return override          # explícito: se respeta sin preguntar

    import shutil
    candidates = [PROSPECTOR_DIR / "venv" / "bin" / "python"]
    # Localiza la raíz "Ai programas" por nombre entre los ancestros (AI_PROGRAMAS_DIR
    # queda mal calculado cuando el cockpit vive a otra profundidad que el original)
    # y busca ahí el venv de prospector-flow, de donde se copiaron los motores.
    for anc in [BACKEND_DIR, *BACKEND_DIR.parents]:
        if anc.name == "Ai programas":
            candidates.append(anc / "01-ACTIVO" / "prospector-flow" / "venv" / "bin" / "python")
            break
    # Intérpretes del sistema, por si los venv ya no existen: en esta máquina
    # playwright vive en el python3.12 de Homebrew.
    for nombre in ("python3.12", "python3.13", "python3.11", "python3"):
        ruta = shutil.which(nombre)
        if ruta:
            candidates.append(Path(ruta))

    existentes = [c for c in candidates if c.exists()]
    for c in existentes:
        if _tiene_playwright(str(c)):
            return str(c)
    # Ninguno trae playwright: se devuelve el primero que exista (mismo
    # comportamiento de antes) y el motor dirá qué falta en su propio error.
    return str(existentes[0]) if existentes else "python3"


PYTHON = _resolve_python()

# Carga el .env de PLATZI/ (ANTHROPIC_API_KEY, APOLLO_API_KEY, SMTP_*, ...).
ENV_FILE = PROSPECTOR_DIR / ".env"

# Identidad del remitente. El correo sale del buzón real de Platzi: por la regla
# VP a VP, un VP presta su perfil de LinkedIn pero nunca su buzón, así que el
# remitente de correo es siempre el equipo, aunque el anfitrión sea otro.
DEFAULT_FROM_EMAIL = "jtrujillo@platzi.com"
DEFAULT_FROM_NAME = "Juan José Trujillo · Platzi para Empresas"
DEFAULT_REPLY_TO = "jtrujillo@platzi.com"
DEFAULT_BCC = ""

# URL pública del cockpit (túnel ngrok de run.sh). La usan el webhook de
# SendGrid y los links de atribución de links.py; sin ella los links salen
# relativos y no se pueden clicar desde un correo.
PUBLIC_BASE_URL = os.environ.get(
    "PLATZI_PUBLIC_BASE_URL",
    "https://" + os.environ.get("PLATZI_NGROK_DOMAIN",
                                "fiddling-shown-anaerobic.ngrok-free.dev"),
).rstrip("/")

# Carpeta donde el cockpit guarda artefactos de jobs lanzados desde la UI.
COCKPIT_RUNS_DIR = RESULTS_DIR  # mismo lugar: aparece en el Historial unificado
