import clsx from "clsx";
import { ReactNode } from "react";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("card p-5", className)}>{children}</div>;
}

export function Stat({
  label, value, sub, accent = "white", icon,
}: {
  label: string; value: ReactNode; sub?: ReactNode;
  accent?: "platzi" | "volt" | "amber" | "white" | "sky"; icon?: ReactNode;
}) {
  const ring = {
    platzi: "from-platzi/20", volt: "from-volt/20", amber: "from-amber/20",
    sky: "from-sky-400/20", white: "from-white/10",
  }[accent];
  return (
    <div className="card relative overflow-hidden p-5">
      <div className={clsx("pointer-events-none absolute -right-8 -top-8 h-28 w-28 rounded-full bg-gradient-to-br to-transparent blur-2xl", ring)} />
      <div className="flex items-start justify-between">
        <div className="text-xs uppercase tracking-wide text-white/45">{label}</div>
        {icon && <div className="text-white/40">{icon}</div>}
      </div>
      <div className="mt-2 text-3xl font-extrabold tracking-tight">{value}</div>
      {sub && <div className="mt-1 text-xs text-white/45">{sub}</div>}
    </div>
  );
}

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx("chip bg-white/[0.06] text-white/70", className)}>{children}</span>;
}

export function Progress({ value, total }: { value: number; total: number }) {
  const pct = total ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
      <div
        className="h-full rounded-full bg-gradient-to-r from-platzi to-platzi-soft transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-platzi" />
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-white/35">{children}</div>;
}
