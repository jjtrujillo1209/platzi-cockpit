"""Inscribir contactos en la secuencia de Amplemarket, con aprobación humana.

Amplemarket envía; el cockpit decide **a quién** y **con qué datos**. Reparto:

  cockpit      → lote, semáforo de HubSpot, gancho por compañía, link propio,
                 cola de aprobación, registro en el pipeline
  Amplemarket  → cadencia, envío de correo y LinkedIn, tareas de llamada,
                 detección de respuestas, lista de exclusión

Lo que se aprueba aquí NO es el cuerpo del mensaje: ese vive en la plantilla de
Amplemarket y no se puede leer por API. Se aprueba **la inscripción y lo que
nosotros generamos** — el gancho y el link —, que es exactamente la superficie
donde un modelo puede meter la pata.
"""
from __future__ import annotations

from datetime import datetime

import ai
import amplemarket_seq as ams
import campaigns
import guard
import hubspot_check
import links
import store
from contacts import _norm_li

CANAL = "amplemarket"


def _ya_encolado(contact_id: int, campaign_slug: str) -> bool:
    return bool(store.fetch_one(
        "SELECT id FROM approval_queue WHERE contact_id=? AND channel=? "
        "AND status IN ('pending','blocked','approved','sent','confirmed') LIMIT 1",
        (contact_id, CANAL)))


def prepare(campaign, contact_ids: list[int], force_hubspot: bool = False) -> dict:
    """Genera gancho + link por contacto y los deja en la cola de aprobación.

    No inscribe nada: eso ocurre al aprobar. Aquí se gastan tokens de Claude, no
    créditos de Amplemarket ni la reputación del remitente.
    """
    hoy = datetime.now().date()
    if hoy > campaign.cutoff_day:
        return {"ok": False, "error": (
            f"pasó el corte de la campaña ({campaign.cutoff_day.isoformat()}): "
            "inscribir ahora haría que el primer toque llegue encima del evento")}

    resumen = {"encolados": 0, "bloqueados": 0, "ya_estaban": 0,
               "sin_canal": 0, "hubspot_rojo": [], "detalle": []}

    # Semáforo de HubSpot en una sola pasada (3 llamadas por cada 100 correos)
    # en vez de una consulta por contacto dentro del bucle.
    _hs: dict[str, dict] = {}
    if hubspot_check.configured() and not force_hubspot:
        correos = []
        for cid in contact_ids:
            c = store.get_contact(cid)
            if c and (c.get("email") or "").strip():
                correos.append(c["email"])
        if correos:
            _hs = hubspot_check.check_lote(correos)

    for cid in contact_ids:
        c = store.get_contact(cid)
        if not c:
            continue
        if _ya_encolado(cid, campaign.slug):
            resumen["ya_estaban"] += 1
            continue
        # Amplemarket necesita al menos un identificador por lead.
        if not (c.get("email") or "").strip() and not (c.get("linkedin") or "").strip():
            resumen["sin_canal"] += 1
            continue

        # Semáforo de HubSpot: Amplemarket no conoce el CRM de Platzi, así que
        # su lista de exclusión no sabe quién ya es cliente o tiene deal abierto.
        if _hs and (c.get("email") or "").strip():
            res = _hs.get((c.get("email") or "").strip().lower(), {})
            if res.get("ok"):
                store.set_hubspot(cid, res.get("verdict", ""), res.get("reason", ""))
                if res.get("verdict") == "rojo":
                    resumen["hubspot_rojo"].append(
                        {"contact_id": cid, "nombre": c.get("nombre", ""),
                         "reason": res.get("reason", "")})
                    continue

        h = ai.draft_hook(c, campaign)
        hook = h["hook"]
        link = links.url_for(cid, campaign.slug)
        razones = guard.check(hook, "hook")
        if not link:
            razones.append("no se pudo generar el link de atribución")

        aid = store.add_approval(
            cid, CANAL,
            draft_subject=f"Inscribir en {campaign.name}",
            draft_body=hook, model=h["model"], reasons=razones)
        resumen["bloqueados" if razones else "encolados"] += 1
        resumen["detalle"].append({"approval_id": aid, "contact_id": cid,
                                   "nombre": c.get("nombre", ""),
                                   "empresa": c.get("empresa", ""),
                                   "gancho": hook, "link": link,
                                   "bloqueado": bool(razones), "razones": razones})
    return {"ok": True, **resumen}


