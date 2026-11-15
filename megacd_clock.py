"""Corrección del reloj PCM del reproductor original Motorola 68000.

El producto muestras*600 desbordaba el entero con signo a los 223,7 s.
Se divide primero en segundos y resto, conservando el redondeo original.
No se cambian ni el audio ni los fotogramas del archivo CIN.
"""
import struct


def patch_audio_clock(subplay):
    original = bytes.fromhex('4e5600002f2e00084ebaffb22200202e000c4eba0046')
    if subplay[0x1684:0x169a] != original:
        raise ValueError('Revisión de AudioTime no compatible con el reloj corregido.')
    base = len(subplay)
    code = bytearray()

    def emit(h):
        code.extend(bytes.fromhex(h))

    def jsr(target):
        pos = base + len(code)
        code.extend(struct.pack('>Hh', 0x4eba, target - (pos + 2)))

    emit('4e56fffc')       # LINK A6,#-4: resto de muestras
    emit('2f2e0008')       # AudioData
    jsr(0x1640)           # AudioTicks: D0 = muestras PCM
    emit('223c00003e80')   # 16000, misma escala del player original
    jsr(0x178e)           # división sin signo: D1=segundos; D0=resto
    emit('2d40fffc')       # conservar resto
    emit('2001222e000c')   # D0=segundos; D1=escala temporal (600)
    jsr(0x16de)           # segundos*escala
    emit('2e80')           # sustituir argumento AudioData ya consumido
    emit('202efffc222e000c')
    jsr(0x16de)           # resto*escala: producto pequeño y acotado
    emit('d0bc00001f40223c00003e80')  # redondeo +8000; divisor 16000
    jsr(0x178e)
    emit('2001d09f4e5e4e75') # D0=cociente + segundos*escala; retorno
    subplay.extend(code)
    struct.pack_into('>Hh', subplay, 0x1684, 0x6000, base - 0x1686)
    return {'offset': base, 'length': len(code), 'revision': 'astra4-clock-split-v1'}
