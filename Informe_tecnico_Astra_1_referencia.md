# Mega CD Estudio Astra — informe de entrega

Fecha: 9 de noviembre de 2026. Versión beta para Windows de 64 bits.

## Resultado y límites

Se entrega «Mega Cd Grabador de Video Beta.exe» con ffmpeg 8.1.1 e ImgBurn incluidos, el código fuente, los recursos originales recuperados, las pruebas y un disco PAL de tres minutos. El EXE pasó su autoprueba y una conversión real de ocho segundos a 24 FPS con ISO/CUE. La ventana del EXE se abrió correctamente.

**No se ha probado en una consola real. No hay garantía de ausencia absoluta de cuelgues. Las transiciones entre archivos CIN todavía tienen una pausa; no se ha implementado una transición sin corte ni audio continuo entre CIN.** La espera nativa de 60 ticks se conserva. Los bucles de espera de la BIOS/CDC nativa no tienen un watchdog nuevo; una avería de lectura todavía puede bloquearlos. El control del retorno de FilmOpen evita ejecutar FilmTask sobre una apertura fallida, pero no convierte todos los errores de hardware en recuperables.

## Recursos recuperados y fuentes

Se extrajeron las siete constantes comprimidas de megacd_assets del EXE 7.0 existente junto al código anterior. Se inspeccionó el archivo PyInstaller y se descomprimieron constantes de datos; no se ejecutó el EXE antiguo. Los SHA-256 están en Procedencia_recursos.json. El código 7.0 de la carpeta Argon coincide con la referencia recibida.

