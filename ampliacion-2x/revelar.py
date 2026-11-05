"""Revela 1 decision maker COMPLETO por empresa candidata.

Completo = las tres cosas a la vez, exigidas por el usuario el 2026-08-24:
  correo CORPORATIVO · TELÉFONO · perfil de LINKEDIN
Un DM al que le falte cualquiera de las tres no entra en la base.

Etapas (cada una cachea en data/estado.json → todo es reanudable):
  1 firmografia · lotes C y E: find_company() para conocer los empleados REALES.
    Se cae toda empresa por debajo de MIN_EMPLEADOS (el dato de Apollo infla).
  2 dms         · lotes B, C y E: people/search priorizando el ICP → hasta 2 DMs.
  3 emails      · enrich del DM #1 revelando correo Y teléfono; la empresa cuyo
    DM #1 no quede completo se reintenta con el DM #2.
  4 telefonos   · rescate de los perfiles que se revelaron sin teléfono: se
    re-enriquecen POR CORREO (más barato que por LinkedIn) pidiendo solo el
    teléfono, sin volver a pagar el correo.
  5 validar     · deliverability de los correos corporativos hallados.

Costo en créditos de Amplemarket: enrich = 0.5 al encontrar +1 por correo +1 por
teléfono. people/search y companies/find no cobran revelado. La validación cuesta
1 por correo. Medido sobre la base madre: de los DMs con correo corporativo, el
93% también trae teléfono, así que exigir los tres cuesta ~7% de rendimiento.

Uso:
  python3 revelar.py --etapa todo [--limite N] [--validar]
  python3 revelar.py --etapa emails --limite 50      # pasada corta de prueba
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import amplemarket as am
from candidatos import MIN_EMPLEADOS, entero, es_corporativo

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CANDIDATOS = DATA / "candidatos.csv"
ESTADO = DATA / "estado.json"
SALIDA = DATA / "revelados.csv"

LOTE_ENRICH = 25          # leads por petición de enrichment
POLL_MAX = 900            # s de espera por lote: 25 perfiles tardan minutos, no 120 s
LOTE_VALIDA = 50          # correos por petición de validación
CONCURRENCIA = 8          # peticiones de enrichment en vuelo a la vez
HILOS = 6                 # hilos para firmografía y búsqueda de DMs

_LOCK = threading.Lock()  # el estado lo tocan varios hilos

OUT_FIELDS = [
    "fase", "empresa", "dominio", "pais", "empleados", "industria",
    "dm_nombre", "dm_cargo", "dm_linkedin", "dm_email", "dm_email_estado",
    "dm_email_catch_all", "dm_telefono", "origen_dm",
]


def es_completo(d: dict) -> bool:
    """Los tres datos obligatorios. Se aplica igual al elegir DM, al contar el
    avance contra la meta y al escribir la salida: un solo criterio, un solo
    lugar donde cambiarlo."""
    return bool(d) and es_corporativo(d.get("email", "")) and bool(d.get("telefono"))


# ------------------------------- estado -------------------------------------
def cargar_estado() -> dict:
    base = {"firmografia": {}, "dms": {}, "emails": {}, "validacion": {},
            "peticiones": {}}
    if ESTADO.exists():
        base.update(json.loads(ESTADO.read_text(encoding="utf-8")))
    return base


def guardar_estado(st: dict) -> None:
    tmp = ESTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ESTADO)


def candidatos() -> list[dict]:
    return list(csv.DictReader(CANDIDATOS.open(encoding="utf-8")))


# --------------------------- 1) firmografía (C) -----------------------------
def etapa_firmografia(rows: list[dict], st: dict, limite: int) -> None:
    """Empleados REALES de cada empresa. El dato de Apollo infla: de la base
    madre, solo ~44% de las que Apollo daba >=300 lo eran de verdad."""
    pend = [r for r in rows
            if r["fase"] in ("C", "E") and r["dominio"] not in st["firmografia"]]
    if limite:
        pend = pend[:limite]
    print(f"[1/5] firmografía · pendientes: {len(pend)}")

    def uno(r: dict) -> None:
        dom = r["dominio"]
        try:
            c = am.find_company(domain=dom)
        except Exception as e:                                  # noqa: BLE001
            print(f"   {dom}: error {e.__class__.__name__}")
            return
        with _LOCK:
            st["firmografia"][dom] = {
                "found": bool(c.get("found")),
                "empleados": entero(c.get("estimated_number_of_employees")),
                "industria": c.get("industry") or "",
                "nombre": c.get("name") or "",
            }

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        for i in range(0, len(pend), 200):
            list(pool.map(uno, pend[i:i + 200]))
            with _LOCK:
                guardar_estado(st)
                vivas = sum(1 for d in st["firmografia"].values()
                            if d["found"] and d["empleados"] >= MIN_EMPLEADOS)
            print(f"   {min(i + 200, len(pend))}/{len(pend)} · con >={MIN_EMPLEADOS} "
                  f"empleados reales: {vivas}")
    guardar_estado(st)


# ----------------------------- 2) DMs (B y C) -------------------------------
def etapa_dms(rows: list[dict], st: dict, limite: int) -> None:
    pend = []
    for r in rows:
        if r["fase"] not in ("B", "C", "E") or r["dominio"] in st["dms"]:
            continue
        if r["fase"] in ("C", "E"):
            f = st["firmografia"].get(r["dominio"])
            if not f or not f["found"] or f["empleados"] < MIN_EMPLEADOS:
                continue
        pend.append(r)
    if limite:
        pend = pend[:limite]
    print(f"[2/5] búsqueda de DMs · pendientes: {len(pend)}")

    def uno(r: dict) -> None:
        dom = r["dominio"]
        try:
            people = am.top_decision_makers(dom, n=2)
        except Exception as e:                                  # noqa: BLE001
            print(f"   {dom}: error {e.__class__.__name__}")
            return
        with _LOCK:
            st["dms"][dom] = [{"nombre": p.get("name") or "",
                               "cargo": p.get("title") or "",
                               "linkedin": p.get("linkedin_url") or ""} for p in people]

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        for i in range(0, len(pend), 200):
            list(pool.map(uno, pend[i:i + 200]))
            with _LOCK:
                guardar_estado(st)
                con = sum(1 for v in st["dms"].values() if v)
            print(f"   {min(i + 200, len(pend))}/{len(pend)} · empresas con DM: {con}")
    guardar_estado(st)


def _duo(r: dict, st: dict) -> list[dict]:
    """Los (hasta 2) DMs de una empresa, vengan del CSV o de la búsqueda."""
    if r["fase"] == "A":
        out = [{"nombre": r["dm_nombre"], "cargo": r["dm_cargo"],
                "linkedin": r["dm_linkedin"]}]
        if r["dm2_linkedin"]:
            out.append({"nombre": r["dm2_nombre"], "cargo": r["dm2_cargo"],
                        "linkedin": r["dm2_linkedin"]})
        return out
    return st["dms"].get(r["dominio"], [])


# ------------------------- 3) correos (A, B, C) -----------------------------
def _guardar_resultados(res: list[dict], st: dict) -> int:
    """Vuelca los resultados de un enrichment al estado. Dos cuidados:
    · solo se cachean los leads que Amplemarket resolvió (si uno no vino se deja
      sin cachear para reintentarlo, en vez de darlo por vacío);
    · se FUSIONA con lo que ya hubiera. El rescate de teléfonos pide el perfil
      sin revelar correo, así que pisar el registro borraría el correo ya pagado.
    Cuando el lead entró por correo, Amplemarket no repite el linkedin_url arriba
    sino dentro de `result`: se toman los dos para no perder la clave."""
    nuevos = 0
    for x in res:
        p = x.get("result") or {}
        li = x.get("linkedin_url") or p.get("linkedin_url")
        if not li or x.get("status") not in ("enriched", "not_found", "failed"):
            continue
        prev = st["emails"].get(li, {})
        tels = p.get("phone_numbers") or []
        st["emails"][li] = {
            "email": ((p.get("email") or p.get("work_email") or "").strip().lower()
                      or prev.get("email", "")),
            "nombre": p.get("name") or prev.get("nombre", ""),
            "cargo": p.get("title") or prev.get("cargo", ""),
            "telefono": (tels[0].get("number") if tels else prev.get("telefono", "")),
            "status": x.get("status"),
        }
        nuevos += 1
    return nuevos


def _repoll_pendientes(st: dict) -> None:
    """Re-consulta peticiones ya enviadas que quedaron sin leer. Los créditos de
    esas peticiones YA se gastaron: recuperarlas es gratis, re-enviarlas no."""
    pend = dict(st.get("peticiones", {}))
    if not pend:
        return
    print(f"   re-leyendo {len(pend)} peticiones previas sin resolver…")

    def uno(req_id: str) -> None:
        try:
            d = am._poll(f"/people/enrichment-requests/{req_id}", max_wait=POLL_MAX)
        except Exception as e:                                  # noqa: BLE001
            print(f"   petición {req_id}: error {e.__class__.__name__}")
            return
        with _LOCK:
            hubo = _guardar_resultados(d.get("results") or [], st)
            if hubo or d.get("status") == "completed":
                st["peticiones"].pop(req_id, None)

    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as pool:
        list(pool.map(uno, list(pend)))
    guardar_estado(st)


def _enrich_lote(leads: list[dict], st: dict, por_correo: bool = False) -> None:
    """Envía un lote y espera su resultado. La petición se anota en el estado
    ANTES de esperarla, para que un corte no pierda créditos ya pagados."""
    if por_correo:
        entrada = [{"email": l["email"]} for l in leads]
    else:
        entrada = [{"linkedin_url": l["linkedin"]} for l in leads]
    body = {"leads": entrada, "reveal_email": not por_correo,
            "reveal_phone_numbers": True}
    req_id = None
    try:
        r = am._post("/people/enrichment-requests", body)
        if r.status_code not in (200, 202):
            print(f"   lote de {len(leads)}: HTTP {r.status_code} {r.text[:120]}")
            return
        j = r.json()
        req_id = j.get("id")
        with _LOCK:
            st["peticiones"][str(req_id)] = [l["linkedin"] for l in leads]
            guardar_estado(st)
        href = (j.get("_links", {}).get("self", {}).get("href")
                or f"/people/enrichment-requests/{req_id}")
        d = am._poll(href, max_wait=POLL_MAX)
    except Exception as e:                                      # noqa: BLE001
        print(f"   lote de {len(leads)}: error {e.__class__.__name__}")
        return
    with _LOCK:
        _guardar_resultados(d.get("results") or [], st)
        if d.get("status") == "completed":
            st["peticiones"].pop(str(req_id), None)
        guardar_estado(st)


def _empresas_listas(rows: list[dict], st: dict) -> int:
    """Empresas que ya tienen un DM con correo corporativo."""
    n = 0
    for r in rows:
        if r["fase"] == "D":
            n += es_completo({"email": r["dm_email"], "telefono": r.get("dm_telefono", "")})
            continue
        if any(es_completo(st["emails"].get(d["linkedin"], {}))
               for d in _duo(r, st) if d["linkedin"]):
            n += 1
    return n


def etapa_emails(rows: list[dict], st: dict, limite: int, objetivo: int = 0) -> None:
    """Dos pasadas: DM #1 de cada empresa y, si no dio correo corporativo, DM #2."""
    _repoll_pendientes(st)
    for vuelta, idx in (("DM #1", 0), ("DM #2", 1)):
        if idx == 1:
            etapa_telefonos(rows, st)
        pend = []
        for r in rows:
            if r["fase"] == "D":
                continue
            duo = _duo(r, st)
            if len(duo) <= idx or not duo[idx]["linkedin"]:
                continue
            if idx == 1:
                if es_completo(st["emails"].get(duo[0]["linkedin"], {})):
                    continue          # el #1 ya quedó completo: no se gasta en el #2
            if duo[idx]["linkedin"] in st["emails"]:
                continue
            pend.append(duo[idx])
        if limite:
            pend = pend[:limite]
        print(f"[3/5] correos {vuelta} · pendientes: {len(pend)} "
              f"(~{len(pend) * 1.5:.0f} créditos)")
        lotes = [pend[j:j + LOTE_ENRICH] for j in range(0, len(pend), LOTE_ENRICH)]
        ola = CONCURRENCIA
        with ThreadPoolExecutor(max_workers=ola) as pool:
            for k in range(0, len(lotes), ola):
                if objetivo and _empresas_listas(rows, st) >= objetivo:
                    print(f"   meta de {objetivo} empresas alcanzada · se corta el gasto")
                    break
                list(pool.map(lambda L: _enrich_lote(L, st), lotes[k:k + ola]))
                hechos = min((k + ola) * LOTE_ENRICH, len(pend))
                print(f"   {hechos}/{len(pend)} · empresas con correo corporativo: "
                      f"{_empresas_listas(rows, st)}", flush=True)
                time.sleep(1)
        guardar_estado(st)


