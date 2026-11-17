# Mega CD Estudio 2

**Crea tus propias películas y vídeos para tu Mega CD.**  
**Play your own videos on your Mega CD.**

[Español](#es) · [English](#en) · [Français](#fr) · [Português](#pt) · [Italiano](#it) · [日本語](#ja) · [简体中文](#zh) · [Deutsch](#de) · [Русский](#ru)

<a id="es"></a>
## Español

Mega CD Estudio 2 distribuye la **versión 14.1 de Mega CD Estudio**. Es una aplicación para Windows x64 que convierte vídeos en imágenes **ISO + CUE** arrancables para Sega Mega CD / Sega CD. Admite fuentes MKV, MP4, AVI, MOV y otros formatos reconocidos por FFmpeg. La interfaz está en español y toda la conversión se ejecuta localmente.

**[Descargar para Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

### Cómo crear un disco

1. Descarga y extrae el ZIP completo de la sección Releases. Abre **Mega CD Estudio 14.1.exe**. El ejecutable incluye los componentes de Windows necesarios y no requiere instalar Python.
2. Añade los vídeos en **Biblioteca**. En **Ajustes**, selecciona el códec y la región de tu consola. Para mantener el encuadre, elige conservar la proporción.
3. Para **CAVC** o **LORE**, instala Ubuntu 24.04 en WSL 2, completa su configuración inicial y ejecuta una vez **Preparar_CAVC.bat**. Mantén la carpeta **cavc** junto al EXE. Cinepak funciona directamente en Windows.
4. En **Disco y grabación**, selecciona la carpeta de salida y pulsa **Crear disco**. Conserva cada archivo CUE junto a su ISO. Abre el CUE en Kega Fusion o ares, o instálate ImgBurn para grabarlo en un CD-R.

| Códec | Cuándo usarlo |
| --- | --- |
| **Cinepak** | Reproductor nativo, menú START, capítulos y perfiles de nitidez. El modo de 24 FPS nítidos puede crear varios CD automáticamente cuando hace falta. |
| **CAVC** | Vídeo de hasta 320 × 224 y audio ADPCM mono. Ofrece 15, 24 o 30 FPS nominales en NTSC y 25 FPS nominales experimentales en PAL. |
| **LORE** | Para PAL. Adapta las actualizaciones de vídeo entre 15 y 25 FPS nominales y usa audio ADPCM mono a 18 kHz. El ajuste experimental de movimiento se puede activar en la interfaz. |

En CAVC y LORE, los capítulos son partes que se reproducen en orden dentro de cada CD. **Dividir en varios CD si la película no cabe** está activado por defecto. Si hacen falta varios discos, el programa crea carpetas **Disco 1 de N**, **Disco 2 de N**, etc., cada una con su ISO, CUE y **CAVC.json**, además de **LEEME_VARIOS_DISCOS.txt**. Graba el CUE de cada carpeta en un CD-R distinto. Cada disco arranca de forma independiente y vuelve a empezar al terminar. La duración, el caudal y los datos de arranque influyen en la capacidad final.

### Novedades y ajustes de la versión 14.1

- **Conversión de películas largas.** La extracción salta al tramo de cada capítulo y el audio se procesa una vez por trabajo. La aceleración automática utiliza CPU y GPU según la etapa y la compatibilidad del equipo.
- **Duración de cada capítulo.** En Ajustes puedes elegir **Automático**, 1, 2, 3, 5, 10 o 15 minutos. El límite automático de referencia es de 4.500 fotogramas, unos 3 minutos a 25 FPS. Con la vista previa MP4 activada, un vídeo de hasta 9.000 fotogramas puede quedar en un solo capítulo. Los capítulos largos consumen más memoria y el programa puede acortarlos según la memoria disponible en WSL. Entre capítulos hay una pausa de carga de varios segundos.
- **Cortar los capítulos en un cambio de escena.** Está activado por defecto. Busca cambios de plano o fundidos próximos al corte y puede ampliar el tramo hasta un 12 %.
- **Arranque de LORE.** Incluye correcciones de inicialización del lector y de comunicación durante la carga en Mega-CD 2 PAL. Genera una nueva ISO/CUE para incorporar las correcciones del reproductor a una conversión antigua.
- **Velocidad y seguimiento.** Conserva Numba y CUDA opcional, la estimación de partes y la vista previa MP4 de discos con varias partes. **Codificar hasta dos partes a la vez** es opcional y depende de los recursos disponibles. La interfaz avisa del espacio libre y muestra una estimación del tiempo restante.
- **Reanudar una conversión.** Se conservan las partes verificadas al cancelar. Repite el trabajo con los mismos ajustes para reutilizarlas. **Limpiar caché temporal** elimina los datos temporales que permiten esa reutilización.

En CAVC, **Perfil de codificación** ofrece **Rápido**, **Detallado** y **Nitidez**. LORE utiliza su perfil de nitidez automáticamente. **Reducir bloques en movimiento (experimental)** está desactivado inicialmente y permite probar un tratamiento adicional de las escenas con movimiento.

**Comprobar integridad del disco** verifica la estructura, los sectores y las huellas de las imágenes CAVC/LORE. Para revisar la reproducción utiliza **Probar en ares** o **Probar disco en Fusion**. En Cinepak, la comprobación completa de reproducción utiliza una copia compatible de Kega Fusion 3.64 y una BIOS de la región. Kega Fusion, ares, ImgBurn y las BIOS se obtienen por separado.

La primera conversión puede tardar más por la compilación inicial. Comprueba también el cambio de capítulo al probar el disco. Los capítulos de más de 3 minutos siguen pendientes de prueba en consola física. La compatibilidad puede variar según la consola, el vídeo y el disco.

<a id="en"></a>
## English

Mega CD Estudio 2 distributes **Mega CD Estudio version 14.1**, a Windows x64 application that turns your videos into bootable **ISO + CUE** images for Sega Mega CD / Sega CD. It uses FFmpeg to read MKV, MP4, AVI, MOV and other formats. The interface is in Spanish and encoding runs locally.

**[Download for Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Extract the complete ZIP and open **Mega CD Estudio 14.1.exe**. You can run the Windows build without installing Python.
2. Add your videos in **Biblioteca**. Choose a codec and console region in **Ajustes**. Select the option that preserves the aspect ratio to keep the full image.
3. **CAVC and LORE** require Ubuntu 24.04 on WSL 2. Finish the Ubuntu setup, run **Preparar_CAVC.bat** once and keep **cavc** next to the EXE. Cinepak runs directly on Windows.
4. Select an output folder under **Disco y grabación**, click **Crear disco**, and keep each CUE next to its ISO. Open the CUE in Kega Fusion or ares, or install ImgBurn to write a CD-R.

**Cinepak** offers chapter navigation, the START menu and a sharp 24 FPS option that can split a film across discs. **CAVC** supports up to 320 × 224 with mono ADPCM audio. **LORE** is a PAL option with 15–25 nominal video updates per second and 18 kHz mono ADPCM. CAVC and LORE chapters play in order within each disc. **Dividir en varios CD si la película no cabe** enables automatic disc splitting and is on by default. When needed, the program creates **Disco 1 de N**, **Disco 2 de N**, etc., each with its own ISO, CUE and **CAVC.json**, plus **LEEME_VARIOS_DISCOS.txt**. Burn each folder’s CUE onto a separate CD-R. Each disc boots independently and loops when finished.

### Version 14.1 settings and changes

- Extraction seeks directly to each chapter’s segment, and audio is processed once per job. Automatic acceleration uses CPU and GPU according to the stage and available hardware.
- **Duración de cada capítulo** offers **Automático**, 1, 2, 3, 5, 10 or 15 minutes. The automatic baseline is 4,500 frames, about 3 minutes at 25 FPS. With MP4 preview enabled, a video of up to 9,000 frames may remain a single chapter. Longer chapters need more memory and may be shortened to fit the memory available in WSL. A loading pause of several seconds occurs between chapters.
- **Cortar los capítulos en un cambio de escena** is on by default. It searches for nearby shot changes or fades and can extend a segment by up to 12%.
- LORE includes reader initialization and startup communication fixes for PAL Mega-CD 2. Generate a fresh ISO/CUE to apply the player fixes to an older conversion.
- Optional two-part parallel encoding, Numba/CUDA acceleration, part estimates, multipart MP4 previews and reuse of verified parts are retained. The interface adds free-space notices and an estimated remaining time. Repeat the same settings to resume a job. **Limpiar caché temporal** removes the temporary data used for reuse.

CAVC offers **Rápido**, **Detallado** and **Nitidez** encoding profiles. LORE selects its sharpness profile automatically. **Reducir bloques en movimiento (experimental)** is initially off and provides an optional motion-processing experiment.

CAVC/LORE integrity checks verify image structure, sectors and hashes. Review playback with **Probar en ares** or **Probar disco en Fusion**. Full Cinepak playback verification uses a compatible external Kega Fusion 3.64 and a region-matched BIOS. Emulators, BIOS files and ImgBurn are obtained separately. The first conversion may take longer while components compile. Test a chapter transition as well. Chapters longer than 3 minutes still await physical-console testing.


<a id="fr"></a>
## Français

Mega CD Estudio 2 distribue la **version 14.1 de Mega CD Estudio**. Cette application Windows x64 convertit vos vidéos en images amorçables **ISO + CUE** pour le Sega Mega CD / Sega CD. L’interface est en espagnol et le traitement s’effectue localement.

**[Télécharger pour Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Décompressez le ZIP complet et ouvrez **Mega CD Estudio 14.1.exe**. Python n’est pas nécessaire pour lancer le programme.
2. Ajoutez les vidéos dans **Biblioteca**, puis choisissez le codec et la région de votre console dans **Ajustes**. Conservez les proportions pour garder l’image entière.
3. Pour **CAVC** et **LORE**, configurez Ubuntu 24.04 dans WSL 2, exécutez une fois **Preparar_CAVC.bat** et laissez le dossier **cavc** à côté de l’EXE. Cinepak fonctionne directement sous Windows.
4. Choisissez le dossier de sortie dans **Disco y grabación**, puis cliquez sur **Crear disco**. Gardez chaque CUE à côté de son ISO. Ouvrez le CUE dans Kega Fusion ou ares, ou installez ImgBurn pour graver un CD-R.

**Cinepak** propose des chapitres, un menu START et un mode net à 24 i/s qui peut répartir un film sur plusieurs disques. **CAVC** monte à 320 × 224 avec du son ADPCM mono. **LORE** est destiné au PAL, avec 15 à 25 mises à jour d’image par seconde en cadence nominale et du son mono à 18 kHz. Les chapitres CAVC/LORE se lisent dans l’ordre sur chaque disque. **Dividir en varios CD si la película no cabe** active la répartition automatique sur plusieurs CD et est coché par défaut. Le programme crée au besoin les dossiers **Disco 1 de N**, **Disco 2 de N**, etc., avec leur ISO, CUE et **CAVC.json**, ainsi que **LEEME_VARIOS_DISCOS.txt**. Gravez le CUE de chaque dossier sur un CD-R distinct. Chaque disque démarre indépendamment et reprend au début en fin de lecture.

### Réglages et nouveautés de la version 14.1

- L’extraction accède directement au passage de chaque chapitre et l’audio est traité une fois par conversion. L’accélération automatique utilise le CPU et le GPU selon l’étape et le matériel disponible.
- **Duración de cada capítulo** propose **Automático**, 1, 2, 3, 5, 10 ou 15 minutes. La limite automatique de référence est de 4 500 images, soit environ 3 minutes à 25 i/s. Avec l’aperçu MP4 activé, une vidéo de 9 000 images au maximum peut rester en un seul chapitre. Les chapitres longs demandent davantage de mémoire et peuvent être raccourcis selon la mémoire disponible dans WSL. Une pause de chargement de plusieurs secondes sépare les chapitres.
- **Cortar los capítulos en un cambio de escena** est activé par défaut. Il recherche un changement de plan ou un fondu proche de la coupure, avec une prolongation possible de 12 %.
- LORE comprend des corrections d’initialisation du lecteur et de communication au démarrage sur Mega-CD 2 PAL. Régénérez l’ISO/CUE d’une ancienne conversion pour intégrer ces corrections.
- L’encodage parallèle optionnel de deux parties, Numba/CUDA, l’estimation des parties, l’aperçu MP4 et la reprise des parties vérifiées sont conservés. L’interface indique l’espace libre et estime le temps restant. Relancez les mêmes réglages pour reprendre. **Limpiar caché temporal** supprime les données temporaires utilisées pour cette reprise.

CAVC propose les profils **Rápido**, **Detallado** et **Nitidez**. LORE choisit automatiquement son profil de netteté. **Reducir bloques en movimiento (experimental)** est désactivé au départ et permet de tester un traitement supplémentaire du mouvement.

Le contrôle CAVC/LORE vérifie la structure, les secteurs et les empreintes des images. Pour examiner la lecture, utilisez **Probar en ares** ou **Probar disco en Fusion**. Le contrôle complet de Cinepak utilise un Kega Fusion 3.64 compatible et le BIOS de la bonne région. Émulateurs, BIOS et ImgBurn sont à obtenir séparément. La première conversion peut être plus longue pendant la compilation. Testez aussi le changement de chapitre. Les chapitres de plus de 3 minutes restent à vérifier sur une console physique.

<a id="pt"></a>
## Português

Mega CD Estudio 2 distribui a **versão 14.1 do Mega CD Estudio**. O programa para Windows x64 converte vídeos em imagens inicializáveis **ISO + CUE** para Sega Mega CD / Sega CD. A interface está em espanhol e o processamento é local.

**[Baixar para Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Extraia todo o ZIP e abra **Mega CD Estudio 14.1.exe**. Não é necessário instalar o Python.
2. Adicione vídeos em **Biblioteca** e escolha o codec e a região do console em **Ajustes**. Para manter a imagem inteira, preserve a proporção.
3. Para **CAVC** e **LORE**, configure o Ubuntu 24.04 no WSL 2, execute **Preparar_CAVC.bat** uma vez e mantenha a pasta **cavc** junto ao EXE. O Cinepak funciona diretamente no Windows.
4. Selecione a pasta de saída em **Disco y grabación** e clique em **Crear disco**. Mantenha cada CUE junto ao ISO correspondente. Abra o CUE no Kega Fusion ou no ares, ou instale o ImgBurn para gravar um CD-R.

O **Cinepak** oferece capítulos, menu START e um modo de 24 FPS que pode distribuir um filme por vários discos. O **CAVC** oferece até 320 × 224 com áudio ADPCM mono. O **LORE** é para PAL, com 15–25 atualizações de vídeo por segundo em cadência nominal e áudio mono de 18 kHz. Os capítulos CAVC/LORE são reproduzidos em ordem em cada disco. **Dividir en varios CD si la película no cabe** ativa a divisão automática em vários CDs e vem marcada. Quando necessário, o programa cria **Disco 1 de N**, **Disco 2 de N**, etc., cada pasta com ISO, CUE e **CAVC.json**, além de **LEEME_VARIOS_DISCOS.txt**. Grave o CUE de cada pasta em um CD-R separado. Cada disco inicia de forma independente e repete a reprodução ao terminar.

### Ajustes e novidades da versão 14.1

- A extração salta diretamente ao trecho de cada capítulo e o áudio é processado uma vez por trabalho. A aceleração automática usa CPU e GPU conforme a etapa e o equipamento.
- **Duración de cada capítulo** oferece **Automático**, 1, 2, 3, 5, 10 ou 15 minutos. O limite automático de referência é de 4.500 quadros, cerca de 3 minutos a 25 FPS. Com a prévia MP4 ativada, um vídeo de até 9.000 quadros pode ficar em um único capítulo. Capítulos longos exigem mais memória e podem ser encurtados de acordo com a memória disponível no WSL. Entre capítulos há uma pausa de carregamento de alguns segundos.
- **Cortar los capítulos en un cambio de escena** vem ativado. Procura mudanças de plano ou transições para preto próximas ao corte e pode ampliar o trecho em até 12%.
- LORE inclui correções de inicialização do leitor e de comunicação durante o carregamento no Mega-CD 2 PAL. Gere uma nova ISO/CUE de uma conversão antiga para incorporar essas correções.
- Continuam disponíveis a codificação opcional de duas partes em paralelo, Numba/CUDA, estimativas, prévia MP4 e reutilização de partes verificadas. A interface avisa sobre espaço livre e estima o tempo restante. Repita os mesmos ajustes para retomar. **Limpiar caché temporal** apaga os dados temporários usados na reutilização.

O CAVC oferece os perfis **Rápido**, **Detallado** e **Nitidez**. O LORE escolhe automaticamente seu perfil de nitidez. **Reducir bloques en movimiento (experimental)** vem desativado e permite experimentar um tratamento adicional do movimento.

A verificação de CAVC/LORE confere estrutura, setores e hashes das imagens. Revise a reprodução com **Probar en ares** ou **Probar disco en Fusion**. A verificação completa de Cinepak usa Kega Fusion 3.64 compatível e a BIOS da região correta. Emuladores, BIOS e ImgBurn são obtidos separadamente. A primeira conversão pode levar mais tempo durante a compilação. Teste também a mudança de capítulo. Capítulos com mais de 3 minutos ainda aguardam teste em console físico.

<a id="it"></a>
## Italiano

Mega CD Estudio 2 distribuisce la **versione 14.1 di Mega CD Estudio**. È un’applicazione per Windows x64 che converte i video in immagini avviabili **ISO + CUE** per Sega Mega CD / Sega CD. L’interfaccia è in spagnolo e l’elaborazione avviene sul computer.

**[Scarica per Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Estrai tutto il contenuto dello ZIP e apri **Mega CD Estudio 14.1.exe**. L’eseguibile non richiede Python.
2. Aggiungi i video in **Biblioteca**. In **Ajustes** seleziona il codec e la regione della console. Per mantenere l’inquadratura completa, conserva le proporzioni originali.
3. Per **CAVC** e **LORE**, configura Ubuntu 24.04 in WSL 2, avvia **Preparar_CAVC.bat** una volta e tieni la cartella **cavc** accanto all’EXE. Cinepak funziona direttamente su Windows.
4. Scegli la cartella di uscita in **Disco y grabación** e premi **Crear disco**. Tieni ogni CUE insieme alla relativa ISO. Apri il CUE in Kega Fusion o ares, oppure installa ImgBurn per scrivere un CD-R.

**Cinepak** offre capitoli, menu START e una modalità a 24 FPS che può ripartire un film su più dischi. **CAVC** arriva a 320 × 224 con audio ADPCM mono. **LORE** è pensato per PAL, con una frequenza nominale variabile di 15–25 aggiornamenti video al secondo e audio mono a 18 kHz. I capitoli CAVC/LORE vengono riprodotti in ordine su ciascun disco. **Dividir en varios CD si la película no cabe** abilita la suddivisione automatica su più CD ed è attivo per impostazione iniziale. Se necessario, il programma crea **Disco 1 de N**, **Disco 2 de N**, ecc., ciascuna cartella con ISO, CUE e **CAVC.json**, oltre a **LEEME_VARIOS_DISCOS.txt**. Masterizza il CUE di ogni cartella su un CD-R diverso. Ogni disco si avvia autonomamente e ricomincia alla fine.

### Impostazioni e novità della versione 14.1

- L’estrazione raggiunge direttamente il tratto di ogni capitolo e l’audio viene elaborato una volta per conversione. L’accelerazione automatica usa CPU e GPU secondo la fase e il dispositivo disponibile.
- **Duración de cada capítulo** offre **Automático**, 1, 2, 3, 5, 10 o 15 minuti. Il limite automatico di riferimento è di 4.500 fotogrammi, circa 3 minuti a 25 FPS. Con l’anteprima MP4 attiva, un video fino a 9.000 fotogrammi può restare in un solo capitolo. I capitoli lunghi richiedono più memoria e possono essere accorciati secondo la memoria disponibile in WSL. Tra i capitoli compare una pausa di caricamento di alcuni secondi.
- **Cortar los capítulos en un cambio de escena** è attivo inizialmente. Cerca cambi di inquadratura o dissolvenze vicini al punto di taglio e può estendere il tratto fino al 12%.
- LORE include correzioni all’inizializzazione del lettore e alla comunicazione durante l’avvio su Mega-CD 2 PAL. Genera una nuova ISO/CUE di una vecchia conversione per integrare queste correzioni.
- Restano disponibili l’elaborazione opzionale di due parti in parallelo, Numba/CUDA, stime, anteprima MP4 e riutilizzo delle parti verificate. L’interfaccia segnala lo spazio libero e stima il tempo rimanente. Ripeti le stesse impostazioni per riprendere. **Limpiar caché temporal** elimina i dati temporanei usati per il riutilizzo.

CAVC offre i profili **Rápido**, **Detallado** e **Nitidez**. LORE seleziona automaticamente il proprio profilo di nitidezza. **Reducir bloques en movimiento (experimental)** è inizialmente disattivato e permette di provare un trattamento aggiuntivo del movimento.

Il controllo CAVC/LORE verifica struttura, settori e hash delle immagini. Esamina la riproduzione con **Probar en ares** o **Probar disco en Fusion**. Per il controllo completo di Cinepak servono Kega Fusion 3.64 compatibile e il BIOS della regione corretta. Emulatori, BIOS e ImgBurn si ottengono separatamente. La prima conversione può durare di più durante la compilazione. Prova anche un cambio di capitolo. I capitoli oltre 3 minuti attendono ancora una verifica su console fisica.

<a id="ja"></a>
## 日本語

Mega CD Estudio 2 は **Mega CD Estudio バージョン 14.1** の配布用リポジトリです。Windows x64 で動画を Sega Mega CD / Sega CD 用の起動可能な **ISO + CUE** に変換します。画面表示はスペイン語で、変換はパソコン上で行われます。

**[Windows x64 版をダウンロード](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. ZIP をすべて展開し、**Mega CD Estudio 14.1.exe** を開きます。Python のインストールは不要です。
2. **Biblioteca** で動画を追加し、**Ajustes** でコーデックと本体のリージョンを選びます。映像全体を表示するには、アスペクト比を維持する設定を選びます。
3. **CAVC** と **LORE** を使う場合は、WSL 2 で Ubuntu 24.04 をセットアップし、**Preparar_CAVC.bat** を一度実行します。EXE と同じ場所に **cavc** フォルダーを置いてください。Cinepak は Windows 上で直接動作します。
4. **Disco y grabación** で出力先を選び、**Crear disco** を押します。CUE と対応する ISO を同じフォルダーに置きます。CUE は Kega Fusion や ares で開けます。CD-R に書き込む場合は ImgBurn を別途インストールしてください。

**Cinepak** はチャプターと START メニューを備え、24 FPS の高画質モードでは必要に応じて複数のディスクに分割できます。**CAVC** は最大 320 × 224 とモノラル ADPCM 音声に対応します。PAL 用の **LORE** は毎秒 15～25 回の公称映像更新と 18 kHz のモノラル ADPCM を使用します。CAVC と LORE の各チャプターは、各 CD 内で順番に再生されます。**Dividir en varios CD si la película no cabe** は複数 CD への自動分割で、初期状態で有効です。必要に応じて **Disco 1 de N**、**Disco 2 de N** などのフォルダーが作成され、それぞれに ISO、CUE、**CAVC.json** が入ります。全体の案内は **LEEME_VARIOS_DISCOS.txt** に保存されます。各フォルダーの CUE を、それぞれ別の CD-R に書き込んでください。各ディスクは単独で起動し、最後まで再生すると最初に戻ります。

### バージョン 14.1 の設定と変更点

- 各チャプターの開始位置へ直接移動して映像を抽出し、音声は変換作業全体で一度処理します。自動高速化では、処理段階と対応機器に応じて CPU と GPU を使います。
- **Duración de cada capítulo** では **Automático**、1、2、3、5、10、15 分を選べます。自動設定の基準は 4,500 フレームで、25 FPS では約 3 分です。MP4 プレビューを有効にすると、9,000 フレーム以内の動画が一つのチャプターになる場合があります。長いチャプターには多くのメモリが必要で、WSL の使用可能メモリに合わせて短縮される場合があります。チャプター間には数秒の読み込み待ちがあります。
- **Cortar los capítulos en un cambio de escena** は初期状態で有効です。分割位置の近くにあるカットやフェードを探し、最大 12% まで区間を延長できます。
- LORE には PAL Mega-CD 2 用のドライブ初期化と起動中の通信の修正が含まれます。以前の変換に修正済みプレーヤーを反映するには、新しい ISO/CUE を生成してください。
- 2 パートの同時処理、Numba/CUDA による高速化、パート数の見積もり、MP4 プレビュー、検証済みパートの再利用に対応します。同時処理は任意設定です。空き容量の通知と残り時間の推定も表示します。再開時は同じ設定を使います。**Limpiar caché temporal** は再利用に使う一時データを削除します。

CAVC のプロファイルは **Rápido**、**Detallado**、**Nitidez** です。LORE は自動的に鮮明さを重視するプロファイルを使います。**Reducir bloques en movimiento (experimental)** は初期状態で無効で、動きのある場面への追加処理を試せます。

CAVC/LORE の整合性確認では、イメージの構造、セクター、ハッシュを検証します。再生は **Probar en ares** または **Probar disco en Fusion** で確認します。Cinepak の全編再生の確認には、対応する Kega Fusion 3.64 とリージョンに合った BIOS が必要です。エミュレーター、BIOS、ImgBurn は別途入手してください。初回はコンパイルで時間がかかる場合があります。チャプターの切り替わりも確認してください。3 分を超えるチャプターの実機検証は今後の課題です。

<a id="zh"></a>
## 简体中文

Mega CD Estudio 2 提供 **Mega CD Estudio 第 14.1 版**。这款 Windows x64 应用可将视频转换为 Sega Mega CD / Sega CD 可启动的 **ISO + CUE** 光盘镜像。界面为西班牙语，转换在本机完成。

**[下载 Windows x64 版](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. 完整解压 ZIP，打开 **Mega CD Estudio 14.1.exe**。运行该程序无需安装 Python。
2. 在 **Biblioteca** 中添加视频，在 **Ajustes** 中选择编码方式和主机区域。想保留完整画面时，请选择保持宽高比。
3. 使用 **CAVC** 或 **LORE** 时，请先在 WSL 2 中配置 Ubuntu 24.04，运行一次 **Preparar_CAVC.bat**，并将 **cavc** 文件夹保留在 EXE 旁边。Cinepak 可直接在 Windows 中运行。
4. 在 **Disco y grabación** 中设置输出目录，点击 **Crear disco**。将每个 CUE 与对应 ISO 放在同一文件夹。使用 Kega Fusion 或 ares 打开 CUE，或另行安装 ImgBurn 将其刻录到 CD-R。

**Cinepak** 支持章节、START 菜单和可自动分盘的 24 FPS 清晰模式。**CAVC** 支持最高 320 × 224 分辨率和单声道 ADPCM 音频。PAL 专用的 **LORE** 使用标称每秒 15～25 次画面更新和 18 kHz 单声道 ADPCM 音频。CAVC 和 LORE 的各个章节在每张光盘内依次播放。**Dividir en varios CD si la película no cabe** 表示自动分配到多张 CD，默认开启。需要分盘时，程序会创建 **Disco 1 de N**、**Disco 2 de N** 等文件夹，每个文件夹包含各自的 ISO、CUE 和 **CAVC.json**，并生成总说明 **LEEME_VARIOS_DISCOS.txt**。请把每个文件夹的 CUE 分别刻录到不同的 CD-R。每张光盘均可独立启动，播放结束后循环播放。

### 14.1 版的设置与改进

- 提取视频时直接定位到各章节的起始位置，音频在每次转换任务中处理一次。自动加速会根据处理阶段和设备兼容性使用 CPU 与 GPU。
- **Duración de cada capítulo** 可选 **Automático**、1、2、3、5、10 或 15 分钟。自动设置的基准为 4,500 帧，在 25 FPS 下约为 3 分钟。启用 MP4 预览后，不超过 9,000 帧的视频可能保留为一个章节。较长章节需要更多内存，程序可能根据 WSL 的可用内存缩短章节。章节之间会有数秒的加载停顿。
- **Cortar los capítulos en un cambio de escena** 默认开启。它会寻找分割点附近的镜头切换或渐变，并可将片段延长最多 12%。
- LORE 包含 PAL Mega-CD 2 的光驱初始化和启动通信修正。要将修正后的播放器应用到旧转换结果，请重新生成 ISO/CUE。
- 保留可选的双分段并行编码、Numba/CUDA 加速、分段估算、MP4 预览和已验证分段的复用。界面增加磁盘空间提醒和预计剩余时间。恢复任务时使用相同设置。**Limpiar caché temporal** 会删除用于复用的临时数据。

CAVC 提供 **Rápido**、**Detallado** 和 **Nitidez** 编码配置。LORE 自动使用其清晰度配置。**Reducir bloques en movimiento (experimental)** 默认关闭，可用于尝试额外的运动画面处理。

CAVC/LORE 的完整性检查验证镜像结构、扇区和哈希值。请使用 **Probar en ares** 或 **Probar disco en Fusion** 检查播放情况。Cinepak 的完整播放验证需要兼容的 Kega Fusion 3.64 和相应区域的 BIOS。模拟器、BIOS 和 ImgBurn 需另行获取。首次转换可能因编译而耗时更长。测试时也请检查章节切换。超过 3 分钟的章节仍有待实机验证。

<a id="de"></a>
## Deutsch

Mega CD Estudio 2 stellt **Mega CD Estudio Version 14.1** bereit. Die Windows-x64-Anwendung wandelt Videos in startfähige **ISO- und CUE-Dateien** für Sega Mega CD / Sega CD um. Die Oberfläche ist auf Spanisch und die Verarbeitung erfolgt lokal.

**[Für Windows x64 herunterladen](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Entpacke das vollständige ZIP und starte **Mega CD Estudio 14.1.exe**. Python ist für das fertige Programm nicht erforderlich.
2. Füge Videos in **Biblioteca** hinzu und wähle Codec und Konsolenregion unter **Ajustes**. Behalte das Seitenverhältnis bei, um das vollständige Bild zu erhalten.
3. Für **CAVC** und **LORE** richtest du Ubuntu 24.04 unter WSL 2 ein und führst einmal **Preparar_CAVC.bat** aus. Der Ordner **cavc** muss neben der EXE liegen. Cinepak läuft direkt unter Windows.
4. Wähle den Ausgabeordner unter **Disco y grabación** und klicke auf **Crear disco**. Lass jede CUE bei ihrer ISO. Öffne die CUE in Kega Fusion oder ares. Zum Brennen auf CD-R kannst du ImgBurn separat installieren.

**Cinepak** bietet Kapitel, ein START-Menü und einen 24-FPS-Modus, der lange Filme auf mehrere CDs verteilen kann. **CAVC** unterstützt bis zu 320 × 224 und ADPCM-Monoton. **LORE** ist für PAL gedacht und aktualisiert das Bild nominell 15- bis 25-mal pro Sekunde bei 18 kHz Monoton. Die CAVC/LORE-Kapitel werden auf jeder CD nacheinander abgespielt. **Dividir en varios CD si la película no cabe** aktiviert die automatische Verteilung auf mehrere CDs und ist zunächst eingeschaltet. Bei Bedarf entstehen die Ordner **Disco 1 de N**, **Disco 2 de N** usw., jeweils mit ISO, CUE und **CAVC.json**, sowie die Übersicht **LEEME_VARIOS_DISCOS.txt**. Brenne die CUE jedes Ordners auf eine eigene CD-R. Jede CD startet eigenständig und beginnt am Ende erneut.

### Einstellungen und Neuerungen in Version 14.1

- Die Extraktion springt direkt zum Abschnitt jedes Kapitels. Audio wird einmal pro Auftrag verarbeitet. Die automatische Beschleunigung verwendet CPU und GPU je nach Verarbeitungsschritt und verfügbarer Hardware.
- **Duración de cada capítulo** bietet **Automático**, 1, 2, 3, 5, 10 oder 15 Minuten. Der automatische Richtwert liegt bei 4.500 Frames, etwa 3 Minuten bei 25 FPS. Mit aktivierter MP4-Vorschau kann ein Video mit bis zu 9.000 Frames ein einzelnes Kapitel bleiben. Lange Kapitel brauchen mehr Speicher und können an den verfügbaren WSL-Speicher angepasst werden. Zwischen Kapiteln liegt eine Ladepause von mehreren Sekunden.
- **Cortar los capítulos en un cambio de escena** ist zunächst eingeschaltet. Es sucht nahe Szenenwechsel oder Überblendungen und kann einen Abschnitt um bis zu 12 % verlängern.
- LORE enthält Korrekturen für die Laufwerksinitialisierung und die Kommunikation beim Start auf dem PAL Mega-CD 2. Erstelle für ältere Konvertierungen eine neue ISO/CUE, um die Playerkorrekturen zu übernehmen.
- Optionales paralleles Kodieren von zwei Teilen, Numba/CUDA, Schätzungen, MP4-Vorschau und Wiederverwendung geprüfter Teile bleiben verfügbar. Die Oberfläche meldet freien Speicherplatz und schätzt die Restzeit. Verwende zum Fortsetzen dieselben Einstellungen. **Limpiar caché temporal** löscht die dafür verwendeten temporären Daten.

CAVC bietet die Profile **Rápido**, **Detallado** und **Nitidez**. LORE wählt sein Schärfeprofil automatisch. **Reducir bloques en movimiento (experimental)** ist zunächst ausgeschaltet und ermöglicht zusätzliche Versuche zur Bewegungsverarbeitung.

Die CAVC/LORE-Prüfung kontrolliert Struktur, Sektoren und Hashwerte der Images. Prüfe die Wiedergabe mit **Probar en ares** oder **Probar disco en Fusion**. Die vollständige Cinepak-Wiedergabeprüfung verwendet ein kompatibles Kega Fusion 3.64 und ein BIOS der richtigen Region. Emulatoren, BIOS und ImgBurn sind separat erhältlich. Die erste Konvertierung kann wegen der Kompilierung länger dauern. Prüfe auch einen Kapitelwechsel. Kapitel über 3 Minuten stehen noch zur Prüfung auf echter Hardware aus.

<a id="ru"></a>
## Русский

В репозитории Mega CD Estudio 2 публикуется **Mega CD Estudio версии 14.1**. Программа для Windows x64 преобразует видео в загрузочные образы **ISO + CUE** для Sega Mega CD / Sega CD. Интерфейс на испанском языке, обработка выполняется на компьютере пользователя.

**[Скачать для Windows x64](https://github.com/xXxLorexXx/Mega-CD-Estudio-2/releases/latest)**

1. Распакуйте весь ZIP-архив и запустите **Mega CD Estudio 14.1.exe**. Установка Python для готового приложения не нужна.
2. Добавьте видео в **Biblioteca**, а в **Ajustes** выберите кодек и регион консоли. Чтобы сохранить кадр целиком, выберите сохранение пропорций.
3. Для **CAVC** и **LORE** настройте Ubuntu 24.04 в WSL 2, один раз запустите **Preparar_CAVC.bat** и оставьте папку **cavc** рядом с EXE. Cinepak работает непосредственно в Windows.
4. В разделе **Disco y grabación** выберите папку вывода и нажмите **Crear disco**. Храните каждый CUE вместе с его ISO. Откройте CUE в Kega Fusion или ares либо установите ImgBurn для записи на CD-R.

**Cinepak** поддерживает главы, меню START и режим 24 FPS, в котором фильм можно автоматически распределить по нескольким дискам. **CAVC** поддерживает разрешение до 320 × 224 и монофонический звук ADPCM. **LORE** предназначен для PAL, обновляет изображение с номинальной частотой 15–25 раз в секунду и использует монофонический ADPCM 18 кГц. Главы CAVC/LORE воспроизводятся по порядку на каждом диске. Опция **Dividir en varios CD si la película no cabe** автоматически распределяет фильм по нескольким CD и включена по умолчанию. При необходимости создаются папки **Disco 1 de N**, **Disco 2 de N** и т. д., каждая со своими ISO, CUE и **CAVC.json**, а также общий файл **LEEME_VARIOS_DISCOS.txt**. Запишите CUE из каждой папки на отдельный CD-R. Каждый диск запускается самостоятельно и повторяет воспроизведение после завершения.

### Настройки и изменения в версии 14.1

- Извлечение видео сразу переходит к участку каждой главы, а звук обрабатывается один раз за задание. Автоматическое ускорение использует CPU и GPU с учётом этапа обработки и возможностей оборудования.
- **Duración de cada capítulo** предлагает **Automático**, 1, 2, 3, 5, 10 или 15 минут. Базовый автоматический предел составляет 4500 кадров, около 3 минут при 25 FPS. При включённом MP4-предпросмотре видео до 9000 кадров может остаться одной главой. Длинные главы требуют больше памяти и могут быть сокращены с учётом доступной памяти WSL. Между главами возникает пауза загрузки в несколько секунд.
- **Cortar los capítulos en un cambio de escena** включено по умолчанию. Программа ищет смену плана или затемнение рядом с точкой разделения и может увеличить участок до 12%.
- LORE содержит исправления инициализации привода и обмена данными при запуске на PAL Mega-CD 2. Чтобы применить исправленный проигрыватель к старой конвертации, создайте новую ISO/CUE.
- Сохранены дополнительное параллельное кодирование двух частей, Numba/CUDA, оценка числа частей, MP4-предпросмотр и повторное использование проверенных частей. Интерфейс сообщает о свободном месте и оценивает оставшееся время. Для продолжения используйте те же настройки. **Limpiar caché temporal** удаляет временные данные, используемые для повторного использования.

CAVC предлагает профили **Rápido**, **Detallado** и **Nitidez**. LORE автоматически выбирает свой профиль чёткости. **Reducir bloques en movimiento (experimental)** изначально выключено и позволяет опробовать дополнительную обработку движения.

Проверка CAVC/LORE сверяет структуру, секторы и хеши образов. Проверяйте воспроизведение через **Probar en ares** или **Probar disco en Fusion**. Для полной проверки Cinepak нужны совместимый Kega Fusion 3.64 и BIOS нужного региона. Эмуляторы, BIOS и ImgBurn предоставляются отдельно. Первая конвертация может занять больше времени из-за компиляции. Проверьте также переход между главами. Главы длиннее 3 минут ещё ожидают проверки на физической консоли.

## Código fuente, compilación y créditos · Source, build and credits

El punto de entrada es **Mega_Cd_Grabador_de_Video_Beta.py**. Para compilar en Windows usa Python 3.11 x64, FFmpeg y FFprobe en el PATH, y ejecuta **Compilar_EXE.bat**. También puedes ejecutar el fuente directamente.

The entry point is **Mega_Cd_Grabador_de_Video_Beta.py**. To build on Windows, install Python 3.11 x64, put FFmpeg and FFprobe on PATH, and run **Compilar_EXE.bat**. To run from source:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe Mega_Cd_Grabador_de_Video_Beta.py
```

- [Informe técnico de referencia (versión 13)](Informe_tecnico.md) · [CAVC upstream](https://github.com/akiyan/segacd-fmv-cavc) and its [MIT license](cavc/LICENSE)
- [Licencia de la aplicación](LICENSE) · [Avisos de terceros](THIRD_PARTY_NOTICES.md) · [Base del reproductor y conversor](https://github.com/Newbilius/SegaCD_VideoCD_Converter)
- Proyecto independiente de Sega. Usa vídeos propios o con autorización. / Independent project, unaffiliated with Sega. Use videos you own or have permission to convert.
