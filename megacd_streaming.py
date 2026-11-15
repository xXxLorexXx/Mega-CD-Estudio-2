"""Presupuestos del lector; alta cadencia con margen adicional, sin cambiar <=15 FPS."""

STREAM_REVISION = 'astra3-stream-120k-v2'


def stream_limits(fps):
    if fps > 15:
        # Incluye las dos imágenes de inicialización y el PCM adelantado:
        # por redondeo de ticks pueden coincidir 25 imágenes en una ventana de 1 s.
        return {'average': 120000, 'peak_6s': 126000, 'peak_1s': 144000,
                'video': 96000, 'label': '24/20 FPS con margen de lectura'}
    return {'average': 142000, 'peak_6s': 150000, 'peak_1s': None,
            'video': 128000, 'label': 'Perfil original hasta 15 FPS'}
