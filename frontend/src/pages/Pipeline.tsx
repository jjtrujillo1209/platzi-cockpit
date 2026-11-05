import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RefreshCw, PlayCircle, Clock, Phone, MessageCircle, Mail, Linkedin, StickyNote,
  UserPlus, X, AlertTriangle, Check, Target, Zap, ZapOff,
} from "lucide-react";
import clsx from "clsx";
import { api, EnrichJob, NewContact, PipelineContact, SecuenciasAm, SecuenciasHs, Stage } from "../lib/api";
import { STAGES } from "../lib/stages";
import { useContactNotes } from "../lib/useContactNotes";
import { Card, Empty, Spinner } from "../components/ui";
import { Header } from "./Dashboard";
import { Field } from "./Connections";
import ContactTimeline from "../components/ContactTimeline";
import { phoneDigits } from "./Calls";

const SEQ_DOT: Record<string, string> = {
  active: "bg-sky-400", replied: "bg-volt", paused: "bg-amber",
  completed: "bg-white/30", failed: "bg-danger",
};

// Un color por remitente, estable por id: escanear una columna y ver de un
// golpe de quién es cada tarjeta es la mitad del valor de la etiqueta.
const SENDER_CHIP = [
  "bg-sky-400/15 text-sky-300",
  "bg-fuchsia-400/15 text-fuchsia-300",
  "bg-amber/15 text-amber",
];

function colorRemitente(seatId: string, ids: string[]) {
  const i = ids.indexOf(seatId);
  return SENDER_CHIP[(i < 0 ? 0 : i) % SENDER_CHIP.length];
}

// Solo el primer nombre: la tarjeta tiene 64 px de ancho útil y «Patricia
// Salgar Hurtado» no cabe sin comerse el nombre del prospecto.
function nombreCorto(nombre: string) {
  return (nombre || "").trim().split(/\s+/)[0] || nombre;
}

/* Las dos secuencias de Amplemarket, una por perfil de VP.

   Va arriba de la trazabilidad porque es lo que está operando hoy: la
   trazabilidad mira una `Campaign` con copy en el cockpit, y aquí el copy vive
   entero en la plantilla de Amplemarket. Son dos mecanismos distintos y el que
   manda ahora es este. */
function PanelSecuenciasVP() {
  const [d, setD] = useState<SecuenciasAm | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState("");

  const cargar = () => api.secuenciasAm().then(setD).catch(() => {});
  useEffect(() => { cargar(); const t = setInterval(cargar, 30000); return () => clearInterval(t); }, []);

  async function accion(fn: () => Promise<any>, ok?: (r: any) => string) {
    setOcupado(true);
    try {
      const r = await fn();
      setAviso(ok ? ok(r) : "");
      await cargar();
    } catch (e: any) {
      setAviso(String(e?.message || e));
    } finally { setOcupado(false); }
  }

  if (!d) return null;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-[280px] flex-1">
          <div className="flex items-center gap-2">
            {d.activo ? <Zap size={16} className="text-volt" />
                      : <ZapOff size={16} className="text-white/35" />}
            <span className="font-semibold">
              Secuencias por perfil · {d.activo ? "encendidas" : "apagadas"}
            </span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-white/50">
            Quien acepta la invitación de un perfil entra en <b>su</b> secuencia de
            Amplemarket: aceptó a esa persona, le escribe esa persona. Se inscribe
            a los de <b>{d.etapas.join(" y ")}</b> — el correo corre en paralelo
            mientras la invitación de LinkedIn está pendiente.
          </p>
          <p className="mt-2 text-[11px] text-white/40">
            No pasa por la cola de aprobación: la plantilla la escribió una persona
            en Amplemarket y el cockpit solo aporta nombre y empresa. No hay texto
            generado por modelo que auditar.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className={clsx(d.activo ? "btn-ghost" : "btn-primary")} disabled={ocupado}
                  onClick={() => accion(() => api.secuenciasAmSwitch(!d.activo))}>
            {ocupado ? <Spinner /> : d.activo ? <ZapOff size={15} /> : <Zap size={15} />}
            {d.activo ? "Apagar" : "Encender"}
          </button>
          <button className="btn-ghost" disabled={ocupado}
                  onClick={() => accion(() => api.secuenciasAmCorrer(true),
                    (r) => `Se inscribirían ${r.objetivos ?? 0}.`)}>
            Ensayo
          </button>
          <button className="btn-ghost" disabled={ocupado || !d.pendientes}
                  onClick={() => accion(() => api.secuenciasAmCorrer(false),
                    (r) => `Añadidos ${r.inscritos ?? 0} · ya estaban ${r.ya_estaban ?? 0} · saltados ${r.saltados ?? 0}.`)}>
            <PlayCircle size={15} /> Inscribir ya
          </button>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {d.asientos.map((a) => (
          <div key={a.seat_id}
               className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
            <Check size={13} className="text-white/35" />
            <span className="text-sm font-semibold">{a.vp}</span>
            <span className="text-white/25">→</span>
            {a.secuencia
              ? <span className="min-w-0 truncate text-[11px] text-white/60"
                      title={a.secuencia}>
                  {a.secuencia_nombre || a.secuencia}
                </span>
              : <span className="text-[11px] text-amber">sin secuencia asignada</span>}
          </div>
        ))}
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-x-3 text-[11px] text-white/40">
        <span>{d.pendientes} por inscribir</span>
        <span>· tope de {d.tope_automatico} por vuelta automática</span>
        {d.ultima_corrida && <span>· última: {d.ultima_corrida.replace("T", " ")}</span>}
      </p>
      {aviso && (
        <p className="mt-3 flex items-center gap-2 text-xs text-amber">
          <AlertTriangle size={13} /> {aviso}
        </p>
      )}
    </Card>
  );
}

