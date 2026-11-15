# Mega CD Estudio 13 — Informe Técnico de Arquitectura, Mejoras y Aceleración

Versión 13 de Mega CD Estudio, publicada como Mega CD Estudio 2.

---

## 1. Novedades y correcciones de Mega CD Estudio 13 respecto a la versión 12 y Astra 8.1.1

### 1.1. Gestión inteligente de caché temporal y limpieza desde la interfaz (`megacd_cavc.py`, `megacd_cavc_ui.py`)
- **Podado selectivo al cancelar (`_prune_incomplete_job`)**:
  - Si se cancela un trabajo de 1 sola parte (`parts == 1`) o un trabajo multiparte que aún no ha completado ninguna parte (`0` archivos `multipart-complete.json`), se eliminan automáticamente al 100 % sus carpetas `cavc/tmp/astra_<hash>*`, `cavc/out/astra_<hash>*` y registros huérfanos en `cavc/logs`.
  - Si se cancela un trabajo multiparte con una o más partes ya terminadas y verificadas (`multipart-complete.json`), se conservan únicamente las carpetas `part-XXXX` completadas y el `plan.json` para reanudación instantánea, purgando las partes incompletas, archivos `handoff-*` y `.pending.iso`.
- **Limpieza garantizada del espacio de trabajo en WSL (`astra_encode.py`)**:
  - Se registra `atexit.register(workspace.cleanup)` al crear el directorio temporal en `~/.local/share/mega-cd-estudio/cavc81-workspace` para garantizar su eliminación incluso ante salidas anticipadas (`SystemExit`).
- **Botón «Limpiar caché temporal» en la pestaña CAVC / LORE**:
  - Añadido junto a las opciones de exportación en `megacd_cavc_ui.py`. Calcula el espacio ocupado por `cavc/tmp`, `cavc/out` y `cavc/logs` y los vacía en segundo plano (incluyendo restos en WSL) sin bloquear la interfaz.

### 1.2. Columna «Partes» por vídeo en la tabla de biblioteca (`megacd_cavc_ui.py`, `megacd_studio_ui.py`)
- En modo **CAVC** o **LORE**, la cabecera de la columna cambia dinámicamente a **`Partes`** y cada vídeo añadido muestra su número de partes estimado (`1 parte`, `2 partes`, etc.) según su duración, FPS y corte seleccionado, además del resumen global en la barra inferior.
- Al volver a **Cinepak**, la cabecera restaura automáticamente **`Capítulos`** y el cálculo de capítulos por elemento.

### 1.3. Ajuste de reserva por parte en discos multiparte (`cavc/tools/astra_capacity.py`)
- Se ha ajustado `PART_RESERVE` de `1 MiB` (`1024 ** 2`) a `512 KiB` (`512 * 1024`), acorde con la sobrecarga real en ISO 9660 (`PARTIP.BIN` + `PARTSP.BIN` + `HEADER.DAT` + descriptores). Esto evita falsos bloqueos de capacidad en películas largas (72–76 minutos) divididas en numerosas partes.

### 1.4. Exportación de vista previa MP4 en discos multiparte (`cavc/tools/astra_encode.py`, `cavc/tools/multipart.py`)
- La opción **«Exportar vista previa MP4»** funciona ahora tanto para discos de **1 parte** como para discos **multiparte (`parts > 1`)**.
- En trabajos multiparte, `multipart.py` renderiza `preview.mp4` para cada parte antes de podar los PNG intermedios (`ASTRA_PRUNE_PART_PNGS=1`), y `astra_encode.py` concatena sin recodificar (`ffmpeg -f concat -c copy -movflags +faststart`) todas las partes en `Vista_previa_<CODEC>.mp4`.

---

## 2. Aceleración de procesamiento en CAVC y LORE (`fast_kernels.py`, `sim.py`, `output_dither.py`, `quantize_global4_tiles.py`, `astra_extract.py`)

Se han implementado kernels compilados con **Numba (`@njit(cache=True)` y `@njit(nogil=True, cache=True)`)** matemáticamente equivalentes al 100 % con la referencia NumPy:

1. **Fase `Decide` (`sim.py` + `fast_kernels.py`)**:
   - `cell_f3_score_lut`: sustituye las conversiones `float64` y productos matriciales por celda en `acceptable()` por evaluación directa sobre las tablas precalculadas `_F3_DY_LUT` y `_F3_DC_LUT`.
   - `score_smooth_fallback_numba` y `score_match_motion_numba`: compilan en código máquina la puntuación perceptual de subceldas `4×4`, textura y costuras de bordes entre bloques vecinos para `choose_smooth_fallback` y `match_motion` en LORE y `detail_motion`.
   - `tile_edge_error_numba`: calcula el desacuerdo de bordes `8×8` recorriendo únicamente las fronteras compartidas sin reservar tensores temporales `(28, 40, 8, 8, 3)` en `int16` ni acumuladores `float64` 2D.
2. **Fase `Palette` y cuantización RGB333 (`sim.py`, `output_dither.py`, `quantize_global4_tiles.py`, `fast_kernels.py`)**:
   - `local_luma_range_numba`, `expand_edge_range_numba`, `quantize_bayer_threshold_numba` y `quantize_attenuated_bayer_numba` (`@njit(nogil=True, cache=True)`): eliminan las vistas acolchadas (`np.pad`) de 9 vecinos y los tensores temporales `float32` por fotograma, usando tablas LUT de 256 entradas y liberando el GIL en el `ThreadPoolExecutor` de `FrameFeatureCache`.
   - `frame_metrics_numba`, `edge_strengths_numba` y `flatten_low_detail_numba` (`@njit(nogil=True, cache=True)`): calculan `dark`, `uniform`, `fade_probe`, gradientes locales y aplanado de celdas de bajo detalle en pasadas directas sobre `uint8` sin reservar copias `float64` de fotograma completo.
   - Uso unificado de `FrameFeatureCache` tanto en `mosaic-gm` como en `stl4` para evitar decodificar dos veces los PNG en CPU y aprovechar siempre la cuantización por lotes en GPU (`gpu_quant.assign_idx_batch`).
3. **Fase `Extract` (`astra_extract.py`)**:
   - Extracción de fotogramas PNG intermedios con `-compression_level 1` y extracción dual en una sola pasada (`video_dual`) cuando `master != raw` (`Detallado` o `detail_motion`), decodificando el vídeo de origen una sola vez.

---

## 3. Referencia histórica (Astra 4 — revisión del bloqueo en Cinepak)

10 de noviembre de 2026. `megacd_clock.py` añade una rutina 68000 que divide las muestras en segundos y resto antes de multiplicar, evitando el desbordamiento con signo de 223,69 s y el desbordamiento sin signo de 447,39 s en Cinepak. Se conservan íntegramente menú START, pausa, reproducción, avance/retroceso, capítulos, salto, bucle, audio, paletas, brillo/tinte, encuadre, GPU/CPU, formatos FFmpeg, cancelación, reanudación y salida multidisco.
