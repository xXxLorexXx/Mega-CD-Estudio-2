#!/bin/bash
set -euo pipefail
runtime=~/.local/share/mega-cd-estudio/cavc81-venv/bin/python
if [[ ! -x "$runtime" ]]; then
    echo "Falta el entorno de CAVC. Ejecuta Preparar_CAVC.bat." >&2
    exit 2
fi
exec 2>&1
exec "$runtime" -u tools/astra_encode.py "$@"