/* El otro carril de correo: secuencia de HubSpot enviada desde la bandeja de
   Google del propio vendedor.

   Se pinta aparte del de Amplemarket y no como una fila más porque son dos
   motores distintos y, sobre todo, EXCLUYENTES por asiento: un perfil con las
   dos puestas no envía por ninguno (recibiría dos correos de dos remitentes).
   Verlos en el mismo cuadro invitaría justo a esa configuración. */
function PanelSecuenciasHs() {
  const [d, setD] = useState<SecuenciasHs | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [aviso, setAviso] = useState("");

  const cargar = () => api.secuenciasHs().then(setD).catch(() => {});
  useEffect(() => { cargar(); const t = setInterval(cargar, 30000); return () => clearInterval(t); }, []);

  async function accion(fn: () => Promise<any>, ok?: (r: any) => string) {
    setOcupado(true);
    try {
      const r = await fn();
      // `aviso` es el ensayo corriendo sin los scopes de secuencias: el
      // resultado es bueno y hay que enseñarlo, pero con la advertencia.
      const nota = r?.error || r?.aviso;
      setAviso([ok ? ok(r) : "", nota ? String(nota) : ""].filter(Boolean).join(" — "));
      await cargar();
    } catch (e: any) {
      setAviso(String(e?.message || e));
    } finally { setOcupado(false); }
  }

  if (!d) return null;
  const configurados = d.asientos.filter((a) => a.secuencia || a.remitente);
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-[280px] flex-1">
          <div className="flex items-center gap-2">
            {d.activo ? <Mail size={16} className="text-volt" />
                      : <Mail size={16} className="text-white/35" />}
            <span className="font-semibold">
              Secuencias de HubSpot · {d.activo ? "encendidas" : "apagadas"}
            </span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-white/50">
            El correo sale de la <b>bandeja de Google del propio vendedor</b>: HubSpot
            no retransmite, le pide a Gmail que envíe, así que pasa el SPF y el DMARC
            de platzi.com como cualquier correo suyo. Se inscribe a los
            de <b>{d.etapas.join(" y ")}</b>, con tope de <b>{d.tope_dia}/día</b> por
            vendedor.
          </p>
          <p className="mt-2 text-[11px] text-white/40">
            Al inscribir se envía el primer paso y <b>HubSpot no tiene API para
            desinscribir</b>: lo que sale de aquí solo se detiene a mano en su UI.
            Haz el ensayo antes.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className={clsx(d.activo ? "btn-ghost" : "btn-primary")} disabled={ocupado}
                  onClick={() => accion(() => api.secuenciasHsSwitch(!d.activo))}>
            {ocupado ? <Spinner /> : d.activo ? <ZapOff size={15} /> : <Zap size={15} />}
            {d.activo ? "Apagar" : "Encender"}
          </button>
          <button className="btn-ghost" disabled={ocupado}
                  onClick={() => accion(() => api.secuenciasHsCorrer(true),
                    (r) => `Se inscribirían ${r.objetivos ?? 0} · ${r.por_crear ?? 0} hay que crearlos en el CRM`
                           + (r.ya_en_secuencia ? ` · ${r.ya_en_secuencia} ya están en otra secuencia y se saltan` : "") + ".")}>
            Ensayo
          </button>
          <button className="btn-ghost" disabled={ocupado || !d.pendientes}
                  onClick={() => accion(() => api.secuenciasHsCorrer(false),
                    (r) => `Inscritos ${r.inscritos ?? 0} · creados en el CRM ${r.creados ?? 0} · saltados ${r.saltados ?? 0}.`)}>
            <PlayCircle size={15} /> Inscribir ya
          </button>
        </div>
      </div>

      {!!d.conflictos.length && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber/30 bg-amber/[0.06] px-3 py-2 text-xs text-amber">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span>
            {d.conflictos.map((c) => c.vp).join(", ")} tiene los dos motores puestos
            (Amplemarket y HubSpot). No envía por ninguno: quita una de las dos
            secuencias o el prospecto recibiría dos correos distintos.
          </span>
        </p>
      )}

      <div className="mt-4 space-y-2">
        {configurados.length === 0 && (
          <p className="text-[11px] text-white/40">
            Ningún perfil tiene este carril. Se configura con <code>secuencia_hs</code>{" "}
            y <code>remitente_hs</code> en el asiento.
          </p>
        )}
        {configurados.map((a) => (
          <div key={a.seat_id}
               className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2">
            <Check size={13} className={a.listo ? "text-volt" : "text-white/35"} />
            <span className="text-sm font-semibold">{a.vp}</span>
            <span className="text-white/25">→</span>
            {a.remitente
              ? <span className="text-[11px] text-white/60">{a.remitente}</span>
              : <span className="text-[11px] text-amber">sin remitente</span>}
            {a.secuencia
              ? <span className="min-w-0 truncate text-[11px] text-white/40"
                      title={a.secuencia}>· {a.secuencia_nombre || `secuencia ${a.secuencia}`}</span>
              : <span className="text-[11px] text-amber">· sin secuencia</span>}
            <span className="ml-auto text-[11px] text-white/40">
              {a.enviados_hoy}/{d.tope_dia} hoy
            </span>
          </div>
        ))}
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-x-3 text-[11px] text-white/40">
        <span>{d.pendientes} por inscribir</span>
        <span>· tope de {d.tope_automatico} por vuelta automática</span>
        {d.ultima_corrida && <span>· última: {d.ultima_corrida.replace("T", " ")}</span>}
      </p>
      {aviso && (
        <p className="mt-3 flex items-center gap-2 text-xs text-amber">
          <AlertTriangle size={13} /> {aviso}
        </p>
      )}
    </Card>
  );
}

