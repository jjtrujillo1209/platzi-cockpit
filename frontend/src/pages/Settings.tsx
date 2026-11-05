import { useEffect, useState, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Seat } from "../lib/api";
import { Header } from "./Dashboard";
import { Card, Empty, Spinner } from "./../components/ui";
import { Field, Toggle } from "./Connections";
import {
  KeyRound, ShieldCheck, AlertTriangle, Save, Plug, CheckCircle2, Sparkles, Linkedin, LogIn, Target, Inbox, ExternalLink,
  Users, Flame, Crown, Power, PowerOff, Trash2, UserPlus,
} from "lucide-react";

export default function Settings() {
  const { data, refetch, isLoading } = useQuery({ queryKey: ["settingsAll"], queryFn: api.settingsAll });
  if (isLoading || !data) return <Empty>Cargando ajustes…</Empty>;
  return (
    <div className="space-y-6">
      <Header title="Ajustes" sub="Accesos y credenciales del cockpit" />
      <SeatsCard />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SendgridCard s={data.sendgrid} reload={refetch} />
        <AnthropicCard s={data.anthropic} reload={refetch} />
        <GmailCard />
        <LinkedInCard s={data.linkedin} reload={refetch} />
        <GoogleCard s={data.google} reload={refetch} />
        <ApolloCard s={data.apollo} reload={refetch} />
      </div>
    </div>
  );
}

function Shell({ icon, title, sub, badge, children }: {
  icon: ReactNode; title: string; sub: string; badge: ReactNode; children: ReactNode;
}) {
  return (
    <Card>
      <div className="mb-5 flex items-center gap-2">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-platzi/15 text-platzi">{icon}</div>
        <div><h3 className="text-sm font-semibold">{title}</h3><p className="text-xs text-white/45">{sub}</p></div>
        <span className="ml-auto">{badge}</span>
      </div>
      {children}
    </Card>
  );
}

function StatusBadge({ ok, okText = "Configurada", noText = "Sin configurar" }: { ok: boolean; okText?: string; noText?: string }) {
  return (
    <span className={`chip ${ok ? "bg-volt/10 text-volt" : "bg-amber/10 text-amber"}`}>
      {ok ? <><ShieldCheck size={12} /> {okText}</> : <><AlertTriangle size={12} /> {noText}</>}
    </span>
  );
}

function TestResult({ t, ok }: { t: any; ok: ReactNode }) {
  if (!t) return null;
  return (
    <div className={`mt-4 rounded-xl border px-4 py-3 text-sm ${t.ok ? "border-volt/30 bg-volt/[0.06] text-volt" : "border-platzi/30 bg-platzi/[0.06] text-platzi"}`}>
      {t.ok ? <span className="flex items-center gap-1.5"><CheckCircle2 size={15} /> {ok}</span>
        : <span className="flex items-center gap-2"><AlertTriangle size={15} /> {t.error}</span>}
    </div>
  );
}

