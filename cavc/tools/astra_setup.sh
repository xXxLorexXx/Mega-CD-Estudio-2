#!/bin/bash
set -euo pipefail
runtime=~/.local/share/mega-cd-estudio/cavc81-venv
python3 -m venv "$runtime"
"$runtime/bin/python" -m pip install 'numpy==2.3.5' 'pillow==12.1.1' 'numba==0.68.1'
if [[ -e /dev/dxg ]] && [[ -x /usr/lib/wsl/lib/nvidia-smi ]] &&
    /usr/lib/wsl/lib/nvidia-smi -L >/dev/null 2>&1; then
    if ! "$runtime/bin/python" -m pip install 'cupy-cuda12x[ctk]==14.2.0'; then
        echo "CUDA no se pudo instalar. CAVC seguirá disponible por CPU." >&2
    fi
fi
"$runtime/bin/python" -c 'import numpy, PIL; print("Runtime CAVC OK")'