IP.BIN, MAINPLAY.BIN y SUBPLAY.BIN coinciden byte por byte con los de [Newbilius/SegaCD_VideoCD_Converter](https://github.com/Newbilius/SegaCD_VideoCD_Converter), commit ced816ac6c8df7907652f89031b2ea9269cfb832. Se consultaron sus fuentes de análisis CINEPAKSRC y [la implementación de mandos de SGDK](https://github.com/Stephane-D/SGDK/blob/master/src/joy.c). No se han distribuido las BIOS del usuario.

## Mando y menú

El MAINPLAY original no contiene accesos a $A10003/$A10009 ni escribe el mando en $A1201A. La hipótesis del documento queda confirmada para estos binarios.

Se añaden 162 bytes al final de MAINPLAY y de su copia en IP. No se sobrescribe un supuesto hueco libre: se amplía el binario. El IP ampliado termina en $1C52, antes del SP en $2000. El hook $00A0 sustituye el LEA de inicialización por BSR a la rutina que configura TH una vez y ejecuta el LEA original. El hook $01B8 sustituye MOVEA.L #$A12000,A0 por BSR/NOP; la rutina reproduce esa instrucción al retornar. El MOVEM del handler y el RTE originales permanecen. Se guardan D0-D2/A0 en el lector; el SR del programa interrumpido lo restaura el RTE.

Cada VBlank lee las dos fases clásicas y completa ocho fases TH con dos NOP de asentamiento. Los bits de identificación y X/Y/Z/Mode no se mezclan con botones o direcciones: sólo se usan Start/A de la primera fase baja y C/B/R/L/D/U de la primera alta. No se selecciona una ruta de lectura por tipo de mando; esta secuencia común conserva los ocho botones clásicos en ambos tipos. TH termina alto. El mando 1 se repite en ambos bytes de $A1201A, activo en alto. La palabra de diagnóstico pad_ever acumula los botones vistos. El coste exacto de ciclos sobre hardware no se ha medido; el handler y los parches se probaron en emulación.

El callback VBlank del Sub captura el flanco START y conserva una petición pendiente. Si el banco actual todavía no tiene SM, se sigue llamando a FilmTask; se elimina el bucle que antes podía dejarlo bloqueado. En modo 1M cada CPU tiene su banco mapeado; SwapMeg intercambia cuál. Las pruebas con dos bancos comprueban que los dos SwapMeg del menú devuelven el mismo banco y restauran los bytes del fotograma. Kega ejercitó además el camino real de Word RAM/VDP/PCM. Se pausa antes de esperar que se suelte START y se borra la petición al cerrar, evitando reaperturas por una pulsación mantenida. El color 15 del menú se guarda, cambia temporalmente a blanco y se restaura.

Los botones A/B/C, el flanco largo/doble y los bytes alto/bajo se comprueban con Unicorn M68000. Se conservan Play, Pausa, Rew, Fwd y Stop. Rew vuelve al inicio del capítulo lógico. Fwd usa el reloj PCM para seis segundos y una cota de llamadas, en lugar de asumir que 90 llamadas equivalen a seis segundos. Su calidad y comportamiento bajo errores de CD aún requieren consola.

## Heap, capítulos y disco

FilmOpen lee la cabecera FILM completa, incluida STAB, y la aloja en el heap, redondeada a sectores. El heap se conserva en $10000..$5B000 (307200 bytes), con la pila por debajo de $10000 y las copias del menú en $E000. No se amplía a 400 KiB sin una validación completa del mapa BIOS/PRG-RAM.

La prueba usa el asignador 68000 real InitHeap/mmalloc/MaxFree: cabecera de 88064 bytes (5400 entradas), bloque de streaming de $34000 bytes y bloque PCM de 4096 bytes dejan 1968 bytes libres. Por ello se mantiene el máximo de 5400 entradas. A 24 FPS la duración teórica máxima es 216.057 s; el planificador deja además tres entradas de margen y alinea los cortes a fotogramas.

Los capítulos lógicos mantienen 1, 2, 3, 5 o 10 minutos elegidos por el usuario. Cada uno se divide internamente si hace falta; CAP XX/YY y Anterior/Siguiente usan el mapa lógico. A 24 FPS un capítulo de cinco minutos se divide en dos CIN y uno de diez en tres. **La división evita exceder STAB; no elimina la pausa de apertura entre archivos.**

FilmOpen devuelve 0 en éxito y -1 en error. El binario ya incluye cuatro intentos internos. El nuevo programa comprueba D0 antes de FilmTask, salta al siguiente CIN si falla y se detiene tras una vuelta completa de fallos. No envía FilmClose sobre un Open que no llegó a enviar el comando de reproducción al Main.

Los CIN se escriben contiguos, en orden y alineados a sectores; el último sector se rellena con ceros. Se verifican los directorios, las extensiones, ambos endian, los tamaños y las firmas FILM después de crear el ISO. Los directorios ahora pueden ocupar varios sectores; antes se omitían archivos al llenarse un único sector. Hay una prueba con 40 CIN. No se conoce otro límite de sector específico que se pueda certificar con estas pruebas; no se afirma compatibilidad absoluta con todo lector.

## Pruebas de datos

Las 30 filas siguientes son archivos FILM reales construidos a partir de ocho segundos codificados por FPS, con testsrc2, ruido, oscuridad y movimiento rápido, repetidos para alcanzar las duraciones largas. **No son 30 conversiones completas de películas distintas ni una reproducción de todas esas duraciones en emulador.** Se verifican duración de vídeo, caudal, cabecera y STAB de cada archivo resultante. Los límites son 142000 B/s de media y 150000 B/s en ventana de seis segundos; el lector entrega como máximo 153600 B/s = 150 KiB/s. Las medidas se hacen sobre los archivos finales e incluyen su cabecera en la media.

| FPS | Minutos lógicos | CIN | Media máxima B/s | Pico 6 s B/s | STAB máxima |
|---:|---:|---:|---:|---:|---:|
| 8 | 1 | 1 | 89180.2 | 98727.33 | 540 |
| 8 | 2 | 1 | 88448.57 | 98727.33 | 1080 |
| 8 | 3 | 1 | 88600.96 | 98727.33 | 1619 |
| 8 | 5 | 1 | 88539.77 | 98727.33 | 2699 |
| 8 | 10 | 1 | 88366.11 | 98727.33 | 5397 |
| 10 | 1 | 1 | 107907.53 | 119719.0 | 660 |
| 10 | 2 | 1 | 106931.32 | 119719.0 | 1320 |
| 10 | 3 | 1 | 107165.23 | 119719.0 | 1979 |
| 10 | 5 | 1 | 107071.44 | 119719.0 | 3299 |
| 10 | 10 | 2 | 107071.44 | 119719.0 | 3299 |
| 12 | 1 | 1 | 118213.33 | 130365.0 | 780 |
| 12 | 2 | 1 | 117515.32 | 130365.0 | 1560 |
| 12 | 3 | 1 | 117656.5 | 130365.0 | 2339 |
| 12 | 5 | 1 | 117599.8 | 130365.0 | 3899 |
| 12 | 10 | 2 | 117599.8 | 130365.0 | 3899 |
| 15 | 1 | 1 | 130225.0 | 142632.33 | 960 |
| 15 | 2 | 1 | 129954.82 | 142632.33 | 1920 |
| 15 | 3 | 1 | 129953.39 | 142632.33 | 2879 |
| 15 | 5 | 1 | 129953.73 | 142632.33 | 4799 |
| 15 | 10 | 2 | 129953.73 | 142632.33 | 4799 |
| 20 | 1 | 1 | 133723.03 | 139501.67 | 1260 |
| 20 | 2 | 1 | 133615.82 | 139501.67 | 2520 |
| 20 | 3 | 1 | 133560.07 | 139501.67 | 3779 |
| 20 | 5 | 2 | 133444.8 | 139501.67 | 3150 |
| 20 | 10 | 3 | 133533.59 | 139501.67 | 4199 |
| 24 | 1 | 1 | 135228.6 | 137093.0 | 1500 |
| 24 | 2 | 1 | 135206.82 | 137093.0 | 3000 |
| 24 | 3 | 1 | 135122.59 | 137093.0 | 4499 |
| 24 | 5 | 2 | 135175.35 | 137093.0 | 3750 |
| 24 | 10 | 3 | 135124.59 | 137093.0 | 4999 |


El audio PCM se conserva byte por byte frente a 7.0 en Alto Cine, Máximo, Estándar y Original, con y sin claridad vocal: ocho comparaciones, cero bytes diferentes. El código 68000 de audio también es idéntico a los parches de 7.0; la única diferencia nueva en SUBPLAY es $0797: $7C → $8C, que continúa el preroll de sectores dentro del mismo VBlank. Los cambios A/V heredados se documentan íntegros en Auditoria_68000.json.

El contenedor conserva la escala de tiempos y el prebuffer de audio de 7.0. El PCM lleva relleno de silencio hasta sus bloques de 16384 bytes; por eso audio_s del informe puede superar la duración visual. No es una prueba de sincronía labial ni una garantía de continuidad audible al cambiar de CIN.

Las 27 combinaciones de tres perfiles de brillo, tres ajustes de pantalla y tres presets antirosa generan paletas neutras con sesgo R/G/B cero en una rampa gris. Se corrige la protección de grises para reconocerlos antes de la compensación de canal, incluido el recorte del verde en el preset fuerte. La carta de piel tiene nueve medidas en Tabla_piel.csv. Los presets antirosa cambian intencionadamente los colores; no se afirma identidad de tonos de piel entre presets. Para no aplicar esa corrección de canal, seleccionar Desactivado. No se añade una curva DAC no lineal sin medidas de la consola/cable concretos. Tampoco se certifica ausencia de todo parpadeo de paletas en cualquier película.

La geometría Tk pasa seis tamaños entre 640×480 y 3840×2160, sin hijos más anchos que su contenedor. Se abrió visualmente la ventana del EXE en la pantalla disponible. La prueba 4K usa un tamaño de ventana, no una pantalla 4K física ni todas las configuraciones de DPI.

## Kega Fusion y consola

Kega Fusion 3.64 usa la BIOS PAL local eu_mcd2_9306.bin. La prueba de integración inyecta START en comando 5 a los 5 y 45 segundos del reloj Main; permitió observar menú blanco, pausa y reanudación. Esa inyección sólo existe en Pruebas/Kega_menu_automatico y su script build_kega_test.py. La imagen de Disco_prueba_PAL_3_min usa el lector de mandos normal. La herramienta de teclado genera pulsaciones demasiado breves para certificar aquí el botón físico de tres o seis botones en Kega; esa lectura se verifica con el protocolo simulado exhaustivo de Unicorn (4352 combinaciones).

La constatación del final del disco y su estado guardado se registra aparte en Pruebas/Resultado_Kega.json. No se ha grabado ningún CD ni usado una consola real.

## Archivos de auditoría

Pruebas contiene los CSV, los registros de compilación y pruebas, Autoprueba_EXE.json, Menu_Kega.jpg, Auditoria_68000.json (bytes antiguos/nuevos, rutinas añadidas, SP completo y símbolos), Listado_68000.txt y el disco de integración. Compilar_EXE.bat recompila dentro de esta misma carpeta. Para ejecutar las pruebas: instalar requirements-pruebas.txt y ejecutar test_astra.py, test_color.py, test_layout.py; validate_media.py requiere --work con una carpeta temporal.

## Cierre de verificación

La repetición final de Kega llegó al fin de los 180 segundos a 24 FPS. El estado guardado confirma Sub-PC $6118 (bucle final), CurOffset=4, STABIndex=$FFFFFFFF y cero fallos de apertura. El menú se abrió, pausó y reanudó con las pulsaciones programadas. Tras corregir la ruta de EOF pasan 13 pruebas de lógica, protocolo, heap y directorios. La última compilación pasó otra autoprueba y otra conversión real de ocho segundos a 24 FPS.

La prueba de 40 CIN usa archivos sintéticos para comprobar únicamente el directorio y las extensiones ISO.
