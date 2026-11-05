# Platzi Cockpit

Cockpit de outreach B2B de Platzi con **copiloto agéntico**: detecta respuestas,
aceptaciones y clics solo, avanza las secuencias multicanal, arma cada mensaje
sobre copy aprobado y presenta una **cola de aprobación** — tú apruebas con un
click y los motores envían. **Nunca envía nada sin aprobación humana.**

Canales: invitaciones de LinkedIn con nota, mensajes a ya-conectados, correo
(open/click/bounce/spam + detección de respuestas por Gmail) y llamadas humanas
como tarea con guion. LinkedIn lo firman los **perfiles de VP** que se abren
desde Ajustes, cada uno con su sesión, su cupo y su calentamiento.

## Cómo redacta (y qué no inventa)

El copy **no lo escribe el modelo**. Vive en `backend/campaigns.py`, escrito y
revisado por una persona, y Claude solo aporta el **gancho**: una frase
específica de la empresa dentro del correo. Después todo pasa por
`backend/guard.py`, que frena el mensaje si encuentra:

- un token sin resolver o un marcador tipo `PEGAR AQUÍ`;
- una nota de LinkedIn de más de 300 caracteres;
- el link de atribución ausente en un paso que lo lleva;
- una **cifra sin fuente** (porcentajes, «3x», «millones») — la guía de outbound
  las tiene bloqueadas y un correo en frío es donde más se cuelan;
- palabras o signos fuera de tono para el segmento.

Lo frenado entra a la cola como `blocked`: se ve, se corrige y se aprueba a mano.
Un «aprobar todos» nunca lo toca, y aprobar re-audita el texto final — si lo
editaste, la compuerta mira lo editado.

## Amplemarket es quien envía

El outreach sale por Amplemarket. El reparto:

- **cockpit** → lote, semáforo de HubSpot, gancho por compañía, link propio,
  cola de aprobación, pipeline.
- **Amplemarket** → cadencia, correo, LinkedIn, tareas de llamada, detección de
  respuestas, lista de exclusión.

Lo que la API permite, verificado contra la cuenta: `GET /sequences` y
`POST /sequences/{id}/leads`. **No** se puede crear una secuencia ni leerla
(`GET /sequences/{id}` da 404), así que la secuencia se arma en la web app y su
copy no lo podemos auditar — solo auditamos lo que generamos nosotros.

Dos consecuencias que muerden:

1. Los campos de `data` **solo sirven si la plantilla los nombra**. Amplemarket
   publica en `dynamic_fields_used` qué variables usa cada secuencia;
   `amplemarket_seq.preflight()` compara contra `{{ gancho }}` y
   `{{ link_invitacion }}` y avisa antes de inscribir a nadie. Sin el link no
   hay atribución y el test no se puede demostrar.
2. La respuesta de `add_leads` trae los **saltados por motivo** (duplicado,
   exclusión, contactado hace poco). Un lead saltado no se marca como enviado:
   quedaría como contactado sin que nadie le escribiera.

```bash
curl -s localhost:8532/api/campaigns/desayuno-28/amplemarket   # spec + preflight
curl -X POST .../amplemarket/prepare -d '{"contact_ids":[1,2]}'  # gancho+link → cola
curl -X POST .../amplemarket/enroll  -d '{"dry_run":true}'       # inscribir
```

`spec` devuelve la secuencia lista para pegar en la web app: las constantes de
la campaña ya resueltas y las variables por lead en Liquid.

**Anti doble envío:** si un contacto ya va por Amplemarket, la cadencia local se
pausa sola. Sin eso, un contacto en los dos motores recibe la misma invitación
dos veces con dos remitentes distintos.

## Atribución

Cada contacto recibe un link propio (`backend/links.py`). `GET /r/<code>` deja la
actividad `click` con su nombre y redirige al registro con UTM. De ahí sale la
señal más caliente del flujo: **clic sin registro → llamada para hoy**, que el
tick convierte en tarea sola.

Sin esto el día del evento hay asistentes y ninguna forma de demostrar que
llegaron por el outreach.

## El flujo diario