export default function Pipeline() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["pipeline"], queryFn: () => api.pipeline(150), refetchInterval: 20000 });
  const sync = useMutation({
    mutationFn: api.syncRun,
    onSettled: () => qc.invalidateQueries({ queryKey: ["pipeline"] }),
  });
  const [selected, setSelected] = useState<number | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [remitente, setRemitente] = useState("");   // "" = todos
  const [enrichJobId, setEnrichJobId] = useState<string | null>(null);

  const pending = useQuery({ queryKey: ["enrichPending"], queryFn: api.enrichPending, refetchInterval: 20000 });
  const enrichJob = useQuery({
    queryKey: ["enrichJob", enrichJobId],
    queryFn: () => api.enrichBulkStatus(enrichJobId!),
    enabled: !!enrichJobId,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const startEnrich = useMutation({
    mutationFn: api.enrichBulkStart,
    onSuccess: (r) => {
      if (r.ok) setEnrichJobId(r.job_id);
    },
  });
  const enriching = enrichJob.data?.status === "running";

  if (isLoading || !data) return <Empty>Cargando pipeline…</Empty>;

  // Tolerante a un backend desalineado: si el proceso que responde es más viejo
  // que esta página, `remitentes` no viene y un Object.keys(undefined) tumbaba
  // el Pipeline entero — la tabla desaparecía por una etiqueta.
  const remitentesMap = data.remitentes ?? {};
  const seatIds = Object.keys(remitentesMap).sort();
  const porRemitente = (cs: PipelineContact[]) =>
    remitente ? cs.filter((c) => (c.li_seat || "") === remitente) : cs;

  return (
    <div className="space-y-6">
      <PanelSecuenciasVP />
      <PanelSecuenciasHs />
      <div className="flex items-start justify-between gap-4">
        <Header title="Pipeline" sub={`${data.total} contactos · el stage avanza solo con cada señal (envío, aceptación, respuesta)`} />
        <div className="flex items-center gap-2">
          <button onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 rounded-xl bg-platzi px-4 py-2 text-sm font-medium hover:bg-platzi/90">
            <UserPlus size={15} /> Agregar prospecto
          </button>
          <button
            onClick={() => startEnrich.mutate()}
            disabled={enriching || startEnrich.isPending || !pending.data?.total}
            title="Busca en Apollo el correo/teléfono de los contactos del pipeline que solo tienen LinkedIn"
            className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium hover:bg-white/[0.1] disabled:opacity-40">
            {enriching || startEnrich.isPending ? <Spinner /> : <Target size={15} />}
            Prospección · nutrir pipeline{pending.data?.total ? ` (${pending.data.total})` : ""}
          </button>
          <button onClick={() => sync.mutate()} disabled={sync.isPending}
            className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium hover:bg-white/[0.1]">
            {sync.isPending ? <Spinner /> : <RefreshCw size={15} />} Sincronizar CSV
          </button>
        </div>
      </div>

      {startEnrich.data && !startEnrich.data.ok && (
        <Card className="border-danger/30 bg-danger/[0.06]">
          <p className="flex items-center gap-2 text-sm text-danger">
            <AlertTriangle size={15} /> {startEnrich.data.error}
          </p>
        </Card>
      )}

      {enrichJob.data && (
        <EnrichBanner
          job={enrichJob.data}
          onDone={() => { qc.invalidateQueries({ queryKey: ["pipeline"] }); qc.invalidateQueries({ queryKey: ["enrichPending"] }); }}
        />
      )}

      {/* Filtro por remitente: con casi mil prospectos, la etiqueta sirve para
          identificar uno y este filtro para trabajar la lista de uno solo. */}
      {seatIds.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-white/35">Firma:</span>
          <button onClick={() => setRemitente("")}
            className={clsx("chip", remitente === "" ? "bg-white/[0.14] text-white" : "bg-white/[0.05] text-white/50")}>
            todos
          </button>
          {seatIds.map((id) => (
            <button key={id} onClick={() => setRemitente(remitente === id ? "" : id)}
              className={clsx("chip", remitente === id
                ? colorRemitente(id, seatIds).replace("/15", "/30")
                : colorRemitente(id, seatIds))}>
              {remitentesMap[id]}
            </button>
          ))}
          {(data.sin_remitente ?? 0) > 0 && (
            <span className="text-white/30" title="Contactos con LinkedIn y sin perfil asignado: el copiloto les asignará uno al escribirles">
              · {data.sin_remitente} sin asignar
            </span>
          )}
        </div>
      )}

      <div className="flex gap-3 overflow-x-auto pb-4">
        {STAGES.map((s) => {
          const cs = porRemitente(data.stages[s.id] ?? []);
          return (
            <StageColumn key={s.id} stage={s}
              count={remitente ? cs.length : (data.counts[s.id] ?? 0)}
              contacts={cs} onSelect={setSelected} seatIds={seatIds}
              remitentes={remitentesMap}
              onChanged={() => qc.invalidateQueries({ queryKey: ["pipeline"] })} />
          );
        })}
      </div>

      {selected != null && <ContactTimeline contactId={selected} onClose={() => setSelected(null)} />}
      {showAdd && <AddProspectModal onClose={() => setShowAdd(false)}
        onAdded={() => qc.invalidateQueries({ queryKey: ["pipeline"] })} />}
    </div>
  );
}

