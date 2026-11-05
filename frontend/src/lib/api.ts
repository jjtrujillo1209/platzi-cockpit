export type Contact = {
  id: string;
  nombre: string;
  email: string;
  cargo: string;
  celular: string;
  ciudad: string;
  sector: string;
  linkedin: string;
  empresa: string;
  tipo: string;
  estado: string;
  campaign: string;
  tier: string;
  linkedin_status: string;
  linkedin_channel: string;
  linkedin_ts: string;
  email_status: string;
  email_ts: string;
  email_enviado: boolean;
  email_bounced: boolean;
  bounce_reason: string;
  validacion: string;
  empresa_linkedin: string;
  li_seat: string; li_seat_nombre: string;   // perfil de LinkedIn que lo firma
  call_key: string;
  llamada: boolean;
  llamada_outcome: string;
  llamada_notes: string;
  llamada_ts: string;
};

/** Petición de prospección pedida a mano desde el cockpit. La máquina las
 *  resuelve en su vuelta de 60s (ver modulo-prospeccion/backend/maquina.py). */
export type Peticion = {
  id: number;
  consulta: string;
  empresa: string;
  dominio: string;
  pidio: string;
  estado: "pendiente" | "en_proceso" | "resuelta" | "frenada" | "sin_contactos" | "error" | "cancelada";
  nota: string;
  decisores: { nombre: string; cargo: string; email: string; linkedin: string; celular: string; hubspot?: string }[];
  contactos_nuevos: number;
  created_at: string;
  updated_at: string;
};

export type HubspotVerdict = "verde" | "amarillo" | "rojo";

/** Comparativa leads vs prospectos según el cruce de la base con HubSpot. */
export type HubspotSummary = {
  configured: boolean;
  total: number;
  con_email: number;
  checked: number;
  pendientes: number;
  sin_email: number;
  verdicts: Record<HubspotVerdict, number>;
  motivos: Array<{ verdict: HubspotVerdict; reason: string; n: number }>;
  last_check: string;
};

export type RunEvent = {
  idx: number;
  total: number;
  url?: string;
  email?: string;
  nombre?: string;
  empresa?: string;
  status: string;
  detail?: string;
  ts?: string;
};

export type JobProgress = {
  job_id: string;
  channel: string;
  status: string;
  total: number;
  done: number;
  started_at: string;
  events_file: string;
  error?: string;
  cap_info?: { cap: number; usado_hoy: number; remaining: number; solicitados: number; enviados: number };
};

export type OrgProfile = {
  nombre: string;
  cargo: string;
  area: string;
  linkedin: string;
  email?: string;
  celular?: string;
  validacion?: string;
};

export type OrgChartResult = {
  ok: boolean;
  error?: string;
  company?: string;
  domain?: string;
  total_candidates?: number;
  profiles?: OrgProfile[];
  note?: string;
};

export type NewContact = {
  nombre?: string;
  cargo?: string;
  linkedin?: string;
  empresa?: string;
  email?: string;
  celular?: string;
  ciudad?: string;
  sector?: string;
  campaign?: string;
  tier?: string;
  validacion_empresa?: string;
  validacion?: string;
};

export type AddContactsResult = {
  ok: boolean;
  error?: string;
  added: number;
  skipped: Array<{ nombre: string; empresa: string; reason: string }>;
  total: number;
};

export type BulkCompany = {
  empresa?: string;
  domain?: string;
  nit?: string;
  sector?: string;
  ciudad?: string;
};

export type BulkResultEntry = {
  empresa: string;
  status: "agregado" | "duplicado" | "encontrado" | "sin_perfiles" | "ya_cliente" | "error" | "";
  profiles: OrgProfile[];
  added: number;
  detail: string;
};

export type BulkJob = {
  ok?: boolean;
  error?: string;
  job_id: string;
  status: "running" | "done" | "stopped" | "error" | "sin_creditos";
  total: number;
  done: number;
  profiles_found: number;
  added: number;
  skipped_dup: number;
  ya_cliente: number;
  auto_add: boolean;
  started_at: string;
  finished_at: string;
  results: BulkResultEntry[];
};

