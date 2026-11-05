import { useEffect, useRef, useState } from "react";
import { jobSocket, RunEvent, JobProgress, api } from "../lib/api";
import { statusOf } from "../lib/status";
import { Progress, Spinner } from "./ui";
import { StopCircle, CheckCircle2 } from "lucide-react";

export function LiveProgress({ jobId, channel }: { jobId: string; channel: string }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [prog, setProg] = useState<JobProgress | null>(null);
  const [done, setDone] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]); setProg(null); setDone(false);
    const ws = jobSocket(jobId);
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "event") {
        if (msg.data.idx === 0) return;
        setEvents((prev) => [...prev, msg.data]);
      } else if (msg.type === "progress") {
        setProg(msg.data);
      } else if (msg.type === "done") {
        setProg(msg.data); setDone(true);
      }
    };
    return () => ws.close();
  }, [jobId]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [events.length]);

  const total = prog?.total ?? 0;
  const counts = events.reduce<Record<string, number>>((a, e) => {
    a[e.status] = (a[e.status] || 0) + 1; return a;
  }, {});

  return (
    <div className="card p-5">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          {done ? <CheckCircle2 size={16} className="text-volt" /> : <Spinner />}
          <span>{done ? "Completado" : "En curso"}</span>
          <span className="font-mono text-xs text-white/40">{jobId}</span>
        </div>
        {!done && (
          <button className="btn-ghost !px-3 !py-1.5 text-xs" onClick={() => api.stopJob(jobId)}>
            <StopCircle size={14} /> Detener
          </button>
        )}
      </div>

      <Progress value={events.length} total={total} />
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-white/55">
        <span>{events.length}/{total || "?"}</span>
        {Object.entries(counts).map(([s, n]) => {
          const st = statusOf(s);
          return (
            <span key={s} className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${st.dot}`} /> {st.label}: {n}
            </span>
          );
        })}
      </div>

      <div ref={listRef} className="mt-4 max-h-72 space-y-1 overflow-y-auto pr-1">
        {events.map((e, i) => {
          const st = statusOf(e.status);
          return (
            <div key={i} className="flex items-center gap-3 rounded-lg bg-white/[0.02] px-3 py-1.5 text-xs">
              <span className="w-8 font-mono text-white/30">{e.idx}</span>
              <span className={`h-2 w-2 shrink-0 rounded-full ${st.dot}`} />
              <span className="min-w-0 flex-1 truncate text-white/70">
                {e.email || e.url || "—"}
              </span>
              <span className={`shrink-0 font-medium ${st.color}`}>{st.label}</span>
              {e.detail && <span className="hidden shrink-0 text-white/30 md:inline">{e.detail}</span>}
            </div>
          );
        })}
        {events.length === 0 && (
          <div className="py-6 text-center text-xs text-white/30 animate-pulse2">
            Esperando primeros eventos del navegador…
          </div>
        )}
      </div>
    </div>
  );
}