function EnrichBanner({ job, onDone }: { job: EnrichJob; onDone: () => void }) {
  const notifiedRef = useRef(false);
  useEffect(() => {
    if (job.status !== "running" && job.finished_at && !notifiedRef.current) {
      notifiedRef.current = true;
      onDone();
    }
  }, [job.status, job.finished_at, onDone]);

  const halted = job.status === "sin_creditos";
  return (
    <Card className={halted ? "border-danger/30 bg-danger/[0.06]" : undefined}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {job.status === "running" ? <Spinner /> : halted
          ? <AlertTriangle size={15} className="text-danger" />
          : <Check size={15} className="text-volt" />}
        <span className="font-medium">
          {job.status === "running" ? "Buscando correo/teléfono en Apollo…"
            : halted ? "Sin créditos de Apollo — se detuvo"
            : "Enriquecimiento terminado"}
        </span>
        <span className="text-white/45">{job.done}/{job.total} contactos</span>
        <span className="ml-auto flex items-center gap-3 text-xs text-white/50">
          <span className="text-volt">{job.updated} actualizados</span>
          <span>{job.sin_datos} sin datos</span>
          {job.error_count > 0 && <span className="text-danger">{job.error_count} errores</span>}
        </span>
      </div>
    </Card>
  );
}

function AddProspectModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [form, setForm] = useState<NewContact>({});
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const add = useMutation({
    mutationFn: (c: NewContact) => api.addContacts([c]),
    onSuccess: (r) => {
      if (!r.ok) { setResult({ ok: false, msg: r.error || "Error desconocido" }); return; }
      if (r.added > 0) {
        setResult({ ok: true, msg: "Prospecto agregado ✓" });
        onAdded();
        setForm({});
        setTimeout(onClose, 900);
      } else {
        setResult({ ok: false, msg: r.skipped[0]?.reason || "Ya estaba en la base (duplicado)" });
      }
    },
    onError: (e: any) => setResult({ ok: false, msg: e?.message || "Error de red" }),
  });

  const set = (k: keyof NewContact) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const canSubmit = (form.nombre?.trim() || form.linkedin?.trim() || form.email?.trim()) && !add.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md">
        <Card>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-bold">Agregar prospecto manual</h3>
          <button onClick={onClose} className="rounded-lg bg-white/[0.06] p-1.5 hover:bg-white/[0.12]"><X size={15} /></button>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Nombre"><input className="input" value={form.nombre || ""} onChange={set("nombre")} placeholder="Juan Pérez" /></Field>
            <Field label="Empresa"><input className="input" value={form.empresa || ""} onChange={set("empresa")} placeholder="Acme S.A.S" /></Field>
          </div>
          <Field label="Cargo"><input className="input" value={form.cargo || ""} onChange={set("cargo")} placeholder="Gerente de Mantenimiento" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Correo"><input className="input" value={form.email || ""} onChange={set("email")} placeholder="juan@acme.com" /></Field>
            <Field label="Celular"><input className="input" value={form.celular || ""} onChange={set("celular")} placeholder="3001234567" /></Field>
          </div>
          <Field label="LinkedIn"><input className="input" value={form.linkedin || ""} onChange={set("linkedin")} placeholder="https://linkedin.com/in/…" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Ciudad"><input className="input" value={form.ciudad || ""} onChange={set("ciudad")} placeholder="Bogotá" /></Field>
            <Field label="Sector"><input className="input" value={form.sector || ""} onChange={set("sector")} placeholder="Industria" /></Field>
          </div>
        </div>
        <button onClick={() => add.mutate(form)} disabled={!canSubmit}
          className="btn-primary mt-5 w-full justify-center">
          {add.isPending ? <Spinner /> : <UserPlus size={16} />} Agregar a la base
        </button>
        {result && (
          <p className={clsx("mt-3 flex items-center gap-1.5 text-sm", result.ok ? "text-volt" : "text-platzi")}>
            {result.ok ? <Check size={14} /> : <AlertTriangle size={14} />} {result.msg}
          </p>
        )}
        <p className="mt-2 text-center text-[11px] text-white/30">
          Nombre, correo o LinkedIn — al menos uno es obligatorio. Se deduplica automático.
        </p>
        </Card>
      </div>
    </div>
  );
}

