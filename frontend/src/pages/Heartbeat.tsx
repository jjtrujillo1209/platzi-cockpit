import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { api } from "../lib/api";
import { Header } from "./Dashboard";
import { Stat, Card, Empty, Spinner } from "../components/ui";
import { Activity, MailOpen, Link2, RefreshCw, HeartPulse, Clock } from "lucide-react";

export default function Heartbeat() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["heartbeat"], queryFn: api.heartbeat, refetchInterval: 10000 });
  const [busy, setBusy] = useState<"" | "email" | "li">("");

  if (isLoading || !data) return <Empty>Cargando heartbeat…</Empty>;
  const live = data.live;
  const latest = data.latest;

  const run = async (checkLi: boolean) => {
    setBusy(checkLi ? "li" : "email");
    try { await api.runHeartbeat(checkLi); qc.invalidateQueries({ queryKey: ["heartbeat"] }); }
    finally { setBusy(""); }
  };

  const series = (data.history ?? []).map((s: any) => ({
    date: s.date,
    open_rate: s.email?.open_rate ?? 0,
    acceptance: s.conexiones?.acceptance_rate ?? 0,
  }));

  return (
    <div className="space-y-6">
      <Header title="Heartbeat" sub="Validación diaria de open rate de correos y conexiones aceptadas" />

      <Card className="border-volt/20 bg-volt/[0.04]">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <HeartPulse size={18} className={data.running ? "animate-pulse2 text-platzi" : "text-volt"} />
            {data.running ? "Verificando LinkedIn…" : "Activo · 1×/día automático"}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-white/50">
            <Clock size={13} /> Último: {latest ? latest.ts.replace("T", " ").slice(0, 16) : "—"}
          </div>
          <div className="ml-auto flex gap-2">
            <button className="btn-ghost !py-2 text-xs" onClick={() => run(false)} disabled={!!busy}>
              {busy === "email" ? <Spinner /> : <RefreshCw size={14} />} Snapshot
            </button>
            <button className="btn-primary !py-2 text-xs" onClick={() => run(true)} disabled={!!busy}>
              {busy === "li" ? <Spinner /> : <Link2 size={14} />} Verificar aceptadas (LinkedIn)
            </button>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Open rate (correos)" value={`${live.email.open_rate}%`} accent="amber"
          sub={`${live.email.abiertos_unicos} aperturas / ${live.email.enviados} enviados`} icon={<MailOpen size={18} />} />
        <Stat label="Conexiones aceptadas" value={live.conexiones.aceptadas} accent="volt"
          sub={`de ${live.conexiones.invitadas} invitadas`} icon={<Link2 size={18} />} />
        <Stat label="Tasa de aceptación" value={`${live.conexiones.acceptance_rate}%`} accent="sky"
          sub={`${live.conexiones.pendientes} pendientes`} icon={<Activity size={18} />} />
        <Stat label="Perfiles verificados" value={live.conexiones.checked} accent="white"
          sub={`de ${live.conexiones.invitadas} invitadas`} />
      </div>

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Tendencia diaria</h3>
          <div className="flex gap-3 text-xs text-white/45">
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber" /> Open rate %</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-volt" /> Aceptación %</span>
          </div>
        </div>
        {series.length ? (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={series} margin={{ left: -20, right: 8, top: 8 }}>
              <defs>
                <linearGradient id="ho" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#f5b942" stopOpacity={0.5} /><stop offset="100%" stopColor="#f5b942" stopOpacity={0} /></linearGradient>
                <linearGradient id="ha" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#27e0b3" stopOpacity={0.5} /><stop offset="100%" stopColor="#27e0b3" stopOpacity={0} /></linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="date" tick={{ fill: "#ffffff55", fontSize: 11 }} tickFormatter={(d) => d.slice(5)} />
              <YAxis tick={{ fill: "#ffffff55", fontSize: 11 }} unit="%" />
              <Tooltip contentStyle={{ background: "#141823", border: "1px solid #ffffff14", borderRadius: 12 }} />
              <Legend />
              <Area type="monotone" name="Open rate" dataKey="open_rate" stroke="#f5b942" fill="url(#ho)" strokeWidth={2} />
              <Area type="monotone" name="Aceptación" dataKey="acceptance" stroke="#27e0b3" fill="url(#ha)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : <Empty>Aún no hay snapshots diarios — corre uno con el botón de arriba</Empty>}
      </Card>
    </div>
  );
}