export type EnrichResultEntry = {
  nombre: string;
  empresa: string;
  linkedin: string;
  status: "actualizado" | "sin_datos" | "no_encontrado_en_base" | "error" | "";
  detail: string;
};

export type EnrichJob = {
  ok?: boolean;
  error?: string;
  job_id: string;
  status: "running" | "done" | "stopped" | "sin_creditos";
  total: number;
  done: number;
  found: number;
  updated: number;
  sin_datos: number;
  error_count: number;
  started_at: string;
  finished_at: string;
  results: EnrichResultEntry[];
};

// ----------------------------- Copiloto (agente) --------------------------
export type Stage =
  | "nuevo" | "contactado" | "conectado" | "no_responde" | "en_conversacion"
  | "reunion" | "cliente" | "descartado";

export type PipelineContact = {
  id: number; nombre: string; empresa: string; cargo: string; email: string;
  linkedin: string; celular: string; sector: string; stage: Stage; notas: string;
  last_activity?: string; seq_status?: string; updated_at: string;
  hubspot_verdict?: string; hubspot_reason?: string;
  li_seat?: string;   // perfil de LinkedIn que firma a este prospecto
};

export type PipelineSummary = {
  counts: Record<Stage, number>;
  stages: Record<Stage, PipelineContact[]>;
  total: number;
  // id del asiento → nombre del VP que firma. Viene resuelto del backend.
  remitentes: Record<string, string>;
  sin_remitente: number;
};

/** Carril automático de LinkedIn para lo que entra por Platzi Ventas
 *  (23-platzi-ventas.vercel.app). Ver cockpit/backend/conexiones.py. */
export type ViaRapida = {
  activo: boolean;
  origenes: string[];
  /** Perfiles que atienden este origen en exclusiva (Seat.prioriza). */
  atienden: { seat_id: string; vp: string; rol: string; listo: boolean;
              cupo: number; secuencia: string }[];
  ventana: string;
  toca_ahora: string;
  pendientes: { sin_asiento: number; con_asiento: number; total: number };
  ultima_corrida: string;
  ultimo_parte: string;
};

export type PipelineVentas = {
  origenes: string[];
  pipeline: PipelineSummary;
  via_rapida: ViaRapida;
};

/** Secuencia de Amplemarket por perfil de VP: quien acepta la invitación de
 *  ese perfil entra en SU secuencia. Ver cockpit/backend/secuencias_am.py. */
export type SecuenciasAm = {
  activo: boolean;
  etapas: string[];
  tope_automatico: number;
  asientos: { seat_id: string; vp: string; secuencia: string; secuencia_nombre: string }[];
  pendientes: number;
  ultima_corrida: string;
  ultimo_parte: string;
};

/** Secuencia de HubSpot por perfil. Mismo reparto que la de Amplemarket, otro
 *  motor: el correo sale de la bandeja de Google del propio vendedor
 *  (`remitente`), no de una herramienta externa. Los dos motores son
 *  excluyentes por asiento — `conflictos` lista los que tienen las dos puestas
 *  y por eso no envían por ninguno. Ver cockpit/backend/secuencias_hs.py. */
export type SecuenciasHs = {
  activo: boolean;
  etapas: string[];
  tope_automatico: number;
  tope_dia: number;
  asientos: {
    seat_id: string; vp: string; secuencia: string; secuencia_nombre: string;
    remitente: string; enviados_hoy: number; listo: boolean;
  }[];
  conflictos: { seat_id: string; vp: string }[];
  pendientes: number;
  ultima_corrida: string;
  ultimo_parte: string;
};

export type Activity = {
  id: number; contact_id: number; channel: string; type: string;
  ts: string; run_id: string; meta: Record<string, any>;
  // Quién ejecutó la acción. `seat_inferido` = no venía grabada en la actividad
  // (es anterior a los asientos) y se dedujo del asiento asignado hoy.
  seat_id?: string; seat_nombre?: string; seat_inferido?: boolean;
};