1. **Scheduler** (cada 15 min, L-V 8:00-18:00): ingesta respuestas y
   aceptaciones, reconcilia envíos, crea llamadas por clic y arma los mensajes
   de los pasos vencidos.
2. **Hoy**: briefing, cola de aprobación, respuestas clasificadas por intención
   y llamadas pendientes con el guion ya renderizado.
3. **Pipeline**: contactos por etapa (nuevo → contactado → conectado → en
   conversación → **registrado** → reunión → cliente). Una respuesta en
   cualquier canal pausa la secuencia; marcar `registrado` la cierra.
4. **Guardarraíles**: cupo por perfil de LinkedIn (no un tope global), tope de
   correo, jitter, horario laboral, semáforo de HubSpot (🔴 Customer o deal
   abierto no se toca) y preflight de los asientos antes de enviar.

## Arrancar

No se arranca a mano: backend, frontend y ngrok son agentes de launchd que
suben solos al iniciar sesión en el Mac y se relevantan si se caen.

```bash
bash run.sh              # carga lo que falte y muestra el estado
bash run.sh reiniciar    # reinicia backend, frontend (:5181) y ngrok
bash run.sh estado       # solo el estado
```

UI en http://127.0.0.1:5181 (el 5183 lo tiene otro proyecto).

Requiere un intérprete con `fastapi`, `uvicorn`, `anthropic` y `playwright`
(`PLATZI_PYTHON` para fijarlo). Estado en `backend/platzi_cockpit.db`.

El intérprete de los motores ya no se adivina por la ruta: `config._resolve_python()`
**pregunta** a cada candidato si puede importar playwright y se queda con el
primero que sí. Un venv que existe pero perdió la dependencia hacía que todo
subproceso muriera al instante con `ModuleNotFoundError`, y desde la UI eso se
veía como un job colgado sin explicación.

Variables (todas opcionales, con defaults sensatos): `PLATZI_CHROME`, `PLATZI_API_PORT`,
`PLATZI_WEB_PORT`, `PLATZI_PYTHON`, `PLATZI_LI_COOKIES`, `PLATZI_LINKEDIN_CDP`,
`PLATZI_ALLOWED_DOMAIN`, `PLATZI_NGROK_DOMAIN`, `PLATZI_PUBLIC_BASE_URL`,
`PLATZI_TRACKING_DB`, `PLATZI_DEDUP_CSV`.

## El backend corre solo (launchd)

`run.sh` sigue sirviendo para trabajar a mano, pero **el envío por LinkedIn no
puede depender de una terminal abierta**. El tick del copiloto —que cada 15 min
corre la vía rápida de Platzi Ventas y, en su ventana, la corrida de la mañana—
vive *dentro* de uvicorn: si el proceso se cae, nadie invita a nadie y no hay
error en ninguna parte. Es la misma avería que dejó a la máquina de prospección
muda 4 días.

Desde el 18-sep-2026 el backend es un agente de launchd con `KeepAlive`:

```bash
launchctl list | grep com.platzi.cockpit.backend    # pid y último código de salida
launchctl kickstart -k gui/$(id -u)/com.platzi.cockpit.backend   # reiniciarlo
launchctl bootout   gui/$(id -u)/com.platzi.cockpit.backend      # apagarlo
tail -40 ~/Library/Logs/platzi/com.platzi.cockpit.backend.log
```

Probado: `kill -9` al proceso y launchd lo devolvió en segundos.

**`AbandonProcessGroup` no es decorativo.** Los Chrome de los VP se lanzan como
hijos del backend. Sin esa llave, cada reinicio del agente se llevaría por
delante los cinco navegadores —y con ellos las sesiones de LinkedIn que costó
un login manual levantar—. Con ella, el backend se reinicia y los Chrome siguen
en pie (comprobado en el traspaso: 5 antes, 5 después).

**El cron no envía nada por su cuenta, y es a propósito.** Quien envía sigue
siendo el tick de dentro de uvicorn. Un segundo proceso llamando a
`conexiones.correr_via_rapida()` en paralelo sería la forma más directa de
mandar la misma invitación dos veces: esa función **no tiene candado** —su
protección es que solo hay un llamador—. Lo que el agente garantiza es que ese
único llamador esté siempre vivo.

