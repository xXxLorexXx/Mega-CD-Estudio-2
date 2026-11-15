"""Windows/Tk regression for switching the disc codec in Settings."""
import faulthandler
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import patch

from Mega_Cd_Grabador_de_Video_Beta import MegaCDVideoStudioApp


def check_switches(root, app):
    app._select_page("settings")
    for size in ("1200x850", "420x360", "865x530", "900x670"):
        root.geometry(size)
        root.update()
        selection = app.tree.selection()
        for codec in ("CAVC", "LORE", "Cinepak", "LORE", "CAVC", "Cinepak"):
            faulthandler.dump_traceback_later(8, exit=True)
            try:
                app.codec_var.set(codec)
                root.update()
            finally:
                faulthandler.cancel_dump_traceback_later()
            cavc = codec in ("CAVC", "LORE")
            lore = codec == "LORE"
            assert app.cavc_fps_control.instate(("disabled",)) == lore
            if lore:
                assert app.cavc_fps_control.get() == "15–25 (PAL)"
                assert not app.cavc_profile_row.winfo_ismapped()
            assert bool(app.cavc_card.winfo_ismapped()) == cavc
            assert bool(app.cinepak_picture.winfo_ismapped()) != cavc
            assert app.disc_format_var.get() == f"ISO / CUE · {codec}"
            assert app.tree.selection() == selection
            assert app.tree.heading("chapters")["text"] == ("Partes" if cavc else "Capítulos")
            if app.videos:
                col_val = app.tree.item("0", "values")[4]
                if cavc:
                    assert "parte" in col_val and col_val != "—", f"Expected part count, got {col_val!r}"
                else:
                    assert "cap." in col_val, f"Expected chapter count, got {col_val!r}"
            summary = app.picture_summary.get()
            assert ("MOSAIC-GM" if cavc else "paletas") in summary
            assert app.preview_btn.cget("text") == f"Prueba de {5 if cavc else 15} s"


def main():
    with tempfile.TemporaryDirectory() as folder, patch.object(
            MegaCDVideoStudioApp, "refresh_burners"):
        root = tk.Tk()

        def report_callback_exception(_type, error, traceback):
            raise error.with_traceback(traceback)

        root.report_callback_exception = report_callback_exception
        try:
            app = MegaCDVideoStudioApp(root)
            check_switches(root, app)
            source = Path(folder) / "video.mp4"
            source.touch()
            app.videos = [dict(path=str(source), duration=249.334,
                               width=480, height=360, name=source.name)]
            app._refresh_tree()
            app.tree.selection_set("0")
            check_switches(root, app)
        finally:
            root.destroy()
    print("Cambios de códec verificados con biblioteca vacía y con un vídeo seleccionado.")


if __name__ == "__main__":
    main()