# ------------------------- 4) rescate de teléfonos --------------------------
def etapa_telefonos(rows: list[dict], st: dict) -> None:
    """Perfiles con correo corporativo pero sin teléfono. Se re-enriquecen POR
    CORREO pidiendo solo el teléfono: no se vuelve a pagar el correo ya revelado.
    También rescata a los del lote D, que traían correo del CEC y ningún teléfono."""
    faltan = [{"linkedin": li, "email": v["email"]}
              for li, v in st["emails"].items()
              if es_corporativo(v.get("email", "")) and not v.get("telefono")]
    vistos = {f["email"] for f in faltan}
    for r in rows:
        if r["fase"] == "D" and es_corporativo(r["dm_email"]) and r["dm_email"] not in vistos:
            en_cache = st["emails"].get(r["dm_linkedin"], {})
            if not en_cache.get("telefono"):
                faltan.append({"linkedin": r["dm_linkedin"], "email": r["dm_email"]})
    print(f"[4/5] rescate de teléfonos · pendientes: {len(faltan)} "
          f"(~{len(faltan) * 1.5:.0f} créditos)")
    if not faltan:
        return
    lotes = [faltan[j:j + LOTE_ENRICH] for j in range(0, len(faltan), LOTE_ENRICH)]
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as pool:
        for k in range(0, len(lotes), CONCURRENCIA):
            list(pool.map(lambda L: _enrich_lote(L, st, por_correo=True),
                          lotes[k:k + CONCURRENCIA]))
            con = sum(1 for v in st["emails"].values() if es_completo(v))
            print(f"   {min((k + CONCURRENCIA) * LOTE_ENRICH, len(faltan))}/{len(faltan)} "
                  f"· perfiles completos: {con}", flush=True)
    guardar_estado(st)


