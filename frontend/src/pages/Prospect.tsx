import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, BulkCompany, BulkJob, OrgChartResult, OrgProfile } from "../lib/api";
import { useBulkJob } from "../state/bulkJobStore";
import { Header } from "./Dashboard";
import { Card, Empty, Spinner, Badge, Progress } from "../components/ui";
import { Field } from "./Connections";
import {
  Search, Linkedin, Mail, Phone, Building2, GraduationCap, Users, TrendingUp, Cpu, Briefcase,
  AlertTriangle, Copy, Check, Target, UserPlus, Upload, Play, Square, Layers, User,
} from "lucide-react";

// Personas clave del ICP oficial de Platzi Business (ENT · >300 empleados).
const AREA_ICON: Record<string, JSX.Element> = {
  ld: <GraduationCap size={13} />,        // L&D / Capacitación
  rrhh: <Users size={13} />,              // HR / People / Talento
  tecnologia: <Cpu size={13} />,          // Tecnología / Innovación (CTO/CIO)
  ventas: <TrendingUp size={13} />,       // Ventas / Sales Enablement
  direccion: <Briefcase size={13} />,     // CEO / C-Level / Finanzas
};

export default function Prospect() {
  const [tab, setTab] = useState<"uno" | "masivo">("uno");
  return (
    <div className="space-y-6">
      <Header
        title="Prospección"
        sub="Empresa (>300 empl.) → organigrama → 2 decisores (L&D · People/Talento · Tecnología/CTO · Ventas) → base de outreach"
      />
      <div className="flex gap-2">
        <TabBtn active={tab === "uno"} onClick={() => setTab("uno")}>
          <User size={14} /> Individual
        </TabBtn>
        <TabBtn active={tab === "masivo"} onClick={() => setTab("masivo")}>
          <Layers size={14} /> Masivo
        </TabBtn>
      </div>
      {tab === "uno" ? <SingleProspect /> : <BulkProspect />}
    </div>
  );
}

