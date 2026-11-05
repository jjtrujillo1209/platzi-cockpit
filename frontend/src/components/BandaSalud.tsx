import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, ShieldAlert } from "lucide-react";
import { api, type Salud } from "../lib/api";

/**
 * Banda de alarma cuando algo de la maquinaria no está corriendo.
 *
 * Existe por la avería del 21 al 23-sep-2026: los cuatro agentes de launchd
 * murieron a la vez tras un reinicio y estuvieron 48 h caídos sin que nada lo
 * dijera. El tablero de 23-platzi-ventas mostraba «la máquina lleva sin dar
 * señales», que es lo que muestra también cuando la máquina está sana y la cola
 * vacía — por eso nadie lo miró con urgencia.
 *
 * Va en el marco de la aplicación, no en una pestaña. Una pestaña de salud solo
 * avisa a quien ya sospecha; esta avería se caracteriza precisamente porque
 * nadie sospecha.
 */
export default function BandaSalud() {
  const [s, setS] = useState<Salud | null>(null);
  const [reparando, setReparando] = useState(false);

  async function mirar() {
    try {
      setS(await api.agentes());
    } catch {
      // El backend no responde. No se pinta nada: si está caído, la interfaz
      // entera ya lo está gritando (no carga ninguna página).
      setS(null);
    }
  }

  useEffect(() => {
    mirar();
    const t = setInterval(mirar, 60_000);
    return () => clearInterval(t);
  }, []);

  if (!s) return null;

  const avisos = s.avisos ?? [];
  if (s.sano && avisos.length === 0) return null;

  async function reparar() {
    setReparando(true);
    try {
      await api.repararAgentes();
      await mirar();
    } finally {
      setReparando(false);
    }
  }

  // Sano pero con riesgo conocido: se cuenta en tono menor, sin robar atención.
  if (s.sano) {
    return (
      <div className="flex items-start gap-3 border-b border-amber-400/20 bg-amber-400/[0.07] px-8 py-2.5 text-[12px] text-amber-200/80">
        <ShieldAlert size={15} className="mt-px shrink-0" />
        <div>
          {avisos.map((a) => (
            <div key={a.label}>
              <span className="font-semibold">{a.nombre}</span>: {a.aviso}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="border-b border-red-500/30 bg-red-500/[0.09] px-8 py-3">
      <div className="flex items-start gap-3">
        <AlertTriangle size={17} className="mt-0.5 shrink-0 text-red-400" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-red-200">
            La maquinaria no está corriendo entera
          </div>
          <ul className="mt-1 space-y-0.5 text-[12px] text-red-200/75">
            {s.problemas.map((p, i) => (
              <li key={i}>· {p}</li>
            ))}
          </ul>
          <div className="mt-1.5 text-[11px] text-red-200/50">
            Mientras esto dure, lo que un vendedor marque en Platzi Ventas se
            queda en la cola sin que nadie lo tome.
          </div>
        </div>
        <button
          onClick={reparar}
          disabled={reparando}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-[12px] font-medium text-red-100 transition hover:bg-red-500/20 disabled:opacity-50"
        >
          <RefreshCw size={13} className={reparando ? "animate-spin" : ""} />
          {reparando ? "Levantando…" : "Levantar ahora"}
        </button>
      </div>
    </div>
  );
}