export type FichaProspecto = {
  contact: PipelineContact;
  timeline: Activity[];
  remitentes: Record<string, string>;
  seat_asignado: { id: string; nombre: string };
  aprobaciones: Array<{ id: number; channel: string; status: string;
                        draft_subject: string; draft_body: string;
                        created_at: string; blocked_reasons: string; model: string }>;
  sequences: any[];
};

export type Approval = {
  id: number; contact_id: number; contact_sequence_id: number | null;
  step_id: number | null; channel: "linkedin_invite" | "linkedin_msg" | "email";
  draft_subject: string; draft_body: string; status: string;
  created_at: string; nombre: string; empresa: string; cargo: string;
  email: string; linkedin: string; stage: Stage; step_order?: number;
  li_seat?: string;   // qué perfil de LinkedIn firma este toque
};

export type Task = {
  id: number; contact_id: number | null; type: "call" | "reply_review" | "manual";
  due_at: string; status: string; notes: string; meta: Record<string, any>;
  nombre?: string; empresa?: string; email?: string; celular?: string; linkedin?: string;
};

// ---- Asientos de LinkedIn: los perfiles que firman el outreach ----------
export type Bolsa = { cap: number; used: number; queued: number; remaining: number };

// Dónde va el VP en el trámite de entrar. Lo sondea la UI en bucle, así que es
// barato a propósito: sale de leer las pestañas del Chrome, no de abrir páginas.
export type LoginEstado = {
  seat_id: string; puerto: number; endpoint: string;
  fase: "cerrado" | "login" | "verifica" | "dentro" | "otro";
  detalle: string; chrome_abierto: boolean;
  perfil_en_disco: boolean; login_ts: string; perfil_url: string;
  // Paracaídas del perfil de Chrome, no una forma de entrar (restaurar cookies
  // en un navegador limpio termina en checkpoint).
  respaldo: { existe: boolean; archivo: string; fecha: string; li_at_vence: string };
  urls?: string[];
};

export type Seat = {
  seat_id: string; nombre: string; nombre_real: string; rol: string;
  login: LoginEstado;
  activo: boolean; configurado: boolean; listo: boolean;
  sesion: { ok: boolean; detalle: string; via: "cdp" | "cookies" };
  es_anfitrion: boolean; sales_nav: boolean; warmup_start: string; tope_dia: number;
  invites: Bolsa;
  semana: { cap: number; used: number; remaining: number };
  mensajes: Bolsa;
  asignados: number;
};

export type SeatsResumen = {
  seats: Seat[]; listos: number; total: number;
  // Cupos apagados y sin contactos: se pueden estrenar con otro VP.
  libres: string[];
  cap_invites: number; used_invites: number; queued_invites: number; remaining_invites: number;
  cap_mensajes: number; used_mensajes: number; queued_mensajes: number; remaining_mensajes: number;
  problemas: string[];
};

export type AgentStatus = {
  cadence: { enabled: boolean; cap_linkedin: number; cap_email: number;
             seats_listos: number; seats_total: number;
             tick_minutes: number; last_tick: string };
  budgets: {
    linkedin: Bolsa;
    linkedin_invites: Bolsa;
    linkedin_mensajes: Bolsa;
    seats: Seat[];
    seats_problemas: string[];
    email: Bolsa;
  };
  seats: SeatsResumen;
  gmail: { state: "not_configured" | "needs_connect" | "connected" | "error";
           token_exists: boolean; connected_email: string; last_error: string; last_check: string };
  ai_available: boolean;
  pending_approvals: number;
  last_tick_result: any;
};

// ---- Trazabilidad de la campaña (Amplemarket envía; acá se demuestra) ----
export type TrazaFila = {
  contact_id: number; nombre: string; empresa: string; cargo: string;
  email: string; linkedin: string; celular: string; stage: Stage;
  hubspot_verdict: string; codigo: string; link: string; gancho: string;
  cola_estado: "" | "pending" | "blocked" | "approved" | "sent" | "confirmed" | "failed" | "skipped";
  cola_razones: string; modelo: string; inscrito_ts: string; secuencia: string;
  clics: number; clic_ts: string; respuestas: number; respuesta_ts: string;
  registro: string; registro_ts: string; registro_atribuido: boolean;
};

