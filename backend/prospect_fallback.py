"""Decisores con respaldo: Apollo primero, Amplemarket cuando Apollo no alcanza.

Apollo es la fuente principal porque revela correo en la misma llamada. Pero
en el lote del 13-oct-2026 dejó 73 de 380 cuentas (19%) sin un solo perfil, y
cuando su cuenta se cae (créditos, plan, rate limit) el job entero se detenía.
Amplemarket entra en tres casos:

  · Apollo no encontró a nadie en la cuenta.
  · Apollo encontró uno y faltan perfiles para llegar a TOP_N.
  · La cuenta de Apollo está caída: se deja de llamarla y sigue Amplemarket.

## Lo que Amplemarket da y lo que cuesta

La búsqueda (`/people/search`) no cobra y trae LinkedIn, cargo y empresa. El
correo sale de `enrich`, que cobra 0,5 del find + 1 del revelado, **los dos del
pozo de correo** (ver `am_budget`). Con ese pozo en cero (402 verificado el
13-oct-2026) los perfiles de Amplemarket entran solo con LinkedIn, que ya
alimenta el motor de invitaciones. Cuando se recargue y se actualice el saldo
en `am_budget`, el revelado se activa solo.

## El filtro es el mismo que el de Apollo

Sin filtro, la búsqueda de Amplemarket devuelve a cualquiera con el dominio en
su historial: para mundodigital.net trajo un «Seller» en Argentina. Por eso
cada perfil pasa por `apollo_prospect._area` (fuera si es «otro») y por la
misma comparación de raíz de dominio que usa Apollo para la empresa.
"""
from __future__ import annotations

import am_budget
import apollo_budget
import apollo_prospect as apollo
import contacts
from telefonos_am import _cargar_cliente

TOP_N = apollo.TOP_N

# Del ICP hacia afuera; nunca «cualquiera de la empresa», que es el tramo que
# trae vendedores y jefes de almacén.
_BUSQUEDAS = (
    dict(person_departments=["Human Resources", "Information Technology",
                             "Engineering & Technical", "Revenue",
                             "Senior Leadership"],
         person_seniorities=["C-Suite", "VP", "Director", "Head", "Manager"]),
    dict(person_seniorities=["C-Suite", "VP", "Director", "Head"]),
)

_COSTO_CORREO_AM = am_budget.COSTO_FIND + am_budget.COSTO_CORREO


class Circuito:
    """Qué proveedores siguen vivos durante un job.

    Un error de cuenta no se arregla en la empresa siguiente: reintentarlo en
    cada una solo quema tiempo y, en el caso del 402, ensucia el parte. Cada
    job masivo lleva su propio circuito; una consulta suelta usa uno nuevo.
    """

    def __init__(self) -> None:
        self.apollo = True
        self.apollo_motivo = ""
        self.am_busqueda = True
        self.am_correo = True
        self.am_motivo = ""

    def cortar_apollo(self, motivo: str) -> None:
        self.apollo, self.apollo_motivo = False, motivo

    def vivo(self) -> bool:
        return self.apollo or self.am_busqueda

    def estado(self) -> dict:
        return {"apollo": self.apollo, "apollo_motivo": self.apollo_motivo,
                "amplemarket_busqueda": self.am_busqueda,
                "amplemarket_correo": self.am_correo,
                "amplemarket_motivo": self.am_motivo}


def amplemarket_configurado() -> bool:
    return _cargar_cliente().configured()


def amplemarket_configurado() -> bool:
    return _cargar_cliente().configured()


def _limpiar_dominio(d: str) -> str:
    d = (d or "").strip().lower().replace("https://", "").replace("http://", "")
    return d.removeprefix("www.").split("/")[0]


def _misma_empresa(dominio: str, persona: dict) -> bool:
    """La empresa actual del perfil debe tener la misma raíz de dominio. Si
    Amplemarket no trae sitio web, no hay cómo refutarlo: la búsqueda ya fue
    por dominio."""
    web = _limpiar_dominio((persona.get("company") or {}).get("website") or "")
    if not web:
        return True
    return apollo._domain_root(web) == apollo._domain_root(dominio)