function StageColumn({ stage, count, contacts, onSelect, onChanged, seatIds, remitentes }: {
  stage: { id: Stage; label: string; color: string };
  count: number; contacts: PipelineContact[];
  onSelect: (id: number) => void; onChanged: () => void;
  seatIds: string[]; remitentes: Record<string, string>;
}) {
  return (
    <div className={clsx("w-64 shrink-0 rounded-2xl border-t-2 bg-white/[0.02] p-3", stage.color)}>
      <div className="mb-3 flex items-center justify-between px-1">
        <span className="text-xs font-bold uppercase tracking-wide text-white/60">{stage.label}</span>
        <span className="chip bg-white/[0.08] text-white/60">{count}</span>
      </div>
      <div className="max-h-[65vh] space-y-2 overflow-y-auto pr-1">
        {contacts.length === 0 && <div className="py-6 text-center text-xs text-white/25">vacío</div>}
        {contacts.map((c) => (
          <ContactCard key={c.id} c={c} onSelect={onSelect} onChanged={onChanged}
            seatIds={seatIds} remitentes={remitentes} />
        ))}
      </div>
    </div>
  );
}

const HS_DOT: Record<string, string> = {
  verde: "bg-emerald-400", amarillo: "bg-amber-400", rojo: "bg-red-500",
};

