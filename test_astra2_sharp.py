# -*- coding: utf-8 -*-
import importlib.util,json,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
from megacd_conversion import make_capacity_plan,FFmpegReadGuard,ConversionCancelled,run_ffmpeg_quiet
from test_astra2_codec import decoded_indices

class SharpTests(unittest.TestCase):
    def test_auto_fps_fits_full_movie_without_changed_block_reuse(self):
        with tempfile.NamedTemporaryFile(suffix='.mkv') as f:
            source={'path':f.name,'duration':7613.376,'width':1920,'height':816}
            plan=make_capacity_plan([source],{'fps':24,'aspect_mode':'crop','fit_cd':True,'chapter_seconds':600,'quality_mode':'sharp'},s)
            self.assertEqual(plan['requested_fps'],24);self.assertEqual(plan['fps'],10)
            self.assertEqual(plan['frames'],76134)
            self.assertTrue(all(j['quality_floor']==7700 for j in plan['jobs']))
            self.assertLessEqual(plan['estimated_iso_bytes'],plan['capacity_bytes'])
    def test_matches_70_decoded_pixels_at_sufficient_budget(self):
        spec=importlib.util.spec_from_file_location('original70',ROOT/'Codigo_Fuente_MegaCD_Video_Studio_7_0.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        rng=np.random.default_rng(340);banks=[None,None]
        for frame in range(4):
            rgb=rng.integers(0,256,(192,256,3),dtype=np.uint8)
            original=old.quantize_and_encode_frame(rgb,(256,192,0,0),prev2_tinds=banks[frame%2],target_frame_bytes=8000)
            actual=s.quantize_and_encode_frame(rgb,(256,192,0,0),prev2_tinds=banks[frame%2],target_frame_bytes=8000,hard_frame_bytes=8000,allow_lossy_reuse=False)
            self.assertEqual(original[1],actual[1]);self.assertEqual(original[2],actual[2])
            expected=decoded_indices(original,banks[frame%2]);pixels=decoded_indices(actual,banks[frame%2])
            self.assertTrue(np.array_equal(expected,pixels))
            banks[frame%2]=pixels
    def test_low_budget_cannot_force_changed_block_copy(self):
        rgb=np.random.default_rng(9).integers(0,256,(192,256,3),dtype=np.uint8)
        with self.assertRaises(ValueError):
            s.quantize_and_encode_frame(rgb,(256,192,0,0),prev2_tinds=np.zeros((768,64),np.uint8),
                target_frame_bytes=2000,hard_frame_bytes=2000,allow_lossy_reuse=False)
    def test_stalled_decoder_unblocks_and_cancel_unblocks(self):
        for mode in ('timeout','cancel'):
            proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.PIPE,
                                  creationflags=s.CREATE_NO_WINDOW)
            event=threading.Event();guard=FFmpegReadGuard(proc,event.is_set,timeout=.25 if mode=='timeout' else 60)
            timer=threading.Timer(.25,event.set) if mode=='cancel' else None
            if timer:timer.start()
            started=time.monotonic()
            try:
                with self.assertRaises(ConversionCancelled if mode=='cancel' else RuntimeError):guard.read(1)
                self.assertLess(time.monotonic()-started,3)
            finally:
                guard.close();proc.wait(timeout=3);proc.stdout.close()
                if timer:timer.cancel()
    def test_audio_process_is_cancelable(self):
        event=threading.Event();timer=threading.Timer(.2,event.set);timer.start()
        try:
            with self.assertRaises(ConversionCancelled):
                run_ffmpeg_quiet([sys.executable,'-c','import time;time.sleep(30)'],event.is_set)
        finally:timer.cancel()

    def test_24fps_bands_stay_black_in_both_banks(self):
        import struct
        with tempfile.TemporaryDirectory(prefix='astra2_banks_') as tmp:
            root=Path(tmp);video=root/'bandas.mkv';target=root/'bandas.cin'
            subprocess.run([s.find_ffmpeg(),'-v','error','-y','-f','lavfi','-i',
                            'testsrc2=s=256x112:r=24:d=3','-c:v','ffv1',str(video)],
                           check=True,creationflags=s.CREATE_NO_WINDOW)
            plan=make_capacity_plan([{'path':str(video),'duration':3,'width':256,'height':112}],
                {'quality_mode':'sharp24','aspect_mode':'keep'},s)
            box=plan['jobs'][0]['active_box']
            size,frames=s.encode_segment_to_cin(str(video),0,3,24,'keep',4,str(target),tmp,
                                                allow_lossy_reuse=False,acceleration='cpu',frame_active_box=box)
            self.assertEqual(frames,72);self.assertTrue(s.inspect_cin(target,24)['seguro'])
            data=target.read_bytes();hs=struct.unpack_from('>I',data,4)[0]
            count=struct.unpack_from('>I',data,48)[0];banks=[None,None];frame=0
            for i in range(count):
                offset,length,timestamp,ticks=struct.unpack_from('>IIII',data,52+16*i)
                if timestamp==0xffffffff:continue
                sm=data[hs+offset:hs+offset+length];pals=(sm[3]&3)+1;p=12+pals*32
                current=decoded_indices((pals,sm[12:p],sm[p:p+192],sm[p+192:],None),banks[frame%2])
                grid=current.reshape(24,32,64)
                active=np.zeros((24,32),bool)
                active[box[3]//8:(box[3]+box[1])//8,box[2]//8:(box[2]+box[0])//8]=True
                self.assertTrue(np.all(grid[~active]==0))
                banks[frame%2]=current;frame+=1

if __name__=='__main__':unittest.main(verbosity=2)
