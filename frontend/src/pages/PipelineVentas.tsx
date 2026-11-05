import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Zap, ZapOff, PlayCircle, Eye, Linkedin, Building2, Clock, AlertTriangle, Check,
} from "lucide-react";
import clsx from "clsx";
import { api, PipelineContact, Stage } from "../lib/api";
import { STAGES } from "../lib/stages";
import { Card, Empty, Spinner, Stat } from "../components/ui";
import { Header } from "./Dashboard";
import ContactTimeline from "../components/ContactTimeline";

/* Tablero de lo que entra por Platzi Ventas (23-platzi-ventas.vercel.app).

   Es el Pipeline de siempre acotado a ese origen, con el interruptor del carril
   automático arriba. Se separa en su propia pantalla porque la pregunta que
   contesta es distinta a la del Pipeline general: no es «cómo va la base», es
   «lo que un vendedor mandó hace un rato, ¿ya salió?».

   La invitación de LinkedIn sin nota no pasa por la cola de aprobación —no hay
   texto que revisar— así que este carril envía solo. Lo que lo acota es el cupo
   diario de cada VP, su rampa de calentamiento y la bandera roja de HubSpot,
   igual que la corrida de la mañana. */

// El camino que recorre un prospecto de este origen. Se pintan siempre, aunque
// estén vacías: la pantalla tiene que mostrar hacia dónde va lo que entró, no
// solo dónde está parado. Las demás etapas aparecen solo si tienen alguien.
const CAMINO: Stage[] = ["nuevo", "contactado", "conectado", "en_conversacion", "reunion"];

function nombreCorto(n: string) {
  return (n || "").trim().split(/\s+/)[0] || n;
}

function Tarjeta({ c, remitentes, onClick }: {
  c: PipelineContact;
  remitentes: Record<string, string>;
  onClick: () => void;
}) {
  const vp = c.li_seat ? remitentes[c.li_seat] || c.li_seat : "";
  const rojo = c.hubspot_verdict === "rojo";
  return (
    <button onClick={onClick}
      className="w-full rounded-lg border border-white/10 bg-white/[0.03] p-3 text-left transition hover:border-white/25 hover:bg-white/[0.06]">
      <div className="truncate text-sm font-semibold">{c.nombre || "Sin nombre"}</div>
      <div className="truncate text-[11px] text-white/45">{c.cargo}</div>
      <div className="mt-1.5 flex items-center gap-1.5 truncate text-[11px] text-white/60">
        <Building2 size={11} className="shrink-0 text-white/30" />{c.empresa}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {vp ? (
          <span className="rounded-full bg-sky-400/15 px-2 py-0.5 text-[10px] font-semibold text-sky-300"
                title={`Lo firma ${vp}`}>
            {nombreCorto(vp)}
          </span>
        ) : (
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/50"
                title="Todavía sin perfil asignado: el carril le da uno en su próxima vuelta">
            sin VP
          </span>
        )}
        {rojo && (
          <span className="flex items-center gap-1 rounded-full bg-red-400/15 px-2 py-0.5 text-[10px] font-semibold text-red-300"
                title={c.hubspot_reason || "Ya en proceso de venta: no se le invita"}>
            <AlertTriangle size={9} /> HubSpot
          </span>
        )}
        {c.linkedin && <Linkedin size={11} className="ml-auto text-white/25" />}
      </div>
    </button>
  );
}

