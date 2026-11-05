"""Alias: el cruce con HubSpot vive en platzi_core/hubspot_check.py, compartido
con la nube (platzi-cockpit-nube). `import hubspot_check` devuelve ese mismo módulo."""
import sys
from pathlib import Path

_RAIZ = str(Path(__file__).resolve().parents[2])
if _RAIZ not in sys.path:
    sys.path.append(_RAIZ)

from platzi_core import hubspot_check as _m  # noqa: E402

sys.modules[__name__] = _m
