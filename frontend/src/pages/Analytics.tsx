import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, LabelList } from "recharts";
import clsx from "clsx";
import { api, EngagementRow } from "../lib/api";
import { Header } from "./Dashboard";
import { Stat, Card, Empty, Badge } from "../components/ui";
import { AlertTriangle, MailOpen, MousePointerClick, Reply, Ban } from "lucide-react";

const FUNNEL_COLORS = ["#FF3151", "#ff7a8c", "#27e0b3", "#5cf0cc"];

export default function Analytics() {
  const { data, isLoading } = useQuery({ queryKey: ["emailStats"], queryFn: api.emailStats, refetchInterval: 8000 });
  if (isLoading || !data) return <Empty>Cargando analytics…</Empty>;

  // Números reales de SendGrid (categoría platzi-cockpit); fallback a eventos locales.
  const sg = data.sendgrid?.ok ? data.sendgrid : null;
  const funnel = sg ? [
    { stage: "Enviados", value: sg.totals.requests },
    { stage: "Entregados", value: sg.totals.delivered },
    { stage: "Abiertos", value: sg.totals.unique_opens },
    { stage: "Clicks", value: sg.totals.unique_clicks },
  ] : data.funnel;

  return (
    <div className="space-y-6">
      <Header title="Analytics de Email"
        sub={sg ? `Números reales de SendGrid · categoría ${sg.category} · desde ${sg.since}` : "Embudo y rendimiento de las campañas por SendGrid"} />

      {!data.webhook_active && (
        <Card className="border-amber/30 bg-amber/[0.06]">
          <div className="flex items-start gap-3 text-sm">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber" />
            <div>
              <strong className="text-amber">El Event Webhook de SendGrid aún no recibe datos.</strong>
              <p className="mt-1 text-white/55">Registra <code className="rounded bg-black/30 px-1.5 py-0.5">/api/webhooks/sendgrid</code> (vía ngrok)
                en SendGrid → Settings → Mail Settings → Event Webhook. Las aperturas, clicks, bounces y spam aparecerán aquí en tiempo real.</p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Enviados" value={sg ? sg.totals.requests : data.sent} accent="white"
          sub={sg ? `${sg.totals.delivered} entregados` : undefined} />
        <Stat label="Open rate" value={`${sg ? sg.open_rate : data.open_rate}%`} accent="amber"
          sub={`${sg ? sg.totals.unique_opens : data.opened_unique} únicos`} />
        <Stat label={sg ? "Click rate" : "Click CTA"} value={`${sg ? sg.click_rate : data.click_rate}%`} accent="volt"
          sub={`${sg ? sg.totals.unique_clicks : data.cta_clicks} clicks únicos`} />
        <Stat label="Bounce rate" value={`${sg ? sg.bounce_rate : data.bounce_rate}%`} accent="platzi"
          sub={`${sg ? sg.totals.bounces : data.bounced} bounces`} />
        <Stat label="Spam" value={sg ? sg.totals.spam_reports : `${data.spam_rate}%`} accent="platzi"
          sub={sg ? "reportes" : `${data.spam} reports`} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.3fr_1fr]">
        <Card>
          <h3 className="mb-4 text-sm font-semibold">Embudo de conversión</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={funnel} layout="vertical" margin={{ left: 20, right: 40 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="stage" tick={{ fill: "#ffffff80", fontSize: 12 }} width={90} />
              <Tooltip cursor={{ fill: "#ffffff08" }} contentStyle={{ background: "#141823", border: "1px solid #ffffff14", borderRadius: 12 }} />
              <Bar dataKey="value" radius={[0, 8, 8, 0]} barSize={34}>
                {funnel.map((_: any, i: number) => <Cell key={i} fill={FUNNEL_COLORS[i % FUNNEL_COLORS.length]} />)}
                <LabelList dataKey="value" position="right" fill="#fff" fontSize={12} fontWeight={700} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <h3 className="mb-4 text-sm font-semibold">Por contacto (últimos eventos)</h3>
          <div className="max-h-64 space-y-1.5 overflow-y-auto pr-1">
            {(data.per_contact ?? []).filter((p: any) => p.contact_id).slice(0, 60).map((p: any, i: number) => (
              <div key={i} className="flex items-center justify-between rounded-lg bg-white/[0.02] px-3 py-2 text-xs">
                <span className="min-w-0 flex-1 truncate text-white/70">{p.contact_id}</span>
                <div className="flex gap-1">
                  {[...new Set(p.events.map((e: any) => e.type))].map((t: any) => (
                    <Badge key={t} className="!px-2 !py-0.5 capitalize">{t}</Badge>
                  ))}
                </div>
              </div>
            ))}
            {!(data.per_contact ?? []).some((p: any) => p.contact_id) && <Empty>Aún sin eventos de tracking por contacto</Empty>}
          </div>
        </Card>
      </div>

      <Engagement />
    </div>
  );
}

const FILTERS = [
  { id: "all", label: "Todos" },
  { id: "cta", label: "Click en CTA" },
  { id: "clicked", label: "Cualquier click" },
  { id: "opened", label: "Abrieron" },
  { id: "replied", label: "Respondieron" },
  { id: "bounced", label: "Rebotaron" },
] as const;

function Engagement() {
  const { data } = useQuery({ queryKey: ["engagement"], queryFn: api.emailEngagement, refetchInterval: 30000 });
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  if (!data) return null;

  const rows = data.rows.filter((r) =>
    filter === "cta" ? r.cta_clicks > 0 :
    filter === "clicked" ? r.clicks > 0 :
    filter === "opened" ? r.opens > 0 :
    filter === "replied" ? r.replies > 0 :
    filter === "bounced" ? r.bounces > 0 || r.spam > 0 : true);

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">
          ¿Quién abrió y quién hizo click en el CTA?
          <span className="ml-2 text-white/40">
            {data.cta_clickers} con click en CTA · {data.clickers} con algún click · {data.openers} abrieron · {data.bounced} rebotaron
          </span>
        </h3>
        <div className="flex gap-1.5">
          {FILTERS.map((f) => (
            <button key={f.id} onClick={() => setFilter(f.id)}
              className={clsx("rounded-lg px-2.5 py-1 text-xs font-medium",
                filter === f.id ? "bg-platzi/20 text-platzi-soft" : "bg-white/[0.04] text-white/50 hover:bg-white/[0.08]")}>
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {!data.webhook_active && (
        <p className="mb-3 rounded-lg border border-amber/20 bg-amber/[0.05] px-3 py-2 text-xs text-amber/90">
          Las aperturas y clicks por persona se capturan en vivo vía el Event Webhook (ngrok) — actívalo para
          empezar a llenar esta tabla. Los <b>rebotes y spam por persona ya son reales</b> (suppression API de SendGrid).
        </p>
      )}
      <p className="mb-3 text-[11px] text-white/35">
        "Click en CTA" filtra el link de agendar/contactar de la plantilla, separado de cualquier otro link
        del correo. Ojo: escáneres de seguridad corporativos (Microsoft Defender, Proofpoint…) "detonan" todos
        los links de un correo apenas llega, antes de que el destinatario lo abra — un número alto de clicks en
        pocos segundos tras el envío suele ser eso, no interés real. La apertura y el reply siguen siendo la
        señal más confiable de que una persona lo vio de verdad.
      </p>

      {rows.length === 0 ? <Empty>Sin contactos que cumplan el filtro</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wide text-white/35">
                <th className="py-2 pr-3">Contacto</th>
                <th className="px-3 py-2">Enviados</th>
                <th className="px-3 py-2"><MailOpen size={12} className="mr-1 inline" />Aperturas</th>
                <th className="px-3 py-2"><MousePointerClick size={12} className="mr-1 inline" />Clicks (CTA / total)</th>
                <th className="px-3 py-2"><Reply size={12} className="mr-1 inline" />Respondió</th>
                <th className="px-3 py-2">Estado</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 150).map((r) => <EngRow key={r.id} r={r} />)}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function EngRow({ r }: { r: EngagementRow }) {
  const dead = r.bounces > 0 || r.spam > 0;
  return (
    <tr className={clsx("border-b border-white/[0.04]", dead && "opacity-60")}>
      <td className="max-w-[260px] py-2 pr-3">
        <div className="truncate font-medium text-white/80">{r.nombre || r.email}</div>
        <div className="truncate text-white/40">{r.empresa}{r.email ? ` · ${r.email}` : ""}</div>
      </td>
      <td className="px-3 py-2 text-white/60">
        {r.sends}{r.last_sent && <div className="text-[10px] text-white/30">{r.last_sent.slice(0, 10)}</div>}
      </td>
      <td className="px-3 py-2">
        <span className={r.opens ? "font-semibold text-amber" : "text-white/25"}>{r.opens}</span>
        {r.last_open && <div className="text-[10px] text-white/30">últ. {r.last_open.slice(0, 16).replace("T", " ")}</div>}
      </td>
      <td className="px-3 py-2">
        {r.clicks ? (
          <span className="font-semibold text-volt">
            {r.cta_clicks}<span className="font-normal text-white/35"> / {r.clicks}</span>
          </span>
        ) : <span className="text-white/25">0</span>}
        {r.last_click && <div className="text-[10px] text-white/30">últ. {r.last_click.slice(0, 16).replace("T", " ")}</div>}
      </td>
      <td className="px-3 py-2">
        {r.replies > 0 ? <Badge className="bg-volt/15 !px-2 !py-0.5 text-volt">sí</Badge> : <span className="text-white/25">—</span>}
      </td>
      <td className="px-3 py-2">
        {dead ? (
          <span title={r.bounce_reason} className="inline-flex items-center gap-1 text-platzi-soft">
            <Ban size={11} /> {r.spam > 0 ? "spam" : "rebotó"}
          </span>
        ) : <Badge className="!px-2 !py-0.5">{r.stage.replace("_", " ")}</Badge>}
      </td>
    </tr>
  );
}
