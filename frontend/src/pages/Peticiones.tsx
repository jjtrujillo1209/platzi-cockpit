import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Peticion } from "../lib/api";
import { Header } from "./Dashboard";
import { Card, Empty, Spinner, Stat } from "../components/ui";
import {
  Inbox, Send, RotateCcw, X, Building2, Mail, Phone, Linkedin, AlertTriangle,
} from "lucide-react";

/* Pides una empresa y la máquina la trabaja en su siguiente vuelta (60 s). No
   hay botón de «resolver» a propósito: si resolver fuera manual, la bandeja
   volvería a depender de que alguien esté mirando, que es justo lo que evita. */

const ESTADO: Record<Peticion["estado"], { et: string; clase: string; que: string }> = {
  pendiente:     { et: "Pendiente",      clase: "bg-white/10 text-white/70",        que: "En cola. La máquina la toma en su próxima vuelta." },
  en_proceso:    { et: "En proceso",     clase: "bg-amber-400/15 text-amber-300",   que: "La máquina la está trabajando ahora." },
  resuelta:      { et: "Resuelta",       clase: "bg-volt/15 text-volt",             que: "Decisores encontrados y dados de alta en la base." },
  frenada:       { et: "En gestión",     clase: "bg-red-400/15 text-red-300",       que: "Ya la trabaja alguien más: no ir en frío." },
  sin_contactos: { et: "Sin contactos",  clase: "bg-white/10 text-white/50",        que: "Apollo no tiene decisores del ICP en esa empresa." },
  error:         { et: "Error",          clase: "bg-red-400/15 text-red-300",       que: "Falló el intento. Se puede reintentar." },
  cancelada:     { et: "Cancelada",      clase: "bg-white/5 text-white/40",         que: "La canceló quien la pidió." },
};

function Chip({ estado }: { estado: Peticion["estado"] }) {
  const e = ESTADO[estado] ?? ESTADO.pendiente;
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold ${e.clase}`} title={e.que}>
      {e.et}
    </span>
  );
}

function Decisores({ p }: { p: Peticion }) {
  if (!p.decisores?.length) return null;
  return (
    <ul className="mt-3 space-y-2 border-t border-white/5 pt-3">
      {p.decisores.map((d, i) => (
        <li key={i} className="text-sm">
          <div className="font-semibold">{d.nombre || "Sin nombre"}</div>
          <div className="text-xs text-white/50">{d.cargo}</div>
          <div className="mt-0.5 flex flex-wrap gap-3 font-mono text-[11px] text-white/60">
            {d.email && <span className="flex items-center gap-1"><Mail size={11} />{d.email}</span>}
            {d.celular && <span className="flex items-center gap-1"><Phone size={11} />{d.celular}</span>}
            {d.linkedin && (
              <a href={d.linkedin} target="_blank" rel="noopener"
                 className="flex items-center gap-1 hover:text-white">
                <Linkedin size={11} />LinkedIn
              </a>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function Peticiones() {
  const qc = useQueryClient();
  const [consulta, setConsulta] = useState("");
  const [aviso, setAviso] = useState("");
  // Refresco corto: la máquina resuelve en su vuelta de 60 s y la bandeja tiene
  // que moverse sola, sin que nadie recargue.
  const { data, isLoading } = useQuery({
    queryKey: ["peticiones"], queryFn: () => api.peticiones(), refetchInterval: 15000,
  });

  const pedir = useMutation({
    mutationFn: (c: string) => api.crearPeticion(c),
    onSuccess: (r) => {
      setConsulta("");
      setAviso(r.repetida ? `«${r.peticion.consulta}» ya estaba pedida y sigue abierta.` : "");
      qc.invalidateQueries({ queryKey: ["peticiones"] });
    },
    onError: (e: any) => setAviso(e?.message || "No se pudo crear la petición"),
  });
  const cancelar = useMutation({
    mutationFn: (id: number) => api.cancelarPeticion(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["peticiones"] }),
  });
  const reintentar = useMutation({
    mutationFn: (id: number) => api.reintentarPeticion(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["peticiones"] }),
  });

  const filas = data?.peticiones ?? [];
  const r = data?.resumen ?? {};
  const abiertas = (r.pendiente ?? 0) + (r.en_proceso ?? 0);

  return (
    <div className="space-y-6">
      <Header title="Peticiones"
              sub="Pide una empresa y la máquina la trabaja sola: 2 decisores del ICP, dados de alta en la base" />

      <Card>
        <form className="flex flex-wrap gap-3"
              onSubmit={(e) => { e.preventDefault(); if (consulta.trim()) pedir.mutate(consulta.trim()); }}>
          <input
            className="input flex-1 min-w-[260px]"
            placeholder="Nombre o dominio — Cementos Argos, sura.com…"
            value={consulta} onChange={(e) => { setConsulta(e.target.value); setAviso(""); }}
          />
          <button className="btn-primary" type="submit" disabled={pedir.isPending || !consulta.trim()}>
            {pedir.isPending ? <Spinner /> : <Send size={15} />} Pedir
          </button>
        </form>
        {aviso && (
          <p className="mt-3 flex items-center gap-2 text-xs text-amber-300">
            <AlertTriangle size={13} /> {aviso}
          </p>
        )}
        <p className="mt-3 text-xs text-white/40">
          La máquina revisa la bandeja cada 60 s. Cuesta hasta 3 créditos de Apollo por
          empresa; las que ya trabaja otro no cuestan nada.
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Abiertas" value={abiertas} />
        <Stat label="Resueltas" value={r.resuelta ?? 0} />
        <Stat label="En gestión" value={r.frenada ?? 0} />
        <Stat label="Sin contactos" value={r.sin_contactos ?? 0} />
      </div>

      {isLoading ? (
        <Empty>Cargando peticiones…</Empty>
      ) : !filas.length ? (
        <Empty>Nadie ha pedido nada todavía. Escribe una empresa arriba.</Empty>
      ) : (
        <div className="space-y-3">
          {filas.map((p) => (
            <Card key={p.id}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="flex items-center gap-2 font-semibold">
                  <Building2 size={15} className="text-white/40" />
                  {p.empresa || p.consulta}
                </span>
                <Chip estado={p.estado} />
                {p.dominio && <span className="font-mono text-xs text-white/40">{p.dominio}</span>}
                <span className="ml-auto flex items-center gap-2 text-xs text-white/40">
                  {p.pidio && <>pidió {p.pidio}</>}
                  <span>· {(p.updated_at || p.created_at || "").replace("T", " ").slice(5, 16)}</span>
                  {(p.estado === "pendiente" || p.estado === "en_proceso") && (
                    <button className="btn-ghost !px-2 !py-1" title="Cancelar"
                            onClick={() => cancelar.mutate(p.id)}><X size={13} /></button>
                  )}
                  {(p.estado === "error" || p.estado === "cancelada") && (
                    <button className="btn-ghost !px-2 !py-1" title="Reintentar"
                            onClick={() => reintentar.mutate(p.id)}><RotateCcw size={13} /></button>
                  )}
                </span>
              </div>

              {p.nota && <p className="mt-1.5 text-xs text-white/55">{p.nota}</p>}
              {p.contactos_nuevos > 0 && (
                <p className="mt-1 flex items-center gap-1.5 text-xs text-volt">
                  <Inbox size={12} /> {p.contactos_nuevos} contacto(s) nuevos en la base
                </p>
              )}
              <Decisores p={p} />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
