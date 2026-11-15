# -*- coding: utf-8 -*-
"""Comprobación de geometría Tk por tamaños de ventana; no cambia la pantalla."""
from pathlib import Path
import csv
import sys
import tkinter as tk
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s

def main():
    root=tk.Tk()
    # Evitar enumerar repetidamente dispositivos durante la prueba de layout.
    s.MegaCDVideoStudioApp.refresh_burners=lambda self:None
    app=s.MegaCDVideoStudioApp(root)
    root.maxsize(4000,2300)
    rows=[]
    for w,h in [(640,480),(800,600),(1024,768),(1280,720),(1920,1080),(3840,2160)]:
        root.geometry(f'{w}x{h}+0+0')
        problems=[]
        for page in tuple(app.pages):
            app._select_page(page)
            for codec in ("Cinepak", "CAVC", "LORE"):
                app.codec_var.set(codec)
                for _ in range(4):
                    root.update()
                inner=app.scroll.inner
                def check(widget):
                    for child in widget.winfo_children():
                        if child.winfo_ismapped() and child.winfo_width()>widget.winfo_width()+2:
                            problems.append((page,codec,str(child),child.winfo_width(),widget.winfo_width()))
                        check(child)
                check(inner)
        rows.append({'ancho':w,'alto':h,'ancho_real':root.winfo_width(),'alto_real':root.winfo_height(),
                     'ancho_interior':inner.winfo_width(),'desbordes_horizontales':len(problems),
                     'detalle':str(problems)})
    root.destroy()
    out=ROOT/'Pruebas'/'Tabla_interfaz.csv'
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    for row in rows:print(row)
    assert all(row['desbordes_horizontales']==0 for row in rows)

if __name__=='__main__':main()