def _candidatos_am(am, dominio: str, excluir: set[str],
                   circuito: Circuito) -> list[dict]:
    vistos, cands = set(excluir), []
    for kw in _BUSQUEDAS:
        try:
            gente = am.search_people([dominio], **kw)
        except am.AmpleError as e:
            if "402" in str(e) or "401" in str(e) or "429" in str(e):
                circuito.am_busqueda = False
                circuito.am_motivo = str(e)[:160]
            return cands
        for p in gente:
            li = (p.get("linkedin_url") or "").strip().rstrip("/")
            slug = contacts._norm_li(li)
            titulo = p.get("title") or ""
            if not slug or slug in vistos:
                continue
            vistos.add(slug)
            area = apollo._area(titulo)
            if area == "otro" or not _misma_empresa(dominio, p):
                continue
            cands.append({"nombre": p.get("name") or "", "cargo": titulo,
                          "area": area, "score": apollo._score(titulo),
                          "linkedin": li, "email": "", "celular": "",
                          "validacion": "amplemarket: perfil sin correo",
                          "fuente": "amplemarket"})
        if len(cands) >= TOP_N:
            break
    return cands


def _revelar_correos(am, perfiles: list[dict], circuito: Circuito) -> int:
    """Revela el correo de los perfiles de Amplemarket si el pozo alcanza.
    Devuelve cuántos correos llegaron."""
    if not perfiles or not circuito.am_correo:
        return 0
    saldo = am_budget.status()
    if saldo["updated_at"] and \
            saldo["pozos"]["correo"]["disponibles"] < _COSTO_CORREO_AM * len(perfiles):
        circuito.am_correo = False
        circuito.am_motivo = "pozo de correo de Amplemarket sin saldo (am_budget)"
        return 0
    try:
        res = am.enrich_people([{"linkedin_url": p["linkedin"]} for p in perfiles],
                               reveal_email=True, reveal_phone=False, max_wait=90)
    except am.AmpleError as e:
        if "402" in str(e) or "insufficient_credits" in str(e):
            circuito.am_correo = False
            circuito.am_motivo = "Amplemarket 402: pozo de correo agotado"
        return 0
    # Emparejar por slug de LinkedIn, nunca por posición (ver telefonos_am).
    por_slug = {contacts._norm_li(p["linkedin"]): p for p in perfiles}
    llegaron = 0
    for r in res:
        p = por_slug.get(contacts._norm_li(r.get("linkedin_url") or ""))
        correo = ((r.get("result") or {}).get("email") or "").strip()
        if p is not None and correo:
            p["email"] = correo
            p["validacion"] = "amplemarket: correo revelado"
            llegaron += 1
    am_budget.consume(correo=len(perfiles) * am_budget.COSTO_FIND
                      + llegaron * am_budget.COSTO_CORREO)
    return llegaron


def find_decision_makers(company: str = "", domain: str = "",
                         circuito: Circuito | None = None) -> dict:
    """Mismo contrato que `apollo_prospect.find_decision_makers`, más `fuente`
    en cada perfil y el estado del circuito."""
    circuito = circuito or Circuito()
    dominio = _limpiar_dominio(domain)
    perfiles: list[dict] = []
    nombre = company
    nota = ""
    error = ""

    if circuito.apollo and not apollo_budget.has_budget():
        circuito.cortar_apollo("presupuesto local de Apollo en 0")
    if circuito.apollo:
        r = apollo.find_decision_makers(company=company, domain=dominio)
        if r.get("ok"):
            perfiles = [{**p, "fuente": "apollo"} for p in r.get("profiles", [])]
            dominio = _limpiar_dominio(r.get("domain") or dominio)
            nombre = r.get("company") or nombre
            nota = r.get("note", "")
        else:
            error = r.get("error", "")
            if apollo.is_account_error(error):
                circuito.cortar_apollo(error[:160])

    faltan = TOP_N - len(perfiles)
    if faltan > 0 and dominio and circuito.am_busqueda:
        am = _cargar_cliente()
        if am.configured():
            ya = {contacts._norm_li(p.get("linkedin", "")) for p in perfiles} - {""}
            cands = apollo._select_order(_candidatos_am(am, dominio, ya, circuito))
            extra = [{k: v for k, v in c.items() if k != "score"}
                     for c in cands[:faltan]]
            _revelar_correos(am, extra, circuito)
            perfiles += extra
            if extra:
                nota = f"{len(extra)} perfil(es) completados con Amplemarket."
            if extra:
                nota = f"{len(extra)} perfil(es) completados con Amplemarket."

    if perfiles:
        error = ""
    elif not error:
        nota = nota or "Sin perfiles del ICP ni en Apollo ni en Amplemarket."
    return {"ok": not error, "error": error, "company": nombre or dominio,
            "domain": dominio, "profiles": perfiles, "note": nota,
            "circuito": circuito.estado()}
