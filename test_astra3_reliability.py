"""Regresiones de errores CDC, navegación larga y presupuestos de lectura."""
import importlib.util
import struct
import tempfile
import subprocess
import unittest
from pathlib import Path

import numpy as np
import test_astra as native
import Mega_Cd_Grabador_de_Video_Beta as s
from megacd_conversion import make_capacity_plan, protected_frame_budget
from megacd_streaming import stream_limits
from megacd_burn import confirm_imgburn_verification


class RecoveryTests(unittest.TestCase):
    def test_read_error_reopens_same_cin_without_skipping(self):
        h = native.SubHarness()
        h.uc.mem_write(h.addr('CurOffset'), struct.pack('>H', 4))
        h.task_result = -2
        for failures in (1, 2):
            self.assertEqual(h.run('DoFilmTask', ['RetryCurrentVideo', 'FinishCurrentVideo']), 'RetryCurrentVideo')
            h.run('RetryCurrentVideo', ['PlayCurrentVideo'])
            self.assertEqual(h.word('CurOffset'), 4)
            self.assertEqual(h.word('ReadFailures'), failures)
        self.assertEqual(h.calls, [1, 2, 1, 2])

    def test_third_error_displays_retry_menu_without_closing_live_context(self):
        h = native.SubHarness()
        h.valid_frame()
        h.uc.mem_write(h.addr('ReadFailures'), struct.pack('>H', 2))
        h.task_result = -2
        h.run('DoFilmTask', ['MenuInputLoop'])
        self.assertEqual(h.word('ReadErrorPending'), 1)
        self.assertEqual(h.word('ReadFailures'), 3)
        self.assertNotIn(2, h.calls)
        self.assertTrue(h.displayed)
        h.run('CloseMenuAndResume', ['PlayCurrentVideo'])
        self.assertEqual(h.word('ReadErrorPending'), 0)
        self.assertEqual(h.word('ReadFailures'), 0)
        self.assertEqual(h.word('CurOffset'), 0)

    def test_fast_forward_read_error_is_not_end_of_chapter(self):
        h = native.SubHarness()
        h.valid_frame()
        native.call(h.uc, h.addr('CaptureCurrentFrameBackup'))
        h.task_result = -2
        h.run('ActionFastForward', ['RetryCurrentVideo', 'ActionNextTrack'])
        self.assertEqual(h.stopped, 'RetryCurrentVideo')
        self.assertEqual(bytes(h.uc.mem_read(0xFF0007, 1)), b'\x04')

    def test_all_26_internal_parts_keep_correct_logical_next(self):
        original = native.studio.build_multi_sp_bin
        ids = [chapter for chapter in range(1, 10) for _ in range(3)][:26]
        def full_map(subplay, sectors, **kwargs):
            kwargs['chapter_ids'] = ids
            return original(subplay, [10000+i*14000 for i in range(26)], **kwargs)
        try:
            native.studio.build_multi_sp_bin = full_map
            h = native.SubHarness()
        finally:
            native.studio.build_multi_sp_bin = original
        for i, chapter in enumerate(ids):
            h.uc.mem_write(h.addr('CurOffset'), struct.pack('>H', i*4))
            h.run('ActionNextTrack', ['PlayCurrentVideo'])
            expected = next((j*4 for j in range(i+1, len(ids)) if ids[j] != chapter), len(ids)*4)
            self.assertEqual(h.word('CurOffset'), expected)

    def test_failed_open_preserves_position_and_never_closes_unopened_film(self):
        h = native.SubHarness(open_result=-1)
        h.run('StartUserCall0', ['OpenErrorWait'])
        self.assertEqual(h.word('CurOffset'), 0)
        self.assertEqual(h.calls, [0, 0])


