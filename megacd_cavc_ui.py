"""CAVC controls, sharing the original studio layout and job status."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from cavc.tools.astra_capacity import (CD_BYTES, MAX_PART_FRAMES, discs as disc_count,
                                       estimate, part_limit, part_memory)
from megacd_cavc import (
    CavcCancelled,
    cavc_cache_bytes,
    clear_cavc_cache,
    convert_cavc,
    read_cavc_info,
)
from megacd_studio_ui import Flow


class CavcStudio:
    def _build_cavc_settings(self, grid, disc):
        self.codec_var = tk.StringVar(value="Cinepak")
        self._field(disc, "Códec del disco", self.codec_var, ["Cinepak", "CAVC", "LORE"])
        card = self._card(grid, "CAVC · vídeo a pantalla completa")
        self.cavc_card = card
        grid.add(card)
        self.cavc_tip = self._wrap(card, style="Body.TLabel",
                                  text="Selecciona CAVC en Formato y capacidad para usar estas opciones.")
        self.cavc_tip.pack(fill="x", pady=(0, self.px(9)))
        self.cavc_size = tk.StringVar(value="320 × 224")
        self._field(card, "Resolución de imagen", self.cavc_size, ["320 × 224", "320 × 192", "256 × 192"])
        self.cavc_fps = tk.StringVar(value="30")
        _, self.cavc_fps_control = self._field(card, "Fotogramas por segundo", self.cavc_fps,
                                             ["15", "24", "30"])
        self.cavc_fit = tk.StringVar(value="Conservar proporción")
        self._field(card, "Encuadre", self.cavc_fit, ["Conservar proporción", "Recortar para llenar"])
        self.cavc_palette = tk.StringVar(value="MOSAIC-GM")
        self._field(card, "Paletas dinámicas", self.cavc_palette, ["MOSAIC-GM", "STL4"])
        self.cavc_parts = tk.StringVar(value="Automático")
        self._field(card, "Duración de cada capítulo", self.cavc_parts,
                    ["Automático", "1 min", "2 min", "3 min", "5 min", "10 min", "15 min"])
        self.cavc_parts_tip = self._wrap(card, style="Body.TLabel")
        self.cavc_parts_tip.pack(fill="x", pady=(0, self.px(8)))
        self.cavc_scene = tk.BooleanVar(value=True)
        self._check(card, "Cortar los capítulos en un cambio de escena", self.cavc_scene)
        self.cavc_multi = tk.BooleanVar(value=True)
        self._check(card, "Dividir en varios CD si la película no cabe", self.cavc_multi)
        self.cavc_parallel = tk.BooleanVar(value=False)
        self._check(card, "Codificar hasta dos partes a la vez", self.cavc_parallel)
        self.cavc_detail = tk.BooleanVar(value=False)
        self._check(card, "Reducir bloques en movimiento (experimental)", self.cavc_detail)
        self.cavc_speed = tk.StringVar(value="Rápido")
        self.cavc_profile_row, _ = self._field(
            card, "Perfil de codificación", self.cavc_speed, ["Rápido", "Detallado", "Nitidez"])
        self.cavc_profile_tip = self._wrap(card, style="Body.TLabel",
                   text="Rápido reduce el análisis de movimiento y la prelectura. "
                        "Detallado suaviza ruido y tramas. Nitidez mantiene el análisis completo y conserva más textura.")
        self.cavc_profile_tip.pack(fill="x", pady=(0, self.px(8)))
        self.cavc_clip = tk.StringVar(value="5")
        self.cavc_clip_row, _ = self._field(
            card, "Duración de la prueba", self.cavc_clip, ["3", "5", "10", "15"])
        self.cavc_mp4 = tk.BooleanVar(value=False)
        self._check(card, "Exportar vista previa MP4", self.cavc_mp4)
        cache_row = Flow(card, self.px(7))
        cache_row.pack(fill="x", pady=(self.px(6), 0))
        self.cavc_cache_btn = self._action(
            cache_row, "Limpiar caché temporal", self._clear_cavc_cache_ui, "Quiet.TButton"
        )
        self._watched_settings.extend((
            self.codec_var, self.cavc_size, self.cavc_fps, self.cavc_fit,
            self.cavc_palette, self.cavc_parts, self.cavc_speed, self.cavc_clip,
            self.cavc_mp4, self.cavc_parallel, self.cavc_detail,
            self.cavc_scene, self.cavc_multi,
        ))

    def _sync_preview_label(self, *_):
        self.disc_format_var.set(f"ISO / CUE · {self.codec_var.get()}")
        if hasattr(self, "preview_btn"):
            seconds = self.cavc_clip.get() if self._uses_cavc() else "15"
            self.preview_btn.configure(text=f"Prueba de {seconds} s")

    def _sync_cavc_region(self):
        pal = self._get_selected_region() == "E"
        lore = self._uses_lore()
        if lore != getattr(self, "_show_lore_profile", False):
            self._show_lore_profile = lore
            if lore:
                self.cavc_profile_row.pack_forget()
                self.cavc_profile_tip.pack_forget()
            else:
                self.cavc_profile_row.pack(
                    fill="x", pady=(0, self.px(10)), before=self.cavc_clip_row)
                self.cavc_profile_tip.pack(
                    fill="x", pady=(0, self.px(8)), before=self.cavc_clip_row)
        self.cavc_card.title_label.configure(text="LORE · vídeo adaptativo" if lore else
                                 "CAVC · vídeo a pantalla completa")
        self._update_parts_tip()
        if lore:
            self.cavc_fps_control.configure(values=("15–25 (PAL)",), state="disabled",
                                           textvariable="")
            self.cavc_fps_control.set("15–25 (PAL)")
            self.cavc_tip.configure(text=(
                "PAL · 15–25 FPS nominales · audio ADPCM mono a 18 kHz."
                if pal else "Selecciona Europa / España (PAL) para convertir con LORE."))
            return
        self.cavc_fps_control.configure(state="readonly", textvariable=self.cavc_fps)
        rates = ("25",) if pal else ("15", "24", "30")
        self.cavc_fps_control.configure(values=rates)
        if self.cavc_fps.get() not in rates:
            self.cavc_fps.set(rates[-1])
        self.cavc_tip.configure(text=(
            "PAL experimental · 25 FPS nominales · audio ADPCM mono a 22,05 kHz."
            if pal else "NTSC · 15, 24 o 30 FPS · audio ADPCM mono a 22,05 kHz."
        ))

    def _chapter_minutes(self):
        text = self.cavc_parts.get().split()[0]
        return int(text) if text.isdigit() else 0

    def _update_parts_tip(self):
        if not hasattr(self, "cavc_parts_tip"):
            return
        pal = self._get_selected_region() == "E"
        nominal = 25 if pal or self._uses_lore() else int(self.cavc_fps.get() or 30)
        minutes = self._chapter_minutes()
        frames = part_limit(nominal, minutes)
        shown, rest = divmod(frames // nominal, 60)
        text = (f"Capítulos de hasta {shown}:{rest:02d} a {nominal} FPS. Entre capítulo y capítulo "
                "el Mega CD se detiene unos 4 s en negro para cargar el siguiente.")
        if frames > MAX_PART_FRAMES:
            need = part_memory(frames) / 1024**3
            text += (f" Cada capítulo necesita unos {need:.0f} GB de memoria al codificar; "
                     "si WSL no tiene tanta, el programa acortará los capítulos al máximo "
                     "que quepa y lo indicará al empezar.")
        self.cavc_parts_tip.configure(text=text)

    def _uses_cavc(self):
        return hasattr(self, "codec_var") and self.codec_var.get() in ("CAVC", "LORE")

    def _uses_lore(self):
        return self.codec_var.get() == "LORE"

    def _refresh_cavc_tree(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.tree.heading("chapters", text="Partes")
        lore = self._uses_lore()
        fps = 25 if lore else int(self.cavc_fps.get() or 30)
        part_min = self._chapter_minutes()
        self._update_parts_tip()
        total_duration = sum(video["duration"] for video in self.videos)
        total_parts, total_bytes = estimate(total_duration, fps, part_min, self.cavc_mp4.get())
        total_mb = total_bytes / 1024**2 if self.videos else 0.0
        for index, video in enumerate(self.videos):
            dur = video["duration"]
            minutes, seconds = divmod(dur, 60)
            video_parts, video_bytes = estimate(dur, fps, part_min, self.cavc_mp4.get())
            video["segments"] = video_parts
            parts_str = "1 parte" if video_parts == 1 else f"{video_parts} partes"
            self.tree.insert("", "end", iid=str(index), values=(
                index + 1, video["name"], f'{video["width"]}x{video["height"]}',
                f"{int(minutes):02}:{seconds:04.1f}", parts_str,
                f"{video_bytes / 1024**2:.1f} MiB", video["path"]))
        self.tree.selection_set(tuple(item for item in selected if self.tree.exists(item)))
        accel = "CPU" if self.acceleration_var.get().startswith("CPU") else "GPU + CPU automáticas"
        codec = self.codec_var.get()
        rate = "15–25" if lore else self.cavc_fps.get()
        profile = "Movimiento adaptativo" if lore else self.cavc_speed.get()
        pct = (total_bytes / CD_BYTES) * 100.0 if self.videos else 0.0
        over = ""
        if pct > 100:
            over = (f" · {disc_count(total_bytes)} CD" if self.cavc_multi.get()
                    else " · EXCEDE 1 CD")
        self.cap_label.configure(text=(
            f"{codec} · {self.cavc_size.get()} · {rate} FPS · est. {total_mb:.1f} / {CD_BYTES/1024**2:.1f} MiB ({pct:.1f}%) · {total_parts if self.videos else 0} parte(s){over}"))
        self.picture_summary.set(
            f"{self.cavc_fit.get()} · {self.cavc_palette.get()} · {profile}\n{accel}")
        self._update_library_summary(pct)

    def clear_cavc_temp_cache(self, interactive=True):
        if self.is_converting:
            if interactive:
                messagebox.showwarning(
                    "Conversión en curso",
                    "Espera a que termine o cancela la conversión antes de limpiar la caché temporal.",
                )
            return 0
        used_bytes = cavc_cache_bytes()
        used_mb = used_bytes / 1024**2
        if interactive and used_bytes > 0:
            if not messagebox.askyesno(
                "Limpiar caché temporal",
                f"La caché temporal de CAVC / LORE ocupa {used_mb:.1f} MiB.\n\n"
                "Se eliminarán los archivos temporales y las partes en curso no terminadas. ¿Continuar?",
            ):
                return 0
        freed = clear_cavc_cache()
        freed_mb = freed / 1024**2
        self.status_var.set(
            f"Caché temporal limpia ({freed_mb:.1f} MiB liberados)"
            if freed > 0
            else "La caché temporal ya estaba limpia"
        )
        if interactive:
            messagebox.showinfo(
                "Caché temporal",
                f"Se han liberado {freed_mb:.1f} MiB de la caché temporal."
                if freed > 0
                else "No había archivos temporales acumulados en la caché de CAVC / LORE.",
            )
        return freed

    def _clear_cavc_cache_ui(self):
        if self.is_converting:
            messagebox.showwarning(
                "Conversión en curso",
                "Espera a que termine o cancela la conversión antes de limpiar la caché temporal.",
            )
            return
        used_bytes = cavc_cache_bytes()
        used_mb = used_bytes / 1024**2
        if used_bytes > 0 and not messagebox.askyesno(
            "Limpiar caché temporal",
            f"La caché temporal de CAVC / LORE ocupa {used_mb:.1f} MiB.\n\n"
            "Se eliminarán los archivos temporales y las partes en curso no terminadas. ¿Continuar?",
        ):
            return
        if hasattr(self, "cavc_cache_btn"):
            self.cavc_cache_btn.configure(state="disabled")
        self.status_var.set("Limpiando caché temporal de CAVC / LORE…")

        def worker():
            freed = clear_cavc_cache()
            freed_mb = freed / 1024**2

            def finish():
                if getattr(self, "_closing", False):
                    return
                if hasattr(self, "cavc_cache_btn") and not self.is_converting:
                    self.cavc_cache_btn.configure(state="normal")
                self.status_var.set(
                    f"Caché temporal limpia ({freed_mb:.1f} MiB liberados)"
                    if freed > 0
                    else "La caché temporal ya estaba limpia"
                )
                messagebox.showinfo(
                    "Caché temporal",
                    f"Se han liberado {freed_mb:.1f} MiB de la caché temporal."
                    if freed > 0
                    else "No había archivos temporales acumulados en la caché de CAVC / LORE.",
                )

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _start_cavc(self, preview=False):
        if self.is_converting:
            return
        codec = self.codec_var.get()
        lore = self._uses_lore()
        if lore and self._get_selected_region() != "E":
            messagebox.showwarning("Región de LORE", "Selecciona Europa / España (PAL) en Ajustes.")
            return
        if not self.videos:
            messagebox.showwarning("Añade algún vídeo", f"Añade al menos un vídeo para convertir a {codec}.")
            return
        sources = [{"path": video["path"]} for video in self.videos]
        if preview:
            selection = self.tree.selection()
            index = int(selection[0]) if selection else 0
            seconds = min(int(self.cavc_clip.get()), self.videos[index]["duration"])
            sources = [{"path": sources[index]["path"], "length": seconds}]
        else:
            seconds = sum(video["duration"] for video in self.videos)
        output = self.out_dir_var.get().strip()
        if not output and not preview:
            messagebox.showwarning("Carpeta de salida", "Selecciona una carpeta de destino.")
            return
        if preview:
            output = str(Path(tempfile.gettempdir()) / f"MegaCdEstudio_{codec}")
        fps = 25 if lore else int(self.cavc_fps.get())
        part_minutes = self._chapter_minutes() if not preview else 0
        multi_disc = not preview and self.cavc_multi.get()
        if not preview:
            _, est_bytes = estimate(seconds, fps, part_minutes, self.cavc_mp4.get())
            if est_bytes > CD_BYTES and not multi_disc:
                messagebox.showwarning(
                    f"Excede la capacidad del CD ({codec})",
                    f"La estimación para {seconds/60:.1f} minutos supera un CD de 80 minutos. "
                    "Activa «Dividir en varios CD si la película no cabe» en Ajustes.")
                return
            if est_bytes > CD_BYTES and not messagebox.askyesno(
                    f"Varios CD ({codec})",
                    f"La película ({seconds/60:.0f} min) no cabe en un CD. Se crearán unos "
                    f"{disc_count(est_bytes)} discos, cada uno en su carpeta «Disco N de M». ¿Continuar?"):
                return
            if not self._check_free_space(seconds, fps):
                return
        auto_burn = not preview and self.auto_burn_var.get()
        settings = {
            "codec": codec,
            "region": {"E": "eu", "U": "us", "J": "jp"}[self._get_selected_region()],
            "fps": fps, "size": list(map(int, self.cavc_size.get().split(" × "))),
            "fit": "crop" if self.cavc_fit.get().startswith("Recortar") else "pad",
            "palette": self.cavc_palette.get().lower(),
            "cold_cap": 200 if lore else {15: 260, 24: 195, 25: 185, 30: 185}[fps],
            "preview": preview,
            "profile": "sharp" if lore else {"Rápido": "fast", "Detallado": "detailed", "Nitidez": "sharp"}[self.cavc_speed.get()],
            "acceleration": "cpu" if self.acceleration_var.get().startswith("CPU") else "auto",
            "preview_mp4": preview or self.cavc_mp4.get(),
            "part_minutes": part_minutes,
            "part_workers": 2 if self.cavc_parallel.get() else 1,
            "detail_motion": self.cavc_detail.get(),
            "scene_split": not preview and self.cavc_scene.get(),
            "multi_disc": multi_disc,
        }
        self._conversion_cancel_event.clear()
        self.is_converting = self._conversion_running = True
        self.pbar.stop()
        self.pbar.configure(mode="determinate")
        self.progress_var.set(1)
        started = time.monotonic()
        label = f"Prueba {codec}" if preview else f"Disco {codec}"
        minutes, remainder = divmod(round(seconds), 60)
        job_label = f"{label} · {minutes}:{remainder:02d}"
        stage = "Preparando"
        active = True
        self.status_var.set(f"{job_label} · {stage}")
        self.cancel_btn.configure(state="normal")
        for button in (self.convert_btn, self.preview_btn, self.burn_btn, getattr(self, "cavc_cache_btn", None)):
            if button is not None:
                button.configure(state="disabled")

        def refresh_status():
            if not active or self._conversion_cancel_event.is_set():
                return
            elapsed = int(time.monotonic() - started)
            text = f"{job_label} · {stage} · {elapsed // 60}:{elapsed % 60:02d} transcurridos"
            done = self.progress_var.get()
            if 10 <= done < 99 and elapsed > 120:
                # Linear projection of the percentage reported by the encoder.
                left = round(elapsed * (100 - done) / done / 60)
                text += f" · quedan ~{left} min" if left >= 1 else " · menos de 1 min"
            self.status_var.set(text)

        def tick():
            if active:
                refresh_status()
                self.root.after(1000, tick)

        self.root.after(1000, tick)

        def report(percent, name):
            def apply():
                nonlocal stage
                if active and not self._conversion_cancel_event.is_set():
                    stage = name
                    self.progress_var.set(max(self.progress_var.get(), min(99, percent)))
                    refresh_status()
            self.root.after(0, apply)

        def complete(folder=None, result=None, error=None):
            nonlocal active
            active = False
            self._conversion_running = self.is_converting = False
            self.pbar.stop()
            self.pbar.configure(mode="determinate")
            if folder:
                self.progress_var.set(100)
            self.cancel_btn.configure(state="disabled")
            for button in (self.convert_btn, self.preview_btn, self.burn_btn, getattr(self, "cavc_cache_btn", None)):
                if button is not None:
                    button.configure(state="normal")
            if self._closing:
                return
            if error:
                self.status_var.set(f"{codec} cancelado" if isinstance(error, CavcCancelled) else f"Error en {codec}")
                if not isinstance(error, CavcCancelled):
                    messagebox.showerror(f"Conversión {codec}", str(error))
                return
            self.last_iso_path = str(folder / result["iso"])
            self.last_cue_path = str(folder / result["cue"])
            self.fusion_btn.configure(state="normal")
            elapsed = int(time.monotonic() - started)
            display_rate = "15–25 FPS nominales" if lore else f"{result['fps_effective']:.2f} FPS"
            discs = result.get("discs") or []
            disc_text = f" · {len(discs)} CD" if len(discs) > 1 else ""
            self.status_var.set(f"{codec} listo · {result['parts']} capítulo(s){disc_text} · {display_rate} · "
                                f"{elapsed // 60}:{elapsed % 60:02d}")
            if preview and result["preview"]:
                os.startfile(folder / result["preview"])
            elif len(discs) > 1:
                lines = "\n".join(f"• {Path(disc['iso']).parent.name}: {disc['parts']} capítulos"
                                   for disc in discs)
                messagebox.showinfo(
                    f"{len(discs)} discos {codec} creados",
                    f"{folder}\n\n{lines}\n\nGraba el .CUE de cada carpeta en un CD-R distinto. "
                    "Disco 1 queda seleccionado; usa «Elegir disco…» para los demás.")
                if auto_burn:
                    self.start_burn_cd()
            elif auto_burn:
                self.start_burn_cd()
            else:
                messagebox.showinfo(f"Disco {codec} creado", str(folder))

        def work():
            try:
                folder, result = convert_cavc(sources, output, settings, self._conversion_cancel_event, report)
            except Exception as error:
                self.root.after(0, lambda exc=error: complete(error=exc))
            else:
                self.root.after(0, complete, folder, result)

        threading.Thread(target=work, daemon=True).start()

    def _check_free_space(self, seconds, fps):
        """Warn before a long conversion when the system drive is nearly full."""
        frames = round(seconds * fps)
        # Temporary frames of the two parts in flight, the WSL disk image growth
        # and the finished ISO copies; Windows itself needs headroom as well.
        need = 12 * 1024**3 + min(frames, 2 * 9000) * 320 * 224 * 6 + int(seconds * 160_000)
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            return True
        if free >= need:
            return True
        return messagebox.askyesno(
            "Poco espacio libre",
            f"Quedan {free / 1024**3:.1f} GB libres en {drive} y la conversión puede necesitar "
            f"unos {need / 1024**3:.0f} GB (fotogramas temporales de WSL e imágenes ISO).\n\n"
            "Si se llena el disco, la conversión fallará y Windows puede ir lento. "
            "Libera espacio antes de continuar.\n\n¿Convertir de todos modos?")

    def _launch_disc_in_ares(self):
        path = self.last_cue_path or self.last_iso_path
        if not path:
            messagebox.showinfo("Probar disco en ares", "Elige o crea primero un disco Mega CD.")
            return
        cue = Path(path).resolve().with_suffix(".cue")
        if not cue.is_file():
            messagebox.showwarning("Falta el CUE", "Selecciona una imagen con su archivo CUE.")
            return
        candidates = (
            getattr(self, "_ares_exe", None),
            shutil.which("ares.exe"),
            Path.home() / "Desktop" / "ares-v148" / "ares.exe",
        )
        executable = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
        if executable is None:
            selected = filedialog.askopenfilename(title="Seleccionar ares.exe",
                                                  filetypes=[("Emulador ares", "ares.exe")])
            if not selected:
                return
            executable = Path(selected)
        try:
            subprocess.Popen([str(executable), "--system", "Mega CD", str(cue)],
                             cwd=executable.parent)
        except OSError as error:
            messagebox.showerror("No se pudo iniciar ares", str(error))
            return
        self._ares_exe = executable
        self.status_var.set("Disco abierto en ares · comprobación manual")

    def _open_cavc_preview(self):
        info = read_cavc_info(self.last_cue_path)
        preview = info.get("preview")
        if isinstance(preview, str) and Path(preview).name == preview and preview.lower().endswith(".mp4"):
            path = Path(self.last_cue_path).with_name(preview)
            if path.is_file():
                os.startfile(path)
                return
        messagebox.showinfo("Vista MP4", "El disco seleccionado no tiene vista MP4. Activa la exportación en Ajustes.")

