# -*- coding: utf-8 -*-
"""Pruebas reproducibles de 68000 y contenedores. No emula VDP/CDC/RF5C164."""
import sys
from pathlib import Path
import json
import struct
import unittest
import importlib.util
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import *

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('studio', ROOT/'Mega_Cd_Grabador_de_Video_Beta.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
from megacd_runtime import patch_main_controller, chapter_plan, inspect_cin


def cpu():
    uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
    uc.mem_map(0, 0x1000000)
    uc.reg_write(UC_M68K_REG_SR, 0x2000)
    uc.reg_write(UC_M68K_REG_A7, 0xF000)
    return uc


def call(uc, addr, count=200000):
    sp = uc.reg_read(UC_M68K_REG_A7)
    uc.mem_write(sp-4, struct.pack('>I', 0xF800))
    uc.reg_write(UC_M68K_REG_A7, sp-4)
    uc.emu_start(addr, 0xF800, count=count)
    if uc.reg_read(UC_M68K_REG_PC) != 0xF800:
        raise AssertionError(f'La rutina no terminó: {uc.reg_read(UC_M68K_REG_PC):x}')


class MainTests(unittest.TestCase):
    def test_pad_protocol(self):
        # Se ejercitan los 256 estados clásicos, con las 16 combinaciones
        # XYZ/Mode posibles en mando de seis botones, además del de tres.
        main = bytearray(studio.MAINPLAY_BIN)
        ip = bytearray(studio.IP_BIN)
        patch = patch_main_controller(ip, main)
        uc = cpu()
        base = 0x20000
        uc.mem_write(base, bytes(main))
        call(uc, base+patch['init'])
        self.assertEqual(bytes(uc.mem_read(0xA10009, 1)), b'\x40')
        state = {'buttons': 0, 'six': False, 'extra': 0, 'phase': 0, 'th': 0x40}
        def write(uc, access, addr, size, value, data):
            if addr == 0xA10003:
                state['th'] = value
                state['phase'] += 1
        def read(uc, access, addr, size, value, data):
            if addr != 0xA10003:
                return
            p, b = state['phase'], state['buttons']
            if state['th'] == 0:
                val = ((~b >> 2) & 0x30) | (~b & 3)
                if state['six'] and p == 5:
                    val &= 0x30
                if state['six'] and p == 7:
                    val = (val & 0x30) | 0xF
            else:
                val = 0x40 | (~b & 0x3F)
                if state['six'] and p == 6:
                    val = (val & 0x70) | (state['extra'] ^ 0xF)
            uc.mem_write(addr, bytes([val]))
        uc.hook_add(UC_HOOK_MEM_WRITE, write)
        uc.hook_add(UC_HOOK_MEM_READ, read)
        for six in (False, True):
            for extra in range(16 if six else 1):
                for b in range(256):
                    state.update(buttons=b, six=six, extra=extra, phase=0, th=0x40)
                    uc.reg_write(UC_M68K_REG_D0, 0x12345678)
                    uc.reg_write(UC_M68K_REG_D1, 0x87654321)
                    call(uc, base+patch['read'])
                    self.assertEqual(struct.unpack('>H', uc.mem_read(0xA1201A, 2))[0], b*257)
                    self.assertEqual(uc.reg_read(UC_M68K_REG_D0), 0x12345678)
                    self.assertEqual(uc.reg_read(UC_M68K_REG_D1), 0x87654321)
                    self.assertEqual(state['th'], 0x40)
                    self.assertEqual(state['phase'], 8)
        self.assertEqual(ip[48:], main)


class SubHarness:
    def __init__(self, open_result=0):
        self.labels = {}
        self.code = studio.build_multi_sp_bin(studio.SUBPLAY_BIN, [25, 100, 200], loop_flag=False,
                                             chapter_ids=[1, 1, 2], debug_symbols=self.labels)
        self.uc = cpu()
        self.base = 0x6000
        self.uc.mem_write(self.base, self.code)
        self.open_result = open_result
        self.task_result = 0
        self.audio_time = 0
        self.calls = []
        self.bank = 0
        self.banks = [bytearray(0x20000), bytearray(0x20000)]
        self.displayed = []
        self.stop_at = set()
        def code_hook(uc, addr, size, data):
            label = next((k for k in self.stop_at if self.base+self.labels[k] == addr), None)
            if label is not None:
                self.stopped = label
                uc.emu_stop()
                return
            off = addr - self.base - self.labels['Cinepak']
            if off not in (0, 0xDEE, 0x1122, 0x95C, 0x1684):
                return
            sp = uc.reg_read(UC_M68K_REG_A7)
            result = 0
            if off == 0x1684:
                result = self.audio_time
                self.audio_time += 60
            if off == 0xDEE:
                self.banks[self.bank][:] = uc.mem_read(0xC0000, 0x20000)
                self.bank ^= 1
                uc.mem_write(0xC0000, bytes(self.banks[self.bank]))
            if off == 0x1122:
                command = struct.unpack('>I', uc.mem_read(sp+4, 4))[0]
                if command == 2:
                    self.displayed.append(bytes(self.banks[self.bank ^ 1]))
            if off == 0:
                command = struct.unpack('>H', uc.mem_read(sp+4, 2))[0]
                self.calls.append(command)
                if command == 0:
                    result = self.open_result
                if command == 1:
                    result = self.task_result
            uc.reg_write(UC_M68K_REG_D0, result & 0xFFFFFFFF)
            uc.reg_write(UC_M68K_REG_SR, 0x2004 if result == 0 else 0x2008)
            uc.reg_write(UC_M68K_REG_PC, struct.unpack('>I', uc.mem_read(sp, 4))[0])
            uc.reg_write(UC_M68K_REG_A7, sp+4)
        self.uc.hook_add(UC_HOOK_CODE, code_hook)
    def addr(self, name):
        return self.base+self.labels[name]
    def run(self, name, stops):
        self.stop_at = set(stops)
        self.stopped = None
        self.uc.emu_start(self.addr(name), 0xFFFFFF, count=200000)
        if self.stopped is None:
            raise AssertionError(f'No alcanzó {stops}: PC={self.uc.reg_read(UC_M68K_REG_PC):x}')
        return self.stopped
    def pad(self, value):
        self.uc.mem_write(0xFF801A, struct.pack('>H', value))
        self.stop_at.clear()
        call(self.uc, self.addr('PadVBlank'))
    def word(self, name):
        return struct.unpack('>H', self.uc.mem_read(self.addr(name), 2))[0]
    def valid_frame(self):
        self.uc.mem_write(0xC0000, struct.pack('>2sBBIHH', b'SM', 0, 0x23, 24732, 32, 24)+bytes(128+192+24576))


class SubTests(unittest.TestCase):
    def test_menu_latch_and_resume(self):
        for start in (0x8000, 0x80):
            h = SubHarness()
            h.run('StartUserCall0', ['TaskLoop'])
            h.pad(start)
            self.assertEqual(h.word('MenuPending'), 1)
            h.pad(0)  # pulsación breve antes de que llegue un fotograma
            self.assertEqual(h.run('TaskLoop', ['DoFilmTask', 'WaitStartRel1']), 'DoFilmTask')
            h.valid_frame()
            original_frame = bytes(h.uc.mem_read(0xC0000, 0x20000))
            h.run('TaskLoop', ['WaitStartRel1'])
            self.assertEqual(h.uc.mem_read(0xFF0011, 1), b'\xff')
            h.run('WaitStartRel1', ['MenuInputLoop'])
            h.pad(start)
            h.run('MenuInputLoop', ['WaitCloseRelease'])
            for _ in range(8):
                h.pad(start)
            h.pad(0)
            h.run('WaitCloseRelease', ['TaskLoop'])
            self.assertEqual(h.uc.mem_read(0xFF0011, 1), b'\xfe')
            self.assertEqual(h.word('MenuPending'), 0)
            self.assertEqual(h.bank, 0)
            self.assertEqual(bytes(h.uc.mem_read(0xC0000, 0x20000)), original_frame)
            self.assertEqual(len(h.displayed), 2)
            h.pad(start)
            h.run('TaskLoop', ['WaitStartRel1'])
    def test_buttons_and_idle(self):
        for value, target in [(0, 'DoFilmTask'), (0x4000, 'FinishLogicalChapter'),
                              (0x2000, 'FinishLogicalChapter'), (0x1000, 'FinishLogicalChapter')]:
            h = SubHarness()
            h.pad(value)
            self.assertEqual(h.run('TaskLoop', ['DoFilmTask', 'FinishLogicalChapter']), target)
    def test_long_start_edge(self):
        h = SubHarness()
        h.pad(0x8000)
        self.assertEqual(h.word('MenuPending'), 1)
        h.uc.mem_write(h.addr('MenuPending'), b'\0\0')
        for _ in range(100):
            h.pad(0x8000)
        self.assertEqual(h.word('MenuPending'), 0)
        h.pad(0)
        h.pad(0x8000)
        self.assertEqual(h.word('MenuPending'), 1)
    def test_failed_open_is_bounded(self):
        h = SubHarness(-1)
        h.run('StartUserCall0', ['OpenErrorWait'])
        self.assertEqual(h.calls, [0, 0])
        self.assertEqual(h.word('OpenFailures'), 2)
        self.assertEqual(h.word('CurOffset'), 0)
    def test_natural_end_closes_and_advances_without_open_failure(self):
        h=SubHarness()
        h.task_result=-1
        h.run('DoFilmTask',['PlayCurrentVideo'])
        self.assertEqual(h.calls,[1,2])
        self.assertEqual(h.word('CurOffset'),4)
        self.assertEqual(h.word('OpenFailures'),0)
    def test_logical_navigation(self):
        h = SubHarness()
        h.uc.mem_write(h.addr('CurOffset'), b'\0\x04')
        h.run('ActionNextTrack', ['PlayCurrentVideo'])
        self.assertEqual(h.word('CurOffset'), 8)
        h.run('ActionPrevTrack', ['PlayCurrentVideo'])
        self.assertEqual(h.word('CurOffset'), 0)
    def test_fast_forward_uses_audio_clock(self):
        h = SubHarness()
        h.valid_frame()
        call(h.uc,h.addr('CaptureCurrentFrameBackup'))
        h.run('ActionFastForward', ['FFFinished'])
        self.assertGreaterEqual(h.audio_time, 3662)
        self.assertLessEqual(h.audio_time, 3782)
        self.assertTrue(50 < len(h.calls) < 90)
    def test_rewind_uses_logical_chapter_start(self):
        h = SubHarness()
        h.uc.mem_write(h.addr('CurOffset'), b'\0\x04')
        h.run('ActionRewind', ['PlayCurrentVideo'])
        self.assertEqual(h.word('CurOffset'), 0)


class ContainerTests(unittest.TestCase):
    def test_chapter_matrix(self):
        for fps in (8, 10, 12, 15, 20, 24):
            for minutes in (1, 2, 3, 5, 10):
                p = chapter_plan(minutes*60, minutes*60, fps)
                self.assertEqual({x['logical'] for x in p}, {1})
                self.assertAlmostEqual(sum(x['duration'] for x in p), minutes*60)
                for x in p:
                    self.assertLessEqual(round(x['duration']*fps) + __import__('math').ceil(x['duration']*16276/16384), 5400)


class NativeAudioTests(unittest.TestCase):
    def test_audio_binary_matches_70(self):
        spec=importlib.util.spec_from_file_location('original70',ROOT/'Codigo_Fuente_MegaCD_Video_Studio_7_0.py')
        old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
        a=[bytearray(x) for x in (studio.IP_BIN,studio.MAINPLAY_BIN,studio.SUBPLAY_BIN)]
        b=[bytearray(x) for x in (studio.IP_BIN,studio.MAINPLAY_BIN,studio.SUBPLAY_BIN)]
        studio.patch_cinepak_players(*a)
        old.patch_cinepak_players(*b)
        # AudioTime cambia su entrada y a�ade una rutina; el PCM conserva sus instrucciones.
        self.assertGreater(len(a[2]),len(b[2]))
        compared=bytearray(a[2][:len(b[2])]);compared[0x1684:0x1688]=b[2][0x1684:0x1688]
        differences=[i for i,(x,y) in enumerate(zip(compared,b[2])) if x!=y]
        self.assertEqual(differences,[0x797])  # únicamente control de preroll CDC


class NativeHeapTests(unittest.TestCase):
    def test_real_allocator_fits_maximum_stab_stream_and_pcm(self):
        uc=cpu()
        base=0x20000
        uc.mem_write(base,studio.SUBPLAY_BIN)
        uc.mem_write(base+8,struct.pack('>II',0x10000,0x4B000))
        call(uc,base+0xFCE)
        for size in (88064,0x34000,4096):
            sp=uc.reg_read(UC_M68K_REG_A7)
            uc.mem_write(sp-4,struct.pack('>I',size))
            uc.reg_write(UC_M68K_REG_A7,sp-4)
            call(uc,base+0xF3C)
            self.assertNotEqual(uc.reg_read(UC_M68K_REG_D0),0)
            uc.reg_write(UC_M68K_REG_A7,sp)
        call(uc,base+0xEF2)
        self.assertEqual(uc.reg_read(UC_M68K_REG_D0),1968)


class ISOTests(unittest.TestCase):
    def test_many_cins_remain_visible_and_contiguous(self):
        import tempfile
        from megacd_runtime import validate_iso
        with tempfile.TemporaryDirectory(prefix='megacd_iso_') as tmp:
            p=Path(tmp)/'entrada.cin'
            p.write_bytes(b'FILM'+bytes(2044))
            paths=[str(p)]*40
            iso,_,_=studio.build_megacd_disc(paths,tmp,chapter_ids=list(range(1,41)))
            report=validate_iso(iso,paths)
            self.assertEqual(len(report['archivos']),40)
            self.assertGreater(report['archivos'][0]['sector'],25)


if __name__ == '__main__':
    unittest.main(verbosity=2)