Desde el 25-sep-2026 el frontend (`com.platzi.cockpit.frontend`, Vite en
`:5181` con `--strictPort`) y el túnel (`com.platzi.cockpit.ngrok`) también son
agentes con `KeepAlive`. Ninguno hace falta para invitar, pero sin ngrok los
eventos de SendGrid (open/click/bounce) se pierden sin aviso: estaba apagado
cuando se migró. Los dos salen en `/api/agentes` como no críticos.

**El túnel solo deja pasar lo público.** ngrok publica el puerto 8532 entero y
el login está apagado sin `GOOGLE_CLIENT_ID`: hasta el 25-sep cualquiera con la
URL podía leer `/api/seats` o disparar envíos. El middleware
`solo_lo_publico_por_el_tunel` de `main.py` responde 404 a todo lo que entra
por el túnel salvo `/api/webhooks/*`, `/r/*`, `/invitacion`, `/static/*` y las
cuatro llamadas de RSVP que usa esa landing. Una ruta pública nueva hay que
sumarla a `_TUNEL_PREFIJOS`.

**La vía rápida corre también por aviso.** Además del tick, la vía rápida
arranca en cuanto entran decisores de Platzi Ventas a la base. Desde el 28-sep
el aviso lo da `nube.sincronizar()` (ver abajo); antes lo daba la máquina de
prospección. Con dos llamadores, `correr_via_rapida()` ya tiene candado: el que
llega tarde no corre en paralelo, deja pedida otra vuelta al que está corriendo.

## Cockpit híbrido: la nube procesa, el Mac invita (desde el 28-sep-2026)

Con el Mac en batería o con la tapa cerrada, nadie atendía la cola de Platzi
Ventas. Ahora se reparte así:

| Dónde | Qué hace |
|---|---|
| **Nube**: `platzi-cockpit-nube` (Vercel Pro + Neon, `PLATZI/cockpit-nube/`) | Un cron cada minuto toma la cola, frena por HubSpot, busca decisores en Apollo, le reporta a Ventas y deja los contactos en una **bandeja** de Neon. Funciona aunque el Mac esté dormido. |
| **Mac**: este cockpit | Todo lo que exige una sesión real: las invitaciones de LinkedIn (vía rápida y envío diario), las señales de LinkedIn, los follow-ups, los logins de los VPs, las inscripciones en Amplemarket y HubSpot, los webhooks por ngrok, las peticiones escritas a mano y el recordatorio. |

`backend/nube.py` es la costura entre los dos:

- **`sincronizar()`** corre en un bucle de 60 s dentro de uvicorn y también en
  cada tick, antes de la vía rápida. Hace esto, en orden:
  1. trae la bandeja;
  2. la da de alta con `contacts.add_contacts`, con el mismo dedup y el mismo
     sello `maquina-ventas`;
  3. confirma lo entregado;
  4. dispara la vía rápida;
  5. deja un latido.

  Si el Mac durmió, la primera vuelta al despertar recoge todo lo acumulado.
  Un contacto que ya estaba en la base se salta y se confirma igual.
- **`LibroNube`** es el libro de créditos de Apollo. La fuente es una fila de
  Neon, la misma de la que gasta la nube, así que no hay dos saldos. El alias
  `apollo_budget.py` lo enchufa en todo proceso del Mac. Sin red, cae al
  `apollo_budget.json` local y apunta lo gastado en
  `apollo_budget_pendiente.json`, que se devuelve en la siguiente sincronización.

