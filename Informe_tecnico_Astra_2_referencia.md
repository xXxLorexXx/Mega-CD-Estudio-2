# Mega CD Estudio Astra 2 — entrega final

9 de noviembre de 2026. Windows de 64 bits. Carpeta: «mega cd estudio astra 2» del escritorio.

El programa incluye un modo explícito «24 FPS nítidos: adapta área / varios CD». Conserva 24 FPS y no fuerza la copia de bloques de imagen cambiados. Adapta el área activa al caudal del lector y crea tantos ISO/CUE como hagan falta. **Esto no conserva siempre el área original de 256×192 ni obliga a que toda película quepa en un único CD.** El tamaño de imagen y el número de discos se muestran antes de convertir. «Mantener proporción original» conserva todo el encuadre; «Recortar» sigue disponible para quien quiera expresamente ese efecto. No se promete ausencia universal de fallos ni se ha probado una consola real.

## Película completa probada desde el ejecutable

Origen: MKV de 5.548.871.140 bytes, 1920×816 y 7.613,376 segundos. Ajustes: 24 FPS, encuadre completo con proporción original y bandas negras, cuatro paletas, Alto Cine con claridad vocal, brillo estándar, antirrosa suave, capítulos de diez minutos, menú START, salto y bucle activados, región E. Se conservaron todos los 182.721 fotogramas previstos; la duración codificada es 7.613,375 segundos. No se añadió cola artificial a esta película.

La película se conserva completa, sin recortar laterales ni aplicar zoom. El área activa exterior es **256×112**, dentro del lienzo 256×192. El filtro mantiene la proporción original y centra la imagen dentro de esa área, con bandas negras; las cuatro esquinas quedan incluidas. Los 38 CIN se repartieron en dos CD, manteniendo su orden y asignando cada fotograma de salida previsto a un único disco. El filtro FPS adapta la cadencia del origen a 24 FPS; no inventa movimiento nuevo.

| CD | Bytes del ISO | MiB | Fotogramas |
|---:|---:|---:|---:|
| 1 | 731,428,864 | 697.545 | 124,800 |
| 2 | 338,829,312 | 323.133 | 57,921 |

El CD_01 contiene 01:26:40.000 y el CD_02 00:40:13.375; total 02:06:53.375. No falta la segunda parte: está en CD_02.

Cada disco queda por debajo de 734.003.200 bytes (700 MiB). Tiempo total del EXE sin reutilizar capítulos, incluida su extracción inicial: **441.80 segundos (7.36 minutos)**. CUDA y JIT intervinieron en los 38 CIN. La medición corresponde a este equipo: i7-12700KF, 64 GB y RTX 4060 Ti de 16 GB; no garantiza el mismo tiempo para todos los archivos/equipos. No se comparó una conversión con caché con otra sin ella.

La salida está en `Pelicula_24_FPS_completa/CD_01` y `CD_02`. Cada carpeta contiene su propio ISO y CUE. Cada CUE corresponde a un CD físico. El cambio de disco es manual; la casilla de bucle mantiene el comportamiento de Astra en cada disco.

## Nitidez y límites del Mega CD

El mosaico se producía al forzar Method 0 sobre bloques que habían cambiado para reducir los bytes por imagen. El modo protegido no lo hace: Method 0 sólo conserva bloques cuyo resultado cuantizado es idéntico o bandas negras ya inicializadas. Ambos bancos se inicializan completos. Se compararon sus píxeles decodificados directamente con la versión 7.0 con presupuesto suficiente; la prueba pasa. Se conservan los diccionarios de vectores reales y todos los ajustes de paleta, proporción, brillo y tinte.

Para sostener 24 FPS sin esa reutilización con pérdidas, el programa reduce el área activa cuando la imagen completa no es sostenible. En el formato panorámico de este MKV mantiene 256×112; en recorte/estiramiento usa un área menor. Las dimensiones se alinean a tiles y se centran con bandas negras. No se oculta este cambio ni se anuncian 24 FPS cuando la salida contiene menos fotogramas por segundo.

La muestra de 30 segundos con bandas a 24 FPS pasó el control de caudal. El cálculo conservador para la película completa con esas bandas requiere aproximadamente 1.019 MiB; por eso un único CD no permite mantener simultáneamente ese área, duración y nitidez. El modo automático de un CD puede elegir menos FPS; el modo nuevo de 24 FPS mantiene los FPS y reparte en varios discos.

No se puede garantizar «cualquier vídeo, toda la resolución, 24 FPS, un CD y ningún bloqueo» con este hardware/formato. Los vídeos deben ser válidos y decodificables por FFmpeg. En el modo de 24 FPS, los fragmentos finales de menos de dos segundos pueden prolongar el último fotograma y rellenar audio con silencio para amortizar la inicialización; queda indicado en el plan. Los capítulos solicitados por debajo de dos segundos se elevan a dos. Las películas normales no necesitan ese ajuste salvo un fragmento final muy corto.

## Protección frente a bloqueos y errores

Se verifica tamaño real, cabeceras, STAB y caudal de todos los CIN. Límites conservados: 142.000 B/s de media y 150.000 B/s en ventana de seis segundos frente a los 153.600 B/s del lector 1×. En esta película, la media máxima es 140796.60 B/s y el pico máximo de seis segundos 145225.67 B/s. STAB máxima: 5167 entradas, con límite seguro de 5.400.

El presupuesto de clips cortos incluye la precarga PCM y la inicialización de ambos bancos. El plan cuenta audio, cabeceras, tablas, reproductor, directorios ISO/Joliet, sectores de relleno y margen de capacidad. No se publica un conjunto terminado antes de verificar todos sus discos. Se comprobó el SHA-256 de cada CIN dentro de cada ISO y se repitió la lectura mediante un verificador independiente.

