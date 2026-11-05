import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Contact } from "../lib/api";
import { Header } from "./Dashboard";
import { Card, Badge, Empty } from "../components/ui";
import { statusOf } from "../lib/status";
import { Search, Linkedin, Mail } from "lucide-react";
import ContactTimeline from "../components/ContactTimeline";

const VAL_STYLE: Record<string, { color: string; dot: string }> = {
  "Verificado": { color: "text-volt", dot: "bg-volt" },
  "Corregir link": { color: "text-amber", dot: "bg-amber" },
  "Link muerto": { color: "text-danger", dot: "bg-danger" },
  "Sin LinkedIn": { color: "text-white/40", dot: "bg-white/30" },
};

export default function DatabaseView() {
  const [q, setQ] = useState("");
  const [sector, setSector] = useState("");
  const [estado, setEstado] = useState("");
  const [validacion, setValidacion] = useState("");
  const [seat, setSeat] = useState("");
  // La ficha del prospecto se abre desde aquí: el Pipeline solo muestra 150 por
  // columna, así que esta tabla es la única vía a los 976.
  const [abierto, setAbierto] = useState<number | null>(null);
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.facets });
  const { data: perfiles } = useQuery({ queryKey: ["seats"], queryFn: api.seats, staleTime: 60_000 });
  const { data } = useQuery({
    queryKey: ["contacts-db", q, sector, estado, validacion, seat],
    queryFn: () => api.contacts({ ...(q ? { q } : {}), ...(sector ? { sector } : {}), ...(estado ? { estado } : {}), ...(validacion ? { validacion } : {}), ...(seat ? { seat } : {}), limit: "1000" }),
  });
  const rows = data?.contacts ?? [];

  return (
    <div className="space-y-6">
      <Header title="Base de datos" sub={`${facets?.total ?? "—"} contactos · estado por canal en vivo`} />

      <Card className="!p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[240px] flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
            <input className="input pl-9" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="input max-w-[260px]" value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="">Todos los sectores</option>
            {(facets?.sectores ?? []).map((s: string) => <option key={s} value={s}>{s.slice(0, 50)}</option>)}
          </select>
          <select className="input max-w-[200px]" value={validacion} onChange={(e) => setValidacion(e.target.value)}>
            <option value="">Validación: todas</option>
            {(facets?.validaciones ?? []).map((s: string) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="input max-w-[220px]" value={seat} onChange={(e) => setSeat(e.target.value)}>
            <option value="">Firma: todos los perfiles</option>
            {(perfiles?.seats ?? []).map((p) => (
              <option key={p.seat_id} value={p.seat_id}>{p.nombre}</option>
            ))}
          </select>
          <Badge>{rows.length} resultados</Badge>
        </div>
      </Card>

      <Card className="!p-0 overflow-hidden">
        <div className="max-h-[calc(100vh-300px)] overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 z-10 bg-ink-850/95 text-xs uppercase tracking-wide text-white/40 backdrop-blur">
              <tr>
                <th className="px-4 py-3">Empresa / Contacto</th>
                <th className="px-4 py-3">Cargo</th>
                <th className="px-4 py-3">Ciudad</th>
                <th className="px-4 py-3">Firma</th>
                <th className="px-4 py-3">LinkedIn</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Validación</th>
                <th className="px-4 py-3">Canales</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c: Contact) => {
                const li = statusOf(c.linkedin_status);
                const em = statusOf(c.email_status);
                return (
                  <tr key={c.id} onClick={() => setAbierto(Number(c.id))}
                    className="cursor-pointer border-t border-white/[0.04] hover:bg-white/[0.04]"
                    title="Abrir la ficha del prospecto">
                    <td className="px-4 py-3">
                      <div className="font-medium">{c.empresa || "—"}</div>
                      <div className="text-xs text-white/45">{c.nombre}</div>
                    </td>
                    <td className="px-4 py-3 text-white/65">{c.cargo || "—"}</td>
                    <td className="px-4 py-3 text-white/55">{c.ciudad || "—"}</td>
                    <td className="px-4 py-3 text-xs">
                      {c.li_seat_nombre
                        ? <span className="chip bg-platzi/10 text-platzi">{c.li_seat_nombre.split(" ")[0]}</span>
                        : <span className="text-white/25">sin asignar</span>}
                    </td>
                    <td className="px-4 py-3">{c.linkedin ? <Linkedin size={15} className="text-sky-400" /> : <span className="text-white/20">—</span>}</td>
                    <td className="px-4 py-3">{c.email ? <Mail size={15} className="text-platzi" /> : <span className="text-white/20">—</span>}</td>
                    <td className="px-4 py-3">
                      {c.validacion ? (
                        <div>
                          <span className={`flex items-center gap-1.5 text-xs font-medium ${(VAL_STYLE[c.validacion] || VAL_STYLE["Sin LinkedIn"]).color}`}>
                            <span className={`h-1.5 w-1.5 rounded-full ${(VAL_STYLE[c.validacion] || VAL_STYLE["Sin LinkedIn"]).dot}`} />
                            {c.validacion}
                          </span>
                          {c.validacion === "Corregir link" && c.empresa_linkedin && (
                            <div className="mt-0.5 text-[10px] text-white/40">LI: {c.empresa_linkedin}</div>
                          )}
                        </div>
                      ) : <span className="text-xs text-white/25">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        {c.linkedin_status && <span className={`flex items-center gap-1.5 text-xs ${li.color}`}><span className={`h-1.5 w-1.5 rounded-full ${li.dot}`} />{li.label}</span>}
                        {c.email_status && <span className={`flex items-center gap-1.5 text-xs ${em.color}`}><span className={`h-1.5 w-1.5 rounded-full ${em.dot}`} />✉ {em.label}</span>}
                        {!c.linkedin_status && !c.email_status && <span className="text-xs text-white/25">sin contactar</span>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <Empty>Sin contactos para el filtro</Empty>}
        </div>
      </Card>

      {abierto != null && <ContactTimeline contactId={abierto} onClose={() => setAbierto(null)} />}
    </div>
  );
}
