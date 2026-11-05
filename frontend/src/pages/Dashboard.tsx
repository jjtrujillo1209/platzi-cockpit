import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { api } from "../lib/api";
import { Stat, Card, Empty, Progress } from "../components/ui";
import { Link2, MessageSquarePlus, Mail, MailOpen, MousePointerClick, AlertTriangle, StopCircle } from "lucide-react";

export default function Dashboard() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: api.overview, refetchInterval: 8000 });
  const { data: jobs } = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, refetchInterval: 4000 });

  const stopJob = async (jobId: string) => {
    await api.stopJob(jobId);
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  };

  if (isLoading || !data) return <Empty>Cargando métricas…</Empty>;
  const li = data.linkedin, em = data.email;
  // Números reales de SendGrid para la categoría platzi-cockpit (si el API responde)
  const sg = em.sendgrid?.ok ? em.sendgrid : null;
  const active = (jobs?.jobs ?? []).filter((j) => j.status === "running");

  return (
    <div className="space-y-6">
      <Header title="Dashboard" sub="Vista unificada de los tres canales de outreach" />

      {active.length > 0 && (
        <Card className="border-platzi/30 bg-platzi/[0.05]">
          <div className="mb-3 flex items-center gap-3 text-sm">
            <span className="h-2.5 w-2.5 animate-pulse2 rounded-full bg-platzi" />
            <strong>{active.length} job{active.length > 1 ? "s" : ""} en curso</strong>
          </div>
          <div className="space-y-3">
            {active.map((j) => (
              <div key={j.job_id}>
                <div className="mb-1 flex items-center justify-between text-xs text-white/55">
                  <span className="font-medium text-white/70">{j.channel}</span>
                  <div className="flex items-center gap-3">
                    <span>{j.done}/{j.total}</span>
                    <button
                      className="btn-ghost !px-2 !py-1 text-[11px]"
                      onClick={() => stopJob(j.job_id)}
                    >
                      <StopCircle size={12} /> Detener
                    </button>
                  </div>
                </div>
                <Progress value={j.done} total={j.total} />
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Conexiones enviadas" value={li.connections.sent} accent="sky"
          sub={`${li.connections.already} ya conectados · ${li.connections.pending} pendientes`} icon={<Link2 size={18} />} />
        <Stat label="Follow-ups" value={li.followups.message_sent} accent="volt"
          sub="mensajes a ya-conectados" icon={<MessageSquarePlus size={18} />} />
        <Stat label="Correos enviados" value={sg ? sg.totals.requests : em.sent} accent="platzi"
          sub={sg ? `${sg.totals.delivered} entregados · SendGrid` : `${em.delivered || em.sent} entregados`}
          icon={<Mail size={18} />} />
        <Stat label="Open rate" value={`${sg ? sg.open_rate : em.open_rate}%`} accent="amber"
          sub={`${sg ? sg.totals.unique_opens : em.opened_unique} aperturas únicas`} icon={<MailOpen size={18} />} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label={sg ? "Clicks únicos" : "Clicks en CTA"} value={sg ? sg.totals.unique_clicks : em.cta_clicks} accent="volt"
          sub={`${sg ? sg.click_rate : em.click_rate}% click rate`} icon={<MousePointerClick size={18} />} />
        <Stat label="Bounces" value={sg ? sg.totals.bounces : em.bounced} accent="platzi"
          sub={`${sg ? sg.bounce_rate : em.bounce_rate}%${sg && sg.totals.blocks ? ` · ${sg.totals.blocks} bloqueados` : ""}`}
          icon={<AlertTriangle size={18} />} />
        <Stat label="Spam reports" value={sg ? sg.totals.spam_reports : em.spam} accent="amber"
          sub={sg ? "reportados a SendGrid" : `${em.spam_rate}%`} />
        <Stat label="Total alcanzados" value={li.connections.total + li.followups.total + (sg ? sg.totals.requests : em.sent)} accent="white"
          sub="acciones registradas" />
      </div>

      <div className="text-xs text-white/35">
        {sg
          ? <>Métricas de email: <span className="text-white/55">SendGrid · categoría <b>{sg.category}</b></span> · desde {sg.since}</>
          : <>Métricas de email: eventos locales{em.sendgrid?.error ? ` (SendGrid: ${em.sendgrid.error})` : ""}</>}
      </div>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Actividad por día</h3>
          <div className="flex gap-3 text-xs text-white/45">
            <Legend c="#38bdf8" t="Conexiones" /><Legend c="#27e0b3" t="Follow-ups" /><Legend c="#FF3151" t="Correos" />
          </div>
        </div>
        {data.timeseries?.length ? (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={data.timeseries} margin={{ left: -20, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#38bdf8" stopOpacity={0.5} /><stop offset="100%" stopColor="#38bdf8" stopOpacity={0} /></linearGradient>
                <linearGradient id="g2" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#27e0b3" stopOpacity={0.5} /><stop offset="100%" stopColor="#27e0b3" stopOpacity={0} /></linearGradient>
                <linearGradient id="g3" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#FF3151" stopOpacity={0.5} /><stop offset="100%" stopColor="#FF3151" stopOpacity={0} /></linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="date" tick={{ fill: "#ffffff55", fontSize: 11 }} tickFormatter={(d) => d.slice(5)} />
              <YAxis tick={{ fill: "#ffffff55", fontSize: 11 }} allowDecimals={false} />
              <Tooltip contentStyle={{ background: "#141823", border: "1px solid #ffffff14", borderRadius: 12 }} />
              <Area type="monotone" dataKey="linkedin" stroke="#38bdf8" fill="url(#g1)" strokeWidth={2} />
              <Area type="monotone" dataKey="followup" stroke="#27e0b3" fill="url(#g2)" strokeWidth={2} />
              <Area type="monotone" dataKey="email" stroke="#FF3151" fill="url(#g3)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : <Empty>Sin actividad registrada todavía</Empty>}
      </Card>
    </div>
  );
}

function Legend({ c, t }: { c: string; t: string }) {
  return <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: c }} />{t}</span>;
}

export function Header({ title, sub }: { title: string; sub?: string }) {
  return (
    <div>
      <h1 className="text-2xl font-extrabold tracking-tight">{title}</h1>
      {sub && <p className="mt-1 text-sm text-white/45">{sub}</p>}
    </div>
  );
}
