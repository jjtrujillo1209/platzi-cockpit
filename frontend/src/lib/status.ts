// Mapeo de estados (alineado con bulk_connect/app.py STATUS_LABEL).
export const STATUS: Record<string, { label: string; color: string; dot: string }> = {
  sent: { label: "Enviada", color: "text-volt", dot: "bg-volt" },
  message_sent: { label: "Mensaje enviado", color: "text-volt", dot: "bg-volt" },
  already: { label: "Ya conectados", color: "text-indigo-300", dot: "bg-indigo-400" },
  pending_invite: { label: "Invitación pendiente", color: "text-amber", dot: "bg-amber" },
  not_connected: { label: "Sin conexión", color: "text-amber", dot: "bg-amber" },
  not_found: { label: "Botón no hallado", color: "text-orange-300", dot: "bg-orange-400" },
  already_messaged: { label: "Ya con mensaje", color: "text-indigo-300", dot: "bg-indigo-400" },
  rate_limited: { label: "Bloqueo / login", color: "text-danger", dot: "bg-danger" },
  error: { label: "Error", color: "text-danger", dot: "bg-danger" },
  skipped: { label: "Saltada", color: "text-white/40", dot: "bg-white/30" },
  dry_run: { label: "Prueba (dry-run)", color: "text-sky-300", dot: "bg-sky-400" },
};

export function statusOf(s: string) {
  return STATUS[s] || { label: s || "—", color: "text-white/50", dot: "bg-white/30" };
}

export const CHANNEL: Record<string, { label: string; color: string }> = {
  linkedin: { label: "Conexión LinkedIn", color: "text-sky-300" },
  followup: { label: "Follow-up", color: "text-volt" },
  email: { label: "Email", color: "text-platzi" },
};
