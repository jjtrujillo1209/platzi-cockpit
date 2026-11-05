import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  X, Mail, MailOpen, MousePointerClick, AlertTriangle, Link2, UserCheck,
  MessageSquarePlus, Phone, Reply, Circle, StickyNote, Pencil, Check,
  Linkedin, ExternalLink, Send, Clock,
} from "lucide-react";
import clsx from "clsx";
import { api, FichaProspecto, PipelineContact, Stage } from "../lib/api";
import { STAGES } from "../lib/stages";
import { useContactNotes } from "../lib/useContactNotes";
import { Badge, Empty, Spinner } from "./ui";

const TYPE_META: Record<string, { label: string; icon: any; color: string }> = {
  invite_sent: { label: "Invitación LinkedIn", icon: Link2, color: "text-sky-300" },
  accepted: { label: "Conexión aceptada", icon: UserCheck, color: "text-volt" },
  followup_sent: { label: "Mensaje LinkedIn", icon: MessageSquarePlus, color: "text-volt" },
  sent: { label: "Email enviado", icon: Mail, color: "text-platzi-soft" },
  open: { label: "Email abierto", icon: MailOpen, color: "text-amber" },
  click: { label: "Click en email", icon: MousePointerClick, color: "text-amber" },
  bounce: { label: "Rebote", icon: AlertTriangle, color: "text-danger" },
  spam: { label: "Marcado spam", icon: AlertTriangle, color: "text-danger" },
  reply: { label: "Respondió", icon: Reply, color: "text-volt" },
  call: { label: "Llamada", icon: Phone, color: "text-white/70" },
};

const SEQ_CHIP: Record<string, string> = {
  active: "bg-sky-400/15 text-sky-300",
  paused: "bg-amber/15 text-amber",
  replied: "bg-volt/15 text-volt",
  completed: "bg-white/10 text-white/50",
  failed: "bg-danger/15 text-danger-soft",
};

export default function ContactTimeline({ contactId, onClose }: { contactId: number; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["timeline", contactId],
    queryFn: () => api.timeline(contactId),
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose}>
      <div className="h-full w-full max-w-lg overflow-y-auto border-l border-white/10 bg-ink-950 p-6"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-5 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold">{data?.contact.nombre || "…"}</h2>
            <p className="text-sm text-white/50">
              {data?.contact.cargo}{data?.contact.cargo && data?.contact.empresa ? " · " : ""}{data?.contact.empresa}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              {/* Quién firma a este prospecto. Es el dato que decide qué se le
                  puede hacer: solo ese perfil puede escribirle después. */}
              {data?.seat_asignado.nombre && (
                <span className="chip bg-platzi/10 text-platzi">
                  <Linkedin size={11} /> firma {data.seat_asignado.nombre}
                </span>
              )}
              {data?.contact.linkedin && (
                <a href={data.contact.linkedin} target="_blank" rel="noreferrer"
                   className="chip bg-white/[0.06] text-white/60 hover:bg-white/[0.12]">
                  <ExternalLink size={11} /> ver perfil
                </a>
              )}
              {data?.contact.email && <Badge>{data.contact.email}</Badge>}
              {data?.contact.celular && <Badge>{data.contact.celular}</Badge>}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg bg-white/[0.06] p-2 hover:bg-white/[0.12]"><X size={16} /></button>
        </div>

        {isLoading || !data ? <div className="py-10 text-center"><Spinner /></div> : (
          <TimelineBody data={data} contactId={contactId} />
        )}
      </div>
    </div>
  );
}

