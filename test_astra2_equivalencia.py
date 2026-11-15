from pathlib import Path
import sys,time,importlib.util,json
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import Referencia_Astra_1 as s
spec=importlib.util.spec_from_file_location('fast',str(Path(__file__).resolve().parent/'megacd_fast.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
random=np.random.default_rng(322)
pal_diff=0;neutral_diff=0
start=time.perf_counter()
for i in range(150):
    count=random.integers(0,10000,512).astype(np.float64)
    count[random.random(512)<.3]=0
    global_count=random.integers(0,10000,512).astype(np.float64)
    ids=np.repeat(np.arange(512),np.minimum(count.astype(int),10))
    tiles=s.VDP512_RGB[ids]
    hist=np.bincount(ids,minlength=512).astype(float)+.06*global_count
    hist[0]=0
    a=s.optimize_palette_for_tiles(tiles,global_count)
    b=f.palette_counts(hist)
    if not np.array_equal(a,b):
        pal_diff+=1
        if pal_diff<3:print('Diferencia paleta',i,a,b)
for tint,preset in s.TINT_PRESETS.items():
    gains=np.array(preset or (1,1,1),dtype=np.float32)
    for i in range(15):
        image=random.integers(0,256,(192,256,3),dtype=np.uint8)
        a=s.protect_neutrals(image,tint);b=f.neutral_pixels(image,gains)
        neutral_diff+=np.count_nonzero(a!=b)
print('Paletas diferentes',pal_diff,'píxeles diferentes',neutral_diff,'s',time.perf_counter()-start,flush=True)
assert pal_diff==0 and neutral_diff==0
