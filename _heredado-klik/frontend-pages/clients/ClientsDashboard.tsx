import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { api } from "../../lib/api";
import { Stat, Card, Empty } from "../../components/ui";
import { Building2, Zap, Mail, Phone, PhoneCall } from "lucide-react";

const fmt = (n: number) => n.toLocaleString("es-CO");

export default function ClientsDashboard() {
  const { data, isLoading } = useQuery({ queryKey: ["clientsStats"], queryFn: api.clientsStats, refetchInterval: 10000 });
  if (isLoading || !data) return <Empty>Cargando resultados…</Empty>;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Clientes activos" value={data.total} accent="white" icon={<Building2 size={18} />} />
        <Stat label="Consumo total" value={`${fmt(Math.round(data.consumo_total / 1000))}K`} accent="amber"
          sub="kWh/mes" icon={<Zap size={18} />} />
        <Stat label="Con contacto" value={`${data.con_email}✉ · ${data.con_telefono}☎`} accent="sky"
          sub="email · teléfono" icon={<Building2 size={18} />} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Correos enviados" value={data.emailed} accent="klik" icon={<Mail size={18} />} />
        <Stat label="Llamados" value={data.llamados} accent="volt" sub={`${data.total - data.llamados} por llamar`} icon={<PhoneCall size={18} />} />
        <Stat label="Con email" value={`${Math.round(data.con_email / data.total * 100)}%`} accent="sky" sub={`${data.con_email}/${data.total}`} icon={<Mail size={18} />} />
        <Stat label="Con teléfono" value={`${Math.round(data.con_telefono / data.total * 100)}%`} accent="amber" sub={`${data.con_telefono}/${data.total}`} icon={<Phone size={18} />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <h3 className="mb-4 text-sm font-semibold">Consumo por sector (top kWh/mes)</h3>
          {data.por_sector?.length ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={data.por_sector} layout="vertical" margin={{ left: 10, right: 30 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="sector" tick={{ fill: "#ffffff70", fontSize: 10 }} width={150}
                  tickFormatter={(s) => (s.length > 22 ? s.slice(0, 22) + "…" : s)} />
                <Tooltip cursor={{ fill: "#ffffff08" }} contentStyle={{ background: "#141823", border: "1px solid #ffffff14", borderRadius: 12 }}
                  formatter={(v: any) => [fmt(v) + " kWh", "Consumo"]} />
                <Bar dataKey="consumo" radius={[0, 6, 6, 0]} barSize={20}>
                  {data.por_sector.map((_: any, i: number) => <Cell key={i} fill={["#FF3151", "#ff7a8c", "#27e0b3", "#38bdf8", "#f5b942"][i % 5]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty>Sin datos</Empty>}
        </Card>

        <Card>
          <h3 className="mb-4 text-sm font-semibold">Top 10 clientes por consumo</h3>
          <div className="space-y-1.5">
            {(data.top_consumo ?? []).map((c: any, i: number) => (
              <div key={i} className="flex items-center gap-3 rounded-lg bg-white/[0.02] px-3 py-2 text-sm">
                <span className="w-5 font-mono text-white/30">{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{c.empresa}</div>
                  <div className="text-xs text-white/40">{c.ciudad}</div>
                </div>
                <span className="shrink-0 font-mono text-xs text-amber">{fmt(c.consumo)} kWh</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
