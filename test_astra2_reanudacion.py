import json,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
from megacd_conversion import convert_playlist,ConversionCancelled

if __name__=='__main__':
    import tempfile,subprocess
    out=Path(tempfile.mkdtemp(prefix='astra2_resume_'))
    video=out/'patrones.mkv'
    subprocess.run([s.find_ffmpeg(),'-v','error','-y','-f','lavfi','-i','testsrc2=s=256x192:r=24:d=8','-c:v','ffv1',str(video)],check=True,creationflags=s.CREATE_NO_WINDOW)
    source={'path':str(video),'duration':8,'width':256,'height':192,'name':'Cancelar y reanudar'}
    settings={'fps':24,'chapter_seconds':2,'fit_cd':True,'workers':1,'acceleration':'cpu','loop_playback':False}
    cancel=threading.Event()
    def progress(done,total,index,current,*args):
        if done>=60:cancel.set()
    start=time.perf_counter()
    try:
        convert_playlist([source],out,settings,progress_cb=progress,cancel_event=cancel)
        raise AssertionError('No se canceló la conversión')
    except ConversionCancelled:pass
    prior=list(out.glob('.astra2_trabajo/**/completo.json'))
    assert len(prior)>=1 and not list(out.glob('*.ISO'))
    assert not list(out.glob('.astra2_trabajo/**/*.partial'))
    result=convert_playlist([source],out,settings)
    assert result['frames']==192 and any(j['cached'] for j in result['jobs'])
    assert result['iso_payload_sha256_verified']
    # Un vídeo cuyo audio dura un fotograma más: conservar la cola de sonido.
    target=out/'cola.cin'
    s.encode_segment_to_cin(str(video),7,25/24,24,'keep',4,str(target),str(out),max_cin_bytes=200000,acceleration='cpu')
    assert s.inspect_cin(target,24)['fotogramas']==25
    report={'cancelled_safely':True,'no_incomplete_ISO_published':True,'no_partial_CIN_left':True,
            'reused_completed_chapters':sum(j['cached'] for j in result['jobs']),
            'final_frames':result['frames'],'ISO_payload_SHA256_verified':True,
            'audio_tail_last_frame_clone':True,'seconds_including_reencode':time.perf_counter()-start}
    (ROOT/'Pruebas_Astra_2').mkdir(parents=True,exist_ok=True)
    (ROOT/'Pruebas_Astra_2'/'Cancelacion_reanudacion.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
