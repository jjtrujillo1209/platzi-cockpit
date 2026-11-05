"""Trazabilidad de la campaña: la cadena completa, contacto por contacto.

El envío lo hace Amplemarket y el registro ocurre en una página que tampoco es
nuestra, así que el día del evento la pregunta «¿este presidente llegó por el
outreach?» no se puede contestar mirando ninguna de las dos herramientas. Solo
se puede contestar acá, uniendo las seis huellas que sí controlamos:

  1. `attribution_links`  el código propio del contacto (existe = tiene link)
  2. `approval_queue`     el gancho que aprobó una persona (canal amplemarket)
  3. `activities.sent`    la inscripción que Amplemarket aceptó
  4. `activities.click`   el clic en /r/{code} — la señal más caliente del flujo
  5. `activities.reply`   la respuesta que reportó el webhook
  6. `rsvp`               el registro con correo y celular verificados

Un contacto puede tener 1 y 2 y quedarse ahí: eso significa que se preparó y
nadie lo inscribió. Distinguir «no le escribimos» de «le escribimos y no
contestó» es todo el propósito de esta pantalla, porque las dos se ven igual en
un tablero que solo cuenta totales.

Nota sobre los saltados: un lead que Amplemarket rechaza (duplicado, lista de
exclusión, contactado hace poco) queda en `failed`, NO en `sent`. Por eso acá
«inscrito» sale de la actividad y no del estado de la cola: la cola dice qué
quisimos hacer, la actividad dice qué pasó.
"""
from __future__ import annotations

import amplemarket_seq as ams
import campaigns
import links
import store
from config import PUBLIC_BASE_URL

CANAL = "amplemarket"


def _por_contacto(sql: str, params: tuple = ()) -> dict[int, dict]:
    return {r["contact_id"]: r for r in store.fetch_all(sql, params)}