export type TrazaEmbudo = {
  con_link: number; preparados: number; bloqueados: number; pendientes: number;
  aprobados: number; inscritos: number; saltados: number; con_clic: number;
  con_respuesta: number; registrados: number; registrados_atribuidos: number;
};

export type Trazabilidad = {
  campaign: { slug: string; name: string; event_day: string; cutoff_day: string };
  preflight_campana: { ok: boolean; problems: string[]; days_to_event: number };
  secuencia: {
    id: string; name: string; status: string; url: string;
    dynamic_fields_used: string[]; ok: boolean;
    problems: string[]; warnings: string[];
    /** Fecha en que una persona firmó que la plantilla lleva los campos. */
    verificada: string;
    campos_esperados: string[];
  };
  webhook: { configurado: boolean; url: string };
  embudo: TrazaEmbudo;
  filas: TrazaFila[];
};

export const TOKEN_KEY = "platzi_token";

// El token vive en sessionStorage, no en localStorage: al cerrar la pestaña la
// sesión se acaba y hay que volver a entrar con Google. Es por pestaña, así que
// dos pestañas no comparten sesión.
export function getToken(): string {
  const viejo = localStorage.getItem(TOKEN_KEY);
  if (viejo) {
    // Token de antes del cambio: se muda una vez y se borra el rastro
    // persistente, que es justo lo que queríamos quitar de en medio.
    localStorage.removeItem(TOKEN_KEY);
    if (!sessionStorage.getItem(TOKEN_KEY)) sessionStorage.setItem(TOKEN_KEY, viejo);
  }
  return sessionStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t: string) {
  localStorage.removeItem(TOKEN_KEY);
  t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY);
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const tok = getToken();
  const r = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
    },
    ...init,
  });
  if (r.status === 401) {
    setToken("");
    location.reload();
    throw new Error("401");
  }
  if (!r.ok) {
    // El backend explica sus rechazos en `error` ("no quedan puertos libres",
    // "ese LinkedIn ya está en otro asiento"…). Sin esto la pantalla mostraba
    // «409 /api/seats/nuevo», que no le dice a nadie qué hacer.
    const cuerpo = await r.json().catch(() => null);
    throw new Error(cuerpo?.error || `${r.status} ${url}`);
  }
  return r.json();
}

/** Salud de los agentes de launchd + latido de la máquina de prospección.
 *  El tablero de 23-platzi-ventas no sabe distinguir «máquina sana con la cola
 *  vacía» de «máquina apagada»: su aviso sale del último POST que le llega.
 *  Esto sí, porque le pregunta a launchd. Ver backend/agentes.py. */
export type SaludAgente = {
  label: string; nombre: string; hace: string; critico: boolean;
  pid: number | null; ultimo_codigo: number | null; ultima_senal: number | null;
  log: string | null; log_protegido: boolean; log_con_macl: boolean;
  veredicto: "corriendo" | "vivo" | "no_arranca" | "fallando" | "descargado";
  detalle: string; aviso?: string;
};
export type Salud = {
  ts: string; sano: boolean; problemas: string[];
  agentes: SaludAgente[];
  avisos: { label: string; nombre: string; aviso: string }[];
  maquina: {
    veredicto: "viva" | "mudo" | "fallando" | "nunca" | "ilegible";
    detalle: string; edad_min: number | null; ts?: string;
    estado_pasada?: string; error?: string; saldo_apollo?: number; ruta: string;
  };
};