def send_approved(campaign, dry_run: bool = False) -> dict:
    """Inscribe en Amplemarket todo lo aprobado. Es la única puerta de envío."""
    sequence_id = campaigns.amplemarket_sequence_id(campaign)
    pf = ams.preflight(sequence_id)
    if not pf["ok"]:
        return {"ok": False, "error": "la secuencia no está lista",
                "problems": pf["problems"], "warnings": pf.get("warnings", [])}

    aprobados = [a for a in store.list_approvals("approved") if a["channel"] == CANAL]
    if not aprobados:
        return {"ok": True, "enrolled": 0, "note": "no hay nada aprobado"}

    leads, ids, por_email = [], [], {}
    for a in aprobados:
        c = store.get_contact(a["contact_id"])
        if not c:
            continue
        link = links.url_for(c["id"], campaign.slug)
        leads.append(ams.build_lead(c, a["draft_body"], link))
        ids.append(a["id"])
        clave = (c.get("email") or c.get("linkedin") or "").strip().lower()
        por_email[clave] = c["id"]

    if dry_run:
        return {"ok": True, "dry_run": True, "would_enroll": len(leads),
                "sequence": pf["sequence"]["name"], "sample": leads[:2],
                "warnings": pf.get("warnings", [])}

    try:
        res = ams.add_leads(sequence_id, leads)
    except ams.AmpleError as e:
        return {"ok": False, "error": str(e)}

    # Amplemarket devuelve los saltados por motivo. Un lead saltado NO se marca
    # como enviado: si se marcara, el contacto quedaría como contactado sin que
    # nadie le haya escrito nunca.
    saltados = set()
    for motivo, filas in (res.get("skipped") or {}).items():
        for f in (filas or []):
            clave = (f.get("email") or f.get("linkedin_url") or "").strip().lower()
            if clave:
                saltados.add(clave)

    enviados = []
    for a in aprobados:
        c = store.get_contact(a["contact_id"])
        clave = ((c.get("email") if c else "") or (c.get("linkedin") if c else "") or "").strip().lower()
        if clave in saltados:
            store.decide_approval(a["id"], "failed")
            continue
        enviados.append(a["id"])
        store.add_activity(a["contact_id"], "email", "sent", store.now_iso(),
                           ext_id=f"ams:{a['id']}",
                           meta={"campaign": campaign.slug, "engine": "amplemarket",
                                 "sequence_id": sequence_id,
                                 "sequence_name": pf["sequence"].get("name", "")})
        # El contacto queda marcado con la campaña recién inscrito, no antes: la
        # marca significa «a esta persona le escribimos por el desayuno», y hasta
        # que Amplemarket no lo acepta nadie le escribió nada.
        store.set_contact_fields(a["contact_id"], {"campaign": campaign.slug})
        store.decide_approval(a["id"], "confirmed")

    return {"ok": True, "sequence": pf["sequence"]["name"],
            "enrolled": res.get("total_added_to_sequence", len(enviados)),
            "skipped": res.get("skipped", {}),
            "confirmed_local": len(enviados), "warnings": pf.get("warnings", []),
            "raw": res}


# --------------------------------------------------------------- webhooks
_LABEL_STAGE = {
    "interested": "en_conversacion",
    "hard_no": "descartado",
}


