"""Reparte la base entre los perfiles que firman LinkedIn.

Los contactos nuevos se reparten solos cuando el copiloto va a escribirles
(`seats.pick_for`). Esto es para el atrasado: una base ya cargada que todavía no
tiene dueño, o un lote grande que acaba de entrar y se quiere ver repartido antes
de arrancar.

## Se reparte por EMPRESA, no por contacto

Dos personas de la misma compañía invitadas la misma semana por dos VP distintos
de Platzi no se lee como dos acercamientos personales: se lee como una campaña.
Y si además hablan entre ellas —que es lo normal entre dos gerentes del mismo
sitio— el efecto es peor que no haber escrito. Así que la empresa entera va a un
solo perfil.

## Reparto proporcional al cupo, no mitad y mitad

El reparto sigue el cupo diario de cada asiento: uno con el doble de cupo recibe
el doble de contactos. Con cupos iguales sale 50/50, pero si un VP sube su techo
o entra un tercero, el reparto se ajusta solo en la siguiente corrida.

## Lo que nunca se mueve

Un contacto que ya tuvo contacto por LinkedIn se queda con su asiento para
siempre: es el único que puede escribirle, porque es el único a quien aceptó.
Reasignarlo mandaría el siguiente mensaje desde un perfil que ese prospecto no
conoce — y LinkedIn ni siquiera deja abrir ese chat.
"""
from __future__ import annotations

import re
import unicodedata

import seats
import store

# Sufijos societarios: «Woden Colombia S.A.S.» y «Woden Colombia» son la misma
# compañía y tienen que caer en el mismo perfil.
_SUFIJOS = re.compile(
    r"\b(s\.?a\.?s|s\.?a\.?|s\.? de r\.?l\.?( de c\.?v\.?)?|s\.?a\.?p\.?i\.? de c\.?v\.?"
    r"|s\.?a\.? de c\.?v\.?|ltda|limitada|inc|llc|corp|corporation|company|co|group"
    r"|grupo|holding|holdings|and cia|y cia|cia|e\.?u\.?|bic|zomac|esp)\b\.?", re.I)


