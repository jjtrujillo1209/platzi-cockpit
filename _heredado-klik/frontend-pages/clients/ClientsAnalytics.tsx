import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, LabelList } from "recharts";
import { api, HubspotVerdict } from "../../lib/api";
import { Stat, Card, Empty, Spinner } from "../../components/ui";
import { Sparkles, Handshake, Ban, RefreshCw, AlertTriangle, ShieldCheck } from "lucide-react";

/** Taxonomía del cruce (misma regla de negocio que backend/hubspot_check.py). */
const VERDICT: Record<HubspotVerdict, { label: string; desc: string; color: string; text: string }> = {
  verde: {
    label: "Prospecto nuevo", color: "#0ae98a", text: "text-klik",
    desc: "No está en HubSpot (o solo Prospect sin actividad) — apto para ir en frío",
  },
  amarillo: {
    label: "Ya es lead del CRM", color: "#f5b942", text: "text-amber",
    desc: "Lead / MQL / SQL / Opportunity — coordinar con el rep, no ir en frío",
  },
  rojo: {
    label: "Cliente o deal abierto", color: "#e53256", text: "text-danger",
    desc: "Customer o venta en proceso — no contactar",
  },
};

const ORDER: HubspotVerdict[] = ["verde", "amarillo", "rojo"];

export default function ClientsAnalytics() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["hubspotSummary"], queryFn: api.hubspotSummary, refetchInterval: 30000,
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  if (isLoading || !data) return <Empty>Cargando comparativa…</Empty>;

  const { verdicts, checked, pendientes, sin_email, con_email } = data;
  const pct = (n: number) => (checked ? Math.round((n / checked) * 100) : 0);

  const cruzar = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await api.hubspotBackfill();
      setMsg(r.ok ? `Cruzados ${r.checked} contactos` : r.error || "No se pudo cruzar");
      refetch();
    } finally { setBusy(false); }
  };

  const chart = ORDER.map((v) => ({ v, name: VERDICT[v].label, n: verdicts[v] ?? 0 }));

  return (
    <div className="space-y-6">
      {!data.configured && (
        <Card className="border-amber/30 bg-amber/[0.06]">
          <p className="flex items-center gap-2 text-sm text-amber">
            <AlertTriangle size={15} />
            HubSpot no está configurado — sin token no se puede saber cuáles de estos
            contactos ya son leads del CRM. Configura la Private App para poblar esta vista.
          </p>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Base cruzada" value={checked} accent="white" icon={<ShieldCheck size={18} />}
          sub={`de ${con_email} con email${pendientes ? ` · ${pendientes} sin cruzar` : ""}`} />
        <Stat label="Prospectos nuevos" accent="klik" icon={<Sparkles size={18} />}
          value={<span className={VERDICT.verde.text}>{verdicts.verde ?? 0}</span>}
          sub={`${pct(verdicts.verde ?? 0)}% de lo cruzado · aptos para frío`} />
        <Stat label="Ya son leads" accent="amber" icon={<Handshake size={18} />}
          value={<span className={VERDICT.amarillo.text}>{verdicts.amarillo ?? 0}</span>}
          sub={`${pct(verdicts.amarillo ?? 0)}% · coordinar con el rep`} />
        <Stat label="Clientes / deal abierto" accent="white" icon={<Ban size={18} />}
          value={<span className={VERDICT.rojo.text}>{verdicts.rojo ?? 0}</span>}
          sub={`${pct(verdicts.rojo ?? 0)}% · no contactar`} />
      </div>

      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-semibold">Prospectos vs leads ya conocidos</h3>
          <span className="text-[11px] text-white/35">
            {data.last_check ? `último cruce ${data.last_check.slice(0, 16).replace("T", " ")}` : "sin cruces todavía"}
          </span>
          {pendientes > 0 && (
            <button className="btn-ghost ml-auto" onClick={cruzar} disabled={busy}>
              {busy ? <Spinner /> : <RefreshCw size={15} />} Cruzar {pendientes} pendientes
            </button>
          )}
          {msg && <span className="text-xs text-volt">{msg}</span>}
        </div>

        {checked ? (
          <>
            <div className="mb-5 flex h-3 overflow-hidden rounded-full bg-white/[0.04]">
              {ORDER.map((v) => (verdicts[v] ? (
                <div key={v} style={{ width: `${pct(verdicts[v])}%`, background: VERDICT[v].color }}
                  title={`${VERDICT[v].label}: ${verdicts[v]} (${pct(verdicts[v])}%)`} />
              ) : null))}
            </div>
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={chart} layout="vertical" margin={{ left: 20, right: 45 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" tick={{ fill: "#ffffff80", fontSize: 12 }} width={165} />
                <Tooltip cursor={{ fill: "#ffffff08" }}
                  contentStyle={{ background: "#141823", border: "1px solid #ffffff14", borderRadius: 12 }}
                  formatter={(n: any) => [`${n} contacto(s)`, "Total"]} />
                <Bar dataKey="n" radius={[0, 8, 8, 0]} barSize={30}>
                  {chart.map((d) => <Cell key={d.v} fill={VERDICT[d.v].color} />)}
                  <LabelList dataKey="n" position="right" fill="#fff" fontSize={12} fontWeight={700} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </>
        ) : (
          <Empty>
            Nadie de la base se ha cruzado con HubSpot todavía
            {con_email === 0 && " — y ningún contacto tiene email, que es la llave del cruce"}.
          </Empty>
        )}

        <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {ORDER.map((v) => (
            <div key={v} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold">
                <span className="h-2 w-2 rounded-full" style={{ background: VERDICT[v].color }} />
                {VERDICT[v].label}
              </div>
              <p className="mt-1 text-[11px] leading-snug text-white/40">{VERDICT[v].desc}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <h3 className="mb-4 text-sm font-semibold">Por qué quedó en cada grupo</h3>
        {data.motivos?.length ? (
          <div className="space-y-1.5">
            {data.motivos.map((m, i) => (
              <div key={i} className="flex items-center gap-3 rounded-lg bg-white/[0.02] px-3 py-2 text-sm">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: VERDICT[m.verdict]?.color }} />
                <span className="min-w-0 flex-1 truncate text-white/70">{m.reason}</span>
                <span className="shrink-0 font-mono text-xs text-white/45">{m.n}</span>
              </div>
            ))}
          </div>
        ) : <Empty>Sin motivos que mostrar — falta cruzar la base</Empty>}
        {sin_email > 0 && (
          <p className="mt-3 text-[11px] text-white/35">
            {sin_email} contacto(s) sin email quedan fuera del cruce: HubSpot se busca por correo.
          </p>
        )}
      </Card>
    </div>
  );
}
