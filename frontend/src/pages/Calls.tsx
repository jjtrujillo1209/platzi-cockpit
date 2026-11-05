import { useState, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Contact } from "../lib/api";
import { Header } from "./Dashboard";
import { Card, Badge, Empty, Stat } from "../components/ui";
import { Phone, MessageCircle, Mail, Linkedin, Search, Check, X, PhoneCall, CheckSquare, Square } from "lucide-react";

const OUTCOMES = ["Contestó", "No contestó", "Agendó", "No interesado", "Volver a llamar"];

const emailSent = (c: Contact) => c.email_status === "sent";
const liSent = (c: Contact) =>
  ["sent", "already", "pending_invite"].includes(c.linkedin_status);

export function phoneDigits(s: string): string {
  const d = (s || "").replace(/\D/g, "");
  // Colombia: celulares de 10 dígitos -> prefijo 57.
  return d.length === 10 ? `57${d}` : d;
}

function YesNo({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`chip ${ok ? "bg-volt/10 text-volt" : "bg-white/[0.04] text-white/35"}`}>
      {ok ? <Check size={12} /> : <X size={12} />} {label}
    </span>
  );
}

export default function Calls() {
  const [q, setQ] = useState("");
  const [validacion, setValidacion] = useState("Verificado");
  const [soloPend, setSoloPend] = useState(false);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const saveTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const qc = useQueryClient();
  const { data: facets } = useQuery({ queryKey: ["facets"], queryFn: api.facets });
  const callsKey = ["calls", q, validacion];
  const { data } = useQuery({
    queryKey: callsKey,
    queryFn: () => api.contacts({ ...(q ? { q } : {}), ...(validacion ? { validacion } : {}), limit: "1000" }),
  });
  let rows = data?.contacts ?? [];
  rows = rows.filter((c) => c.celular);
  if (soloPend) rows = rows.filter((c) => !(emailSent(c) && liSent(c)));

  const conTel = rows.length;
  const conCorreo = rows.filter(emailSent).length;
  const conLi = rows.filter(liSent).length;
  const llamados = rows.filter((c) => c.llamada).length;

  function noteFor(c: Contact): string {
    return notes[c.call_key] ?? c.llamada_notes ?? "";
  }

  async function toggleCall(c: Contact) {
    await api.setCall({ key: c.call_key, called: !c.llamada });
    qc.invalidateQueries({ queryKey: ["calls"] });
  }
  async function setOutcome(c: Contact, outcome: string) {
    await api.setCall({ key: c.call_key, called: true, outcome, notes: noteFor(c) });
    qc.invalidateQueries({ queryKey: ["calls"] });
  }
  function handleNoteChange(c: Contact, value: string) {
    setNotes((prev) => ({ ...prev, [c.call_key]: value }));
    clearTimeout(saveTimers.current[c.call_key]);
    saveTimers.current[c.call_key] = setTimeout(async () => {
      await api.setCall({ key: c.call_key, called: true, outcome: c.llamada_outcome, notes: value });
      qc.invalidateQueries({ queryKey: ["calls"] });
    }, 900);
  }

  return (
    <div className="space-y-6">
      <Header title="Llamadas" sub="Hoja de contactos para llamar: empresa, correo y teléfono, con estado de outreach" />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Contactos con teléfono" value={conTel} accent="white" icon={<Phone size={18} />} />
        <Stat label="Correo enviado" value={conCorreo} accent="platzi" sub={`${conTel - conCorreo} sin correo`} icon={<Mail size={18} />} />
        <Stat label="Conexión enviada" value={conLi} accent="sky" sub={`${conTel - conLi} sin conexión`} icon={<Linkedin size={18} />} />
        <Stat label="Llamados" value={llamados} accent="volt"
          sub={`${conTel - llamados} por llamar`} icon={<PhoneCall size={18} />} />
      </div>

      <Card className="!p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[220px] flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
            <input className="input pl-9" placeholder="Buscar empresa, nombre, correo…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="input max-w-[200px]" value={validacion} onChange={(e) => setValidacion(e.target.value)}>
            <option value="">Validación: todas</option>
            {(facets?.validaciones ?? []).map((s: string) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button onClick={() => setSoloPend(!soloPend)}
            className={`chip ${soloPend ? "bg-platzi/15 text-platzi" : "bg-white/[0.04] text-white/50"}`}>
            {soloPend ? "Solo pendientes ✓" : "Solo pendientes"}
          </button>
          <Badge>{rows.length} para llamar</Badge>
        </div>
      </Card>

      <Card className="!p-0 overflow-hidden">
        <div className="max-h-[calc(100vh-340px)] overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 z-10 bg-ink-850/95 text-xs uppercase tracking-wide text-white/40 backdrop-blur">
              <tr>
                <th className="px-4 py-3">Empresa / Contacto</th>
                <th className="px-4 py-3">Cargo</th>
                <th className="px-4 py-3">Teléfono</th>
                <th className="px-4 py-3">Correo</th>
                <th className="px-4 py-3">Outreach</th>
                <th className="px-4 py-3">Llamada</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c: Contact) => {
                const tel = phoneDigits(c.celular);
                return (
                  <tr key={c.id} className="border-t border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <div className="font-medium">{c.empresa || "—"}</div>
                      <div className="text-xs text-white/45">{c.nombre} {c.ciudad ? `· ${c.ciudad}` : ""}</div>
                    </td>
                    <td className="px-4 py-3 text-white/65">{c.cargo || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-white/80">{c.celular}</span>
                        {tel && (
                          <>
                            <a href={`tel:+${tel}`} title="Llamar"
                              className="grid h-7 w-7 place-items-center rounded-lg bg-white/[0.05] text-sky-300 hover:bg-white/10">
                              <Phone size={13} />
                            </a>
                            <a href={`https://wa.me/${tel}`} target="_blank" rel="noreferrer" title="WhatsApp"
                              className="grid h-7 w-7 place-items-center rounded-lg bg-white/[0.05] text-volt hover:bg-white/10">
                              <MessageCircle size={13} />
                            </a>
                          </>
                        )}
                        {c.linkedin && (
                          <a href={c.linkedin} target="_blank" rel="noreferrer" title="Ver LinkedIn"
                            className="grid h-7 w-7 place-items-center rounded-lg bg-white/[0.05] text-[#0A66C2] hover:bg-white/10">
                            <Linkedin size={13} />
                          </a>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {c.email ? <a href={`mailto:${c.email}`} className="text-platzi hover:underline">{c.email}</a>
                        : <span className="text-white/25">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1.5">
                        <YesNo ok={emailSent(c)} label="Correo" />
                        <YesNo ok={liSent(c)} label="LinkedIn" />
                      </div>
                    </td>
                    <td className="px-4 py-3 min-w-[220px]">
                      <div className="flex items-center gap-2">
                        <button onClick={() => toggleCall(c)} title="Marcar llamada"
                          className={`flex items-center gap-1.5 text-xs font-medium ${c.llamada ? "text-volt" : "text-white/40 hover:text-white/70"}`}>
                          {c.llamada ? <CheckSquare size={18} /> : <Square size={18} />}
                          {c.llamada ? "Llamado" : "Llamar"}
                        </button>
                        {c.llamada && (
                          <select value={c.llamada_outcome} onChange={(e) => setOutcome(c, e.target.value)}
                            className="rounded-lg border border-white/10 bg-ink-850 px-2 py-1 text-xs text-white/80 outline-none focus:border-volt/60">
                            <option value="">resultado…</option>
                            {OUTCOMES.map((o) => <option key={o} value={o}>{o}</option>)}
                          </select>
                        )}
                      </div>
                      {c.llamada && (
                        <textarea
                          rows={2}
                          placeholder="Notas de la llamada…"
                          value={noteFor(c)}
                          onChange={(e) => handleNoteChange(c, e.target.value)}
                          className="mt-2 w-full resize-none rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder-white/25 outline-none focus:border-volt/50 focus:bg-white/[0.06]"
                        />
                      )}
                      {c.llamada && c.llamada_ts && (
                        <div className="mt-0.5 text-[10px] text-white/35">{c.llamada_ts.replace("T", " ").slice(0, 16)}</div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {rows.length === 0 && <Empty>Sin contactos con teléfono para este filtro</Empty>}
        </div>
      </Card>
    </div>
  );
}