export default function PipelineVentas() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [ensayo, setEnsayo] = useState<any>(null);
  const [aviso, setAviso] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["pipelineVentas"], queryFn: api.pipelineVentas, refetchInterval: 20000,
  });

  const refrescar = () => qc.invalidateQueries({ queryKey: ["pipelineVentas"] });

  const cambiar = useMutation({
    mutationFn: (activo: boolean) => api.viaRapidaSwitch(activo),
    onSuccess: () => { setAviso(""); refrescar(); },
    onError: (e: any) => setAviso(e?.message || "No se pudo cambiar el interruptor"),
  });
  const correr = useMutation({
    mutationFn: (simular: boolean) => api.viaRapidaCorrer(simular),
    onSuccess: (r, simular) => {
      if (simular) { setEnsayo(r); setAviso(""); }
      else {
        setEnsayo(null);
        setAviso(r?.corrio
          ? `Salieron ${r.enviadas ?? 0} invitaciones.`
          : `No salió nada: ${r?.motivo || "sin motivo"}`);
      }
      refrescar();
    },
    onError: (e: any) => setAviso(e?.message || "Falló la corrida"),
  });

  const vr = data?.via_rapida;
  const pipe = data?.pipeline;
  const pend = vr?.pendientes;
  const remitentes = pipe?.remitentes ?? {};
  const columnas = STAGES.filter(
    (s) => CAMINO.includes(s.id) || (pipe?.counts?.[s.id] ?? 0) > 0);

  return (
    <div className="space-y-6">
      <Header title="Pipeline Ventas"
              sub="Las cuentas que los vendedores marcan en Platzi Ventas: llegan aquí, se les asigna VP y sale la solicitud de LinkedIn sola" />

      <Card>
        <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
          <div className="min-w-[260px] flex-1">
            <div className="flex items-center gap-2">
              {vr?.activo
                ? <Zap size={17} className="text-volt" />
                : <ZapOff size={17} className="text-white/35" />}
              <span className="font-semibold">
                Envío automático {vr?.activo ? "encendido" : "apagado"}
              </span>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-white/50">
              {vr?.activo
                ? <>Cada prospecto que entre por Platzi Ventas recibe solicitud de
                    LinkedIn en la vuelta siguiente del copiloto (≤15 min), sin
                    pasar por la cola de aprobación: una conexión sin nota no lleva
                    texto que revisar.</>
                : <>Los prospectos llegan y se quedan quietos. La corrida de la
                    mañana los tomará junto al resto del backlog, cuando les toque.</>}
            </p>
            {!!vr?.atienden?.length && (
              <p className="mt-2 text-xs text-white/55">
                Atienden esta base:{" "}
                {vr.atienden.map((a, i) => (
                  <span key={a.seat_id}>
                    {i > 0 && " · "}
                    <b className={a.listo ? "text-sky-300" : "text-amber"}>{a.vp}</b>
                    <span className="text-white/40">
                      {" "}({a.listo ? `${a.cupo} de cupo` : "sin sesión hoy"}
                      {!a.secuencia && ", sin secuencia"})
                    </span>
                  </span>
                ))}
                <span className="text-white/35">
                  {" "}— lo que entre nuevo va solo a ellos; si no pueden, espera.
                </span>
              </p>
            )}
            <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-white/40">
              <span className="flex items-center gap-1">
                <Clock size={11} /> Ventana {vr?.ventana || "—"}
              </span>
              <span>· Ahora: {vr?.toca_ahora || "—"}</span>
              {vr?.ultima_corrida && <span>· Última: {vr.ultima_corrida.replace("T", " ")}</span>}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button className={clsx(vr?.activo ? "btn" : "btn-primary")}
                    disabled={cambiar.isPending}
                    onClick={() => cambiar.mutate(!vr?.activo)}>
              {cambiar.isPending ? <Spinner />
                : vr?.activo ? <ZapOff size={15} /> : <Zap size={15} />}
              {vr?.activo ? "Apagar" : "Encender"}
            </button>
            <button className="btn" disabled={correr.isPending}
                    onClick={() => correr.mutate(true)}
                    title="Muestra a quién le saldría, sin enviar ni asignar nada">
              {correr.isPending ? <Spinner /> : <Eye size={15} />} Ensayo
            </button>
            <button className="btn" disabled={correr.isPending || !pend?.total}
                    onClick={() => correr.mutate(false)}
                    title="Dispara el carril ya, sin esperar la próxima vuelta">
              <PlayCircle size={15} /> Enviar ahora
            </button>
          </div>
        </div>

        {aviso && (
          <p className="mt-4 flex items-center gap-2 text-xs text-amber">
            <AlertTriangle size={13} /> {aviso}
          </p>
        )}

        {ensayo && (
          <div className="mt-4 space-y-2 rounded-lg border border-white/10 bg-black/20 p-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-white/45">
              Ensayo · no se envió ni se asignó nada
            </div>
            {Object.entries(ensayo.saldria || {}).map(([sid, lote]: [string, any]) => (
              <div key={sid} className="text-xs">
                <span className="font-semibold text-sky-300">
                  {remitentes[sid] || sid}
                </span>
                <span className="text-white/40"> · {lote.length} invitaciones</span>
                {!!lote.length && (
                  <div className="mt-1 text-white/60">
                    {lote.map((c: any) => `${c.nombre} (${c.empresa})`).join(" · ")}
                  </div>
                )}
              </div>
            ))}
            {!!ensayo.reparto?.esperando?.length && (
              <div className="border-t border-white/5 pt-2 text-xs text-amber">
                {ensayo.reparto.esperando.map((e: any, i: number) => (
                  <div key={i}>{e.nombre} ({e.empresa}) — {e.motivo}</div>
                ))}
              </div>
            )}
            {!Object.values(ensayo.saldria || {}).some((l: any) => l.length) && (
              <div className="text-xs text-white/45">
                Hoy no saldría ninguna: {ensayo.motivo}
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="De Platzi Ventas" value={pipe?.total ?? 0}
              sub="prospectos en total" icon={<Building2 size={18} />} />
        <Stat label="Esperando VP" value={pend?.sin_asiento ?? 0} accent="amber"
              sub="el carril les asigna perfil" />
        <Stat label="Listos para invitar" value={pend?.con_asiento ?? 0} accent="sky"
              sub="con VP y dentro del cupo" />
        <Stat label="Ya contactados" value={
                STAGES.filter((s) => s.id !== "nuevo")
                  .reduce((a, s) => a + (pipe?.counts?.[s.id] ?? 0), 0)
              } accent="volt" sub="salió la solicitud" icon={<Check size={18} />} />
      </div>

      {isLoading ? (
        <Empty>Cargando…</Empty>
      ) : !pipe?.total ? (
        <Empty>
          Todavía no ha llegado nadie de Platzi Ventas. Cuando un vendedor marque
          «trabajar con esta empresa», sus decisores aparecen aquí.
        </Empty>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-2">
          {columnas.map((s) => (
            <div key={s.id} className="w-[250px] shrink-0">
              <div className={clsx("mb-3 border-t-2 pt-2", s.color)}>
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-semibold">{s.label}</span>
                  <span className="text-xs text-white/40">{pipe.counts[s.id]}</span>
                </div>
              </div>
              <div className="space-y-2">
                {(pipe.stages[s.id as Stage] || []).map((c) => (
                  <Tarjeta key={c.id} c={c} remitentes={remitentes}
                           onClick={() => setSelected(c.id)} />
                ))}
                {!pipe.counts[s.id] && (
                  <div className="rounded-lg border border-dashed border-white/[0.07] p-3 text-[11px] text-white/25">
                    nadie todavía
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {selected !== null && (
        <ContactTimeline contactId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
