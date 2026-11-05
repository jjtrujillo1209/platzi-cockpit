import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Header } from "./Dashboard";
import { Card } from "../components/ui";
import { ContactPicker, pickContacts } from "../components/ContactPicker";
import { LiveProgress } from "../components/LiveProgress";
import { Field, Toggle } from "./Connections";
import { Mail, MailCheck, MailPlus, ShieldCheck, AlertTriangle } from "lucide-react";
import clsx from "clsx";

type EmailTab = "email_pending" | "email_sent";

export default function Emails() {
  // ambas listas viven separadas: lo nuevo prospectado nunca se mezcla con lo ya enviado
  const { data: pend } = useQuery({ queryKey: ["contacts", "email_pending", ""], queryFn: () => api.contacts({ channel: "email_pending", limit: "400" }) });
  const { data: sent } = useQuery({ queryKey: ["contacts", "email_sent", ""], queryFn: () => api.contacts({ channel: "email_sent", limit: "400" }) });
  const { data: tpls } = useQuery({ queryKey: ["templates"], queryFn: api.templates });
  const templates = tpls?.templates ?? [];

  const [tab, setTab] = useState<EmailTab>("email_pending");
  const all = (tab === "email_pending" ? pend?.contacts : sent?.contacts) ?? [];

  // selección independiente por pestaña
  const [selPend, setSelPend] = useState<Set<string>>(new Set());
  const [selSent, setSelSent] = useState<Set<string>>(new Set());
  const sel = tab === "email_pending" ? selPend : selSent;
  const setSel = tab === "email_pending" ? setSelPend : setSelSent;

  const [template, setTemplate] = useState("");
  const [subject, setSubject] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [bcc, setBcc] = useState("jtrujillo@platzi.com");
  const [jobId, setJobId] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  const [launchErr, setLaunchErr] = useState("");
  const [capNotice, setCapNotice] = useState("");

  const launch = async () => {
    const recipients = pickContacts(sel, all).map((c) => ({
      email: c.email, nombre: c.nombre, empresa: c.empresa, cargo: c.cargo, ciudad: c.ciudad, linkedin: c.linkedin,
    }));
    if (!recipients.length) return;
    setLaunching(true); setLaunchErr(""); setCapNotice("");
    try {
      const j = await api.launchEmail({
        recipients, template: template || null, subject: subject || null,
        bcc: bcc ? bcc.split(",").map((s) => s.trim()).filter(Boolean) : [], dry_run: dryRun,
      });
      if (j.error) { setLaunchErr(j.error); return; }
      if (j.job_id) setJobId(j.job_id);
      if (j.cap_info && j.cap_info.enviados < j.cap_info.solicitados) {
        setCapNotice(`Solo se enviarán ${j.cap_info.enviados} de ${j.cap_info.solicitados} — tope diario de email compartido con el copiloto (${j.cap_info.usado_hoy}/${j.cap_info.cap} ya usado hoy).`);
      }
    } finally { setLaunching(false); }
  };

  const activeTpl = templates.find((t) => t.file === template);
  const resend = tab === "email_sent";
  const verb = dryRun ? "Simular" : resend ? "Reenviar a" : "Enviar a";

  return (
    <div className="space-y-6">
      <Header title="Correos" sub="Campaña por SendGrid con open/click tracking activado (cuando no es dry-run)" />

      <div className="flex gap-2">
        <TabBtn active={tab === "email_pending"} onClick={() => setTab("email_pending")}>
          <MailPlus size={14} /> Sin enviar
          <span className="rounded-md bg-white/10 px-1.5 text-[11px]">{pend?.total ?? "…"}</span>
        </TabBtn>
        <TabBtn active={tab === "email_sent"} onClick={() => setTab("email_sent")}>
          <MailCheck size={14} /> Ya enviados
          <span className="rounded-md bg-white/10 px-1.5 text-[11px]">{sent?.total ?? "…"}</span>
        </TabBtn>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1fr]">
        <Card className="flex h-[560px] min-w-0 flex-col">
          <ContactPicker key={tab} channelFilter={tab} selected={sel} onChange={setSel} />
        </Card>

        <div className="min-w-0 space-y-6">
          <Card>
            <h3 className="mb-3 text-sm font-semibold">Plantilla y envío</h3>
            <Field label="Plantilla">
              <select className="input" value={template} onChange={(e) => setTemplate(e.target.value)}>
                <option value="">— Predeterminada del sistema —</option>
                {templates.map((t) => <option key={t.file} value={t.file}>{t.nombre}</option>)}
              </select>
            </Field>
            {activeTpl && (
              <div className="mt-2 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-xs text-white/55">
                <div className="font-medium text-white/75">{activeTpl.subject}</div>
                {activeTpl.cta?.url && <div className="mt-1">CTA → <span className="text-platzi">{activeTpl.cta.text}</span> · {activeTpl.cta.url}</div>}
              </div>
            )}
            <div className="mt-4"><Field label="Asunto (opcional, sobreescribe plantilla)"><input className="input" placeholder="{nombre}, te escribo desde Platzi…" value={subject} onChange={(e) => setSubject(e.target.value)} /></Field></div>
            <div className="mt-4"><Field label="BCC (separado por comas)"><input className="input" value={bcc} onChange={(e) => setBcc(e.target.value)} /></Field></div>
            <div className="mt-3"><Toggle on={dryRun} set={setDryRun} label="Dry-run (no envía, sólo simula)" /></div>

            <button className="btn-primary mt-5 w-full" disabled={!sel.size || launching} onClick={launch}>
              <Mail size={16} /> {launching ? "Lanzando…" : `${verb} ${sel.size}`}
            </button>
            {resend && !dryRun && (
              <p className="mt-2 text-center text-[11px] text-amber-400">
                ⚠ Estos contactos ya recibieron un correo — esto es un reenvío.
              </p>
            )}
            {!dryRun && (
              <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-[11px] text-volt">
                <ShieldCheck size={12} /> Tracking de aperturas y clicks activado
              </p>
            )}
            {launchErr && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-danger"><AlertTriangle size={14} /> {launchErr}</p>
            )}
            {capNotice && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-amber-400"><AlertTriangle size={14} /> {capNotice}</p>
            )}
          </Card>
          {jobId && <LiveProgress jobId={jobId} channel="email" />}
        </div>
      </div>
    </div>
  );
}

function TabBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition",
        active ? "bg-platzi/15 text-platzi" : "text-white/45 hover:text-white/75",
      )}
    >
      {children}
    </button>
  );
}