FFmpeg dispone de cancelación y tiempo máximo durante la extracción de audio, y un vigilante termina la lectura si deja de entregar fotogramas durante 60 segundos. El tiempo de cálculo/JIT no se confunde con una lectura atascada. Las pruebas simulan un proceso que no entrega datos y comprueban que tanto la cancelación como el límite de espera lo terminan. La interfaz continúa en su hilo principal. Los CIN incompletos se descartan y los capítulos terminados se conservan para reanudar.

El reproductor 68000, sus parches de mando, START, heap y audio se mantienen equivalentes a Astra. El control de datos y las pruebas en Kega reducen riesgos, pero no certifican el estado de un lector real ni todos los errores de BIOS. **Las pausas nativas entre CIN siguen existiendo. No hay reproducción continua entre CD.**

## Rapidez y GPU para otros formatos

CUDA acelera búsquedas de vectores Cinepak y comparación de bancos para cualquier contenedor decodificable; no se limita a leer MKV. La lectura NVDEC se añade cuando el códec/perfil pasa una prueba real. Se comprobaron AVI/FFV1, MP4/H.264, MOV/MJPEG y WebM/VP9, además del MKV/H.264 completo. Incluso AVI y MOV usaron CUDA en Cinepak mientras su lectura se hacía en CPU. Las salidas de los cuatro ejemplos CPU/GPU coinciden byte por byte.

Numba acelera paletas, agrupaciones, preservación de grises y empaquetado. Se evitan hilos BLAS excesivos y se convierten hasta seis CIN en paralelo en automático. La interfaz permite 1, 2, 4, 6 u 8 procesos; más procesos no siempre mejoran el tiempo. La GPU tiene respaldo CPU si no está disponible. El EXE incluye FFmpeg, ffprobe, ImgBurn, NumPy/Numba/LLVM y las dos DLL necesarias de NVRTC; no requiere instalar Python ni el toolkit CUDA. CUDA se ha probado en NVIDIA; AMD/Intel disponen de la ruta CPU.

Las conversiones conservan una caché con huellas de fuente/ajustes y SHA-256. Con el mismo vídeo, ajustes y destino pueden reutilizar capítulos completos. La caché, `Archivos_CD` e ISO consumen espacio adicional. El modo de un CD ya completó también esta película a 10 FPS en una prueba previa de aproximadamente 4 min 48 s; esa opción se conserva junto al nuevo modo de 24 FPS.

## Opciones conservadas y uso

Se conservan paletas, regiones, proporción, recorte y estiramiento, brillo, antirrosa, audio y claridad vocal, orden de vídeos, capítulos, START con Play/Pausa/Rew/Fwd/Stop, salto A/B/C, bucle, prueba rápida y grabación manual/automática con ImgBurn. No se eliminó ninguna de las 31 definiciones de función/clase del módulo anterior. Ocho combinaciones de procesado PCM coinciden exactamente con la versión 7.0. Los recursos originales y su procedencia siguen incluidos.

Seleccione «24 FPS nítidos: adapta área / varios CD» en «Prioridad al ajustar a CD». Ese modo fija los FPS a 24 y desmarca el límite a un único CD. Revise el área y el número de CD mostrados y pulse Convertir. Use «Elegir disco...» para probar o grabar CD_01, CD_02, etc. La grabación automática comienza por el primero; los siguientes requieren cambiar el CD virgen y elegir su imagen. La prueba rápida usa el mismo modo y área que el plan de la película.

Los otros modos son «Nitidez 7.0: FPS automáticos» para ajustar a un CD sin forzar bloques cambiados, y «FPS fijos: compresión temporal» para conservar el comportamiento de compresión anterior. El segundo puede producir mosaico; no es el modo protegido.

Código fuente, `Compilar_EXE.bat`, `compilar.py`, dependencias y pruebas están incluidos. La compilación usa Python 3.11 de 64 bits y requiere ffmpeg/ffprobe en PATH o junto al código. `Referencia_Astra_1.py`, `Codigo_Fuente_MegaCD_Video_Studio_7_0.py` e `Informe_tecnico_Astra_1_referencia.md` conservan la base y el análisis anterior; no son el informe vigente.

## Pruebas y fuentes

Registros en `Pruebas_Astra_2`: 13 pruebas nativas/heap/ISO, cinco de codec y renovación heredada, seis de nitidez/7.0/bancos/cancelación, tres de multidisco, cuatro formatos CPU/GPU, 30 combinaciones de caudal FPS/duración, 27 combinaciones de grises, nueve medidas de piel y seis tamaños de ventana. Las 30 duraciones largas repiten ocho segundos de patrones codificados; no son treinta películas diferentes ni treinta reproducciones en emulador. La película completa nueva sí se convirtió desde cero mediante el EXE final. No se grabó un CD físico ni se probó una consola real.

Fuentes primarias: [FFmpeg](https://www.ffmpeg.org/ffmpeg-all.html), [NVIDIA y FFmpeg](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html), [CUDA Driver API](https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__EXEC.html), [NVRTC](https://docs.nvidia.com/cuda/nvrtc/index.html), [Numba](https://github.com/numba/numba/blob/main/docs/source/user/installing.rst), [fuentes Cinepak Mega CD](https://github.com/Newbilius/SegaCD_VideoCD_Converter) y [SGDK](https://github.com/Stephane-D/SGDK/blob/master/src/joy.c).
