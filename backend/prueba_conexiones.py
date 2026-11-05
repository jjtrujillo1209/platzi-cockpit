"""Prueba de invitaciones SIN NOTA, una por perfil de VP, con trazabilidad.

Para qué sirve: comprobar de punta a punta que una invitación sale de verdad
desde la cuenta del VP correcto y que queda registrada sola. Sin nota a
propósito: así la prueba mide el canal (sesión, cupo, asiento, registro) y no el
copy, que se prueba aparte.

## A quién le llega

Solo a contactos que ya son de ese perfil (`contacts.li_seat`), en etapa `nuevo`,
con LinkedIn, sin ninguna actividad previa de LinkedIn y sin bandera roja de
HubSpot. Un perfil no invita a los contactos de otro ni en una prueba: quien
invita es el único que después puede escribirle.

## Qué queda guardado

  · `jobs_log`   — un job por asiento, con sus parámetros (incluido el asiento)
  · `<run>-events.jsonl` — una línea por perfil visitado, con estado y detalle
  · `activities` — `invite_sent` por contacto, con run_id y el asiento en meta
  · `contacts.stage` — pasa a «contactado» solo, por la actividad

La ingesta la hace `sync.sync_contacts()`, la misma que corre el copiloto cada
15 minutos: la prueba no escribe actividades por su cuenta, para que lo que se
verifique sea el camino real y no un atajo.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import seats
import store
import sync
from config import RESULTS_DIR
from jobs import manager
from runs import read_jsonl


def candidatos(seat_id: str, n: int) -> list[dict]:
    """Contactos aptos de ESE perfil. Los más recientes primero."""
    return store.fetch_all(
        "SELECT c.id, c.nombre, c.empresa, c.cargo, c.linkedin, c.hubspot_verdict "
        "FROM contacts c "
        "WHERE c.li_seat=? AND c.linkedin<>'' AND c.stage='nuevo' "
        "  AND COALESCE(c.hubspot_verdict,'') <> 'rojo' "
        "  AND NOT EXISTS (SELECT 1 FROM activities a "
        "                  WHERE a.contact_id=c.id AND a.channel='linkedin') "
        "  AND NOT EXISTS (SELECT 1 FROM approval_queue q "
        "                  WHERE q.contact_id=c.id AND q.status IN "
        "                        ('pending','blocked','approved','sent','confirmed')) "
        "ORDER BY c.id DESC LIMIT ?", (seat_id, n))


def elegidos(seat_id: str, ids: list[int]) -> list[dict]:
    """Contactos concretos, siempre comprobando que son de ESE asiento."""
    marks = ",".join("?" for _ in ids)
    return store.fetch_all(
        f"SELECT id, nombre, empresa, cargo, linkedin, hubspot_verdict FROM contacts "
        f"WHERE id IN ({marks}) AND li_seat=? AND linkedin<>''", (*ids, seat_id))


def plan(por_asiento: int, solo: str = "", ids: list[int] | None = None) -> list[dict]:
    fuera = []
    for s in seats.en_uso():
        if not s.activo or (solo and s.id != solo):
            continue
        b = seats.budget(s)
        cabe = min(por_asiento, b["invites"]["remaining"])
        fuera.append({
            "seat": s, "budget": b,
            "cupo_libre": b["invites"]["remaining"],
            "contactos": (elegidos(s.id, ids) if ids
                          else (candidatos(s.id, cabe) if cabe > 0 else [])),
        })
    return fuera


def enviar(por_asiento: int, espera: float = 300.0, solo: str = "",
           ids: list[int] | None = None) -> dict:
    """Lanza un job por asiento y espera a que terminen. Devuelve la traza."""
    resultado = {"jobs": [], "problemas": []}
    for fila in plan(por_asiento, solo, ids):
        s, lote = fila["seat"], fila["contactos"]
        if not lote:
            resultado["problemas"].append(
                f"{s.etiqueta}: sin candidatos o sin cupo (libre {fila['cupo_libre']})")
            continue
        listo = seats.asegurar_envio(s)
        if not listo.get("ok"):
            resultado["problemas"].append(f"{s.etiqueta}: {listo.get('error')}")
            continue

        urls = [c["linkedin"] for c in lote]
        # messages=None y sin `with_notes`: el runner pulsa «Enviar sin nota».
        opts = {"daily_cap": len(urls), "headless": True, "cookies": s.cookies,
                "seat": s.id, "prueba": "conexiones-sin-nota",
                # El asiento decide: Patricia tiene Sales Navigator, así que sus
                # prospectos se guardan ahí en la misma visita al perfil.
                "sales_nav": s.sales_nav}
        if s.cdp:
            opts["cdp_endpoint"] = s.cdp
        job = manager.launch_linkedin(urls, opts)
        seats.marcar_arranque(s)
        resultado["jobs"].append({"run_id": job.job_id, "seat": s.id,
                                  "nombre": s.etiqueta, "lote": lote})

    # Esperar a que los subprocesos terminen antes de ingerir.
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        vivos = [j for j in resultado["jobs"]
                 if (manager.get(j["run_id"]) or None) and manager.get(j["run_id"]).status == "running"]
        if not vivos:
            break
        time.sleep(3)

    # backfill_activities() es la que lee los JSONL y crea actividades;
    # sync_contacts() solo importa contactos. Llamar a la segunda dejaba la
    # prueba sin rastro en la base y parecía que el envío no se había registrado.
    resultado["ingesta"] = sync.backfill_activities()
    return resultado


def traza(run_id: str) -> list[dict]:
    """Lo que quedó escrito de un envío: evento del motor + actividad en la base."""
    eventos = [e for e in read_jsonl(RESULTS_DIR / f"{run_id}-events.jsonl")
               if e.get("idx", 0) != 0]
    fuera = []
    for e in eventos:
        act = store.fetch_one(
            "SELECT a.id, a.type, a.ts, a.meta_json, c.nombre, c.empresa, c.stage, c.li_seat "
            "FROM activities a JOIN contacts c ON c.id=a.contact_id "
            "WHERE a.run_id=? AND c.linkedin=? LIMIT 1", (run_id, e.get("url", "")))
        fuera.append({"url": e.get("url", ""), "estado": e.get("status", ""),
                      "detalle": e.get("detail", ""), "actividad": act})
    return fuera


def _ids(txt: str) -> list[int] | None:
    v = [int(x) for x in (txt or "").replace(" ", "").split(",") if x.isdigit()]
    return v or None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1, help="invitaciones por perfil")
    ap.add_argument("--enviar", action="store_true", help="sin esto solo muestra a quién")
    ap.add_argument("--seat", default="", help="solo este asiento")
    ap.add_argument("--ids", default="", help="ids de contacto concretos, separados por coma")
    a = ap.parse_args()
    store.init()

    if not a.enviar:
        print(f"SIMULACIÓN · {a.n} invitación(es) sin nota por perfil\n")
        for f in plan(a.n, a.seat, _ids(a.ids)):
            s, b = f["seat"], f["budget"]
            print(f"  {s.etiqueta}  (cupo libre hoy: {f['cupo_libre']} de {b['invites']['cap']})")
            for c in f["contactos"]:
                print(f"     → {c['nombre'][:30]:<30} {c['empresa'][:26]:<26} "
                      f"{(c['cargo'] or '')[:24]:<24} hs={c['hubspot_verdict'] or '—'}")
                print(f"       {c['linkedin']}")
            if not f["contactos"]:
                print("     (sin candidatos o sin cupo)")
        raise SystemExit(0)

    res = enviar(a.n, solo=a.seat, ids=_ids(a.ids))
    for p in res["problemas"]:
        print("PROBLEMA:", p)
    for j in res["jobs"]:
        print(f"\n=== {j['nombre']} · run {j['run_id']} ===")
        for t in traza(j["run_id"]):
            act = t["actividad"]
            marca = "✓" if act else "·"
            print(f"  {marca} {t['estado']:<14} {t['detalle'][:22]:<22} {t['url']}")
            if act:
                meta = json.loads(act["meta_json"] or "{}")
                print(f"      actividad #{act['id']} {act['type']} {act['ts']} "
                      f"· asiento {meta.get('seat') or '—'} · stage ahora: {act['stage']}")
    print("\ningesta:", res["ingesta"])
