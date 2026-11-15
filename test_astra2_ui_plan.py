from pathlib import Path
import sys,tkinter as tk
from tkinter import ttk
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s

if __name__=='__main__':
    root=tk.Tk();root.withdraw()
    s.MegaCDVideoStudioApp.refresh_burners=lambda self:None
    app=s.MegaCDVideoStudioApp(root)
    app.videos=[{'path':str(Path(__file__).resolve()),'duration':7613.376,'width':1920,'height':816,'name':'Prueba de plan'}]
    app.quality_mode_var.set('24 FPS nítidos: adapta área / varios CD')
    app._quality_mode_selected()
    assert '208×88' in app.cap_label.cget('text')
    def walk(widget):
        for child in widget.winfo_children():
            yield child;yield from walk(child)
    combos=[w for w in walk(root) if isinstance(w,ttk.Combobox) and str(w.cget('textvariable'))==str(app.aspect_var)]
    assert len(combos)==1
    app.aspect_var.set('Recortar al Centro 256x192 (Zoom / Rellenar 4:3)')
    combos[0].event_generate('<<ComboboxSelected>>');root.update()
    assert '160×120' in app.cap_label.cget('text'),app.cap_label.cget('text')
    assert app._conversion_settings()['fps']==24 and not app.fit_cd_var.get()
    root.destroy()
    print('Plan actualizado al cambiar de panorámico a recorte: 24 FPS, 2 CD, 160×120.')
