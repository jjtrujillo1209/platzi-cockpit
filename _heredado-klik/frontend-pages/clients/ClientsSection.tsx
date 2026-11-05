import { useState } from "react";
import clsx from "clsx";
import { LayoutDashboard, BarChart3 } from "lucide-react";
import { Header } from "../Dashboard";
import ClientsDashboard from "./ClientsDashboard";
import ClientsAnalytics from "./ClientsAnalytics";

const TABS = [
  { id: "resultados", label: "Resultados", icon: LayoutDashboard, el: ClientsDashboard },
  { id: "analitica", label: "Analítica", icon: BarChart3, el: ClientsAnalytics },
];

export default function ClientsSection() {
  const [tab, setTab] = useState("resultados");
  const Current = TABS.find((t) => t.id === tab)!.el;
  return (
    <div className="space-y-6">
      <Header title="Clientes Platzi" sub="Base de clientes activos — separada de la base de prospección" />
      <div className="flex gap-1.5 rounded-2xl border border-white/[0.06] bg-ink-900/60 p-1.5">
        {TABS.map((t) => {
          const on = tab === t.id;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={clsx("flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition",
                on ? "bg-klik text-ink-950 shadow-glow" : "text-white/55 hover:bg-white/[0.04] hover:text-white")}>
              <t.icon size={16} /> {t.label}
            </button>
          );
        })}
      </div>
      <Current />
    </div>
  );
}
