"""Arma el universo de empresas candidatas para duplicar la base del cockpit.

Estándar heredado (idéntico al de la base actual de 569):
  · >300 empleados REALES (dato de Amplemarket, no el inflado de Apollo)
  · ICP Platzi Business (industrias tier 1/2, anti-ICP fuera)
  · LATAM completo MENOS Venezuela, Cuba y Nicaragua (alcance fijado por el
    usuario el 2026-08-24; la base original era solo Colombia + México)
  · fuera de HubSpot
  · 1 decision maker por empresa, con correo CORPORATIVO

Lotes de origen, en orden de costo creciente:
  A · prospeccion-2000: calificadas (>=300 empl reales + DM) que nunca se
      revelaron. Ya traen dm1/dm2 con LinkedIn → solo falta revelar correo.
  B · prospeccion-2000: >=300 empl reales pero dm_count=0 → falta buscar DM.
  C · apollo_raw sin explotar: >=300 (Apollo) que pasan el ICP y no estaban en
      la supresión de HubSpot → falta validar empresa + buscar DM.
  D · cec-listado: enterprise «fuera del CRM» que YA tienen correo corporativo
      revelado → solo falta re-verificar HubSpot.
  E · reserva_empresas_icp_no_hubspot: las 3.937 calificadas sobrantes del mismo
      build (AR/CL/BR/PE/MX/CO) → falta validar empresa + buscar DM + revelar.

Salida: data/candidatos.csv
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAIZ = HERE.parent.parent                      # …/PLATZI
P2000 = RAIZ / "prospeccion-2000"
CEC = RAIZ / "cec-listado"
COCKPIT_DB = RAIZ / "cockpit" / "backend" / "platzi_cockpit.db"

MIN_EMPLEADOS = 300

# LATAM menos los tres excluidos por el usuario. Se listan en positivo (y no como
# «todo menos VE/CU/NI») para que un país fuera de la región que se cuele en el
# universo de Apollo no entre por descuido.
PAISES = {
    "colombia", "mexico", "méxico", "brazil", "brasil", "argentina", "chile",
    "peru", "perú", "ecuador", "uruguay", "paraguay", "bolivia",
    "costa rica", "panama", "panamá", "guatemala", "honduras", "el salvador",
    "dominican republic", "republica dominicana", "república dominicana",
    "puerto rico", "belize", "belice",
}
PAISES_EXCLUIDOS = {"venezuela", "cuba", "nicaragua"}


def pais_ok(pais: str) -> bool:
    p = (pais or "").strip().lower()
    return p in PAISES and p not in PAISES_EXCLUIDOS

FIELDS = [
    "fase", "necesita", "empresa", "dominio", "pais", "empleados", "industria",
    "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email",
    "dm2_nombre", "dm2_cargo", "dm2_linkedin",
]

LIBRES = (
    "gmail.", "hotmail.", "yahoo.", "outlook.", "icloud.", "live.", "msn.",
    "aol.", "protonmail.", "proton.me", "gmx.", "mail.ru", "yandex.",
    "me.com", "mac.com", "email.com", "terra.com", "prodigy.net",
)


def es_corporativo(email: str) -> bool:
    e = (email or "").strip().lower()
    return bool(e) and "@" in e and not any(f in e.split("@", 1)[1] for f in LIBRES)


def entero(v) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


def _norm_dom(d: str) -> str:
    d = (d or "").strip().lower()
    for p in ("https://", "http://", "www."):
        if d.startswith(p):
            d = d[len(p):]
    return d.split("/")[0].strip()


def _raiz_dom(d: str) -> str:
    """Raíz comparable: acme.com ≡ acme.com.co ≡ acme.mx → 'acme'."""
    d = _norm_dom(d)
    return d.split(".")[0] if d else ""


# --------------------------- lo que ya está dentro ---------------------------
def ya_en_cockpit() -> tuple[set[str], set[str], set[str]]:
    """(emails, linkedins, raíces de dominio) que ya viven en el cockpit."""
    con = sqlite3.connect(COCKPIT_DB)
    emails, lis, doms = set(), set(), set()
    for email, li, empresa in con.execute(
            "SELECT email, linkedin, empresa FROM contacts"):
        if email:
            emails.add(email.strip().lower())
            doms.add(_raiz_dom(email.split("@", 1)[1]))
        if li:
            lis.add(li.strip().lower().rstrip("/"))
    con.close()
    return emails, lis, {d for d in doms if d}


def dominios_revelados() -> set[str]:
    f = P2000 / "base_2000_revelada_calificadas.csv"
    if not f.exists():
        return set()
    return {r["dominio"] for r in csv.DictReader(f.open(encoding="utf-8"))}


# ------------------------------- lotes A y B --------------------------------
def lotes_ab(revelados: set[str]) -> list[dict]:
    f = P2000 / "base_2000_validada_amplemarket.csv"
    base = {r["dominio"]: r for r in csv.DictReader(
        (P2000 / "base_2000_empresas_icp_platzi_no_hubspot.csv").open(encoding="utf-8"))}
    out = []
    for r in csv.DictReader(f.open(encoding="utf-8")):
        dom = r["dominio"]
        if r.get("am_encontrada") != "sí" or dom in revelados:
            continue
        if entero(r.get("am_empleados")) < MIN_EMPLEADOS:
            continue
        meta = base.get(dom, {})
        if not pais_ok(meta.get("pais", "") or "Colombia"):
            continue
        tiene_dm = bool((r.get("dm1_linkedin") or "").strip())
        out.append({
            "fase": "A" if tiene_dm else "B",
            "necesita": "revelar" if tiene_dm else "buscar_dm+revelar",
            "empresa": r.get("am_nombre") or r.get("empresa", ""),
            "dominio": dom,
            "pais": meta.get("pais", ""),
            "empleados": r.get("am_empleados", ""),
            "industria": r.get("am_industria", ""),
            "dm_nombre": r.get("dm1_nombre", ""),
            "dm_cargo": r.get("dm1_cargo", ""),
            "dm_linkedin": (r.get("dm1_linkedin") or "").strip(),
            "dm_email": "",
            "dm2_nombre": r.get("dm2_nombre", ""),
            "dm2_cargo": r.get("dm2_cargo", ""),
            "dm2_linkedin": (r.get("dm2_linkedin") or "").strip(),
        })
    return out


# --------------------------------- lote C -----------------------------------
def lote_c() -> list[dict]:
    """Apollo sin explotar: pasa el mismo filtro ICP de build_base.py."""
    sys.path.insert(0, str(P2000))
    import build_base as bb  # noqa: E402  (usa los filtros originales, sin copiarlos)

    usados = set()
    for nombre in ("base_2000_empresas_icp_platzi_no_hubspot.csv",
                   "reserva_empresas_icp_no_hubspot.csv"):
        for r in csv.DictReader((P2000 / nombre).open(encoding="utf-8")):
            usados.add(r["apollo_id"])

    dom_hs, nom_hs = bb.cargar_supresion()
    raices_hs = {bb.raiz(d) for d in dom_hs if bb.raiz(d)}

    out = []
    with (P2000 / "data" / "apollo_raw.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("id") in usados:
                continue
            if entero(o.get("estimated_num_employees")) < MIN_EMPLEADOS:
                continue
            if not pais_ok(o.get("country") or ""):
                continue
            dom = bb.norm_domain(o.get("primary_domain") or "") or \
                bb.norm_domain(o.get("website_url") or "")
            if not dom or bb.motivo_anti_icp(o, bb.texto_empresa(o)):
                continue
            nn = bb.norm_name(o.get("name") or "")
            if (dom in dom_hs
                    or (bb.raiz(dom) in raices_hs and len(bb.raiz(dom)) >= 3)
                    or (nn and len(nn) > 4 and nn in nom_hs)):
                continue
            out.append({
                "fase": "C", "necesita": "validar_empresa+buscar_dm+revelar",
                "empresa": o.get("name") or "", "dominio": dom,
                "pais": o.get("country") or "",
                "empleados": o.get("estimated_num_employees") or "",
                "industria": o.get("industry") or "",
                "dm_nombre": "", "dm_cargo": "", "dm_linkedin": "", "dm_email": "",
                "dm2_nombre": "", "dm2_cargo": "", "dm2_linkedin": "",
            })
    return out


# --------------------------------- lote D -----------------------------------
def lote_d() -> list[dict]:
    """CEC enterprise fuera del CRM, con correo corporativo ya revelado."""
    f = CEC / "CEC_enterprise_solo_con_correo.csv"
    if not f.exists():
        return []
    estado = {}
    fe = CEC / "CEC_estado_hubspot.csv"
    if fe.exists():
        for r in csv.DictReader(fe.open(encoding="utf-8-sig")):
            estado[r["empresa"]] = r.get("estado_platzi", "")
    out = []
    for r in csv.DictReader(f.open(encoding="utf-8-sig")):
        if estado.get(r["empresa"]) != "fuera del CRM":
            continue
        if entero(r.get("empleados")) < MIN_EMPLEADOS:
            continue
        if not es_corporativo(r.get("dm_email", "")):
            continue
        out.append({
            "fase": "D", "necesita": "solo_verificar",
            "empresa": r.get("empresa", ""), "dominio": _norm_dom(r.get("dominio", "")),
            "pais": "Colombia", "empleados": r.get("empleados", ""),
            "industria": "", "dm_nombre": r.get("dm_nombre", ""),
            "dm_cargo": r.get("dm_cargo", ""),
            "dm_linkedin": (r.get("dm_linkedin") or "").strip(),
            "dm_email": r.get("dm_email", "").strip().lower(),
            "dm2_nombre": "", "dm2_cargo": "", "dm2_linkedin": "",
        })
    return out


# --------------------------------- lote E -----------------------------------
def lote_e() -> list[dict]:
    """La reserva del mismo build: ya pasó el ICP y la supresión de HubSpot del
    11-ago, pero nunca se validó contra Amplemarket. Viene ordenada por
    puntaje_icp, así que se conserva ese orden: lo mejor se revela primero."""
    f = P2000 / "reserva_empresas_icp_no_hubspot.csv"
    if not f.exists():
        return []
    out = []
    for r in csv.DictReader(f.open(encoding="utf-8")):
        if entero(r.get("empleados")) < MIN_EMPLEADOS or not pais_ok(r.get("pais", "")):
            continue
        out.append({
            "fase": "E", "necesita": "validar_empresa+buscar_dm+revelar",
            "empresa": r.get("empresa", ""), "dominio": _norm_dom(r.get("dominio", "")),
            "pais": r.get("pais", ""), "empleados": r.get("empleados", ""),
            "industria": r.get("industria", ""),
            "dm_nombre": "", "dm_cargo": "", "dm_linkedin": "", "dm_email": "",
            "dm2_nombre": "", "dm2_cargo": "", "dm2_linkedin": "",
        })
    return out


def main() -> int:
    emails_in, lis_in, doms_in = ya_en_cockpit()
    revelados = dominios_revelados()
    print(f"Cockpit hoy: {len(emails_in)} correos · {len(lis_in)} LinkedIn · "
          f"{len(doms_in)} raíces de dominio")

    filas = lotes_ab(revelados) + lote_d() + lote_c() + lote_e()

    # dedup: por dominio, por LinkedIn del DM y por correo; nada que ya esté dentro
    vistos_dom, vistos_li, final = set(), set(), []
    fuera = Counter()
    for r in filas:
        rd = _raiz_dom(r["dominio"])
        li = r["dm_linkedin"].lower().rstrip("/")
        if not rd:
            fuera["sin dominio"] += 1
            continue
        if rd in doms_in:
            fuera["dominio ya en el cockpit"] += 1
            continue
        if rd in vistos_dom:
            fuera["dominio duplicado en el lote"] += 1
            continue
        if li and (li in lis_in or li in vistos_li):
            fuera["DM duplicado"] += 1
            continue
        if r["dm_email"] and r["dm_email"] in emails_in:
            fuera["correo ya en el cockpit"] += 1
            continue
        vistos_dom.add(rd)
        if li:
            vistos_li.add(li)
        final.append(r)

    out = HERE / "data" / "candidatos.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(final)

    print(f"\nDescartados: {dict(fuera)}")
    print(f"Candidatas por lote: {dict(Counter(r['fase'] for r in final))}")
    print(f"TOTAL candidatas: {len(final)} → {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