class StreamingTests(unittest.TestCase):
    def test_high_fps_profile_and_full_movie_no_missing_frames(self):
        self.assertEqual(stream_limits(15)['average'], 142000)
        self.assertEqual(stream_limits(24)['average'], 120000)
        self.assertLessEqual(s.default_frame_budget(24), 4000)
        source = {'path': __file__, 'duration': 7613.376, 'width': 1920, 'height': 816}
        plan = make_capacity_plan([source], {'quality_mode': 'sharp24', 'chapter_seconds': 600, 'aspect_mode': 'keep'}, s)
        self.assertEqual(plan['fps'], 24)
        self.assertEqual(plan['frames'], round(source['duration']*24))
        self.assertEqual(sum(job['frames'] for job in plan['jobs']), plan['frames'])
        self.assertTrue(all(job['duration'] <= 128 for job in plan['jobs']))
        self.assertTrue(all(job['frame_budget'] <= 4000 for job in plan['jobs']))
        self.assertTrue(all(disc['estimated_iso_bytes'] <= 700*1024*1024 for disc in plan['discs']))

    def test_one_second_burst_is_rejected_even_when_six_second_average_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'burst.cin'
            frames = 240
            entries=[]; offset=0
            for i in range(frames):
                length=8000 if 72 <= i < 96 else 2000
                entries.append((offset,length,round(i*25*16276/16000),round(25*16276/16000)))
                offset += length
            hs=68+16*len(entries)
            header=struct.pack('>4sIII4sI4sII4sIII',b'FILM',hs,0,0,b'FDSC',20,b'SEGA',192,256,b'STAB',16+16*frames+16,600,frames)
            path.write_bytes(header+b''.join(struct.pack('>IIII',*x) for x in entries)+bytes(16)+bytes(offset))
            checked=s.inspect_cin(path,24)
            self.assertLess(checked['media_B_s'],120000)
            self.assertLess(checked['pico_6s_B_s'],126000)
            self.assertGreater(checked['pico_1s_B_s'],144000)
            self.assertFalse(checked['seguro'])

    def test_15fps_frame_budget_is_unchanged(self):
        self.assertEqual(s.default_frame_budget(15), 128000//15)
        self.assertEqual(protected_frame_budget(15,4500), 124000//15)

    def test_15fps_cin_is_identical_to_astra2(self):
        baseline=Path(__file__).resolve().parent.parent/'mega cd estudio astra 2/Mega_Cd_Grabador_de_Video_Beta.py'
        if not baseline.is_file():self.skipTest('La referencia Astra 2 completa no está junto a este proyecto.')
        spec=importlib.util.spec_from_file_location('astra2_baseline',baseline)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'pattern.mkv'
            subprocess.run([s.find_ffmpeg(),'-v','error','-y','-f','lavfi','-i','testsrc2=s=256x192:r=15:d=3','-c:v','ffv1',str(video)],check=True,creationflags=s.CREATE_NO_WINDOW)
            for module,name in ((old,'old.cin'),(s,'new.cin')):
                module.encode_segment_to_cin(str(video),0,3,15,'keep',4,str(root/name),tmp,acceleration='cpu')
            self.assertEqual((root/'old.cin').read_bytes(),(root/'new.cin').read_bytes())


class BurnVerificationTests(unittest.TestCase):
    def test_written_disc_with_aborted_verification_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'burn.log'
            path.write_text('I 21:00:00 Operation Successfully Completed!\nI 21:00:01 Cycling Tray before Verify...\nE 21:00:02 Operation Aborted!\n')
            with self.assertRaises(RuntimeError):confirm_imgburn_verification(path)

    def test_comparison_completed_with_no_errors_is_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'verify.log'
            path.write_text('I 21:00:00 Operation Started!\nI 21:00:00 Image File: test.iso\nI 21:00:01 Verifying Session 1 of 1...\nI 21:02:00 Operation Successfully Completed!\n')
            self.assertTrue(confirm_imgburn_verification(path))


if __name__ == '__main__':
    unittest.main(verbosity=2)