export const api = {
  health: () => j<any>("/api/health"),
  agentes: () => j<Salud>("/api/agentes"),
  repararAgentes: (label?: string) =>
    j<any>(`/api/agentes/reparar${label ? `?label=${encodeURIComponent(label)}` : ""}`,
           { method: "POST" }),
  contacts: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return j<{ total: number; contacts: Contact[] }>(`/api/contacts?${qs}`);
  },
  facets: () => j<any>("/api/contacts/facets"),
  peticiones: (estado = "") =>
    j<{ peticiones: Peticion[]; resumen: Record<string, number> }>(
      `/api/peticiones?estado=${encodeURIComponent(estado)}`),
  crearPeticion: (consulta: string) =>
    j<{ ok: boolean; repetida: boolean; peticion: Peticion }>("/api/peticiones", {
      method: "POST", body: JSON.stringify({ consulta }),
    }),
  cancelarPeticion: (id: number) =>
    j<any>(`/api/peticiones/${id}/cancelar`, { method: "POST" }),
  reintentarPeticion: (id: number) =>
    j<any>(`/api/peticiones/${id}/reintentar`, { method: "POST" }),
  templates: () => j<{ templates: any[] }>("/api/templates"),
  overview: () => j<any>("/api/stats/overview"),
  emailStats: () => j<any>("/api/stats/email"),
  history: () => j<{ runs: any[] }>("/api/history"),
  historyDetail: (runId: string, channel: string) =>
    j<any>(`/api/history/${runId}?channel=${channel}`),
  jobs: () => j<{ jobs: JobProgress[] }>("/api/jobs"),
  launchLinkedin: (body: any) =>
    j<JobProgress>("/api/jobs/linkedin", { method: "POST", body: JSON.stringify(body) }),
  launchFollowup: (body: any) =>
    j<JobProgress>("/api/jobs/followup", { method: "POST", body: JSON.stringify(body) }),
  launchEmail: (body: any) =>
    j<JobProgress>("/api/jobs/email", { method: "POST", body: JSON.stringify(body) }),
  stopJob: (id: string) => j<JobProgress>(`/api/jobs/${id}/stop`, { method: "POST" }),
  calls: () => j<Record<string, any>>("/api/calls"),
  setCall: (body: { key: string; called: boolean; outcome?: string; notes?: string; by?: string }) =>
    j<any>("/api/calls", { method: "POST", body: JSON.stringify(body) }),
  hubspotSummary: () => j<HubspotSummary>("/api/hubspot/summary"),
  hubspotBackfill: (limit = 500) =>
    j<{ ok: boolean; error?: string; checked: number; summary: Record<string, number> }>(
      `/api/hubspot/backfill?limit=${limit}`, { method: "POST" }),
  sendgridStatus: () => j<{ configured: boolean; masked: string; from_email: string; from_name: string; bcc: string }>("/api/settings/sendgrid"),
  sendgridUpdate: (body: { api_key?: string; from_email?: string; from_name?: string; bcc?: string }) =>
    j<any>("/api/settings/sendgrid", { method: "POST", body: JSON.stringify(body) }),
  sendgridTest: (api_key?: string) =>
    j<any>("/api/settings/sendgrid/test", { method: "POST", body: JSON.stringify({ api_key: api_key || null }) }),
  heartbeat: () => j<any>("/api/heartbeat"),
  runHeartbeat: (checkLinkedin: boolean) =>
    j<any>(`/api/heartbeat/run?check_linkedin=${checkLinkedin}`, { method: "POST" }),
  settingsAll: () => j<any>("/api/settings/all"),
  anthropicUpdate: (api_key: string) => j<any>("/api/settings/anthropic", { method: "POST", body: JSON.stringify({ api_key }) }),
  anthropicTest: (api_key?: string) => j<any>("/api/settings/anthropic/test", { method: "POST", body: JSON.stringify({ api_key: api_key || null }) }),
  linkedinUpdate: (cookies: string) => j<any>("/api/settings/linkedin", { method: "POST", body: JSON.stringify({ cookies }) }),
  googleUpdate: (body: { client_id?: string; allowed_domain?: string }) => j<any>("/api/settings/google", { method: "POST", body: JSON.stringify(body) }),
  apolloUpdate: (api_key: string) => j<any>("/api/settings/apollo", { method: "POST", body: JSON.stringify({ api_key }) }),
  apolloTest: (api_key?: string) => j<any>("/api/settings/apollo/test", { method: "POST", body: JSON.stringify({ api_key: api_key || null }) }),
  apolloBudgetGet: () => j<{ budget: number; used: number; remaining: number; updated_at: string }>("/api/settings/apollo/budget"),
  apolloBudgetSet: (value: number) => j<{ budget: number; used: number; remaining: number; updated_at: string }>("/api/settings/apollo/budget", { method: "POST", body: JSON.stringify({ value }) }),
  prospectOrgchart: (body: { company?: string; domain?: string }) =>
    j<OrgChartResult>("/api/prospect/orgchart", { method: "POST", body: JSON.stringify(body) }),
  addContacts: (contacts: NewContact[]) =>
    j<AddContactsResult>("/api/contacts", { method: "POST", body: JSON.stringify({ contacts }) }),
  reloadContacts: () => j<{ ok: boolean; total: number }>("/api/contacts/reload", { method: "POST" }),
  prospectBulkStart: (body: { companies: BulkCompany[]; auto_add?: boolean }) =>
    j<BulkJob>("/api/prospect/bulk", { method: "POST", body: JSON.stringify(body) }),
  prospectBulkStatus: (jobId: string) => j<BulkJob>(`/api/prospect/bulk/${jobId}`),
  prospectBulkStop: (jobId: string) =>
    j<BulkJob>(`/api/prospect/bulk/${jobId}/stop`, { method: "POST" }),
  enrichPending: () => j<{ total: number; contacts: PipelineContact[] }>("/api/enrich/pending"),
  enrichBulkStart: () => j<EnrichJob>("/api/enrich/bulk", { method: "POST" }),
  enrichBulkStatus: (jobId: string) => j<EnrichJob>(`/api/enrich/bulk/${jobId}`),
  enrichBulkStop: (jobId: string) =>
    j<EnrichJob>(`/api/enrich/bulk/${jobId}/stop`, { method: "POST" }),
  authConfig: () => j<{ enabled: boolean; client_id: string; allowed_domain: string }>("/api/auth/config"),
  googleLogin: (credential: string) =>
    j<any>("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) }),

  // ---- Copiloto (agente) ----
  pipeline: (limit = 100) => j<PipelineSummary>(`/api/pipeline?limit=${limit}`),

  // ---- Pipeline Ventas (lo que llega de 23-platzi-ventas) ----
  pipelineVentas: () => j<PipelineVentas>("/api/pipeline-ventas"),

  // ---- Secuencias de Amplemarket por perfil ----
  secuenciasAm: () => j<SecuenciasAm>("/api/secuencias-am"),
  secuenciasAmSwitch: (activo: boolean) =>
    j<SecuenciasAm>("/api/secuencias-am", {
      method: "POST", body: JSON.stringify({ activo }),
    }),
  // El botón «secuencia» de cada tarjeta del Pipeline. No usa
  // /api/sequences/enroll: eso exige una Campaign con copy en el cockpit y hoy
  // no hay ninguna. Aquí el copy vive en la plantilla de Amplemarket.
  secuenciasAmInscribir: (contact_ids: number[]) =>
    j<{ inscritos: number; ya_estaban?: number; saltados: number; error?: string }>(
      "/api/secuencias-am/inscribir",
      { method: "POST", body: JSON.stringify({ contact_ids }) }),
  secuenciasAmCorrer: (simular = false) =>
    j<any>(`/api/secuencias-am/correr?simular=${simular}`, { method: "POST" }),

  // ---- Secuencias de HubSpot por perfil (el correo sale del buzón del VP) ----
  secuenciasHs: () => j<SecuenciasHs>("/api/secuencias-hs"),
  secuenciasHsSwitch: (activo: boolean) =>
    j<SecuenciasHs>("/api/secuencias-hs", {
      method: "POST", body: JSON.stringify({ activo }),
    }),
  secuenciasHsInscribir: (contact_ids: number[]) =>
    j<{ inscritos: number; saltados: number; creados?: number; error?: string }>(
      "/api/secuencias-hs/inscribir",
      { method: "POST", body: JSON.stringify({ contact_ids }) }),
  secuenciasHsCorrer: (simular = false) =>
    j<any>(`/api/secuencias-hs/correr?simular=${simular}`, { method: "POST" }),
  viaRapidaSwitch: (activo: boolean) =>
    j<ViaRapida>("/api/conexiones/via-rapida", {
      method: "POST", body: JSON.stringify({ activo }),
    }),
  viaRapidaCorrer: (simular = false) =>
    j<any>(`/api/conexiones/via-rapida/correr?simular=${simular}&forzar=true`,
           { method: "POST" }),
  setStage: (id: number, stage: Stage) =>
    j<any>(`/api/contacts/${id}`, { method: "PATCH", body: JSON.stringify({ stage }) }),
  updateContact: (
    id: number,
    fields: Partial<Pick<PipelineContact, "nombre" | "empresa" | "cargo" | "email" | "celular" | "linkedin">>,
  ) => j<{ ok: boolean; contact: PipelineContact }>(`/api/contacts/${id}`,
    { method: "PATCH", body: JSON.stringify(fields) }),
  setNotes: (id: number, notas: string) =>
    j<any>(`/api/contacts/${id}/notes`, { method: "PATCH", body: JSON.stringify({ notas }) }),
  timeline: (id: number) => j<FichaProspecto>(`/api/contacts/${id}/timeline`),
  syncRun: () => j<any>("/api/sync/run", { method: "POST" }),
  ingestRun: () => j<any>("/api/ingest/run", { method: "POST" }),
  agentStatus: () => j<AgentStatus>("/api/agent/status"),
  agentTick: () => j<any>("/api/agent/tick", { method: "POST" }),
  // El cupo de LinkedIn ya no se manda aquí: es de cada asiento (POST /api/seats/:id).
  cadenceUpdate: (body: { enabled?: boolean; cap_email?: number }) =>
    j<any>("/api/settings/cadence", { method: "POST", body: JSON.stringify(body) }),
  seats: () => j<SeatsResumen>("/api/seats"),
  // Abre un espacio para otro perfil. Si hay un cupo apagado y sin contactos, el
  // backend lo estrena en vez de crear otro: un asiento cuesta una carpeta de
  // Chrome y un puerto.
  seatCrear: () => j<{ ok: boolean; seat: Seat }>("/api/seats/nuevo", { method: "POST" }),
  seatUpdate: (id: string, body: Partial<{
    nombre: string; rol: string; activo: boolean; sales_nav: boolean;
    es_anfitrion: boolean; cdp: string; tope_dia: number; tope_semana: number;
    tope_msgs: number; warmup_start: string;
  }>) => j<{ ok: boolean; seat: Seat }>(`/api/seats/${id}`, { method: "POST", body: JSON.stringify(body) }),
  seatCookies: (id: string, cookies: string) =>
    j<{ ok: boolean; seat: Seat }>(`/api/seats/${id}/cookies`, { method: "POST", body: JSON.stringify({ cookies }) }),
  seatTest: (id: string) =>
    j<{ ok: boolean; sesion: Seat["sesion"]; seat: Seat }>(`/api/seats/${id}/test`, { method: "POST" }),
  // Entrada de sesión del VP: abrir → sondear → guardar. Nada más.
  seatLoginAbrir: (id: string) =>
    j<{ ok: boolean; puerto: number; fase: string; detalle: string; error?: string }>(
      `/api/seats/${id}/login/abrir`, { method: "POST" }),
  seatLoginEstado: (id: string) => j<LoginEstado>(`/api/seats/${id}/login/estado`),
  seatLoginGuardar: (id: string) =>
    j<{ ok: boolean; perfil: { nombre: string; titular: string; url: string }; seat: Seat }>(
      `/api/seats/${id}/login/guardar`, { method: "POST" }),
  seatLoginCerrar: (id: string) =>
    j<{ ok: boolean }>(`/api/seats/${id}/login/cerrar-ventana`, { method: "POST" }),
  seatLoginOlvidar: (id: string) =>
    j<{ ok: boolean }>(`/api/seats/${id}/login/olvidar`, { method: "POST" }),
  seatRespaldar: (id: string) =>
    j<{ ok: boolean; n?: number; li_at_vence?: string; error?: string }>(
      `/api/seats/${id}/cookies/respaldar`, { method: "POST" }),
  seatDespertar: (id: string) =>
    j<{ ok: boolean; levantado?: boolean; error?: string }>(
      `/api/seats/${id}/despertar`, { method: "POST" }),
  contactSeat: (contactId: number, seat_id: string) =>
    j<any>(`/api/contacts/${contactId}/seat`, { method: "POST", body: JSON.stringify({ seat_id }) }),
  enroll: (contact_ids: number[]) =>
    j<{ ok: boolean; enrolled: number; already: number; blocked: any[]; blocked_count: number }>(
      "/api/sequences/enroll", { method: "POST", body: JSON.stringify({ contact_ids }) }),
  hubspotCheck: (contact_id: number) =>
    j<{ ok: boolean; exists?: boolean; verdict?: string; reason?: string;
        lifecyclestage_label?: string; open_deals?: number }>(
      `/api/contacts/${contact_id}/hubspot`),
  approvals: (status = "pending") => j<{ approvals: Approval[] }>(`/api/approvals?status=${status}`),
  approve: (id: number, subject?: string, body?: string) =>
    j<any>(`/api/approvals/${id}/approve`, { method: "POST", body: JSON.stringify({ subject, body }) }),
  skipApproval: (id: number) => j<any>(`/api/approvals/${id}/skip`, { method: "POST" }),
  bulkApprove: (ids: number[]) =>
    j<any>("/api/approvals/bulk-approve", { method: "POST", body: JSON.stringify({ ids }) }),
  sendApproved: (dry_run_email = false) =>
    j<any>("/api/approvals/send", { method: "POST", body: JSON.stringify({ dry_run_email }) }),
  tasks: (status = "open") => j<{ tasks: Task[] }>(`/api/tasks?status=${status}`),
  taskAction: (id: number, action: "done" | "dismiss") =>
    j<any>(`/api/tasks/${id}/${action}`, { method: "POST" }),
  briefing: (force = false) =>
    j<{ date: string; text: string }>(`/api/briefing?force=${force}`),
  emailEngagement: () => j<EmailEngagement>("/api/stats/email/engagement"),
  gmailStatus: () => j<GmailStatus>("/api/settings/gmail"),
  gmailSaveSecret: (content: string) =>
    j<{ ok: boolean; error?: string; client_id?: string; type?: string }>(
      "/api/settings/gmail/client-secret", { method: "POST", body: JSON.stringify({ content }) }),
  gmailConnect: () =>
    j<{ ok: boolean; error?: string; email?: string; messages_total?: number }>(
      "/api/settings/gmail/connect", { method: "POST" }),
  gmailCheck: () => j<any>("/api/settings/gmail/check", { method: "POST" }),

  // ---- Campaña / secuencia de Amplemarket ----
  campaigns: () => j<{ campaigns: Array<{ slug: string; name: string; event_day: string;
                       cutoff_day: string; steps: number; missing: string[]; preflight: any }> }>(
    "/api/campaigns"),
  trazabilidad: (slug: string) =>
    j<Trazabilidad>(`/api/campaigns/${slug}/trazabilidad`),
  plantillaVerificada: (verificada: boolean, slug: string) =>
    j<{ ok: boolean; error?: string; verificada: string }>(
      `/api/campaigns/${slug}/amplemarket/plantilla-verificada`,
      { method: "POST", body: JSON.stringify({ verificada }) }),
};

export type GmailStatus = {
  state: "not_configured" | "needs_connect" | "connected" | "error";
  client_secret_exists: boolean;
  token_exists: boolean;
  connected_email: string;
  replies_db_exists: boolean;
  last_error: string;
  last_check: string;
};

export type EngagementRow = {
  id: number; nombre: string; empresa: string; cargo: string; email: string;
  stage: Stage; sends: number; last_sent: string | null;
  opens: number; last_open: string | null; clicks: number; last_click: string | null;
  cta_clicks: number; replies: number; bounces: number; spam: number; bounce_reason: string;
};

export type EmailEngagement = {
  webhook_active: boolean; total: number; clickers: number; cta_clickers: number;
  openers: number; bounced: number; rows: EngagementRow[];
};

export function jobSocket(jobId: string): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const tok = getToken();
  const qs = tok ? `?token=${encodeURIComponent(tok)}` : "";
  return new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}${qs}`);
}