def reporte(campaign: campaigns.Campaign) -> dict:
    slug = campaign.slug
    links.init()

    # Universo: todo el que tenga link propio de esta campaña, más todo el que
    # esté en la cola. No se parte de `contacts.campaign` porque esa etiqueta se
    # escribe al inscribir — quien está preparado y sin inscribir no la tiene.
    base = store.fetch_all(
        "SELECT c.id AS contact_id, c.nombre, c.empresa, c.cargo, c.email, "
        "       c.linkedin, c.celular, c.stage, c.campaign, "
        "       c.hubspot_verdict, l.code, l.created_at AS link_at "
        "FROM contacts c "
        "LEFT JOIN attribution_links l ON l.contact_id = c.id AND l.campaign = ? "
        "WHERE l.code IS NOT NULL "
        "   OR c.id IN (SELECT contact_id FROM approval_queue WHERE channel = ?) "
        "ORDER BY c.empresa, c.nombre", (slug, CANAL))

    # El último registro de la cola por contacto: si se preparó dos veces, lo
    # que manda es el intento vigente, no el primero.
    cola = _por_contacto(
        "SELECT contact_id, MAX(id) AS approval_id, status, draft_body AS gancho, "
        "       blocked_reasons, model, created_at, decided_at "
        "FROM approval_queue WHERE channel = ? GROUP BY contact_id", (CANAL,))

    inscritos = _por_contacto(
        "SELECT contact_id, MIN(ts) AS ts, "
        "       json_extract(meta_json,'$.sequence_name') AS secuencia "
        "FROM activities WHERE type = 'sent' "
        "  AND json_extract(meta_json,'$.engine') = 'amplemarket' "
        "  AND (json_extract(meta_json,'$.campaign') = ? "
        "       OR json_extract(meta_json,'$.campaign') IS NULL) "
        "GROUP BY contact_id", (slug,))

    clics = _por_contacto(
        "SELECT contact_id, COUNT(*) AS n, MAX(ts) AS ts FROM activities "
        "WHERE type = 'click' AND json_extract(meta_json,'$.campaign') = ? "
        "GROUP BY contact_id", (slug,))

    respuestas = _por_contacto(
        "SELECT contact_id, COUNT(*) AS n, MAX(ts) AS ts FROM activities "
        "WHERE type = 'reply' AND json_extract(meta_json,'$.engine') = 'amplemarket' "
        "GROUP BY contact_id")

    registros = _por_contacto(
        "SELECT contact_id, estado, attribution_code, updated_at AS ts "
        "FROM rsvp WHERE campaign = ? AND contact_id IS NOT NULL", (slug,))

    filas = []
    for b in base:
        cid = b["contact_id"]
        c, i = cola.get(cid, {}), inscritos.get(cid, {})
        k, r = clics.get(cid, {}), respuestas.get(cid, {})
        g = registros.get(cid, {})
        filas.append({
            "contact_id": cid,
            "nombre": b["nombre"], "empresa": b["empresa"], "cargo": b["cargo"],
            "email": b["email"], "linkedin": b["linkedin"], "celular": b["celular"],
            "stage": b["stage"], "hubspot_verdict": b["hubspot_verdict"],
            "codigo": b["code"] or "",
            "link": f"{PUBLIC_BASE_URL}/r/{b['code']}" if b["code"] else "",
            "gancho": c.get("gancho") or "",
            "cola_estado": c.get("status") or "",
            "cola_razones": c.get("blocked_reasons") or "",
            "modelo": c.get("model") or "",
            "inscrito_ts": i.get("ts") or "",
            "secuencia": i.get("secuencia") or "",
            "clics": k.get("n") or 0, "clic_ts": k.get("ts") or "",
            "respuestas": r.get("n") or 0, "respuesta_ts": r.get("ts") or "",
            "registro": g.get("estado") or "",
            "registro_ts": g.get("ts") or "",
            # El registro cuenta como atribuido solo si llegó con el código: sin
            # eso es un asistente que no se puede amarrar al outreach.
            "registro_atribuido": bool(g.get("attribution_code")),
        })

    def cuenta(pred) -> int:
        return sum(1 for f in filas if pred(f))

    embudo = {
        "con_link": cuenta(lambda f: bool(f["codigo"])),
        "preparados": cuenta(lambda f: bool(f["gancho"])),
        "bloqueados": cuenta(lambda f: f["cola_estado"] == "blocked"),
        "pendientes": cuenta(lambda f: f["cola_estado"] == "pending"),
        "aprobados": cuenta(lambda f: f["cola_estado"] == "approved"),
        "inscritos": cuenta(lambda f: bool(f["inscrito_ts"])),
        "saltados": cuenta(lambda f: f["cola_estado"] == "failed"),
        "con_clic": cuenta(lambda f: f["clics"] > 0),
        "con_respuesta": cuenta(lambda f: f["respuestas"] > 0),
        "registrados": cuenta(lambda f: f["registro"] == "confirmado"),
        "registrados_atribuidos": cuenta(
            lambda f: f["registro"] == "confirmado" and f["registro_atribuido"]),
    }

    seq_id = campaigns.amplemarket_sequence_id(campaign)
    pf = ams.preflight(seq_id)
    secreto = store.kv_get("amplemarket_webhook_secret", "").strip()
    # La constancia va atada al id: si alguien cambia la secuencia, lo firmado
    # sobre la anterior deja de valer y el aviso vuelve a salir.
    firmada = campaigns.plantilla_verificada(campaign)
    fecha, _, id_firmado = firmada.partition("|")
    verificada = fecha if seq_id and id_firmado == seq_id else ""

    return {
        "campaign": {"slug": slug, "name": campaign.name,
                     "event_day": campaign.event_day.isoformat(),
                     "cutoff_day": campaign.cutoff_day.isoformat()},
        "preflight_campana": campaigns.preflight(campaign),
        "secuencia": {
            "id": seq_id,
            "name": (pf.get("sequence") or {}).get("name", ""),
            "status": (pf.get("sequence") or {}).get("status", ""),
            "url": (pf.get("sequence") or {}).get("url", ""),
            "dynamic_fields_used": pf.get("dynamic_fields_used", []),
            "ok": pf["ok"], "problems": pf["problems"],
            # Firmada la plantilla, el aviso deja de salir: ya se contestó.
            "warnings": [] if verificada else pf.get("warnings", []),
            "verificada": verificada,
            "campos_esperados": campaigns.lead_fields(),
        },
        # Sin webhook registrado la cadena se corta después del clic: las
        # respuestas se quedan en Amplemarket y el pipeline nunca las ve.
        "webhook": {
            "configurado": bool(secreto),
            "url": f"{PUBLIC_BASE_URL}/api/webhooks/amplemarket/{secreto}" if secreto else "",
        },
        "embudo": embudo,
        "filas": filas,
    }
