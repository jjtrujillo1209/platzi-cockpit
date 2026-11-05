import { Stage } from "./api";

// Única definición de las etapas del pipeline — la comparten Pipeline.tsx
// (columnas del kanban + selector en la tarjeta) y ContactTimeline.tsx
// (selector en el pop-up de detalle), para que no diverjan.
export const STAGES: { id: Stage; label: string; color: string }[] = [
  { id: "nuevo", label: "Nuevo", color: "border-white/20" },
  { id: "contactado", label: "Contactado", color: "border-sky-400/50" },
  { id: "conectado", label: "Conectado", color: "border-volt/50" },
  { id: "no_responde", label: "No responde", color: "border-white/15" },
  { id: "en_conversacion", label: "En conversación", color: "border-amber/60" },
  { id: "reunion", label: "Reunión", color: "border-platzi/60" },
  { id: "cliente", label: "Cliente", color: "border-volt" },
  { id: "descartado", label: "Descartado", color: "border-white/10" },
];
