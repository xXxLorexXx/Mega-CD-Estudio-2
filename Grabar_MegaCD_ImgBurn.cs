using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Acceso auxiliar portátil: reutiliza el único flujo BAT con verificación.
internal static class Launcher {
    [STAThread]
    static int Main(string[] args) {
        try {
            string folder = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(folder, "Grabar_MegaCD_ImgBurn.bat");
            if (!File.Exists(script)) throw new FileNotFoundException("Falta el grabador con verificación", script);
            var info = new ProcessStartInfo(script) {
                WorkingDirectory = folder,
                UseShellExecute = true,
                Arguments = String.Join(" ", Array.ConvertAll(args, a => "\"" + a.Replace("\"", "") + "\""))
            };
            Process.Start(info);
            return 0;
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Mega CD Astra 3 — Grabación", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