function TimelineBody({ data, contactId }: { data: FichaProspecto; contactId: number }) {
  const qc = useQueryClient();
  const move = useMutation({
    mutationFn: (stage: Stage) => api.setStage(contactId, stage),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["timeline", contactId] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
    },
  });
  const notes = useContactNotes(contactId, data.contact.notas ?? "");

  return (
    <>
      {/* Datos del lead (editables) */}
      <LeadFields key={contactId} contactId={contactId} contact={data.contact} />

      {/* Etapa + Notas */}
      <div className="mb-5 grid grid-cols-1 gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <label className="block">
          <span className="mb-1 block text-xs text-white/45">Etapa</span>
          <select
            value={data.contact.stage}
            onChange={(e) => move.mutate(e.target.value as Stage)}
            className="input"
          >
            {STAGES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 flex items-center gap-1.5 text-xs text-white/45">
            <StickyNote size={12} /> Notas
            {notes.failed && (
              <button onClick={() => notes.flush(notes.notas)}
                className="ml-auto flex items-center gap-1 font-medium text-platzi hover:underline">
                <AlertTriangle size={11} /> no se guardó — reintentar
              </button>
            )}
          </span>
          <textarea
            rows={3}
            placeholder="Escribe notas sobre este contacto…"
            value={notes.notas}
            onChange={(e) => notes.onChange(e.target.value)}
            onBlur={(e) => notes.flush(e.target.value)}
            className={clsx("input resize-none text-sm",
              notes.failed && "border-platzi/40 focus:border-platzi/60")}
          />
        </label>
      </div>

      {(data.sequences?.length ?? 0) > 0 && (
        <div className="mb-5 space-y-2">
          {data.sequences.map((s: any) => (
            <div key={s.id} className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs">
              <span className="font-medium text-white/70">{s.name} · paso {s.current_step}</span>
              <span className="flex items-center gap-2">
                <span className={clsx("chip", SEQ_CHIP[s.status] ?? "bg-white/10")}>{s.status}</span>
                {s.next_action_at && <span className="text-white/35">próx. {s.next_action_at.slice(0, 16).replace("T", " ")}</span>}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* En cola: lo que ya está redactado y todavía no ha salido. Sin esto la
          ficha muestra solo el pasado y esconde lo que está por pasar. */}
      {(data.aprobaciones?.length ?? 0) > 0 && (
        <div className="mb-5 space-y-2">
          <div className="flex items-center gap-1.5 text-xs text-white/45">
            <Clock size={12} /> En cola
          </div>
          {data.aprobaciones.map((a) => (
            <div key={a.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
              <div className="flex items-center gap-2 text-xs">
                <span className="font-medium text-white/70">
                  {TYPE_META[a.channel === "linkedin_invite" ? "invite_sent"
                    : a.channel === "linkedin_msg" ? "followup_sent" : "sent"]?.label ?? a.channel}
                </span>
                <span className={clsx("chip", a.status === "blocked" ? "bg-amber/15 text-amber"
                  : a.status === "pending" ? "bg-white/10 text-white/60" : "bg-volt/15 text-volt")}>
                  {a.status}
                </span>
                <span className="ml-auto text-white/30">{a.created_at.slice(5, 16).replace("T", " ")}</span>
              </div>
              {a.blocked_reasons && (
                <div className="mt-1 text-[11px] text-amber/90">{a.blocked_reasons}</div>
              )}
              <div className="mt-1 line-clamp-2 text-xs text-white/45">{a.draft_body}</div>
            </div>
          ))}
        </div>
      )}

      {/* timeline abajo */}
      {!data.timeline.length ? <Empty>Sin actividad registrada con este contacto</Empty> : (
        <ol className="relative space-y-4 border-l border-white/[0.08] pl-5">
          {data.timeline.map((a) => {
            const m = TYPE_META[a.type] ?? { label: `${a.channel}:${a.type}`, icon: Circle, color: "text-white/50" };
            return (
              <li key={a.id} className="relative">
                <span className={clsx("absolute -left-[27px] top-0.5 grid h-4 w-4 place-items-center rounded-full bg-ink-950", m.color)}>
                  <m.icon size={12} />
                </span>
                <div className="text-sm">
                  <span className={clsx("font-medium", m.color)}>{m.label}</span>
                  <span className="ml-2 text-xs text-white/35">{(a.ts || "").slice(0, 16).replace("T", " ")}</span>
                </div>
                {a.seat_nombre && (
                  <div className="flex items-center gap-1 text-[11px] text-white/40"
                       title={a.seat_inferido
                         ? "No quedó grabado en la actividad: se deduce del perfil asignado hoy"
                         : "Grabado en el momento del envío"}>
                    <Send size={10} /> por {a.seat_nombre}
                    {a.seat_inferido && <span className="text-white/25">· inferido</span>}
                  </div>
                )}
                {a.meta?.subject && <div className="text-xs text-white/50">{a.meta.subject}</div>}
                {a.meta?.snippet && <div className="mt-0.5 text-xs italic text-white/45">“{a.meta.snippet}”</div>}
                {a.meta?.outcome && <div className="text-xs text-white/50">{a.meta.outcome}{a.meta.notes ? ` — ${a.meta.notes}` : ""}</div>}
                {a.meta?.url && <div className="truncate text-xs text-white/35">{a.meta.url}</div>}
              </li>
            );
          })}
        </ol>
      )}
    </>
  );
}

const LEAD_FIELDS: Array<{
  key: "nombre" | "cargo" | "empresa" | "email" | "celular" | "linkedin";
  label: string;
  type?: string;
  placeholder: string;
  wide?: boolean;
}> = [
  { key: "nombre", label: "Nombre", placeholder: "Nombre del lead", wide: true },
  { key: "cargo", label: "Cargo", placeholder: "Cargo / rol" },
  { key: "empresa", label: "Empresa", placeholder: "Empresa" },
  { key: "email", label: "Email", type: "email", placeholder: "correo@empresa.com", wide: true },
  { key: "celular", label: "Celular", placeholder: "57 3xx xxx xxxx" },
  { key: "linkedin", label: "LinkedIn", placeholder: "https://linkedin.com/in/…", wide: true },
];

function LeadFields({ contactId, contact }: { contactId: number; contact: PipelineContact }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (fields: Record<string, string>) => api.updateContact(contactId, fields),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["timeline", contactId] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
    },
  });

  return (
    <div className="mb-5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center gap-1.5 text-xs text-white/45">
        <Pencil size={12} /> Datos del lead
        {save.isPending && <span className="ml-auto text-white/35">guardando…</span>}
        {!save.isPending && save.isSuccess && (
          <span className="ml-auto flex items-center gap-1 text-volt"><Check size={12} /> guardado</span>
        )}
        {save.isError && (
          <button onClick={() => save.mutate(save.variables as Record<string, string>)}
            className="ml-auto flex items-center gap-1 font-medium text-platzi hover:underline">
            <AlertTriangle size={11} /> no se guardó — reintentar
          </button>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        {LEAD_FIELDS.map((f) => (
          <EditableField
            key={f.key}
            label={f.label}
            type={f.type}
            placeholder={f.placeholder}
            wide={f.wide}
            value={(contact[f.key] as string) ?? ""}
            onSave={(v) => save.mutate({ [f.key]: v })}
          />
        ))}
      </div>
    </div>
  );
}

function EditableField({ label, value, type, placeholder, wide, onSave }: {
  label: string; value: string; type?: string; placeholder: string;
  wide?: boolean; onSave: (v: string) => void;
}) {
  return (
    <label className={clsx("block", wide && "col-span-2")}>
      <span className="mb-1 block text-xs text-white/45">{label}</span>
      <input
        type={type || "text"}
        defaultValue={value}
        placeholder={placeholder}
        onBlur={(e) => {
          const v = e.target.value.trim();
          if (v !== (value ?? "").trim()) onSave(v);
        }}
        className="input text-sm"
      />
    </label>
  );
}