def clave_empresa(nombre: str) -> str:
    t = unicodedata.normalize("NFKD", (nombre or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s&-]", " ", t)
    t = _SUFIJOS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _candidatos() -> list[dict]:
    """Contactos con LinkedIn que se pueden repartir.

    Se excluyen los descartados (no se les va a escribir) y los que ya tienen
    asiento: esos se cuentan, pero no se mueven.
    """
    return store.fetch_all(
        "SELECT id, nombre, empresa, li_seat, stage FROM contacts "
        "WHERE linkedin<>'' AND stage NOT IN ('descartado','cliente') ORDER BY id")


def plan() -> dict:
    """Calcula el reparto sin escribir nada."""
    # `configurado` = alguien ya inició sesión con ese perfil (el nombre lo lee
    # li_perfil al entrar). Un cupo activo pero sin estrenar no puede firmar
    # nada, así que darle parte del backlog solo congela esos contactos: se
    # quedan asignados a un asiento que nunca va a poder invitarlos.
    # Y los que reclaman un origen (`Seat.prioriza`) también quedan fuera: su
    # cupo está reservado. Si el backlog general les cayera encima, la corrida
    # de la mañana se lo comería y la cuenta que un vendedor marcó HOY saldría
    # mañana — que es justo lo que la reserva evita. Conservan lo que ya tenían;
    # solo dejan de recibir cosas nuevas.
    activos = [s for s in seats.en_uso()
               if s.activo and s.configurado and not (s.prioriza or "").strip()]
    if not activos:
        return {"ok": False, "error": ("no hay ningún perfil con sesión que pueda "
                                       "firmar: inicia sesión en al menos un asiento")}

    # Peso = cupo diario de invitaciones del asiento (su techo, no el del día:
    # el reparto es del backlog completo, no de hoy).
    pesos = {s.id: max(1, s.tope_dia) for s in activos}
    total_peso = sum(pesos.values())

    filas = _candidatos()
    congelados: dict[str, int] = {s.id: 0 for s in activos}
    grupos: dict[str, list[dict]] = {}
    fijos: dict[str, str] = {}      # clave de empresa → asiento ya comprometido
    intocables = 0

    for f in filas:
        k = clave_empresa(f["empresa"]) or f"__sin_empresa_{f['id']}"
        actual = (f["li_seat"] or "").strip()
        if actual and seats.tiene_historia_li(f["id"]):
            # Ya hubo contacto: este manda. Su empresa se ancla a ese asiento
            # para que sus colegas no salgan por otro perfil.
            fijos[k] = actual
            congelados[actual] = congelados.get(actual, 0) + 1
            intocables += 1
            continue
        grupos.setdefault(k, []).append(f)

    # Empresas grandes primero: colocar los bloques pesados al final desbalancea.
    orden = sorted(grupos.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    asignado: dict[str, list[int]] = {s.id: [] for s in activos}
    conteo = dict(congelados)
    total = sum(len(v) for v in grupos.values()) + intocables
    movimientos, ya_estaban = [], 0

    for k, contactos in orden:
        if k in fijos and fijos[k] in asignado:
            destino = fijos[k]
        else:
            # El que esté más lejos de su cuota proporcional.
            destino = max(activos, key=lambda s: (
                total * pesos[s.id] / total_peso - conteo.get(s.id, 0),
                -pesos[s.id], s.id)).id
        for c in contactos:
            if (c["li_seat"] or "").strip() == destino:
                ya_estaban += 1
            else:
                movimientos.append({"contact_id": c["id"], "nombre": c["nombre"],
                                    "empresa": c["empresa"], "de": c["li_seat"] or "",
                                    "a": destino})
            asignado[destino].append(c["id"])
            conteo[destino] = conteo.get(destino, 0) + 1

    return {
        "ok": True,
        "total": total,
        "intocables": intocables,
        "ya_estaban": ya_estaban,
        "movimientos": movimientos,
        "empresas": len(orden),
        "reparto": [{"seat_id": s.id, "nombre": s.etiqueta, "peso": pesos[s.id],
                     "contactos": conteo.get(s.id, 0),
                     "congelados": congelados.get(s.id, 0)} for s in activos],
    }


def aplicar() -> dict:
    p = plan()
    if not p.get("ok"):
        return p
    for m in p["movimientos"]:
        store.set_seat(m["contact_id"], m["a"])
    p["aplicados"] = len(p["movimientos"])
    return p


def resumen_actual() -> list[dict]:
    """Cómo está repartida la base ahora mismo, por asiento y etapa."""
    fuera = store.fetch_one(
        "SELECT COUNT(*) n FROM contacts WHERE linkedin<>'' AND (li_seat IS NULL OR li_seat='')")
    out = [{"seat_id": "", "nombre": "sin asignar", "contactos": (fuera or {}).get("n", 0)}]
    for s in seats.all_seats():
        n = store.count_seat_contacts(s.id)
        if n:
            out.append({"seat_id": s.id, "nombre": s.etiqueta, "contactos": n})
    return out


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Reparte la base entre los perfiles")
    ap.add_argument("--aplicar", action="store_true", help="sin esto solo simula")
    a = ap.parse_args()
    store.init()
    res = aplicar() if a.aplicar else plan()
    if not res.get("ok"):
        print(res.get("error"))
        raise SystemExit(1)
    print(f"{'APLICADO' if a.aplicar else 'SIMULACIÓN'} · {res['total']} contactos "
          f"en {res['empresas']} empresas")
    for r in res["reparto"]:
        print(f"  {r['nombre']:<26} {r['contactos']:>4} contactos "
              f"(peso {r['peso']}, congelados {r['congelados']})")
    print(f"  sin mover (ya tenían ese asiento): {res['ya_estaban']}")
    print(f"  intocables (ya invitados)        : {res['intocables']}")
    print(f"  {'movidos' if a.aplicar else 'por mover'}: {len(res['movimientos'])}")
