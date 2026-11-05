import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Header } from "./Dashboard";
import { Card, Badge, Empty } from "../components/ui";
import { statusOf, CHANNEL } from "../lib/status";
import { ChevronRight, X } from "lucide-react";

export default function HistoryView() {
  const { data } = useQuery({ queryKey: ["history"], queryFn: api.history, refetchInterval: 10000 });
  const runs = data?.runs ?? [];
  const [open, setOpen] = useState<{ run_id: string; channel: string } | null>(null);

  return (
    <div className="space-y-6">
      <Header title="Historial / Envíos guardados" sub="Todas las corridas registradas en results/, unificadas por canal" />

      <div className="grid grid-cols-1 gap-3">
        {runs.map((r: any) => {
          const ch = CHANNEL[r.channel] || { label: r.channel, color: "text-white/60" };
          return (
            <button key={r.file} onClick={() => setOpen({ run_id: r.run_id, channel: r.channel })}
              className="card flex items-center gap-4 p-4 text-left transition hover:bg-white/[0.03]">
              <div className={`w-32 shrink-0 text-sm font-semibold ${ch.color}`}>{ch.label}</div>
              <div className="w-40 shrink-0 font-mono text-xs text-white/45">{r.run_id}</div>
              <div className="flex flex-1 flex-wrap gap-1.5">
                {Object.entries(r.by_status || {}).map(([s, n]) => {
                  const st = statusOf(s);
                  return <span key={s} className={`chip bg-white/[0.05] ${st.color}`}><span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />{st.label}: {n as number}</span>;
                })}
              </div>
              <div className="shrink-0 text-right">
                <div className="text-lg font-bold">{r.total}</div>
                <div className="text-[10px] text-white/35">acciones</div>
              </div>
              <ChevronRight size={16} className="shrink-0 text-white/30" />
            </button>
          );
        })}
        {runs.length === 0 && <Empty>Aún no hay corridas guardadas</Empty>}
      </div>

      {open && <RunDetail {...open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function RunDetail({ run_id, channel, onClose }: { run_id: string; channel: string; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["history", run_id, channel], queryFn: () => api.historyDetail(run_id, channel) });
  const events = data?.events ?? [];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm" onClick={onClose}>
      <div className="card max-h-[80vh] w-full max-w-2xl overflow-hidden p-0" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
          <div>
            <div className="text-sm font-semibold">{CHANNEL[channel]?.label || channel}</div>
            <div className="font-mono text-xs text-white/40">{run_id}</div>
          </div>
          <button className="btn-ghost !p-2" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="max-h-[60vh] space-y-1 overflow-y-auto p-4">
          {events.map((e: any, i: number) => {
            const st = statusOf(e.status);
            return (
              <div key={i} className="flex items-center gap-3 rounded-lg bg-white/[0.02] px-3 py-2 text-xs">
                <span className="w-7 font-mono text-white/30">{e.idx}</span>
                <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} />
                <span className="min-w-0 flex-1 truncate text-white/70">{e.email || e.url}</span>
                <span className={`shrink-0 ${st.color}`}>{st.label}</span>
              </div>
            );
          })}
          {events.length === 0 && <Empty>Sin eventos en esta corrida</Empty>}
        </div>
      </div>
    </div>
  );
}
