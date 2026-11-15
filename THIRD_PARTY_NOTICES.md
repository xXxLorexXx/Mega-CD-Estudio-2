# Third-party notices / Avisos de terceros

This repository and its release do not include commercial films, console BIOS files, emulator ROMs, Kega Fusion or ImgBurn. / Este repositorio y su distribución no incluyen películas comerciales, BIOS, ROM, Kega Fusion ni ImgBurn.

## Native player and converter base / Reproductor nativo y base

- Upstream: https://github.com/Newbilius/SegaCD_VideoCD_Converter
- Resource comparison baseline: commit `ced816ac6c8df7907652f89031b2ea9269cfb832`.
- The upstream GPL v2 text is preserved in `LICENSE`. Native player source and historical resources are available in the upstream repository, including `sources_of_information`.
- `megacd_assets.py` retains the original native resources, with sizes and SHA-256 hashes in `Procedencia_recursos.json`; these are player resources, not a console BIOS.
- The current Python source, regression reference sources and build scripts are included here. Historical authorship and third-party rights remain with their respective holders.

## FFmpeg / FFprobe

The Windows executable bundles FFmpeg and FFprobe from the Gyan full build installed on the build machine (8.1.1). They run as separate processes. License text is in `LICENSE_FFMPEG.txt`; build configuration is in `FFMPEG_BUILD.txt`.

- Project and source: https://ffmpeg.org/ and https://git.ffmpeg.org/ffmpeg.git
- Windows build provider, build details and source links: https://www.gyan.dev/ffmpeg/builds/
- FFmpeg release source: https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz
- The complete build has its own third-party components and GPL terms. Their licenses are not replaced by the application license. For rebuilding the bundled utilities, use the provider's corresponding build configuration and dependency source links.

## NVIDIA NVRTC

NVRTC runtime libraries from `nvidia-cuda-nvrtc-cu12` 12.9.86 are bundled: `nvrtc64_120_0.dll`, `nvrtc64_120_0.alt.dll` and `nvrtc-builtins64_129.dll` (including package-local copies collected by PyInstaller). They retain NVIDIA's license in `LICENSE_NVIDIA_NVRTC.txt`; the application license does not cover these libraries. No NVIDIA driver is bundled. GPU support is optional.

- Package: https://pypi.org/project/nvidia-cuda-nvrtc-cu12/12.9.86/
- Documentation and terms: https://docs.nvidia.com/cuda/nvrtc/ and https://docs.nvidia.com/cuda/eula/

## Python runtime and libraries

The frozen executable also contains Python 3.11, Tcl/Tk, NumPy, Numba, llvmlite and their runtime dependencies. PyInstaller packages the executable. Their notices are collected in `DEPENDENCY_LICENSES.txt`; dependency versions are pinned in `requirements.txt`.

- Python: https://www.python.org/ — PSF license; Tcl/Tk retains its own notices.
- NumPy: https://numpy.org/ — BSD license, with bundled library notices.
- Numba: https://numba.pydata.org/ — BSD license.
- llvmlite: https://github.com/numba/llvmlite — BSD license and LLVM notices.
- PyInstaller: https://pyinstaller.org/ — GPL with its bootloader exception.

## External optional applications / Aplicaciones externas opcionales

ImgBurn is **not redistributed**. Install it separately from https://www.imgburn.com/ and comply with its terms. Creating ISO/CUE files does not require it.

Kega Fusion and any legally obtained BIOS required by the emulator must be supplied by the user. Neither is distributed here.

ImgBurn **no se redistribuye**: se instala aparte desde su web oficial. Kega Fusion y las BIOS necesarias también debe aportarlos el usuario.

## CAVC and LORE / CAVC y LORE

The CAVC player and encoder are derived from akiyan/segacd-fmv-cavc. The MIT
license is in `cavc/LICENSE`, and the upstream references are documented in
`cavc/vendor/README.md`. The small boot resources in `cavc/boot` are needed
to build the discs. Console BIOS files are supplied separately by the user.

El reproductor y codificador CAVC proceden de akiyan/segacd-fmv-cavc. Su
licencia MIT está en `cavc/LICENSE` y las referencias de terceros se
documentan en `cavc/vendor/README.md`.
