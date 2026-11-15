# -*- coding: utf-8 -*-
import json,sys,tempfile,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
import megacd_conversion as c

class MultiDiscTests(unittest.TestCase):
    def test_full_movie_24fps_every_frame_assigned_exactly_once(self):
        with tempfile.NamedTemporaryFile(suffix='.mkv') as f:
            source={'path':f.name,'duration':7613.376,'width':1920,'height':816}
            settings={'quality_mode':'sharp24','fps':8,'aspect_mode':'crop','chapter_seconds':600}
            plan=c.make_capacity_plan([source],settings,s)
            self.assertEqual(plan['fps'],24);self.assertEqual(plan['frames'],182721)
            self.assertEqual(plan['disc_count'],2)
            self.assertEqual(plan['active_boxes'][0]['active_box'],[160,120,48,32])
            assigned=[i for d in plan['discs'] for i in d['job_indices']]
            self.assertEqual(assigned,list(range(len(plan['jobs']))))
            for disc in plan['discs']:
                self.assertLessEqual(disc['estimated_iso_bytes'],c.TARGET)
                self.assertEqual(disc['chapter_ids'][0],1)
            self.assertTrue(all(j['quality_floor']<=96000//24 and j['keyframes']==0 for j in plan['jobs']))
            settings['aspect_mode']='keep'
            panoramic=c.make_capacity_plan([source],settings,s)
            self.assertEqual(panoramic['active_boxes'][0]['active_box'],[208,88,24,48])
            self.assertTrue(panoramic['active_boxes'][0]['adapted'])
    def test_very_long_playlist_restarts_chapter_numbers_per_disc(self):
        with tempfile.NamedTemporaryFile(suffix='.mkv') as f:
            plan=c.make_capacity_plan([{'path':f.name,'duration':60000,'width':640,'height':480}],
                {'quality_mode':'sharp24','chapter_seconds':600,'aspect_mode':'stretch'},s)
            self.assertEqual(plan['logical_chapters'],100)
            self.assertGreater(plan['disc_count'],2)
            self.assertTrue(all(max(d['chapter_ids'])<=99 and d['chapter_ids'][0]==1 for d in plan['discs']))
    def test_real_two_disc_pipeline_hashes_order_and_capacity(self):
        with tempfile.TemporaryDirectory(prefix='astra2_multidisc_') as tmp:
            root=Path(tmp).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            video=root/'patrones.avi'
            subprocess.run([s.find_ffmpeg(),'-v','error','-y','-f','lavfi','-i',
                'testsrc2=s=256x192:r=24:d=8','-c:v','ffv1',str(video)],check=True,creationflags=s.CREATE_NO_WINDOW)
            settings={'quality_mode':'sharp24','chapter_seconds':2,'fps':24,'aspect_mode':'crop',
                      'workers':2,'acceleration':'auto','loop_playback':False}
            source={'path':str(video),'duration':8,'width':256,'height':192,'name':'Multidisco'}
            previous=c.TARGET
            try:
                c.TARGET=1300000  # Límite pequeño sólo en esta prueba; CD real sigue en 700 MiB.
                result=c.convert_playlist([source],root/'salida',settings)
            finally:c.TARGET=previous
            self.assertEqual(result['disc_count'],2);self.assertEqual(result['fps'],24)
            self.assertEqual(result['frames'],192);self.assertFalse(result['lossy_block_reuse'])
            self.assertTrue(result['iso_payload_sha256_verified'])
            for disc in result['discs']:
                self.assertTrue(Path(disc['cue_path']).is_file() and Path(disc['iso_path']).is_file())
                self.assertLessEqual(disc['iso_bytes'],1300000)
            self.assertEqual(sum(d['frames'] for d in result['discs']),192)
            proof={'disc_count':2,'fps':24,'frames':192,'all_payload_SHA256_verified':True,
                   'order_and_no_duplicate_frames':True,'lossy_changed_block_reuse':False,
                   'test_capacity_bytes':1300000,'iso_bytes':[d['iso_bytes'] for d in result['discs']],
                   'gpu_encode_jobs':result['gpu_encode_jobs'],'jit_jobs':result['jit_jobs']}
            (ROOT/'Pruebas_Astra_2').mkdir(parents=True,exist_ok=True)
            (ROOT/'Pruebas_Astra_2/Multidisco_prueba.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')

if __name__=='__main__':unittest.main(verbosity=2)
