"""Flujo de comprobación y grabación, sin bloquear el bucle de Tk."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox
from megacd_studio_ui import Flow
from megacd_cavc import read_cavc_info
from megacd_integrity import inspect_stream_disc, is_stream_disc, project_images, sha256


def install_validation_ui(cls):
    old_build=cls._build_ui;old_burn=cls.start_burn_cd;old_worker=cls._burn_worker
    old_cancel=cls.cancel_conversion;old_close=cls._close_window
    def build(self):
        old_build(self)
        self._validation_running=False;self._validation_tickets={};self._validation_cancel=None
        self.check_before_burn=tk.BooleanVar(value=True)
        card=self.validation_card
        self._check(card,'Comprobar antes de grabar',self.check_before_burn)
        actions=Flow(card,self.px(7))
        actions.pack(fill='x',pady=(self.px(9),0))
        self.check_btn=self._action(actions,'Comprobar película completa',lambda:start_check(self),'Tonal.TButton')
        self._action(actions,'Configurar BIOS…',lambda:bios(self))
        def sync():
            if self._closing:return
            selected = self.last_cue_path or self.last_iso_path
            is_stream = self._uses_cavc() or bool(selected and read_cavc_info(selected))
            self.check_btn.configure(state='disabled' if self.is_converting or getattr(self, "_probing_count", 0) else 'normal',
                                     text="Comprobar integridad del disco" if is_stream else "Comprobar película completa")
            self.root.after(250,sync)
        self.root.after(250,sync)
    def resource(self,name):
        return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'Kega'/name
    def find_external_kega(self):
        base = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
        candidates = (getattr(self, '_verify_kega_path', None),
                      base / 'Kega' / 'Fusion.exe', base / 'Fusion.exe',
                      Path.home() / 'Desktop' / 'Fusion.exe')
        for item in candidates:
            if item and Path(item).is_file():
                return str(item)
        path = filedialog.askopenfilename(title='Seleccionar Fusion.exe para comprobar Cinepak',
                                          filetypes=[('Kega Fusion', 'Fusion.exe'), ('Ejecutables', '*.exe')])
        if path:
            self._verify_kega_path = path
        return path

    def user_ini():
        return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'MegaCDStudioAstra5'/'KegaUser.ini'
    def bios(self):
        path=filedialog.askopenfilename(title='Seleccionar su BIOS de Mega CD',filetypes=[('BIOS Mega CD','*.bin *.BIN'),('Todos','*.*')])
        if not path:return
        import re
        source=user_ini() if user_ini().is_file() else resource(self,'FusionDefaults.ini')
        text=source.read_text(encoding='utf-8-sig',errors='replace')
        key={'E':'SCDEURBIOS','U':'SCDUSABIOS','J':'SCDJAPBIOS'}[self._get_selected_region()]
        text=re.sub(r'^'+key+r'=.*$',lambda m:key+'='+path,text,flags=re.M)
        target=user_ini();target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
        messagebox.showinfo('BIOS configurada','Se utilizará su BIOS en las comprobaciones de esta región.')
    def start_check(self,then_burn=False):
        if self.is_converting:return
        self._check_existing_disc()
        if not self.last_iso_path:self.choose_generated_disc()
        selected=self.last_cue_path or self.last_iso_path
        if not selected:return
        info=read_cavc_info(selected)
        if info or is_stream_disc(selected):
            start_stream_check(self, selected, info, then_burn)
            return
        try:images=project_images(selected)
        except Exception as exc:messagebox.showerror('No se puede comprobar',str(exc));return
        kega = find_external_kega(self)
        if not kega:
            return
        folder=Path(images[0]).parent/'Comprobaciones_Kega';folder.mkdir(exist_ok=True)
        work=Path(tempfile.mkdtemp(prefix='prueba_',dir=str(folder)))
        config={'images':images,'kega':kega,
                'ini':str(user_ini() if user_ini().is_file() else resource(self,'FusionDefaults.ini')),
                'work':str(work),'status':str(work/'estado.json'),'cancel':str(work/'cancelar')}
        config_path=work/'config.json';config_path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
        self._validation_cancel=Path(config['cancel']);self._validation_running=True;self.is_converting=True
        self.cancel_btn.configure(state='normal')
        self.convert_btn.configure(state='disabled');self.burn_btn.configure(state='disabled');self.preview_btn.configure(state='disabled')
        self.progress_var.set(0);self.status_var.set('Iniciando comprobación completa de reproducción en Kega...')
        def run():
            command=([sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('Mega_Cd_Grabador_de_Video_Beta.py'))])+['--verify-worker',str(config_path)]
            result=None
            try:
                with (work/'motor.log').open('wb') as log:
                    proc=subprocess.Popen(command,stdout=log,stderr=log,creationflags=0x08000000)
                    while proc.poll() is None:
                        try:
                            current=json.loads(Path(config['status']).read_text(encoding='utf-8'))
                            if not self._closing:self.root.after(0,lambda d=current:(self.progress_var.set(d.get('progress',0)*100),self.status_var.set(d.get('message','Comprobando...'))))
                        except (OSError,ValueError):pass
                        time.sleep(.3)
                    result=json.loads(Path(config['status']).read_text(encoding='utf-8'))
                    if proc.returncode or result.get('state')!='passed':raise RuntimeError(result.get('message','La comprobación no terminó.'))
                for item in result['results']:self._validation_tickets[os.path.normcase(str(Path(item['iso']).resolve()))]=item
                (work/'Resultado_comprobacion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                self._validation_result=result
                def ok():
                    restore(self)
                    if self._closing:self.root.destroy();return
                    self.progress_var.set(100);self.status_var.set('Comprobación completa: sin bloqueos detectados. Puede grabar.')
                    if then_burn:old_burn(self)
                    elif messagebox.askyesno('Película comprobada','No se detectaron bloqueos durante la reproducción completa en Kega.\n\n¿Pasar a grabación?\n\nInforme: '+str(work)):
                        old_burn(self)
                self.root.after(0,ok)
            except Exception as exc:
                error=str(exc)
                def failed():
                    restore(self)
                    if self._closing:self.root.destroy();return
                    self.progress_var.set(0);self.status_var.set('Comprobación sin aprobar: '+error)
                    if not self._validation_cancel.exists():messagebox.showerror('No se puede dar la película por comprobada',error+'\n\nNo se inicia la grabación. Informe: '+str(work))
                self.root.after(0,failed)
        threading.Thread(target=run,daemon=True).start()
    def start_stream_check(self, selected, info, then_burn):
        cancel_stream = threading.Event()
        self._stream_check_cancel = cancel_stream
        self._validation_running = self.is_converting = True
        self.cancel_btn.configure(state="normal")
        self._set_probe_actions("disabled")
        self.progress_var.set(5)
        self.status_var.set("Comprobando sectores y huellas del disco…")

        def complete(result=None, error=None):
            self._stream_check_cancel = None
            restore(self)
            if self._closing:
                self.root.destroy()
                return
            if error:
                self.progress_var.set(0)
                self.status_var.set("Comprobación cancelada" if cancel_stream.is_set() else "Comprobación sin aprobar")
                if not cancel_stream.is_set():
                    messagebox.showerror("Comprobación del disco", str(error))
                return
            key = os.path.normcase(result["iso"])
            self._validation_tickets[key] = result
            self.last_iso_path, self.last_cue_path = result["iso"], result["cue"]
            self.progress_var.set(100)
            checked = ("Estructura correcta y huellas coincidentes con la conversión."
                       if result["receipt"] else "Estructura correcta. Este disco carece de huellas de origen.")
            self.status_var.set(f"{checked} · {result['parts']} parte(s) · {result['frames']} fotogramas")
            if then_burn or messagebox.askyesno(
                "Integridad del disco comprobada",
                checked + "\n\nLa reproducción se puede revisar en ares o Kega. ¿Pasar a grabación?",
            ):
                old_burn(self)

        def run():
            try:
                result = inspect_stream_disc(selected, info, cancel_stream.is_set)
            except Exception as error:
                self.root.after(0, lambda exc=error: complete(error=exc))
            else:
                self.root.after(0, complete, result)
        threading.Thread(target=run, daemon=True).start()

    def restore(self):
        self._validation_running=False;self.is_converting=False
        self.cancel_btn.configure(state='disabled')
        self.convert_btn.configure(state='normal');self.burn_btn.configure(state='normal');self.preview_btn.configure(state='normal')
    def ticket(self):
        if not self.last_iso_path:return None
        path=Path(self.last_iso_path).resolve();value=self._validation_tickets.get(os.path.normcase(str(path)))
        if value and path.is_file() and [path.stat().st_size,path.stat().st_mtime_ns]==value['iso_stat']:return value
        return None
    def start_burn(self):
        if self.is_converting or getattr(self, '_probing_count', 0):return
        if self.check_before_burn.get():
            if not self.last_iso_path and not self.videos:self.choose_generated_disc()
            if self.last_iso_path and not ticket(self):start_check(self,then_burn=True);return
        old_burn(self)
    def burn_worker(self,iso_path,rec_id,drive_letter,speed_x):
        if self.check_before_burn.get():
            value=self._validation_tickets.get(os.path.normcase(str(Path(iso_path).resolve())))
            try:
                valid = (value and sha256(iso_path) == value["sha256"]
                         and sha256(value["cue"]) == value["cue_sha256"]
                         and Path(self.last_cue_path or "").resolve() == Path(value["cue"]).resolve())
            except OSError:
                valid = False
            if not valid:
                def invalid():
                    self.is_converting=False;self.burn_btn.configure(state='normal');self.convert_btn.configure(state='normal');self.preview_btn.configure(state='normal')
                    messagebox.showerror('Comprobación caducada','La imagen no está comprobada o ha cambiado. Vuelva a comprobarla antes de grabar.')
                self.root.after(0,invalid);return
        old_worker(self,iso_path,rec_id,drive_letter,speed_x)
    def cancel(self):
        if getattr(self, "_stream_check_cancel", None) is not None:
            self._stream_check_cancel.set()
            self.status_var.set("Cancelando la comprobación del disco…")
        elif getattr(self,'_validation_running',False):
            self._validation_cancel.touch();self.status_var.set('Cancelando comprobación y cerrando la copia privada de Kega...')
        else:old_cancel(self)
    def close(self):
        if getattr(self,'_validation_running',False):self._closing=True;cancel(self)
        else:old_close(self)
    cls._build_ui=build;cls.start_integrity_check=start_check;cls.start_burn_cd=start_burn
    cls._burn_worker=burn_worker;cls.cancel_conversion=cancel;cls._close_window=close
