import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot, CheckCheck, Check, X, Send, RefreshCw, Mail, Link2,
  MessageSquarePlus, Phone, Sparkles, AlertTriangle, Inbox,
} from "lucide-react";
import clsx from "clsx";
import { api, Approval, Task } from "../lib/api";
import { Card, Badge, Empty, Spinner } from "../components/ui";
import { Header } from "./Dashboard";

const CHANNEL_META: Record<string, { label: string; icon: any; chip: string }> = {
  linkedin_invite: { label: "Invitación LinkedIn", icon: Link2, chip: "bg-sky-400/15 text-sky-300" },
  linkedin_msg: { label: "Mensaje LinkedIn", icon: MessageSquarePlus, chip: "bg-volt/15 text-volt" },
  email: { label: "Email", icon: Mail, chip: "bg-platzi/15 text-platzi-soft" },
};

const INTENT_CHIP: Record<string, string> = {
  interesado: "bg-volt/20 text-volt",
  no_interesado: "bg-white/10 text-white/50",
  fuera_oficina: "bg-amber/15 text-amber",
  referido: "bg-sky-400/15 text-sky-300",
  otro: "bg-white/10 text-white/60",
};

export default function Hoy() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["approvals"] });
    qc.invalidateQueries({ queryKey: ["approved"] });
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["agentStatus"] });
  };

  const { data: status } = useQuery({ queryKey: ["agentStatus"], queryFn: api.agentStatus, refetchInterval: 15000 });
  const { data: pending } = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals("pending"), refetchInterval: 15000 });
  const { data: approved } = useQuery({ queryKey: ["approved"], queryFn: () => api.approvals("approved"), refetchInterval: 15000 });
  const { data: tasksData } = useQuery({ queryKey: ["tasks"], queryFn: () => api.tasks("open"), refetchInterval: 30000 });
  const { data: brief } = useQuery({ queryKey: ["briefing"], queryFn: () => api.briefing(), refetchInterval: 3600000 });

  const tick = useMutation({ mutationFn: api.agentTick, onSettled: invalidate });
  const bulkOk = useMutation({
    mutationFn: () => api.bulkApprove((pending?.approvals ?? []).map((a) => a.id)),
    onSettled: invalidate,
  });
  const send = useMutation({ mutationFn: () => api.sendApproved(dryRun), onSettled: invalidate });
  const [dryRun, setDryRun] = useState(false);

  const approvals = pending?.approvals ?? [];
  const readyCount = approved?.approvals.length ?? 0;
  const tasks = tasksData?.tasks ?? [];
  const replies = tasks.filter((t) => t.type === "reply_review");
  const calls = tasks.filter((t) => t.type === "call");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <Header title="Hoy" sub="Agenda del copiloto: aprueba, envía y da seguimiento" />
        <button onClick={() => tick.mutate()} disabled={tick.isPending}
          className="btn-secondary flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-medium hover:bg-white/[0.1]">
          {tick.isPending ? <Spinner /> : <RefreshCw size={15} />} Correr ciclo ahora
        </button>
      </div>

      {status && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge className={status.cadence.enabled ? "bg-volt/15 text-volt" : "bg-white/10 text-white/50"}>
            <Bot size={12} className="mr-1 inline" />
            Cadencia {status.cadence.enabled ? "activa" : "pausada"} · tick {status.cadence.tick_minutes}m
          </Badge>
          {/* Un badge por perfil: el total de LinkedIn es engañoso porque no se
              puede gastar de la bolsa de al lado — el contacto de un asiento
              agotado espera aunque a otro le sobre cupo. */}
          {status.budgets.seats.map((s) => (
            <Badge key={s.seat_id} className={s.listo ? "" : "bg-amber/10 text-amber"}>
              {s.nombre}: {s.listo
                ? `${s.invites.used + s.invites.queued}/${s.invites.cap} invit · ${s.mensajes.used + s.mensajes.queued}/${s.mensajes.cap} msj`
                : "sin sesión"}
            </Badge>
          ))}
          <Badge>Email hoy: {status.budgets.email.used + status.budgets.email.queued}/{status.budgets.email.cap}</Badge>
          <Badge className={status.ai_available ? "bg-volt/15 text-volt" : "bg-amber/15 text-amber"}>
            <Sparkles size={12} className="mr-1 inline" />IA {status.ai_available ? "OK" : "sin API key"}
          </Badge>
          <Badge className={
            status.gmail.state === "connected" ? "bg-volt/15 text-volt"
              : status.gmail.state === "error" ? "bg-amber/15 text-amber"
                : "bg-white/10 text-white/50"}>
            <Inbox size={12} className="mr-1 inline" />
            Gmail {status.gmail.state === "connected" ? "OK"
              : status.gmail.state === "needs_connect" ? "falta autorizar"
                : status.gmail.state === "error" ? "error" : "sin configurar"}
          </Badge>
          {status.cadence.last_tick && <span className="text-white/35">último ciclo {status.cadence.last_tick.slice(11, 16)}</span>}
        </div>
      )}

      {!!status?.budgets.seats_problemas.length && (
        <Card className="border-amber/30 bg-amber/[0.06]">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber">
            <AlertTriangle size={15} /> Perfiles de LinkedIn
          </div>
          <ul className="space-y-1 text-xs text-amber/90">
            {status.budgets.seats_problemas.map((p) => <li key={p}>· {p}</li>)}
          </ul>
          <p className="mt-2 text-xs text-white/45">
            Un perfil sin sesión tiene cupo cero: el copiloto no le arma borradores, así que
            sus contactos esperan en vez de acumularse en una cola que nadie puede enviar.
          </p>
        </Card>
      )}

      {status?.gmail.last_error && (
        <Card className="border-amber/30 bg-amber/[0.06]">
          <div className="flex items-center gap-2 text-sm text-amber">
            <AlertTriangle size={15} /> Detección de respuestas: {status.gmail.last_error}
          </div>
        </Card>
      )}

      {brief?.text && (
        <Card className="border-platzi/20">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Sparkles size={15} className="text-platzi" /> Briefing · {brief.date}
          </div>
          <div className="whitespace-pre-line text-sm leading-relaxed text-white/75">{brief.text}</div>
        </Card>
      )}

      {/* ---- Cola de aprobación ---- */}
      <Card>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">
            Cola de aprobación
            <span className="ml-2 text-white/40">{approvals.length} borradores · {readyCount} aprobados sin enviar</span>
          </h3>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-white/50">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)}
                className="accent-platzi" /> dry-run email
            </label>
            {approvals.length > 0 && (
              <button onClick={() => bulkOk.mutate()} disabled={bulkOk.isPending}
                className="flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs font-medium hover:bg-white/[0.1]">
                <CheckCheck size={14} /> Aprobar todos
              </button>
            )}
            <button onClick={() => send.mutate()} disabled={send.isPending || readyCount === 0}
              className={clsx("flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold",
                readyCount > 0 ? "bg-platzi text-ink-950 shadow-glow" : "bg-white/[0.05] text-white/30")}>
              {send.isPending ? <Spinner /> : <Send size={14} />} Enviar aprobados ({readyCount})
            </button>
          </div>
        </div>
        {approvals.length === 0 && readyCount === 0 ? (
          <Empty>Nada pendiente de aprobar. El copiloto genera borradores cuando hay pasos de secuencia vencidos.</Empty>
        ) : (
          <div className="space-y-3">
            {approvals.map((a) => <ApprovalRow key={a.id} a={a} onChange={invalidate} />)}
          </div>
        )}
      </Card>

      {/* ---- Respuestas por revisar ---- */}
      <Card>
        <h3 className="mb-4 text-sm font-semibold">Respuestas recibidas <span className="ml-2 text-white/40">{replies.length}</span></h3>
        {replies.length === 0 ? <Empty>Sin respuestas nuevas por revisar</Empty> : (
          <div className="space-y-3">
            {replies.map((t) => <ReplyRow key={t.id} t={t} onChange={invalidate} />)}
          </div>
        )}
      </Card>

      {/* ---- Llamadas ---- */}
      <Card>
        <h3 className="mb-4 text-sm font-semibold">Llamadas pendientes <span className="ml-2 text-white/40">{calls.length}</span></h3>
        {calls.length === 0 ? <Empty>Sin llamadas programadas</Empty> : (
          <div className="space-y-2">
            {calls.map((t) => (
              <div key={t.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-sm">
                <div className="min-w-0">
                  <span className="font-medium">{t.nombre || "—"}</span>
                  <span className="ml-2 text-white/45">{t.empresa}</span>
                  <span className="ml-2 text-xs text-white/35">vence {t.due_at?.slice(0, 10)}</span>
                  {t.notes && <div className="truncate text-xs text-white/40">{t.notes}</div>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {t.celular && (
                    <a href={`tel:${t.celular}`} className="rounded-lg bg-white/[0.06] p-1.5 hover:bg-white/[0.12]"><Phone size={14} /></a>
                  )}
                  <TaskButtons id={t.id} onChange={invalidate} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// El id del asiento («vp-2») no le dice nada a quien aprueba: lo que necesita
// saber es de quién es el perfil que va a firmar ese mensaje.
function useSeatNombre() {
  const { data } = useQuery({ queryKey: ["seats"], queryFn: api.seats, staleTime: 60_000 });
  return (seatId: string) =>
    data?.seats.find((s) => s.seat_id === seatId)?.nombre ?? seatId;
}

function ApprovalRow({ a, onChange }: { a: Approval; onChange: () => void }) {
  const seatNombre = useSeatNombre();
  const meta = CHANNEL_META[a.channel] ?? CHANNEL_META.email;
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState(a.draft_subject);
  const [body, setBody] = useState(a.draft_body);
  const approve = useMutation({
    mutationFn: () => api.approve(a.id, subject, body),
    onSettled: onChange,
  });
  const skip = useMutation({ mutationFn: () => api.skipApproval(a.id), onSettled: onChange });

  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <button onClick={() => setOpen(!open)} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <span className={clsx("chip", meta.chip)}><meta.icon size={11} className="mr-1 inline" />{meta.label}</span>
            <span className="text-sm font-medium">{a.nombre || "—"}</span>
            <span className="text-sm text-white/45">{a.cargo && `${a.cargo} · `}{a.empresa}</span>
            {a.step_order != null && <span className="text-xs text-white/30">paso {a.step_order}</span>}
            {a.channel.startsWith("linkedin") && a.li_seat && (
              <span className="chip bg-white/[0.06] text-white/50">firma {seatNombre(a.li_seat)}</span>
            )}
          </div>
          <div className="mt-1 truncate text-xs text-white/45">
            {a.channel === "email" && a.draft_subject ? <b className="text-white/60">{a.draft_subject}</b> : null}
            {a.channel === "email" && a.draft_subject ? " — " : ""}{a.draft_body}
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          <button onClick={() => approve.mutate()} disabled={approve.isPending}
            title="Aprobar" className="rounded-lg bg-volt/15 p-2 text-volt hover:bg-volt/25">
            {approve.isPending ? <Spinner /> : <Check size={15} />}
          </button>
          <button onClick={() => skip.mutate()} disabled={skip.isPending}
            title="Saltar" className="rounded-lg bg-white/[0.06] p-2 text-white/50 hover:bg-white/[0.12]">
            <X size={15} />
          </button>
        </div>
      </div>
      {open && (
        <div className="space-y-2 border-t border-white/[0.06] px-4 py-3">
          {a.channel === "email" && (
            <input value={subject} onChange={(e) => setSubject(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 py-2 text-sm outline-none focus:border-platzi/50"
              placeholder="Asunto" />
          )}
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={a.channel === "email" ? 8 : 4}
            className="w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 py-2 text-sm leading-relaxed outline-none focus:border-platzi/50" />
          <div className="text-right text-xs text-white/35">
            {a.channel === "linkedin_invite" && `${body.length}/280`}
            <span className="ml-3">Editar y luego ✓ aprobar guarda tu versión</span>
          </div>
        </div>
      )}
    </div>
  );
}

function ReplyRow({ t, onChange }: { t: Task; onChange: () => void }) {
  const cls = t.meta?.classification ?? {};
  const reply = t.meta?.reply ?? {};
  const intent: string = cls.intent || "otro";
  const [open, setOpen] = useState(intent === "interesado");
  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <button onClick={() => setOpen(!open)} className="min-w-0 flex-1 text-left">
          <div className="flex flex-wrap items-center gap-2">
            <span className={clsx("chip", INTENT_CHIP[intent] ?? INTENT_CHIP.otro)}>{intent.replace("_", " ")}</span>
            <span className="text-sm font-medium">{t.nombre || reply.from || "—"}</span>
            <span className="text-sm text-white/45">{t.empresa}</span>
          </div>
          <div className="mt-1 truncate text-xs text-white/45">{cls.summary || reply.snippet || t.notes}</div>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          {t.email && (
            <a href={`mailto:${t.email}?subject=${encodeURIComponent("Re: " + (reply.subject || ""))}&body=${encodeURIComponent(cls.suggested_response || "")}`}
              title="Responder (borrador sugerido)" className="rounded-lg bg-platzi/15 p-2 text-platzi-soft hover:bg-platzi/25">
              <Mail size={15} />
            </a>
          )}
          <TaskButtons id={t.id} onChange={onChange} />
        </div>
      </div>
      {open && (
        <div className="space-y-2 border-t border-white/[0.06] px-4 py-3 text-sm">
          {reply.snippet && <p className="text-white/60">“{reply.snippet}”</p>}
          {cls.suggested_response && (
            <div className="rounded-lg border border-volt/20 bg-volt/[0.05] px-3 py-2 text-white/75">
              <div className="mb-1 text-xs font-semibold text-volt">Respuesta sugerida</div>
              <div className="whitespace-pre-line">{cls.suggested_response}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskButtons({ id, onChange }: { id: number; onChange: () => void }) {
  const done = useMutation({ mutationFn: () => api.taskAction(id, "done"), onSettled: onChange });
  const dismiss = useMutation({ mutationFn: () => api.taskAction(id, "dismiss"), onSettled: onChange });
  return (
    <>
      <button onClick={() => done.mutate()} title="Hecho"
        className="rounded-lg bg-volt/15 p-1.5 text-volt hover:bg-volt/25"><Check size={14} /></button>
      <button onClick={() => dismiss.mutate()} title="Descartar"
        className="rounded-lg bg-white/[0.06] p-1.5 text-white/50 hover:bg-white/[0.12]"><X size={14} /></button>
    </>
  );
}