def _origen(payload: dict) -> dict:
    """De qué secuencia y de qué campaña viene este evento.

    Amplemarket manda la secuencia con nombres distintos según el evento, así
    que se miran los tres sitios donde aparece. Lo que no se hace es adivinar:
    si la secuencia no es de ninguna campaña nuestra, `campaign` queda vacío y
    el evento se ve tal cual — de otra cuenta, no del desayuno.
    """
    seq = payload.get("sequence")
    sid = ""
    if isinstance(seq, dict):
        sid = str(seq.get("id") or "")
    elif seq:
        sid = str(seq)
    sid = sid or str(payload.get("sequence_id") or "")
    camp = campaigns.by_amplemarket_sequence(sid)
    return {"engine": "amplemarket", "sequence_id": sid,
            "campaign": camp.slug if camp else ""}


def ingest_reply(payload: dict) -> dict:
    """Respuesta (correo o LinkedIn) que reporta Amplemarket.

    Amplemarket ya pausa la secuencia de ese lead; aquí solo se refleja en el
    pipeline y se abre la tarea para que una persona conteste.
    """
    contacto = payload.get("contact") or {}
    email = ((payload.get("from") or {}).get("email")
             if isinstance(payload.get("from"), dict) else payload.get("from")) or ""
    email = (email or contacto.get("email") or "").strip().lower()
    li = _norm_li((contacto.get("linkedin_url") or "").strip())
    c = store.find_contact(email=email, linkedin_slug=li)
    if not c:
        return {"ok": False, "error": "no reconozco a ese lead", "email": email}

    es_linkedin = bool(payload.get("linkedin"))
    cuerpo = (payload.get("linkedin") or {}).get("description") if es_linkedin \
        else payload.get("body", "")
    asunto = (payload.get("linkedin") or {}).get("subject") if es_linkedin \
        else payload.get("subject", "")

    origen = _origen(payload)
    store.add_activity(c["id"], "linkedin" if es_linkedin else "email", "reply",
                       store.now_iso(), ext_id=f"ams-reply:{payload.get('id') or store.now_iso()}",
                       meta={"asunto": asunto, "labels": payload.get("labels", []),
                             **origen})
    store.pause_sequences_for(c["id"], "replied")

    etiquetas = [l.lower() for l in (payload.get("labels") or []) if isinstance(l, str)]
    for et in etiquetas:
        if et in _LABEL_STAGE:
            store.set_stage(c["id"], _LABEL_STAGE[et])
            break
    else:
        store.set_stage(c["id"], "en_conversacion")

    clasif = ai.classify_reply(c, asunto or "", (cuerpo or "")[:1500])
    if not store.open_task_exists(c["id"], "reply_review"):
        store.add_task(c["id"], "reply_review", store.now_iso(),
                       notes=clasif.get("suggested_response", ""),
                       meta={"classification": clasif,
                             "canal": "linkedin" if es_linkedin else "email",
                             **origen})
    return {"ok": True, "contact_id": c["id"], "intent": clasif.get("intent"),
            "campaign": origen["campaign"]}


def ingest_stage(payload: dict) -> dict:
    """El lead avanzó de etapa en la secuencia: queda como toque enviado."""
    contacto = payload.get("contact") or {}
    email = (contacto.get("email") or "").strip().lower()
    c = store.find_contact(
        email=email, linkedin_slug=_norm_li((contacto.get("linkedin_url") or "").strip()))
    if not c:
        return {"ok": False, "error": "no reconozco a ese lead", "email": email}
    etapa = payload.get("stage") or payload.get("sequence_stage") or {}
    n = etapa.get("number") or etapa.get("id") or ""
    origen = _origen(payload)
    store.add_activity(c["id"], "email", "sent", store.now_iso(),
                       ext_id=f"ams-stage:{c['id']}:{n}",
                       meta={"stage": etapa, **origen})
    store.set_stage(c["id"], "contactado", force=False)
    # Un toque que salió por una secuencia nuestra también etiqueta: cubre a
    # quien entró a la secuencia desde la web app de Amplemarket y no por el
    # cockpit, que si no queda en el pipeline sin campaña.
    if origen["campaign"] and not (c.get("campaign") or "").strip():
        store.set_contact_fields(c["id"], {"campaign": origen["campaign"]})
    return {"ok": True, "contact_id": c["id"], "campaign": origen["campaign"]}