function SendgridCard({ s, reload }: { s: any; reload: () => void }) {
  const [key, setKey] = useState(""); const [fe, setFe] = useState(""); const [fn, setFn] = useState(""); const [bcc, setBcc] = useState("");
  const [busy, setBusy] = useState(false); const [test, setTest] = useState<any>(null); const [saved, setSaved] = useState(false);
  useEffect(() => { setFe(s.from_email || ""); setFn(s.from_name || ""); setBcc(s.bcc || ""); }, [s]);
  const save = async () => { setBusy(true); setSaved(false); try { await api.sendgridUpdate({ ...(key ? { api_key: key } : {}), from_email: fe, from_name: fn, bcc }); setKey(""); setSaved(true); reload(); } finally { setBusy(false); } };
  return (
    <Shell icon={<KeyRound size={18} />} title="SendGrid · API" sub="Envío de correos y tracking" badge={<StatusBadge ok={s.configured} />}>
      <div className="space-y-4">
        <Field label={`API Key${s.configured ? ` (actual: ${s.masked})` : ""}`}>
          <input className="input font-mono" type="password" autoComplete="off" placeholder={s.configured ? "•••• dejar vacío = mantener" : "SG.xxxx…"} value={key} onChange={(e) => setKey(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Remitente (email)"><input className="input" value={fe} onChange={(e) => setFe(e.target.value)} /></Field>
          <Field label="Remitente (nombre)"><input className="input" value={fn} onChange={(e) => setFn(e.target.value)} /></Field>
        </div>
        <Field label="BCC por defecto"><input className="input" value={bcc} onChange={(e) => setBcc(e.target.value)} /></Field>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy}><Save size={16} /> Guardar</button>
        <button className="btn-ghost" onClick={async () => setTest(await api.sendgridTest(key || undefined))}><Plug size={16} /> Probar</button>
        {saved && <span className="text-xs text-volt">Guardado ✓</span>}
      </div>
      <TestResult t={test} ok={test && `Válida · ${test.scopes} permisos · ${test.can_send ? "puede enviar" : "sin mail.send"}`} />
    </Shell>
  );
}

function AnthropicCard({ s, reload }: { s: any; reload: () => void }) {
  const [key, setKey] = useState(""); const [busy, setBusy] = useState(false); const [test, setTest] = useState<any>(null); const [saved, setSaved] = useState(false);
  const save = async () => { if (!key) return; setBusy(true); setSaved(false); try { await api.anthropicUpdate(key); setKey(""); setSaved(true); reload(); } finally { setBusy(false); } };
  return (
    <Shell icon={<Sparkles size={18} />} title="Anthropic · Claude" sub="IA de mensajes personalizados" badge={<StatusBadge ok={s.configured} />}>
      <Field label={`API Key${s.configured ? ` (actual: ${s.masked})` : ""}`}>
        <input className="input font-mono" type="password" autoComplete="off" placeholder={s.configured ? "•••• dejar vacío = mantener" : "sk-ant-…"} value={key} onChange={(e) => setKey(e.target.value)} />
      </Field>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy || !key}><Save size={16} /> Guardar</button>
        <button className="btn-ghost" onClick={async () => setTest(await api.anthropicTest(key || undefined))}><Plug size={16} /> Probar</button>
        {saved && <span className="text-xs text-volt">Guardado ✓</span>}
      </div>
      <TestResult t={test} ok={test && `Válida · ${test.models} modelos disponibles`} />
    </Shell>
  );
}

function LinkedInCard({ s, reload }: { s: any; reload: () => void }) {
  const [cookies, setCookies] = useState(""); const [busy, setBusy] = useState(false); const [res, setRes] = useState<any>(null);
  const save = async () => { if (!cookies.trim()) return; setBusy(true); try { const r = await api.linkedinUpdate(cookies); setRes(r); if (r.ok) { setCookies(""); reload(); } } finally { setBusy(false); } };
  return (
    <Shell icon={<Linkedin size={18} />} title="LinkedIn · Sesión suelta"
      sub="Solo para jobs manuales; el outreach lo firman los perfiles de arriba"
      badge={<StatusBadge ok={s.valid} okText={`Activa${s.li_at_expires ? ` · vence ${s.li_at_expires}` : ""}`} noText={s.configured ? "Expirada" : "Sin sesión"} />}>
      <div className="mb-3 text-xs text-white/45">{s.count} cookies · li_at {s.valid ? "vigente" : "no vigente"}{s.li_at_expires ? ` (vence ${s.li_at_expires})` : ""}</div>
      <Field label="Pegar cookies (JSON exportado del navegador)">
        <textarea className="input h-28 resize-none font-mono text-xs" placeholder='[{"name":"li_at","value":"...","domain":".linkedin.com","expires":...}, ...]' value={cookies} onChange={(e) => setCookies(e.target.value)} />
      </Field>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy || !cookies.trim()}><Save size={16} /> Actualizar sesión</button>
      </div>
      {res && (
        <div className={`mt-4 rounded-xl border px-4 py-3 text-sm ${res.ok ? "border-volt/30 bg-volt/[0.06] text-volt" : "border-platzi/30 bg-platzi/[0.06] text-platzi"}`}>
          {res.ok ? <span className="flex items-center gap-1.5"><CheckCircle2 size={15} /> Sesión actualizada · {res.count} cookies · vence {res.li_at_expires}</span>
            : <span className="flex items-center gap-2"><AlertTriangle size={15} /> {res.error}</span>}
        </div>
      )}
    </Shell>
  );
}

