"""Progress protocol for the Astra desktop runner."""
import json
import os


STAGES = {
    "prepare": (1, 7, "Preparando vídeos"),
    "extract": (8, 15, "Extrayendo imagen y audio"),
    "features": (15, 24, "Analizando fotogramas"),
    "palette": (24, 33, "Calculando paletas"),
    "scroll": (33, 40, "Analizando desplazamientos"),
    "quant": (40, 64, "Cuantizando imagen"),
    "forecast": (64, 79, "Planificando lectura y memoria"),
    "decide": (79, 92, "Codificando fotogramas"),
    "finalize": (92, 94, "Verificando el flujo"),
    "encoded": (94, 94, "Flujo verificado"),
    "pack_part": (93, 93, "Comprobando y guardando parte"),
    "build": (95, 96, "Creando ISO y CUE"),
    "disc": (97, 97, "Imagen de disco verificada"),
    "preview": (97, 98, "Reconstruyendo vista MP4"),
    "ready": (99, 99, "Guardando los archivos"),
}


def emit(stage, done=None, total=None, *, part=None, parts=None):
    path = os.environ.get("ASTRA_PROGRESS_FILE")
    if not path:
        return
    start, end, title = STAGES[stage]
    if done is not None and total:
        # Keep progress I/O proportional to visible changes.
        step = max(1, total // 100)
        if done not in (0, total) and done % step:
            return
        start += (end - start) * min(done, total) / total
        title += f" · {done:,}/{total:,}"
    if part is None:
        part = int(os.environ.get("ASTRA_PROGRESS_PART", "0"))
    if parts is None:
        parts = int(os.environ.get("ASTRA_PROGRESS_PARTS", "1"))
    event = {"percent": int(start), "stage": title}
    if part and parts > 1:
        event.update(part=part, parts=parts, percent=min(94, int(start)),
                     stage=f"Parte {part}/{parts} · {title}")
    payload = (json.dumps(event) + "\n").encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)

