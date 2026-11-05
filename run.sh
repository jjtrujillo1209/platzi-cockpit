#!/usr/bin/env bash
# Platzi Cockpit — todo corre como agentes de launchd; esto solo los maneja.
#
#   bash run.sh             carga lo que falte y muestra el estado
#   bash run.sh reiniciar   reinicia backend, frontend y ngrok
#   bash run.sh estado      solo el estado
#
# Los Chrome de los VP no se tocan aquí: el backend los levanta en segundo
# plano (headless) cuando hace falta enviar.
set -euo pipefail

U="gui/$(id -u)"
LA="$HOME/Library/LaunchAgents"
AGENTES=(com.platzi.cockpit.backend com.platzi.cockpit.frontend com.platzi.cockpit.ngrok)
API_PORT="${PLATZI_API_PORT:-8532}"
WEB_PORT="${PLATZI_WEB_PORT:-5181}"
NGROK_DOMAIN="${PLATZI_NGROK_DOMAIN:-fiddling-shown-anaerobic.ngrok-free.dev}"

accion="${1:-arrancar}"

for a in "${AGENTES[@]}"; do
  if ! launchctl print "$U/$a" >/dev/null 2>&1; then
    echo "▶ cargando $a"
    launchctl bootstrap "$U" "$LA/$a.plist"
  elif [ "$accion" = "reiniciar" ]; then
    echo "▶ reiniciando $a"
    launchctl kickstart -k "$U/$a"
  fi
done

[ "$accion" = "estado" ] || sleep 3

estado() {
  local pid
  pid="$(launchctl list | awk -v l="$1" '$3==l {print $1}')"
  if [ -n "$pid" ] && [ "$pid" != "-" ]; then echo "corriendo (pid $pid)"; else echo "CAÍDO"; fi
}

cat <<EOF

──────────────────────────────────────────────────────────────
  Platzi Cockpit
    UI        →  http://127.0.0.1:$WEB_PORT          $(estado com.platzi.cockpit.frontend)
    API docs  →  http://127.0.0.1:$API_PORT/docs     $(estado com.platzi.cockpit.backend)
    Webhook   →  https://$NGROK_DOMAIN/api/webhooks/sendgrid
                 $(estado com.platzi.cockpit.ngrok)

  Logs: ~/Library/Logs/platzi/<agente>.log
──────────────────────────────────────────────────────────────
EOF
