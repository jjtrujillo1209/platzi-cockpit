"""Alias: el libro de créditos vive en platzi_core/apollo_budget.py, compartido
con la nube (platzi-cockpit-nube). `import apollo_budget` devuelve ese mismo módulo;
el saldo sigue en apollo_budget.json de esta carpeta salvo que `nube.py` enchufe
el libro remoto."""
import sys
from pathlib import Path

_RAIZ = str(Path(__file__).resolve().parents[2])
if _RAIZ not in sys.path:
    sys.path.append(_RAIZ)

from platzi_core import apollo_budget as _m  # noqa: E402

sys.modules[__name__] = _m

try:
    import nube as _nube  # noqa: E402
    _nube.enchufar_libro()
except Exception as _e:  # sin nube el libro sigue siendo el JSON local
    print(f"[apollo_budget] libro local: {_e.__class__.__name__}: {_e}", file=sys.stderr)
