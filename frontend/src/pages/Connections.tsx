import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Header } from "./Dashboard";
import { Card } from "../components/ui";
import { ContactPicker, pickContacts } from "../components/ContactPicker";
import { LiveProgress } from "../components/LiveProgress";
import { Rocket, AlertTriangle, Plus, X, Linkedin } from "lucide-react";

const DEFAULT_NOTE =
  "Hola {nombre}, me encantaría conectar para generar un gran impacto juntos.";

type Extra = { linkedin: string; nombre: string; empresa: string };

export default function Connections() {
  const { data } = useQuery({ queryKey: ["contacts", "linkedin_pending", ""], queryFn: () => api.contacts({ channel: "linkedin_pending", limit: "400" }) });
  const all = data?.contacts ?? [];
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [extras, setExtras] = useState<Extra[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [nameInput, setNameInput] = useState("");
  const [message, setMessage] = useState(DEFAULT_NOTE);
  const [jobId, setJobId] = useState<string | null>(null);
  const [opts, setOpts] = useState({ daily_cap: 20, min_delay: 8, max_delay: 18, headless: true, sales_nav: false });
  const [launching, setLaunching] = useState(false);
  const [launchErr, setLaunchErr] = useState("");
  const [capNotice, setCapNotice] = useState("");

  const total = sel.size + extras.length;

  const addExtra = () => {
    const url = urlInput.trim();
    if (!url.includes("linkedin.com/in/")) {
      setLaunchErr("Pega una URL válida de perfil (linkedin.com/in/…)");
      return;
    }
    if (extras.some((e) => e.linkedin === url)) { setUrlInput(""); setNameInput(""); return; }
    setExtras([...extras, { linkedin: url, nombre: nameInput.trim(), empresa: "" }]);
    setUrlInput(""); setNameInput(""); setLaunchErr("");
  };

  const launch = async () => {
    const picked = pickContacts(sel, all).map((c) => ({ linkedin: c.linkedin, nombre: c.nombre, cargo: c.cargo, empresa: c.empresa }));
    const contacts = [...picked, ...extras];
    if (!contacts.length) return;
    setLaunching(true); setLaunchErr(""); setCapNotice("");
    try {
      const j = await api.launchLinkedin({ contacts, message, ...opts });
      if (j.error) { setLaunchErr(j.error); return; }
      if (j.job_id) setJobId(j.job_id);
      if (j.cap_info && j.cap_info.enviados < j.cap_info.solicitados) {
        setCapNotice(`Solo se enviarán ${j.cap_info.enviados} de ${j.cap_info.solicitados} — tope diario de LinkedIn compartido con el copiloto (${j.cap_info.usado_hoy}/${j.cap_info.cap} ya usado hoy).`);
      }
    } finally { setLaunching(false); }
  };

  return (
    <div className="space-y-6">
      <Header title="Conexiones LinkedIn" sub="Selecciona prospectos (o pega un perfil) y lanza solicitudes con mensaje personalizado" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <Card className="flex h-[560px] min-w-0 flex-col">
          <ContactPicker channelFilter="linkedin_pending" selected={sel} onChange={setSel} />
        </Card>

        <div className="min-w-0 space-y-6">
          <Card>
            <h3 className="mb-4 text-sm font-semibold">Configuración del envío</h3>

            {/* Agregar un perfil de LinkedIn directo (no tiene que estar en el pipeline) */}
            <div className="mb-4">
              <span className="mb-1 block text-xs text-white/45">Agregar perfil de LinkedIn</span>
              <div className="flex gap-2">
                <input className="input flex-1 font-mono text-xs" placeholder="https://www.linkedin.com/in/…"
                       value={urlInput} onChange={(e) => setUrlInput(e.target.value)}
                       onKeyDown={(e) => e.key === "Enter" && addExtra()} />
                <input className="input w-28 text-sm" placeholder="Nombre"
                       value={nameInput} onChange={(e) => setNameInput(e.target.value)}
                       onKeyDown={(e) => e.key === "Enter" && addExtra()} />
                <button className="btn-ghost !px-3" onClick={addExtra}><Plus size={16} /></button>
              </div>
              {extras.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {extras.map((e, i) => (
                    <span key={e.linkedin} className="flex items-center gap-1.5 rounded-lg bg-platzi/10 px-2 py-1 text-xs text-platzi">
                      <Linkedin size={11} /> {e.nombre || e.linkedin.split("/in/")[1]?.replace(/\/$/, "") || "perfil"}
                      <button onClick={() => setExtras(extras.filter((_, j) => j !== i))}><X size={12} /></button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Mensaje personalizado de la invitación */}
            <div className="mb-4">
              <span className="mb-1 block text-xs text-white/45">Mensaje de invitación (deja vacío para enviar sin nota)</span>
              <textarea className="input min-h-[90px] resize-y text-sm" value={message}
                        onChange={(e) => setMessage(e.target.value)}
                        placeholder="Hola {nombre}, me encantaría conectar…" />
              <div className="mt-1.5 flex items-center gap-2 text-[11px]">
                <button className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/50 hover:text-white" onClick={() => setMessage(message + "{nombre}")}>+ {"{nombre}"}</button>
                <button className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/50 hover:text-white" onClick={() => setMessage(message + "{empresa}")}>+ {"{empresa}"}</button>
                <span className={`ml-auto ${message.length > 300 ? "text-danger" : "text-white/30"}`}>{message.length}/300</span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <Field label="Tope diario"><input type="number" className="input" value={opts.daily_cap} onChange={(e) => setOpts({ ...opts, daily_cap: +e.target.value })} /></Field>
              <Field label="Delay (s) min–max">
                <div className="flex gap-2">
                  <input type="number" className="input" value={opts.min_delay} onChange={(e) => setOpts({ ...opts, min_delay: +e.target.value })} />
                  <input type="number" className="input" value={opts.max_delay} onChange={(e) => setOpts({ ...opts, max_delay: +e.target.value })} />
                </div>
              </Field>
            </div>
            <div className="mt-4 space-y-2">
              <Toggle on={opts.sales_nav} set={(v) => setOpts({ ...opts, sales_nav: v })} label="Guardar también en Sales Navigator" />
              <Toggle on={!opts.headless} set={(v) => setOpts({ ...opts, headless: !v })} label="Ver el navegador (no headless)" />
            </div>
            <button className="btn-primary mt-5 w-full" disabled={!total || launching} onClick={launch}>
              <Rocket size={16} /> {launching ? "Lanzando…" : `Conectar con ${total} prospecto${total === 1 ? "" : "s"}`}
            </button>
            <p className="mt-2 text-center text-[11px] text-white/35">Usa la sesión de LinkedIn guardada (cookies). Pacing humano entre cada perfil.</p>
            {launchErr && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-danger"><AlertTriangle size={14} /> {launchErr}</p>
            )}
            {capNotice && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-amber-400"><AlertTriangle size={14} /> {capNotice}</p>
            )}
          </Card>

          {jobId && <LiveProgress jobId={jobId} channel="linkedin" />}
        </div>
      </div>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-xs text-white/45">{label}</span>{children}</label>;
}

export function Toggle({ on, set, label }: { on: boolean; set: (v: boolean) => void; label: string }) {
  return (
    <button onClick={() => set(!on)} className="flex w-full items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-sm">
      <span className="text-white/75">{label}</span>
      <span className={`relative h-5 w-9 rounded-full transition ${on ? "bg-platzi" : "bg-white/15"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? "left-4" : "left-0.5"}`} />
      </span>
    </button>
  );
}