function ApolloCard({ s, reload }: { s: any; reload: () => void }) {
  const [key, setKey] = useState(""); const [busy, setBusy] = useState(false); const [test, setTest] = useState<any>(null); const [saved, setSaved] = useState(false);
  const save = async () => { if (!key) return; setBusy(true); setSaved(false); try { await api.apolloUpdate(key); setKey(""); setSaved(true); reload(); } finally { setBusy(false); } };
  return (
    <Shell icon={<Target size={18} />} title="Apollo · Prospección" sub="Organigrama y decisores por empresa" badge={<StatusBadge ok={s?.configured} />}>
      <Field label={`API Key${s?.configured ? ` (actual: ${s.masked})` : ""}`}>
        <input className="input font-mono" type="password" autoComplete="off" placeholder={s?.configured ? "•••• dejar vacío = mantener" : "API key de Apollo.io"} value={key} onChange={(e) => setKey(e.target.value)} />
      </Field>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy || !key}><Save size={16} /> Guardar</button>
        <button className="btn-ghost" onClick={async () => setTest(await api.apolloTest(key || undefined))}><Plug size={16} /> Probar</button>
        {saved && <span className="text-xs text-volt">Guardado ✓</span>}
      </div>
      <TestResult t={test} ok="Válida · autenticada en Apollo" />
      <p className="mt-3 text-[11px] text-white/35">Settings → API en Apollo.io. La búsqueda de personas requiere plan que habilite el API.</p>
      <ApolloBudget />
    </Shell>
  );
}

function ApolloBudget() {
  const { data, refetch } = useQuery({ queryKey: ["apolloBudget"], queryFn: api.apolloBudgetGet });
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    const n = parseInt(value, 10);
    if (Number.isNaN(n) || n < 0) return;
    setBusy(true); setSaved(false);
    try { await api.apolloBudgetSet(n); setValue(""); setSaved(true); refetch(); }
    finally { setBusy(false); }
  };

  const remaining = data?.remaining ?? 0;
  const low = remaining <= 0;

  return (
    <div className="mt-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-white/70">Presupuesto local de créditos</span>
        <span className={low ? "font-semibold text-platzi" : "text-volt"}>
          {remaining} disponibles {data?.used ? `(${data.used} usados de ${data.budget})` : ""}
        </span>
      </div>
      <p className="mt-1.5 text-[11px] text-white/35">
        Apollo no expone el saldo real por API — repórtalo aquí tras revisarlo en tu
        dashboard (Settings → Plan/Usage). Mientras esté en 0, los jobs masivos
        (Prospección y enriquecimiento) se niegan a arrancar en vez de agotarlo sin avisar.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <input className="input font-mono text-sm" type="number" min={0} placeholder="créditos disponibles ahora"
               value={value} onChange={(e) => setValue(e.target.value)} />
        <button className="btn-ghost shrink-0" onClick={save} disabled={busy || !value}>
          <Save size={15} /> Actualizar
        </button>
      </div>
      {saved && <span className="mt-1.5 block text-[11px] text-volt">Guardado ✓</span>}
    </div>
  );
}