function TabBtn({ active, onClick, children }: {
  active: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition
        ${active ? "bg-platzi/15 text-platzi" : "text-white/45 hover:text-white/75"}`}
    >
      {children}
    </button>
  );
}

// ============================ individual ============================
function SingleProspect() {
  const [company, setCompany] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<OrgChartResult | null>(null);

  const run = async () => {
    if (!company.trim() && !domain.trim()) return;
    setBusy(true);
    setRes(null);
    try {
      setRes(await api.prospectOrgchart({ company: company.trim(), domain: domain.trim() }));
    } catch (e: any) {
      setRes({ ok: false, error: e?.message || "Error de red" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field label="Empresa">
            <input
              className="input"
              placeholder="Ej. Bancolombia, Rappi, Bimbo…"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </Field>
          <Field label="Dominio (opcional, más preciso)">
            <input
              className="input font-mono text-sm"
              placeholder="bancolombia.com"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </Field>
          <button
            className="btn-primary h-[42px] justify-center"
            onClick={run}
            disabled={busy || (!company.trim() && !domain.trim())}
          >
            {busy ? <Spinner /> : <Search size={16} />} Buscar
          </button>
        </div>
        <p className="mt-3 flex items-center gap-1.5 text-[11px] text-white/35">
          <Target size={12} /> Perfil validado (LinkedIn, correo y teléfono cuando Apollo los tenga). Máximo 2 perfiles por empresa.
        </p>
      </Card>

      {busy && <Empty>Buscando organigrama en Apollo…</Empty>}

      {res && !busy && (
        res.ok ? <Results res={res} /> : (
          <Card>
            <div className="flex items-center gap-2 text-sm text-platzi">
              <AlertTriangle size={16} /> {res.error}
            </div>
          </Card>
        )
      )}
    </div>
  );
}

function Results({ res }: { res: OrgChartResult }) {
  const profiles = res.profiles || [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm text-white/55">
        <Building2 size={15} className="text-white/40" />
        <span className="font-semibold text-white/80">{res.company}</span>
        {res.domain && <span className="font-mono text-xs text-white/35">{res.domain}</span>}
        <Badge>{res.total_candidates ?? 0} candidatos en organigrama</Badge>
      </div>

      {profiles.length === 0 ? (
        <Empty>{res.note || "Sin perfiles con LinkedIn válido para los cargos objetivo."}</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {profiles.map((p, i) => (
            <ProfileCard key={i} p={p} empresa={res.company || ""} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProfileCard({ p, empresa }: { p: OrgProfile; empresa: string }) {
  const qc = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [addState, setAddState] = useState<"idle" | "busy" | "added" | "dup" | "error">("idle");
  const [addDetail, setAddDetail] = useState("");

  const copy = () => {
    navigator.clipboard.writeText(p.linkedin);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const addToBase = async () => {
    setAddState("busy");
    try {
      const r = await api.addContacts([{
        nombre: p.nombre, cargo: p.cargo, linkedin: p.linkedin,
        email: p.email, celular: p.celular, validacion: p.validacion,
        empresa, validacion_empresa: empresa,
      }]);
      if (!r.ok) {
        setAddState("error");
        setAddDetail(r.error || "error");
      } else if (r.added > 0) {
        setAddState("added");
        qc.invalidateQueries({ queryKey: ["contacts"] });
      } else {
        setAddState("dup");
        setAddDetail(r.skipped[0]?.reason || "ya estaba en la base");
      }
    } catch (e: any) {
      setAddState("error");
      setAddDetail(e?.message || "error de red");
    }
  };

  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-base font-bold">{p.nombre}</h3>
          <p className="mt-0.5 text-sm text-white/55">{p.cargo}</p>
        </div>
        <span className="chip shrink-0 bg-platzi/10 text-platzi">
          {AREA_ICON[p.area] || <Building2 size={13} />} {p.area}
        </span>
      </div>
      {(p.email || p.celular) && (
        <div className="mt-2 space-y-1 text-xs text-white/50">
          {p.email && <div className="flex items-center gap-1.5 truncate"><Mail size={12} /> {p.email}</div>}
          {p.celular && <div className="flex items-center gap-1.5"><Phone size={12} /> {p.celular}</div>}
        </div>
      )}
      <div className="mt-4 flex items-center gap-2">
        <a
          href={p.linkedin}
          target="_blank"
          rel="noreferrer"
          className="btn-primary flex-1 justify-center"
        >
          <Linkedin size={16} /> Ver perfil
        </a>
        <button
          className="btn-ghost"
          onClick={addToBase}
          disabled={addState === "busy" || addState === "added"}
          title={addState === "dup" ? addDetail : "Agregar a la base de outreach"}
        >
          {addState === "busy" ? <Spinner />
            : addState === "added" ? <Check size={16} className="text-volt" />
            : <UserPlus size={16} />}
        </button>
        <button className="btn-ghost" onClick={copy} title="Copiar URL">
          {copied ? <Check size={16} className="text-volt" /> : <Copy size={16} />}
        </button>
      </div>
      {addState === "added" && (
        <p className="mt-2 text-[11px] text-volt">✓ Agregado a la base de outreach</p>
      )}
      {addState === "dup" && (
        <p className="mt-2 text-[11px] text-white/40">Ya estaba en la base · {addDetail}</p>
      )}
      {addState === "error" && (
        <p className="mt-2 text-[11px] text-platzi">{addDetail}</p>
      )}
    </Card>
  );
}

// ============================ masivo ============================
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else inQ = false;
      } else cell += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === "," || ch === ";") { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some((c) => c.trim())) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell);
  if (row.some((c) => c.trim())) rows.push(row);
  return rows;
}

function rowsToCompanies(rows: string[][]): BulkCompany[] {
  if (!rows.length) return [];
  const header = rows[0].map((h) => h.replace(/^﻿/, "").trim().toLowerCase());
  const find = (names: string[]) => header.findIndex((h) => names.some((n) => h.includes(n)));
  const iEmp = find(["empresa", "company", "razon", "razón"]);
  if (iEmp >= 0) {
    const iNit = find(["nit"]);
    const iSec = find(["sector"]);
    const iCiu = find(["ciudad", "city"]);
    const iDom = find(["dominio", "domain", "web"]);
    return rows.slice(1).map((r) => ({
      empresa: (r[iEmp] || "").trim(),
      nit: iNit >= 0 ? (r[iNit] || "").trim() : "",
      sector: iSec >= 0 ? (r[iSec] || "").trim() : "",
      ciudad: iCiu >= 0 ? (r[iCiu] || "").trim() : "",
      domain: iDom >= 0 ? (r[iDom] || "").trim() : "",
    })).filter((c) => c.empresa || c.domain);
  }
  // sin encabezado reconocible: primera columna = empresa
  return rows.map((r) => ({ empresa: (r[0] || "").trim() })).filter((c) => c.empresa);
}

function linesToCompanies(text: string): BulkCompany[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    // una línea que parece dominio (sin espacios y con punto) entra como domain
    if (!l.includes(" ") && l.includes(".")) return { domain: l };
    return { empresa: l };
  });
}

const BULK_STATUS: Record<string, { label: string; cls: string }> = {
  agregado: { label: "✓ agregado", cls: "text-volt" },
  duplicado: { label: "duplicado", cls: "text-white/40" },
  encontrado: { label: "encontrado", cls: "text-white/70" },
  sin_perfiles: { label: "sin perfiles", cls: "text-white/35" },
  ya_cliente: { label: "ya cliente", cls: "text-amber-400" },
  error: { label: "error", cls: "text-danger" },
};

function BulkProspect() {
  const { job, launching, launchErr, launch: launchJob, stop } = useBulkJob();
  const [text, setText] = useState("");
  const [companies, setCompanies] = useState<BulkCompany[]>([]);
  const [fileName, setFileName] = useState("");
  const [autoAdd, setAutoAdd] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);

  const queue = companies.length ? companies : linesToCompanies(text);
  const running = job?.status === "running";

  const onFile = async (f: File) => {
    const parsed = rowsToCompanies(parseCsv(await f.text()));
    setCompanies(parsed);
    setFileName(`${f.name} · ${parsed.length} empresas`);
    setText("");
  };

  const launch = () => launchJob(queue, autoAdd);

  const clearQueue = () => {
    setCompanies([]);
    setFileName("");
    setText("");
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="space-y-6">
      <Card>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Field label="Pega empresas (una por línea)">
            <textarea
              className="input min-h-[120px] font-mono text-sm"
              placeholder={"Bancolombia\nRappi\nBimbo\nnubank.com"}
              value={text}
              disabled={!!companies.length}
              onChange={(e) => setText(e.target.value)}
            />
          </Field>
          <div className="space-y-3">
            <Field label="…o sube un CSV (columnas: empresa · nit · sector · ciudad)">
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
              />
              <button className="btn-ghost w-full justify-center" onClick={() => fileRef.current?.click()}>
                <Upload size={15} /> {fileName || "Elegir archivo CSV"}
              </button>
            </Field>
            <label className="flex items-center gap-2 text-sm text-white/70">
              <input type="checkbox" checked={autoAdd} onChange={(e) => setAutoAdd(e.target.checked)} />
              Agregar automáticamente a la base de outreach (con dedup)
            </label>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button className="btn-primary" onClick={launch} disabled={!queue.length || running || launching}>
            {running || launching ? <Spinner /> : <Play size={15} />} Prospectar {queue.length || ""} empresas
          </button>
          {running && (
            <button className="btn-ghost" onClick={stop}>
              <Square size={14} /> Detener
            </button>
          )}
          {(companies.length > 0 || text) && !running && (
            <button className="btn-ghost text-white/45" onClick={clearQueue}>Limpiar</button>
          )}
          <span className="text-xs text-white/35">
            ~2 perfiles por empresa · LinkedIn + correo + teléfono validados · dedup contra la base actual
          </span>
        </div>
        {running && (
          <p className="mt-2 text-[11px] text-white/30">
            El progreso sigue corriendo en el servidor aunque cambies de pestaña — vuelve aquí cuando quieras para verlo.
          </p>
        )}
        {launchErr && (
          <p className="mt-3 flex items-center gap-1.5 text-sm text-platzi">
            <AlertTriangle size={14} /> {launchErr}
          </p>
        )}
      </Card>

      {job && <BulkResults job={job} />}
    </div>
  );
}

function BulkResults({ job }: { job: BulkJob }) {
  const isHalted = job.status === "sin_creditos";
  const statusLabel = job.status === "running" ? "en curso"
    : job.status === "sin_creditos" ? "detenido: sin créditos Apollo"
    : job.status;
  return (
    <div className="space-y-4">
      {isHalted && (
        <Card className="border-platzi/30 bg-platzi/[0.06]">
          <p className="flex items-center gap-2 text-sm text-platzi">
            <AlertTriangle size={15} />
            Apollo respondió "sin créditos" — el job se detuvo automáticamente para no seguir gastando llamadas en empresas condenadas a fallar. Revisa tu saldo en Apollo antes de relanzar.
          </p>
        </Card>
      )}
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={isHalted ? "bg-platzi/10 text-platzi" : ""}>{statusLabel}</Badge>
          <Badge>{job.done}/{job.total} empresas</Badge>
          <Badge>{job.profiles_found} perfiles</Badge>
          <Badge className="bg-volt/10 text-volt">{job.added} agregados</Badge>
          {job.skipped_dup > 0 && <Badge>{job.skipped_dup} duplicados</Badge>}
          {job.ya_cliente > 0 && <Badge className="text-amber-400">{job.ya_cliente} ya clientes</Badge>}
        </div>
        <div className="mt-3">
          <Progress value={job.done} total={job.total} />
        </div>
      </Card>

      {job.results.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/5 text-left text-[11px] uppercase tracking-wide text-white/35">
                <th className="px-4 py-2.5">Empresa</th>
                <th className="px-4 py-2.5">Estado</th>
                <th className="px-4 py-2.5">Perfiles</th>
              </tr>
            </thead>
            <tbody>
              {[...job.results].reverse().map((r, i) => {
                const st = BULK_STATUS[r.status] || { label: r.status, cls: "text-white/50" };
                return (
                  <tr key={i} className="border-b border-white/5 last:border-0">
                    <td className="max-w-[220px] truncate px-4 py-2 font-medium text-white/80">{r.empresa}</td>
                    <td className={`px-4 py-2 whitespace-nowrap ${st.cls}`}>
                      {st.label}
                      {r.detail && <span className="ml-1.5 text-[11px] text-white/30">{r.detail}</span>}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex flex-wrap gap-2">
                        {r.profiles.map((p, k) => {
                          const tip = [p.cargo, p.email, p.celular].filter(Boolean).join(" · ");
                          const cls = "chip bg-white/5 text-white/70 hover:text-white";
                          const label = (
                            <>
                              {AREA_ICON[p.area] || <Linkedin size={12} />} {p.nombre}
                              {p.email && <Mail size={11} className="text-white/35" />}
                              {p.celular && <Phone size={11} className="text-white/35" />}
                            </>
                          );
                          return p.linkedin ? (
                            <a key={k} href={p.linkedin} target="_blank" rel="noreferrer"
                               className={cls} title={tip}>{label}</a>
                          ) : (
                            <span key={k} className={cls} title={tip}>{label}</span>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
