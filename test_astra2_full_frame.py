from pathlib import Path
import tempfile,subprocess,sys
import numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='astra2_encuadre_') as tmp:
        root=Path(tmp).resolve();assert root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        rgb=np.full((104,240,3),96,np.uint8)
        rgb[:20,:20]=(255,0,0);rgb[:20,-20:]=(0,255,0)
        rgb[-20:,:20]=(0,0,255);rgb[-20:,-20:]=(255,255,0)
        image=root/'encuadre.ppm';image.write_bytes(b'P6\n240 104\n255\n'+rgb.tobytes())
        box=(240,104,8,40)
        vf=s.build_video_filter(24,'standard','keep',box,'bt601','off')
        data=subprocess.run([s.find_ffmpeg(),'-v','error','-loop','1','-i',str(image),
            '-vf',vf,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],
            stdout=subprocess.PIPE,check=True,creationflags=s.CREATE_NO_WINDOW).stdout
        actual=np.frombuffer(data,np.uint8).reshape(192,256,3)
        active=np.zeros((192,256),bool);active[40:144,8:248]=True
        assert np.all(actual[~active]==0),'La imagen sobresale del área y el cuantizador la recortaría.'
        pixels=actual[active]
        for color in ((255,0,0),(0,255,0),(0,0,255),(255,255,0)):
            distance=np.max(abs(pixels.astype(int)-np.array(color)),axis=1)
            assert np.count_nonzero(distance<60)>=100,'Se ha perdido una esquina del encuadre.'
    print('Encuadre completo: las cuatro esquinas permanecen dentro del área activa; sin recorte ni desbordamiento.')
