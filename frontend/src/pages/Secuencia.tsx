import { useEffect, useState } from "react";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ExternalLink, Link2, MousePointerClick,
  RefreshCw, Reply, Send, ShieldCheck, Sparkles, UserCheck, XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, Trazabilidad, TrazaFila } from "../lib/api";
import { Card, Empty, Spinner } from "../components/ui";

/** Un eslabón del embudo. El número de arriba es el que sobrevive al eslabón;
 *  el de abajo dice cuántos se cayeron ahí, que es lo que hay que trabajar. */
function Eslabon({
  label, value, prev, icon, ultimo = false, acento = "white",
}: {
  label: string; value: number; prev?: number; icon: React.ReactNode;
  ultimo?: boolean; acento?: "white" | "platzi" | "amber";
}) {
  const caida = prev !== undefined ? prev - value : 0;
  const pct = prev ? Math.round((value / prev) * 100) : null;
  return (
    <>
      <div className="min-w-0 flex-1">
        <div className="card p-4">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-white/45">
            <span className={acento === "platzi" ? "text-platzi" : "text-white/40"}>{icon}</span>
            {label}
          </div>
          <div className={clsx("mt-2 text-2xl font-extrabold tracking-tight",
            acento === "platzi" && value > 0 && "text-platzi",
            acento === "amber" && value > 0 && "text-amber")}>
            {value}
          </div>
          <div className="mt-0.5 h-4 text-[11px] text-white/40">
            {pct !== null && `${pct}% · se cayeron ${caida}`}
          </div>
        </div>
      </div>
      {!ultimo && <ArrowRight size={14} className="mt-8 shrink-0 text-white/20" />}
    </>
  );
}

function EstadoCola({ f }: { f: TrazaFila }) {
  if (f.inscrito_ts) return <span className="chip bg-platzi/15 text-platzi">inscrito</span>;
  const mapa: Record<string, [string, string]> = {
    pending: ["bg-sky-400/15 text-sky-300", "por aprobar"],
    blocked: ["bg-amber/15 text-amber", "bloqueado"],
    approved: ["bg-white/[0.08] text-white/70", "aprobado"],
    failed: ["bg-rose-400/15 text-rose-300", "saltado"],
    skipped: ["bg-white/[0.06] text-white/40", "descartado"],
  };
  const m = mapa[f.cola_estado];
  if (!m) return <span className="text-white/25">—</span>;
  return <span className={clsx("chip", m[0])} title={f.cola_razones}>{m[1]}</span>;
}

function Fila({ f }: { f: TrazaFila }) {
  return (
    <tr className="border-b border-white/[0.04] align-top hover:bg-white/[0.02]">
      <td className="py-2.5 pr-3">
        <div className="font-medium text-white/90">{f.nombre || "—"}</div>
        <div className="text-[11px] text-white/40">{f.empresa}</div>
      </td>
      <td className="px-3 py-2.5">
        {f.codigo ? (
          <a href={f.link} target="_blank" rel="noreferrer"
             className="font-mono text-[11px] text-white/60 underline-offset-2 hover:text-platzi hover:underline"
             title={f.link}>{f.codigo}</a>
        ) : <span className="text-amber" title="Sin código no hay atribución posible">sin link</span>}
      </td>
      <td className="max-w-[26rem] px-3 py-2.5">
        {f.gancho
          ? <span className="text-[11px] leading-snug text-white/55">{f.gancho}</span>
          : <span className="text-white/25">—</span>}
      </td>
      <td className="px-3 py-2.5"><EstadoCola f={f} /></td>
      <td className="px-3 py-2.5 text-center">
        {f.clics > 0
          ? <span className="chip bg-platzi/15 text-platzi" title={f.clic_ts}>{f.clics}</span>
          : <span className="text-white/25">—</span>}
      </td>
      <td className="px-3 py-2.5 text-center">
        {f.respuestas > 0
          ? <span className="chip bg-volt/15 text-volt" title={f.respuesta_ts}>{f.respuestas}</span>
          : <span className="text-white/25">—</span>}
      </td>
      <td className="px-3 py-2.5">
        {f.registro === "confirmado" ? (
          <span className={clsx("chip", f.registro_atribuido
            ? "bg-platzi/15 text-platzi" : "bg-amber/15 text-amber")}
            title={f.registro_atribuido
              ? "Llegó con su código: registro atribuido"
              : "Registro sin código: no se puede amarrar al outreach"}>
            {f.registro_atribuido ? "registrado" : "registrado (sin atribuir)"}
          </span>
        ) : f.registro
          ? <span className="chip bg-white/[0.06] text-white/50">{f.registro}</span>
          : <span className="text-white/25">—</span>}
      </td>
    </tr>
  );
}


