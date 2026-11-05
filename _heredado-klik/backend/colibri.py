"""Conector al CRM Colibri (microservicio ms-customer de Klik).

Lee la base de CLIENTES reales desde el microservicio
`GET /ms-customer/api/v1/customers` y la usa para validar que ninguna empresa
de la base de prospección ya sea cliente.

El microservicio devuelve identidad de EMPRESA (name, legalName, NIT en
legalNumberId, ciiuCode), no contactos: por eso el cruce es por **NIT** (clave
fuerte) y **razón social / nombre comercial**. El correo, teléfono y cargo se
muestran desde el lado del prospecto (no existen en el CRM).

Autenticación: header `x-authorization: <api_key>`. La key se guarda en el .env
(COLIBRI_API_KEY) igual que el resto de credenciales del cockpit; el esquema se
auto-detecta y se recuerda en COLIBRI_AUTH_SCHEME.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata

import requests

from settings import set_env_value, _read_env, _mask

DEFAULT_BASE_URL = "https://klik-ms-customer-api-307337563408.us-central1.run.app"
DEFAULT_PATH = "/ms-customer/api/v1/customers"
# Esquemas de auth que probamos, en orden. El confirmado por Klik va primero.
AUTH_SCHEMES = [
    ("x-authorization", lambda k: {"x-authorization": k}),
    ("x-api-key", lambda k: {"x-api-key": k}),
    ("api-key", lambda k: {"api-key": k}),
    ("authorization-bearer", lambda k: {"Authorization": f"Bearer {k}"}),
    ("authorization-raw", lambda k: {"Authorization": k}),
]

# Posibles nombres de campo en la respuesta (defensivo ante cambios de esquema).
_LIST_KEYS = ("data", "items", "results", "customers", "rows", "content", "records")
_NAME_KEYS = ("name", "nombre", "tradeName", "nombreComercial")
_LEGALNAME_KEYS = ("legalName", "razonSocial", "legal_name", "businessName")
_NIT_KEYS = ("legalNumberId", "nit", "documentNumber", "taxId", "identification")
_CODE_KEYS = ("code", "customerCode", "codigo")
_CIIU_KEYS = ("ciiuCode", "ciiu", "activityCode")
_ID_KEYS = ("id", "customerId", "_id", "uuid")

# Sufijos societarios (ES/EN) a eliminar al normalizar la razón social.
_LEGAL_SUFFIXES = re.compile(
    r"\b("
    r"sociedad por acciones simplificada|sociedad anonima|sociedad limitada|"
    r"sociedad|s\.?a\.?s|s\.?a|ltda|limitada|e\.?u|e\.?s\.?p|"
    r"sas|sa|inc|corp|llc|cia|compania|company|group|grupo|holding|"
    r"en comandita|s\.? en c|zomac|esp|s\.?a\.?s\.?\.?"
    r")\b\.?",
    re.I,
)


# --------------------------- normalización ------------------------------
def norm_company(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = _LEGAL_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def nit_variants(s: str) -> set[str]:
    """Variantes comparables de un NIT colombiano.

    "860.513.970-1" → {"8605139701", "860513970"} (con y sin dígito de
    verificación) para tolerar que la fuente guarde uno u otro formato.
    """
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 7:
        return set()
    out = {digits}
    had_dash = "-" in (s or "")
    if had_dash or len(digits) >= 10:
        out.add(digits[:-1])  # sin dígito de verificación
    return {v for v in out if len(v) >= 7}


def _first(d: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k]).strip()
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _extract_customer(raw: dict) -> dict:
    activo = raw.get("isActive")
    return {
        "id": _first(raw, _ID_KEYS),
        "code": _first(raw, _CODE_KEYS),
        "empresa": _first(raw, _NAME_KEYS),
        "legal_name": _first(raw, _LEGALNAME_KEYS),
        "nit": _first(raw, _NIT_KEYS),
        "ciiu": _first(raw, _CIIU_KEYS),
        "activo": bool(activo) if activo is not None else True,
    }


def _items_from_payload(payload) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in _LIST_KEYS:
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                for kk in _LIST_KEYS:
                    vv = v.get(kk)
                    if isinstance(vv, list):
                        return [x for x in vv if isinstance(x, dict)]
    return []


# --------------------------- configuración ------------------------------
def _base_url() -> str:
    env = _read_env()
    return (env.get("COLIBRI_BASE_URL") or os.environ.get("COLIBRI_BASE_URL")
            or DEFAULT_BASE_URL).rstrip("/")


def _path() -> str:
    env = _read_env()
    p = env.get("COLIBRI_PATH") or os.environ.get("COLIBRI_PATH") or DEFAULT_PATH
    return p if p.startswith("/") else "/" + p


def _api_key() -> str:
    return (_read_env().get("COLIBRI_API_KEY")
            or os.environ.get("COLIBRI_API_KEY", "")).strip()


def _saved_scheme() -> str:
    return (_read_env().get("COLIBRI_AUTH_SCHEME")
            or os.environ.get("COLIBRI_AUTH_SCHEME", "")).strip()


def _headers_for(scheme_id: str, key: str) -> dict:
    for sid, fn in AUTH_SCHEMES:
        if sid == scheme_id:
            return {"Accept": "application/json", **fn(key)}
    return {"Accept": "application/json", **AUTH_SCHEMES[0][1](key)}


def status() -> dict:
    key = _api_key()
    env = _read_env()
    return {
        "configured": bool(key),
        "masked": _mask(key),
        "base_url": _base_url(),
        "path": _path(),
        "auth_scheme": _saved_scheme(),
        "last_sync": env.get("COLIBRI_LAST_SYNC", "")
                     or os.environ.get("COLIBRI_LAST_SYNC", ""),
    }


def update(api_key: str | None = None, base_url: str | None = None,
           path: str | None = None) -> dict:
    if api_key:
        set_env_value("COLIBRI_API_KEY", api_key.strip())
    if base_url is not None and base_url.strip():
        set_env_value("COLIBRI_BASE_URL", base_url.strip().rstrip("/"))
    if path is not None and path.strip():
        set_env_value("COLIBRI_PATH", path.strip())
    _customers_cache.clear()
    return status()


# --------------------------- llamadas HTTP ------------------------------
def _request_page(key: str, scheme_id: str, take: int, page: int,
                  search: str = "", order_by: str = "", order: str = "") -> requests.Response:
    params: dict[str, str] = {"take": str(take), "page": str(page)}
    if search:
        params["search"] = search
    if order_by:
        params["orderBy"] = order_by
    if order:
        params["order"] = order
    return requests.get(_base_url() + _path(), params=params,
                        headers=_headers_for(scheme_id, key), timeout=30)


def test_connection(api_key: str | None = None) -> dict:
    """Prueba la key probando esquemas de auth en orden; guarda el que funcione."""
    key = (api_key or "").strip() or _api_key()
    if not key:
        return {"ok": False, "error": "No hay API key configurada"}

    order = list(AUTH_SCHEMES)
    saved = _saved_scheme()
    if saved:
        order.sort(key=lambda s: 0 if s[0] == saved else 1)

    last_err = "sin respuesta"
    for sid, _ in order:
        try:
            r = _request_page(key, sid, take=2, page=1)
        except requests.RequestException as e:
            last_err = f"red: {e.__class__.__name__}"
            continue
        if r.status_code == 200:
            try:
                payload = r.json()
            except ValueError:
                last_err = "respuesta no-JSON"
                continue
            items = _items_from_payload(payload)
            set_env_value("COLIBRI_AUTH_SCHEME", sid)
            return {"ok": True, "auth_scheme": sid,
                    "sample_count": len(items),
                    "sample_fields": sorted(items[0].keys()) if items else []}
        if r.status_code in (401, 403):
            last_err = f"auth {r.status_code} (esquema {sid})"
            continue
        last_err = f"HTTP {r.status_code}"
    return {"ok": False, "error": f"No autenticado · {last_err}"}


# Cache simple con TTL para no martillar el microservicio en cada validación.
_customers_cache: dict[str, object] = {}
_CACHE_TTL = 600  # 10 min


def fetch_all_customers(force: bool = False, page_size: int = 200,
                        max_pages: int = 200) -> dict:
    """Pagina TODOS los clientes del CRM. Devuelve {ok, customers, total, ...}."""
    now = time.time()
    if not force and _customers_cache.get("customers") is not None \
            and now - float(_customers_cache.get("ts", 0)) < _CACHE_TTL:
        cust = _customers_cache["customers"]
        return {"ok": True, "cached": True, "customers": cust, "total": len(cust)}  # type: ignore

    key = _api_key()
    if not key:
        return {"ok": False, "error": "No hay API key configurada", "customers": []}

    scheme = _saved_scheme()
    if not scheme:
        t = test_connection(key)
        if not t.get("ok"):
            return {"ok": False, "error": t.get("error", "auth"), "customers": []}
        scheme = t["auth_scheme"]

    out: list[dict] = []
    seen: set[str] = set()
    page = 1
    while page <= max_pages:
        try:
            r = _request_page(key, scheme, take=page_size, page=page,
                             order_by="name", order="ASC")
        except requests.RequestException as e:
            if out:
                break
            return {"ok": False, "error": f"red: {e.__class__.__name__}", "customers": []}
        if r.status_code != 200:
            if out:
                break
            return {"ok": False, "error": f"HTTP {r.status_code}", "customers": []}
        items = _items_from_payload(r.json())
        if not items:
            break
        new = 0
        for raw in items:
            c = _extract_customer(raw)
            dedup = (c["id"] or c["nit"] or c["empresa"]).lower()
            if dedup and dedup in seen:
                continue
            seen.add(dedup)
            out.append(c)
            new += 1
        if len(items) < page_size or new == 0:
            break
        page += 1

    _customers_cache["customers"] = out
    _customers_cache["ts"] = now
    set_env_value("COLIBRI_LAST_SYNC", time.strftime("%Y-%m-%dT%H:%M:%S"))
    return {"ok": True, "cached": False, "customers": out, "total": len(out), "pages": page}


# --------------------------- validación ---------------------------------
def _build_index(customers: list[dict]) -> dict:
    by_nit: dict[str, dict] = {}
    by_company: dict[str, dict] = {}
    for c in customers:
        for v in nit_variants(c["nit"]):
            by_nit.setdefault(v, c)
        for nm in (c["empresa"], c["legal_name"]):
            co = norm_company(nm)
            if co and len(co) >= 3:
                by_company.setdefault(co, c)
    return {"nit": by_nit, "company": by_company}


def validate_prospects(prospects: list[dict]) -> dict:
    """Cruza prospectos contra el CRM Colibri por NIT y razón social.

    Cada prospecto puede traer: nombre, empresa, nit, email, celular, cargo.
    Marca `ya_cliente` cuando la EMPRESA coincide por NIT o por nombre/razón
    social, e indica en `match` qué cruzó y el cliente del CRM correspondiente.
    """
    res = fetch_all_customers()
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "no se pudo leer Colibri"),
                "conflictos": 0, "results": []}
    idx = _build_index(res["customers"])

    results = []
    conflicts = 0
    sin_info = 0
    for p in prospects:
        company = norm_company(p.get("empresa", ""))
        p_nits = nit_variants(p.get("nit", ""))

        matched_on: list[str] = []
        crm = None
        for v in p_nits:
            if v in idx["nit"]:
                matched_on.append("nit")
                crm = idx["nit"][v]
                break
        if company and company in idx["company"]:
            if "nit" not in matched_on:
                matched_on.append("empresa")
            crm = crm or idx["company"][company]

        ya_cliente = bool(matched_on)
        if ya_cliente:
            conflicts += 1

        # Calidad de la ficha del prospecto (la "respectiva info" del pedido).
        faltantes = [lbl for lbl, val in (
            ("correo", p.get("email")), ("teléfono", p.get("celular") or p.get("telefono")),
            ("cargo", p.get("cargo")), ("empresa", p.get("empresa")),
        ) if not (val or "").strip()]
        if faltantes:
            sin_info += 1

        results.append({
            "id": p.get("id", ""),
            "nombre": p.get("nombre", ""),
            "empresa": p.get("empresa", ""),
            "nit": p.get("nit", ""),
            "email": p.get("email", ""),
            "celular": p.get("celular") or p.get("telefono") or "",
            "cargo": p.get("cargo", ""),
            "ya_cliente": ya_cliente,
            "match": matched_on,
            "info_faltante": faltantes,
            "crm_cliente": crm or {},
        })

    return {
        "ok": True,
        "total_prospectos": len(prospects),
        "total_clientes_crm": len(res["customers"]),
        "conflictos": conflicts,
        "nuevos": len(prospects) - conflicts,
        "sin_info_completa": sin_info,
        "cached": res.get("cached", False),
        "results": results,
    }
