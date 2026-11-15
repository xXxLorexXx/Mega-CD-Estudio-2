from pathlib import Path
import importlib.util,sys,time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import megacd_fast as f
p=str(Path(__file__).resolve().parent/'Referencia_Astra_1.py')
spec=importlib.util.spec_from_file_location('old',p);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
rng=np.random.default_rng(54);start=time.perf_counter();differences=0
for n in (1,16,50,199,768):
 for p in (2,4):
  for repeat in range(8):
   tiles=rng.integers(0,8,(n,64,3),dtype=np.int32)
   ids=(tiles[:,:,0]<<6)|(tiles[:,:,1]<<3)|tiles[:,:,2]
   idx=np.sort(rng.choice(768,n,replace=False)).astype(np.int64)
   a=old._cluster_tiles_3d_rgb(tiles,ids,idx,p);b=f.cluster_tiles(tiles,ids,idx,p)
   if any(not np.array_equal(x,y) for x,y in zip(a,b)):
    differences+=1
    print('Diferente',n,p,repeat,[np.count_nonzero(x!=y) for x,y in zip(a,b)],flush=True)
print('Casos 80. Diferencias',differences,'s',time.perf_counter()-start,flush=True)
assert differences==0