# --------------------------- 5) deliverability ------------------------------
def etapa_validar(rows: list[dict], st: dict) -> None:
    correos = sorted({v["email"] for v in st["emails"].values()
                      if es_corporativo(v["email"]) and v["email"] not in st["validacion"]})
    print(f"[5/5] validación de correos · pendientes: {len(correos)} "
          f"({len(correos)} créditos)")
    for j in range(0, len(correos), LOTE_VALIDA):
        lote = correos[j:j + LOTE_VALIDA]
        try:
            res = am.validate_emails(lote)
        except Exception as e:                                  # noqa: BLE001
            print(f"   lote {j}: error {e.__class__.__name__}")
            continue
        for v in res:
            st["validacion"][v.get("email", "")] = {
                "estado": v.get("result") or v.get("status") or "",
                "catch_all": v.get("catch_all", ""),
            }
        guardar_estado(st)
        print(f"   {min(j + LOTE_VALIDA, len(correos))}/{len(correos)}")


# -------------------------------- salida ------------------------------------
def escribir(rows: list[dict], st: dict) -> dict:
    filas, stats = [], Counter()
    for r in rows:
        stats[f"candidatas_{r['fase']}"] += 1
        if r["fase"] == "D":
            tel = st["emails"].get(r["dm_linkedin"], {}).get("telefono", "")
            if es_completo({"email": r["dm_email"], "telefono": tel}):
                filas.append({**{k: r.get(k, "") for k in OUT_FIELDS if k in r},
                              "dm_email_estado": "", "dm_email_catch_all": "",
                              "dm_telefono": tel, "origen_dm": "cec"})
                stats["completos"] += 1
            else:
                stats["descartado_sin_telefono"] += 1
            continue
        elegido = None
        for pos, d in enumerate(_duo(r, st)):
            e = st["emails"].get(d["linkedin"], {})
            if es_completo(e):
                elegido = (d, e, pos)
                break
        if not elegido:
            duo = _duo(r, st)
            visto = [st["emails"].get(d["linkedin"], {}) for d in duo if d["linkedin"]]
            if any(es_corporativo(v.get("email", "")) for v in visto):
                stats["descartado_sin_telefono"] += 1
            else:
                stats["descartado_sin_correo_corporativo"] += 1
            continue
        d, e, pos = elegido
        val = st["validacion"].get(e["email"], {})
        emp = r["empleados"]
        if r["fase"] in ("C", "E"):
            emp = st["firmografia"].get(r["dominio"], {}).get("empleados", emp)
        filas.append({
            "fase": r["fase"], "empresa": r["empresa"], "dominio": r["dominio"],
            "pais": r["pais"], "empleados": emp,
            "industria": r["industria"] or st["firmografia"].get(r["dominio"], {}).get("industria", ""),
            "dm_nombre": d["nombre"] or e.get("nombre", ""),
            "dm_cargo": d["cargo"] or e.get("cargo", ""),
            "dm_linkedin": d["linkedin"], "dm_email": e["email"],
            "dm_email_estado": val.get("estado", ""),
            "dm_email_catch_all": val.get("catch_all", ""),
            "dm_telefono": e.get("telefono", ""),
            "origen_dm": f"amplemarket_dm{pos + 1}",
        })
        stats["completos"] += 1

    with SALIDA.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        w.writeheader()
        w.writerows(filas)
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--etapa", default="todo",
                    choices=["todo", "firmografia", "dms", "emails", "telefonos",
                             "validar", "salida"])
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--objetivo", type=int, default=0,
                    help="corta el gasto al llegar a N empresas con correo corporativo")
    ap.add_argument("--validar", action="store_true",
                    help="incluye la etapa de deliverability (1 crédito por correo)")
    args = ap.parse_args()

    if not am.configured():
        print("ERROR: AMPLEMARKET_API_KEY no configurada", file=sys.stderr)
        return 2

    rows, st = candidatos(), cargar_estado()
    e = args.etapa
    if e in ("todo", "firmografia"):
        etapa_firmografia(rows, st, args.limite)
    if e in ("todo", "dms"):
        etapa_dms(rows, st, args.limite)
    if e in ("todo", "emails"):
        etapa_emails(rows, st, args.limite, args.objetivo)
    if e in ("todo", "telefonos"):
        etapa_telefonos(rows, st)
    if e == "validar" or (e == "todo" and args.validar):
        etapa_validar(rows, st)

    stats = escribir(rows, st)
    print(f"\n→ {SALIDA.name}\n{json.dumps(stats, indent=1, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