function GmailCard() {
  const { data: g, refetch } = useQuery({ queryKey: ["gmailStatus"], queryFn: api.gmailStatus, refetchInterval: 20000 });
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "connect">("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const state = g?.state ?? "not_configured";
  const badge = state === "connected"
    ? <span className="chip bg-volt/10 text-volt"><ShieldCheck size={12} /> Conectado</span>
    : state === "needs_connect"
      ? <span className="chip bg-sky-400/10 text-sky-300"><Plug size={12} /> Falta autorizar</span>
      : state === "error"
        ? <span className="chip bg-platzi/10 text-platzi-soft"><AlertTriangle size={12} /> Error</span>
        : <span className="chip bg-amber/10 text-amber"><AlertTriangle size={12} /> Sin configurar</span>;

  const saveSecret = async () => {
    if (!secret.trim()) return;
    setBusy("save"); setMsg(null);
    try {
      const r = await api.gmailSaveSecret(secret);
      if (r.ok) { setSecret(""); setMsg({ ok: true, text: `Credencial guardada (${r.type}). Ahora autoriza el acceso.` }); refetch(); }
      else setMsg({ ok: false, text: r.error || "No se pudo guardar" });
    } finally { setBusy(""); }
  };
  const connect = async () => {
    setBusy("connect"); setMsg({ ok: true, text: "Abriendo el navegador… elige la cuenta y acepta el acceso de solo lectura." });
    try {
      const r = await api.gmailConnect();
      if (r.ok) { setMsg({ ok: true, text: `Conectado como ${r.email} ✓` }); refetch(); }
      else setMsg({ ok: false, text: r.error || "No se pudo conectar" });
    } finally { setBusy(""); }
  };

  return (
    <Shell icon={<Inbox size={18} />} title="Gmail · Detección de respuestas"
      sub="Lee el inbox (solo lectura) para detectar quién respondió" badge={badge}>
      {state === "connected" ? (
        <div className="space-y-3">
          <div className="rounded-xl border border-volt/25 bg-volt/[0.05] px-4 py-3 text-sm text-volt">
            <span className="flex items-center gap-1.5"><CheckCircle2 size={15} /> Conectado como <b>{g?.connected_email}</b></span>
          </div>
          <p className="text-xs text-white/45">
            El copiloto revisa respuestas en cada ciclo. {g?.last_check && `Última revisión: ${g.last_check.slice(0, 16).replace("T", " ")}.`}
          </p>
          <button className="btn-ghost" onClick={async () => { setBusy("connect"); try { await api.gmailCheck(); refetch(); } finally { setBusy(""); } }} disabled={busy !== ""}>
            {busy === "connect" ? <Spinner /> : <Plug size={16} />} Revisar ahora
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="rounded-lg border border-sky-400/20 bg-sky-400/[0.05] px-3 py-2 text-[11px] text-sky-200/90">
            Las respuestas de los prospectos ahora llegan a <b>tu bandeja</b> (reply_to = jtrujillo@platzi.com).
            En el paso 3 autoriza <b>tu propia cuenta</b>.
          </p>
          <ol className="space-y-1.5 text-xs text-white/55">
            <li className="flex gap-2"><b className="text-white/70">1.</b> Abre
              <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer"
                className="inline-flex items-center gap-1 text-platzi-soft hover:underline">Google Cloud Console <ExternalLink size={11} /></a>
              → habilita <b>Gmail API</b> → crea un <b>OAuth client ID</b> tipo <b>Desktop app</b>.</li>
            <li className="flex gap-2"><b className="text-white/70">2.</b> Descarga el JSON y pégalo aquí abajo.</li>
            <li className="flex gap-2"><b className="text-white/70">3.</b> Pulsa <b>Conectar</b> → elige <b>jtrujillo@platzi.com</b> → acepta (solo lectura).</li>
          </ol>

          <Field label={g?.client_secret_exists ? "client_secret.json (ya cargado — pega otro para reemplazar)" : "Pega el client_secret.json"}>
            <textarea className="input h-24 resize-none font-mono text-xs"
              placeholder='{"installed":{"client_id":"...","project_id":"...","client_secret":"..."}}'
              value={secret} onChange={(e) => setSecret(e.target.value)} />
          </Field>

          <div className="flex items-center gap-3">
            <button className="btn-ghost" onClick={saveSecret} disabled={busy !== "" || !secret.trim()}>
              {busy === "save" ? <Spinner /> : <Save size={16} />} Guardar credencial
            </button>
            <button className="btn-primary" onClick={connect} disabled={busy !== "" || !g?.client_secret_exists}>
              {busy === "connect" ? <Spinner /> : <LogIn size={16} />} Conectar
            </button>
          </div>
        </div>
      )}
      {msg && (
        <div className={`mt-4 rounded-xl border px-4 py-3 text-sm ${msg.ok ? "border-volt/30 bg-volt/[0.06] text-volt" : "border-platzi/30 bg-platzi/[0.06] text-platzi-soft"}`}>
          <span className="flex items-center gap-2">{msg.ok ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />} {msg.text}</span>
        </div>
      )}
    </Shell>
  );
}

function GoogleCard({ s, reload }: { s: any; reload: () => void }) {
  const [cid, setCid] = useState(""); const [dom, setDom] = useState(""); const [busy, setBusy] = useState(false); const [saved, setSaved] = useState(false);
  useEffect(() => { setCid(s.client_id || ""); setDom(s.allowed_domain || "platzi.com"); }, [s]);
  const save = async () => { setBusy(true); setSaved(false); try { await api.googleUpdate({ client_id: cid, allowed_domain: dom }); setSaved(true); reload(); } finally { setBusy(false); } };
  return (
    <Shell icon={<LogIn size={18} />} title="Google · Login" sub="Inicio de sesión restringido por dominio"
      badge={<StatusBadge ok={s.configured} okText="Activo" noText="Abierto (sin login)" />}>
      <div className="space-y-4">
        <Field label="Client ID (OAuth Web · no es secreto)">
          <input className="input font-mono text-xs" placeholder="xxxx.apps.googleusercontent.com" value={cid} onChange={(e) => setCid(e.target.value)} />
        </Field>
        <Field label="Dominio permitido"><input className="input" value={dom} onChange={(e) => setDom(e.target.value)} /></Field>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-primary" onClick={save} disabled={busy}><Save size={16} /> Guardar</button>
        {saved && <span className="text-xs text-volt">Guardado ✓ — recarga la página para aplicar el login</span>}
      </div>
      <p className="mt-3 text-[11px] text-white/35">Con Client ID, el cockpit exige login Google de <b className="text-white/60">@{dom || "tu-dominio"}</b>. Vacío = acceso abierto.</p>
    </Shell>
  );
}


// ===================== Asientos de LinkedIn ==============================
// Cada VP presta su perfil y trae su propia sesión, su cupo y su calentamiento;
// el cockpit no puede mezclarlos porque LinkedIn restringe por cuenta. Esta
// tarjeta responde de un vistazo la única pregunta que importa mientras no
// entreguen el login: cuáles pueden enviar hoy y cuánto. Cuántos son se lee del
// backend —se abren desde aquí—, así que no hay número escrito en la pantalla.
function SeatsCard() {
  const { data, refetch, isLoading } = useQuery({
    queryKey: ["seats"], queryFn: api.seats, refetchInterval: 30_000,
  });
  const [sumando, setSumando] = useState(false);
  const [errorSuma, setErrorSuma] = useState("");
  if (isLoading || !data) return <Card><Empty>Cargando asientos…</Empty></Card>;
  return (
    <Card>
      <div className="mb-5 flex items-center gap-2">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-platzi/15 text-platzi"><Users size={18} /></div>
        <div>
          <h3 className="text-sm font-semibold">LinkedIn · Perfiles que firman</h3>
          <p className="text-xs text-white/45">
            Cada VP entra una sola vez y el cockpit se queda con su perfil. {data.listos} de {data.total}{" "}
            {data.total === 1 ? "perfil puede" : "perfiles pueden"} enviar hoy ·{" "}
            {data.remaining_invites} invitaciones y {data.remaining_mensajes} mensajes disponibles
          </p>
        </div>
      </div>
      <p className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 text-xs leading-relaxed text-white/50">
        La ventana de Chrome se abre <b className="text-white/70">en este computador</b>, no en el del VP:
        el navegador corre donde corre el cockpit. Si el VP no está acá, lo práctico es una llamada
        compartiendo pantalla y dejarlo escribir, o que pase un minuto por esta máquina. Su contraseña
        no pasa por el cockpit — la escribe en un Chrome normal y solo queda la sesión.
      </p>
      {data.problemas.length > 0 && (
        <ul className="mb-5 space-y-1 rounded-xl border border-amber/25 bg-amber/[0.06] px-4 py-3 text-xs text-amber">
          {data.problemas.map((p) => <li key={p} className="flex gap-2"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{p}</li>)}
        </ul>
      )}
      {/* La rejilla sigue al número de perfiles: con dos, tres columnas dejan un
          hueco que se lee como "falta uno" cuando no falta ninguno. */}
      <div className={`grid grid-cols-1 gap-4 ${data.seats.length > 2 ? "lg:grid-cols-3" : "md:grid-cols-2"}`}>
        {data.seats.map((seat) => <SeatCard key={seat.seat_id} seat={seat} reload={refetch} />)}
      </div>
      {/* Sumar un perfil era editar una tupla en `seats.py` y reiniciar el
          backend. Ahora el botón está siempre: con cupos apagados estrena uno
          (no gasta una carpeta de Chrome nueva) y sin ellos abre un espacio.
          El espacio nace en cupo 0 — enviar exige la sesión, que se da desde su
          propia tarjeta. */}
      <button
        className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-white/10 px-4 py-3 text-xs text-white/40 hover:border-white/20 hover:text-white/60 disabled:opacity-40"
        disabled={sumando}
        onClick={async () => {
          setSumando(true); setErrorSuma("");
          try {
            if (data.libres.length > 0) await api.seatUpdate(data.libres[0], { activo: true });
            else await api.seatCrear();
            await refetch();
          } catch (e: any) {
            setErrorSuma(e?.message || "no se pudo abrir el espacio");
          } finally { setSumando(false); }
        }}>
        <UserPlus size={13} />
        {sumando ? "Abriendo el espacio…"
          : data.libres.length > 0
            ? `Sumar otro perfil (${data.libres.length} cupo${data.libres.length > 1 ? "s" : ""} sin usar)`
            : "Abrir un espacio para otro perfil"}
      </button>
      <p className="mt-2 text-center text-[11px] text-white/30">
        Aparece como tarjeta sin asignar: desde ahí se le abre la ventana de acceso y su nombre lo pone LinkedIn.
      </p>
      {errorSuma && <p className="mt-2 text-center text-[11px] text-amber">{errorSuma}</p>}
    </Card>
  );
}

function SeatCard({ seat, reload }: { seat: Seat; reload: () => void }) {
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; texto: string } | null>(null);
  // `sondeando` enciende el bucle que mira si el VP ya entró. Solo corre
  // mientras hay alguien en la ventana: es una llamada cada 2 s y no tiene
  // sentido pagarla cuando no hay nadie haciendo el trámite.
  const [sondeando, setSondeando] = useState(false);

  const { data: login } = useQuery({
    queryKey: ["seatLogin", seat.seat_id],
    queryFn: () => api.seatLoginEstado(seat.seat_id),
    refetchInterval: sondeando ? 2000 : false,
    enabled: sondeando,
    initialData: seat.login,
  });
  const fase = login?.fase ?? seat.login.fase;
  const entrado = !!seat.login.login_ts;

  // Ya entró: se guarda el perfil solo. El VP no tiene que pulsar nada más y
  // nadie tiene que teclear su nombre (ni equivocarse al hacerlo).
  useEffect(() => {
    if (!sondeando || fase !== "dentro") return;
    let vivo = true;
    (async () => {
      setBusy("guardando");
      try {
        const r = await api.seatLoginGuardar(seat.seat_id);
        if (!vivo) return;
        setMsg({ ok: true, texto: `Perfil guardado: ${r.perfil.nombre}` });
        setSondeando(false);
        reload();
      } catch (e: any) {
        if (vivo) setMsg({ ok: false, texto: e?.message || "no se pudo leer el perfil" });
      } finally {
        if (vivo) setBusy("");
      }
    })();
    return () => { vivo = false; };
  }, [sondeando, fase, seat.seat_id]);

  const correr = async (nombre: string, fn: () => Promise<any>, exito?: string) => {
    setBusy(nombre); setMsg(null);
    try {
      const r = await fn();
      if (exito) setMsg({ ok: true, texto: exito });
      reload();
      return r;
    } catch (e: any) {
      setMsg({ ok: false, texto: e?.message || "no se pudo" });
    } finally { setBusy(""); }
  };

  const abrir = async () => {
    setMsg(null); setBusy("abriendo");
    try {
      await api.seatLoginAbrir(seat.seat_id);
      setSondeando(true);
      setMsg({ ok: true, texto: "Ventana abierta. Que inicie sesión ahí." });
    } catch (e: any) {
      setMsg({ ok: false, texto: e?.message || "no se pudo abrir la ventana" });
    } finally { setBusy(""); }
  };

  const semaforo: Record<string, { texto: string; cls: string }> = {
    cerrado: { texto: "Ventana cerrada", cls: "bg-white/[0.06] text-white/50" },
    login: { texto: "Esperando que inicie sesión…", cls: "bg-amber/[0.1] text-amber" },
    verifica: { texto: "LinkedIn pidió verificación", cls: "bg-amber/[0.1] text-amber" },
    otro: { texto: "Ventana abierta, sin LinkedIn a la vista", cls: "bg-white/[0.06] text-white/50" },
    dentro: { texto: "Sesión iniciada", cls: "bg-volt/[0.1] text-volt" },
  };
  const sem = semaforo[fase] ?? semaforo.cerrado;

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="mb-3 flex items-start gap-2">
        <Linkedin size={16} className={seat.listo ? "mt-0.5 text-volt" : "mt-0.5 text-white/30"} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{seat.nombre}</div>
          <div className="truncate text-xs text-white/40" title={seat.rol}>{seat.rol || seat.seat_id}</div>
        </div>
        {seat.es_anfitrion && <span className="chip bg-platzi/10 text-platzi"><Crown size={11} /> anfitrión</span>}
      </div>

      {!entrado ? (
        <>
          <p className="mb-3 text-xs leading-relaxed text-white/50">
            Se abre una ventana de Chrome propia de este asiento. El VP entra a LinkedIn ahí
            y listo: el cockpit guarda la sesión y lee su nombre solo. La contraseña no pasa
            por acá — se escribe en un navegador normal y no queda guardada.
          </p>
          <button className="btn-primary w-full" onClick={abrir} disabled={!!busy}>
            {busy === "abriendo" ? <Spinner /> : <LogIn size={15} />} Abrir ventana para iniciar sesión
          </button>
          {(sondeando || fase !== "cerrado") && (
            <div className={`mt-3 rounded-xl px-3 py-2 text-xs ${sem.cls}`}>
              {busy === "guardando" ? "Leyendo el perfil…" : sem.texto}
              {login?.puerto ? <span className="ml-1 text-white/30">· puerto {login.puerto}</span> : null}
            </div>
          )}
          {fase === "login" && (
            <ol className="mt-3 space-y-1 text-[11px] text-white/40">
              <li>1. Pásale el teclado al VP (o que entre él mismo).</li>
              <li>2. Correo y clave de LinkedIn en esa ventana.</li>
              <li>3. Si pide código, que lo escriba ahí mismo.</li>
              <li>4. Cuando cargue el feed, esto se guarda solo.</li>
            </ol>
          )}
        </>
      ) : (
        <>
          <div className={`mb-3 rounded-xl px-3 py-2 text-xs ${seat.listo ? "bg-volt/[0.08] text-volt" : "bg-amber/[0.08] text-amber"}`}>
            {seat.listo ? <ShieldCheck size={12} className="mr-1 inline" /> : <AlertTriangle size={12} className="mr-1 inline" />}
            {seat.sesion.detalle}
          </div>
          {seat.login.perfil_url && (
            <a href={seat.login.perfil_url} target="_blank" rel="noreferrer"
               className="mb-3 flex items-center gap-1 text-[11px] text-platzi hover:underline">
              <ExternalLink size={11} /> {seat.login.perfil_url.replace("https://www.linkedin.com/in/", "")}
            </a>
          )}
          <div className="mb-3 grid grid-cols-3 gap-2 text-center">
            <Cupo label="Invitaciones" usado={seat.invites.used + seat.invites.queued} cap={seat.invites.cap} />
            <Cupo label="Semana" usado={seat.semana.used} cap={seat.semana.cap} />
            <Cupo label="Mensajes" usado={seat.mensajes.used + seat.mensajes.queued} cap={seat.mensajes.cap} />
          </div>
          <div className="mb-2 flex items-center gap-1.5 text-[11px] text-white/40">
            <Flame size={11} /> {semanaDe(seat.warmup_start)} · techo {seat.tope_dia}/día · {seat.asignados} contactos
          </div>
          {/* La sesión vive en el perfil de Chrome; esto es el paracaídas. Lo
              que de verdad le interesa a alguien mirando: hasta cuándo sirve. */}
          <button
            className="mb-3 flex w-full items-center gap-1.5 rounded-lg bg-white/[0.03] px-2 py-1.5 text-left text-[11px] text-white/40 hover:bg-white/[0.06]"
            disabled={!!busy}
            title="Copiar la sesión al archivo de respaldo"
            onClick={() => correr("respaldo", () => api.seatRespaldar(seat.seat_id), "Sesión respaldada")}>
            <ShieldCheck size={11} className="shrink-0" />
            {seat.login.respaldo.existe
              ? <>respaldo del {seat.login.respaldo.fecha.slice(0, 10)}
                  {seat.login.respaldo.li_at_vence
                    ? ` · sesión válida hasta ${seat.login.respaldo.li_at_vence}`
                    : " · sin fecha de vencimiento"}</>
              : <>sin respaldo — clic para guardarlo</>}
          </button>
          <div className="mb-3 flex gap-2">
            {seat.login.chrome_abierto ? (
              <button className="btn flex-1" disabled={!!busy}
                onClick={() => correr("cerrar", () => api.seatLoginCerrar(seat.seat_id), "Ventana cerrada; la sesión queda guardada")}>
                <PowerOff size={13} /> Cerrar ventana
              </button>
            ) : (
              <button className="btn flex-1" disabled={!!busy}
                onClick={() => correr("despertar", () => api.seatDespertar(seat.seat_id), "Ventana lista")}>
                {busy === "despertar" ? <Spinner /> : <Power size={13} />} Despertar
              </button>
            )}
            <button className="btn" disabled={!!busy}
              onClick={() => { if (confirm(`¿Quitar la sesión de ${seat.nombre}? Habrá que volver a pedirle que entre.`)) correr("olvidar", () => api.seatLoginOlvidar(seat.seat_id), "Sesión borrada"); }}>
              <Trash2 size={13} /> Quitar
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Techo diario">
              <input className="input" type="number" min={0} defaultValue={seat.tope_dia}
                     onBlur={(e) => Number(e.target.value) !== seat.tope_dia && correr("tope", () => api.seatUpdate(seat.seat_id, { tope_dia: Number(e.target.value) }))} />
            </Field>
            <Field label="Techo semanal">
              <input className="input" type="number" min={0} defaultValue={seat.semana.cap || undefined}
                     onBlur={(e) => e.target.value && correr("tope", () => api.seatUpdate(seat.seat_id, { tope_semana: Number(e.target.value) }))} />
            </Field>
          </div>
          <div className="mt-2 space-y-2">
            <Toggle on={seat.es_anfitrion} label="Es el anfitrión del evento"
                    set={(v) => correr("anf", () => api.seatUpdate(seat.seat_id, { es_anfitrion: v }))} />
            <Toggle on={seat.activo} label="Activo"
                    set={(v) => correr("act", () => api.seatUpdate(seat.seat_id, { activo: v }))} />
          </div>
        </>
      )}

      {msg && (
        <div className={`mt-3 rounded-xl border px-3 py-2 text-xs ${msg.ok ? "border-volt/30 bg-volt/[0.06] text-volt" : "border-platzi/30 bg-platzi/[0.06] text-platzi"}`}>
          {msg.texto}
        </div>
      )}
    </div>
  );
}

function semanaDe(inicio: string) {
  if (!inicio) return "sin estrenar";
  const dias = Math.floor((Date.now() - new Date(inicio + "T00:00:00").getTime()) / 86_400_000);
  return `semana ${Math.floor(dias / 7) + 1} de calentamiento`;
}

function Cupo({ label, usado, cap }: { label: string; usado: number; cap: number }) {
  return (
    <div className="rounded-xl bg-white/[0.03] px-2 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className={`text-sm font-bold ${cap === 0 ? "text-white/30" : usado >= cap ? "text-amber" : "text-white"}`}>
        {usado}/{cap}
      </div>
    </div>
  );
}