function ContactCard({ c, onSelect, onChanged, seatIds, remitentes }: {
  c: PipelineContact; onSelect: (id: number) => void; onChanged: () => void;
  seatIds: string[]; remitentes: Record<string, string>;
}) {
  // Auto: si el prospecto ya trae el veredicto del cruce con HubSpot (hecho al
  // cargarlo), se muestra de una; si no, queda el botón HS para re-consultar.
  const [hs, setHs] = useState<{ verdict?: string; reason?: string } | null>(
    c.hubspot_verdict ? { verdict: c.hubspot_verdict, reason: c.hubspot_reason } : null
  );
  const hsCheck = useMutation({
    mutationFn: () => api.hubspotCheck(c.id),
    onSuccess: (r) => setHs(r?.ok ? r : { verdict: "error", reason: "no se pudo consultar" }),
  });
  // Inscribe en la secuencia de SU perfil de Amplemarket. Antes llamaba a
  // api.enroll (que exige una Campaign con copy en el cockpit) y devolvía 400
  // porque no hay ninguna: el botón estaba muerto.
  const [errEnroll, setErrEnroll] = useState("");
  const enroll = useMutation({
    mutationFn: () => api.secuenciasAmInscribir([c.id]),
    onSuccess: () => setErrEnroll(""),
    onError: (e: any) => setErrEnroll(String(e?.message || e).slice(0, 140)),
    onSettled: onChanged,
  });
  const move = useMutation({
    mutationFn: (stage: Stage) => api.setStage(c.id, stage),
    onSettled: onChanged,
  });
  const notes = useContactNotes(c.id, c.notas ?? "");

  const tel = phoneDigits(c.celular);

  return (
    <div className="group rounded-xl border border-white/[0.06] bg-ink-900/60 p-3 text-sm hover:border-white/[0.15]">
      <button onClick={() => onSelect(c.id)} className="block w-full text-left">
        <div className="flex items-center gap-1.5 font-medium leading-tight">
          {c.seq_status && <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", SEQ_DOT[c.seq_status] ?? "bg-white/20")}
            title={`secuencia: ${c.seq_status}`} />}
          <span className="truncate">{c.nombre || c.email || "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs text-white/45">{c.empresa}</span>
          {c.li_seat && remitentes[c.li_seat] && (
            <span className={clsx("chip shrink-0 !px-1.5 !py-0 !text-[10px]", colorRemitente(c.li_seat, seatIds))}
              title={`Firma: ${remitentes[c.li_seat]}`}>
              {nombreCorto(remitentes[c.li_seat])}
            </span>
          )}
        </div>
        {c.last_activity && (
          <div className="mt-1 flex items-center gap-1 text-[10px] text-white/30">
            <Clock size={10} /> {c.last_activity.slice(0, 10)}
          </div>
        )}
      </button>
      {(tel || c.email || c.linkedin) && (
        <div className="mt-2 flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          {tel && (
            <>
              <a href={`tel:+${tel}`} title="Llamar"
                className="grid h-6 w-6 place-items-center rounded-lg bg-white/[0.05] text-sky-300 hover:bg-white/10">
                <Phone size={11} />
              </a>
              <a href={`https://wa.me/${tel}`} target="_blank" rel="noreferrer" title="WhatsApp"
                className="grid h-6 w-6 place-items-center rounded-lg bg-white/[0.05] text-volt hover:bg-white/10">
                <MessageCircle size={11} />
              </a>
            </>
          )}
          {c.email && (
            <a href={`mailto:${c.email}`} title={c.email}
              className="grid h-6 w-6 place-items-center rounded-lg bg-white/[0.05] text-platzi-soft hover:bg-white/10">
              <Mail size={11} />
            </a>
          )}
          {c.linkedin && (
            <a href={c.linkedin} target="_blank" rel="noreferrer" title="Ver LinkedIn"
              className="grid h-6 w-6 place-items-center rounded-lg bg-white/[0.05] text-[#0A66C2] hover:bg-white/10">
              <Linkedin size={11} />
            </a>
          )}
        </div>
      )}
      <div className="mt-2 flex items-start gap-1.5" onClick={(e) => e.stopPropagation()}>
        {notes.failed
          ? <AlertTriangle size={11} className="mt-1.5 shrink-0 text-platzi" />
          : <StickyNote size={11} className="mt-1.5 shrink-0 text-white/25" />}
        <div className="w-full">
          <textarea
            rows={2}
            placeholder="Notas…"
            value={notes.notas}
            onChange={(e) => notes.onChange(e.target.value)}
            onBlur={(e) => notes.flush(e.target.value)}
            className={clsx("w-full resize-none rounded-lg border bg-white/[0.02] px-2 py-1 text-[11px] text-white/70",
              "placeholder-white/20 outline-none focus:bg-white/[0.04]",
              notes.failed ? "border-platzi/40 focus:border-platzi/60" : "border-white/[0.06] focus:border-white/20")}
          />
          {notes.failed && (
            <button onClick={() => notes.flush(notes.notas)}
              className="mt-1 text-[10px] font-medium text-platzi hover:underline">
              ⚠ no se guardó — reintentar
            </button>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1 opacity-0 transition group-hover:opacity-100">
        {!c.seq_status || c.seq_status === "completed" || c.seq_status === "failed" ? (
          <button onClick={() => enroll.mutate()} disabled={enroll.isPending}
            title={errEnroll || "Inscribir en la secuencia de su perfil de LinkedIn"}
            className="flex shrink-0 items-center gap-1 rounded-lg bg-platzi/15 px-1.5 py-1 text-[10px] font-medium text-platzi-soft hover:bg-platzi/25">
            {enroll.isPending ? <Spinner /> : <PlayCircle size={11} />} secuencia
          </button>
        ) : null}
        {hs ? (
          <span title={hs.reason}
            className="flex shrink-0 items-center gap-1 rounded-lg border border-white/10 px-1.5 py-1 text-[10px] font-medium text-white/60">
            <span className={clsx("h-1.5 w-1.5 rounded-full", HS_DOT[hs.verdict ?? ""] ?? "bg-white/30")} /> HubSpot
          </span>
        ) : (
          <button onClick={() => hsCheck.mutate()} disabled={hsCheck.isPending}
            title="Cruzar con HubSpot (cliente / deal abierto / lead)"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-white/10 px-1.5 py-1 text-[10px] font-medium text-white/50 hover:border-white/25 hover:text-white/80">
            {hsCheck.isPending ? <Spinner /> : "HS"}
          </button>
        )}
        <select value={c.stage} onChange={(e) => move.mutate(e.target.value as Stage)}
          className="ml-auto shrink-0 rounded-lg border border-white/10 bg-ink-950 px-1 py-0.5 text-[10px] text-white/60 outline-none">
          {STAGES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </div>
    </div>
  );
}
