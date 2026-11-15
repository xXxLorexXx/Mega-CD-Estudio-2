# Mega CD Estudio 2

**Crea tus propias películas y vídeos para tu Mega CD.**  
**Play your own videos on your Mega CD.**

[Español](#es) · [English](#en) · [Français](#fr) · [Português](#pt) · [Italiano](#it) · [日本語](#ja) · [简体中文](#zh) · [Deutsch](#de) · [Русский](#ru)

<a id="es"></a>
## Español

Mega CD Estudio 2 distribuye la **versión 13 de Mega CD Estudio**. Es una aplicación para Windows x64 que convierte vídeos en imágenes **ISO + CUE** arrancables para Sega Mega CD / Sega CD. Admite fuentes MKV, MP4, AVI, MOV y otros formatos reconocidos por FFmpeg. La interfaz está en español y toda la conversión se ejecuta localmente.

**[Descargar para Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

### Cómo crear un disco

1. Descarga y extrae el ZIP completo de la sección Releases. Abre **Mega CD Estudio 13.exe**. El ejecutable incluye los componentes de Windows necesarios y no requiere instalar Python.
2. Añade los vídeos en **Biblioteca**. En **Ajustes**, selecciona el códec y la región de tu consola. Para mantener el encuadre, elige conservar la proporción.
3. Para **CAVC** o **LORE**, instala Ubuntu 24.04 en WSL 2, completa su configuración inicial y ejecuta una vez **Preparar_CAVC.bat**. Mantén la carpeta **cavc** junto al EXE. Cinepak funciona directamente en Windows.
4. En **Disco y grabación**, selecciona la carpeta de salida y pulsa **Crear disco**. Conserva cada archivo CUE junto a su ISO. Abre el CUE en Kega Fusion o ares, o instálate ImgBurn para grabarlo en un CD-R.

| Códec | Cuándo usarlo |
| --- | --- |
| **Cinepak** | Reproductor nativo, menú START, capítulos y perfiles de nitidez. El modo de 24 FPS nítidos puede crear varios CD automáticamente cuando hace falta. |
| **CAVC** | Vídeo de hasta 320 × 224 y audio ADPCM mono. Ofrece 15, 24 o 30 FPS nominales en NTSC y 25 FPS nominales experimentales en PAL. |
| **LORE** | Para PAL. Adapta las actualizaciones de vídeo entre 15 y 25 FPS nominales y usa audio ADPCM mono a 18 kHz. El ajuste experimental de movimiento se puede activar en la interfaz. |

Las partes de CAVC y LORE son tramos de **un mismo CD**. Si un largometraje excede la capacidad estimada, divide la fuente y crea una pareja ISO/CUE por CD. La duración, el caudal y los datos de arranque influyen en la capacidad final.

### Qué trae la versión 13

- Acelera el cálculo de paletas, la cuantización y la selección de bloques con Numba y, según el equipo, CUDA. Usa extracción compartida y puede codificar dos partes en paralelo.
- Muestra la estimación de partes por vídeo y ajusta la reserva de espacio para CAVC y LORE.
- Permite exportar una **vista previa MP4** incluso cuando el disco contiene varias partes.
- Conserva las partes verificadas tras cancelar y añade **Limpiar caché temporal**. Repite la conversión con los mismos ajustes para reanudar.
- Comprueba la estructura y las huellas del ISO/CUE de CAVC y LORE antes de grabar. En Cinepak se puede verificar la reproducción completa con una copia compatible de Kega Fusion 3.64 y una BIOS de la región. Kega Fusion, ares, ImgBurn y las BIOS se obtienen por separado.

La primera conversión puede tardar más por la compilación inicial. Comprueba la reproducción antes de grabar. La compatibilidad puede variar según la consola, el vídeo y el disco.

<a id="en"></a>
## English

Mega CD Estudio 2 distributes **Mega CD Estudio version 13**, a Windows x64 application that turns your videos into bootable **ISO + CUE** images for Sega Mega CD / Sega CD. It uses FFmpeg to read MKV, MP4, AVI, MOV and other formats. The interface is in Spanish and encoding runs locally.

**[Download for Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Extract the complete ZIP and open **Mega CD Estudio 13.exe**. You can run the Windows build without installing Python.
2. Add your videos in **Biblioteca**. Choose a codec and console region in **Ajustes**. Select the option that preserves the aspect ratio to keep the full image.
3. **CAVC and LORE** require Ubuntu 24.04 on WSL 2. Finish the Ubuntu setup, run **Preparar_CAVC.bat** once and keep **cavc** next to the EXE. Cinepak runs directly on Windows.
4. Select an output folder under **Disco y grabación**, click **Crear disco**, and keep each CUE next to its ISO. Open the CUE in Kega Fusion or ares, or install ImgBurn to write a CD-R.

**Cinepak** offers chapter navigation, the START menu and a sharp 24 FPS option that can split a film across discs. **CAVC** supports up to 320 × 224 with mono ADPCM audio. **LORE** is a PAL option with 15–25 nominal video updates per second and 18 kHz mono ADPCM. CAVC and LORE parts play within one disc. Split an oversized video source before making separate discs.

Version 13 speeds up frame extraction and encoding with shared work, Numba and optional CUDA. It can encode two parts in parallel, estimates part counts, and exports a combined MP4 preview for multipart discs. It keeps verified parts when a job is interrupted and includes a temporary-cache cleanup button. CAVC/LORE validation checks image structure and hashes. Cinepak can check full playback using a compatible external Kega Fusion 3.64 and a region-matched BIOS.

Emulators, BIOS files and ImgBurn are separate. Test playback before burning.

<a id="fr"></a>
## Français

Mega CD Estudio 2 distribue la **version 13 de Mega CD Estudio**. Cette application Windows x64 convertit vos vidéos en images amorçables **ISO + CUE** pour le Sega Mega CD / Sega CD. L’interface est en espagnol et le traitement s’effectue localement.

**[Télécharger pour Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Décompressez le ZIP complet et ouvrez **Mega CD Estudio 13.exe**. Python n’est pas nécessaire pour lancer le programme.
2. Ajoutez les vidéos dans **Biblioteca**, puis choisissez le codec et la région de votre console dans **Ajustes**. Conservez les proportions pour garder l’image entière.
3. Pour **CAVC** et **LORE**, configurez Ubuntu 24.04 dans WSL 2, exécutez une fois **Preparar_CAVC.bat** et laissez le dossier **cavc** à côté de l’EXE. Cinepak fonctionne directement sous Windows.
4. Choisissez le dossier de sortie dans **Disco y grabación**, puis cliquez sur **Crear disco**. Gardez chaque CUE à côté de son ISO. Ouvrez le CUE dans Kega Fusion ou ares, ou installez ImgBurn pour graver un CD-R.

**Cinepak** propose des chapitres, un menu START et un mode net à 24 i/s qui peut répartir un film sur plusieurs disques. **CAVC** monte à 320 × 224 avec du son ADPCM mono. **LORE** est destiné au PAL, avec 15 à 25 mises à jour d’image par seconde en cadence nominale et du son mono à 18 kHz. Les parties CAVC/LORE appartiennent au même disque. Divisez la vidéo source si elle dépasse la capacité d’un CD.

La version 13 accélère l’extraction et l’encodage avec Numba et CUDA en option, traite jusqu’à deux parties en parallèle et estime leur nombre. Elle exporte un aperçu MP4 des disques en plusieurs parties, reprend les parties déjà vérifiées après une interruption et permet de vider le cache temporaire. Le programme vérifie la structure et les empreintes ISO/CUE de CAVC et LORE. Le contrôle complet de Cinepak utilise un Kega Fusion 3.64 compatible et un BIOS de la bonne région. Les émulateurs, le BIOS et ImgBurn sont à obtenir séparément.

<a id="pt"></a>
## Português

Mega CD Estudio 2 distribui a **versão 13 do Mega CD Estudio**. O programa para Windows x64 converte vídeos em imagens inicializáveis **ISO + CUE** para Sega Mega CD / Sega CD. A interface está em espanhol e o processamento é local.

**[Baixar para Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Extraia todo o ZIP e abra **Mega CD Estudio 13.exe**. Não é necessário instalar o Python.
2. Adicione vídeos em **Biblioteca** e escolha o codec e a região do console em **Ajustes**. Para manter a imagem inteira, preserve a proporção.
3. Para **CAVC** e **LORE**, configure o Ubuntu 24.04 no WSL 2, execute **Preparar_CAVC.bat** uma vez e mantenha a pasta **cavc** junto ao EXE. O Cinepak funciona diretamente no Windows.
4. Selecione a pasta de saída em **Disco y grabación** e clique em **Crear disco**. Mantenha cada CUE junto ao ISO correspondente. Abra o CUE no Kega Fusion ou no ares, ou instale o ImgBurn para gravar um CD-R.

O **Cinepak** oferece capítulos, menu START e um modo de 24 FPS que pode distribuir um filme por vários discos. O **CAVC** oferece até 320 × 224 com áudio ADPCM mono. O **LORE** é para PAL, com 15–25 atualizações de vídeo por segundo em cadência nominal e áudio mono de 18 kHz. Em CAVC e LORE, as partes ficam no mesmo disco. Divida o vídeo original se ultrapassar a capacidade de um CD.

A versão 13 acelera a extração e a codificação com Numba e CUDA opcional, pode processar duas partes em paralelo, estima a quantidade de partes e exporta uma prévia MP4 que inclui todas elas. Preserva partes verificadas após cancelamento e permite limpar o cache temporário. A verificação de CAVC/LORE confere a estrutura e os hashes da imagem. O Cinepak pode verificar a reprodução completa com uma cópia compatível do Kega Fusion 3.64 e uma BIOS da região correta. Obtenha emuladores, BIOS e ImgBurn separadamente.

<a id="it"></a>
## Italiano

Mega CD Estudio 2 distribuisce la **versione 13 di Mega CD Estudio**. È un’applicazione per Windows x64 che converte i video in immagini avviabili **ISO + CUE** per Sega Mega CD / Sega CD. L’interfaccia è in spagnolo e l’elaborazione avviene sul computer.

**[Scarica per Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Estrai tutto il contenuto dello ZIP e apri **Mega CD Estudio 13.exe**. L’eseguibile non richiede Python.
2. Aggiungi i video in **Biblioteca**. In **Ajustes** seleziona il codec e la regione della console. Per mantenere l’inquadratura completa, conserva le proporzioni originali.
3. Per **CAVC** e **LORE**, configura Ubuntu 24.04 in WSL 2, avvia **Preparar_CAVC.bat** una volta e tieni la cartella **cavc** accanto all’EXE. Cinepak funziona direttamente su Windows.
4. Scegli la cartella di uscita in **Disco y grabación** e premi **Crear disco**. Tieni ogni CUE insieme alla relativa ISO. Apri il CUE in Kega Fusion o ares, oppure installa ImgBurn per scrivere un CD-R.

**Cinepak** offre capitoli, menu START e una modalità a 24 FPS che può ripartire un film su più dischi. **CAVC** arriva a 320 × 224 con audio ADPCM mono. **LORE** è pensato per PAL, con una frequenza nominale variabile di 15–25 aggiornamenti video al secondo e audio mono a 18 kHz. Le parti di CAVC e LORE appartengono allo stesso disco. Dividi il video di partenza se supera la capacità di un CD.

La versione 13 accelera estrazione e codifica con Numba e CUDA facoltativo. Può elaborare due parti in parallelo, ne mostra la stima ed esporta un’anteprima MP4 anche per i dischi con più parti. Conserva le parti verificate se interrompi il lavoro e permette di svuotare la cache temporanea. Per CAVC e LORE controlla la struttura e gli hash dell’immagine. Per verificare tutta la riproduzione di un disco Cinepak servono una copia compatibile di Kega Fusion 3.64 e il BIOS della regione corretta. Emulatori, BIOS e ImgBurn sono separati.

<a id="ja"></a>
## 日本語

Mega CD Estudio 2 は **Mega CD Estudio バージョン 13** の配布用リポジトリです。Windows x64 で動画を Sega Mega CD / Sega CD 用の起動可能な **ISO + CUE** に変換します。画面表示はスペイン語で、変換はパソコン上で行われます。

**[Windows x64 版をダウンロード](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. ZIP をすべて展開し、**Mega CD Estudio 13.exe** を開きます。Python のインストールは不要です。
2. **Biblioteca** で動画を追加し、**Ajustes** でコーデックと本体のリージョンを選びます。映像全体を表示するには、アスペクト比を維持する設定を選びます。
3. **CAVC** と **LORE** を使う場合は、WSL 2 で Ubuntu 24.04 をセットアップし、**Preparar_CAVC.bat** を一度実行します。EXE と同じ場所に **cavc** フォルダーを置いてください。Cinepak は Windows 上で直接動作します。
4. **Disco y grabación** で出力先を選び、**Crear disco** を押します。CUE と対応する ISO を同じフォルダーに置きます。CUE は Kega Fusion や ares で開けます。CD-R に書き込む場合は ImgBurn を別途インストールしてください。

**Cinepak** はチャプターと START メニューを備え、24 FPS の高画質モードでは必要に応じて複数のディスクに分割できます。**CAVC** は最大 320 × 224 とモノラル ADPCM 音声に対応します。PAL 用の **LORE** は毎秒 15～25 回の公称映像更新と 18 kHz のモノラル ADPCM を使用します。CAVC と LORE のパートは同じ CD 内で再生されます。1 枚に収まらない場合は、元の動画を分割してください。

バージョン 13 では Numba とオプションの CUDA で処理を高速化し、最大 2 パートの並列処理、パート数の見積もり、複数パートを含む MP4 プレビューに対応しました。中断後は検証済みパートを再利用でき、一時キャッシュも削除できます。CAVC と LORE の検証では ISO/CUE の構造とハッシュを確認します。Cinepak の全編再生の確認には、対応する Kega Fusion 3.64 とリージョンに合った BIOS が必要です。エミュレーター、BIOS、ImgBurn は別途入手してください。

<a id="zh"></a>
## 简体中文

Mega CD Estudio 2 提供 **Mega CD Estudio 第 13 版**。这款 Windows x64 应用可将视频转换为 Sega Mega CD / Sega CD 可启动的 **ISO + CUE** 光盘镜像。界面为西班牙语，转换在本机完成。

**[下载 Windows x64 版](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. 完整解压 ZIP，打开 **Mega CD Estudio 13.exe**。运行该程序无需安装 Python。
2. 在 **Biblioteca** 中添加视频，在 **Ajustes** 中选择编码方式和主机区域。想保留完整画面时，请选择保持宽高比。
3. 使用 **CAVC** 或 **LORE** 时，请先在 WSL 2 中配置 Ubuntu 24.04，运行一次 **Preparar_CAVC.bat**，并将 **cavc** 文件夹保留在 EXE 旁边。Cinepak 可直接在 Windows 中运行。
4. 在 **Disco y grabación** 中设置输出目录，点击 **Crear disco**。将每个 CUE 与对应 ISO 放在同一文件夹。使用 Kega Fusion 或 ares 打开 CUE，或另行安装 ImgBurn 将其刻录到 CD-R。

**Cinepak** 支持章节、START 菜单和可自动分盘的 24 FPS 清晰模式。**CAVC** 支持最高 320 × 224 分辨率和单声道 ADPCM 音频。PAL 专用的 **LORE** 使用标称每秒 15～25 次画面更新和 18 kHz 单声道 ADPCM 音频。CAVC 和 LORE 的各个分段都在同一张光盘上。如果源视频超出单盘容量，请先将其切分。

第 13 版使用 Numba 和可选的 CUDA 加快提取与编码，支持同时编码两个分段、估算分段数量，以及为多分段光盘导出完整 MP4 预览。中断后可继续使用已验证的分段，也可以清理临时缓存。CAVC 和 LORE 的验证会检查 ISO/CUE 结构和哈希值。Cinepak 的完整播放验证需要兼容的 Kega Fusion 3.64 和相应区域的 BIOS。模拟器、BIOS 和 ImgBurn 需另行获取。

<a id="de"></a>
## Deutsch

Mega CD Estudio 2 stellt **Mega CD Estudio Version 13** bereit. Die Windows-x64-Anwendung wandelt Videos in startfähige **ISO- und CUE-Dateien** für Sega Mega CD / Sega CD um. Die Oberfläche ist auf Spanisch und die Verarbeitung erfolgt lokal.

**[Für Windows x64 herunterladen](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Entpacke das vollständige ZIP und starte **Mega CD Estudio 13.exe**. Python ist für das fertige Programm nicht erforderlich.
2. Füge Videos in **Biblioteca** hinzu und wähle Codec und Konsolenregion unter **Ajustes**. Behalte das Seitenverhältnis bei, um das vollständige Bild zu erhalten.
3. Für **CAVC** und **LORE** richtest du Ubuntu 24.04 unter WSL 2 ein und führst einmal **Preparar_CAVC.bat** aus. Der Ordner **cavc** muss neben der EXE liegen. Cinepak läuft direkt unter Windows.
4. Wähle den Ausgabeordner unter **Disco y grabación** und klicke auf **Crear disco**. Lass jede CUE bei ihrer ISO. Öffne die CUE in Kega Fusion oder ares. Zum Brennen auf CD-R kannst du ImgBurn separat installieren.

**Cinepak** bietet Kapitel, ein START-Menü und einen 24-FPS-Modus, der lange Filme auf mehrere CDs verteilen kann. **CAVC** unterstützt bis zu 320 × 224 und ADPCM-Monoton. **LORE** ist für PAL gedacht und aktualisiert das Bild nominell 15- bis 25-mal pro Sekunde bei 18 kHz Monoton. Die Teile von CAVC und LORE gehören jeweils zu einer CD. Teile bei Bedarf das Quellvideo für mehrere CDs auf.

Version 13 beschleunigt Extraktion und Kodierung durch Numba und optionales CUDA, ermöglicht zwei parallele Teile und zeigt deren geschätzte Anzahl an. Sie erstellt eine MP4-Vorschau auch für mehrteilige CDs, übernimmt geprüfte Teile nach einem Abbruch und kann den temporären Cache leeren. Die Überprüfung für CAVC und LORE kontrolliert ISO/CUE-Struktur und Hashwerte. Für die vollständige Cinepak-Wiedergabeprüfung werden ein kompatibles Kega Fusion 3.64 und das BIOS der richtigen Region benötigt. Emulatoren, BIOS und ImgBurn sind separat erhältlich.

<a id="ru"></a>
## Русский

В репозитории Mega CD Estudio 2 публикуется **Mega CD Estudio версии 13**. Программа для Windows x64 преобразует видео в загрузочные образы **ISO + CUE** для Sega Mega CD / Sega CD. Интерфейс на испанском языке, обработка выполняется на компьютере пользователя.

**[Скачать для Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Распакуйте весь ZIP-архив и запустите **Mega CD Estudio 13.exe**. Установка Python для готового приложения не нужна.
2. Добавьте видео в **Biblioteca**, а в **Ajustes** выберите кодек и регион консоли. Чтобы сохранить кадр целиком, выберите сохранение пропорций.
3. Для **CAVC** и **LORE** настройте Ubuntu 24.04 в WSL 2, один раз запустите **Preparar_CAVC.bat** и оставьте папку **cavc** рядом с EXE. Cinepak работает непосредственно в Windows.
4. В разделе **Disco y grabación** выберите папку вывода и нажмите **Crear disco**. Храните каждый CUE вместе с его ISO. Откройте CUE в Kega Fusion или ares либо установите ImgBurn для записи на CD-R.

**Cinepak** поддерживает главы, меню START и режим 24 FPS, в котором фильм можно автоматически распределить по нескольким дискам. **CAVC** поддерживает разрешение до 320 × 224 и монофонический звук ADPCM. **LORE** предназначен для PAL, обновляет изображение с номинальной частотой 15–25 раз в секунду и использует монофонический ADPCM 18 кГц. Части CAVC и LORE воспроизводятся в пределах одного диска. Если видео не помещается, заранее разделите исходный файл.

Версия 13 ускоряет извлечение кадров и кодирование с помощью Numba и необязательного CUDA, позволяет обрабатывать до двух частей параллельно и оценивает их количество. Она экспортирует общий MP4 для диска из нескольких частей, сохраняет проверенные части при отмене и позволяет очищать временный кэш. Проверка CAVC и LORE сверяет структуру и хеши ISO/CUE. Для проверки полного воспроизведения Cinepak нужны совместимая версия Kega Fusion 3.64 и BIOS нужного региона. Эмуляторы, BIOS и ImgBurn распространяются отдельно.

## Código fuente, compilación y créditos · Source, build and credits

El punto de entrada es **Mega_Cd_Grabador_de_Video_Beta.py**. Para compilar en Windows usa Python 3.11 x64, FFmpeg y FFprobe en el PATH, y ejecuta **Compilar_EXE.bat**. También puedes ejecutar el fuente directamente.

The entry point is **Mega_Cd_Grabador_de_Video_Beta.py**. To build on Windows, install Python 3.11 x64, put FFmpeg and FFprobe on PATH, and run **Compilar_EXE.bat**. To run from source:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe Mega_Cd_Grabador_de_Video_Beta.py
```

- [Informe técnico de la versión 13](Informe_tecnico.md) · [CAVC upstream](https://github.com/akiyan/segacd-fmv-cavc) and its [MIT license](cavc/LICENSE)
- [Licencia de la aplicación](LICENSE) · [Avisos de terceros](THIRD_PARTY_NOTICES.md) · [Base del reproductor y conversor](https://github.com/Newbilius/SegaCD_VideoCD_Converter)
- Proyecto independiente de Sega. Usa vídeos propios o con autorización. / Independent project, unaffiliated with Sega. Use videos you own or have permission to convert.
