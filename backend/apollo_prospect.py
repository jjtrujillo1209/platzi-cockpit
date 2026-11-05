"""Alias: el motor de decisores vive en platzi_core/apollo_prospect.py, compartido
con la nube (platzi-cockpit-nube). `import apollo_prospect` devuelve ese mismo módulo."""
import sys
from pathlib import Path

_RAIZ = str(Path(__file__).resolve().parents[2])
if _RAIZ not in sys.path:
    sys.path.append(_RAIZ)

from platzi_core import apollo_prospect as _m  # noqa: E402

sys.modules[__name__] = _m
