"""Ejecuta las instrucciones 68000 reales, incluidos multiplicador y divisor."""
import struct
import unittest
from unicorn import UC_HOOK_CODE
from unicorn.m68k_const import *
import test_astra as native
from megacd_clock import patch_audio_clock


class ClockTests(unittest.TestCase):
    def clock(self, samples, corrected=True):
        code=bytearray(native.studio.SUBPLAY_BIN)
        if corrected:
            patch_audio_clock(code)
        uc=native.cpu()
        base=0x20000
        uc.mem_write(base,bytes(code))
        def ticks(uc, addr, size, unused):
            if addr == base+0x1640:
                sp=uc.reg_read(UC_M68K_REG_A7)
                uc.reg_write(UC_M68K_REG_D0,samples)
                uc.reg_write(UC_M68K_REG_PC,int.from_bytes(uc.mem_read(sp,4),'big'))
                uc.reg_write(UC_M68K_REG_A7,sp+4)
        uc.hook_add(UC_HOOK_CODE,ticks)
        uc.mem_write(0xf000,struct.pack('>II',0x10000,600))
        native.call(uc,base+0x1684)
        self.assertEqual(uc.reg_read(UC_M68K_REG_A7),0xf000)
        return uc.reg_read(UC_M68K_REG_D0)

    def test_old_clock_becomes_negative_at_224_seconds(self):
        self.assertGreater(self.clock(224*16000,False),0x7fffffff)

    def test_rounding_and_long_playback_without_overflow(self):
        import random
        rng=random.Random(46)
        values=[0,1,4095,4096,3579138,3579139,224*16000,300*16000,
                448*16000,600*16000,7613*16276,0xffffffff]
        values += [rng.randrange(0,0xffffffff) for _ in range(300)]
        for samples in values:
            with self.subTest(samples=samples):
                self.assertEqual(self.clock(samples),(samples*600+8000)//16000)

    def test_before_overflow_is_bit_exact(self):
        for samples in (0,16000,4095,16276*120,3579120):
            self.assertEqual(self.clock(samples),self.clock(samples,False))


if __name__=='__main__':unittest.main(verbosity=2)
