# Ampliación 2x de la base del Platzi Cockpit

Objetivo: duplicar la base de contactos del cockpit (569 → 1.138) con el mismo
estándar de la base madre, **1 decision maker por empresa** y **correo
corporativo + teléfono + LinkedIn**.

## Estado

| | |
|---|---|
| Base al empezar | 569 contactos · 307 empresas |
| **Base hoy** | **976 contactos · 714 empresas** |
| Agregados | 407 (175 completos + 232 sin teléfono) |
| Faltan para el 2x | 162 |

Todos los nuevos: >300 empleados **reales** (Amplemarket, no el inflado de
Apollo), LATAM sin Venezuela/Cuba/Nicaragua, verificados en vivo contra HubSpot
y 🟢 verdes.

| Lote (`campaign`) | Contactos | Correo | Teléfono | LinkedIn |
|---|---|---|---|---|
| `ampliacion-2x` | 174 | ✅ | ✅ | ✅ |
| `ampliacion-2x-sin-telefono` | 233 | ✅ | ❌ | ✅ |

Los `-sin-telefono` salieron de Apollo, que desde 2026 solo entrega teléfono de
forma asíncrona contra un webhook público. Decisión del usuario: entran igual,
marcados, para rellenar el teléfono después.

## Por qué se detuvo

1. **Amplemarket se quedó sin créditos** a mitad del revelado (HTTP 402).
2. **Apollo agotó su presupuesto local** (349 créditos, `backend/apollo_budget.json`).

## Lo que ya está pagado y espera

- **906 empresas** con >300 empleados reales confirmados
- **560** con decisor identificado; de ellas **87 del ICP sin revelar**
- **340** buscadas sin decisor ICP en Amplemarket → Apollo podría encontrarlos
- `people/search` de Amplemarket **sigue funcionando sin créditos**: buscar
  decisores es gratis, solo revelar el contacto cuesta

## Para retomar

```bash
# opción A · recargar Amplemarket (correo + teléfono + entregabilidad)
./correr.sh 660 && python3 verificar.py && python3 subir.py

# opción B · Apollo (correo + LinkedIn, sin teléfono)
python3 -c "import sys; sys.path.insert(0,'../backend'); import apollo_budget; \
            apollo_budget.set_budget(SALDO_REAL_DEL_DASHBOARD)"
python3 apollo_revelar.py
python3 verificar.py --in data/revelados_apollo.csv --out data/verificados_apollo.csv
python3 subir.py --in data/verificados_apollo.csv --campaign ampliacion-2x-sin-telefono
```

Todo es reanudable: `data/estado.json` y `data/estado_apollo.json` cachean cada
llamada pagada. Relanzar no vuelve a gastar en lo ya revelado.

## Archivos

| Script | Qué hace |
|---|---|
| `candidatos.py` | arma el universo de 2.484 empresas candidatas desde 5 pozos |
| `revelar.py` | firmografía → decisores → correo+teléfono → entregabilidad (Amplemarket) |
| `apollo_revelar.py` | revela por Apollo, con filtro de cargo ICP y tope de presupuesto |
| `verificar.py` | HubSpot en vivo, nivel empresa (dominio+nombre) y nivel persona |
| `subir.py` | carga al cockpit llamando a `contacts.add_contacts` en proceso |

`data/respaldo_cockpit_*.db` es la copia de la base antes de la primera carga.

## Dos cosas que conviene saber

- **Se quitaron 2 contactos** tras la carga: uno `undeliverable` y otro con
  dominio placeholder (`tudominio.com`).
- **El clasificador `_area()` del cockpit no reconoce** «Capital Humano»,
  «Compensaciones y Beneficios», «Desarrollo Organizacional», «Administración de
  Personal» ni «RRHH» como sigla. Por eso 113 de los nuevos figuran como área
  «otro» cuando la mayoría sí son el comprador de RR.HH. Es el mismo hueco que
  ya tenía la base madre (149 sin clasificar). Arreglarlo mejora la segmentación
  de todo el cockpit, no solo de este lote.


## Segmentación por país (25 ago 2026)

El país no estaba guardado en ninguna parte: las 569 originales nunca lo tuvieron
y los 407 nuevos lo traían en `ciudad`, que es el campo equivocado. `pais.py` lo
reconstruye con cuatro señales y lo escribe en las columnas nuevas **`pais`** y
**`pais_fuente`** (esta última deja auditar de dónde salió cada valor). Además
limpia `ciudad`, para que la UI no muestre «Mexico» donde va una ciudad.

| País | Contactos | Empresas | Correo | Teléfono | 3 datos | Canal |
|---|---|---|---|---|---|---|
| México | 504 | 330 | 421 | 479 | 396 | llamada + digital |
| Colombia | 221 | 142 | 177 | 212 | 168 | llamada + digital |
| Argentina | 109 | 109 | 109 | 0 | 0 | solo digital |
| Brasil | 48 | 48 | 46 | 2 | 0 | solo digital |
| Chile | 46 | 46 | 46 | 0 | 0 | solo digital |
| Perú | 29 | 29 | 29 | 0 | 0 | solo digital |
| Ecuador · Costa Rica · Paraguay | 4 | 4 | 0 | 4 | 0 | no accionable |
| sin país | 15 | 11 | 1 | 14 | 0 | revisar y borrar |

**El hallazgo:** Argentina, Chile y Perú (184 contactos) no tienen **ni un solo
teléfono** — salieron del lote de Apollo. La llamada solo existe en México y
Colombia, que concentran el 97% de los teléfonos. En el resto de LATAM la
secuencia tiene que ser correo + LinkedIn.

`segmentar.py` escribe `data/segmentacion_paises.json` y un CSV por país en
`data/por_pais/`, cada uno ordenado por puntaje del cargo.

Reporte visual: https://claude.ai/code/artifact/fcc9eff0-bc01-4bd9-addd-05bd63ac36a5

### Pendientes que la segmentación destapó

- **15 registros basura** de la base original (empresas «TBD», «Yo», «GEN», sin
  correo, con prefijos de Holanda/Francia/Nigeria/Irán): no se pueden contactar
  ni ubicar. Conviene borrarlos.
- **356 contactos como «sin clasificar»** por el hueco de `_area()` con «Capital
  Humano», «Compensaciones y Beneficios», «Desarrollo Organizacional»,
  «Administración de Personal» y «RRHH» como sigla. Muchos son RR.HH.