export default function Secuencia() {
  const [d, setD] = useState<Trazabilidad | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState("");
  const [firmando, setFirmando] = useState(false);
  // Qué campaña mirar. Ya no está fija en el código: se retiró la del desayuno
  // y puede no haber ninguna, que es un estado legítimo y hay que mostrarlo en
  // vez de pedir la trazabilidad de algo que no existe.
  const [slug, setSlug] = useState<string | null>(null);
  const [sinCampanas, setSinCampanas] = useState(false);

  async function cargar() {
    setCargando(true);
    try {
      let s = slug;
      if (!s) {
        const cs = await api.campaigns();
        if (!cs.campaigns.length) { setSinCampanas(true); setD(null); setError(""); return; }
        s = cs.campaigns[0].slug;
        setSlug(s);
      }
      setD(await api.trazabilidad(s));
      setError("");
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setCargando(false);
    }
  }
  useEffect(() => { cargar(); }, []);

  /** Constancia humana de que la plantilla lleva los campos propios. No es una
   *  verificación: la API no puede leer la plantilla, solo una persona puede. */
  async function firmar(verificada: boolean) {
    setFirmando(true);
    try {
      if (!slug) return;
      await api.plantillaVerificada(verificada, slug);
      await cargar();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setFirmando(false);
    }
  }

  if (sinCampanas) return (
    <div className="space-y-6">
      <Card>
      <div className="py-10 text-center text-sm text-white/45">
        No hay ninguna secuencia con copy aprobado.
        <div className="mt-1 text-xs text-white/30">
          La campaña del desayuno se retiró el 25/08. Hasta que se cree la que la
          reemplace, el botón «Secuencia» del Pipeline no tiene dónde inscribir.
        </div>
      </div>
      </Card>
    </div>
  );
  if (cargando && !d) return <div className="py-20 text-center"><Spinner /></div>;
  if (error) return <Card><div className="text-sm text-rose-300">No pude leer la trazabilidad: {error}</div></Card>;
  if (!d) return null;

  const { secuencia: s, embudo: e, webhook, preflight_campana: pc } = d;
  const activa = s.status === "active";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Secuencia · trazabilidad</h1>
          <p className="mt-1 text-sm text-white/45">
            {d.campaign.name} · evento {d.campaign.event_day} ·{" "}
            {pc.days_to_event >= 0 ? `faltan ${pc.days_to_event} días` : "el evento ya pasó"}
          </p>
        </div>
        <button onClick={cargar} className="btn-ghost">
          <RefreshCw size={14} className={cargando ? "animate-spin" : ""} /> Refrescar
        </button>
      </div>

      {/* ---------- la secuencia que envía ---------- */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-white/35">
              Motor de envío · Amplemarket
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="text-lg font-bold">{s.name || "sin secuencia amarrada"}</span>
              <span className={clsx("chip", activa
                ? "bg-platzi/15 text-platzi" : "bg-amber/15 text-amber")}>
                {activa ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
                {s.status || "—"}
              </span>
            </div>
            <div className="mt-1 font-mono text-[11px] text-white/30">{s.id || "—"}</div>
          </div>
          {s.url && (
            <a href={s.url} target="_blank" rel="noreferrer" className="btn-ghost">
              Abrir en Amplemarket <ExternalLink size={13} />
            </a>
          )}
        </div>

        {s.problems.length > 0 && (
          <div className="mt-4 space-y-1.5 rounded-xl border border-rose-400/20 bg-rose-400/[0.06] p-3">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-rose-300">
              <XCircle size={13} /> Frena el envío
            </div>
            {s.problems.map((p) => <div key={p} className="text-[11px] text-rose-200/80">· {p}</div>)}
          </div>
        )}

        {s.verificada ? (
          <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-platzi/20 bg-platzi/[0.05] p-3 text-[11px]">
            <CheckCircle2 size={13} className="text-platzi" />
            <span className="font-semibold text-platzi">Plantilla revisada a mano</span>
            <span className="text-white/45">
              el {s.verificada} · lleva {s.campos_esperados.join(", ")}
            </span>
            <button onClick={() => firmar(false)}
              className="ml-auto text-[10px] text-white/30 underline-offset-2 hover:text-white/60 hover:underline"
              title="Quita la constancia y vuelve a mostrar el aviso">
              quitar constancia
            </button>
          </div>
        ) : s.warnings.length > 0 && (
          <div className="mt-3 space-y-1.5 rounded-xl border border-amber/20 bg-amber/[0.05] p-3">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-amber">
              <AlertTriangle size={13} /> No se puede verificar por API
            </div>
            {s.warnings.map((p) => <div key={p} className="text-[11px] text-amber/70">· {p}</div>)}
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <div className="min-w-0 flex-1 text-[10px] text-white/35">
                Amplemarket solo reporta sus campos nativos ({s.dynamic_fields_used.join(", ") || "—"}),
                nunca los propios. La única comprobación real es abrir la secuencia.
              </div>
              <button onClick={() => firmar(true)} disabled={firmando} className="btn-ghost shrink-0 !py-1.5 !text-xs">
                {firmando ? <Spinner /> : <CheckCircle2 size={13} />} Ya la revisé
              </button>
            </div>
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 border-t border-white/[0.06] pt-3 text-[11px]">
          <span className={clsx("flex items-center gap-1.5",
            webhook.configurado ? "text-white/50" : "text-amber")}>
            <ShieldCheck size={12} />
            Webhook de respuestas: {webhook.configurado ? "con secreto" : "sin configurar"}
          </span>
          <span className={clsx("flex items-center gap-1.5",
            pc.ok ? "text-white/50" : "text-amber")}>
            <Sparkles size={12} />
            Campaña: {pc.ok ? "lista" : pc.problems.join(" · ")}
          </span>
        </div>
      </Card>

      {/* ---------- el embudo ---------- */}
      <div className="flex items-start gap-2">
        <Eslabon label="Con link propio" value={e.con_link} icon={<Link2 size={12} />} />
        <Eslabon label="Gancho preparado" value={e.preparados} prev={e.con_link} icon={<Sparkles size={12} />} />
        <Eslabon label="Inscritos" value={e.inscritos} prev={e.preparados} icon={<Send size={12} />} />
        <Eslabon label="Hicieron clic" value={e.con_clic} prev={e.inscritos} icon={<MousePointerClick size={12} />} acento="platzi" />
        <Eslabon label="Respondieron" value={e.con_respuesta} prev={e.con_clic} icon={<Reply size={12} />} acento="platzi" />
        <Eslabon label="Registrados" value={e.registrados_atribuidos} prev={e.con_clic}
                 icon={<UserCheck size={12} />} acento="platzi" ultimo />
      </div>

      {(e.bloqueados > 0 || e.pendientes > 0 || e.saltados > 0) && (
        <div className="flex flex-wrap gap-2 text-[11px]">
          {e.pendientes > 0 && <span className="chip bg-sky-400/15 text-sky-300">{e.pendientes} por aprobar</span>}
          {e.bloqueados > 0 && <span className="chip bg-amber/15 text-amber">{e.bloqueados} bloqueados por la compuerta</span>}
          {e.saltados > 0 && <span className="chip bg-rose-400/15 text-rose-300">{e.saltados} saltados por Amplemarket</span>}
          {e.registrados > e.registrados_atribuidos && (
            <span className="chip bg-amber/15 text-amber">
              {e.registrados - e.registrados_atribuidos} registros sin atribuir
            </span>
          )}
        </div>
      )}

      {/* ---------- la cadena, contacto por contacto ---------- */}
      <Card className="overflow-x-auto">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-semibold">Cadena por contacto</div>
          <div className="text-[11px] text-white/35">{d.filas.length} en la campaña</div>
        </div>
        {d.filas.length === 0 ? (
          <Empty>
            Nadie tiene link ni gancho todavía, así que no hay nada que trazar.
            <div className="mx-auto mt-3 max-w-lg text-left text-[11px] leading-relaxed text-white/40">
              La cadena arranca al preparar el lote: eso genera el gancho y el link propio
              de cada contacto y los deja en la cola de aprobación, sin inscribir a nadie.
              Hoy se dispara por API —{" "}
              <span className="font-mono text-white/55">POST /api/campaigns/{slug}/amplemarket/prepare</span>{" "}
              con los <span className="font-mono">contact_ids</span> del lote.
            </div>
          </Empty>
        ) : (
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wide text-white/35">
                <th className="py-2 pr-3">Contacto</th>
                <th className="px-3 py-2">Código</th>
                <th className="px-3 py-2">Gancho generado</th>
                <th className="px-3 py-2">Estado</th>
                <th className="px-3 py-2 text-center">Clics</th>
                <th className="px-3 py-2 text-center">Respuestas</th>
                <th className="px-3 py-2">Registro</th>
              </tr>
            </thead>
            <tbody>
              {d.filas.map((f) => <Fila key={f.contact_id} f={f} />)}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
