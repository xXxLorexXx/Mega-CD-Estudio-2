# -*- coding: utf-8 -*-
"""Pruebas de formato, equivalencia visual y presupuestos reales."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import Mega_Cd_Grabador_de_Video_Beta as s
from megacd_conversion import make_capacity_plan

def decoded_indices(result,previous=None):
    """Decodificador independiente de los cuatro métodos SM para verificar los bytes."""
    count,pal,mapbytes,payload,_=result
    pos=0;n=struct.unpack_from('>I',payload,pos)[0];pos+=4
    book2=np.frombuffer(payload[pos:pos+n],np.uint8);pos+=n
    book2=np.column_stack((book2>>4,book2&15)).ravel().reshape(-1,8)
    n=struct.unpack_from('>I',payload,pos)[0];pos+=4
    book3=np.frombuffer(payload[pos:pos+n],np.uint8);pos+=n
    book3=np.column_stack((book3>>4,book3&15)).ravel().reshape(-1,4)
    methods=payload[pos:pos+192];pos+=192
    image=np.zeros((768,64),np.uint8) if previous is None else previous.copy()
    for t in range(768):
        method=(methods[t//4]>>(6-2*(t%4)))&3
        if method==1:
            raw=np.frombuffer(payload[pos:pos+32],np.uint8);pos+=32
            image[t]=np.column_stack((raw>>4,raw&15)).ravel()
        elif method==2:
            codes=np.frombuffer(payload[pos:pos+8],np.uint8);pos+=8
            image[t]=book2[codes].reshape(4,2,2,4).transpose(0,2,1,3).reshape(64)
        elif method==3:
            codes=np.frombuffer(payload[pos:pos+16],np.uint8);pos+=16
            image[t]=book3[codes].reshape(4,4,2,2).transpose(0,2,1,3).reshape(64)
        elif previous is None:raise AssertionError('Method 0 en un banco sin inicializar.')
    assert pos==len(payload)
    return image

class CodecTests(unittest.TestCase):
    def test_dictionary_compaction_keeps_rendered_pixels_and_both_banks(self):
        # Referencia congelada: se incluye para comparar sin depender de otra carpeta.
        spec=importlib.util.spec_from_file_location('reference',ROOT/'Referencia_Astra_1.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        rng=np.random.default_rng(102)
        for palettes in (1,2,4):
            previous_old=[None,None];previous_new=[None,None]
            for frame in range(4):
                rgb=rng.integers(0,256,(192,256,3),dtype=np.uint8)
                bank=frame%2
                a=old.quantize_and_encode_frame(rgb,(256,192,0,0),num_palettes=palettes,
                    prev2_tinds=previous_old[bank],target_frame_bytes=8800)
                b=s.quantize_and_encode_frame(rgb,(256,192,0,0),num_palettes=palettes,
                    prev2_tinds=previous_new[bank],target_frame_bytes=8800)
                self.assertEqual(a[1],b[1]);self.assertEqual(a[2],b[2])
                ai=decoded_indices(a,previous_old[bank]);bi=decoded_indices(b,previous_new[bank])
                self.assertTrue(np.array_equal(ai,bi))
                self.assertTrue(np.array_equal(bi,b[4]))
                self.assertLessEqual(len(b[3]),len(a[3]))
                previous_old[bank]=ai;previous_new[bank]=bi
    def test_hard_frame_limit_and_decoded_state(self):
        rng=np.random.default_rng(155)
        for box in ((256,192,0,0),(256,112,0,40),(112,192,72,0)):
            banks=[None,None]
            for frame in range(8):
                rgb=rng.integers(0,256,(192,256,3),dtype=np.uint8)
                cap=8000 if frame<2 else 2600
                result=s.quantize_and_encode_frame(rgb,box,prev2_tinds=banks[frame%2],
                    target_frame_bytes=cap,hard_frame_bytes=cap,force_border_refresh=frame<2)
                total=12+len(result[1])+len(result[2])+len(result[3])
                self.assertLessEqual(total,cap)
                decoded=decoded_indices(result,banks[frame%2])
                self.assertTrue(np.array_equal(decoded,result[4]))
                banks[frame%2]=decoded
    def test_all_previous_features_and_player_bytes_retained(self):
        spec=importlib.util.spec_from_file_location('reference2',ROOT/'Referencia_Astra_1.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        for ids in ([1],[1,1,2],[1,2,3]):
            old_sub=bytearray(old.SUBPLAY_BIN);new_sub=bytearray(s.SUBPLAY_BIN)
            old_ip,old_main=bytearray(old.IP_BIN),bytearray(old.MAINPLAY_BIN)
            new_ip,new_main=bytearray(s.IP_BIN),bytearray(s.MAINPLAY_BIN)
            old.patch_cinepak_players(old_ip,old_main,old_sub);s.patch_cinepak_players(new_ip,new_main,new_sub)
            restored=bytearray(new_sub[:len(old_sub)])
            restored[0x1684:0x1688]=old_sub[0x1684:0x1688]
            self.assertEqual(old_sub,restored)
            self.assertGreater(len(new_sub),len(old_sub))
            # El envoltorio ahora distingue error CDC de EOF. Los recursos y
            # todas las rutas del menú permanecen; el binario ya no es idéntico.
            symbols={}
            wrapper=s.build_multi_sp_bin(bytes(new_sub),[25]*len(ids),chapter_ids=ids,debug_symbols=symbols)
            self.assertEqual(wrapper[symbols['Cinepak']:],bytes(new_sub))
            for label in ('ActionPrevTrack','ActionRewind','CloseMenuAndResume','ActionFastForward','ActionNextTrack','ActionStop','RecoverReadError'):
                self.assertIn(label,symbols)
            self.assertEqual(old.patch_main_controller(old_ip,old_main),s.patch_main_controller(new_ip,new_main))
            self.assertEqual(old_ip,new_ip);self.assertEqual(old_main,new_main)
    def test_full_movie_plan_includes_audio_headers_sector_padding_and_leadout(self):
        with tempfile.NamedTemporaryFile(suffix='.mkv') as f:
            sources=[{'path':f.name,'duration':7613.376,'width':1920,'height':816}]
            for fps in (8,10,12,15,20,24):
                for minutes in (1,3,5,10):
                    # El menú limita 99 capítulos; usar 3 min o más para esta película.
                    if minutes==1:continue
                    settings={'fps':fps,'chapter_seconds':minutes*60,'fit_cd':True,'aspect_mode':'crop'}
                    plan=make_capacity_plan(sources,settings,s)
                    predicted=(plan['fixed_sectors']+sum(j['max_cin_bytes']//2048 for j in plan['jobs']))*2048
                    self.assertLessEqual(predicted,plan['capacity_bytes'])
                    self.assertEqual(predicted,plan['target_bytes'])
                    self.assertEqual(sum(j['frames'] for j in plan['jobs']),round(7613.376*fps))

    def test_periodic_refresh_reserves_full_updates_for_both_banks(self):
        # Un límite de CD nunca debe suprimir la renovación que ya tenía Astra.
        import tempfile,subprocess
        with tempfile.TemporaryDirectory(prefix='astra2_refresh_') as tmp:
            root=Path(tmp);video=root/'movimiento.mkv';target=root/'prueba.cin'
            subprocess.run([s.find_ffmpeg(),'-v','error','-y','-f','lavfi','-i',
                            'nullsrc=s=256x192:r=24:d=3,noise=alls=100:allf=t+u','-c:v','ffv1',str(video)],
                           check=True,creationflags=s.CREATE_NO_WINDOW)
            size,frames=s.encode_segment_to_cin(str(video),0,3,24,'crop',4,str(target),tmp,
                                               max_cin_bytes=300000,acceleration='cpu')
            self.assertLessEqual(size,300000);self.assertEqual(frames,72)
            data=target.read_bytes();header=struct.unpack_from('>I',data,4)[0]
            entries=struct.unpack_from('>I',data,48)[0];frame=0;banks=[None,None]
            for i in range(entries):
                offset,length,timestamp,ticks=struct.unpack_from('>IIII',data,52+16*i)
                if timestamp==0xffffffff:continue
                sm=data[header+offset:header+offset+length];count=(sm[3]&3)+1
                p=12+count*32
                result=(count,sm[12:p],sm[p:p+192],sm[p+192:],None)
                current=decoded_indices(result,banks[frame%2]);banks[frame%2]=current
                if frame in (48,49):
                    payload=result[3];p=4+struct.unpack_from('>I',payload,0)[0]
                    p+=4+struct.unpack_from('>I',payload,p)[0]
                    methods=np.frombuffer(payload[p:p+192],np.uint8)
                    copied=sum(int((methods[t//4]>>(6-2*(t%4)))&3)==0 for t in range(768))
                    self.assertLess(copied,150)
                    self.assertGreater(length,6500)
                frame+=1
            self.assertTrue(s.inspect_cin(target,24)['seguro'])

if __name__=='__main__':unittest.main(verbosity=2)
