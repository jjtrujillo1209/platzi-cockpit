import { useState } from "react";
import {
  LayoutDashboard, Link2, MessageSquarePlus, Mail, BarChart3, Database, History, Zap, Phone, LogOut, Settings as SettingsIcon, HeartPulse, Target, Bot, Kanban, Loader2, Route, Inbox, Rocket,
} from "lucide-react";
import clsx from "clsx";
import { getToken } from "./lib/api";
import { logout } from "./components/AuthGate";
import { useBulkJob } from "./state/bulkJobStore";
import BandaSalud from "./components/BandaSalud";
import Dashboard from "./pages/Dashboard";
import Connections from "./pages/Connections";
import Followups from "./pages/Followups";
import Emails from "./pages/Emails";
import Analytics from "./pages/Analytics";
import DatabaseView from "./pages/DatabaseView";
import HistoryView from "./pages/HistoryView";
import Calls from "./pages/Calls";
import Prospect from "./pages/Prospect";
import Settings from "./pages/Settings";
import Heartbeat from "./pages/Heartbeat";
import Hoy from "./pages/Hoy";
import Pipeline from "./pages/Pipeline";
import PipelineVentas from "./pages/PipelineVentas";
import Secuencia from "./pages/Secuencia";
import Peticiones from "./pages/Peticiones";

const NAV = [
  { id: "hoy", label: "Hoy", icon: Bot, el: Hoy, group: "Leads" },
  { id: "pipeline", label: "Pipeline", icon: Kanban, el: Pipeline, group: "Leads" },
  { id: "pipeline-ventas", label: "Pipeline Ventas", icon: Rocket, el: PipelineVentas, group: "Leads" },
  { id: "secuencia", label: "Secuencia", icon: Route, el: Secuencia, group: "Leads" },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, el: Dashboard, group: "Leads" },
  { id: "prospect", label: "Prospección", icon: Target, el: Prospect, group: "Leads" },
  { id: "peticiones", label: "Peticiones", icon: Inbox, el: Peticiones, group: "Leads" },
  { id: "connections", label: "Conexiones", icon: Link2, el: Connections, group: "Leads" },
  { id: "followups", label: "Follow-ups", icon: MessageSquarePlus, el: Followups, group: "Leads" },
  { id: "emails", label: "Correos", icon: Mail, el: Emails, group: "Leads" },
  { id: "analytics", label: "Analytics Email", icon: BarChart3, el: Analytics, group: "Leads" },
  { id: "calls", label: "Llamadas", icon: Phone, el: Calls, group: "Leads" },
  { id: "database", label: "Base de datos", icon: Database, el: DatabaseView, group: "Leads" },
  { id: "history", label: "Historial", icon: History, el: HistoryView, group: "Leads" },
  { id: "heartbeat", label: "Heartbeat", icon: HeartPulse, el: Heartbeat, group: "Sistema" },
  { id: "settings", label: "Ajustes", icon: SettingsIcon, el: Settings, group: "Sistema" },
];

export default function App() {
  const [active, setActive] = useState("hoy");
  const Current = NAV.find((n) => n.id === active)!.el;
  const { job } = useBulkJob();
  const prospectRunning = job?.status === "running";

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-64 shrink-0 flex-col border-r border-white/[0.06] bg-ink-950/60 px-4 py-6">
        <div className="mb-8 flex items-center gap-2.5 px-2">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-platzi shadow-glow">
            <Zap size={18} className="text-ink-950" fill="#13161c" />
          </div>
          <div>
            <div className="text-sm font-extrabold leading-none">Platzi Cockpit</div>
            <div className="mt-0.5 text-[10px] uppercase tracking-widest text-white/35">Outreach OS</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto">
          {["Leads", "Clientes", "Sistema"].map((group) => (
            <div key={group} className="pb-2">
              <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-widest text-white/30">{group}</div>
              {NAV.filter((n) => n.group === group).map((n) => {
                const on = active === n.id;
                return (
                  <button key={n.id} onClick={() => setActive(n.id)}
                    className={clsx("flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
                      on ? "bg-platzi/[0.12] text-white shadow-[inset_0_0_0_1px_rgba(10,233,138,0.3)]"
                         : "text-white/55 hover:bg-white/[0.04] hover:text-white")}>
                    <n.icon size={17} className={on ? "text-platzi" : ""} />
                    {n.label}
                    {n.id === "prospect" && prospectRunning && (
                      <span className="ml-auto flex items-center gap-1 text-[10px] font-semibold text-platzi"
                            title={`Prospectando: ${job!.done}/${job!.total} empresas`}>
                        <Loader2 size={12} className="animate-spin" /> {job!.done}/{job!.total}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="mt-4 rounded-xl border border-white/[0.06] bg-ink-900/60 p-3 text-[11px] text-white/40">
          <div className="font-semibold text-white/60">Platzi ⚡</div>
          Conexiones · Follow-ups · Email tracking en un solo lugar.
        </div>
        {getToken() && (
          <button onClick={logout}
            className="mt-2 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-xs font-medium text-white/45 hover:bg-white/[0.04] hover:text-white">
            <LogOut size={15} /> Cerrar sesión
          </button>
        )}
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
        {/* Va en el marco, encima de cualquier pestaña: la avería que cubre se
            caracteriza por que nadie la está buscando. Ver BandaSalud.tsx. */}
        <BandaSalud />
        <div className="mx-auto max-w-[1400px] px-8 py-8">
          <Current />
        </div>
      </main>
    </div>
  );
}
