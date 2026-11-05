import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Contact } from "../lib/api";
import { Search } from "lucide-react";
import clsx from "clsx";

export function ContactPicker({
  channelFilter,
  selected,
  onChange,
}: {
  channelFilter?: string; // linkedin_pending | connected | has_email
  selected: Set<string>;
  onChange: (s: Set<string>) => void;
}) {
  const [q, setQ] = useState("");
  const { data } = useQuery({
    queryKey: ["contacts", channelFilter, q],
    queryFn: () => api.contacts({ ...(channelFilter ? { channel: channelFilter } : {}), ...(q ? { q } : {}), limit: "400" }),
  });
  const rows = data?.contacts ?? [];

  const toggle = (id: string) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange(next);
  };
  const allVisible = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const toggleAll = () => {
    const next = new Set(selected);
    if (allVisible) rows.forEach((r) => next.delete(r.id));
    else rows.forEach((r) => next.add(r.id));
    onChange(next);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
          <input className="input pl-9" placeholder="Buscar empresa, nombre, correo…"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <button className="btn-ghost !py-2 text-xs" onClick={toggleAll}>
          {allVisible ? "Quitar todos" : "Seleccionar todos"}
        </button>
      </div>
      <div className="flex items-center justify-between px-1 pb-2 text-xs text-white/40">
        <span>{rows.length} contactos</span>
        <span className="text-volt">{selected.size} seleccionados</span>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {rows.map((c: Contact) => {
          const on = selected.has(c.id);
          return (
            <button key={c.id} onClick={() => toggle(c.id)}
              className={clsx("flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition",
                on ? "border-platzi/50 bg-platzi/[0.08]" : "border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.05]")}>
              <span className={clsx("grid h-4 w-4 place-items-center rounded border", on ? "border-platzi bg-platzi" : "border-white/20")}>
                {on && <span className="h-1.5 w-1.5 rounded-sm bg-ink-950" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{c.empresa || "—"}</div>
                <div className="truncate text-xs text-white/45">
                  {c.nombre} · {c.cargo || "—"} {c.ciudad ? `· ${c.ciudad}` : ""}
                </div>
              </div>
              <div className="shrink-0 text-right text-[10px] text-white/35">
                {c.linkedin && <div>in/✓</div>}
                {c.email && (
                  c.email_bounced
                    ? <div className="text-platzi" title={c.bounce_reason}>⚠ rebotó</div>
                    : c.email_enviado
                    ? <div className="text-volt">✉ enviado</div>
                    : <div className="text-white/30">✉</div>
                )}
              </div>
            </button>
          );
        })}
        {rows.length === 0 && <div className="py-8 text-center text-xs text-white/30">Sin resultados</div>}
      </div>
    </div>
  );
}

export function pickContacts(ids: Set<string>, all: Contact[]): Contact[] {
  return all.filter((c) => ids.has(c.id));
}
