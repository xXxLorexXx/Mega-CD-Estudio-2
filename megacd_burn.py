"""Comprobar el resultado real de grabación y comparación con la imagen."""
from pathlib import Path
import re


def confirm_imgburn_verification(log_path):
    path=Path(log_path)
    raw=path.read_bytes() if path.is_file() else b''
    encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
    text=raw.decode(encoding,errors='replace')
    start=text.rfind('Verifying Session ')
    finished=text.rfind('Operation Successfully Completed!')
    compared='Image File:' in text[start if start>=0 else len(text):]
    # ImgBurn escribe Image File antes de Verifying Session.
    if start>=0:
        operation=text.rfind('Operation Started!',0,start)
        compared='Image File:' in text[max(0,operation):start]
    errors=[line for line in text.splitlines() if re.match(r'^E\s+\d\d:',line)]
    if start<0 or finished<start or not compared or errors or 'Operation Aborted!' in text:
        raise RuntimeError('El CD no ha quedado verificado contra la imagen. Puede estar escrito, '
                           'pero la comprobación no terminó correctamente. Si se abre la bandeja '
                           'antes de verificar, ciérrela con el mismo CD dentro. Registro: '+str(path))
    return True


def verify_iso_against_device(iso_path,drive):
    """Lectura sin caché de aplicación del volumen, tras IMAPI2; nunca escribe."""
    drive=drive.rstrip('\\').upper()
    if not re.fullmatch('[A-Z]:',drive):
        raise RuntimeError('No se puede verificar: letra de unidad no válida.')
    offset=0
    try:
        with open(iso_path,'rb') as source, open('\\\\.\\'+drive,'rb',buffering=0) as disc:
            while True:
                expected=source.read(2*1024*1024)
                if not expected:break
                actual=disc.read(len(expected))
                if actual!=expected:
                    raise RuntimeError(f'El CD difiere de la ISO o no se puede leer, desde el sector {offset//2048}.')
                offset+=len(expected)
    except OSError as error:
        raise RuntimeError('El CD se ha escrito con IMAPI2, pero Windows no permitió completar '
                           'la comparación de lectura. Verifíquelo con ImgBurn antes de usarlo: '+str(error)) from error
    return offset
