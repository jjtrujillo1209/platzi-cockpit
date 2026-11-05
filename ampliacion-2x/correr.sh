#!/bin/bash
# Orquesta las etapas en orden de costo creciente hasta llegar a la meta.
# La base exige los 3 datos: correo corporativo + teléfono + LinkedIn.
cd "$(dirname "$0")"
PY=/opt/homebrew/bin/python3.12
OBJ=${1:-660}
{
  echo "=== $(date '+%H:%M:%S') lote A · correos+teléfonos de las 316 ya calificadas"
  $PY revelar.py --etapa emails --objetivo "$OBJ"
  echo "=== $(date '+%H:%M:%S') firmografía · empleados reales de C y E"
  $PY revelar.py --etapa firmografia
  echo "=== $(date '+%H:%M:%S') DMs · B, C y E"
  $PY revelar.py --etapa dms --limite 900   # de sobra para la meta; evita barrer las 1.500
  echo "=== $(date '+%H:%M:%S') correos+teléfonos · B, C y E hasta la meta"
  $PY revelar.py --etapa emails --objetivo "$OBJ"
  echo "=== $(date '+%H:%M:%S') rescate final de teléfonos"
  $PY revelar.py --etapa telefonos
  echo "=== $(date '+%H:%M:%S') validación de entregabilidad"
  $PY revelar.py --etapa validar
  echo "=== $(date '+%H:%M:%S') FIN"
} 2>&1 | tee -a data/corrida.log
