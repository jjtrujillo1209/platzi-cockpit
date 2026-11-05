import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, BulkCompany, BulkJob } from "../lib/api";

// El job de prospección masiva corre en un hilo del backend, ajeno a qué
// pestaña esté activa en el cockpit. Este store vive por encima del switch de
// páginas (en App.tsx) y persiste el job_id en localStorage, así que el
// progreso se ve sin importar a qué pestaña se cambie o si se recarga la página.
const STORAGE_KEY = "platzi_bulk_job_id";

type BulkJobCtx = {
  job: BulkJob | null;
  launching: boolean;
  launchErr: string;
  launch: (companies: BulkCompany[], autoAdd: boolean) => Promise<void>;
  stop: () => Promise<void>;
  clear: () => void;
};

const Ctx = createContext<BulkJobCtx | null>(null);

export function BulkJobProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [job, setJob] = useState<BulkJob | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchErr, setLaunchErr] = useState("");
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollFrom = (jobId: string) => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const st = await api.prospectBulkStatus(jobId);
        setJob(st);
        if (st.status !== "running") {
          stopPolling();
          qc.invalidateQueries({ queryKey: ["contacts"] });
        }
      } catch {
        /* red caída puntual: se reintenta en el próximo tick */
      }
    }, 2000);
  };

  // Al montar (arranque del cockpit o F5): si había un job guardado, recupera
  // su estado y retoma el polling si sigue corriendo.
  useEffect(() => {
    const savedId = localStorage.getItem(STORAGE_KEY);
    if (!savedId) return;
    api.prospectBulkStatus(savedId).then((st) => {
      setJob(st);
      if (st.status === "running") pollFrom(savedId);
      else localStorage.removeItem(STORAGE_KEY);
    }).catch(() => localStorage.removeItem(STORAGE_KEY));
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const launch = async (companies: BulkCompany[], autoAdd: boolean) => {
    if (!companies.length || job?.status === "running") return;
    setLaunchErr("");
    setLaunching(true);
    try {
      const j = await api.prospectBulkStart({
        companies, auto_add: autoAdd,
      });
      if (!j.ok && j.error) {
        setLaunchErr(j.error);
        return;
      }
      setJob(j);
      localStorage.setItem(STORAGE_KEY, j.job_id);
      pollFrom(j.job_id);
    } catch (e: any) {
      setLaunchErr(e?.message || "Error de red");
    } finally {
      setLaunching(false);
    }
  };

  const stop = async () => {
    if (!job) return;
    setJob(await api.prospectBulkStop(job.job_id));
  };

  const clear = () => {
    stopPolling();
    setJob(null);
    setLaunchErr("");
    localStorage.removeItem(STORAGE_KEY);
  };

  return (
    <Ctx.Provider value={{ job, launching, launchErr, launch, stop, clear }}>
      {children}
    </Ctx.Provider>
  );
}

export function useBulkJob(): BulkJobCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useBulkJob debe usarse dentro de <BulkJobProvider>");
  return ctx;
}
