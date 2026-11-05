# Heredado de Klik — fuera del cockpit de Platzi

Estos módulos venían del cockpit de Klik Energy y no aplican a Platzi:

| Módulo | Qué hacía | Reemplazo en Platzi |
|---|---|---|
| `colibri.py` | Conector al CRM Colibri (ms-customer de Klik) para no prospectar clientes | `backend/hubspot_check.py` (semáforo 🟢/🟡/🔴), ya enganchado al alta de contactos y al enroll |
| `clients.py` | Base de clientes de Klik/BEAM leída de un CSV | HubSpot es la fuente de clientes de Platzi |
| `validate_company.py` | Validaba el perfil de LinkedIn contra la columna `FRONTERAS_ATIENDE` (mercado energético) | Amplemarket ya entrega empresa validada en la base 2.000 |
| `segment_base.py` | Segmentaba `BASE_SEGMENTADA.csv` por validación | La base de Platzi entra por `POST /api/contacts` |
| `frontend-pages/clients/` | Sección «Clientes» de la UI | — |

No los importa nadie. Se pueden borrar cuando quieras; quedan aquí solo porque
esta carpeta no está en git.
