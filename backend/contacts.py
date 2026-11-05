"""Base de contactos: store.db (platzi_cockpit.db) es la ÚNICA fuente de
verdad que lee la UI (Conexiones, Follow-ups, Correos, Base de datos).

El CSV (BASE_SEGMENTADA.csv) sigue existiendo, pero ya solo como formato de
IMPORTACIÓN/EXPORTACIÓN: `sync.sync_contacts()` lo importa hacia store.db
(vía `_load_base()`, sin cambios), y `add_contacts()`/`enrich_contacts()`
siguen escribiéndolo porque un proyecto externo (`consolidar.py`) lo usa
como su propia fuente de contactos — es una interfaz de intercambio
legítima entre dos sistemas, no una segunda fuente de verdad para este.

Antes de esta fase, list_contacts() releía el CSV + reparseaba JSONL crudo
en cada request para calcular "¿ya se envió?"/"¿ya se conectó?", y el estado
de rebote se cruzaba a mano contra store.db con un parche aparte
(`_bounced_emails`). Todo eso vivía ya, correctamente, en `store.db
activities` — alimentado por `sync.backfill_activities()` en cada tick del
scheduler — así que ahora se lee de ahí directamente.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import threading
from functools import lru_cache

import calls_store
import hubspot_check
import store
from config import DEDUP_CSV

_WRITE_LOCK = threading.Lock()


def _cross_hubspot(contact_id: int, email: str, empresa: str) -> None:
    """Cruce automático con HubSpot al cargar un prospecto: marca el contacto
    con verde/amarillo/rojo. Fail-open: si HubSpot no está o falla, no rompe
    el alta (el veredicto queda vacío y se puede reintentar con el botón HS)."""
    try:
        if not (email or "").strip() or not hubspot_check.configured():
            return
        res = hubspot_check.check(email, empresa or "")
        if res.get("ok"):
            store.set_hubspot(contact_id, res.get("verdict", ""), res.get("reason", ""))
    except Exception:
        pass

# Defaults que siguen la convención de la base (filas existentes).
_NEW_ROW_DEFAULTS = {
    "TIPO_PROSPECTO": "🎯 Oportunidad",
    "PRIORIDAD": "🎯 OPORTUNIDAD",
    "ESTADO": "⏳ Por contactar",
}

# store.db guarda el tipo de actividad en su propio vocabulario
# (invite_sent/accepted/followup_sent); las páginas del cockpit siguen
# esperando el vocabulario histórico del runner (sent/already/message_sent)
# en `linkedin_status` — se traduce aquí para no tocar los filtros de canal
# ya existentes en main.py.
_LI_STATUS_DISPLAY = {"invite_sent": "sent", "accepted": "already",
                      "followup_sent": "message_sent"}


def _norm_li(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url.strip(), re.I)
    return m.group(1).lower() if m else url.strip().lower()


def _norm_txt(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@lru_cache(maxsize=1)
def _load_base() -> list[dict]:
    """Lee el CSV crudo — SOLO para `sync.sync_contacts()` (importación hacia
    store.db). Nada más debe llamar a esta función."""
    if not DEDUP_CSV.exists():
        return []
    rows: list[dict] = []
    with DEDUP_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, r in enumerate(reader):
            rows.append({
                "id": str(i),
                "nombre": (r.get("CONTACTO_NOMBRE") or "").strip(),
                "email": (r.get("CONTACTO_EMAIL") or "").strip(),
                "cargo": (r.get("CONTACTO_CARGO") or "").strip(),
                "celular": (r.get("CONTACTO_CELULAR") or "").strip(),
                "ciudad": (r.get("CIUDAD") or "").strip(),
                "sector": (r.get("SECTOR") or "").strip(),
                "linkedin": (r.get("LINKEDIN_PROFILE") or "").strip(),
                # EMPRESA_REAL suele ser "OTROS"; la empresa real está en FRONTERAS_ATIENDE.
                "empresa": ((r.get("FRONTERAS_ATIENDE") or "").strip()
                            or (r.get("EMPRESA_REAL") or "").strip()),
                "tipo": (r.get("TIPO_PROSPECTO") or "").strip(),
                "estado": (r.get("ESTADO") or "").strip(),
                "validacion": (r.get("VALIDACION_ESTADO") or "").strip(),
                "empresa_linkedin": (r.get("VALIDACION_EMPRESA_LI") or "").strip(),
            })
    return rows


def clear_cache() -> None:
    _load_base.cache_clear()


# ----------------------------- lectura (store.db) ------------------------
def list_contacts() -> list[dict]:
    """Contactos + estado por canal + quién los firma, todo desde store.db."""
    base = store.fetch_all("SELECT * FROM contacts")
    # Nombre del perfil que firma cada prospecto. El id («vp-2») no le dice nada
    # a quien mira la tabla; el nombre sí, y es el dato que decide quién puede
    # tocar a ese contacto.
    remitentes = {r["key"].split(":")[1]: r["value"] for r in store.fetch_all(
        "SELECT key, value FROM kv WHERE key LIKE 'seat:%:nombre' AND value<>''")}

    li_by_contact: dict[int, dict] = {}
    for r in store.fetch_all(
            "SELECT contact_id, type, ts FROM activities WHERE channel='linkedin' "
            "AND type IN ('invite_sent','accepted','followup_sent') ORDER BY ts"):
        li_by_contact[r["contact_id"]] = {"status": r["type"], "ts": r["ts"]}

    sent_contacts: set[int] = set()
    em_by_contact: dict[int, dict] = {}
    for r in store.fetch_all(
            "SELECT contact_id, type, ts FROM activities WHERE channel='email' "
            "AND type IN ('sent','bounce','spam') ORDER BY ts"):
        if r["type"] == "sent":
            sent_contacts.add(r["contact_id"])
        em_by_contact[r["contact_id"]] = {"status": r["type"], "ts": r["ts"]}

    bounce_reason: dict[int, str] = {}
    for r in store.fetch_all(
            "SELECT contact_id, meta_json FROM activities WHERE channel='email' "
            "AND type IN ('bounce','spam') ORDER BY ts"):
        try:
            reason = json.loads(r.get("meta_json") or "{}").get("reason", "") or "bounce"
        except json.JSONDecodeError:
            reason = "bounce"
        bounce_reason[r["contact_id"]] = reason  # última gana (ordenado por ts)

    calls = calls_store.load()
    out = []
    for c in base:
        cid = c["id"]
        li_st = li_by_contact.get(cid, {})
        em_st = em_by_contact.get(cid, {})
        key = calls_store.call_key(c["email"], c["linkedin"], c["nombre"], c["empresa"])
        call = calls.get(key, {})
        out.append({
            "id": str(cid),
            "nombre": c["nombre"] or "", "email": (c["email"] or "").lower(),
            "cargo": c["cargo"] or "", "celular": c["celular"] or "",
            "ciudad": c["ciudad"] or "", "sector": c["sector"] or "",
            "linkedin": c["linkedin"] or "", "empresa": c["empresa"] or "",
            "tipo": c.get("tipo") or "", "estado": c.get("estado") or "",
            "validacion": c["validacion"] or "",
            "campaign": c.get("campaign") or "", "tier": c.get("tier") or "",
            "li_seat": c.get("li_seat") or "",
            "li_seat_nombre": remitentes.get(c.get("li_seat") or "", ""),
            "empresa_linkedin": c.get("empresa_linkedin") or "",
            "linkedin_status": _LI_STATUS_DISPLAY.get(li_st.get("status", ""), ""),
            "linkedin_channel": "followup" if li_st.get("status") == "followup_sent" else "",
            "linkedin_ts": li_st.get("ts", ""),
            "email_status": em_st.get("status", ""),
            "email_ts": em_st.get("ts", ""),
            "email_enviado": cid in sent_contacts,
            "email_bounced": cid in bounce_reason,
            "bounce_reason": bounce_reason.get(cid, ""),
            "call_key": key,
            "llamada": call.get("called", False),
            "llamada_outcome": call.get("outcome", ""),
            "llamada_notes": call.get("notes", ""),
            "llamada_ts": call.get("updated_at", ""),
        })
    return out


def facets() -> dict:
    base = store.fetch_all(
        "SELECT sector, estado, validacion, campaign, tier FROM contacts")
    sectors = sorted({c["sector"] for c in base if c["sector"]})
    estados = sorted({c["estado"] for c in base if c.get("estado")})
    validaciones = sorted({c["validacion"] for c in base if c.get("validacion")})
    campanas = sorted({c["campaign"] for c in base if c.get("campaign")})
    tiers = sorted({c["tier"] for c in base if c.get("tier")})
    return {"sectores": sectors, "estados": estados, "validaciones": validaciones,
            "campanas": campanas, "tiers": tiers, "total": len(base)}


# ----------------------------- dedup (contra store.db, en vivo) ----------
def _existing_keys() -> tuple[set, set, set]:
    """(slugs LinkedIn, emails, nombre+empresa) ya presentes en store.db —
    la base viva, no el CSV (que puede tener varios minutos de rezago)."""
    slugs, emails, names = set(), set(), set()
    for c in store.fetch_all("SELECT email, linkedin, nombre, empresa FROM contacts"):
        s = _norm_li(c["linkedin"] or "")
        if s:
            slugs.add(s)
        if c["email"]:
            emails.add(c["email"].lower())
        ne = _norm_txt((c["nombre"] or "") + "|" + (c["empresa"] or ""))
        if ne != "|":
            names.add(ne)
    return slugs, emails, names


# ----------------------------- escritura (store.db + CSV interchange) ----
def _write_csv_row(row: dict) -> None:
    """Agrega una fila al CSV — solo para que un ETL externo (consolidar.py)
    siga viendo los contactos nuevos. store.db ya tiene el dato real; esto
    es un espejo de exportación, no de lectura."""
    if not DEDUP_CSV.exists():
        return
    with DEDUP_CSV.open(encoding="utf-8-sig", newline="") as f:
        fieldnames = csv.DictReader(f).fieldnames or []
    if not fieldnames:
        return
    full = {k: "" for k in fieldnames}
    full.update(_NEW_ROW_DEFAULTS)
    full.update(row)
    full = {k: v for k, v in full.items() if k in fieldnames}
    shutil.copy2(DEDUP_CSV, DEDUP_CSV.with_suffix(".csv.bak"))
    needs_nl = DEDUP_CSV.read_bytes()[-1:] not in (b"\n", b"")
    with DEDUP_CSV.open("a", encoding="utf-8", newline="") as f:
        if needs_nl:
            f.write("\n")
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writerow(full)


def add_contacts(items: list[dict]) -> dict:
    """Agrega prospectos nuevos a store.db (dedup en vivo por slug de
    LinkedIn, email y nombre+empresa) y los exporta al CSV para el ETL externo.

    Cada item: nombre, cargo, linkedin, empresa; opcionales email, celular,
    ciudad, sector, validacion, validacion_empresa, campaign, tier.
    """
    with _WRITE_LOCK:
        slugs, emails, names = _existing_keys()
        added, skipped = 0, []
        for it in items:
            nombre = (it.get("nombre") or "").strip()
            empresa = (it.get("empresa") or "").strip()
            linkedin = (it.get("linkedin") or "").strip().rstrip("/")
            email = (it.get("email") or "").strip()
            slug = _norm_li(linkedin)
            ne = _norm_txt(nombre + "|" + empresa)

            if not nombre and not linkedin and not email:
                skipped.append({"nombre": nombre, "empresa": empresa,
                                "reason": "sin datos (nombre/linkedin/email)"})
                continue
            if slug and slug in slugs:
                skipped.append({"nombre": nombre, "empresa": empresa,
                                "reason": "duplicado: LinkedIn ya en base"})
                continue
            if email and email.lower() in emails:
                skipped.append({"nombre": nombre, "empresa": empresa,
                                "reason": "duplicado: email ya en base"})
                continue
            if ne != "|" and ne in names:
                skipped.append({"nombre": nombre, "empresa": empresa,
                                "reason": "duplicado: nombre+empresa ya en base"})
                continue

            validacion = (it.get("validacion") or "").strip() \
                or ("Verificado" if linkedin else "Sin LinkedIn")
            csv_key = calls_store.call_key(email, linkedin, nombre, empresa)
            cid = store.upsert_contact(csv_key, {
                "nombre": nombre, "email": email,
                "cargo": (it.get("cargo") or "").strip(),
                "celular": (it.get("celular") or "").strip(),
                "ciudad": (it.get("ciudad") or "").strip(),
                "sector": (it.get("sector") or "").strip(),
                "linkedin": linkedin, "empresa": empresa,
                "campaign": (str(it.get("campaign") or "")).strip(),
                "tier": (str(it.get("tier") or "")).strip(),
                "validacion": validacion,
                "tipo": _NEW_ROW_DEFAULTS["TIPO_PROSPECTO"],
                "estado": _NEW_ROW_DEFAULTS["ESTADO"],
                "empresa_linkedin": (it.get("validacion_empresa") or "").strip(),
            }, source=(it.get("fuente") or "apollo"))
            _cross_hubspot(cid, email, empresa)  # cruce automático al cargar
            _write_csv_row({
                "CONTACTO_NOMBRE": nombre, "CONTACTO_EMAIL": email,
                "CONTACTO_CARGO": (it.get("cargo") or "").strip(),
                "CONTACTO_CELULAR": (it.get("celular") or "").strip(),
                "CIUDAD": (it.get("ciudad") or "").strip(),
                "SECTOR": (it.get("sector") or "").strip(),
                "LINKEDIN_PROFILE": linkedin,
                "EMPRESA_REAL": empresa, "FRONTERAS_ATIENDE": empresa,
                "VALIDACION_ESTADO": validacion,
                "VALIDACION_VEREDICTO": (it.get("validacion") or "").strip(),
                "VALIDACION_EMPRESA_LI": (it.get("validacion_empresa") or "").strip(),
            })

            added += 1
            if slug:
                slugs.add(slug)
            if email:
                emails.add(email.lower())
            if ne != "|":
                names.add(ne)

    total = store.fetch_one("SELECT COUNT(*) n FROM contacts")["n"]
    return {"ok": True, "added": added, "skipped": skipped, "total": total}


def _enrich_csv_rows(linkedin_updates: dict[str, dict]) -> None:
    """Rellena email/celular/validación en las filas del CSV que matcheen por
    slug de LinkedIn — espejo de exportación para el ETL externo, igual que
    add_contacts. No pisa un valor ya presente."""
    if not linkedin_updates or not DEDUP_CSV.exists():
        return
    with DEDUP_CSV.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if not fieldnames:
        return
    touched = False
    for row in rows:
        slug = _norm_li((row.get("LINKEDIN_PROFILE") or "").strip())
        upd = linkedin_updates.get(slug)
        if not upd:
            continue
        if upd.get("email") and not row.get("CONTACTO_EMAIL"):
            row["CONTACTO_EMAIL"] = upd["email"]
            touched = True
        if upd.get("celular") and not row.get("CONTACTO_CELULAR"):
            row["CONTACTO_CELULAR"] = upd["celular"]
            touched = True
        if upd.get("validacion") and not row.get("VALIDACION_VEREDICTO"):
            row["VALIDACION_VEREDICTO"] = upd["validacion"]
            touched = True
    if touched:
        shutil.copy2(DEDUP_CSV, DEDUP_CSV.with_suffix(".csv.bak"))
        with DEDUP_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)


def enrich_contacts(items: list[dict]) -> dict:
    """Actualiza correo/celular/validación de contactos YA EXISTENTES en
    store.db, matcheados por slug de LinkedIn (a diferencia de add_contacts,
    que solo agrega prospectos nuevos). No pisa un valor ya presente; solo
    rellena lo que esté vacío. También refleja el cambio en el CSV (ETL externo).
    Cada item: {linkedin, email?, celular?, validacion?}."""
    with _WRITE_LOCK:
        by_slug: dict[str, dict] = {}
        for c in store.fetch_all("SELECT * FROM contacts"):
            s = _norm_li(c["linkedin"] or "")
            if s:
                by_slug[s] = c

        updated, not_found = 0, []
        csv_updates: dict[str, dict] = {}
        for it in items:
            slug = _norm_li((it.get("linkedin") or "").strip())
            c = by_slug.get(slug) if slug else None
            if not c:
                not_found.append(it.get("linkedin", ""))
                continue
            email = (it.get("email") or "").strip()
            celular = (it.get("celular") or "").strip()
            validacion = (it.get("validacion") or "").strip()
            if not (email or celular or validacion):
                continue

            fields = {k: c.get(k) or "" for k in store._CONTACT_FIELDS}
            changed = False
            if email and not fields["email"]:
                fields["email"] = email
                changed = True
            if celular and not fields["celular"]:
                fields["celular"] = celular
                changed = True
            if validacion and not fields["validacion"]:
                fields["validacion"] = validacion
                changed = True
            if changed:
                csv_key = c["csv_key"] or calls_store.call_key(
                    fields["email"], fields["linkedin"], fields["nombre"], fields["empresa"])
                store.upsert_contact(csv_key, fields, source=c.get("source") or "apollo")
                csv_updates[slug] = {"email": email, "celular": celular, "validacion": validacion}
                updated += 1

        _enrich_csv_rows(csv_updates)

    return {"ok": True, "updated": updated, "not_found": not_found}
