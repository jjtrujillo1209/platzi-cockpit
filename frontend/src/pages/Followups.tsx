import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Header } from "./Dashboard";
import { Card } from "../components/ui";
import { ContactPicker, pickContacts } from "../components/ContactPicker";
import { LiveProgress } from "../components/LiveProgress";
import { Field, Toggle } from "./Connections";
import { Send, AlertTriangle } from "lucide-react";

const DEFAULT_MSG =
  "Gracias {nombre} por conectar !\nVamos a generar un gran impacto juntos";

export default function Followups() {
  const { data } = useQuery({ queryKey: ["contacts", "connected", ""], queryFn: () => api.contacts({ channel: "connected", limit: "400" }) });
  const all = data?.contacts ?? [];
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState(DEFAULT_MSG);
  const [jobId, setJobId] = useState<string | null>(null);
  const [opts, setOpts] = useState({ daily_cap: 30, min_delay: 6, max_delay: 14, headless: true });
  const [launching, setLaunching] = useState(false);
  const [launchErr, setLaunchErr] = useState("");
  const [capNotice, setCapNotice] = useState("");

  const launch = async () => {
    const contacts = pickContacts(sel, all).map((c) => ({ linkedin: c.linkedin, nombre: c.nombre, empresa: c.empresa }));
    if (!contacts.length) return;
    setLaunching(true); setLaunchErr(""); setCapNotice("");
    try {
      const j = await api.launchFollowup({ contacts, message: msg, default_message: msg.replace(/\{nombre\}|\{empresa\}/g, "").trim(), ...opts });
      if (j.error) { setLaunchErr(j.error); return; }
      if (j.job_id) setJobId(j.job_id);
      if (j.cap_info && j.cap_info.enviados < j.cap_info.solicitados) {
        setCapNotice(`Solo se enviarán ${j.cap_info.enviados} de ${j.cap_info.solicitados} — tope diario de LinkedIn compartido con el copiloto y Conexiones (${j.cap_info.usado_hoy}/${j.cap_info.cap} ya usado hoy).`);
      }
    } finally { setLaunching(false); }
  };

  return (
    <div className="space-y-6">
      <Header title="Follow-ups LinkedIn" sub="Envía un mensaje a contactos ya conectados. Usa {nombre} y {empresa} para personalizar." />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1fr]">
        <Card className="flex h-[560px] min-w-0 flex-col">
          <ContactPicker channelFilter="connected" selected={sel} onChange={setSel} />
        </Card>

        <div className="min-w-0 space-y-6">
          <Card>
            <h3 className="mb-3 text-sm font-semibold">Mensaje de follow-up</h3>
            <textarea className="input h-36 resize-none font-mono text-[13px] leading-relaxed" value={msg} onChange={(e) => setMsg(e.target.value)} />
            <div className="mt-2 flex gap-2 text-[11px] text-white/40">
              <button className="chip bg-white/5" onClick={() => setMsg(msg + " {nombre}")}>+ {"{nombre}"}</button>
              <button className="chip bg-white/5" onClick={() => setMsg(msg + " {empresa}")}>+ {"{empresa}"}</button>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <Field label="Tope diario"><input type="number" className="input" value={opts.daily_cap} onChange={(e) => setOpts({ ...opts, daily_cap: +e.target.value })} /></Field>
              <Field label="Delay (s) min–max">
                <div className="flex gap-2">
                  <input type="number" className="input" value={opts.min_delay} onChange={(e) => setOpts({ ...opts, min_delay: +e.target.value })} />
                  <input type="number" className="input" value={opts.max_delay} onChange={(e) => setOpts({ ...opts, max_delay: +e.target.value })} />
                </div>
              </Field>
            </div>
            <div className="mt-3"><Toggle on={!opts.headless} set={(v) => setOpts({ ...opts, headless: !v })} label="Ver el navegador (no headless)" /></div>
            <button className="btn-primary mt-5 w-full" disabled={!sel.size || launching} onClick={launch}>
              <Send size={16} /> {launching ? "Lanzando…" : `Enviar a ${sel.size} contacto${sel.size === 1 ? "" : "s"}`}
            </button>
            {launchErr && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-danger"><AlertTriangle size={14} /> {launchErr}</p>
            )}
            {capNotice && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-amber-400"><AlertTriangle size={14} /> {capNotice}</p>
            )}
          </Card>
          {jobId && <LiveProgress jobId={jobId} channel="followup" />}
        </div>
      </div>
    </div>
  );
}