| Ruta | Qué muestra |
|---|---|
| [platzi-cockpit-nube.vercel.app](https://platzi-cockpit-nube.vercel.app) | Página de estado: última pasada, bandeja, último latido del Mac y créditos. Solo conteos y horas; no muestra ni una empresa. |
| `GET /api/nube` (local) | Última sincronización y de dónde salió el saldo (`origen: nube` o `local`). |
| `POST /api/nube/sincronizar` (local) | Fuerza una vuelta. |

Operar la nube:

```bash
cockpit-nube/desplegar.sh                       # deploy a producción (sube cockpit-nube + platzi_core, nada más)
python3.12 cockpit-nube/cargar_secretos.py      # (re)carga secretos desde los .env del Mac
python3.12 cockpit-nube/cargar_secretos.py --sombra 1 && cockpit-nube/desplegar.sh   # modo sombra
```

Secretos del proyecto:

- `MAQUINA_TOKEN`, `APOLLO_API_KEY` y `HUBSPOT_PRIVATE_APP_TOKEN` salen de los
  `.env` del Mac.
- `NUBE_TOKEN` (el Mac lo manda en `X-Nube-Token`) y `CRON_SECRET` (el cron de
  Vercel lo manda como `Bearer`) se generan la primera vez y quedan también en
  `PLATZI/.env`.
- `DATABASE_URL` la pone la integración de Neon.

Ninguno está en el repo. El esquema de Neon se crea solo en la primera conexión.

La nube tiene tres cosas que el Mac no necesitaba:

- **Un candado** para que solo corra una pasada a la vez, porque el cron es de
  un minuto y una pasada con cola tarda más.
- **La tabla `cuentas`.** Si una cuenta ya tiene decisores pagados y Ventas no
  anotó el paso, se reusan en vez de pagar Apollo otra vez. Una cuenta que
  falla espera 2, 4, 8… hasta 60 min antes del siguiente intento.
- **Caché de HubSpot limpio en cada pasada**, porque la instancia de Vercel
  sigue caliente entre crons.

**Verificado el 28-sep-2026:**

- *Sombra*: sin gastar ni escribir, la nube tomó la misma decisión que el Mac
  en 10 de 10 cuentas reales de la cola (frenar o ir a Apollo). HubSpot le
  respondió lo mismo que al Mac en 10 de 10.
- *Mac*: con un contacto de la bandeja que ya existía, la base quedó igual
  (1.020 → 1.020) y el contacto se confirmó. Con la nube caída, el libro cayó
  al local.
- *Cutover*: la máquina del Mac cedió la cola (`COLA_EN_NUBE=1`), el oído quedó
  retirado y la nube pasó a modo real.

### Los Chrome de los VP corren en segundo plano (headless)

Con la sesión iniciada, cada asiento corre `--headless=new` sobre su mismo
perfil: sin ventanas, sin pestañas de LinkedIn en pantalla y sin robar el foco.
La única vez que aparece una ventana es al pulsar **Abrir ventana para iniciar
sesión** en Ajustes → Perfiles; al guardar el login vuelve sola a segundo plano.
Una ventana que quede abierta con sesión se pasa a headless en el siguiente
`asegurar()` (antes de cada job). Detalle y lo que se descartó, en la cabecera
de `backend/li_login.py`.

Un Chrome que arrancó antes de una actualización de Chrome se relanza solo al
cerrarlo, con los mismos argumentos más `--no-startup-window`, y se queda con
el perfil. `li_login.cerrar()` lo detecta y lo cierra también.

### Los logs NO pueden vivir bajo `~/Documents` (avería del 21-sep-2026)

El 21-sep a las 12:20 la máquina reinició y **los cuatro** agentes de launchd
—máquina de prospección, vigía, backend del cockpit y recordatorio— quedaron
muertos hasta el 23-sep. Dos días sin prospectar y sin invitar.

No falló ni una línea de código. launchd no pudo abrir el archivo al que
apuntaban `StandardOutPath`/`StandardErrorPath` y **abortó el job antes de
ejecutarlo**, con `EX_CONFIG` (78). Los cuatro logs vivían bajo `~/Documents`,
que macOS protege con TCC, y tras el reinicio los cuatro archivos tenían colgado
el atributo extendido `com.apple.macl` — la lista de control por-app de la
carpeta protegida. launchd no está en esa lista.

Comprobado con una sonda mínima: un agente que solo hace `/bin/echo` falla con
78 **solo** por apuntar su log a ese archivo; el mismo agente con el log en
`~/Library/Logs` sale 0.

Lo perverso: **el canal por el que la avería se habría contado era la avería
misma**. Cero líneas en cualquier log durante 48 h. Y el vigía de la máquina
(`latido.py --vigilar`), que existe justo para avisar de esto, murió igual — es
otro agente de launchd con el log en la misma carpeta. El tablero de
23-platzi-ventas decía «la máquina lleva sin dar señales», que es exactamente
lo que dice también cuando la máquina está sana y la cola vacía.

Tres cosas cambiaron:

1. **Los cuatro logs se mudaron a `~/Library/Logs/platzi/<label>.log`**, que no
   es carpeta protegida. Copia de los plists originales en
   `~/Library/LaunchAgents/.respaldo-20260923/`.
2. **`backend/agentes.py` le pregunta a launchd, no a los logs.**
   `launchctl list <label>` publica el estado de salida aunque el proceso no
   haya llegado a arrancar: el 78 estuvo visible las 48 horas y nadie se lo
   preguntó. Ojo — publica el estado **crudo** de `wait(2)`, no el código: un
   `exit 78` sale como `19968` (78 << 8) y una muerte por SIGTERM como `15`.
   Leerlo sin decodificar confunde las dos cosas.
3. **Se repara sola.** Un bucle dentro de uvicorn mira cada 5 min, le quita el
   `com.apple.macl` al log y vuelve a arrancar lo que se haya caído. Probado en
   vivo: con el vigía descargado a mano, volvió en 60 s y lo dejó escrito.

```bash
curl -s localhost:8532/api/agentes | python3 -m json.tool   # veredicto de los 4
curl -s -XPOST localhost:8532/api/agentes/reparar           # levantar lo caído
```

La banda roja de la interfaz (`components/BandaSalud.tsx`) sale en **el marco de
la app**, no en una pestaña: esta avería se caracteriza por que nadie la está
buscando. Si un agente está sano pero su log sigue en carpeta protegida, sale
una banda ámbar — es una bomba de relojería, no un fallo.

**Lo que este montaje no puede hacer es vigilarse a sí mismo**: corre dentro de
uvicorn. Ese lado lo cierra el vigía, que es otro proceso y otro agente y ahora
comprueba también que `GET /api/agentes` responda. Se vigilan mutuamente sin
compartir proceso. El backend caído además se nota solo —la interfaz deja de
cargar—, que es justo lo que a la máquina callada le faltaba.

**Regla para cualquier agente nuevo**: su log va en `~/Library/Logs/platzi/` y
su label se registra en `AGENTES` de `backend/agentes.py`. Lo que no esté ahí,
no lo vigila nadie.

### Un asiento puede estar «listo» y no enviar nada

El 18-sep-2026 vp-3 tenía 7 invitaciones de cupo y 5 candidatos, y salió 0. El
parte lo decía: *«Chrome no respondió en el puerto de depuración (9415)»*.

La causa es una trampa de `li_login`: `asegurar()` **borra el pid guardado
antes** de llamar a `reclamar_puerto()`, y sin pid `_ocupado_por_otro()` da por
ajeno cualquier puerto que conteste — incluido el del propio Chrome del
asiento. Así que esquivó su navegador vivo en 9413, reclamó el 9415 y lanzó un
segundo Chrome sobre el mismo `user-data-dir`; Chrome no admite dos, el proceso
murió al instante y el puerto nunca contestó.

**No se arregla matando el Chrome**: ahí vive la sesión que el VP inició a mano.
Lo único que se había perdido era el apunte — y no era cosa de un asiento: esa
misma tarde los **cinco** estaban así, tres apuntando al 9415 y uno al 9417 con
sus Chrome vivos en 9411-9414 y 9416.

Por eso ahora `li_login` **adopta** en vez de relanzar. `_chrome_del_perfil()`
le pregunta al sistema (`ps`) qué proceso tiene abierto el `user-data-dir` del
asiento y con qué `--remote-debugging-port`; `adoptar()` guarda ese pid y ese
puerto. Es la pregunta que faltaba: `reclamar_puerto()` mira quién escucha en
cada puerto, nunca quién tiene abierto el perfil.

La adopción cuesta un `ps`, así que **no** vive en `chrome_vivo()` —`fase()` lo
llama en bucle desde la UI— sino en `vivo_o_adoptado()`, que usan `abrir()` y
`asegurar()`: una vez por job.

Este fallo es invisible desde fuera —el tick corre, el asiento figura «listo»,
el parte queda en un kv que nadie mira—, así que conviene revisar
`via_rapida_parte` cuando `enviadas` sea 0 con cupo y candidatos disponibles.

## Cargar contactos

Entran por `POST /api/contacts`, que dispara solo el cruce con HubSpot 🟢/🟡/🔴 y
deduplica por slug de LinkedIn, email y nombre+empresa. La importación por CSV
está apagada a propósito (`config.DEDUP_CSV` apunta a un archivo que no existe).

```bash
python3 ../conf-bogota/lote_50.py            # ver el lote
python3 ../conf-bogota/lote_50.py --subir    # cargarlo
```

## Los perfiles que firman (`backend/seats.py`)

Unos VP prestan su perfil de LinkedIn (nunca su buzón: el correo sale siempre del
equipo). Cada uno ocupa un **cupo** (`vp-1`, `vp-2`, …) y los cupos se abren
desde Ajustes → Perfiles con «Abrir un espacio para otro perfil»: el botón
estrena primero cualquier cupo apagado y sin contactos, y solo cuando no queda
ninguno crea uno nuevo con su propio puerto de depuración. Sumar un VP dejó de
ser editar `SEAT_IDS` y reiniciar el backend; los cuatro primeros siguen escritos
como semilla en `seats.py` y el resto vive en `kv` (`seats:ids`).

Un cupo que no se usa se apaga, no se borra: así su puerto y su historia quedan
reservados, y el botón lo reusa antes de gastar otra carpeta de Chrome.

Un cupo apagado desaparece de la contabilidad y de las alertas, **salvo** si
todavía carga contactos: ese asiento es el único que puede escribirles, así que
esconderlo dejaría contactos congelados sin nada en pantalla que lo explique.

Todo lo suyo vive en `kv` (`seat:<id>:<campo>`), editable desde Ajustes sin tocar
código. No son «el doble de cupo»: son bolsas separadas, por tres razones:

- **LinkedIn restringe por cuenta.** Un tope global deja que dos asientos se
  coman el del tercero, y el tercero es el que recibe la restricción. Por eso
  `cadence.cap_linkedin` dejó de ser configurable: ahora es la SUMA de lo que
  aguanta hoy cada perfil, y `POST /api/settings/cadence` con `cap_linkedin`
  responde 400 en vez de aceptarlo y hacer otra cosa.
- **Quien invita es quien mensajea.** El contacto queda amarrado a su asiento
  (`contacts.li_seat`) desde la primera invitación: solo aceptó a ESE perfil, y
  desde otro LinkedIn ni siquiera deja abrir el chat. Si su asiento se queda sin
  cupo, el contacto espera — nunca se reparte al que tenga cupo libre. Se puede
  reasignar a mano (`POST /api/contacts/<id>/seat`) **solo antes** del primer
  toque; después responde 409.
- **Un asiento sin sesión tiene cupo cero.** Mientras el VP no entregue su login
  no se le generan borradores. Encolarlos sería fabricar trabajo que nadie puede
  enviar y dejar su cupo comprometido con mensajes que envejecen en la cola.

**Repartir la base ya cargada** (`backend/reparto.py`). Los contactos nuevos se
reparten solos cuando el copiloto va a escribirles; esto es para el atrasado:

```bash
python3 reparto.py            # simula y muestra cómo quedaría
python3 reparto.py --aplicar  # escribe contacts.li_seat
```

Dos reglas que lo hacen distinto de partir la lista por la mitad:

- **Se reparte por empresa, no por contacto.** Dos personas de la misma compañía
  invitadas la misma semana por dos VP distintos de Platzi no se lee como dos
  acercamientos personales: se lee como una campaña. Y si hablan entre ellas
  —normal entre dos gerentes del mismo sitio— es peor que no haber escrito. La
  normalización quita sufijos societarios, así que «Woden Colombia S.A.S.» y
  «Woden Colombia» caen en el mismo perfil.
- **Proporcional al cupo, no mitad y mitad.** Con techos iguales sale 50/50;
  si un VP sube su techo o entra un tercero, la siguiente corrida se ajusta sola.

Lo que ya tuvo contacto por LinkedIn no se mueve nunca, y su empresa queda
anclada a ese mismo asiento.

En el **Pipeline** cada tarjeta lleva el nombre del VP que la firma, con color
propio, y hay filtro por remitente arriba.

**Calentamiento.** El cupo de invitaciones arranca en 6/día y sube 20% por
semana hasta el techo del asiento (20/día gratis, 40 con Sales Navigator, según
la auditoría en `PLATZI/linkedin-rate-limits/`). El reloj arranca con el primer
envío real, no al configurar el asiento: un perfil creado en agosto y estrenado
en octubre no se calentó por esperar. Hay además un tope semanal (LinkedIn corta
por semana antes que por día), y los mensajes a ya-conectados no se calientan:
van a gente que ya te aceptó, que es lo que LinkedIn mira.

### Cómo entra un VP (`backend/li_login.py`)

Hace **una** cosa: iniciar sesión en una ventana que le abrimos. Nada de pedirle
cookies, ni su contraseña, ni repetir el trámite cada semana.

1. En Ajustes → LinkedIn · Perfiles, **Abrir ventana para iniciar sesión**. Se
   lanza un Chrome propio de ese asiento, con su `--user-data-dir` y su
   `--remote-debugging-port`, en la pantalla de acceso de LinkedIn.
2. El VP escribe su correo, su clave y el código si LinkedIn lo pide. Eso ocurre
   en un Chrome normal: **el cockpit no ve ni guarda la contraseña**. Lo único
   que queda en disco es la sesión, en `data/chrome/<asiento>/`.
3. Apenas carga el feed, el cockpit lee su perfil (`linkedin.com/in/me`) y guarda
   nombre, titular y URL en el asiento. Nadie teclea el nombre, así que nadie se
   equivoca al hacerlo.

De ahí en adelante el VP no vuelve a aparecer. Si su ventana está cerrada cuando
hay algo que enviar, `asegurar()` la levanta sola con la sesión del disco; si la
sesión sí caducó, se dice con nombre propio en vez de dejar que el motor falle
con un `rate_limited` que no significa eso. **Quitar** borra la carpeta del
perfil: después de eso el cockpit no puede entrar a su LinkedIn ni con la ventana
abierta.

Tres detalles que muerden:

- **La ventana se abre en la máquina del cockpit**, no en la del VP: el navegador
  corre donde corre el backend. Para un VP remoto, lo práctico es una llamada
  compartiendo pantalla y dejarlo escribir.
- **El puerto 9334 es del operador** (`PLATZI_LINKEDIN_CDP`). Los asientos
  empiezan en 9411 y reclaman el primero libre, y antes de usar uno comprueban
  que quien contesta sea *su* Chrome (pid propio). Sin esa comprobación, un
  asiento encontraba el Chrome del operador, lo daba por bueno y habría enviado
  desde la cuenta del operador firmando como el VP.
- **Un Chrome abierto no es una sesión.** Mientras no haya un login verificado el
  asiento queda en cupo 0, aunque la ventana esté ahí: deslogueado, LinkedIn
  manda `/feed/` a su portada, que no dice «login» en ninguna parte.

**La sesión no está en cookies, está en el perfil.** `data/chrome/<asiento>/`
sobrevive a cerrar la ventana y a reiniciar la máquina: eso es lo que hace que el
VP entre una sola vez. Aun así, tras cada login se guarda un **respaldo** de las
cookies en `data/cookies/<asiento>.json` (`POST /api/seats/<id>/cookies/respaldar`
para forzarlo), con dos advertencias que hay que tener claras:

- **No es una segunda forma de entrar.** Restauradas en un navegador limpio,
  LinkedIn ve una huella nueva y manda a un checkpoint — que le llega al VP, no a
  nosotros. Sirve para un rescate en la máquina de siempre, no para montar la
  sesión en otra parte ni para ahorrarle el trámite a un VP nuevo.
- **Es la sesión de otra persona.** El respaldo se escribe `0600` y las carpetas
  `0700`; con los permisos por defecto cualquier cuenta de la máquina podría leer
  el archivo y entrar como ese VP.

El respaldo publica hasta cuándo sirve la sesión (`li_at`), que es la única fecha
que importa: normalmente un año desde el login.

**Un job por asiento y uno solo.** Al enviar, cada perfil lanza su propio job con
su sesión y su cupo; si a un asiento le tocan invitaciones y mensajes a la vez,
salen primero los mensajes y las invitaciones quedan para la corrida siguiente:
dos motores sobre el mismo Chrome se pelean el navegador y dejan las pestañas
trancadas, que se ve como `rate_limited` sin serlo.

**Aceptaciones.** El chequeo diario re-visita los perfiles invitados **desde la
sesión de quien los invitó**, en serie. Mirarlos desde otro perfil los reporta
como no conectados, y ese falso negativo congela la cadencia: el paso que
escribe al que aceptó nunca se dispara.

```bash
curl -s  localhost:8532/api/seats                        # cupo y sesión de cada perfil
curl -X POST localhost:8532/api/seats/vp-1/login/abrir    # abre su ventana
curl -s  localhost:8532/api/seats/vp-1/login/estado       # ¿ya entró? (sondeo barato)
curl -X POST localhost:8532/api/seats/vp-1/login/guardar   # lee y guarda su perfil
curl -X POST localhost:8532/api/seats/vp-1/despertar       # levanta la ventana dormida
curl -X POST localhost:8532/api/seats/vp-1/login/olvidar   # se salió del programa
curl -X POST localhost:8532/api/seats/vp-1 \
     -H 'Content-Type: application/json' -d '{"es_anfitrion":true,"tope_dia":12}'
```

El copy lo sabe: si el perfil que firma es el anfitrión del desayuno, el paso 1
sale en primera persona («Estoy armando…») en vez de hablar de sí mismo en
tercera, que es justo como se ve un mensaje que escribió otro.

### Una pestaña por VP, no una por preparación

`asegurar()` corre **antes de cada job**, y hasta el 18-sep-2026 cada pasada
abría una pestaña con `/json/new` sin cerrar ninguna. Con un tick cada 15 min
eso es acumulación garantizada: vp-3 tenía seis copias del mismo
`/feed/foryou/`, cada una recargándose sola en segundo plano dentro de la sesión
del VP.

Ahora `li_login` trae `una_pestana()` y `podar()`:

- `una_pestana(seat, url)` abre la nueva y cierra **las que ya estaban,
  anotadas por id**. Por id y no por posición porque el orden en que Chrome
  lista sus targets no es un contrato; y abriendo antes de cerrar porque dejar
  la ventana sin pestañas un instante, en macOS, es cerrarla.
- `podar(seat)` es el barrendero, y se llama en el camino normal de
  `asegurar()` (fase `dentro`), que es justo el momento previo a cada envío.

**La poda solo toca lo reciclable**: `about:blank`, la pestaña nueva de Chrome y
`linkedin.com/feed`. Una pestaña en `/in/`, `/checkpoint/` o `/messaging` se
queda donde está — puede ser el runner mandando una invitación o el VP a mitad
de una verificación, y cerrarla rompería cualquiera de las dos cosas.

`/json/close/<id>` contesta **texto plano**, no JSON, así que no se puede usar
el helper `_cdp()`; y cierra en diferido: si se relee la lista de inmediato
todavía salen las pestañas viejas.

Los runners (`bulk_connect/runner.py`, `accept_checker.py`, `li_senales.py`,
`li_perfil.py`, `followup_runner.py`) ya estaban bien: abren **una** página para
toda la corrida, la navegan perfil por perfil y la cierran al terminar. Sobre
CDP cierran solo su pestaña, nunca el navegador del VP.

## Heredado

`_heredado-klik/` guarda los módulos del cockpit del que salió este (conector a
otro CRM, base de clientes en CSV, dos validadores atados a columnas de otro
mercado). No los importa nadie; se pueden borrar.
